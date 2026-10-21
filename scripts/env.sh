# shellcheck shell=bash
# Shared helpers for the bash scripts. Source this file; don't run it.

# load_env FILE: export KEY=VALUE lines from a .env file.
# - Variables already set in the environment win, so `VAR=x script` overrides .env once.
# - Values are taken literally (nothing is expanded or executed); one pair of surrounding
#   quotes is removed. Comments and blank lines are skipped.
load_env() {
  local file=$1 line key value
  [[ -f $file ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    if [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]]; then
      value=${BASH_REMATCH[1]}
    fi
    [[ -n ${!key+set} ]] && continue
    export "$key=$value"
  done <"$file"
}

die() {
  echo "error: $*" >&2
  exit 1
}

# use_pip_cuda_toolkit PYTHON SCRATCH_DIR: point JIT compilers (FlashInfer and others) at
# the CUDA toolkit that pip installed into PYTHON's environment, so no system CUDA is needed.
# That toolkit has no unversioned libcudart.so, so link one in SCRATCH_DIR for the linker.
use_pip_cuda_toolkit() {
  local python=$1 scratch=$2 home
  home=$("$python" -c 'import os, nvidia; print(os.path.join(list(nvidia.__path__)[0], "cu13"))') ||
    return 1
  [[ -x $home/bin/nvcc && -e $home/lib/libcudart.so.13 ]] || return 1
  mkdir -p "$scratch/cuda-lib"
  ln -sf "$home/lib/libcudart.so.13" "$scratch/cuda-lib/libcudart.so"
  export CUDA_HOME=$home
  export LIBRARY_PATH=$scratch/cuda-lib${LIBRARY_PATH:+:$LIBRARY_PATH}
}

# roster ARGS...: run cluster/roster.py with any Python >= 3.11 that uv can find.
roster() {
  uv run --quiet --no-project --python '>=3.11' "$REPO/cluster/roster.py" "$@"
}
