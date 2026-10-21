#!/usr/bin/env bash
# Submit a job with the SLURM settings from .env (by default the serving job).
#
# Usage: cluster/submit.sh [--print] [--script FILE] [SBATCH_OPTION...] [-- SCRIPT_ARG...]
#   --print           print the sbatch command instead of running it
#   --script FILE     job script to submit (default: cluster/serve.sbatch)
#   SBATCH_OPTION...  extra sbatch options; they override .env (e.g. --time=00:30:00)
#   SCRIPT_ARG...     arguments for the job script, after --
#
# The job's own output goes to $LLM_RUN_DIR/logs/slurm-<jobid>.out, and each process's log to
# $LLM_RUN_DIR/logs/<jobid>/. LLM_RUN_DIR defaults to ~/.local-llm.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

print=0
script=$REPO/cluster/serve.sbatch
opts=()
while (($#)); do
  case $1 in
    --print) print=1 ;;
    --script)
      [[ $# -ge 2 ]] || die "--script needs a file"
      script=$(realpath "$2")
      shift
      ;;
    --)
      shift
      break
      ;;
    *) opts+=("$1") ;;
  esac
  shift
done
[[ -f $script ]] || die "no job script at $script"

run_dir=${LLM_RUN_DIR:-$HOME/.local-llm}
# sbatch needs the output directory to exist before the job starts.
((print)) || mkdir -p "$run_dir/logs" "$run_dir/run"

args=(--chdir="$REPO" --output="$run_dir/logs/slurm-%j.out")
[[ -n ${SLURM_PARTITION:-} ]] && args+=(--partition="$SLURM_PARTITION")
[[ -n ${SLURM_TIME:-} ]] && args+=(--time="$SLURM_TIME")
[[ -n ${SLURM_CPUS_PER_TASK:-} ]] && args+=(--cpus-per-task="$SLURM_CPUS_PER_TASK")
[[ -n ${SLURM_GRES:-} ]] && args+=(--gres="$SLURM_GRES")
read -ra extra <<<"${SLURM_EXTRA_ARGS:-}"
args+=("${extra[@]}" "${opts[@]}" "$script" "$@")

if ((print)); then
  printf '%q ' sbatch "${args[@]}"
  echo
else
  exec sbatch "${args[@]}"
fi
