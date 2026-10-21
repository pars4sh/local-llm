#!/usr/bin/env python3
"""Check that Hugging Face model repos exist, and report their size and architecture.

Usage: check_repos.py REPO_ID...

Uses only the public Hub API (no login, standard library only). Exits non-zero if a repo
is missing, gated, private, renamed or unreachable, so a download never starts on a wrong ID.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def fetch(repo: str) -> dict:
    req = urllib.request.Request(
        f"{ENDPOINT}/api/models/{repo}?blobs=true",
        headers={"User-Agent": "local-llm/check_repos"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main(repos: list[str]) -> int:
    if not repos:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    ok = True
    total = 0
    for repo in repos:
        try:
            info = fetch(repo)
        except urllib.error.HTTPError as e:
            reason = {401: "missing or private", 403: "gated", 404: "not found"}
            print(f"[FAIL] {repo}: HTTP {e.code} ({reason.get(e.code, e.reason)})")
            ok = False
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[FAIL] {repo}: cannot reach {ENDPOINT}: {e}")
            ok = False
            continue

        problems = []
        if info.get("id") != repo:
            problems.append(f"canonical ID is {info.get('id')}")
        if info.get("gated"):
            problems.append(f"gated ({info['gated']})")
        if info.get("private"):
            problems.append("private")
        files = info.get("siblings", [])
        size = sum(f.get("size") or 0 for f in files)
        weights = [f for f in files if f["rfilename"].endswith(".safetensors")]
        if not weights:
            problems.append("no .safetensors files")
        cfg = info.get("config") or {}
        quant = (cfg.get("quantization_config") or {}).get("quant_method", "none")
        params = (info.get("safetensors") or {}).get("total")
        license_ = (info.get("cardData") or {}).get("license", "?")
        total += size

        params_text = f", {params / 1e9:.2f}B params" if params else ""
        print(f"[{'FAIL' if problems else ' OK '}] {repo}")
        print(
            f"       revision {info.get('sha', '?')}  modified {info.get('lastModified', '?')}"
        )
        print(
            f"       {size / 1e9:.1f} GB in {len(files)} files "
            f"({len(weights)} safetensors){params_text}"
        )
        print(
            f"       architectures {cfg.get('architectures', '?')}, "
            f"quantization {quant}, license {license_}"
        )
        for p in problems:
            print(f"       problem: {p}")
        ok &= not problems
    print(f"total download size: {total / 1e9:.1f} GB")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
