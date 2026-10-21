#!/usr/bin/env bash
# Download roster checkpoints into $LLM_MODEL_DIR/<hf_repo>. Run on the login node;
# serving jobs run offline and only read these directories.
#
# Usage: scripts/download_models.sh [--check] [--dry-run] [--all | MODEL...]
#   (no names)  the enabled models in config/models.toml
#   MODEL...    these roster names, enabled or not
#   --all       every model in the roster
#   --check     don't download: check that the repo IDs exist and show sizes and free space
#   --dry-run   print the download commands without running them
#
# Settings come from .env (the environment wins): LLM_MODEL_DIR (required), HF_HOME.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

# Pinned so the download does not depend on whichever `hf` is on PATH.
HF_HUB_VERSION=2.2.0
HF_XET_VERSION=1.7.0

check=0 dry_run=0 all=0
names=()
for arg in "$@"; do
  case $arg in
    --check) check=1 ;;
    --dry-run) dry_run=1 ;;
    --all) all=1 ;;
    -h | --help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*) die "unknown option $arg" ;;
    *) names+=("$arg") ;;
  esac
done
((all == 0 || ${#names[@]} == 0)) || die "pass either --all or model names, not both"

[[ -n ${LLM_MODEL_DIR:-} ]] || die "LLM_MODEL_DIR is not set (see .env.example)"
case $(realpath -m "$LLM_MODEL_DIR")/ in
  "$REPO"/*) die "LLM_MODEL_DIR must be outside the repository" ;;
esac
command -v uv >/dev/null || die "uv is not on PATH"

# "<name> <kind> <hf_repo>" lines for the selected models, in start order.
if ((all || ${#names[@]})); then
  selected=$(roster repos --all)
else
  selected=$(roster repos)
fi
if ((${#names[@]})); then
  filtered=""
  for n in "${names[@]}"; do
    line=$(awk -v n="$n" '$1 == n' <<<"$selected")
    [[ -n $line ]] || die "no model named '$n' in the roster"
    filtered+="$line"$'\n'
  done
  selected=$filtered
fi
[[ -n ${selected//[[:space:]]/} ]] || die "no models selected (none enabled?)"
mapfile -t repos < <(awk 'NF {print $3}' <<<"$selected")

if ((check)); then
  echo "Model directory: $LLM_MODEL_DIR"
  if [[ -d $LLM_MODEL_DIR ]]; then
    df -h "$LLM_MODEL_DIR" | tail -1
    if command -v getfattr >/dev/null; then
      # CephFS quota of the nearest ancestor that has one (0 or missing = no quota).
      d=$(realpath "$LLM_MODEL_DIR")
      while [[ $d != / ]]; do
        max=$(getfattr --only-values -n ceph.quota.max_bytes "$d" 2>/dev/null || true)
        if [[ -n $max && $max != 0 ]]; then
          used=$(getfattr --only-values -n ceph.dir.rbytes "$d" 2>/dev/null || echo 0)
          awk -v d="$d" -v m="$max" -v u="$used" 'BEGIN {
            printf "CephFS quota on %s: %.0f GB used of %.0f GB, %.0f GB free\n", d, u/1e9, m/1e9, (m-u)/1e9 }'
          break
        fi
        d=$(dirname "$d")
      done
    fi
  else
    echo "(does not exist yet)"
  fi
  echo
  exec uv run --quiet --no-project --python '>=3.11' "$REPO/scripts/check_repos.py" "${repos[@]}"
fi

export HF_HOME HF_HUB_DISABLE_TELEMETRY=1
# The weights land in --local-dir; don't keep a second copy in the Xet chunk cache.
export HF_XET_CHUNK_CACHE_SIZE_BYTES=0

hf=(uvx --quiet --from "huggingface_hub==$HF_HUB_VERSION" --with "hf-xet==$HF_XET_VERSION" hf)
for repo in "${repos[@]}"; do
  dest=$LLM_MODEL_DIR/$repo
  cmd=("${hf[@]}" download "$repo" --local-dir "$dest")
  if ((dry_run)); then
    printf '%q ' "${cmd[@]}"
    echo
    continue
  fi
  echo "==> $repo -> $dest"
  mkdir -p "$dest"
  start=$SECONDS
  "${cmd[@]}"
  echo "==> $repo done in $((SECONDS - start)) s, $(du -sh "$dest" | cut -f1)"
done
