#!/usr/bin/env bash
# Forward the gateway of the running serving job to this laptop, and follow the job when it moves.
#
# Usage: client/tunnel.sh            run in the foreground; Ctrl-C closes the tunnel
#
# Every TUNNEL_POLL seconds it reads the newest run file on the cluster
# ($CLUSTER_RUN_DIR/run/<jobid>.env) that has a gateway port and belongs to a running job, and
# forwards 127.0.0.1:$GATEWAY_LOCAL_PORT to <node>:<gateway port> through the login host. When a
# new job starts on another node or port, the forward moves with it. All SSH traffic goes over
# one control connection, which is reopened if it drops.
#
# Settings (.env): CLUSTER_SSH_HOST (required), GATEWAY_LOCAL_PORT (default 4000),
# CLUSTER_RUN_DIR (default ~/.local-llm on the cluster), TUNNEL_BIND_ADDRS (default 127.0.0.1;
# comma-separated, see client/README.md for Linux), TUNNEL_POLL (default 30).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/env.sh
source "$REPO/scripts/env.sh"
load_env "$REPO/.env"

host=${CLUSTER_SSH_HOST:-}
[[ -n $host ]] || die "CLUSTER_SSH_HOST is not set (see .env.example)"
local_port=${GATEWAY_LOCAL_PORT:-4000}
poll=${TUNNEL_POLL:-30}
IFS=, read -ra bind_addrs <<<"${TUNNEL_BIND_ADDRS:-127.0.0.1}"

log() { echo "$(date '+%F %T') tunnel: $*" >&2; }
# Sleep in the background, so a TERM (e.g. from `llm down`) is handled at once, not after it.
pause() {
  sleep "$poll" &
  wait $! || true
}

for addr in "${bind_addrs[@]}"; do
  if nc -z "$addr" "$local_port" 2>/dev/null; then
    die "$addr:$local_port is already in use (another tunnel running?)"
  fi
done

# The control socket path must stay short (about 100 bytes on macOS), so not under $TMPDIR.
ctl_dir=$(mktemp -d /tmp/llm-tunnel.XXXXXX)
ctl=$ctl_dir/ctl
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15
  -o ServerAliveCountMax=3 -S "$ctl")

cleanup() {
  ssh "${ssh_opts[@]}" -O exit "$host" 2>/dev/null || true
  rm -rf "$ctl_dir"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Runs on the login host under bash (its login shell may be tcsh). Prints "<job> <node> <port>"
# for the newest run file with a gateway whose job is still running; exits 3 if there is none.
if [[ -n ${CLUSTER_RUN_DIR:-} ]]; then
  run_dir_line="d=$(printf %q "$CLUSTER_RUN_DIR")"
else
  # shellcheck disable=SC2016 # $HOME expands on the cluster
  run_dir_line='d=$HOME/.local-llm'
fi
read -r -d '' find_job <<EOF || true
$run_dir_line
for f in \$(ls -t "\$d"/run/*.env 2>/dev/null); do
  job=\$(sed -n 's/^LLM_JOB_ID=//p' "\$f")
  node=\$(sed -n 's/^LLM_NODE=//p' "\$f")
  port=\$(sed -n 's/^LLM_GATEWAY_PORT=//p' "\$f")
  [ -n "\$job" ] && [ -n "\$node" ] && [ -n "\$port" ] || continue
  squeue -h -j "\$job" -t RUNNING -o %i 2>/dev/null | grep -q . || continue
  echo "\$job \$node \$port"
  exit 0
done
exit 3
EOF

master_up() { ssh "${ssh_opts[@]}" -O check "$host" 2>/dev/null; }
start_master() {
  ssh "${ssh_opts[@]}" -o ControlMaster=yes -o ControlPersist=no -fN "$host"
}

# forward add|cancel NODE PORT: change the forward on every bind address.
forward() {
  local op=$1 node=$2 port=$3 addr
  for addr in "${bind_addrs[@]}"; do
    ssh "${ssh_opts[@]}" -O "$op" -L "$addr:$local_port:$node:$port" "$host" 2>/dev/null ||
      return 1
  done
}

current="" # "<job> <node> <port>" while forwarding
waiting=0
while true; do
  if ! master_up; then
    [[ -z $current ]] || log "connection to the login host dropped; reconnecting"
    current=""
    if ! start_master; then
      log "can't reach $host; retrying in ${poll}s"
      pause
      continue
    fi
  fi

  rc=0
  target=$(ssh "${ssh_opts[@]}" "$host" bash -s <<<"$find_job") || rc=$?
  if ((rc == 0)) && [[ $target != "$current" ]]; then
    if [[ -n $current ]]; then
      read -r _ node port <<<"$current"
      forward cancel "$node" "$port" || true
    fi
    read -r job node port <<<"$target"
    if forward forward "$node" "$port"; then
      current=$target
      waiting=0
      log "job $job: ${bind_addrs[*]}:$local_port -> $node:$port"
      if curl -fs -m 10 -o /dev/null "http://${bind_addrs[0]}:$local_port/health/liveliness"; then
        log "gateway is up"
      else
        log "warning: gateway did not answer /health/liveliness"
      fi
    else
      current=""
      log "could not open the forward to $node:$port; retrying in ${poll}s"
    fi
  elif ((rc == 3)); then
    if [[ -n $current ]]; then
      read -r _ node port <<<"$current"
      forward cancel "$node" "$port" || true
      current=""
    fi
    ((waiting)) || log "no running job with a gateway; waiting for one"
    waiting=1
  elif ((rc != 0)); then
    log "run-file lookup failed (ssh exit $rc); retrying in ${poll}s"
  fi
  pause
done
