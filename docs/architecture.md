# Architecture

## Overview

```
Laptop (Docker)                          GPU node (one SLURM job)
+-------------------------+              +-------------------------------------+
| Open WebUI       chat   |  SSH tunnel  | LiteLLM gateway (one port, API key) |
| SearXNG          search |------------->|   vLLM main    Qwen3.8-27B-FP8      |
| Docling          docs   |              |   vLLM fast    Qwen3.5-4B           |
| Open Terminal    files  |              |   vLLM embed   Qwen3-Embedding-0.6B |
+-------------------------+              |   vLLM rerank  Qwen3-Reranker-0.6B  |
                                         +-------------------------------------+
                                                            ^
                        Cluster login node: coding agent ---+
                        (Qwen Code / OpenCode)
```

The system has three layers, and each does one job:

1. **Serving (cluster).** vLLM runs one process per model on a single GPU. A LiteLLM gateway sits
   in front of them, so every client sees one OpenAI-compatible endpoint with stable model names.
2. **Interface (laptop).** Open WebUI is the chat UI, with tools, RAG and presets. Its helper
   services run next to it. Chat history lives here, so it survives GPU jobs ending.
3. **Coding (login node).** A terminal agent works in real git repositories through the same gateway.

## Components

| Component | Runs on | Purpose |
|---|---|---|
| vLLM | GPU node | Inference: continuous batching, prefix caching, tool and reasoning parsing |
| LiteLLM proxy | GPU node | One endpoint, model aliases, one API key per client, usage log |
| Open WebUI | Laptop | Chat, image input, RAG, tool calling, MCP, presets |
| SearXNG | Laptop | Private metasearch behind the web-search tool |
| Docling (docling-serve) | Laptop | Extracts PDFs and Office files (tables, equations, layout) for RAG |
| Open Terminal | Laptop | Creates and edits files and runs code, confined to one sandbox folder |
| Qwen Code / OpenCode | Login node | Agentic coding against the gateway |

The UI services live on the laptop for two reasons:
- The GPU job ends when its `SLURM_TIME` runs out, and chat history must survive that.
- Compute nodes may not have internet access at every site.

## Model roster and GPU budget

This is the initial budget for one 96 GB RTX PRO 6000. Phase 1 tunes it, and the roster file is
the source of truth once it exists.

| Name | Checkpoint | `--gpu-memory-utilization` | ≈ GB | Notes |
|---|---|---|---|---|
| `qwen-main` | Qwen3.8-27B-FP8 + MTP head | 0.60 | 57 | ~30 GB weights; the rest is FP8 KV cache. Hybrid attention keeps the KV cache small, so full 262k context fits. |
| `qwen-fast` | Qwen3.5-4B | 0.12 | 11.5 | Task model (titles, search queries, tags) and quick Q&A. Context capped at 32k. |
| `embed` | Qwen3-Embedding-0.6B | 0.04 | 4 | RAG indexing and queries |
| `rerank` | Qwen3-Reranker-0.6B | 0.04 | 4 | RAG precision; vLLM `/rerank` endpoint |
| (headroom) | | ~0.20 | 19 | CUDA contexts (~0.5–1 GB per process), activations, safety margin |

**On demand, as a separate job:** a larger coding model, for example Qwen3-Coder-Next
(80B MoE). It runs on this GPU when the main job is down, or on another GPU node, and the
gateway registers it by reading that job's run file. vLLM sleep-mode hot-swapping is not used
(see decisions D-003 and D-009).

## Network and connection flow

1. The job starts and picks free ports for the gateway and for each vLLM process.
2. Each vLLM process binds to `127.0.0.1:<port>`. Only the gateway binds to all interfaces, and
   it rejects requests that don't carry a valid key.
3. The job writes `$LLM_RUN_DIR/run/<jobid>.env`, containing the node, the gateway port and the job ID.
4. **Laptop:** `client/tunnel.sh` reads the newest run file over SSH and forwards
   `127.0.0.1:$GATEWAY_LOCAL_PORT` to the gateway. Containers reach it at
   `host.docker.internal:$GATEWAY_LOCAL_PORT`.
5. **Login node:** the coding agent reads the same run file and connects to
   `<node>:<gateway port>` with its own key.

## Request flows

- **Chat and images:** Open WebUI → tunnel → gateway → `qwen-main`. Thinking is toggled per
  request. Background tasks go to `qwen-fast`.
- **Web research:**
  1. The model calls the search tool, which uses SearXNG.
  2. It calls the fetch tool on the most relevant pages.
  3. It answers with citations.

  Native function calling lets it repeat steps 1–2 before answering.
- **Papers (RAG):**
  1. Upload → Docling extracts the text → `embed` → vector store (inside Open WebUI).
  2. A query runs hybrid search (BM25 plus vectors), then `rerank`, then the top chunks go to `qwen-main`.
- **Files:** in the Code preset the model uses Open Terminal tools, confined to `$OPEN_TERMINAL_DIR`.
- **Coding:** the agent on the login node → gateway → `qwen-main` (or the on-demand coder).

## Job lifecycle

1. If `LLM_STAGE_DIR` is set, copy the weights to node-local disk.
2. Start each vLLM process in roster order, waiting for `/health` before starting the next.
3. Start the gateway, using a config generated from the roster.
4. Write the run file.
5. If `LLM_AUTO_RESUBMIT=1`, submit the follow-up job shortly before `SLURM_TIME` runs out,
   then shut down cleanly.

## Node facts that drive decisions

Measured in a GPU job at the development site. Check them at a new site before reusing the
choices that depend on them.

| Fact | Consequence |
|---|---|
| The driver supports CUDA 13.2 | cu130 wheels for torch and vLLM work |
| No node-local disk | Weights load from the shared filesystem; `LLM_STAGE_DIR` stays empty by default |
| `/tmp` is RAM-backed tmpfs | Fine for the per-job `TMPDIR`. Staging weights there would cost host RAM |
| Shared storage writes ~670 MB/s with a single stream (reads not measured yet) | If reads are similar, ~30 GB of weights load in under a minute; the job logs the real load time |

When the job is down, Open WebUI keeps its history but cannot reach any model. The tunnel
script reconnects to the new node and port once the next job writes its run file.

## Security

Running the model locally does not stop prompt injection. A web page or a PDF can carry
instructions, and those instructions can trigger whatever tools the model has. The defence is to
keep reading capabilities and acting capabilities apart:

| Preset | Model | Tools | Must not have |
|---|---|---|---|
| Research | `qwen-main`, thinking on | web search, fetch, scholarly search, knowledge bases | file write, code execution |
| Code | `qwen-main` | Open Terminal (sandbox folder) | web search, fetch |
| Quick | `qwen-fast` | none | |
| Figures | `qwen-main` (vision) | none | |

Other rules:
- Open Terminal sees one folder. Keep SSH keys, tokens, `.env` files and patient or medical data out of it.
- Keep medical data out of any preset that has web access.
- Keys:
  - the gateway master key is for admin use only
  - each client gets its own key (Open WebUI, coding agent, evals)
  - all keys live in `.env`
- Published ports bind to `127.0.0.1` on the laptop. vLLM is never exposed beyond its node.
