# client/

The laptop side: the Docker Compose stack and the SSH tunnel to the gateway.

**Status:** not started (roadmap phase 2).

Planned contents:

| File | Purpose |
|---|---|
| `compose.yaml` | Open WebUI, SearXNG, Docling (docling-serve) and Open Terminal, with pinned tags, ports bound to `127.0.0.1` and named volumes |
| `searxng/settings.yml` | SearXNG config: JSON output enabled for Open WebUI, rate limiting off for local use |
| `tunnel.sh` | Finds the node and port from the newest run file, forwards `127.0.0.1:$GATEWAY_LOCAL_PORT`, reconnects when the job moves |

## Manual Open WebUI settings

Settings that can't be set through environment variables are listed here as they come up, so a
fresh install can be reproduced.

Conventions for this side are in `.claude/rules/client.md`.
