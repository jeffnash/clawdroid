#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

ACTION="${1:-status}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${OPENCLAW_ANDROID_STATE_DIR:-$RUNTIME_DIR/openclaw-android-waydroid}"
HEALTH_FILE="$STATE_DIR/health.env"
PID_FILE="$STATE_DIR/ui-supervisor.pid"
REQUEST_START_FILE="$STATE_DIR/request-start"
REQUEST_STOP_FILE="$STATE_DIR/request-stop"
REQUEST_RESET_FILE="$STATE_DIR/request-reset"
REQUEST_ATTACH_FILE="$STATE_DIR/request-attach"

usage() {
  cat <<'EOF'
Usage: ./scripts/waydroid_supervisor_ctl.sh <status|start|stop|reset|attach>

Control the desktop-session Waydroid UI supervisor through its runtime state
directory inside XDG_RUNTIME_DIR.
EOF
}

pid_alive() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

supervisor_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(<"$PID_FILE")"
  pid_alive "$pid" || return 1
  printf '%s\n' "$pid"
}

load_health() {
  [[ -f "$HEALTH_FILE" ]] || return 1
  # shellcheck disable=SC1090
  source "$HEALTH_FILE"
}

ensure_supervisor() {
  supervisor_pid >/dev/null || fatal "Waydroid UI supervisor is not running"
}

wait_for_condition() {
  local condition="$1"
  local timeout_seconds="${2:-60}"
  local deadline=$((SECONDS + timeout_seconds))
  while (( SECONDS < deadline )); do
    load_health || {
      sleep 1
      continue
    }
    if eval "$condition"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

print_status() {
  if ! load_health; then
    fatal "No health file present at $HEALTH_FILE"
  fi
  cat <<EOF
supervisor_pid=${SUPERVISOR_PID:-}
desired_state=${DESIRED_STATE:-}
display=${DISPLAY:-}
wayland_display=${WAYLAND_DISPLAY:-}
xdg_session_type=${XDG_SESSION_TYPE:-}
window_backend_requested=${WINDOW_BACKEND_REQUESTED:-}
weston_backend=${WESTON_BACKEND:-}
parent_wayland_display=${PARENT_WAYLAND_DISPLAY:-}
weston_pid=${WESTON_PID:-}
weston_socket_present=${WESTON_SOCKET_PRESENT:-0}
weston_window_present=${WESTON_WINDOW_PRESENT:-0}
waydroid_session_pid=${WAYDROID_SESSION_PID:-}
session_bus_ready=${SESSION_BUS_READY:-0}
waydroid_running=${WAYDROID_RUNNING:-0}
android_adb_serial=${ANDROID_ADB_SERIAL:-}
android_adb_state=${ANDROID_ADB_STATE:-}
android_adb_device=${ANDROID_ADB_DEVICE:-0}
android_boot_completed=${ANDROID_BOOT_COMPLETED:-0}
android_system_server=${ANDROID_SYSTEM_SERVER:-0}
android_ready=${ANDROID_READY:-0}
android_health_failures=${ANDROID_HEALTH_FAILURES:-0}
last_container_restart_at=${LAST_CONTAINER_RESTART_AT:-0}
last_attach_at=${LAST_ATTACH_AT:-0}
last_attach_ok=${LAST_ATTACH_OK:-0}
last_action=${LAST_ACTION:-}
last_error=${LAST_ERROR:-}
health_updated_at=${HEALTH_UPDATED_AT:-0}
EOF
}

case "$ACTION" in
  status)
    print_status
    ;;
  start)
    ensure_supervisor
    : >"$REQUEST_START_FILE"
    wait_for_condition '[[ "${DESIRED_STATE:-}" == "running" && "${WESTON_WINDOW_PRESENT:-0}" == "1" && "${WAYDROID_RUNNING:-0}" == "1" && "${ANDROID_READY:-0}" == "1" ]]' 90 ||
      fatal "Waydroid runtime did not become healthy after start request"
    print_status
    ;;
  stop)
    ensure_supervisor
    : >"$REQUEST_STOP_FILE"
    wait_for_condition '[[ "${DESIRED_STATE:-}" == "stopped" && -z "${WESTON_PID:-}" && -z "${WAYDROID_SESSION_PID:-}" ]]' 60 ||
      fatal "Waydroid runtime did not stop after stop request"
    print_status
    ;;
  reset)
    ensure_supervisor
    : >"$REQUEST_RESET_FILE"
    wait_for_condition '[[ "${DESIRED_STATE:-}" == "running" && "${WESTON_WINDOW_PRESENT:-0}" == "1" && "${WAYDROID_RUNNING:-0}" == "1" && "${ANDROID_READY:-0}" == "1" && "${LAST_ATTACH_OK:-0}" == "1" ]]' 120 ||
      fatal "Waydroid runtime did not recover after reset request"
    print_status
    ;;
  attach)
    ensure_supervisor
    request_time="$(date +%s)"
    : >"$REQUEST_ATTACH_FILE"
    wait_for_condition "[[ \"\${LAST_ATTACH_OK:-0}\" == \"1\" && \"\${LAST_ATTACH_AT:-0}\" -ge \"$request_time\" ]]" 60 ||
      fatal "Waydroid UI did not re-attach after attach request"
    print_status
    ;;
  -h|--help)
    usage
    ;;
  *)
    fatal "Unknown action: $ACTION"
    ;;
esac
