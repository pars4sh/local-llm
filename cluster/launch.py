#!/usr/bin/env python3
"""Start the enabled roster models on this GPU node and keep them running.

Runs inside the serving job (cluster/serve.sbatch). For each enabled model, in start order:
pick a free port, start `vllm serve` bound to 127.0.0.1 with its own log file, and wait for
GET /health before starting the next one (each process sizes its GPU share at startup).
Then write the run file $LLM_RUN_DIR/run/<jobid>.env and supervise. If a model exits, or
the job gets SIGTERM/SIGINT, stop every model and remove the run file.

Environment:
    LLM_MODEL_DIR        weights directory (required)
    LLM_RUN_DIR          run files and logs (default ~/.local-llm)
    SLURM_JOB_ID         set by SLURM
    LLM_STARTUP_TIMEOUT  seconds to wait for one model's /health (default 1800)
"""

from __future__ import annotations

import contextlib
import datetime as dt
import http.client
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
_log_file = None


def log(msg: str) -> None:
    line = f"[launch {dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def _on_signal(signum, _frame) -> None:
    global _stop_signal
    _stop_signal = signum


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthy(port: int) -> bool:
    try:
        with _http.open(f"http://127.0.0.1:{port}/health", timeout=5) as r:
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


def stop(procs: dict[str, subprocess.Popen]) -> None:
    """SIGTERM each model's process group (newest first), then SIGKILL stragglers."""
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

    log_dir = run_dir / "logs" / job
    log_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run").mkdir(parents=True, exist_ok=True)
    run_file = run_dir / "run" / f"{job}.env"
    _log_file = open(log_dir / "launch.log", "a")  # noqa: SIM115 - lives for the whole job

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)

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
    if os.environ.get("LLM_STAGE_DIR"):
        log(
            "LLM_STAGE_DIR is set, but weight staging is not implemented yet; loading in place"
        )
    if os.environ.get("LLM_AUTO_RESUBMIT") == "1":
        log("LLM_AUTO_RESUBMIT=1, but auto-resubmit is not implemented yet")

    log(
        f"job {job} on {node}; models: {', '.join(m.name for m in models)}; logs in {log_dir}"
    )
    log(f"GPU memory before start: {gpu_memory()}")
    procs: dict[str, subprocess.Popen] = {}
    run = {
        "LLM_JOB_ID": job,
        "LLM_NODE": node,
        "LLM_LOG_DIR": str(log_dir),
        "LLM_MODELS": ",".join(m.name for m in models),
    }
    job_t0 = time.monotonic()
    try:
        for m in models:
            port = free_port()
            cmd = roster.build_command(m, port, model_dir, vllm=vllm)
            log_path = log_dir / f"{m.name}.log"
            log(f"starting {m.name} on 127.0.0.1:{port}: {shlex.join(cmd)}")
            with open(log_path, "ab") as out:
                procs[m.name] = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            t0 = time.monotonic()
            while not healthy(port):
                if _stop_signal is not None:
                    log(f"signal {_stop_signal} during startup")
                    return 0
                if procs[m.name].poll() is not None:
                    log(
                        f"{m.name} exited with code {procs[m.name].returncode}; log tail:"
                    )
                    log("\n" + tail(log_path))
                    return 1
                if time.monotonic() - t0 > timeout:
                    log(f"{m.name}: no /health after {timeout:.0f} s; log tail:")
                    log("\n" + tail(log_path))
                    return 1
                time.sleep(2)
            log(f"{m.name} ready in {time.monotonic() - t0:.1f} s")
            for line in milestones(log_path):
                log(f"  {m.name} {line}")
            log(f"GPU memory now: {gpu_memory()}")
            key = env_key(m.name)
            run[f"LLM_MODEL_{key}_PORT"] = str(port)
            run[f"LLM_MODEL_{key}_KIND"] = m.kind

        run["LLM_STARTED"] = (
            dt.datetime.now().astimezone().isoformat(timespec="seconds")
        )
        write_run_file(run_file, run)
        log(
            f"all models ready in {time.monotonic() - job_t0:.1f} s; run file {run_file}"
        )

        while _stop_signal is None:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    log(
                        f"{name} exited unexpectedly with code {proc.returncode}; log tail:"
                    )
                    log("\n" + tail(log_dir / f"{name}.log"))
                    return 1
            time.sleep(5)
        log(f"signal {_stop_signal}; shutting down")
        return 0
    finally:
        run_file.unlink(missing_ok=True)
        stop(procs)
        log("stopped")


if __name__ == "__main__":
    sys.exit(main())
