"""Client-key auth and a usage log for the LiteLLM gateway, without a database.

LiteLLM's own per-client keys need Postgres, which a SLURM job doesn't have (see D-013).
Instead, the gateway config that cluster/launch.py generates wires in:

- `user_api_key_auth`: a request passes with the master key ($LITELLM_MASTER_KEY) or with a
  client key whose SHA-256 is listed in the keys file ($LLM_GATEWAY_KEYS). The file is reread
  when it changes, so keys made with scripts/new_client_key.sh work without a restart.
- `usage_log`: one JSON line per request in $LLM_RUN_DIR/usage/<YYYY-MM>.jsonl with the time,
  client, model, call type, token counts, latency and status. Never any content.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import threading
from pathlib import Path

from fastapi import Request
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import (
    LitellmUserRoles,
    ProxyErrorTypes,
    ProxyException,
    UserAPIKeyAuth,
)

_keys_lock = threading.Lock()
_keys_cache: dict = {"mtime": None, "keys": {}}


def _client_keys() -> dict[str, str]:
    """{sha256 hex digest: client name} from the keys file; empty if there is none."""
    path = Path(os.environ["LLM_GATEWAY_KEYS"])
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    with _keys_lock:
        if mtime != _keys_cache["mtime"]:
            clients = json.loads(path.read_text()).get("clients", {})
            _keys_cache["keys"] = {c["sha256"]: name for name, c in clients.items()}
            _keys_cache["mtime"] = mtime
        return _keys_cache["keys"]


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    key = (api_key or "").removeprefix("Bearer ").strip()
    master = os.environ.get("LITELLM_MASTER_KEY", "")
    if master and hmac.compare_digest(key.encode(), master.encode()):
        return UserAPIKeyAuth(
            api_key="master", key_alias="master", user_role=LitellmUserRoles.PROXY_ADMIN
        )
    digest = hashlib.sha256(key.encode()).hexdigest()
    name = _client_keys().get(digest)
    if name is None:
        raise ProxyException(
            message="invalid API key",
            type=ProxyErrorTypes.auth_error,
            param="api_key",
            code=401,
        )
    return UserAPIKeyAuth(api_key=digest, key_alias=name)


class UsageLog(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self._dir = Path(os.environ["LLM_RUN_DIR"]) / "usage"
        self._lock = threading.Lock()

    def _write(
        self, kwargs: dict, start_time: dt.datetime, end_time: dt.datetime
    ) -> None:
        p = kwargs.get("standard_logging_object") or {}
        meta = p.get("metadata") or {}
        record = {
            "time": start_time.astimezone().isoformat(timespec="seconds"),
            "client": meta.get("user_api_key_alias"),
            "model": p.get("model_group") or p.get("model"),
            "call": p.get("call_type"),
            "prompt_tokens": p.get("prompt_tokens"),
            "completion_tokens": p.get("completion_tokens"),
            "seconds": round((end_time - start_time).total_seconds(), 2),
            "status": p.get("status"),
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self._dir / f"{start_time:%Y-%m}.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._write(kwargs, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._write(kwargs, start_time, end_time)


usage_log = UsageLog()
