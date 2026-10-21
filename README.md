# local-llm

A self-hosted AI assistant for an HPC cluster. Open-weight models are served by vLLM on a SLURM
GPU node, and you use them through Open WebUI on your laptop. The assistant can:
- search the web and do research with sources
- read papers and images
- create and edit files in a sandbox
- help with coding

> **Status:** early, work in progress. Nothing here has been validated end to end yet; see
> [docs/roadmap.md](docs/roadmap.md).

## Architecture

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

- **Models run on the cluster.** Several vLLM processes share one GPU, and a LiteLLM gateway
  gives them a single OpenAI-compatible endpoint protected by an API key.
- **The interface runs on your laptop.** Chat history, web search, document extraction and the
  file sandbox keep working when a GPU job ends; the tunnel reconnects when the next job starts.
- **Coding happens in your real repositories,** through a terminal agent on the login node that
  talks to the same gateway.

More detail is in [docs/architecture.md](docs/architecture.md), and the reasons behind each
choice are in [docs/decisions.md](docs/decisions.md).

## Repository layout

```
cluster/   SLURM job: starts vLLM processes and the gateway, smoke test
gateway/   LiteLLM proxy config (generated from the model roster)
client/    Laptop side: Docker Compose stack and SSH tunnel script
evals/     Fixed prompt set and runner for comparing models and versions
scripts/   Helpers such as model downloads
docs/      Architecture, decisions, roadmap
```

## Requirements

- A SLURM cluster with an NVIDIA GPU, and SSH access to the compute node while your job runs.
  Development uses a 96 GB RTX PRO 6000 Blackwell; a smaller GPU needs a smaller model roster.
- [uv](https://docs.astral.sh/uv/) on the cluster.
- Docker on the laptop.

## Quickstart

Not available yet; it will be written once phases 1 and 2 of the [roadmap](docs/roadmap.md)
are done. All site-specific settings will live in `.env`; see [.env.example](.env.example).

## License

Not chosen yet.
