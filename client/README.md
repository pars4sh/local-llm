# client/

The laptop side: the Docker Compose stack and the SSH tunnel to the gateway.

**Status:** phase 2. Versions and the reasons behind them are in D-014 (`docs/decisions.md`).

| File | Purpose |
|---|---|
| `llm` | One command for everything: `llm up`, `llm down [--keep-ui]`, `llm status`, `llm backup` |
| `tunnel.sh` | Finds the node and gateway port from the newest run file of a running job, forwards `127.0.0.1:$GATEWAY_LOCAL_PORT` to it through the login host, and follows the job when it moves |
| `compose.sh` | Runs `docker compose` with the repository's `.env` and the client models from the roster (`cluster/roster.py client-env`) |
| `compose.yaml` | Open WebUI, SearXNG, Docling (docling-serve), Open Terminal and the MCP tool servers: pinned tags, only Open WebUI published (on `127.0.0.1`), named volumes |
| `searxng/settings.yml` | SearXNG config: JSON output for Open WebUI, rate limiting off for local use |
| `tools/` | Images built here: the arXiv MCP server (no published image) and our Semantic Scholar MCP server; pinned base and pip lock files |

## Setup

1. Fill in the laptop section of `.env` (template: `.env.example`).
   - `CLUSTER_SSH_HOST`: the login host, reachable with a key and no prompt.
   - `CLUSTER_REPO_DIR`: this repository's checkout on the cluster (absolute path); `llm up`
     submits the serving job from there.
   - `GATEWAY_API_KEY`: on the cluster, `scripts/new_client_key.sh open-webui`.
   - `WEBUI_SECRET_KEY`, `SEARXNG_SECRET`, `OPEN_TERMINAL_API_KEY`: `openssl rand -hex 32` each.
   - `OPEN_TERMINAL_DIR`: an empty folder outside the repository. It becomes the terminal's
     home directory, so Open Terminal writes `.bashrc`, `.profile` and `.local/` into it.
     Keep SSH keys, tokens, `.env` files and patient data out of it.
   - `BACKUP_DIR`: where `llm backup` writes (default `~/local-llm-backups`).
2. Install the command: `ln -s "$PWD/client/llm" ~/.local/bin/llm` (any directory on `PATH`).
3. `llm up`

Python 3.11 or newer must be on `PATH` (`compose.sh` reads the roster). `llm` runs under macOS's
`/bin/bash` 3.2.

## Everyday use

| Command | What it does |
|---|---|
| `llm up [--no-open]` | Reuses my running or pending serving job (job name from `cluster/serve.sbatch`), or submits one through `cluster/submit.sh` on the cluster. Shows pending, starting, ready; waits up to `LLM_QUEUE_TIMEOUT` (1800 s) in the queue and `LLM_START_TIMEOUT` (900 s) for startup, and on failure prints the job ID and the tail of its log. Then starts the tunnel and the containers (both skipped if already up), checks `/v1/models` through the tunnel, prints `Ready: http://localhost:3000` and opens the browser. |
| `llm down [--keep-ui]` | Cancels my serving jobs (only those with the project's job name), stops the tunnel and stops the containers. Volumes are never removed. `--keep-ui` leaves Open WebUI running, to read old chats. |
| `llm status` | Job (pending, running or none; node, time left, idle limit), tunnel, containers, and whether the gateway answers with the client key |
| `llm backup` | Backs up the Open WebUI volume to `BACKUP_DIR`, keeping the last 8 |

The tunnel runs in the background; its log is `~/.local/state/local-llm/tunnel.log`.
`client/tunnel.sh` and `client/compose.sh` still work on their own.

A forgotten job doesn't hold the GPU node for long: the serving job shuts itself down after
`LLM_IDLE_MINUTES` (cluster `.env`, default 120, 0 = never) without a request to any model.
`llm status` shows the limit. Open WebUI listing models doesn't count as a request; a chat,
an embedding or a rerank does.

## Backup and restore

`llm backup` stops Open WebUI for a moment (about a second), so its SQLite database, the vector
store and the uploads are copied at rest, then starts it again. The archive is
`$BACKUP_DIR/open-webui-<date>-<time>.tar.gz` (mode 700 directory; it holds your chats). It
leaves out `cache/`, the models Open WebUI downloads by itself (about 260 MB).

To restore, first check the backup in a scratch volume:

```
b=~/local-llm-backups/open-webui-YYYYMMDD-HHMMSS.tar.gz
docker volume create llm-restore-test
docker run --rm -v llm-restore-test:/data -v "$(dirname "$b"):/backup:ro" alpine:3.24.2 \
  tar -xzf "/backup/$(basename "$b")" -C /data
docker run --rm --entrypoint python3 -v llm-restore-test:/app/backend/data \
  ghcr.io/open-webui/open-webui:v0.12.3 -c "import sqlite3; db = sqlite3.connect('/app/backend/data/webui.db'); print(db.execute('PRAGMA integrity_check').fetchone()[0], db.execute('SELECT COUNT(*) FROM chat').fetchone()[0], 'chats')"
docker volume rm llm-restore-test
```

Then replace the live data (this deletes what is in the volume now; take an `llm backup` first
if it holds anything you want):

```
client/compose.sh stop open-webui
docker run --rm -v local-llm_open-webui:/data -v "$(dirname "$b"):/backup:ro" alpine:3.24.2 \
  sh -c 'find /data -mindepth 1 -delete && tar -xzf "/backup/$1" -C /data' _ "$(basename "$b")"
client/compose.sh start open-webui
```

Tested on 2026-10-26, both blocks as written: the restored scratch volume had the same integrity
check and row counts (users, chats, files, models) as the live database, and Open WebUI started
on it. The second block ran against a stand-in volume filled with junk (including hidden files),
not against the live one: it left exactly the backup's contents.

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
| MCP tool servers | `TOOL_SERVER_CONNECTIONS`, one container each on the `web` network (see Tools) |
| Offline mode | `OFFLINE_MODE=true`: nothing is downloaded from Hugging Face; `WHISPER_MODEL=""` keeps local Whisper unloaded (voice input isn't set up) |

Docling and Open Terminal sit on the `sandbox` network, which is `internal: true`: they can't
reach the internet. Open Terminal therefore can't read the web, and a prompt injected through
it has nowhere to send data (D-008).

## Tools

Each tool is an MCP server in its own container, speaking Streamable HTTP, on the `web` network
(route out; only Open WebUI shares it). Open WebUI registers them through
`TOOL_SERVER_CONNECTIONS` in `compose.yaml`, where `function_name_filter_list` exposes only each
server's reading tools. The comparison behind the choices is D-016.

| Id | Server | Exposed tools | Key in `.env` (optional) |
|---|---|---|---|
| `page` | Playwright MCP (headless Chromium) | `browser_navigate`, `browser_snapshot`, `browser_find`, `browser_wait_for` | |
| `pubmed` | cyanheads/pubmed-mcp-server | PubMed search and fetch, PMC full text, Europe PMC search | `NCBI_API_KEY` |
| `openalex` | cyanheads/openalex-mcp-server | works search, citation graph | `OPENALEX_API_KEY` |
| `arxiv` | blazickjp/arxiv-mcp-server (built in `tools/arxiv/`) | search, abstract, full text, outline and sections | |
| `s2` | our own (`tools/semantic-scholar/server.py`) | `search_papers`, `get_papers` (batch), `citation_graph` | `S2_API_KEY` (sent as `x-api-key`) |
| `huggingface` | huggingface/hf-mcp-server (built from the release commit) | `hub_repo_search`, `hub_repo_details`, `hf_fs` (papers, docs, files) | `HF_TOKEN` (read-only) |

`LITERATURE_CONTACT_EMAIL` is sent to NCBI, Europe PMC and OpenAlex, which ask clients for a
contact. Without keys everything works at the anonymous rate limits; Semantic Scholar's anonymous
pool is refused most of the time. The Semantic Scholar server is the only client of the S2 key:
one process, one limiter (a request every `S2_MIN_INTERVAL` = 1.1 s, below the key's 1/s across
all endpoints) for every tool call and the digest, backoff and retry on 429, the batch endpoint
for details, and only the fields it shows. It never logs the key.

Built-in web search (SearXNG) and `fetch_url` stay as they were; the page reader is for pages
that need JavaScript.

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
