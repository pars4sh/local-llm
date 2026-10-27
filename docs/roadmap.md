# Roadmap

Tick each item when it's done and verified. Every phase ends with a "done when" check.

## Phase 0: Scaffold ✅
- [x] Repository layout, agent guide (`CLAUDE.md`, `.claude/rules/`), and docs for architecture, decisions and roadmap
- [x] `.gitignore`, `.env.example`, `CLAUDE.local.md.example`

## Phase 1: Serving (cluster) ✅
- [x] Create a uv environment for vLLM (vLLM 0.32.0, see D-012). In a short GPU job
      (`cluster/check_env.py`), confirm that torch reports compute capability `(12, 0)`.
- [x] `scripts/download_models.sh`: on the login node, fetch the roster checkpoints into `$LLM_MODEL_DIR`.
- [x] Create the roster file, the single source of truth (planned: `config/models.toml`, which
      Python can read with `tomllib` and no extra dependencies). It holds each model's name,
      checkpoint, GPU share, max context and extra flags.
- [x] `cluster/serve.sbatch` with the main model only:
  - free-port selection
  - run file
  - per-process logs
  - offline Hugging Face flags
- [x] `cluster/smoke_test.sh`: text, image, tool call, thinking on and off, a ~50k-token prompt,
      single-stream decode speed. (Embeddings and rerank checks come with those models.)
- [x] Turn on MTP. Measure tokens per second with and without it, and record the numbers in D-006.
- [x] Add the fast, embed and rerank models. Start them in sequence, with a health wait between each.
- [x] LiteLLM gateway: config generated from the roster, a master key and one key per client.
- [x] Resubmit automatically before `SLURM_TIME` runs out (opt-in: `LLM_AUTO_RESUBMIT=1`).

**Done when:**
- one `sbatch` brings up all four models behind one authenticated endpoint
- the smoke test passes
- jobs cannot clash by design: each job's run file and log directory are named by its job ID,
  and ports are picked on the job's own node at startup (see `architecture.md` § Concurrent
  jobs). Overlap can't be tested yet: the development partition allows one node per user, and a
  resubmitted successor starts only after its predecessor ends. The real test is in phase 4.

## Phase 2: Laptop client
- [x] First, rerun `cluster/smoke_test.sh` on the GPU with the allowlist gateway (D-013).
      Passed 12/12 with all four models on 2026-10-26 (blocked on 2026-10-22: GPU nodes drained).
- [x] `client/compose.yaml` with Open WebUI, SearXNG, Docling and Open Terminal (D-014):
  - pinned tags
  - ports bound to `127.0.0.1`
  - named volumes
  - `extra_hosts: host.docker.internal:host-gateway` on every service that reaches the tunnel,
    so the stack also works on Colima and Linux
- [x] `client/tunnel.sh`:
  - finds the node and port from the newest run file
  - opens the tunnel
  - reconnects when the job moves
- [ ] Open WebUI wiring (configured in `client/compose.yaml`; every backend path checked from the
      container on 2026-10-26, browser tests pending):
  - connection to the gateway
  - task model set to `qwen-fast`
  - embeddings and reranking through the gateway
  - Docling extraction
  - SearXNG web search
  - native function calling

- [x] `client/llm`: `up`, `down [--keep-ui]`, `status` and `backup` from the laptop (D-015), and
      an idle shutdown in the serving job (`LLM_IDLE_MINUTES`, default 120 min).

**Done when:** these all work from the browser:
- a chat
- a question about an image
- a web search answered with citations
- Q&A about an uploaded paper

## Phase 3: Tools and presets ✅
Everything is in the repository and applied by `client/presets/apply.py`; nothing depends on
settings clicked by hand (client/README.md).
- [x] Cleanup: `OFFLINE_MODE=true`, `WHISPER_MODEL=""` (D-018); web search and the page reader
      still work.
- [x] JavaScript-rendering page reader (Playwright MCP) as a container (D-016).
- [x] Literature tools through MCP, compared on six queries (three biomedical, three CV):
      PubMed / Europe PMC, OpenAlex, arXiv, and the official Hugging Face server for models,
      datasets and code (D-016).
- [x] Semantic Scholar: own MCP server with one 1.1 s limiter, batch endpoint and 429 backoff,
      because no published server met the key's terms (D-016). OpenAlex is the fallback.
- [x] Presets Research, Code, Quick, Figures and Private, with short system prompts; the preset
      guard enforces the security table (D-017).
- [x] Slash prompts: `/litnote` (biomedical or CV/ML template), `/review`, `/figure`, `/commit`,
      `/compare`, `/related-work`.
- [x] Knowledge bases: Mg implant degradation, CT-histology registration, CT-to-histology
      synthesis, video understanding & action anticipation, and an empty Private base; hybrid
      search and rerank on. One question per base answered correctly with the right file
      cited (2026-10-27). PDFs whose digits extract as stray glyphs are re-extracted with OCR.
- [x] Weekly literature digest (automation, Mondays 10:00, Research preset, "Digest" folder),
      with a biomedical and a CV (arXiv cs.CV) section. One-time run on 2026-10-27: 13 min,
      34 tool calls, all six topics, filed into "Digest".
- [x] Security check: Research can't create a file; Code and Private can't reach the web
      (D-017: guard test plus live chats, 2026-10-27).

**Done when:** each preset completes one realistic task end to end. Done 2026-10-27: Research
(seven tool-choice tasks and the digest), Code (wrote, tested and ran a micro-CT volume
script in the sandbox), Quick, Figures (read a plot's slope and flagged its missing y label),
Private (answered from its base, refused the web).

## Phase 4: Coding agent
- [ ] Qwen Code and/or OpenCode on the login node, pointed at the gateway with its own key.
- [ ] On-demand job for a large coding model, registered in the gateway.
- [ ] Run that job on another partition while the main job serves, and check that the two don't
      clash: separate run files, log directories and ports, and both smoke tests pass at once.

**Done when:** the agent makes a multi-file change in a real repo and the tests pass.

## Phase 5: Evals and monitoring
- [ ] Write 20–30 prompts from real work:
  - tool chains
  - questions about figures
  - code edits
  - questions about papers with known answers
- [ ] `evals/run.py`: runs the set against the gateway and saves the results together with the
      model and vLLM versions.
- [ ] Health and metrics check based on vLLM's `/metrics`.

**Done when:** after any model or version change, one command shows what changed.

## Phase 6: Cleanup and publish
- [ ] Retire the legacy Gradio app on the cluster:
  - archive it
  - delete the `.bak` files
  - free the Qwen3-32B weights if they're no longer needed
- [ ] Choose a license, write the README quickstart, and add CI (shellcheck, ruff).
- [ ] Do a final leak check (hostnames, usernames, paths, keys) before making the repo public.

## Later (optional)
- Install the digest wake-up job (`client/launchd/digest-wakeup.sh`) once the checkout has moved
  out of `~/Desktop`, which macOS doesn't let launchd jobs read (steps in client/README.md).
- Open Terminal has no `pytest` and the sandbox can't install it; a derived image could add it.
- Speech input (Whisper).
- Image generation (ComfyUI), started only on demand because it competes for GPU memory.

## Open questions
Ask the user about these; don't guess.
1. ~~Do GPU compute nodes have outbound internet access?~~ **Answered (measured in a GPU job):**
   yes, outbound HTTPS works at the development site. Downloads still happen on the login node,
   and jobs keep `HF_HUB_OFFLINE=1`, so a job never depends on the Hub. Other sites may differ.
2. How long may one person's serving job hold a GPU node under the group's usage norms?
3. ~~Is there node-local scratch disk on the GPU node, and how large is it?~~ **Answered (measured
   in a GPU job):** no dedicated local disk at the development site. The only fast local storage
   is RAM-backed tmpfs (about half the node's RAM), so staging weights there costs host RAM.
   Phase 1 measures the load time from the shared filesystem before turning staging on.
4. ~~Is Docker Desktop or OrbStack installed on the laptop?~~ **Answered:** Docker Desktop
   4.97.0 on Apple Silicon (arm64), Engine 29.9.2, Compose v5.6.1. The compose file must also
   work on Colima and Linux (see `.claude/rules/client.md`).
5. Which license should the published repo use?
