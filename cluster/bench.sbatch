#!/usr/bin/env bash
#SBATCH --job-name=local-llm-bench
#SBATCH --nodes=1
#SBATCH --ntasks=1
# Benchmark job: runs cluster/bench.py with this script's arguments, in the same environment
# as the serving job. Example (from the repository root):
#   cluster/submit.sh --script cluster/bench.sbatch --time=00:30:00 -- \
#     variants --model qwen-main --variants-file cluster/bench_mtp_kv.txt
set -euo pipefail

REPO=$PWD
[[ -f $REPO/cluster/bench.py ]] || {
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
"${step[@]}" "$VENV/bin/python" "$REPO/cluster/bench.py" "$@"
