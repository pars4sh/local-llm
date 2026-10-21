# Roadmap

Tick each item when it's done and verified. Every phase ends with a "done when" check.

## Phase 0: Scaffold ✅
- [x] Repository layout, agent guide (`CLAUDE.md`, `.claude/rules/`), and docs for architecture, decisions and roadmap
- [x] `.gitignore`, `.env.example`, `CLAUDE.local.md.example`

## Phase 1: Serving (cluster)
- [x] Create a uv environment for vLLM (vLLM 0.32.0, see D-012). In a short GPU job
      (`cluster/check_env.py`), confirm that torch reports compute capability `(12, 0)`.
- [ ] `scripts/download_models.sh`: on the login node, fetch the roster checkpoints into `$LLM_MODEL_DIR`.
- [x] Create the roster file, the single source of truth (planned: `config/models.toml`, which
      Python can read with `tomllib` and no extra dependencies). It holds each model's name,
      checkpoint, GPU share, max context and extra flags.
- [ ] `cluster/serve.sbatch` with the main model only:
  - free-port selection
  - run file
  - per-process logs
  - offline Hugging Face flags
- [ ] `cluster/smoke_test.sh`: text, image, tool call, thinking on and off.
- [ ] Turn on MTP. Measure tokens per second with and without it, and record the numbers in D-006.
- [ ] Add the fast, embed and rerank models. Start them in sequence, with a health wait between each.
- [ ] LiteLLM gateway: config generated from the roster, a master key and one key per client.
- [ ] Resubmit automatically before `SLURM_TIME` runs out (opt-in: `LLM_AUTO_RESUBMIT=1`).

**Done when:**
- one `sbatch` brings up all four models behind one authenticated endpoint
- the smoke test passes
- a second job can run at the same time without clashing

## Phase 2: Laptop client
- [ ] `client/compose.yaml` with Open WebUI, SearXNG, Docling and Open Terminal:
  - pinned tags
  - ports bound to `127.0.0.1`
  - named volumes
- [ ] `client/tunnel.sh`:
  - finds the node and port from the newest run file
  - opens the tunnel
  - reconnects when the job moves
- [ ] Open WebUI wiring:
  - connection to the gateway
  - task model set to `qwen-fast`
  - embeddings and reranking through the gateway
  - Docling extraction
  - SearXNG web search
  - native function calling

**Done when:** these all work from the browser:
- a chat
- a question about an image
- a web search answered with citations
- Q&A about an uploaded paper

## Phase 3: Tools and presets
- [ ] Research, Code, Quick and Figures presets, each with its system prompt and tool set
      (see the security table in `architecture.md`).
- [ ] Web page fetch tool.
- [ ] Scholarly search through MCP (arXiv, Semantic Scholar or OpenAlex). Compare the candidates first.
- [ ] One knowledge base per research topic, with hybrid search and rerank turned on.
- [ ] Security check: no preset that reads the web has write or exec tools.

**Done when:** each preset completes one realistic task end to end.

## Phase 4: Coding agent
- [ ] Qwen Code and/or OpenCode on the login node, pointed at the gateway with its own key.
- [ ] On-demand job for a large coding model, registered in the gateway.

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
4. Is Docker Desktop or OrbStack installed on the laptop?
5. Which license should the published repo use?
