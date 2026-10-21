#!/usr/bin/env bash
# Issue, list or revoke gateway client keys (one key per client).
#
# Usage: scripts/new_client_key.sh NAME          issue a key for NAME and print it once
#        scripts/new_client_key.sh --revoke NAME  remove NAME's key
#        scripts/new_client_key.sh --list         list client names and creation dates
#
# Only a SHA-256 of each key is stored, in $LLM_GATEWAY_KEYS (default
# $LLM_RUN_DIR/gateway-keys.json, mode 600). A running gateway picks up changes on the next
# request. Lost keys can't be recovered; revoke and issue a new one.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

run_dir=${LLM_RUN_DIR:-$HOME/.local-llm}
keys=${LLM_GATEWAY_KEYS:-$run_dir/gateway-keys.json}

action=issue
case ${1:-} in
  --list) action=list ;;
  --revoke)
    action=revoke
    shift
    ;;
  "" | -h | --help)
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac
name=${1:-}
if [[ $action != list ]]; then
  [[ $name =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "client name must match [a-z0-9][a-z0-9._-]*"
fi

# Updates the keys file atomically. The key arrives on stdin, never on a command line where
# ps could show it.
update='
import datetime, hashlib, json, os, sys
path, action, name = sys.argv[1:4]
data = {"clients": {}}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
clients = data.setdefault("clients", {})
if action == "list":
    for n, c in sorted(clients.items()):
        print(n.ljust(24), "created", c["created"])
    sys.exit(0)
if action == "revoke":
    if clients.pop(name, None) is None:
        sys.exit(f"error: no key for {name}")
else:
    if name in clients:
        sys.exit(f"error: {name} already has a key; revoke it first")
    clients[name] = {
        "sha256": hashlib.sha256(sys.stdin.read().encode()).hexdigest(),
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, path)
'

mkdir -p "$(dirname "$keys")"
key=
if [[ $action == issue ]]; then key=sk-$(openssl rand -hex 32); fi
printf '%s' "$key" |
  uv run --quiet --no-project --python '>=3.11' python3 -c "$update" "$keys" "$action" "$name"

case $action in
  issue)
    echo "Key for $name (shown once; store it where the client reads it):" >&2
    echo "$key"
    ;;
  revoke) echo "Revoked the key for $name." >&2 ;;
esac
