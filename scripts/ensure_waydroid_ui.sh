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
UI_LOCK_FILE="${OPENCLAW_ANDROID_UI_LOCK_FILE:-$STATE_DIR/ui-attach.lock}"
REATTACH_SECONDS="${OPENCLAW_ANDROID_UI_REATTACH_SECONDS:-30}"
ATTACH_VERIFY_SECONDS="${OPENCLAW_ANDROID_UI_ATTACH_VERIFY_SECONDS:-15}"

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
require_cmd flock
require_cmd gdbus
require_cmd waydroid

[[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] || fatal "DBUS_SESSION_BUS_ADDRESS is not set"
[[ -S "$RUNTIME_DIR/$WESTON_SOCKET" ]] || fatal "Wayland socket $RUNTIME_DIR/$WESTON_SOCKET is missing"

mkdir -p "$STATE_DIR"
mkdir -p "$PERSISTENT_STATE_DIR"
mkdir -p "$LOG_DIR"

exec 8>"$UI_LOCK_FILE"
if ! flock -n 8; then
  printf '[%(%F %T)T] SKIP: another Waydroid UI attach is already running\n' -1 >>"$ATTACH_LOG"
  exit 0
fi

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

ui_process_running() {
  pgrep -u "$(id -u)" -f '[/]waydroid show-full-ui' >/dev/null 2>&1
}

# Prints the Android-side open-window count, or nothing when it cannot be
# read (no serial, adb unreachable, prop missing). Callers must treat an
# empty result as "unknown", not as zero.
android_open_windows() {
  local serial="${1:-}"
  [[ -n "$serial" ]] || return 1
  adb_quick -s "$serial" shell getprop waydroid.open_windows 2>/dev/null | tr -d '\r[:space:]'
}

# Returns 0 when the UI is verifiably attached. `waydroid show-full-ui`
# is a fire-and-forget client on session-managed setups (it delegates to
# the running session and exits), so process liveness proves nothing;
# Android's own open-window count is the source of truth. Returns 2 when
# Android definitively reports zero open windows, 0 when it reports at
# least one or cannot be asked (unknown is not failure).
ui_attach_verified() {
  local serial="${1:-}"
  local windows
  windows="$(android_open_windows "$serial" || true)"
  if [[ "$windows" =~ ^[0-9]+$ ]] && (( windows == 0 )); then
    return 2
  fi
  return 0
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

stop_stale_ui_processes() {
  local pids pid
  pids="$(pgrep -u "$(id -u)" -f '[/]waydroid show-full-ui' || true)"
  [[ -n "$pids" ]] || return 0
  printf '[%(%F %T)T] Stopping stale Waydroid UI processes: %s\n' -1 "$pids" >>"$ATTACH_LOG"
  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] && kill "$pid" 2>/dev/null || true
  done <<<"$pids"
  sleep 1
  pids="$(pgrep -u "$(id -u)" -f '[/]waydroid show-full-ui' || true)"
  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -KILL "$pid" 2>/dev/null || true
  done <<<"$pids"
}

attach_ui() {
  local serial="${1:-}"
  local windows

  # Already showing a window? Nothing to do. When a launcher process is
  # still around but Android reports zero windows, that is the classic
  # false-positive attach: replace it instead of trusting it.
  windows="$(android_open_windows "$serial" || true)"
  if [[ "$windows" =~ ^[0-9]+$ ]] && (( windows > 0 )); then
    printf '[%(%F %T)T] SKIP: Android already reports %s open window(s)\n' -1 "$windows" >>"$ATTACH_LOG"
    touch "$ATTACH_STAMP_FILE"
    return 0
  fi
  if ui_process_running; then
    if [[ "$windows" =~ ^[0-9]+$ ]] && (( windows == 0 )); then
      printf '[%(%F %T)T] Waydroid UI process is running but Android reports no open window; relaunching\n' -1 >>"$ATTACH_LOG"
      stop_stale_ui_processes
    else
      # A launch is in flight and Android cannot be asked yet; let it be.
      printf '[%(%F %T)T] SKIP: Waydroid UI launch is already in flight\n' -1 >>"$ATTACH_LOG"
      touch "$ATTACH_STAMP_FILE"
      return 0
    fi
  fi

  if [[ -f "$UI_LOG" ]]; then
    mv -f "$UI_LOG" "$UI_LOG.old" 2>/dev/null || : >"$UI_LOG"
  fi
  {
    printf '[%(%F %T)T] Launching Waydroid UI on %s\n' -1 "$WAYLAND_DISPLAY"
  } >>"$UI_LOG" 2>&1

  # 8>&- keeps the attach lock from leaking into the long-lived UI
  # process; otherwise the launcher would hold the flock for its entire
  # lifetime and every later invocation would silently skip as "already
  # running" even after partial failures.
  env XDG_RUNTIME_DIR="$RUNTIME_DIR" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    setsid -f waydroid show-full-ui >>"$UI_LOG" 2>&1 8>&- || true

  local deadline=$((SECONDS + ATTACH_VERIFY_SECONDS))
  while (( SECONDS < deadline )); do
    if grep -q "Already tracking a session" "$UI_LOG"; then
      printf '[%(%F %T)T] ERROR: attach hit a session-bus mismatch\n' -1 >>"$ATTACH_LOG"
      return 3
    fi
    if grep -q "RuntimeError:" "$UI_LOG"; then
      printf '[%(%F %T)T] ERROR: attach raised a Waydroid runtime error\n' -1 >>"$ATTACH_LOG"
      return 2
    fi
    if [[ -z "$serial" ]]; then
      # Android is unreachable over adb, so window count cannot be
      # verified; error-free launch is the best signal available.
      break
    fi
    windows="$(android_open_windows "$serial" || true)"
    if [[ "$windows" =~ ^[0-9]+$ ]] && (( windows > 0 )); then
      break
    fi
    sleep 1
  done

  if [[ -n "$serial" ]]; then
    windows="$(android_open_windows "$serial" || true)"
    if [[ "$windows" =~ ^[0-9]+$ ]] && (( windows == 0 )); then
      printf '[%(%F %T)T] ERROR: attach did not produce a visible Android window\n' -1 >>"$ATTACH_LOG"
      return 4
    fi
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
