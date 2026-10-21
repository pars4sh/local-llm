# Decisions

This is a log of architecture choices, newest last. Add an entry whenever you make a choice or
reverse one. To reverse one, add a new entry and mark the old one as superseded.

Status values:
- **accepted:** decided and in use.
- **to validate:** decided, but not yet proven on our hardware.
- **superseded:** replaced by a later entry.

---

## D-001: Serve with vLLM instead of transformers `generate`
**Status:** accepted · 2026-10-21

**Context.** The first version (a Gradio app) loaded Qwen3-32B with `AutoModelForCausalLM` and
called `generate`. It had several problems:
- requests ran one at a time
- every turn re-encoded the whole conversation
- nothing managed the context
- there was no API that other tools could use

**Decision.** Use vLLM. It provides continuous batching, prefix caching, tool-call and reasoning
parsers, and an OpenAI-compatible API.

**Consequences.**
- Every UI and agent can use the same endpoint.
- vLLM pins its own torch build, so it gets its own uv environment.

## D-002: Main model is Qwen3.8-27B (FP8)
**Status:** to validate · 2026-10-21

**Context.** Qwen3-32B has a 40,960-token context and no vision encoder.

**Decision.** Use Qwen3.8-27B-FP8.
- It is a dense model with hybrid attention and a native vision encoder.
- It has a 262,144-token context, adjustable thinking and tool calling.
- It is Apache-2.0 licensed.
- We chose FP8 over 4-bit for quality, and it still leaves more than 50 GB for the KV cache.

**Rejected.**
- Qwen3.6-35B-A3B: faster, but weaker than the 27B in Qwen's reports. It stays a fallback.
- Qwen3-Coder-Next (80B MoE): strong at coding, but too large to share the GPU with the rest of
  the roster. See D-009.
- Keeping Qwen3-32B.

## D-003: Several models at once with fixed GPU shares
**Status:** accepted · 2026-10-21

**Decision.** Run one vLLM process per model, each with its own `--gpu-memory-utilization`
share, all on one GPU. Don't hot-swap models through vLLM sleep mode.

**Why.** Sleep mode was reported broken in vLLM v0.14+ (the weights were not freed, vllm#32714).
Fixed shares are predictable.

## D-004: LiteLLM gateway in front of all models
**Status:** accepted · 2026-10-21

**Decision.** Run one LiteLLM proxy inside the GPU job. It handles:
- the single exposed port
- stable model aliases
- one key per client
- the usage log

**Why.** Four models would otherwise mean four ports, four tunnels and four client configs.
Swapping a model then only changes the roster, not every client.

## D-005: UI and its services run on the laptop; only models run on the cluster
**Status:** accepted · 2026-10-21

**Why.** GPU jobs end when `SLURM_TIME` runs out, and chat history, search and files must not
end with them.

*Rationale updated 2026-10-21:* the original entry also cited missing internet on compute nodes.
A GPU job measured outbound HTTPS working at the development site, so that reason no longer
holds there. The decision stands on the reason above. Serving jobs still run with
`HF_HUB_OFFLINE=1`, so a job never downloads or changes weights on its own.

## D-006: MTP speculative decoding on the main model
**Status:** to validate · 2026-10-21

**Decision.** Enable Qwen3.8's built-in multi-token-prediction head with
`--speculative-config '{"method": "mtp", "num_speculative_tokens": 2}'`.

**Evidence so far.** Community reports on other GPUs:
- about 1.55× single-stream decode on vLLM 0.27.1 (RTX 5090)
- +60% on llama.cpp (RTX 3090)
- the output is not bit-identical to greedy decoding

**To do.** Measure tokens per second with and without MTP on our GPU and record the results here.

## D-007: RAG stack is Docling, Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B
**Status:** to validate · 2026-10-21

**Decision.** Use Docling for extraction (tables, equations and multi-column papers). Run
embedding and reranking on the GPU through the gateway. Use hybrid search in Open WebUI.

**Rejected.** pymupdf4llm with whole papers pasted into the prompt, which is what the old app
did. It overflowed the context and lost tables.

## D-008: Keep reading tools and acting tools in separate presets
**Status:** accepted · 2026-10-21

**Decision.** Presets that read untrusted content (web, fetched pages) get no file-write or exec
tools. Open Terminal is confined to one sandbox folder.

**Why.** Prompt injection. See `architecture.md` § Security.

## D-009: Large coding model runs as a separate, on-demand job
**Status:** accepted · 2026-10-21

**Decision.** A large coder (for example Qwen3-Coder-Next) runs in its own job. That is either
this GPU while the main job is down, or another GPU node. The gateway registers it through that
job's run file.

## D-010: Fresh repository, ready to publish
**Status:** accepted · 2026-10-21

**Decision.**
- Start clean instead of porting the Gradio app. It stays on the cluster until phase 6.
- Everything site-specific lives in `.env` and `CLAUDE.local.md`, both gitignored, so the repo
  can be made public.

## D-011: uv, one environment per component
**Status:** accepted · 2026-10-21

**Decision.** Give vLLM, the gateway and the evals each a separate uv environment with exact pins.

**Why.** vLLM and LiteLLM both pin heavy, conflicting dependencies, and uv is the cluster's convention.
