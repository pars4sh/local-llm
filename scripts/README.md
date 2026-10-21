# scripts/

Helpers you run by hand, mostly on the login node.

**Status:** not started.

Planned contents:

| File | Purpose |
|---|---|
| `download_models.sh` | Downloads every checkpoint in the roster into `$LLM_MODEL_DIR` (login node; jobs run offline) |
| `new_client_key.sh` | Issues a gateway API key for a new client using the master key |
