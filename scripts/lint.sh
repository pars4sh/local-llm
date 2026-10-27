#!/usr/bin/env bash
# Lint the repository with pinned tool versions: shellcheck and bash -n on every bash script,
# ruff check and ruff format --check on every Python file. This is the one place the versions
# are pinned; `llm clean` keeps these images.
#
# Usage: scripts/lint.sh           check only
#        scripts/lint.sh --fix     also apply ruff's fixes and formatting
#
# Uses Docker when it is available (laptop), otherwise uvx and a shellcheck on PATH (cluster),
# which must be the pinned version.
set -euo pipefail

RUFF_VERSION=0.15.4
SHELLCHECK_VERSION=v0.11.0

fix=0
case ${1:-} in
  --fix) fix=1 ;;
  "") ;;
  *)
    echo "usage: scripts/lint.sh [--fix]" >&2
    exit 2
    ;;
esac

cd "$(dirname "$0")/.."

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  ruff() { docker run --rm -v "$PWD:/io" -w /io "ghcr.io/astral-sh/ruff:$RUFF_VERSION" "$@"; }
  shellcheck() { docker run --rm -v "$PWD:/mnt" -w /mnt "koalaman/shellcheck:$SHELLCHECK_VERSION" "$@"; }
else
  ruff() { uvx "ruff@$RUFF_VERSION" "$@"; }
  have=$(command shellcheck --version 2>/dev/null | sed -n 's/^version: //p')
  if [[ v$have != "$SHELLCHECK_VERSION" ]]; then
    echo "shellcheck ${have:-not found}, but $SHELLCHECK_VERSION is pinned" >&2
    exit 1
  fi
fi

# Bash scripts: tracked *.sh and *.sbatch, plus extensionless files with a bash shebang.
scripts=()
while IFS= read -r f; do
  case $f in
    *.sh | *.sbatch) scripts+=("$f") ;;
    *.*) ;;
    *) if head -n 1 "$f" | grep -q '^#!/usr/bin/env bash'; then scripts+=("$f"); fi ;;
  esac
done < <(git ls-files)

status=0
for f in "${scripts[@]}"; do bash -n "$f" || status=1; done
shellcheck -x "${scripts[@]}" || status=1

if ((fix)); then
  ruff check --fix . || status=1
  ruff format . || status=1
else
  ruff check . || status=1
  ruff format --check . || status=1
fi
exit $status
