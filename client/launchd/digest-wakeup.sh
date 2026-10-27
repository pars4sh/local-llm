#!/usr/bin/env bash
# Optional: start everything (`llm up --no-open`) before the weekly digest runs, so the GPU job
# and the stack are up when the automation fires. macOS only (launchd).
#
# Usage: client/launchd/digest-wakeup.sh install [WEEKDAY HH:MM]   default: 1 09:40 (Monday)
#        client/launchd/digest-wakeup.sh uninstall
#        client/launchd/digest-wakeup.sh status
#
# The job runs only while the Mac is awake and logged in; launchd runs a missed time once the
# Mac wakes. To wake a sleeping Mac a few minutes earlier, use pmset (needs an admin password):
#   sudo pmset repeat wakeorpoweron M 09:35:00
# The plist is generated with this machine's paths and is not kept in the repository.
set -euo pipefail

LABEL=local-llm.digest-wakeup
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LOG=${XDG_STATE_HOME:-$HOME/.local/state}/local-llm/digest-wakeup.log

die() {
  echo "digest-wakeup: $*" >&2
  exit 1
}

install() {
  local weekday=${1:-1} at=${2:-09:40}
  [[ $weekday =~ ^[0-6]$ ]] || die "WEEKDAY is 0-6 (0 = Sunday)"
  [[ $at =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "time is HH:MM"
  local hour=$((10#${at%%:*})) minute=$((10#${at##*:}))
  local docker python
  docker=$(command -v docker) || die "docker is not on PATH"
  python=$(command -v python3) || die "python3 is not on PATH"
  # launchd starts jobs with a minimal PATH; give the job the directories `llm` needs.
  local path
  path="$(dirname "$docker"):$(dirname "$python"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$REPO/client/llm</string><string>up</string><string>--no-open</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$path</string></dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>$weekday</integer>
    <key>Hour</key><integer>$hour</integer>
    <key>Minute</key><integer>$minute</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed: llm up --no-open, weekday $weekday at $at; log: $LOG"
}

uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "uninstalled"
}

status() {
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "installed ($PLIST)"
  else
    echo "not installed"
  fi
}

case ${1:-} in
  install) shift; install "$@" ;;
  uninstall) uninstall ;;
  status) status ;;
  *) die "usage: $0 install [WEEKDAY HH:MM] | uninstall | status" ;;
esac
