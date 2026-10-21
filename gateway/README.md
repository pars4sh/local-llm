# gateway/

The LiteLLM proxy that sits in front of every vLLM process. It exposes one port protected by an
API key, gives each model a stable alias (`qwen-main`, `qwen-fast`, `embed`, `rerank`), issues
one key per client, and keeps a usage log.

**Status:** not started (roadmap phase 1).

Planned contents:

| File | Purpose |
|---|---|
| `config.template.yaml` | LiteLLM config template; the job fills in the model ports from the roster |
| `pyproject.toml` / `uv.lock` | Pinned LiteLLM environment, separate from vLLM |

Client keys are created with the master key from `.env` and never committed.
