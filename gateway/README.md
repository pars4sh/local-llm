# gateway/

The LiteLLM proxy that sits in front of every vLLM process. It exposes one port protected by an
API key, gives each model a stable alias (`qwen-main`, `qwen-fast`, `embed`, `rerank`), accepts
one key per client, and keeps a usage log.

**Status:** runs inside the serving job; see D-013 for the design.

| File | Purpose |
|---|---|
| `pyproject.toml` / `uv.lock` | Pinned LiteLLM environment, separate from vLLM (D-011), with pytest as a dev dependency |
| `local_llm_app.py` | The ASGI app: LiteLLM behind a route allowlist |
| `local_llm_gateway.py` | Client-key auth and the usage log, without a database |
| `tests/` | Auth and route tests; run them after any change here or any LiteLLM upgrade |

There is no config template. `cluster/launch.py` generates the LiteLLM config from the roster
when the job starts (one alias per enabled model, pointing at its local vLLM port), writes it to
the job's log directory, and starts `uvicorn local_llm_app:app` on all interfaces.

**Routes.** Only chat, completions, embeddings, rerank and model listing reach LiteLLM, and each
needs a key. `/health/liveliness` is the only open route. Everything else gets 404 (header
`x-local-llm: route-not-allowed`) and WebSockets are refused, for every caller.

**Keys.**
- The master key (`GATEWAY_MASTER_KEY` in `.env`) is for admin use.
- Each client gets its own key from `scripts/new_client_key.sh NAME`. The script prints the key
  once and stores only its SHA-256 in `$LLM_GATEWAY_KEYS` (default
  `$LLM_RUN_DIR/gateway-keys.json`, mode 600). A running gateway picks up new and revoked keys on
  the next request.
- Requests without a valid key get HTTP 401.
- Deny by default: if the keys file is missing, unreadable, malformed or readable by other users,
  every client key is refused and the gateway log says why. The master key keeps working.

**Usage log.** One JSON line per request in `$LLM_RUN_DIR/usage/<YYYY-MM>.jsonl`: time, client,
model, call type, token counts, latency and status. It never contains prompts or replies.

**Offline.** The job sets `LITELLM_LOCAL_MODEL_COST_MAP` and `LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS`
so LiteLLM fetches nothing at startup, and turns off telemetry, the admin UI and the API docs.

**Tests.** `uv run --project gateway pytest gateway/tests` (from the repository root, on the login
node; no GPU needed). They start the real gateway in front of a fake backend.
