#!/usr/bin/env bash
#SBATCH --job-name=local-llm
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --signal=USR1@600
# Serving job: starts the enabled roster models with cluster/launch.py and keeps them up until
# the job ends. Submit it with cluster/submit.sh, which passes the partition, time, account
# and output path from .env and runs the job from the repository root. SLURM sends SIGUSR1
# about 10 minutes before the time limit; launch.py resubmits then if LLM_AUTO_RESUBMIT=1.
set -euo pipefail

REPO=$PWD
[[ -f $REPO/cluster/launch.py ]] || {
  echo "error: run from the repository root (use cluster/submit.sh)" >&2
  exit 1
}
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
setup_job_env "$REPO"

echo "job $SLURM_JOB_ID on $(hostname -s), $(date -Is)"
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader

step=(srun --ntasks=1)
[[ -n ${SLURM_CPUS_PER_TASK:-} ]] && step+=(--cpus-per-task="$SLURM_CPUS_PER_TASK")
"${step[@]}" "$VENV/bin/python" "$REPO/cluster/launch.py"
