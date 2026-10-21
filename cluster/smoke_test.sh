#!/usr/bin/env bash
# Smoke-test a running serving job (checks are in cluster/smoke_test.py).
#
# vLLM listens on 127.0.0.1 on the GPU node, so run this inside the job's allocation:
#   srun --jobid=<jobid> --overlap --ntasks=1 cluster/smoke_test.sh
#
# Usage: cluster/smoke_test.sh [RUN_FILE] [--model NAME]
#   RUN_FILE defaults to $LLM_RUN_DIR/run/$SLURM_JOB_ID.env, which is the right file when
#   started with srun --jobid as above.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

run_file=
if [[ ${1:-} != "" && ${1:-} != -* ]]; then
  run_file=$1
  shift
fi
if [[ -z $run_file ]]; then
  [[ -n ${SLURM_JOB_ID:-} ]] || die "pass a run file, or run inside the job with srun --jobid"
  run_file=${LLM_RUN_DIR:-$HOME/.local-llm}/run/$SLURM_JOB_ID.env
fi
[[ -f $run_file ]] || die "no run file at $run_file (are the models up yet?)"

python=$REPO/cluster/.venv/bin/python
[[ -x $python ]] || python=python3
exec "$python" "$REPO/cluster/smoke_test.py" "$run_file" "$@"
