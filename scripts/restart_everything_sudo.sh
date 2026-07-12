#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" ]]; then
  echo "SUDO_USER is not set. Run this as: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
RUNTIME_DIR="/run/user/${TARGET_UID}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONTROL_SCRIPT="$PROJECT_ROOT/scripts/waydroid_supervisor_ctl.sh"
STATE_DIR="${OPENCLAW_ANDROID_STATE_DIR:-$RUNTIME_DIR/openclaw-android-waydroid}"
TARGET_STATE_HOME="${TARGET_HOME}/.local/state"
STATE_HOME_DIR="${OPENCLAW_ANDROID_PERSISTENT_STATE_DIR:-$TARGET_STATE_HOME/openclaw-android-waydroid}"
LOG_DIR="${OPENCLAW_ANDROID_LOG_DIR:-$STATE_HOME_DIR/logs}"
source "$PROJECT_ROOT/scripts/common.sh"

FORCE_CONTAINER_RESTART=1
WAIT_TIMEOUT="${OPENCLAW_ANDROID_RESTART_WAIT_TIMEOUT:-240}"

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/restart_everything_sudo.sh [options]

Restarts the Waydroid container, user daemon, and graphical Waydroid supervisor.
Use this after image-level changes such as GApps, ARM translation, or Waydroid
extras so the guest boots from the updated image.

Options:
  --soft, --no-container-restart
                          Keep a running Waydroid container and only reset services/UI
  --force-container-restart
                          Force a real Waydroid container restart (default)
  --wait-timeout SECONDS  Wait for Android/daemon readiness (default: 240)
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --soft|--no-container-restart) FORCE_CONTAINER_RESTART=0 ;;
    --force-container-restart) FORCE_CONTAINER_RESTART=1 ;;
    --wait-timeout) require_option_value "$1" "${2-}"; WAIT_TIMEOUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

if ! [[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]]; then
  fatal "--wait-timeout must be a non-negative integer"
fi

require_cmd adb
require_cmd curl
require_cmd python3

run_as_user() {
  sudo -u "$TARGET_USER" env \
    HOME="$TARGET_HOME" \
    USER="$TARGET_USER" \
    LOGNAME="$TARGET_USER" \
    XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    "$@"
}

restart_container() {
  echo "==> Restarting Waydroid container"
  run_as_user timeout 30s waydroid session stop >/dev/null 2>&1 || true
  if command -v systemctl >/dev/null 2>&1; then
    systemctl restart waydroid-container.service
  else
    timeout 60s waydroid container stop >/dev/null 2>&1 || true
    timeout 90s waydroid container start
  fi
  sleep 5
}

ensure_container_running() {
  echo "==> Ensuring Waydroid container is running"
  container_state="$(waydroid_container_state || true)"
  if [[ "$container_state" == "RUNNING" ]]; then
    echo "Waydroid container already running; soft restart requested"
    return 0
  fi
  if systemctl is-active --quiet waydroid-container.service; then
    echo "waydroid-container.service is active but the container is stopped; restarting it"
    systemctl restart waydroid-container.service
  else
    echo "Starting waydroid-container.service"
    systemctl start waydroid-container.service
  fi
  sleep 3
}

if [[ $FORCE_CONTAINER_RESTART -eq 1 ]]; then
  restart_container
else
  ensure_container_running
fi
ensure_waydroid_network_rules

echo "==> Requesting graphical supervisor reset"
if run_as_user test -x "$CONTROL_SCRIPT"; then
  if ! run_as_user "$CONTROL_SCRIPT" reset; then
    echo "WARN: graphical supervisor is not healthy yet or not running. Log into the XFCE desktop session to let autostart launch it." >&2
    echo "State dir: $STATE_DIR" >&2
  fi
else
  echo "WARN: missing control script at $CONTROL_SCRIPT" >&2
fi

wait_for_waydroid_ip() {
  local deadline=$((SECONDS + WAIT_TIMEOUT))
  local ip
  while (( SECONDS <= deadline )); do
    ip="$(waydroid_ip_address || true)"
    if [[ -n "$ip" ]]; then
      printf '%s\n' "$ip"
      return 0
    fi
    sleep 2
  done
  return 1
}

adb_state_for_serial() {
  local serial="$1"
  local output state
  output="$(run_as_user timeout "${OPENCLAW_ANDROID_ADB_COMMAND_TIMEOUT:-20s}" adb -s "$serial" get-state 2>&1 | tr -d '\r' || true)"
  if grep -qi 'unauthorized' <<<"$output"; then
    printf 'unauthorized'
    return 0
  fi
  state="$(awk 'NF { line = $0 } END { print line }' <<<"$output")"
  printf '%s' "${state:-unknown}"
}

wait_for_android_ready() {
  local serial="$1"
  local deadline=$((SECONDS + WAIT_TIMEOUT))
  local state boot_completed
  local unauthorized_warned=0
  while (( SECONDS <= deadline )); do
    run_as_user timeout "${OPENCLAW_ANDROID_ADB_COMMAND_TIMEOUT:-20s}" adb connect "$serial" >/dev/null 2>&1 || true
    state="$(adb_state_for_serial "$serial")"
    if [[ "$state" == "unauthorized" && $unauthorized_warned -eq 0 ]]; then
      echo "WARN: Android is waiting for USB debugging approval. In Waydroid, check 'Always allow from this computer' and tap 'Allow'." >&2
      unauthorized_warned=1
    fi
    if [[ "$state" == "device" ]]; then
      boot_completed="$(run_as_user timeout "${OPENCLAW_ANDROID_ADB_COMMAND_TIMEOUT:-20s}" adb -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
      if [[ "$boot_completed" == "1" ]]; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

daemon_status_ready() {
  python3 - "$1" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
waydroid = data.get("waydroid") or {}
bridge = data.get("bridge") or {}
current = data.get("current_app") or {}
ready = (
    data.get("ok") is True
    and isinstance(waydroid, dict)
    and waydroid.get("running") is True
    and waydroid.get("session") is True
    and isinstance(bridge, dict)
    and bridge.get("ok") is True
    and isinstance(current, dict)
    and bool(current.get("package"))
)
raise SystemExit(0 if ready else 1)
PY
}

wait_for_daemon_ready() {
  local deadline=$((SECONDS + WAIT_TIMEOUT))
  local status last_status
  while (( SECONDS <= deadline )); do
    status="$(curl -fsS -m 10 http://127.0.0.1:48765/v1/status 2>/dev/null || true)"
    if [[ -n "$status" ]] && daemon_status_ready "$status"; then
      return 0
    fi
    last_status="$status"
    sleep 2
  done
  if [[ -n "${last_status:-}" ]]; then
    printf '%s\n' "$last_status" >&2
  fi
  return 1
}

echo "==> Waiting for Waydroid Android readiness"
WAYDROID_IP="$(wait_for_waydroid_ip)" || fatal "Waydroid did not report an IP within ${WAIT_TIMEOUT}s"
ADB_SERIAL="$WAYDROID_IP:5555"
wait_for_android_ready "$ADB_SERIAL" || fatal "Waydroid ADB did not become ready at $ADB_SERIAL within ${WAIT_TIMEOUT}s"

echo "==> Restarting OpenClaw daemon service"
run_as_user systemctl --user restart --no-block openclaw-android-waydroid.service >/dev/null 2>&1 || true

echo "==> Waiting for daemon bridge readiness"
if ! wait_for_daemon_ready; then
  echo "WARN: daemon bridge did not become ready within ${WAIT_TIMEOUT}s. If Android shows an 'Allow USB debugging?' prompt, check 'Always allow from this computer', tap 'Allow', and rerun this script." >&2
fi

echo
echo "==> Final status"
run_as_user "$CONTROL_SCRIPT" status 2>/dev/null || true
run_as_user bash -lc "ps -u '$TARGET_USER' -o pid=,ppid=,args= | grep -E 'run_waydroid_ui_supervisor.sh|weston .*--socket=|waydroid session start' | grep -v grep || true"

echo
echo "Logs:"
echo "  Weston: $LOG_DIR/weston.log"
echo "  Waydroid session: $LOG_DIR/waydroid-session.log"
echo "  Waydroid UI: $LOG_DIR/waydroid-ui.log"
echo "  Supervisor: $LOG_DIR/ui-supervisor.log"
echo "  Attach: $LOG_DIR/ui-attach.log"
