---
paths:
  - "client/**"
---

# Laptop-side code (Docker, Open WebUI, tunnel)

## Docker
- Target Docker Desktop on macOS and assume arm64, but keep the stack working on Colima and Linux.
  - Every image needs an arm64 build.
  - Don't use `network_mode: host`.
  - Containers reach the SSH tunnel at `host.docker.internal`. Give every service that does
    `extra_hosts: ["host.docker.internal:host-gateway"]`. Docker Desktop resolves the name by
    itself; Colima and Linux need the entry.
  - On Linux, `host-gateway` is the Docker bridge address, so a tunnel bound only to `127.0.0.1`
    is not reachable from containers. Add the bridge address to `TUNNEL_BIND_ADDRS` (D-014).
- Pin image tags; never use `:latest`. When you bump a tag, note it in `docs/decisions.md`.
- Publish ports on `127.0.0.1` only (for example `"127.0.0.1:3000:8080"`). Nothing should be
  reachable from the local network.
- Keep persistent data in named volumes. Never bind-mount the home directory.
- Open Terminal gets exactly one sandbox directory, `$OPEN_TERMINAL_DIR`. Keep these out of it:
  SSH keys, tokens, `.env` files, patient or medical data.
- Compose files contain no host-specific values; they interpolate everything from `.env`.
- Run compose through `client/compose.sh`: it passes the repository's `.env` and the roster's
  client models. Never type model names into `compose.yaml`.

## Open WebUI
- Configure through environment variables wherever Open WebUI supports it, so a fresh install
  is reproducible. Document each remaining manual step in `client/README.md`.
- The task model (titles, tags, search queries) is the fast model, never the main one.
- Turn on native function calling for presets that use tools.
- Embeddings and reranking go through the gateway, not a model running on the laptop.
- Presets (Research, Code, Quick, Figures) follow the security table in `docs/architecture.md`.
  A preset that reads the web gets no write or exec tools.

## Tunnel
- The tunnel script reads the newest run file over SSH to find the node and port; it never
  hard-codes them.
- It must reconnect when the job restarts on a new node or port.
- Bind the local end to `127.0.0.1` only (plus the Docker bridge address on Linux, D-014).
