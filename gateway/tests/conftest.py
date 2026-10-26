"""Shared setup for the gateway tests. Run: uv run --project gateway pytest gateway/tests"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Set before anything imports litellm: no network fetches, no telemetry, and a usage-log
# directory that isn't the real one.
os.environ.update(
    LITELLM_LOCAL_MODEL_COST_MAP="True",
    LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS="True",
    LITELLM_TELEMETRY="False",
    LLM_RUN_DIR=tempfile.mkdtemp(prefix="gateway-tests-"),
)
sys.path[:0] = [str(REPO / "gateway"), str(REPO / "cluster")]
