#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

FORCE_ATTACH=0
DEVICE_PROFILE="${OPENCLAW_ANDROID_DEVICE_PROFILE:-samsung-galaxy-s24-ultra}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${OPENCLAW_ANDROID_STATE_DIR:-$RUNTIME_DIR/openclaw-android-waydroid}"
PERSISTENT_STATE_DIR="${OPENCLAW_ANDROID_PERSISTENT_STATE_DIR:-$(openclaw_android_state_home)}"
LOG_DIR="${OPENCLAW_ANDROID_LOG_DIR:-$(openclaw_android_log_dir)}"
WESTON_SOCKET="${OPENCLAW_ANDROID_WESTON_SOCKET:-wayland-1}"
UI_LOG="${OPENCLAW_ANDROID_UI_LOG:-$LOG_DIR/waydroid-ui.log}"
ATTACH_LOG="${OPENCLAW_ANDROID_UI_ATTACH_LOG:-$LOG_DIR/ui-attach.log}"
ATTACH_STAMP_FILE="${OPENCLAW_ANDROID_UI_ATTACH_STAMP_FILE:-$STATE_DIR/ui.attach.stamp}"
REATTACH_SECONDS="${OPENCLAW_ANDROID_UI_REATTACH_SECONDS:-30}"

usage() {
  cat <<'EOF'
Usage: ./scripts/ensure_waydroid_ui.sh [--force]

Attach the Android UI to an already-running Waydroid session on the current
desktop session bus and nested Weston socket.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force|--attach-only) FORCE_ATTACH=1 ;;
    --no-ui) exit 0 ;;
    --reset-session)
      fatal "Session resets are owned by run_waydroid_ui_supervisor.sh"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fatal "Unknown argument: $1"
      ;;
  esac
  shift
done

require_cmd adb
require_cmd gdbus
require_cmd waydroid

[[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] || fatal "DBUS_SESSION_BUS_ADDRESS is not set"
[[ -S "$RUNTIME_DIR/$WESTON_SOCKET" ]] || fatal "Wayland socket $RUNTIME_DIR/$WESTON_SOCKET is missing"

mkdir -p "$STATE_DIR"
mkdir -p "$PERSISTENT_STATE_DIR"
mkdir -p "$LOG_DIR"

export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export WAYLAND_DISPLAY="$WESTON_SOCKET"
export OPENCLAW_ANDROID_WESTON_SOCKET="$WESTON_SOCKET"

waydroid_running() {
  timeout 5s waydroid status 2>/dev/null | grep -q $'^Session:\tRUNNING'
}

session_bus_ready() {
  timeout 3s gdbus call --session \
    --dest id.waydro.Session \
    --object-path /SessionManager \
    --method org.freedesktop.DBus.Introspectable.Introspect >/dev/null 2>&1
}

attach_stamp_age() {
  [[ -f "$ATTACH_STAMP_FILE" ]] || return 1
  local now last
  now="$(date +%s)"
  last="$(stat -c %Y "$ATTACH_STAMP_FILE" 2>/dev/null || true)"
  [[ -n "$last" ]] || return 1
  printf '%s\n' "$((now - last))"
}

attach_recently() {
  [[ "$FORCE_ATTACH" -eq 1 ]] && return 1
  local age
  age="$(attach_stamp_age 2>/dev/null || true)"
  [[ -n "$age" && "$age" -lt "$REATTACH_SECONDS" ]]
}

adb_serial() {
  local ip
  ip="$(waydroid_ip_address || true)"
  [[ -n "$ip" ]] || return 1
  printf '%s:5555\n' "$ip"
}

wake_and_unlock() {
  local serial="$1"
  adb_quick connect "$serial" >/dev/null 2>&1 || true
  adb_quick -s "$serial" shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1 || true
  adb_quick -s "$serial" shell wm dismiss-keyguard >/dev/null 2>&1 || true
  adb_quick -s "$serial" shell input keyevent 82 >/dev/null 2>&1 || true
}

configure_power() {
  local serial="$1"
  adb_quick -s "$serial" shell settings put global stay_on_while_plugged_in 7 >/dev/null 2>&1 || true
  adb_quick -s "$serial" shell settings put system screen_off_timeout 2147483647 >/dev/null 2>&1 || true
  adb_quick -s "$serial" shell svc power stayon true >/dev/null 2>&1 || true
  adb_quick -s "$serial" shell locksettings set-disabled true >/dev/null 2>&1 || true
}

apply_device_profile() {
  local serial="$1"
  OPENCLAW_ANDROID_ADB_SERIAL="$serial" OPENCLAW_ANDROID_DEVICE_PROFILE="$DEVICE_PROFILE" \
    "$PROJECT_ROOT/scripts/apply_device_profile.sh" >/dev/null 2>&1 || true
}

attach_ui() {
  : >"$UI_LOG"
  {
    printf '[%(%F %T)T] Launching Waydroid UI on %s\n' -1 "$WAYLAND_DISPLAY"
  } >>"$UI_LOG" 2>&1

  env XDG_RUNTIME_DIR="$RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    setsid -f waydroid show-full-ui >>"$UI_LOG" 2>&1 || true
  sleep 2

  if grep -q "Already tracking a session" "$UI_LOG"; then
    printf '[%(%F %T)T] ERROR: attach hit a session-bus mismatch\n' -1 >>"$ATTACH_LOG"
    return 3
  fi
  if grep -q "RuntimeError:" "$UI_LOG"; then
    printf '[%(%F %T)T] ERROR: attach raised a Waydroid runtime error\n' -1 >>"$ATTACH_LOG"
    return 2
  fi

  touch "$ATTACH_STAMP_FILE"
  printf '[%(%F %T)T] OK: attached UI to %s\n' -1 "$WAYLAND_DISPLAY" >>"$ATTACH_LOG"
}

waydroid_running || fatal "Waydroid session is not running"

if ! session_bus_ready; then
  printf '[%(%F %T)T] WARN: session bus probe failed on the current desktop bus; continuing because Waydroid reports Session: RUNNING\n' -1 >>"$ATTACH_LOG"
fi

if attach_recently; then
  printf '[%(%F %T)T] SKIP: recent attach already succeeded\n' -1 >>"$ATTACH_LOG"
  exit 0
fi

SERIAL="$(adb_serial || true)"
if [[ -n "$SERIAL" ]]; then
  wake_and_unlock "$SERIAL"
  configure_power "$SERIAL"
  apply_device_profile "$SERIAL"
fi

attach_ui "$SERIAL"
