# local-llm: guide for Claude Code

A self-hosted assistant. Open-weight models run under vLLM on a SLURM GPU node, behind a
LiteLLM gateway. People use them through Open WebUI on a laptop, and through a terminal
coding agent on the cluster login node.

- Read `docs/architecture.md` before you change how components connect.
- Read `docs/decisions.md` before you revisit a choice. Most choices there have a recorded reason.
- `docs/roadmap.md` has the current phase, the next steps and the open questions.

## Where things run

| Machine | Runs | What you can do there |
|---|---|---|
| GPU compute node (inside a SLURM job) | vLLM processes, LiteLLM gateway | Reach it only through `sbatch`/`srun`. Never start GPU work on the login node. |
| Cluster login node | `sbatch`, model downloads, coding agent, Claude Code | Edit, lint, submit jobs. No GPU. |
| Laptop (macOS) | Docker: Open WebUI, SearXNG, Docling, Open Terminal. SSH tunnel. | `docker` commands work only here. |

Check which machine you're on before running anything:
- `command -v sbatch` means the cluster.
- `command -v docker` means the laptop.
- If neither is found, you're in a sandbox. Edit and lint only, and say what you couldn't test.

## Hard rules

- **This repo will be published.** Never commit any of these:
  - hostnames, usernames or partition names
  - absolute site paths
  - API keys or tokens

  Site-specific values go in `.env`, which is gitignored (template: `.env.example`). Site facts for
  you go in `CLAUDE.local.md`, also gitignored (template: `CLAUDE.local.md.example`).
- Keep these out of git as well: model weights, logs, run files, chat data and eval outputs.
- Don't print secret values from `.env` into the conversation, logs or commit messages.
- Only the gateway listens on the network, and only with an API key. vLLM processes bind to `127.0.0.1`.
- Keep tools that read the web separate from tools that write files or run commands. They go in
  different Open WebUI presets because of prompt injection (see `docs/architecture.md` § Security).
- Each model (name, checkpoint, GPU share, flags) is defined in exactly one place: the roster file
  that phase 1 creates. The launcher and the gateway config are generated from it. Don't copy
  model names or ports into scripts by hand.
- Don't guess answers to the open questions in `docs/roadmap.md`. Ask the user.

## Conventions

- **Commits:** `<type>(<scope>): <subject>`
  - type is one of `chore`, `docs`, `feat`, `fix`, `refactor`, `style`, `test`
  - scope is one of `cluster`, `gateway`, `client`, `evals`, `scripts`, `docs`, `repo`
  - subject is imperative, at most 72 characters
  - one logical change per commit
- **Bash:**
  - start with `#!/usr/bin/env bash` and `set -euo pipefail`
  - must pass `shellcheck`
  - the cluster login shell is **tcsh**, so scripts must not depend on the caller's shell
  - commands you give the user to paste must work in tcsh, or be wrapped in `bash -c '…'`
- **Python:**
  - uv-managed, one environment per component (vLLM, gateway, evals), because their dependency pins conflict
  - pin exact versions
  - `ruff check` and `ruff format`
- **Config:** read environment variables from `.env`, with sensible defaults. No hard-coded paths, ports or model names.
- **Docs move with the code:**
  - tick `docs/roadmap.md` when a step is done
  - when you make or reverse a choice, add an entry to `docs/decisions.md` saying what changed, why, and what was rejected
  - record the exact versions and flags that actually worked on the GPU

## Verifying changes

- Shell: `shellcheck` and `bash -n` on every changed `.sh` or `.sbatch` file.
- Python: `ruff check` and `ruff format --check`.
- Gateway: `uv run --project gateway pytest gateway/tests` after any change in `gateway/` or a LiteLLM upgrade.
- Compose (laptop only): `client/compose.sh config --quiet`.
- Serving: run `cluster/smoke_test.sh` against a live job. It covers gateway auth, text, an image,
  a tool call, thinking on and off, a ~50k-token prompt, embeddings and rerank.
- Model or version changes: run the eval set in `evals/` and compare with the last recorded result.
- If you couldn't run a check, say so. Don't report untested serving changes as working.

## Path-scoped rules

`.claude/rules/cluster.md` and `.claude/rules/client.md` load when you touch files on that side.
They hold the details: SLURM, vLLM flags and the GPU on one side; Docker and Open WebUI on the other.
