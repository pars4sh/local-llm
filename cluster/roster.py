#!/usr/bin/env python3
"""Read and validate the model roster, and build each model's `vllm serve` command.

The roster (config/models.toml, or $LLM_ROSTER) is the single source of truth for the
models the serving job runs. Standard library only, so it runs under any Python >= 3.11.

Usage:
    roster.py validate
    roster.py list [--all]                  names in start order (enabled only by default)
    roster.py repos [--all]                 "<name> <kind> <hf_repo>" lines in start order
    roster.py client-env                    the laptop client's model per role, as KEY=VALUE
    roster.py command NAME --port N [--model-dir DIR] [--vllm PATH]
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROSTER = Path(__file__).resolve().parent.parent / "config" / "models.toml"
MAX_TOTAL_SHARE = 0.9
KINDS = ("chat", "embed", "rerank")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
FIELDS = {
    "name": str,
    "kind": str,
    "hf_repo": str,
    "gpu_memory_utilization": float,
    "max_model_len": int,
    "order": int,
    "enabled": bool,
    "extra_args": list,
}
# Set by the launcher from the fields above, so they must not appear in extra_args.
OWNED_FLAGS = (
    "--host",
    "--port",
    "--served-model-name",
    "--gpu-memory-utilization",
    "--max-model-len",
)
# [client] keys: the model the laptop client uses for each role. Key -> (env var, kind).
CLIENT_ROLES = {
    "default_model": ("LLM_DEFAULT_MODEL", "chat"),
    "task_model": ("LLM_TASK_MODEL", "chat"),
    "embedding_model": ("LLM_EMBED_MODEL", "embed"),
    "reranking_model": ("LLM_RERANK_MODEL", "rerank"),
}


class RosterError(Exception):
    pass


@dataclass(frozen=True)
class Model:
    name: str
    kind: str
    hf_repo: str
    gpu_memory_utilization: float
    max_model_len: int
    order: int
    enabled: bool
    extra_args: tuple[str, ...]

    def weights_dir(self, model_dir: Path) -> Path:
        return model_dir / self.hf_repo


def roster_path() -> Path:
    return Path(os.environ.get("LLM_ROSTER") or DEFAULT_ROSTER)


def _check_type(value: object, expected: type) -> bool:
    # bool is a subclass of int; don't accept true/false where a number is expected.
    if expected in (int, float) and isinstance(value, bool):
        return False
    if expected is float:
        return isinstance(value, (int, float))
    return isinstance(value, expected)


def _parse_entry(i: int, entry: dict, errors: list[str]) -> Model | None:
    label = f"model #{i + 1} ({entry.get('name', '?')})"
    missing = [k for k in FIELDS if k not in entry]
    unknown = [k for k in entry if k not in FIELDS]
    if missing:
        errors.append(f"{label}: missing fields: {', '.join(missing)}")
    if unknown:
        errors.append(f"{label}: unknown fields: {', '.join(unknown)}")
    if missing:
        return None
    bad = [k for k, t in FIELDS.items() if not _check_type(entry[k], t)]
    if bad:
        errors.append(f"{label}: wrong type for: {', '.join(bad)}")
        return None

    m = Model(
        name=entry["name"],
        kind=entry["kind"],
        hf_repo=entry["hf_repo"],
        gpu_memory_utilization=float(entry["gpu_memory_utilization"]),
        max_model_len=entry["max_model_len"],
        order=entry["order"],
        enabled=entry["enabled"],
        extra_args=tuple(entry["extra_args"]),
    )
    if not NAME_RE.match(m.name):
        errors.append(f"{label}: name must match {NAME_RE.pattern}")
    if m.kind not in KINDS:
        errors.append(f"{label}: kind must be one of {', '.join(KINDS)}")
    if not REPO_RE.match(m.hf_repo):
        errors.append(f"{label}: hf_repo must look like 'org/name'")
    if not 0 < m.gpu_memory_utilization < 1:
        errors.append(f"{label}: gpu_memory_utilization must be between 0 and 1")
    if m.max_model_len <= 0:
        errors.append(f"{label}: max_model_len must be positive")
    if not all(isinstance(a, str) for a in m.extra_args):
        errors.append(f"{label}: extra_args must be a list of strings")
    else:
        owned = [
            a for a in m.extra_args if a.split("=", 1)[0] in OWNED_FLAGS or a == "serve"
        ]
        if owned:
            errors.append(
                f"{label}: extra_args must not set {', '.join(owned)}; "
                "use the roster fields instead"
            )
    return m


def load(path: Path | None = None) -> list[Model]:
    """Load and validate the roster; return all models sorted by start order."""
    path = path or roster_path()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise RosterError(f"{path}: {e}") from e

    errors: list[str] = []
    unknown_top = [k for k in data if k not in ("model", "client")]
    if unknown_top:
        errors.append(f"unknown top-level keys: {', '.join(unknown_top)}")
    entries = data.get("model", [])
    if not isinstance(entries, list) or not entries:
        raise RosterError(f"{path}: no [[model]] entries")

    models = [m for i, e in enumerate(entries) if (m := _parse_entry(i, e, errors))]
    for field in ("name", "order"):
        seen: set[object] = set()
        for m in models:
            value = getattr(m, field)
            if value in seen:
                errors.append(f"duplicate {field}: {value}")
            seen.add(value)

    total = sum(m.gpu_memory_utilization for m in models if m.enabled)
    if round(total, 6) > MAX_TOTAL_SHARE:
        errors.append(
            f"enabled gpu_memory_utilization shares sum to {total:.2f} "
            f"(limit {MAX_TOTAL_SHARE})"
        )
    _check_client(data.get("client", {}), models, errors)
    if errors:
        raise RosterError(f"{path}:\n  " + "\n  ".join(errors))
    return sorted(models, key=lambda m: m.order)


def _check_client(client: object, models: list[Model], errors: list[str]) -> None:
    if not isinstance(client, dict):
        errors.append("[client] must be a table")
        return
    missing = [k for k in CLIENT_ROLES if k not in client]
    unknown = [k for k in client if k not in CLIENT_ROLES]
    if missing:
        errors.append(f"[client]: missing keys: {', '.join(missing)}")
    if unknown:
        errors.append(f"[client]: unknown keys: {', '.join(unknown)}")
    by_name = {m.name: m for m in models}
    for key, name in client.items():
        if key not in CLIENT_ROLES:
            continue
        kind = CLIENT_ROLES[key][1]
        m = by_name.get(name) if isinstance(name, str) else None
        if m is None or not m.enabled or m.kind != kind:
            errors.append(f"[client] {key}: must name an enabled {kind} model")


def client_env(path: Path | None = None) -> dict[str, str]:
    """The models the laptop client uses for each role, as environment variables."""
    path = path or roster_path()
    load(path)
    client = tomllib.loads(path.read_text())["client"]
    return {CLIENT_ROLES[k][0]: client[k] for k in CLIENT_ROLES}


def enabled(models: list[Model]) -> list[Model]:
    return [m for m in models if m.enabled]


def find(models: list[Model], name: str) -> Model:
    for m in models:
        if m.name == name:
            return m
    raise RosterError(f"no model named {name!r} in the roster")


def build_command(
    model: Model,
    port: int,
    model_dir: Path,
    vllm: str = "vllm",
    host: str = "127.0.0.1",
) -> list[str]:
    """The full `vllm serve` argv for one model."""
    return [
        vllm,
        "serve",
        str(model.weights_dir(model_dir)),
        "--host",
        host,
        "--port",
        str(port),
        "--served-model-name",
        model.name,
        "--gpu-memory-utilization",
        f"{model.gpu_memory_utilization:g}",
        "--max-model-len",
        str(model.max_model_len),
        *model.extra_args,
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check the roster and print a summary")
    for name in ("list", "repos"):
        s = sub.add_parser(name)
        s.add_argument("--all", action="store_true", help="include disabled models")
    sub.add_parser("client-env", help="print the client's model per role as KEY=VALUE")
    c = sub.add_parser("command", help="print the vllm serve command for a model")
    c.add_argument("name")
    c.add_argument("--port", type=int, required=True)
    c.add_argument("--model-dir", default=os.environ.get("LLM_MODEL_DIR"))
    c.add_argument("--vllm", default="vllm")
    args = p.parse_args(argv)

    try:
        models = load()
        if args.cmd == "validate":
            for m in models:
                state = "enabled " if m.enabled else "disabled"
                print(
                    f"{m.order:>3}  {state}  {m.name:<12} {m.kind:<7} "
                    f"{m.gpu_memory_utilization:.2f}  {m.max_model_len:>7}  {m.hf_repo}"
                )
            total = sum(m.gpu_memory_utilization for m in enabled(models))
            print(f"enabled share: {total:.2f} of {MAX_TOTAL_SHARE}")
        elif args.cmd in ("list", "repos"):
            for m in models if args.all else enabled(models):
                print(
                    m.name if args.cmd == "list" else f"{m.name} {m.kind} {m.hf_repo}"
                )
        elif args.cmd == "client-env":
            for key, value in client_env().items():
                print(f"{key}={value}")
        elif args.cmd == "command":
            if not args.model_dir:
                raise RosterError("set LLM_MODEL_DIR or pass --model-dir")
            cmd = build_command(
                find(models, args.name), args.port, Path(args.model_dir), args.vllm
            )
            print(shlex.join(cmd))
    except RosterError as e:
        print(f"roster: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
