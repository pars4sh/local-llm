# client/

The laptop side: the Docker Compose stack and the SSH tunnel to the gateway.

**Status:** phase 2. Versions and the reasons behind them are in D-014 (`docs/decisions.md`).

| File | Purpose |
|---|---|
| `tunnel.sh` | Finds the node and gateway port from the newest run file of a running job, forwards `127.0.0.1:$GATEWAY_LOCAL_PORT` to it through the login host, and follows the job when it moves |
| `compose.sh` | Runs `docker compose` with the repository's `.env` and the client models from the roster (`cluster/roster.py client-env`) |
| `compose.yaml` | Open WebUI, SearXNG, Docling (docling-serve) and Open Terminal: pinned tags, only Open WebUI published (on `127.0.0.1`), named volumes |
| `searxng/settings.yml` | SearXNG config: JSON output for Open WebUI, rate limiting off for local use |

## Setup

1. Fill in the laptop section of `.env` (template: `.env.example`).
   - `CLUSTER_SSH_HOST`: the login host, reachable with a key and no prompt.
   - `GATEWAY_API_KEY`: on the cluster, `scripts/new_client_key.sh open-webui`.
   - `WEBUI_SECRET_KEY`, `SEARXNG_SECRET`, `OPEN_TERMINAL_API_KEY`: `openssl rand -hex 32` each.
   - `OPEN_TERMINAL_DIR`: an empty folder outside the repository. It becomes the terminal's
     home directory, so Open Terminal writes `.bashrc`, `.profile` and `.local/` into it.
     Keep SSH keys, tokens, `.env` files and patient data out of it.
2. Start the tunnel and leave it running (it reconnects by itself): `client/tunnel.sh`
3. Start the stack: `client/compose.sh up -d`, then open `http://localhost:3000`
   (`OPENWEBUI_PORT`).

Other commands: `client/compose.sh ps`, `client/compose.sh logs -f open-webui`,
`client/compose.sh down` (keeps the volumes).

Python 3.11 or newer must be on `PATH` for `compose.sh` (it reads the roster).

## How it is wired

Open WebUI is configured entirely through environment variables in `compose.yaml`, with
`ENABLE_PERSISTENT_CONFIG=false`: those variables apply on every start, and changes to the same
settings in the admin panel are lost on restart. Change `compose.yaml` instead.

| Setting | Value |
|---|---|
| Chat models | the gateway at `host.docker.internal:$GATEWAY_LOCAL_PORT/v1` |
| Default model, task model | from the roster's `[client]` table (`qwen-main`, `qwen-fast`) |
| Embeddings, reranking | the gateway (`embed` through `/v1/embeddings`, `rerank` through `/v1/rerank`), hybrid search on |
| Document extraction | Docling at `docling:5001` |
| Web search | SearXNG at `searxng:8080` |
| Open Terminal | system connection `Sandbox`, proxied through the Open WebUI backend |
| Function calling | native (Open WebUI 0.12's default for every model) |
| Thinking | the gateway streams it as `reasoning_content`; Open WebUI shows it as a collapsible block |

Docling and Open Terminal sit on the `sandbox` network, which is `internal: true`: they can't
reach the internet. Open Terminal therefore can't read the web, and a prompt injected through
it has nowhere to send data (D-008).

## Manual Open WebUI settings

These can't be set through environment variables. Repeat them after a fresh install.

1. **Admin account.** The first account created at `http://localhost:3000` becomes the admin.
2. **Hide `embed` and `rerank` from the model picker.** Admin Panel → Settings → Models → for each,
   the eye icon. They aren't chat models; Open WebUI still uses them for RAG.

## Linux and Colima

`compose.yaml` maps `host.docker.internal` to the host on every service that reaches the tunnel.
On Docker Desktop (tested) a tunnel on `127.0.0.1` is reachable that way; Colima is untested.
On Linux it is not: there `host-gateway` is the Docker bridge address. Add it to `TUNNEL_BIND_ADDRS`, for example
`TUNNEL_BIND_ADDRS=127.0.0.1,172.17.0.1` (check with `ip -4 addr show docker0`). The bridge
address is not reachable from the local network. Not yet tested on Linux.

Conventions for this side are in `.claude/rules/client.md`.
