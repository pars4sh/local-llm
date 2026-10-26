"""Client-key auth and a usage log for the LiteLLM gateway, without a database.

LiteLLM's own per-client keys need Postgres, which a SLURM job doesn't have (see D-013).
Instead, the gateway config that cluster/launch.py generates wires in:

- `user_api_key_auth`: a request passes with the master key ($LITELLM_MASTER_KEY) or with a
  client key whose SHA-256 is listed in the keys file ($LLM_GATEWAY_KEYS). The file is reread
  when it changes, so keys made or revoked with scripts/new_client_key.sh take effect on the
  next request. Deny by default: if the file is missing, empty, unreadable, malformed or
  readable by other users, every client key is refused and an error is logged.
- `usage_log`: one JSON line per request in $LLM_RUN_DIR/usage/<YYYY-MM>.jsonl with the time,
  client, model, call type, token counts, latency and status. Never any content.

Neither ever logs a key or a key hash.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import stat
import threading
from pathlib import Path

from fastapi import Request
from litellm._logging import verbose_proxy_logger as log
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import (
    LitellmUserRoles,
    ProxyErrorTypes,
    ProxyException,
    UserAPIKeyAuth,
)

_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
_lock = threading.Lock()
_cache: dict = {"signature": None, "keys": {}}
_last_problem: list[str | None] = [None]


def _problem(message: str) -> dict[bytes, str]:
    """Log a keys-file problem once (until it changes) and deny every client key."""
    if message != _last_problem[0]:
        log.error("gateway keys: %s; refusing all client keys", message)
        _last_problem[0] = message
    _cache.update(signature=None, keys={})
    return {}


def _client_keys() -> dict[bytes, str]:
    """{SHA-256 digest: client name} from the keys file, or {} if it can't be trusted."""
    path = os.environ.get("LLM_GATEWAY_KEYS")
    if not path:
        return _problem("LLM_GATEWAY_KEYS is not set")
    with _lock:
        try:
            st = os.stat(path)
        except OSError as e:
            return _problem(f"cannot stat {path}: {e.strerror}")
        if not stat.S_ISREG(st.st_mode):
            return _problem(f"{path} is not a regular file")
        if st.st_mode & 0o077:
            return _problem(
                f"{path} has mode {stat.S_IMODE(st.st_mode):o}; other users must have no "
                "access (chmod 600)"
            )
        # ctime changes with permissions too, so a chmod also forces a reread.
        signature = (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if signature == _cache["signature"]:
            return _cache["keys"]
        try:
            clients = json.loads(Path(path).read_text())["clients"]
            keys = {}
            for name, entry in clients.items():
                digest = entry["sha256"]
                if not isinstance(digest, str) or not _HEX_DIGEST.fullmatch(digest):
                    raise ValueError(f"client {name!r} has no valid sha256")
                keys[bytes.fromhex(digest)] = name
        except OSError as e:
            return _problem(f"cannot read {path}: {e.strerror}")
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            return _problem(f"{path} is malformed ({type(e).__name__})")
        if not keys:
            log.warning("gateway keys: %s lists no clients", path)
        _cache.update(signature=signature, keys=keys)
        _last_problem[0] = None
        return keys


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _reject() -> ProxyException:
    return ProxyException(
        message="invalid API key",
        type=ProxyErrorTypes.auth_error,
        param="api_key",
        code=401,
    )


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    # LiteLLM has already taken the token out of the Authorization (or a provider) header.
    key = (api_key or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if not key:
        raise _reject()
    digest = _digest(key)
    # Fixed-length digests, compared in constant time against every entry (no early exit).
    master = os.environ.get("LITELLM_MASTER_KEY", "")
    is_master = hmac.compare_digest(digest, _digest(master)) and bool(master)
    client = None
    for stored, name in _client_keys().items():
        if hmac.compare_digest(digest, stored):
            client = name
    if is_master:
        return UserAPIKeyAuth(
            api_key="master", key_alias="master", user_role=LitellmUserRoles.PROXY_ADMIN
        )
    if client is not None:
        # The identity LiteLLM carries around (and may log) is the name, never the key or hash.
        return UserAPIKeyAuth(api_key=f"client:{client}", key_alias=client)
    raise _reject()


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
