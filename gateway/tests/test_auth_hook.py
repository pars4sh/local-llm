"""Unit tests for the gateway's auth hook (gateway/local_llm_gateway.py)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import secrets

import local_llm_gateway as gw
import pytest
from litellm.proxy._types import ProxyException

MASTER = "sk-" + secrets.token_hex(32)
KEYS = {
    name: "sk-" + secrets.token_hex(32) for name in ("open-webui", "agent", "evals")
}


def sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def write_keys(path, keys: dict[str, str], mode: int = 0o600) -> None:
    """Write a keys file the way scripts/new_client_key.sh does (new file, then rename)."""
    data = {
        "clients": {n: {"sha256": sha256(k), "created": "now"} for n, k in keys.items()}
    }
    tmp = f"{path}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    os.chmod(path, mode)


def auth(api_key):
    return asyncio.run(gw.user_api_key_auth(request=None, api_key=api_key))


def refused(api_key) -> bool:
    with pytest.raises(ProxyException) as e:
        auth(api_key)
    return str(e.value.code) == "401"


@pytest.fixture
def keys_file(tmp_path, monkeypatch):
    path = tmp_path / "gateway-keys.json"
    monkeypatch.setenv("LLM_GATEWAY_KEYS", str(path))
    monkeypatch.setenv("LITELLM_MASTER_KEY", MASTER)
    gw._cache.update(signature=None, keys={})
    gw._last_problem[0] = None
    return path


@pytest.fixture
def logs():
    """Everything the hook logs, as text."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    gw.log.addHandler(handler)
    yield stream
    gw.log.removeHandler(handler)


# --- valid, wrong, missing, malformed and revoked keys ---------------------------------


def test_valid_client_key_is_accepted_as_its_name(keys_file):
    write_keys(keys_file, KEYS)
    user = auth(KEYS["agent"])
    assert user.key_alias == "agent"
    assert user.api_key == "client:agent"


def test_master_key_is_accepted(keys_file):
    write_keys(keys_file, KEYS)
    assert auth(MASTER).key_alias == "master"


def test_bearer_prefix_is_case_insensitive(keys_file):
    write_keys(keys_file, KEYS)
    assert auth("bearer " + KEYS["evals"]).key_alias == "evals"


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_no_key_is_refused(keys_file, api_key):
    write_keys(keys_file, KEYS)
    assert refused(api_key)


def test_wrong_key_is_refused(keys_file):
    write_keys(keys_file, KEYS)
    assert refused("sk-" + secrets.token_hex(32))


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNzd29yZA==",
        "Token " + KEYS["agent"],
        KEYS["agent"] + " extra",
        KEYS["agent"][:-1],
        "Bearer Bearer " + KEYS["agent"],
    ],
)
def test_malformed_header_is_refused(keys_file, header):
    write_keys(keys_file, KEYS)
    assert refused(header)


def test_revoked_key_is_refused_on_the_next_request(keys_file):
    write_keys(keys_file, KEYS)
    assert auth(KEYS["agent"]).key_alias == "agent"
    write_keys(keys_file, {n: k for n, k in KEYS.items() if n != "agent"})
    assert refused(KEYS["agent"])
    assert auth(KEYS["evals"]).key_alias == "evals"


def test_empty_master_key_never_matches(keys_file, monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "")
    write_keys(keys_file, KEYS)
    assert refused("")
    assert refused("Bearer ")


# --- deny by default -------------------------------------------------------------------


def _missing(path):
    pass


def _empty(path):
    path.write_text("")
    path.chmod(0o600)


def _not_json(path):
    path.write_text("{not json")
    path.chmod(0o600)


def _bad_digest(path):
    path.write_text(json.dumps({"clients": {"x": {"sha256": "not-a-digest"}}}))
    path.chmod(0o600)


def _no_clients_key(path):
    path.write_text(json.dumps({"keys": []}))
    path.chmod(0o600)


def _unreadable(path):
    write_keys(path, KEYS, mode=0o000)


def _group_readable(path):
    write_keys(path, KEYS, mode=0o640)


def _world_readable(path):
    write_keys(path, KEYS, mode=0o644)


def _a_directory(path):
    path.mkdir()


@pytest.mark.parametrize(
    "make, reason",
    [
        (_missing, "cannot stat"),
        (_empty, "malformed"),
        (_not_json, "malformed"),
        (_bad_digest, "malformed"),
        (_no_clients_key, "malformed"),
        (_unreadable, "cannot read"),
        (_group_readable, "chmod 600"),
        (_world_readable, "chmod 600"),
        (_a_directory, "not a regular file"),
    ],
)
def test_untrustworthy_keys_file_refuses_every_client_key(
    keys_file, logs, make, reason
):
    if make is _unreadable and os.geteuid() == 0:
        pytest.skip("root can read a mode-000 file")
    make(keys_file)
    for key in KEYS.values():
        assert refused(key)
    assert reason in logs.getvalue()
    assert "refusing all client keys" in logs.getvalue()
    assert auth(MASTER).key_alias == "master"  # the admin can still get in


def test_unset_keys_path_refuses_every_client_key(keys_file, logs, monkeypatch):
    write_keys(keys_file, KEYS)
    monkeypatch.delenv("LLM_GATEWAY_KEYS")
    assert refused(KEYS["agent"])
    assert "LLM_GATEWAY_KEYS is not set" in logs.getvalue()


def test_file_turning_untrustworthy_after_use_refuses_keys(keys_file):
    write_keys(keys_file, KEYS)
    assert auth(KEYS["agent"]).key_alias == "agent"
    keys_file.chmod(0o644)
    assert refused(KEYS["agent"])
    keys_file.chmod(0o000)
    if os.geteuid() != 0:
        assert refused(KEYS["agent"])


def test_a_problem_is_logged_once_not_per_request(keys_file, logs):
    for _ in range(5):
        refused(KEYS["agent"])
    assert logs.getvalue().count("refusing all client keys") == 1


# --- constant-time comparison ----------------------------------------------------------


def test_every_digest_is_compared_with_compare_digest(keys_file, monkeypatch):
    write_keys(keys_file, KEYS)
    calls = []
    real = gw.hmac.compare_digest

    def spy(a, b):
        calls.append((len(a), len(b)))
        return real(a, b)

    monkeypatch.setattr(gw.hmac, "compare_digest", spy)
    for key in (KEYS["open-webui"], "sk-wrong", MASTER):
        calls.clear()
        try:
            auth(key)
        except ProxyException:
            pass
        # Master plus every client, whether or not one matched, all 32-byte digests.
        assert len(calls) == 1 + len(KEYS)
        assert set(calls) == {(32, 32)}


# --- no secrets in logs ----------------------------------------------------------------


def test_logs_never_contain_keys_or_hashes(keys_file, logs, monkeypatch):
    write_keys(keys_file, KEYS)
    for key in [*KEYS.values(), MASTER, "sk-wrong"]:
        try:
            auth(key)
        except ProxyException:
            pass
    _not_json(keys_file)
    refused(KEYS["agent"])
    monkeypatch.delenv("LLM_GATEWAY_KEYS")
    refused(KEYS["agent"])
    text = logs.getvalue()
    assert text  # the problems above were logged
    for secret in [*KEYS.values(), MASTER]:
        assert secret not in text
        assert sha256(secret) not in text
