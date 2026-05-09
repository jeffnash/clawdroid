#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${OPENCLAW_ANDROID_STATE_DIR:-$RUNTIME_DIR/openclaw-android-waydroid}"
GRAPHICAL_ENV_FILE="$STATE_DIR/graphical.env"
PID_FILE="$STATE_DIR/ui-supervisor.pid"

usage() {
  cat <<'EOF'
Usage: ./scripts/waydroid_graphical_exec.sh <command> [args...]

Run a command under the desktop session environment captured by the Waydroid UI
supervisor.
EOF
}

pid_alive() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

[[ $# -gt 0 ]] || {
  usage
  exit 1
}

[[ -f "$PID_FILE" ]] || fatal "Waydroid UI supervisor is not running"
SUPERVISOR_PID="$(<"$PID_FILE")"
pid_alive "$SUPERVISOR_PID" || fatal "Waydroid UI supervisor pid $SUPERVISOR_PID is not alive"
[[ -f "$GRAPHICAL_ENV_FILE" ]] || fatal "Missing graphical environment snapshot at $GRAPHICAL_ENV_FILE"

# shellcheck disable=SC1090
source "$GRAPHICAL_ENV_FILE"

export DISPLAY
export XAUTHORITY
export DBUS_SESSION_BUS_ADDRESS
export XDG_RUNTIME_DIR
export WAYLAND_DISPLAY
export OPENCLAW_ANDROID_WINDOW_BACKEND
export OPENCLAW_ANDROID_WESTON_BACKEND
export OPENCLAW_ANDROID_WESTON_SOCKET
exec "$@"
