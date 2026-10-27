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

**Attention backend.** With the BF16 KV cache, vLLM picks FLASH_ATTN from FLASH_ATTN,
FLASHINFER, TRITON_ATTN and FLEX_ATTENTION. FlashInfer still runs the linear-attention prefill
and top-p/top-k sampling, so it isn't silently disabled. With FP8 KV it picked FLASHINFER, with
XQA decode.

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

**Served** (2026-10-21, vLLM 0.32.0, through the gateway; retrieval quality still to be judged
in phase 3):
- `embed`: `--runner pooling --convert embed`. 1024-dim vectors; a paraphrase scores 0.74
  against 0.25 for an unrelated sentence.
- `rerank`: `--runner pooling` with the `--hf-overrides` that turn the original causal-LM
  checkpoint into a yes/no classifier, served at `/rerank`. It needs the model card's prompt
  format as a score template (`config/templates/qwen3_reranker.jinja`), or vLLM warns that scores
  may be inaccurate. vLLM ships its own template only in its source tree, not in the wheel, so
  this one is written from the model card, verified byte for byte against it, and tolerant of
  vLLM's one-message warmup. Smoke test: the right document scores 0.994, the others 0.001 or
  less.

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

## D-013: Gateway keys and usage log without a database
**Status:** accepted · 2026-10-21

**Context.** D-004 puts a LiteLLM proxy in front of the models with one key per client and a
usage log. LiteLLM's own per-client keys (`/key/generate`) and its spend log need a Postgres
database. A SLURM job has none, and a database outside the job would be one more service to run.

**Decision.** Pin `litellm[proxy]==1.105.1` in its own environment (`gateway/`). The release was
a week old when chosen; LiteLLM ships several releases a day, and a past release on PyPI was
compromised, so upgrades are deliberate and the lock file holds hashes. Wire two hooks from
`gateway/local_llm_gateway.py` into the config that `cluster/launch.py` generates:
- **Auth** (`custom_auth`): a request passes with the master key or with a client key whose
  SHA-256 is in the keys file (`$LLM_GATEWAY_KEYS`, mode 600). `scripts/new_client_key.sh`
  issues, lists and revokes keys; the gateway rereads the file when it changes. Anything else
  gets HTTP 401.
- **Usage log** (a callback): one JSON line per request in `$LLM_RUN_DIR/usage/<YYYY-MM>.jsonl`
  with time, client, model, call type, token counts, latency and status, but no content.
  Rejected requests are logged too.

The gateway fetches nothing at startup (`LITELLM_LOCAL_MODEL_COST_MAP`,
`LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS`). Telemetry, the admin UI (it needs the database) and
the API docs are off.

**Route allowlist (added 2026-10-22 after a security review).** The first version of this entry
said only `/health/liveliness` was open. That had not been tested, and it was wrong. Probing
LiteLLM 1.105.1's whole route table (638 method/route pairs; its own `GET /routes` lists only
32, because it skips FastAPI's included routers) showed about 60 that answer without a key:
- admin UI, SSO and onboarding routes, despite `DISABLE_ADMIN_UI`
- `/openapi.json` and `/redoc`, despite `NO_DOCS`
- `/public/*` catalogues, two of which (`litellm_blog_posts`, `autorouter_presets`) fetch from
  the internet on request
- MCP, toolset and agent-skills routes, including `GET /v1/skills/{id}/archive`, which has no
  auth dependency at all

Also, with `custom_auth`, LiteLLM skips its `common_checks` unless
`custom_auth_run_common_checks` is set, and its admin-route check lives there, so a client key
could reach admin routes. The gateway therefore runs as `uvicorn local_llm_app:app`, which puts
LiteLLM behind an allowlist (`gateway/local_llm_app.py`):
- chat, completions, embeddings, rerank and model listing (with and without `/v1`, plus
  `/v2/rerank`), each requiring a key
- `/health/liveliness`, the only route open without a key
- everything else gets 404 for every caller, the master key included, before LiteLLM sees the
  request (marked with an `x-local-llm: route-not-allowed` header); every WebSocket is refused
  with 403

LiteLLM's startup loads the config from `CONFIG_FILE_PATH`, the same path its Docker image uses.

**Auth review** (2026-10-22). Each item has tests in `gateway/tests/`
(`uv run --project gateway pytest gateway/tests`, 47 tests). The hardened gateway also passed
an end-to-end smoke test in front of fake vLLM servers and, on 2026-10-26, the GPU smoke test
with all four models behind it (12/12 checks, including 401 without a key and with a wrong
key). It was ready 6.1 s after launch.

| Item | How |
|---|---|
| Deny by default | A missing, empty, unreadable, malformed or non-regular keys file, a file readable by other users, or an unset `LLM_GATEWAY_KEYS` refuses every client key and logs one error per distinct problem. The master key still works, so the admin is never locked out. A file that turns bad after use is caught on the next request (the cache key includes ctime). |
| Constant-time comparison | SHA-256 digests (fixed length) compared with `hmac.compare_digest` against the master key and every client entry, with no early exit |
| Status codes | Wrong key, no key, malformed headers (`Bearer` without a token, `Basic`, another scheme, a truncated key) and a revoked key get 401; a valid key gets 200, also in `x-api-key` and `api-key` headers |
| Routes | Every `/v1/*` route needs a key or is blocked. Without a key, only `/health/liveliness` answers |
| Secrets in logs | No key, master key or key hash in the gateway log or the usage log. LiteLLM sees a client as `client:<name>`, never by its hash. vLLM processes no longer receive the master key |
| Keys file | Written with mode 600; the gateway refuses it otherwise. `new_client_key.sh` refuses a path inside the repo unless it is gitignored, and `.gitignore` covers `gateway-keys.json` |

**Verified** on the GPU (job with `qwen-main`, smoke test through the gateway with a temporary
client key) and offline (real LiteLLM in front of fake vLLM servers that record what they
receive):
- requests without a key or with a wrong key get 401
- image inputs, `tools`/`tool_choice`, `chat_template_kwargs` (`enable_thinking` on and off),
  `max_tokens` and vLLM's own `ignore_eos` reach vLLM unchanged, and tool-call arguments come
  back unchanged
- reasoning text comes back unchanged, but **under a different field name**: vLLM 0.32 streams
  it as `reasoning`, and LiteLLM renames it to `reasoning_content`. Clients should read
  `reasoning_content` (Open WebUI does).
- embeddings and rerank go through the gateway, on the GPU too (since 2026-10-21, see D-007)
- the gateway adds about 0.1 s to the time to first token; decode speed is unchanged

**Rejected.**
- LiteLLM virtual keys with Postgres: needs a database service.
- One shared key for all clients: no way to revoke or attribute usage per client.
- `custom_auth_run_common_checks: true` instead of the allowlist: it would still leave the
  unauthenticated routes open, and its checks expect database records.
- Starlette middleware added to LiteLLM's app from inside the config: LiteLLM loads the config
  after the app has started, when middleware can no longer be added.

## D-014: Laptop stack: pinned images, settings from compose, sandbox network
**Status:** to validate · 2026-10-26 (passes its backend checks; browser tests pending)

**Decision.** `client/compose.yaml` runs four services, each pinned to a release that was at
least a week old when chosen (the same reason as LiteLLM in D-013: deliberate upgrades, not
whatever shipped yesterday). Every image has an arm64 build (checked with
`docker buildx imagetools inspect`).

| Service | Image | Released |
|---|---|---|
| Open WebUI | `ghcr.io/open-webui/open-webui:v0.12.3` | 2026-10-16 |
| SearXNG | `searxng/searxng:2026.10.17-decdfa6f7` | 2026-10-17 |
| Docling | `ghcr.io/docling-project/docling-serve-cpu:v1.37.0` | 2026-10-13 |
| Open Terminal | `ghcr.io/open-webui/open-terminal:0.14.2` | 2026-10-18 |

- **Settings come from compose.** `ENABLE_PERSISTENT_CONFIG=false`, so the environment applies on
  every start and a fresh install is reproducible. Admin-panel changes to the same settings
  don't survive a restart. Per-model settings (stored in the database) are unaffected.
- **Model names come from the roster.** A `[client]` table in `config/models.toml` names the
  default, task, embedding and reranking models; `cluster/roster.py client-env` prints them and
  `client/compose.sh` passes them to compose. The wrapper also points compose at the repository's
  `.env` (compose would otherwise look in `client/`).
- **Only Open WebUI publishes a port** (`127.0.0.1`). It reaches SearXNG, Docling and Open Terminal
  by service name; Open Terminal is a system connection proxied by the Open WebUI backend.
- **Sandbox network.** Docling and Open Terminal are only on an `internal: true` network, so they
  have no route out (checked: DNS and HTTPS fail from both). This keeps Open Terminal from reading
  the web (D-008) and from sending data out. Docling ships its models in the image and converts
  offline (a 15-page paper with tables: 18 s on the M-series CPU).
- **Open Terminal** is the full image (Python, data-science libraries, LibreOffice) rather than
  `slim`, because the Code preset runs code. Its home directory is `$OPEN_TERMINAL_DIR`.
- **Native function calling** is Open WebUI 0.12's default for every model, so nothing is set.
- **Thinking.** The gateway streams `reasoning_content` deltas (checked through the tunnel), which
  Open WebUI 0.12 renders as a collapsible reasoning block. No reasoning-tag settings needed.

**Tunnel.** `client/tunnel.sh` keeps one SSH control connection to the login host and adds or
cancels the forward over it (`ssh -O forward/cancel`). Every `TUNNEL_POLL` seconds it picks the
newest run file that has a gateway port and whose job `squeue` reports as running, so stale run
files from killed jobs are skipped. Tested against job 4732: a killed connection, a run file
pointing at a new port, the return to the old one, and no run file at all.

On Linux, containers reach `host-gateway` at the Docker bridge address, not `127.0.0.1`.
`TUNNEL_BIND_ADDRS` (default `127.0.0.1`) takes extra listen addresses, for example the bridge
address. Untested on Linux.

**Rejected.**
- `ENABLE_PERSISTENT_CONFIG=true` (the default): the environment would seed only the first start,
  and later changes in compose would be silently ignored.
- Open Terminal's own egress firewall (`OPEN_TERMINAL_ALLOWED_DOMAINS=""`): needs `NET_ADMIN`
  at start; the internal network gives the same result without extra privileges.
- Publishing SearXNG, Docling or Open Terminal ports: nothing on the laptop needs them directly.
- Model names typed into `compose.yaml` or `.env`: a second place to keep in sync with the roster.

## D-015: `client/llm` runs the whole cycle; idle jobs shut themselves down
**Status:** accepted · 2026-10-26

**Decision.** One bash script on the laptop, `client/llm` (symlinked onto `PATH`), with `up`,
`down [--keep-ui]`, `status` and `backup`. It runs under macOS's `/bin/bash` 3.2.
- **One job at a time.** `up` looks for my jobs named as in `cluster/serve.sbatch`
  (`squeue -u $USER -n <name> -t PD,CF,R`) and reuses one if it exists; otherwise it submits
  through `cluster/submit.sh` in `CLUSTER_REPO_DIR`. `down` cancels only jobs with that name.
- **Remote commands** go to the login host as a script on stdin of `bash -s`, over one SSH
  control connection, so the login shell (tcsh) never parses them.
- **Progress:** one line per state (pending, starting, ready); on a terminal the line carries a
  timer. Timeouts: 30 min in the queue (`LLM_QUEUE_TIMEOUT`; the job is left queued) and 15 min
  for startup (`LLM_START_TIMEOUT`; it prints the log tail and the job ID). A cold start
  measured 2:28 to 3:00.
- **Tunnel** runs detached (`nohup`, own process group) with its PID in
  `~/.local/state/local-llm/`. `client/tunnel.sh` now sleeps in the background, so `down`
  stops it at once instead of after up to `TUNNEL_POLL` seconds.
- **Backup** stops Open WebUI for about a second and archives the volume with a pinned
  `alpine:3.24.2`, so the SQLite database, the vector store and the uploads are copied at rest
  together. `cache/` (about 260 MB of models Open WebUI downloads by itself) is left out; a
  backup with two chats is 1.4 MB. The last 8 are kept.

**Idle shutdown.** `cluster/launch.py` reads every vLLM process's `/metrics` once a minute. A
change in the summed `vllm:request_success_total`, or any running or waiting request, counts as
activity (chat, embedding and rerank processes all export these). After `LLM_IDLE_MINUTES`
(default 120, 0 = never) without activity it shuts down like on SIGTERM; it doesn't resubmit.
A metrics fetch that fails doesn't count as idle time.

**Tested** on 2026-10-26: `up` reusing a running job (5 s) and run twice (one tunnel, one job);
`down` (9.5 s); `up` from cold (job 4733, ready in 3:00); `down --keep-ui`; `up` attaching to a
starting job; `status` at each step; `backup` with retention (11 files to 8) and a restore into
a scratch volume (same integrity check and row counts; Open WebUI started on it). Idle shutdown
in job 4735 with `LLM_IDLE_MINUTES=3`: ready at 15:47:34, one request at 15:49:43, shutdown at
15:53:34 (the next check after the request reset the timer), SLURM state COMPLETED 0:0.

**Rejected.**
- SQLite's online backup API: it copies `webui.db` only, not the vector store or the uploads, so
  the three could disagree.
- Counting gateway requests for idleness: Open WebUI lists models and the tunnel checks health
  regularly, which would keep a forgotten job alive forever.
- `scancel -n <name>` without `-u`: could cancel another user's job with the same name.

## D-018: Open WebUI runs in offline mode, without local Whisper
**Status:** accepted · 2026-10-27

**Decision.** `OFFLINE_MODE=true` and `WHISPER_MODEL=""` in `client/compose.yaml`.

- Open WebUI 0.12.3 ships its Whisper (base, 142 MB) and sentence-transformers (122 MB) models in
  the image; Docker copied them into the volume's `cache/` on first start. Offline mode sets
  `HF_HUB_OFFLINE`, turns off the version check and skips pip installs for Python tools, so
  nothing is downloaded at start. Embeddings and reranking go through the gateway (D-007) and
  voice input isn't set up, so neither model is used; an empty `WHISPER_MODEL` keeps Whisper from
  loading.
- Offline mode doesn't touch web search, `fetch_url` or the MCP tool servers.
- Consequence: Python tools or functions that declare pip requirements won't install them. The
  preset guard (D-017) needs none.
