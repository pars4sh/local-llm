#!/usr/bin/env bash
#SBATCH --job-name=local-llm
#SBATCH --nodes=1
#SBATCH --ntasks=1
# Serving job: starts the enabled roster models with cluster/launch.py and keeps them up until
# the job ends. Submit it with cluster/submit.sh, which passes the partition, time, account
# and output path from .env and runs the job from the repository root.
set -euo pipefail

REPO=$PWD
[[ -f $REPO/cluster/launch.py ]] || {
  echo "error: run from the repository root (use cluster/submit.sh)" >&2
  exit 1
}
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

VENV=$REPO/cluster/.venv
[[ -x $VENV/bin/python ]] || die "no vLLM environment; run: uv sync --locked --project cluster"
[[ -n ${LLM_MODEL_DIR:-} ]] || die "LLM_MODEL_DIR is not set (see .env.example)"
export LLM_MODEL_DIR LLM_RUN_DIR=${LLM_RUN_DIR:-$HOME/.local-llm}

# Per-job scratch on the node's /tmp (RAM-backed at the development site), removed on exit.
export TMPDIR=/tmp/local-llm-$SLURM_JOB_ID
mkdir -p "$TMPDIR"
chmod 700 "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT
trap 'exit 143' TERM INT

# Jobs never download or change weights; everything comes from LLM_MODEL_DIR.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
[[ -n ${HF_HOME:-} ]] && export HF_HOME
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-4} MKL_NUM_THREADS=${MKL_NUM_THREADS:-4}
export PATH=$VENV/bin:$PATH
use_pip_cuda_toolkit "$VENV/bin/python" "$TMPDIR" ||
  echo "warning: no pip CUDA toolkit found; FlashInfer JIT kernels stay disabled" >&2

echo "job $SLURM_JOB_ID on $(hostname -s), $(date -Is)"
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader

step=(srun --ntasks=1)
[[ -n ${SLURM_CPUS_PER_TASK:-} ]] && step+=(--cpus-per-task="$SLURM_CPUS_PER_TASK")
"${step[@]}" "$VENV/bin/python" "$REPO/cluster/launch.py"
