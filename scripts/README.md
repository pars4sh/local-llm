# scripts/

Helpers you run by hand, mostly on the login node.

**Status:** `download_models.sh` and `check_repos.py` exist; `new_client_key.sh` is planned.

Planned contents:

| File | Purpose |
|---|---|
| `download_models.sh` | Downloads the enabled roster checkpoints (or named ones, or `--all`) into `$LLM_MODEL_DIR` with a pinned `hf`; `--check` verifies the repo IDs and shows sizes and free space (login node; jobs run offline) |
| `check_repos.py` | Checks Hugging Face repo IDs through the public API; used by `download_models.sh --check` |
| `env.sh` | Shared bash helpers: `.env` loading, roster access, pip CUDA toolkit setup |
| `lint.sh` | Pins ruff (0.15.4) and shellcheck (v0.11.0) and lints the whole repo: `shellcheck`, `bash -n`, `ruff check`, `ruff format --check`; `--fix` applies ruff's fixes. Docker on the laptop, `uvx` and a matching `shellcheck` elsewhere |
| `new_client_key.sh` | Issues a gateway API key for a new client using the master key |
