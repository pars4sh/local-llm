# evals/

A fixed set of prompts taken from real work. Rerun it after every model, quantization or vLLM
version change, so that choices rest on measurements instead of impressions.

**Status:** not started (roadmap phase 5).

Planned contents:

| Path | Purpose |
|---|---|
| `prompts/*.yaml` | One case per file: prompt, attachments, expected tool calls or answer, and how to score it |
| `run.py` | Runs the set against the gateway; records the model, vLLM version, latency, tokens per second and scores |
| `results/` | Raw outputs (gitignored); summaries go in `docs/decisions.md` when they drive a decision |

Case categories:
- multi-step tool use
- questions about figures (images)
- code edits in a small fixture repository
- questions about papers that have known answers
- long-context recall
