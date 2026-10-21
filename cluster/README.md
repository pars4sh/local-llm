# cluster/

This directory holds what runs on the cluster: the SLURM job that serves the models. Everything
here runs on the GPU node inside a job, or on the login node to submit and check that job.

**Status:** not started (roadmap phase 1).

Planned contents:

| File | Purpose |
|---|---|
| `serve.sbatch` | Job entry point. Reads `.env`, picks free ports, starts the models and the gateway, writes the run file, resubmits before the wall-time limit |
| `launch.py` | Reads the roster, starts each vLLM process in order with health waits, generates the gateway config |
| `smoke_test.sh` | Checks a live job: text, image, tool call, thinking on and off, embeddings, rerank |
| `pyproject.toml` / `uv.lock` | Pinned vLLM environment |

Conventions for this side are in `.claude/rules/cluster.md`.
