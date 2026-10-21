#!/usr/bin/env python3
"""Smoke-test a running serving job, through its gateway when it has one.

Usage: smoke_test.py RUN_FILE [--model NAME] [--direct]

Reads the job's run file. With a gateway, every request goes through it with the client key
in $LLM_SMOKE_KEY (cluster/smoke_test.sh issues a temporary one), and the gateway must
reject requests without a valid key. --direct talks to each vLLM port instead.

Checks:
- the main chat model (default: the first chat model): plain chat, an image question, a tool
  call, thinking on and off, a ~50k-token prompt, single-stream decode speed
- every other chat model: plain chat

Prints PASS/FAIL with timings per check and exits non-zero if any check fails. vLLM binds to
127.0.0.1, so run this on the GPU node (cluster/smoke_test.sh shows how). Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field

_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class Endpoint:
    """An OpenAI-compatible server: a vLLM port or the gateway, with an optional key."""

    base: str
    key: str | None = None

    def request(self, path: str, body: dict | None = None) -> urllib.request.Request:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = json.dumps(body).encode() if body is not None else None
        return urllib.request.Request(self.base + path, data=data, headers=headers)

    def json(self, path: str, body: dict | None = None, timeout: float = 120):
        with _http.open(self.request(path, body), timeout=timeout) as r:
            return json.load(r)


@dataclass
class Reply:
    content: str = ""
    reasoning: str = ""
    reasoning_field: str | None = None  # which delta field carried the reasoning
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft: float = 0.0  # seconds to the first streamed token
    decode_s: float = 0.0  # seconds from the first to the last streamed token

    @property
    def decode_tps(self) -> float:
        return (
            (self.completion_tokens - 1) / self.decode_s if self.decode_s > 0 else 0.0
        )

    @property
    def prefill_tps(self) -> float:
        return self.prompt_tokens / self.ttft if self.ttft > 0 else 0.0


def read_run_file(path: str) -> dict[str, str]:
    values = {}
    with open(path) as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep:
                values[key] = value
    return values


def chat(ep: Endpoint, model: str, payload: dict, timeout: float = 900) -> Reply:
    """Stream one chat completion and collect content, reasoning, tool calls and timings."""
    body = {
        "model": model,
        **payload,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    reply = Reply()
    content, reasoning, calls = [], [], {}
    t0 = time.monotonic()
    first = last = None
    with _http.open(ep.request("/v1/chat/completions", body), timeout=timeout) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("usage"):
                reply.prompt_tokens = chunk["usage"].get("prompt_tokens", 0)
                reply.completion_tokens = chunk["usage"].get("completion_tokens", 0)
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                got = False
                if delta.get("content"):
                    content.append(delta["content"])
                    got = True
                # vLLM and LiteLLM have used both names for the reasoning parser's output.
                for name in ("reasoning", "reasoning_content"):
                    if delta.get(name):
                        reasoning.append(delta[name])
                        reply.reasoning_field = name
                        got = True
                        break
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(
                        tc.get("index", 0), {"name": "", "arguments": ""}
                    )
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
                    got = True
                if got:
                    last = time.monotonic()
                    first = first or last
                if choice.get("finish_reason"):
                    reply.finish_reason = choice["finish_reason"]
    reply.content = "".join(content)
    reply.reasoning = "".join(reasoning)
    reply.tool_calls = [calls[i] for i in sorted(calls)]
    if first is not None:
        reply.ttft = first - t0
        reply.decode_s = last - first
    return reply


def png_two_colors(size: int = 64) -> bytes:
    """A PNG whose left half is red and right half is blue."""
    half = size // 2
    row = b"\x00" + b"\xff\x00\x00" * half + b"\x00\x00\xff" * (size - half)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


def long_document(target_words: int, needles: dict[float, str]) -> str:
    """Plausible filler text with needle sentences at the given fractions of the way in.

    About 1.11 tokens per word with the Qwen3.8 tokenizer.
    """
    rng = random.Random(0)
    words = [
        "the", "of", "system", "data", "model", "results", "analysis", "method", "sample",
        "process", "value", "measure", "energy", "surface", "structure", "response", "design",
        "cell", "material", "signal", "phase", "flow", "layer", "network", "pressure",
        "temperature", "level", "region", "factor", "effect", "field", "image", "test",
    ]  # fmt: skip
    lines, count = [], 0
    while count < target_words:
        n = rng.randint(12, 24)
        lines.append(" ".join(rng.choice(words) for _ in range(n)).capitalize() + ".")
        count += n
    for where, needle in sorted(needles.items(), reverse=True):
        lines.insert(int(len(lines) * where), needle)
    return "\n".join(lines)


def user(text: str) -> dict:
    return {"role": "user", "content": text}


NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
THINK = {"chat_template_kwargs": {"enable_thinking": True}}
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city", "unit"],
        },
    },
}


def check_plain(ep, model):
    r = chat(
        ep, model, {"messages": [user("What is the capital of France? One word.")]}
    )
    return "paris" in r.content.lower(), r, r.content.strip()[:60]


def check_image(ep, model):
    url = "data:image/png;base64," + base64.b64encode(png_two_colors()).decode()
    msg = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": url}},
            {
                "type": "text",
                "text": "Which two colors does this image contain? Be brief.",
            },
        ],
    }
    r = chat(ep, model, {"messages": [msg], "max_tokens": 200, **NO_THINK})
    text = r.content.lower()
    return "red" in text and "blue" in text, r, r.content.strip()[:60]


def check_tool(ep, model):
    payload = {
        "messages": [user("What's the weather in Paris right now? Use celsius.")],
        "tools": [WEATHER_TOOL],
        "tool_choice": "auto",
    }
    r = chat(ep, model, payload)
    if r.finish_reason != "tool_calls" or not r.tool_calls:
        return False, r, f"finish_reason={r.finish_reason} content={r.content[:200]!r}"
    call = r.tool_calls[0]
    try:
        args = json.loads(call["arguments"])
    except json.JSONDecodeError:
        return False, r, f"arguments are not valid JSON: {call['arguments']!r}"
    ok = (
        call["name"] == "get_weather"
        and isinstance(args, dict)
        and "paris" in str(args.get("city", "")).lower()
        and args.get("unit") == "celsius"
    )
    return ok, r, f"{call['name']}({json.dumps(args)})"


def check_thinking_on(ep, model):
    msgs = [user("What is 17 * 23? Reply with just the number.")]
    r = chat(ep, model, {"messages": msgs, **THINK})
    ok = bool(r.reasoning.strip()) and "391" in r.content
    detail = (
        f"reasoning {len(r.reasoning)} chars in '{r.reasoning_field}', "
        f"answer {r.content.strip()[:40]!r}"
    )
    return ok, r, detail


def check_thinking_off(ep, model):
    msgs = [user("What is 17 * 23? Reply with just the number.")]
    r = chat(ep, model, {"messages": msgs, "max_tokens": 50, **NO_THINK})
    ok = not r.reasoning.strip() and "391" in r.content and "<think>" not in r.content
    detail = f"reasoning {len(r.reasoning)} chars, answer {r.content.strip()[:40]!r}"
    return ok, r, detail


def check_long(ep, model):
    doc = long_document(
        46_000, {0.6: "Note for the reader: the secret code word is aurora-7342."}
    )
    question = (
        "What is the secret code word mentioned in the document? Reply with it only."
    )
    msgs = [user(f"<document>\n{doc}\n</document>\n\n{question}")]
    r = chat(ep, model, {"messages": msgs, "max_tokens": 50, **NO_THINK})
    ok = "7342" in r.content and 40_000 <= r.prompt_tokens <= 65_000
    return ok, r, f"prompt {r.prompt_tokens} tokens, answer {r.content.strip()[:40]!r}"


def check_decode(ep, model):
    payload = {
        "messages": [user("Write a long story about a lighthouse keeper.")],
        "max_tokens": 512,
        "ignore_eos": True,
        **NO_THINK,
    }
    r = chat(ep, model, payload)
    return r.completion_tokens == 512, r, f"{r.completion_tokens} tokens"


def check_auth(ep, model):
    """The gateway must refuse requests without a key or with a wrong one."""
    codes = []
    for key in (None, "sk-not-a-real-key"):
        try:
            Endpoint(ep.base, key).json("/v1/models")
            codes.append(200)
        except urllib.error.HTTPError as e:
            codes.append(e.code)
    ok = all(c in (401, 403) for c in codes)
    return ok, None, f"no key -> {codes[0]}, wrong key -> {codes[1]}"


def check_models(names: list[str]):
    """A check that the gateway lists exactly the run file's models."""

    def check(ep, _model):
        listed = sorted(m["id"] for m in ep.json("/v1/models")["data"])
        return listed == sorted(names), None, f"{listed}"

    return check


MAIN_CHECKS = [
    ("plain chat", check_plain),
    ("image question", check_image),
    ("tool call", check_tool),
    ("thinking on", check_thinking_on),
    ("thinking off", check_thinking_off),
    ("~50k-token prompt", check_long),
    ("decode speed", check_decode),
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("run_file")
    p.add_argument("--model", help="main chat model (default: first chat model)")
    p.add_argument("--direct", action="store_true", help="bypass the gateway")
    args = p.parse_args()

    run = read_run_file(args.run_file)
    names = [n for n in run.get("LLM_MODELS", "").split(",") if n]

    def key(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in name.upper())

    kinds = {n: run.get(f"LLM_MODEL_{key(n)}_KIND") for n in names}
    chat_models = [n for n in names if kinds[n] == "chat"]
    main_model = args.model or (chat_models[0] if chat_models else None)
    if main_model not in chat_models:
        print(f"no chat model {main_model or ''} in {args.run_file} (models: {names})")
        return 1

    gateway = run.get("LLM_GATEWAY_PORT") and not args.direct
    if gateway:
        smoke_key = os.environ.get("LLM_SMOKE_KEY")
        if not smoke_key:
            print("set LLM_SMOKE_KEY to a client key (cluster/smoke_test.sh does this)")
            return 1
        gw = Endpoint(f"http://{run['LLM_NODE']}:{run['LLM_GATEWAY_PORT']}", smoke_key)

    def endpoint(name: str) -> Endpoint:
        if gateway:
            return gw
        return Endpoint(f"http://127.0.0.1:{run[f'LLM_MODEL_{key(name)}_PORT']}")

    via = f"gateway {gw.base}" if gateway else "vLLM ports directly"
    print(
        f"smoke test: job {run.get('LLM_JOB_ID')} on {run.get('LLM_NODE')}, via {via}"
    )

    checks = []
    if gateway:
        checks.append(("gateway auth", check_auth, "gateway"))
        checks.append(("gateway lists models", check_models(names), "gateway"))
    checks += [(label, fn, main_model) for label, fn in MAIN_CHECKS]
    checks += [("plain chat", check_plain, n) for n in chat_models if n != main_model]

    failed = 0
    for label, fn, model in checks:
        stats = ""
        try:
            ok, r, detail = fn(gw if model == "gateway" else endpoint(model), model)
            if r is not None:
                stats = (
                    f"ttft {r.ttft:5.2f}s  prompt {r.prompt_tokens:>6}  "
                    f"out {r.completion_tokens:>5}  decode {r.decode_tps:6.1f} tok/s  "
                    f"prefill {r.prefill_tps:8.0f} tok/s  "
                )
        except urllib.error.HTTPError as e:
            ok, detail = (
                False,
                f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}",
            )
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        failed += not ok
        name = f"{label} ({model})"
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<32} {stats}{detail}", flush=True)
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
