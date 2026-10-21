---
paths:
  - "cluster/**"
  - "gateway/**"
  - "scripts/**"
---

# Cluster-side code (SLURM job, vLLM, gateway, downloads)

## Jobs
- A serving job runs for `SLURM_TIME` (set in `.env`, within the partition's limit). Design for
  restarts:
  - resubmit before `SLURM_TIME` runs out, but only when `LLM_AUTO_RESUBMIT=1` (off by default)
  - keep nothing on the node that must outlive the job
- Two jobs must be able to run at once without clashing. Pick free ports at job start, and write
  the node and ports to a run file, `$LLM_RUN_DIR/run/<jobid>.env`. The tunnel script and the
  agents read that file.
- Request enough CPUs (`SLURM_CPUS_PER_TASK`, default 16). Set `OMP_NUM_THREADS` and similar
  thread variables explicitly to avoid thread-affinity warning spam.
- Log to `$LLM_RUN_DIR/logs/<jobid>/`, one file per process.

## vLLM
- Start the vLLM processes one after another, waiting for `GET /health` before starting the
  next. Each process sizes its GPU memory share at startup, so parallel starts collide.
- The `--gpu-memory-utilization` values of all processes must add up to at most 0.9. Keep the
  budget table in `docs/architecture.md` in sync with the roster.
- The GPU is an RTX PRO 6000 Blackwell (sm_120). Prefer dense FP8 checkpoints on stock vLLM.
  NVFP4 and MoE kernel paths on sm_120 have needed community patches. If something only works
  with a patched build, record that in `docs/decisions.md`.
- Don't rely on sleep-mode hot-swapping unless you've tested it on the pinned version. Upstream
  reported it broken in v0.14+ (vllm#32714). Serve extra models as separate jobs instead.
- Starting flags for the Qwen3.8 main model (check them against the vLLM recipe for the pinned version):
  `--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --kv-cache-dtype fp8`,
  plus MTP: `--speculative-config '{"method": "mtp", "num_speculative_tokens": 2}'`.
- Each vLLM process binds to `127.0.0.1`. Only the gateway binds to all interfaces, and it requires a key.

## Models and downloads
- Compute nodes have internet at the development site, but not at every site. Either way:
  - download on the login node, into `$LLM_MODEL_DIR`
  - run serving jobs with `HF_HUB_OFFLINE=1`, so a job never downloads or changes weights on its own
- Never write weights inside the repo.
- If `LLM_STAGE_DIR` is set, copy the weights there at job start. This avoids reloading 30+ GB
  from a network filesystem each time the job restarts.

## Environments
- Use a separate uv environment each for vLLM and the gateway, with versions pinned in a lock
  file or a requirements file in the component folder.
- Torch and CUDA must match sm_120. In the first job, check that
  `torch.cuda.get_device_capability()` returns `(12, 0)`.
