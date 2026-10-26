"""Integration tests: the real gateway (LiteLLM behind the route allowlist, started the way
cluster/launch.py starts it) in front of a fake OpenAI-compatible backend. Checks HTTP status
codes, which routes are served and which need a key, key-file handling, and that no key or
key hash reaches a log."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import launch
import pytest
import roster
from conftest import REPO
from local_llm_app import ALLOWED, BLOCKED_HEADER

PYTHON = REPO / "gateway" / ".venv" / "bin" / "python"
NEW_KEY = REPO / "scripts" / "new_client_key.sh"
OPEN = {"/health/liveliness"}  # the only route that answers without a key
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FakeBackend(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "qwen-main", "object": "model"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/chat/completions"):
            self._send(
                {
                    "id": "c1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": req["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "pong"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                        "total_tokens": 4,
                    },
                }
            )
        else:
            self._send({"error": "not here"}, 404)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def new_key(name: str, env: dict) -> str:
    out = subprocess.run(
        [str(NEW_KEY), name], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Gateway(dict):
    """The running gateway's details. Its repr hides keys and the environment."""

    def __repr__(self) -> str:
        return f"<gateway at {self['base']}>"


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    """Start the backend and the gateway; yield what the tests need."""
    tmp = tmp_path_factory.mktemp("gw")
    backend = ThreadingHTTPServer(("127.0.0.1", free_port()), FakeBackend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()

    master = "sk-" + os.urandom(32).hex()
    env = dict(os.environ)
    env.update(
        GATEWAY_MASTER_KEY=master,
        LLM_GATEWAY_KEYS=str(tmp / "gateway-keys.json"),
        LLM_RUN_DIR=str(tmp / "run"),
    )
    client_key = new_key("tester", env)

    model = roster.Model("qwen-main", "chat", "Qwen/x", 0.5, 1024, 1, True, ())
    config = tmp / "gateway.yaml"
    ports = {"qwen-main": backend.server_address[1]}
    config.write_text(json.dumps(launch.gateway_config(ports, [model])))
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        gw_env = launch.gateway_env(tmp / "run", config)
    finally:
        os.environ.clear()
        os.environ.update(saved)

    # Every route LiteLLM serves, listed after startup with the same config and env.
    routes_file = tmp / "routes.json"
    dump = Path(__file__).with_name("dump_routes.py")
    subprocess.run(
        [str(PYTHON), str(dump), str(routes_file)],
        env=gw_env,
        capture_output=True,
        check=True,
        timeout=300,
    )

    port = free_port()
    log_path = tmp / "gateway.log"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            launch.gateway_command(port),
            env=gw_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 90
    while not launch.healthy(port, "/health/liveliness"):
        assert proc.poll() is None, log_path.read_text()[-3000:]
        assert time.monotonic() < deadline, "gateway did not start"
        time.sleep(1)
    yield Gateway(
        routes=json.loads(routes_file.read_text()),
        base=f"http://127.0.0.1:{port}",
        master=master,
        client=client_key,
        env=env,
        log=log_path,
        run=tmp / "run",
        keys_file=tmp / "gateway-keys.json",
        issued=[client_key],
    )
    os.killpg(proc.pid, 15)
    proc.wait(timeout=30)
    backend.shutdown()


def call(base, path, headers=None, method="GET", body=None, blocked=False):
    """The status code; with blocked=True, whether the allowlist refused the route."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with _http.open(req, timeout=15) as r:
            status, resp_headers = r.status, r.headers
    except urllib.error.HTTPError as e:
        status, resp_headers = e.code, e.headers
    if blocked:
        return status == 404 and resp_headers.get(BLOCKED_HEADER) == "route-not-allowed"
    return status


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


# --- status codes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_headers, expected",
    [
        (lambda g: {}, 401),
        (lambda g: bearer("sk-" + "0" * 64), 401),
        (lambda g: {"Authorization": "Bearer"}, 401),
        (lambda g: {"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}, 401),
        (lambda g: {"Authorization": "Token " + g["client"]}, 401),
        (lambda g: {"Authorization": "Bearer " + g["client"][:-1]}, 401),
        (lambda g: {"x-api-key": "sk-wrong"}, 401),
        (lambda g: bearer(g["client"]), 200),
        (lambda g: bearer(g["master"]), 200),
        (lambda g: {"x-api-key": g["client"]}, 200),
        (lambda g: {"api-key": g["client"]}, 200),
    ],
    ids=[
        "no-key",
        "wrong-key",
        "bearer-without-token",
        "basic-auth",
        "wrong-scheme",
        "truncated-key",
        "wrong-x-api-key",
        "client-key",
        "master-key",
        "client-key-in-x-api-key",
        "client-key-in-api-key",
    ],
)
def test_models_route_status(gateway, make_headers, expected):
    assert call(gateway["base"], "/v1/models", make_headers(gateway)) == expected


def test_chat_needs_a_key_and_works_with_one(gateway):
    body = {"model": "qwen-main", "messages": [{"role": "user", "content": "ping"}]}
    base = gateway["base"]
    assert call(base, "/v1/chat/completions", {}, "POST", body) == 401
    assert call(base, "/v1/chat/completions", bearer("sk-x"), "POST", body) == 401
    assert (
        call(base, "/v1/chat/completions", bearer(gateway["client"]), "POST", body)
        == 200
    )


def test_revoked_key_gets_401(gateway):
    key = new_key("temporary", gateway["env"])
    gateway["issued"].append(key)
    assert call(gateway["base"], "/v1/models", bearer(key)) == 200
    subprocess.run(
        [str(NEW_KEY), "--revoke", "temporary"],
        env=gateway["env"],
        capture_output=True,
        check=True,
    )
    assert call(gateway["base"], "/v1/models", bearer(key)) == 401
    assert call(gateway["base"], "/v1/models", bearer(gateway["client"])) == 200


# --- which routes are served -----------------------------------------------------------


def _http_routes(gateway) -> list[tuple[str, str]]:
    pairs = set()
    for kind, path, methods in gateway["routes"]:
        if kind == "ws":
            continue
        path = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods:
            if method not in ("HEAD", "OPTIONS"):
                pairs.add((method, path))
    return sorted(pairs)


def test_only_inference_routes_are_served_and_they_need_a_key(gateway):
    pairs = _http_routes(gateway)
    served = [(m, p) for m, p in pairs if ALLOWED.fullmatch(p)]
    blocked = [(m, p) for m, p in pairs if not ALLOWED.fullmatch(p)]
    assert len(blocked) > 500 and 10 < len(served) < 40  # we saw LiteLLM's whole table
    base, problems, answered = gateway["base"], [], []
    for m, p in pairs:
        body = {} if m != "GET" else None
        no_key = call(base, p, None, m, body)
        if no_key not in (401, 403, 404):
            answered.append(f"{m} {p} -> {no_key}")
        if ALLOWED.fullmatch(p):
            expected_no_key = 200 if p in OPEN else 401
            if no_key != expected_no_key:
                problems.append(f"{m} {p} without a key -> {no_key}")
            with_key = call(base, p, bearer(gateway["client"]), m, body)
            refused = with_key in (401, 403) or call(
                base, p, bearer(gateway["client"]), m, body, blocked=True
            )
            if refused and p not in OPEN:
                problems.append(f"{m} {p} with a client key -> {with_key}")
        else:
            for who, key in (("no", None), ("client", gateway["client"]),
                             ("master", gateway["master"])):  # fmt: skip
                if not call(
                    base, p, bearer(key) if key else None, m, body, blocked=True
                ):
                    problems.append(f"{m} {p} with {who} key was not blocked")
    print(
        f"\n{len(pairs)} method/route pairs, {len(served)} served; answered without a key:"
    )
    print("\n".join(answered))
    assert not problems, "\n".join(problems)
    assert {a.split()[1] for a in answered} <= OPEN


def test_websocket_routes_are_refused(gateway):
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    ws_base = gateway["base"].replace("http://", "ws://")
    paths = [p for kind, p, _ in gateway["routes"] if kind == "ws"]
    assert paths
    for path in paths:
        url = ws_base + re.sub(r"\{[^}]+\}", "x", path) + "?model=qwen-main"
        for headers in ({}, bearer(gateway["client"])):
            with (
                pytest.raises(InvalidStatus) as e,
                connect(url, additional_headers=headers, open_timeout=10, proxy=None),
            ):
                pass
            assert e.value.response.status_code == 403, path


# --- keys file and logs ----------------------------------------------------------------


def test_keys_file_is_private_and_kept_out_of_git(gateway):
    assert gateway["keys_file"].stat().st_mode & 0o777 == 0o600
    inside = REPO / "config" / "test-keys.json"
    env = {**gateway["env"], "LLM_GATEWAY_KEYS": str(inside)}
    out = subprocess.run(
        [str(NEW_KEY), "nope"], check=False, env=env, capture_output=True, text=True
    )
    assert out.returncode != 0 and "not gitignored" in out.stderr
    assert not inside.exists()
    ignored = subprocess.run(
        ["git", "-C", str(REPO), "check-ignore", "-q", "gateway-keys.json"], check=False
    )
    assert ignored.returncode == 0


def test_no_key_or_hash_in_any_log(gateway):
    # Runs last in this module: by now every kind of request above has been logged.
    texts = [gateway["log"].read_text(errors="replace")]
    texts += [p.read_text() for p in Path(gateway["run"]).rglob("*.jsonl")]
    text = "\n".join(texts)
    assert "tester" in text  # the usage log names the client
    for secret in [*gateway["issued"], gateway["master"]]:
        assert secret not in text
        assert sha256(secret) not in text
