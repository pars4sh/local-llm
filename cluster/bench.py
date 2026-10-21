#!/usr/bin/env python3
"""Benchmark a chat model: decode speed, concurrency, MTP acceptance, KV capacity, long context.

Usage:
    bench.py endpoint --model NAME --port PORT
        Benchmark a running vLLM server on 127.0.0.1 (run on the GPU node).
    bench.py variants --model NAME --variant LABEL=FLAGS [--variant ...] --out FILE
        Inside a GPU job: for each variant, start the roster model with FLAGS replacing or
        adding to its extra_args, benchmark it, stop it, and append one JSON line to FILE.
        FLAGS are split like a shell command line, so quote JSON values inside them:
        --variant "mtp2=--speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":2}'"

Every run uses the same prompts:
- decode: one stream, 512 tokens, thinking off, the model card's instruct sampling; median of 3
- concurrent: 4 streams of 512 tokens at once; aggregate tok/s
- speculative decoding acceptance, from vLLM's /metrics counters over the decode runs
- needle: two facts at 25% and 75% of a ~200k-token document, greedy answer
- KV capacity and startup milestones, from the vLLM log (variants mode)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import shlex
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launch
import roster
from smoke_test import NO_THINK, Endpoint, chat, long_document, user

# Qwen3.8 model card, instruct (non-thinking) mode.
INSTRUCT_SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "presence_penalty": 1.5,
}
DECODE_TOKENS = 512
TOPICS = ["a lighthouse keeper", "a glacier survey", "a night market", "a chess club"]
NEEDLES = {
    0.25: "Note for the reader: the first code word is aurora-7342.",
    0.75: "Note for the reader: the second code word is boreal-9158.",
}
NEEDLE_QUESTION = (
    "What are the first and the second code words mentioned in the document? "
    "Reply with both, separated by a comma."
)
TOKENS_PER_WORD = 1.11
MAMBA_ERROR = re.compile(
    r"max_num_seqs \((\d+)\) exceeds available Mamba cache blocks \((\d+)\)"
)
METRIC_LINE = re.compile(r"^(vllm:spec_decode_\w+?)(?:_total)?(\{[^}]*\})?\s+(\S+)$")


def story(topic: str) -> dict:
    return {
        "messages": [user(f"Write a long story about {topic}.")],
        "max_tokens": DECODE_TOKENS,
        "ignore_eos": True,
        **NO_THINK,
        **INSTRUCT_SAMPLING,
    }


def spec_counters(port: int) -> dict[str, float]:
    """Speculative-decoding counters from /metrics, summed over labels except position."""
    out: dict[str, float] = {}
    with launch._http.open(f"http://127.0.0.1:{port}/metrics", timeout=10) as r:
        for line in r.read().decode().splitlines():
            m = METRIC_LINE.match(line)
            if not m:
                continue
            name, labels, value = m.groups()
            pos = re.search(r'position="(\d+)"', labels or "")
            key = f"{name}[{pos.group(1)}]" if pos else name
            out[key] = out.get(key, 0.0) + float(value)
    return out


def acceptance(before: dict, after: dict) -> dict | None:
    d = {k: after.get(k, 0.0) - before.get(k, 0.0) for k in after}
    drafts = d.get("vllm:spec_decode_num_drafts", 0.0)
    drafted = d.get("vllm:spec_decode_num_draft_tokens", 0.0)
    accepted = d.get("vllm:spec_decode_num_accepted_tokens", 0.0)
    if not drafts or not drafted:
        return None
    per_pos = sorted(
        (int(k.rsplit("[", 1)[1].rstrip("]")), v)
        for k, v in d.items()
        if k.startswith("vllm:spec_decode_num_accepted_tokens_per_pos[")
    )
    return {
        "acceptance_rate": round(accepted / drafted, 3),
        "mean_acceptance_length": round(1 + accepted / drafts, 2),
        "per_position": [round(v / drafts, 3) for _, v in per_pos],
    }


def benchmark(port: int, model: str, needle_tokens: int) -> dict:
    res: dict = {}
    ep = Endpoint(f"http://127.0.0.1:{port}")
    chat(ep, model, {**story(TOPICS[0]), "max_tokens": 16})  # warm up

    m0 = spec_counters(port)
    singles = [chat(ep, model, story(TOPICS[0])) for _ in range(3)]
    m1 = spec_counters(port)
    res["decode_tps_runs"] = [round(r.decode_tps, 1) for r in singles]
    res["decode_tps"] = round(statistics.median(r.decode_tps for r in singles), 1)
    res["decode_tokens"] = singles[0].completion_tokens

    replies: list = [None] * len(TOPICS)

    def worker(i: int) -> None:
        replies[i] = chat(ep, model, story(TOPICS[i]))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(TOPICS))]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - t0
    m2 = spec_counters(port)
    res["concurrent_streams"] = len(TOPICS)
    res["concurrent_aggregate_tps"] = round(
        sum(r.completion_tokens for r in replies) / wall, 1
    )
    res["concurrent_per_stream_tps"] = round(
        statistics.mean(r.decode_tps for r in replies), 1
    )
    res["spec_single"] = acceptance(m0, m1)
    res["spec_concurrent"] = acceptance(m1, m2)

    doc = long_document(int(needle_tokens / TOKENS_PER_WORD), NEEDLES)
    msgs = [user(f"<document>\n{doc}\n</document>\n\n{NEEDLE_QUESTION}")]
    r = chat(
        ep, model, {"messages": msgs, "max_tokens": 64, "temperature": 0, **NO_THINK}
    )
    res["needle"] = {
        "prompt_tokens": r.prompt_tokens,
        "prefill_s": round(r.ttft, 1),
        "prefill_tps": round(r.prefill_tps),
        "answer": r.content.strip()[:80],
        "found": ["7342" in r.content, "9158" in r.content],
    }
    return res


def override(args: tuple[str, ...], flags: list[str]) -> tuple[str, ...]:
    """Replace the values of flags already in args, and append the others."""
    out = list(args)
    i = 0
    while i < len(flags):
        flag = flags[i]
        has_value = i + 1 < len(flags) and not flags[i + 1].startswith("--")
        value = [flags[i + 1]] if has_value else []
        if flag in out:
            j = out.index(flag)
            had_value = j + 1 < len(out) and not out[j + 1].startswith("--")
            out[j : j + 1 + had_value] = [flag, *value]
        else:
            out += [flag, *value]
        i += 1 + has_value
    return tuple(out)


def log_values(log_path: Path) -> dict:
    text = log_path.read_text(errors="replace")
    found = {}
    for label, pattern in launch.MILESTONES.items():
        matches = pattern.findall(text)
        if matches:
            last = matches[-1]
            found[label] = ", ".join(last) if isinstance(last, tuple) else last
    return found


def run_variant(model: roster.Model, label: str, flags: list[str], model_dir: Path,
                log_dir: Path, needle_tokens: int, timeout: float) -> dict:  # fmt: skip
    m = dataclasses.replace(model, extra_args=override(model.extra_args, flags))
    rec: dict = {"variant": label, "flags": shlex.join(flags), "ok": False}
    vllm = str(Path(sys.executable).with_name("vllm"))
    for attempt in (1, 2):
        port = launch.free_port()
        cmd = roster.build_command(m, port, model_dir, vllm=vllm)
        log_path = log_dir / f"bench-{label}{'-retry' if attempt == 2 else ''}.log"
        launch.log(f"[{label}] starting: {shlex.join(cmd)}")
        proc = launch.start(cmd, log_path)
        procs = {m.name: proc}
        try:
            t0 = time.monotonic()
            while not launch.healthy(port):
                if proc.poll() is not None or time.monotonic() - t0 > timeout:
                    break
                time.sleep(2)
            else:
                rec["startup_s"] = round(time.monotonic() - t0, 1)
                rec["gpu_memory"] = launch.gpu_memory()
                rec["log"] = log_values(log_path)
                launch.log(f"[{label}] ready in {rec['startup_s']} s; benchmarking")
                rec.update(benchmark(port, m.name, needle_tokens))
                rec["ok"] = True
                return rec
            text = log_path.read_text(errors="replace")
            mamba = MAMBA_ERROR.search(text)
            if attempt == 1 and mamba:
                fit = 2 ** int(math.log2(int(mamba.group(2))))
                launch.log(
                    f"[{label}] {mamba.group(0)}; retrying with --max-num-seqs {fit}"
                )
                rec["max_num_seqs_retry"] = {"error": mamba.group(0), "used": fit}
                m = dataclasses.replace(
                    m, extra_args=override(m.extra_args, ["--max-num-seqs", str(fit)])
                )
                continue
            rec["error"] = launch.tail(log_path, 15)
            launch.log(f"[{label}] failed to start; see {log_path}")
            return rec
        except Exception as e:  # noqa: BLE001 - record and move on to the next variant
            rec["error"] = f"{type(e).__name__}: {e}"
            launch.log(f"[{label}] benchmark failed: {rec['error']}")
            return rec
        finally:
            launch.stop(procs)
    return rec


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="mode", required=True)
    e = sub.add_parser("endpoint")
    e.add_argument("--port", type=int, required=True)
    v = sub.add_parser("variants")
    v.add_argument("--variant", action="append", default=[], metavar="LABEL=FLAGS")
    v.add_argument("--variants-file", help="LABEL=FLAGS lines; # starts a comment")
    v.add_argument("--out", help="default: $LLM_RUN_DIR/logs/$SLURM_JOB_ID/bench.jsonl")
    v.add_argument(
        "--timeout", type=float, default=600, help="startup timeout per variant (s)"
    )
    for s in (e, v):
        s.add_argument("--model", required=True)
        s.add_argument("--needle-tokens", type=int, default=200_000)
    args = p.parse_args()

    if args.mode == "endpoint":
        print(
            json.dumps(benchmark(args.port, args.model, args.needle_tokens), indent=2)
        )
        return 0

    specs = list(args.variant)
    if args.variants_file:
        for line in Path(args.variants_file).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                specs.append(line.strip())
    if not specs:
        p.error("give --variant or --variants-file")
    out = Path(
        args.out
        or Path(os.environ.get("LLM_RUN_DIR") or Path.home() / ".local-llm")
        / "logs"
        / os.environ.get("SLURM_JOB_ID", "local")
        / "bench.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    model = roster.find(roster.load(), args.model)
    model_dir = Path(os.environ["LLM_MODEL_DIR"])
    launch.log(f"{len(specs)} variants of {model.name}; results in {out}")
    for spec in specs:
        label, _, flags = spec.partition("=")
        rec = run_variant(model, label, shlex.split(flags), model_dir, out.parent,
                          args.needle_tokens, args.timeout)  # fmt: skip
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        launch.log(f"[{label}] {json.dumps(rec)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
