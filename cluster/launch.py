#!/usr/bin/env python3
"""Start the enabled roster models and the gateway on this GPU node, and keep them running.

Runs inside the serving job (cluster/serve.sbatch):
1. For each enabled model, in start order: pick a free port, start `vllm serve` bound to
   127.0.0.1 with its own log file, and wait for GET /health before starting the next one
   (each process sizes its GPU share at startup).
2. Generate the LiteLLM config from the roster and start the gateway on all interfaces. It
   accepts only the master key or a client key (gateway/local_llm_gateway.py).
3. Write the run file $LLM_RUN_DIR/run/<jobid>.env and supervise.

If a process exits, or the job gets SIGTERM/SIGINT, stop everything and remove the run file.
SLURM sends SIGUSR1 about 10 minutes before the time limit (serve.sbatch); with
LLM_AUTO_RESUBMIT=1 the job then submits its successor and shuts down.

Environment:
    LLM_MODEL_DIR          weights directory (required)
    LLM_RUN_DIR            run files and logs (default ~/.local-llm)
    GATEWAY_MASTER_KEY     gateway admin key (required unless LLM_GATEWAY=0)
    LLM_GATEWAY_KEYS       client key hashes (default $LLM_RUN_DIR/gateway-keys.json)
    LLM_GATEWAY=0          serve the models without the gateway
    LLM_AUTO_RESUBMIT=1    submit a successor job on SIGUSR1 (off by default)
    LLM_AUTO_RESUBMIT_MAX  stop resubmitting after this many successors (default: no limit)
    LLM_STARTUP_TIMEOUT    seconds to wait for one process to become healthy (default 1800)
    SLURM_JOB_ID           set by SLURM
"""

from __future__ import annotations

import contextlib
import datetime as dt
import http.client
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roster

REPO = Path(__file__).resolve().parent.parent
STOP_GRACE_S = 30
# Startup milestones vLLM logs; reported per model when found.
MILESTONES = {
    "weights loaded in (s)": re.compile(r"Loading weights took ([\d.]+) ?s"),
    "model load (GiB, s)": re.compile(
        r"Model loading took ([\d.]+) ?GiB.*? and ([\d.]+) ?s"
    ),
    "attention backend": re.compile(r"Using (\w+) attention backend out of"),
    "vision encoder attention": re.compile(
        r"Using (?:backend )?AttentionBackendEnum\.(\w+) for vit"
    ),
    "torch.compile (s)": re.compile(
        r"torch\.compile (?:takes|took) ([\d.]+) ?s in total"
    ),
    "KV cache memory (GiB)": re.compile(r"Available KV cache memory: ([\d.]+) ?GiB"),
    # vLLM can capture graphs more than once (e.g. piecewise, then full); summed below.
    "CUDA graphs (s)": re.compile(r"Graph capturing finished in ([\d.]+) ?s"),
    "engine init (s)": re.compile(r"init engine .*?took ([\d.]+) ?s"),
    "KV cache (tokens)": re.compile(r"GPU KV cache size: ([\d,]+) tokens"),
    "max concurrency": re.compile(
        r"Maximum concurrency for ([\d,]+) tokens per request: ([\d.]+)x"
    ),
}
# Talk to 127.0.0.1 directly, whatever proxy variables the job inherited.
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_stop_signal: int | None = None
_resubmit_due = False
_log_file = None


class StartupError(Exception):
    pass


def log(msg: str) -> None:
    line = f"[launch {dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def _on_signal(signum, _frame) -> None:
    global _stop_signal
    _stop_signal = signum


def _on_usr1(_signum, _frame) -> None:
    global _resubmit_due
    _resubmit_due = True


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def healthy(port: int, path: str = "/health") -> bool:
    try:
        with _http.open(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status == 200
    except (urllib.error.URLError, http.client.HTTPException, OSError):
        return False


def gpu_memory() -> str:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return out or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def milestones(log_path: Path) -> list[str]:
    text = log_path.read_text(errors="replace")
    found = []
    for label, pattern in MILESTONES.items():
        matches = pattern.findall(text)
        if not matches:
            continue
        if label == "CUDA graphs (s)":
            value = f"{sum(float(m) for m in matches):g}"
        else:
            last = matches[-1]
            value = ", ".join(last) if isinstance(last, tuple) else last
        found.append(f"{label}: {value}")
    return found


def tail(path: Path, lines: int = 40) -> str:
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def start(cmd: list[str], log_path: Path, env: dict | None = None) -> subprocess.Popen:
    """Start one process in its own session, logging to log_path, from the repo root."""
    with open(log_path, "ab") as out:
        return subprocess.Popen(
            cmd,
            cwd=REPO,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def wait_ready(name: str, proc: subprocess.Popen, port: int, path: str,
               log_path: Path, timeout: float) -> float:  # fmt: skip
    """Wait until GET path answers 200; return the seconds it took."""
    t0 = time.monotonic()
    while not healthy(port, path):
        if _stop_signal is not None:
            raise StartupError(f"signal {_stop_signal} while starting {name}")
        if proc.poll() is not None:
            raise StartupError(
                f"{name} exited with code {proc.returncode}; log tail:\n{tail(log_path)}"
            )
        if time.monotonic() - t0 > timeout:
            raise StartupError(
                f"{name}: not healthy after {timeout:.0f} s; log tail:\n{tail(log_path)}"
            )
        time.sleep(2)
    return time.monotonic() - t0


def stop(procs: dict[str, subprocess.Popen]) -> None:
    """SIGTERM each process group (newest first), then SIGKILL stragglers."""
    for name, proc in reversed(procs.items()):
        if proc.poll() is None:
            log(f"stopping {name} (pid {proc.pid})")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_S
    for name, proc in procs.items():
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            log(f"{name} did not stop within {STOP_GRACE_S} s; killing it")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def write_run_file(path: Path, values: dict[str, str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    os.replace(tmp, path)


def env_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", name.upper())


def gateway_config(ports: dict[str, int], models: list[roster.Model]) -> dict:
    """LiteLLM proxy config: one alias per roster model, pointing at its local vLLM port."""
    modes = {"chat": "chat", "embed": "embedding", "rerank": "rerank"}
    entries = []
    for m in models:
        base = f"http://127.0.0.1:{ports[m.name]}"
        entries.append(
            {
                "model_name": m.name,
                "litellm_params": {
                    "model": f"hosted_vllm/{m.name}",
                    # LiteLLM appends /rerank itself; the other routes live under /v1.
                    "api_base": base if m.kind == "rerank" else f"{base}/v1",
                    "api_key": "none",  # vLLM listens on 127.0.0.1 only, without a key
                },
                "model_info": {"mode": modes[m.kind]},
            }
        )
    return {
        "model_list": entries,
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "custom_auth": "local_llm_gateway.user_api_key_auth",
        },
        "litellm_settings": {
            "drop_params": False,
            "num_retries": 0,
            "request_timeout": 3600,
            "callbacks": ["local_llm_gateway.usage_log"],
        },
    }


def gateway_env(run_dir: Path) -> dict:
    env = dict(os.environ)
    env.update(
        LITELLM_MASTER_KEY=os.environ["GATEWAY_MASTER_KEY"],
        LLM_GATEWAY_KEYS=os.environ.get("LLM_GATEWAY_KEYS")
        or str(run_dir / "gateway-keys.json"),
        LLM_RUN_DIR=str(run_dir),
        PYTHONPATH=str(REPO / "gateway"),
        # No network fetches at startup, no telemetry, no admin UI (it needs a database).
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS="True",
        LITELLM_TELEMETRY="False",
        DISABLE_ADMIN_UI="True",
        NO_DOCS="True",
    )
    return env


def resubmit() -> bool:
    """Submit the successor job through cluster/submit.sh; return True on success."""
    count = int(os.environ.get("LLM_RESUBMIT_COUNT") or 0) + 1
    limit = os.environ.get("LLM_AUTO_RESUBMIT_MAX")
    if limit and count > int(limit):
        log(f"auto-resubmit limit reached ({limit}); not submitting a successor")
        return False
    cmd = [
        str(REPO / "cluster" / "submit.sh"),
        f"--export=ALL,LLM_RESUBMIT_COUNT={count}",
    ]
    out = subprocess.run(
        cmd, check=False, capture_output=True, text=True, timeout=120, cwd=REPO
    )
    log(
        f"resubmit #{count}: {(out.stdout + out.stderr).strip()} (exit {out.returncode})"
    )
    return out.returncode == 0


def main() -> int:
    global _log_file
    job = os.environ.get("SLURM_JOB_ID") or sys.exit("launch: not inside a SLURM job")
    model_dir = Path(
        os.environ.get("LLM_MODEL_DIR") or sys.exit("launch: set LLM_MODEL_DIR")
    )
    run_dir = Path(os.environ.get("LLM_RUN_DIR") or Path.home() / ".local-llm")
    timeout = float(os.environ.get("LLM_STARTUP_TIMEOUT") or 1800)
    node = os.environ.get("SLURMD_NODENAME") or socket.gethostname().split(".")[0]
    vllm = str(Path(sys.executable).with_name("vllm"))
    use_gateway = os.environ.get("LLM_GATEWAY", "1") != "0"
    litellm = REPO / "gateway" / ".venv" / "bin" / "litellm"
    auto_resubmit = os.environ.get("LLM_AUTO_RESUBMIT") == "1"

    log_dir = run_dir / "logs" / job
    log_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run").mkdir(parents=True, exist_ok=True)
    run_file = run_dir / "run" / f"{job}.env"
    _log_file = open(log_dir / "launch.log", "a")  # noqa: SIM115 - lives for the whole job

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)
    signal.signal(signal.SIGUSR1, _on_usr1)

    try:
        models = roster.enabled(roster.load())
    except roster.RosterError as e:
        log(f"roster: {e}")
        return 1
    if not models:
        log("no enabled models in the roster")
        return 1
    missing = [
        m.name
        for m in models
        if not (m.weights_dir(model_dir) / "config.json").is_file()
    ]
    if missing:
        log(f"weights missing under {model_dir} for: {', '.join(missing)}")
        return 1
    if use_gateway and not (litellm.exists() and os.environ.get("GATEWAY_MASTER_KEY")):
        log(
            "the gateway needs gateway/.venv (uv sync --locked --project gateway) and "
            "GATEWAY_MASTER_KEY in .env; set LLM_GATEWAY=0 to serve without it"
        )
        return 1
    if os.environ.get("LLM_STAGE_DIR"):
        log(
            "LLM_STAGE_DIR is set, but weight staging is not implemented yet; loading in place"
        )

    log(
        f"job {job} on {node}; models: {', '.join(m.name for m in models)}; "
        f"gateway: {'on' if use_gateway else 'off'}; auto-resubmit: "
        f"{'on' if auto_resubmit else 'off'}; logs in {log_dir}"
    )
    log(f"GPU memory before start: {gpu_memory()}")
    procs: dict[str, subprocess.Popen] = {}
    ports: dict[str, int] = {}
    run = {
        "LLM_JOB_ID": job,
        "LLM_NODE": node,
        "LLM_LOG_DIR": str(log_dir),
        "LLM_MODELS": ",".join(m.name for m in models),
    }
    job_t0 = time.monotonic()
    try:
        try:
            for m in models:
                port = ports[m.name] = free_port()
                cmd = roster.build_command(m, port, model_dir, vllm=vllm)
                log_path = log_dir / f"{m.name}.log"
                log(f"starting {m.name} on 127.0.0.1:{port}: {shlex.join(cmd)}")
                procs[m.name] = start(cmd, log_path)
                took = wait_ready(
                    m.name, procs[m.name], port, "/health", log_path, timeout
                )
                log(f"{m.name} ready in {took:.1f} s")
                for line in milestones(log_path):
                    log(f"  {m.name} {line}")
                log(f"GPU memory now: {gpu_memory()}")
                run[f"LLM_MODEL_{env_key(m.name)}_PORT"] = str(port)
                run[f"LLM_MODEL_{env_key(m.name)}_KIND"] = m.kind

            if use_gateway:
                port = free_port("0.0.0.0")
                config = log_dir / "gateway.yaml"
                config.write_text(json.dumps(gateway_config(ports, models), indent=2))
                cmd = [str(litellm), "--config", str(config), "--host", "0.0.0.0",
                       "--port", str(port), "--num_workers", "1"]  # fmt: skip
                log_path = log_dir / "gateway.log"
                log(f"starting the gateway on 0.0.0.0:{port}: {shlex.join(cmd)}")
                procs["gateway"] = start(cmd, log_path, env=gateway_env(run_dir))
                took = wait_ready("gateway", procs["gateway"], port, "/health/liveliness",
                                  log_path, timeout)  # fmt: skip
                log(f"gateway ready in {took:.1f} s")
                run["LLM_GATEWAY_PORT"] = str(port)
        except StartupError as e:
            log(str(e))
            return 0 if _stop_signal is not None else 1

        run["LLM_STARTED"] = (
            dt.datetime.now().astimezone().isoformat(timespec="seconds")
        )
        write_run_file(run_file, run)
        log(f"all ready in {time.monotonic() - job_t0:.1f} s; run file {run_file}")

        warned = False
        while _stop_signal is None:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    log(
                        f"{name} exited unexpectedly with code {proc.returncode}; log tail:"
                    )
                    log("\n" + tail(log_dir / f"{name}.log"))
                    return 1
            if _resubmit_due and auto_resubmit:
                log("time limit is near; submitting the successor")
                if resubmit():
                    log("successor submitted; shutting down to free the node for it")
                    return 0
                log("no successor submitted; serving until the time limit")
                auto_resubmit, warned = False, True
            if _resubmit_due and not warned:
                log("time limit is near; auto-resubmit is off (LLM_AUTO_RESUBMIT=1)")
                warned = True
            time.sleep(5)
        log(f"signal {_stop_signal}; shutting down")
        return 0
    finally:
        run_file.unlink(missing_ok=True)
        stop(procs)
        log("stopped")


if __name__ == "__main__":
    sys.exit(main())
