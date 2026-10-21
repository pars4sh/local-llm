#!/usr/bin/env bash
# Submit the serving job with the SLURM settings from .env.
#
# Usage: cluster/submit.sh [--print] [SBATCH_OPTION...]
#   SBATCH_OPTION...  extra sbatch options; they override .env (e.g. --time=00:30:00)
#   --print           print the sbatch command instead of running it
#
# The job's own output goes to $LLM_RUN_DIR/logs/slurm-<jobid>.out, and each model's log to
# $LLM_RUN_DIR/logs/<jobid>/. LLM_RUN_DIR defaults to ~/.local-llm.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

print=0
if [[ ${1:-} == --print ]]; then
  print=1
  shift
fi

run_dir=${LLM_RUN_DIR:-$HOME/.local-llm}
# sbatch needs the output directory to exist before the job starts.
((print)) || mkdir -p "$run_dir/logs" "$run_dir/run"

args=(--chdir="$REPO" --output="$run_dir/logs/slurm-%j.out")
[[ -n ${SLURM_PARTITION:-} ]] && args+=(--partition="$SLURM_PARTITION")
[[ -n ${SLURM_TIME:-} ]] && args+=(--time="$SLURM_TIME")
[[ -n ${SLURM_CPUS_PER_TASK:-} ]] && args+=(--cpus-per-task="$SLURM_CPUS_PER_TASK")
[[ -n ${SLURM_GRES:-} ]] && args+=(--gres="$SLURM_GRES")
read -ra extra <<<"${SLURM_EXTRA_ARGS:-}"
args+=("${extra[@]}" "$@" "$REPO/cluster/serve.sbatch")

if ((print)); then
  printf '%q ' sbatch "${args[@]}"
  echo
else
  exec sbatch "${args[@]}"
fi
