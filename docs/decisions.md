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

## D-006: MTP speculative decoding on the main model, with a BF16 KV cache
**Status:** accepted · 2026-10-21

**Decision.** Run the main model with Qwen3.8's multi-token-prediction head,
`--speculative-config '{"method": "mtp", "num_speculative_tokens": 2}'`, and the default
(`auto`, BF16) KV cache instead of FP8.

**Measured** on the RTX PRO 6000 with vLLM 0.32.0 (D-012), at a 0.60 GPU share,
`--max-num-seqs 64`, in one benchmark job (`cluster/bench.sbatch` with
`cluster/bench_mtp_kv.txt`). Every configuration used the same prompts:
- **Decode:** a 512-token story with thinking off and the model card's instruct sampling
  (temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5). Median of 3 runs.
- **Aggregate:** 4 such streams at once.
- **Acceptance:** from vLLM's `/metrics` counters over the single-stream runs; vLLM's own
  "SpecDecoding metrics" log lines agree.
- **Needle:** two code words at 25% and 75% of a 200,346-token document, answered greedily.

| Configuration | KV cache, tokens | Full 262k contexts | Decode, 1 stream (tok/s) | Aggregate, 4 streams (tok/s) | Draft acceptance | Mean acceptance length | Needle, 200k prefill |
|---|---|---|---|---|---|---|---|
| a) FP8 KV, no MTP | 807,524 | 3.08 | 45.8 | 168.1 | – | – | both found, 54.0 s |
| b) FP8 KV, MTP N=2 | 703,022 | 2.68 | 71.8 | 226.8 | 49.4% (0.65, 0.34) | 1.99 | both found, 56.2 s |
| c) FP8 KV, MTP N=3 | 689,775 | 2.63 | 71.5 | 217.7 | 38.8% (0.69, 0.34, 0.14) | 2.17 | both found, 56.3 s |
| **d) BF16 KV, MTP N=2** | **369,314** | **1.41** | **72.9** | **225.9** | **50.1% (0.66, 0.34)** | **2.00** | **both found, 46.8 s** |
| d') BF16 KV, MTP N=3 | 366,084 | 1.40 | 71.8 | 224.1 | 38.4% (0.67, 0.33, 0.15) | 2.15 | both found, 46.9 s |

The figures in brackets are per-position acceptance rates.

**Why.**
- MTP raises single-stream decode by 1.57× (45.8 to 71.8 tok/s) and 4-stream throughput by
  1.35×. This is in line with the community's 1.55× on an RTX 5090.
- N=3 adds nothing over N=2. Its third position is accepted only 14–15% of the time, so it
  drafts more for the same speed, with lower 4-stream throughput and less KV room.
- BF16 KV costs nothing in speed: decode is the same and prefill is ~20% faster (4,281 vs 3,562
  tok/s at 200k). It still holds one full 262k context with 41% to spare. It also removes D-012's
  open question about uncalibrated FP8 KV scales.
- The cost is capacity: 369k tokens shared by all running requests, against 703k with FP8.
  That's enough for a few people, but not for several long contexts at once. Switch back to FP8
  KV if that becomes a limit.
- MTP adds about 1 GiB of GPU memory (drafter weights and its KV) and 60–110 s of startup the
  first time a configuration compiles. The output is not bit-identical to running without MTP.

**Checked.** With MTP, `--max-num-seqs 64` still fits. The shared block pool holds 461 blocks
with BF16 KV and MTP N=2 (439 with FP8), and every running sequence needs one.

**Rejected.** FP8 KV with MTP N=2 (b): it has twice the context capacity, but the scales are
uncalibrated and prefill is slower. MTP N=3 (c, d'): no gain.

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

**Open (resolved by D-006).** The checkpoint has no FP8 KV scales, so vLLM uses 1.0 and warns
about possible accuracy loss. D-006 moved the main model to the BF16 KV cache, so this no longer
applies; it would again if FP8 KV came back.

**Rejected.**
- vLLM 0.27.1: proven on this GPU class by others, with the same torch. It stays the fallback if
  0.32.0 fails to serve.
- `flashinfer-cubin` from PyPI: the newest there is 0.6.13, which doesn't match
  flashinfer-python 0.7.0.post1.
- `vllm download-kernels`: it fetches from a host outside the development login node's network
  allow list, and it adds an unpinned download step.
