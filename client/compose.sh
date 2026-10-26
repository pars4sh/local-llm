#!/usr/bin/env bash
# Run `docker compose` for the laptop stack with the repository's .env and the client models
# from the roster (cluster/roster.py client-env), so no model name is copied by hand.
#
# Usage: client/compose.sh ARGS...     e.g. up -d | ps | logs -f open-webui | config | down
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"

[[ -f $REPO/.env ]] || die "no .env; copy .env.example and fill in the laptop section"

python=""
for p in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$p" >/dev/null &&
    "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    python=$p
    break
  fi
done
[[ -n $python ]] || die "need Python >= 3.11 to read the roster"

roster_env=$(mktemp)
trap 'rm -f "$roster_env"' EXIT
"$python" "$REPO/cluster/roster.py" client-env >"$roster_env"

docker compose --project-directory "$REPO/client" -f "$REPO/client/compose.yaml" \
  --env-file "$REPO/.env" --env-file "$roster_env" "$@"
