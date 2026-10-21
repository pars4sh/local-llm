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

## D-012: vLLM 0.32.0 on torch 2.13.0 (CUDA 13.0 wheels)
**Status:** accepted · 2026-10-21 (served the main model and passed the smoke test)

**Context.** Qwen3.8 needs vLLM 0.17 or newer. Community runs on this GPU class used vLLM 0.27.1
with torch 2.13 and CUDA 13.0 wheels. The development GPU node's driver supports CUDA 13.2, and
the node has no system CUDA toolkit.

**Decision.** Pin the newest stable release, `vllm==0.32.0` (released 2026-10-18), in
`cluster/pyproject.toml`, with every other version fixed by `cluster/uv.lock`. Python 3.12
(uv-managed). The versions that matter:

| Package | Version |
|---|---|
| vllm | 0.32.0 |
| torch | 2.13.0 (CUDA 13.0 build from PyPI) |
| torchvision | 0.28.0 |
| transformers | 5.19.0 |
| flashinfer-python | 0.7.0.post1 |
| triton | 3.7.1 |
| nvidia-cuda-runtime / nvidia-cuda-nvcc | 13.0.96 / 13.0.88 |

**Verified in a GPU job** with `cluster/check_env.py`: compute capability `(12, 0)`,
`torch.version.cuda` 13.0, vLLM and its compiled kernels import, a Triton kernel runs, and a
FlashInfer JIT kernel compiles and runs.

**Workarounds.** Re-check each one when upgrading vLLM, torch or FlashInfer. Rerun
`cluster/check_env.py` in a GPU job; its FlashInfer line must say OK.

1. **Hold the pip CUDA compiler at torch's CUDA version.**
   - *Why:* `humming-kernels[cu13]`, a vLLM dependency, requires nvcc and CCCL without a version
     bound, so uv resolved them to CUDA 13.4 while torch's `cuda-toolkit` is 13.0.3. FlashInfer's
     JIT then failed with "CUDA compiler and CUDA toolkit headers are incompatible".
   - *Fix:* `constraint-dependencies` in `cluster/pyproject.toml` hold nvcc, crt and nvvm at
     13.0.88 and CCCL at 13.0.85, the versions in torch's `cuda-toolkit` 13.0.3.
   - *On upgrade:* read the new torch's `cuda-toolkit` pin and move the constraints with it. Drop
     them if the dependency gains a bound.
2. **Point `CUDA_HOME` at the CUDA toolkit inside the environment.**
   - *Why:* FlashInfer compiles kernels on first use and needs nvcc and ninja. The GPU node has
     no system CUDA toolkit, and without one vLLM disables FlashInfer with a single log line and
     falls back to other kernels.
   - *Fix:* `use_pip_cuda_toolkit` in `scripts/env.sh` sets `CUDA_HOME` to the environment's
     `nvidia/cu13`. `cluster/serve.sbatch` calls it and puts the environment's `bin/` (which has
     ninja) on `PATH`.
   - *On upgrade:* check the toolkit path (`cu13` changes on a CUDA major version bump), and
     check in the vLLM log which attention backend it selected.
3. **Give the linker an unversioned `libcudart.so`.**
   - *Why:* FlashInfer links its JIT kernels with `-lcudart`, but the pip runtime ships only
     `libcudart.so.13`. The link failed with "cannot find -lcudart".
   - *Fix:* the same helper links `libcudart.so` to `libcudart.so.13` in a per-job directory
     under `TMPDIR` and adds it to `LIBRARY_PATH`, so nothing inside the environment is modified.
   - *On upgrade:* check the library's major version and whether FlashInfer still links this way.

4. **Set `--max-num-seqs 64` on the main model.**
   - *Why:* Qwen3.8's linear-attention layers keep one state block per running sequence. vLLM
     0.32 defaults to 1024 sequences, but the 0.60 GPU share holds 505 blocks, so startup failed
     with "max_num_seqs (1024) exceeds available Mamba cache blocks (505)".
   - *Fix:* `--max-num-seqs 64` in the roster's `extra_args`, which is plenty for a few people
     plus background tasks.
   - *On upgrade, or when the share or context changes:* the error states the block count, so
     keep `--max-num-seqs` below it.

**Verified serving** (2026-10-21, 30-minute test job, `cluster/smoke_test.sh` 7/7 passed):

| | |
|---|---|
| Flags | `--gpu-memory-utilization 0.6 --max-model-len 262144 --kv-cache-dtype fp8 --max-num-seqs 64 --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder` |
| Attention | FLASHINFER, chosen from FLASHINFER and TRITON_ATTN; decode uses XQA over the FP8 KV cache. FlashInfer also runs linear-attention prefill and top-p/top-k sampling. The vision encoder uses FLASH_ATTN. |
| FP8 GEMMs | CUTLASS (vLLM turns DeepGEMM off for this architecture on Blackwell) |
| GPU memory | 58.4 GiB for the vLLM process: 28.4 GiB weights, 25.7 GiB KV cache (807,524 tokens, 3.08 full 262k contexts), 0.8 GiB CUDA graphs |
| Weight load | 29 GB from the shared filesystem: 13.0 s cold, 7.8 s warm. Staging is not needed. |
| Startup | 130 s from launch to `/health`. Engine init took 96 s: torch.compile 39 s and CUDA graphs 32 s (the first run also JIT-compiled a FlashInfer decode kernel, now cached). |
| Decode | 46 tok/s, single stream (512 tokens, thinking off) |
| Prefill | 51,177-token prompt in 6.9 s (~7,400 tok/s) |

**Open.** The checkpoint has no FP8 KV scales, so vLLM uses 1.0 and warns about possible
accuracy loss. The phase 5 evals should compare `--kv-cache-dtype fp8` with `auto` before this
counts as settled.

**Rejected.**
- vLLM 0.27.1: proven on this GPU class by others, with the same torch. It stays the fallback if
  0.32.0 fails to serve.
- `flashinfer-cubin` from PyPI: the newest there is 0.6.13, which doesn't match
  flashinfer-python 0.7.0.post1.
- `vllm download-kernels`: it fetches from a host outside the development login node's network
  allow list, and it adds an unpinned download step.
