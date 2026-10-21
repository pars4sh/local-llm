# cluster/

This directory holds what runs on the cluster: the SLURM job that serves the models. Everything
here runs on the GPU node inside a job, or on the login node to submit and check that job.

**Status:** phase 1 in progress. The main model serves and passes the smoke test (D-012); the
gateway, the other models and auto-resubmit are still to come.

Contents:

| File | Purpose |
|---|---|
| `serve.sbatch` | Job entry point. Reads `.env`, sets up a per-job `TMPDIR` and the pip CUDA toolkit, runs `launch.py` |
| `submit.sh` | Submits `serve.sbatch` with the partition, time, account and output path from `.env`; `--print` shows the command |
| `launch.py` | Starts each enabled model in roster order with free ports and `/health` waits, logs startup milestones, writes the run file, and stops everything on exit |
| `roster.py` | Validates `config/models.toml` and builds each model's `vllm serve` command |
| `smoke_test.sh` / `smoke_test.py` | Checks a live job from inside its allocation: text, image, tool call, thinking on and off, a ~50k-token prompt, decode speed |
| `check_env.py` | GPU environment check for a short job: compute capability, vLLM import, Triton and FlashInfer kernels |
| `pyproject.toml` / `uv.lock` | Pinned vLLM environment (D-012) |

Quick use (from the repository root; works in tcsh):

```
cluster/submit.sh                       # submit the serving job
srun --jobid=<jobid> --overlap --ntasks=1 cluster/smoke_test.sh
```

Conventions for this side are in `.claude/rules/cluster.md`.
