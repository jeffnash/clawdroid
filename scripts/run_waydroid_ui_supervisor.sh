#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

require_cmd adb
require_cmd flock
require_cmd gdbus
require_cmd timeout
require_cmd waydroid
require_cmd weston

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${OPENCLAW_ANDROID_STATE_DIR:-$RUNTIME_DIR/openclaw-android-waydroid}"
PERSISTENT_STATE_DIR="${OPENCLAW_ANDROID_PERSISTENT_STATE_DIR:-$(openclaw_android_state_home)}"
LOG_DIR="${OPENCLAW_ANDROID_LOG_DIR:-$(openclaw_android_log_dir)}"
LOCK_FILE="$STATE_DIR/ui-supervisor.lock"
SUPERVISOR_PID_FILE="$STATE_DIR/ui-supervisor.pid"
GRAPHICAL_ENV_FILE="$STATE_DIR/graphical.env"
HEALTH_FILE="$STATE_DIR/health.env"
DESIRED_STATE_FILE="$STATE_DIR/desired.state"
REQUEST_START_FILE="$STATE_DIR/request-start"
REQUEST_STOP_FILE="$STATE_DIR/request-stop"
REQUEST_RESET_FILE="$STATE_DIR/request-reset"
REQUEST_ATTACH_FILE="$STATE_DIR/request-attach"
WESTON_PID_FILE="$STATE_DIR/weston.pid"
SESSION_PID_FILE="$STATE_DIR/session.pid"

WESTON_SOCKET_REQUESTED="${OPENCLAW_ANDROID_WESTON_SOCKET:-wayland-1}"
WESTON_SOCKET="$WESTON_SOCKET_REQUESTED"
WINDOW_BACKEND_REQUESTED="${OPENCLAW_ANDROID_WINDOW_BACKEND:-auto}"
WESTON_BACKEND=""
PARENT_WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}"
WESTON_LOG="${OPENCLAW_ANDROID_WESTON_LOG:-$LOG_DIR/weston.log}"
SESSION_LOG="${OPENCLAW_ANDROID_SESSION_LOG:-$LOG_DIR/waydroid-session.log}"
UI_LOG="${OPENCLAW_ANDROID_UI_LOG:-$LOG_DIR/waydroid-ui.log}"
SUPERVISOR_LOG="${OPENCLAW_ANDROID_UI_SUPERVISOR_LOG:-$LOG_DIR/ui-supervisor.log}"
ATTACH_STAMP_FILE="${OPENCLAW_ANDROID_UI_ATTACH_STAMP_FILE:-$STATE_DIR/ui.attach.stamp}"

ADB_SERIAL="${OPENCLAW_ANDROID_ADB_SERIAL:-}"
INTERVAL_SECONDS="${OPENCLAW_ANDROID_UI_SUPERVISOR_INTERVAL:-5}"
ATTACH_FAILURE_LIMIT="${OPENCLAW_ANDROID_UI_ATTACH_FAILURE_LIMIT:-3}"
START_TIMEOUT="${OPENCLAW_ANDROID_START_TIMEOUT:-60}"
SESSION_STALE_GRACE_SECONDS="${OPENCLAW_ANDROID_SESSION_STALE_GRACE_SECONDS:-20}"
ANDROID_BOOT_GRACE_SECONDS="${OPENCLAW_ANDROID_BOOT_GRACE_SECONDS:-90}"
ANDROID_HEALTH_FAILURE_LIMIT="${OPENCLAW_ANDROID_HEALTH_FAILURE_LIMIT:-4}"
AUTO_CONTAINER_RESTART="${OPENCLAW_ANDROID_AUTO_CONTAINER_RESTART:-1}"
CONTAINER_RESTART_COOLDOWN_SECONDS="${OPENCLAW_ANDROID_CONTAINER_RESTART_COOLDOWN_SECONDS:-180}"
SESSION_STOP_TIMEOUT="${OPENCLAW_ANDROID_SESSION_STOP_TIMEOUT:-8s}"
SESSION_PID_STOP_SECONDS="${OPENCLAW_ANDROID_SESSION_PID_STOP_SECONDS:-5}"
WESTON_PID_STOP_SECONDS="${OPENCLAW_ANDROID_WESTON_PID_STOP_SECONDS:-5}"
WESTON_WIDTH="${OPENCLAW_ANDROID_WESTON_WIDTH:-1600}"
WESTON_HEIGHT="${OPENCLAW_ANDROID_WESTON_HEIGHT:-900}"

mkdir -p "$STATE_DIR"
mkdir -p "$PERSISTENT_STATE_DIR"
mkdir -p "$LOG_DIR"

# Keep one previous generation instead of truncating, so the evidence of
# whatever killed the last run survives the restart that follows it.
rotate_log() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  mv -f "$file" "$file.old" 2>/dev/null || : >"$file"
}

rotate_log "$SUPERVISOR_LOG"
exec >>"$SUPERVISOR_LOG" 2>&1

# Sleep longer after consecutive start failures (capped) so a persistently
# broken component is retried gently instead of hot-looping every interval.
backoff_sleep() {
  local failures="$1"
  local delay=$((INTERVAL_SECONDS * (1 << (failures > 4 ? 4 : failures))))
  (( delay > 60 )) && delay=60
  sleep "$delay"
}

log() {
  printf '[%(%F %T)T] %s\n' -1 "$*"
}

fatal_env() {
  log "ERROR: $*"
  exit 1
}

write_kv() {
  printf '%s=%q\n' "$1" "${2:-}"
}

pid_alive() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

pid_age_seconds() {
  local pid="${1:-}"
  pid_alive "$pid" || return 1
  ps -o etimes= -p "$pid" 2>/dev/null | awk '{print $1}'
}

read_pidfile() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  local pid
  pid="$(<"$file")"
  pid_alive "$pid" || return 1
  printf '%s\n' "$pid"
}

weston_pid_matches() {
  local pid="${1:-}"
  local owner cmdline
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -d "/proc/$pid" ]] || return 1
  owner="$(stat -c '%u' "/proc/$pid" 2>/dev/null || true)"
  [[ "$owner" == "$(id -u)" ]] || return 1
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmdline" == *"weston"* && "$cmdline" == *"--socket=$WESTON_SOCKET"* ]]
}

find_weston_pid() {
  local pid
  if pid="$(read_pidfile "$WESTON_PID_FILE" 2>/dev/null)"; then
    if weston_pid_matches "$pid"; then
      printf '%s\n' "$pid"
    fi
  fi
}

find_session_pid() {
  local pid
  if pid="$(read_pidfile "$SESSION_PID_FILE" 2>/dev/null)"; then
    printf '%s\n' "$pid"
    return 0
  fi
  pgrep -u "$(id -u)" -f "waydroid session start" | head -n 1 || true
}

wayland_display_path() {
  local display="${1:-}"
  [[ -n "$display" ]] || return 1
  if [[ "$display" == /* ]]; then
    printf '%s\n' "$display"
  else
    printf '%s/%s\n' "$RUNTIME_DIR" "$display"
  fi
}

parent_wayland_ready() {
  local path
  path="$(wayland_display_path "$PARENT_WAYLAND_DISPLAY" 2>/dev/null || true)"
  [[ -n "$path" && -S "$path" ]]
}

x11_ready() {
  [[ -n "${DISPLAY:-}" ]] || return 1
  command -v xwininfo >/dev/null 2>&1 || return 1
  DISPLAY="$DISPLAY" xwininfo -root -display "$DISPLAY" >/dev/null 2>&1
}

resolve_window_backend() {
  case "$WINDOW_BACKEND_REQUESTED" in
    x11|wayland)
      WESTON_BACKEND="$WINDOW_BACKEND_REQUESTED"
      ;;
    auto)
      if [[ "${XDG_SESSION_TYPE:-}" == "wayland" && -n "$PARENT_WAYLAND_DISPLAY" ]] && parent_wayland_ready; then
        WESTON_BACKEND="wayland"
      elif x11_ready; then
        WESTON_BACKEND="x11"
      elif [[ -n "$PARENT_WAYLAND_DISPLAY" ]] && parent_wayland_ready; then
        WESTON_BACKEND="wayland"
      elif [[ -n "${DISPLAY:-}" ]]; then
        WESTON_BACKEND="x11"
      elif [[ -n "$PARENT_WAYLAND_DISPLAY" ]]; then
        WESTON_BACKEND="wayland"
      else
        return 1
      fi
      ;;
    *)
      log "Invalid OPENCLAW_ANDROID_WINDOW_BACKEND=$WINDOW_BACKEND_REQUESTED"
      return 1
      ;;
  esac

  if [[ "$WESTON_BACKEND" == "wayland" && -z "${OPENCLAW_ANDROID_WESTON_SOCKET:-}" ]]; then
    local parent_name
    parent_name="$(basename "$(wayland_display_path "$PARENT_WAYLAND_DISPLAY" 2>/dev/null || printf '%s' "$PARENT_WAYLAND_DISPLAY")")"
    if [[ "$WESTON_SOCKET" == "$parent_name" ]]; then
      WESTON_SOCKET="openclaw-wayland-1"
    fi
  elif [[ "$WESTON_BACKEND" == "wayland" && -n "${OPENCLAW_ANDROID_WESTON_SOCKET:-}" ]]; then
    local parent_name
    parent_name="$(basename "$(wayland_display_path "$PARENT_WAYLAND_DISPLAY" 2>/dev/null || printf '%s' "$PARENT_WAYLAND_DISPLAY")")"
    if [[ "$WESTON_SOCKET" == "$parent_name" ]]; then
      log "OPENCLAW_ANDROID_WESTON_SOCKET=$WESTON_SOCKET conflicts with the parent Wayland display"
      return 1
    fi
  fi
}

parent_display_ready() {
  case "$WESTON_BACKEND" in
    x11) x11_ready ;;
    wayland) parent_wayland_ready ;;
    *) return 1 ;;
  esac
}

weston_socket_present() {
  [[ -S "$RUNTIME_DIR/$WESTON_SOCKET" ]]
}

weston_window_present() {
  case "$WESTON_BACKEND" in
    x11)
      [[ -n "${DISPLAY:-}" ]] || return 1
      command -v xwininfo >/dev/null 2>&1 || return 1
      DISPLAY="$DISPLAY" xwininfo -root -tree -display "$DISPLAY" 2>/dev/null | grep -F '"Weston Compositor - screen0"' >/dev/null
      ;;
    wayland)
      local pid
      pid="$(find_weston_pid || true)"
      weston_socket_present && [[ -n "$pid" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

session_bus_ready() {
  timeout 3s gdbus call --session \
    --dest id.waydro.Session \
    --object-path /SessionManager \
    --method org.freedesktop.DBus.Introspectable.Introspect >/dev/null 2>&1
}

waydroid_running() {
  local status
  status="$(timeout 5s waydroid status 2>/dev/null || true)"
  grep -q $'^Session:\tRUNNING' <<<"$status" && grep -q $'^Container:\tRUNNING' <<<"$status"
}

waydroid_adb_serial() {
  local status ip
  if [[ -n "$ADB_SERIAL" ]]; then
    printf '%s\n' "$ADB_SERIAL"
    return 0
  fi
  status="$(timeout 5s waydroid status 2>/dev/null || true)"
  ip="$(awk -F '\t' '$1 == "IP address:" {print $2; exit}' <<<"$status" | cut -d/ -f1 | tr -d '\r')"
  [[ -n "$ip" ]] || return 1
  printf '%s:5555\n' "$ip"
}

adb_state_for_serial() {
  local serial="$1"
  local output state
  output="$(timeout 5s adb -s "$serial" get-state 2>&1 | tr -d '\r' || true)"
  if grep -qi 'unauthorized' <<<"$output"; then
    printf 'unauthorized'
    return 0
  fi
  state="$(awk 'NF { line = $0 } END { print line }' <<<"$output")"
  printf '%s' "${state:-unknown}"
}

refresh_android_health_flags() {
  local state boot_completed system_server_pid
  CURRENT_ADB_SERIAL="$(waydroid_adb_serial || true)"
  ANDROID_ADB_STATE=""
  ANDROID_ADB_DEVICE_OK=0
  ANDROID_BOOT_COMPLETED_OK=0
  ANDROID_SYSTEM_SERVER_OK=0
  ANDROID_READY_OK=0

  [[ -n "$CURRENT_ADB_SERIAL" ]] || return 1
  timeout 5s adb connect "$CURRENT_ADB_SERIAL" >/dev/null 2>&1 || true
  state="$(adb_state_for_serial "$CURRENT_ADB_SERIAL")"
  ANDROID_ADB_STATE="${state:-unknown}"
  [[ "$state" == "device" ]] || return 1
  ANDROID_ADB_DEVICE_OK=1

  boot_completed="$(timeout 5s adb -s "$CURRENT_ADB_SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  if [[ "$boot_completed" == "1" ]]; then
    ANDROID_BOOT_COMPLETED_OK=1
  fi

  system_server_pid="$(timeout 5s adb -s "$CURRENT_ADB_SERIAL" shell pidof system_server 2>/dev/null | tr -d '\r' || true)"
  if [[ "$system_server_pid" =~ ^[0-9]+([[:space:]][0-9]+)*$ ]]; then
    ANDROID_SYSTEM_SERVER_OK=1
  fi

  if [[ "$ANDROID_BOOT_COMPLETED_OK" -eq 1 && "$ANDROID_SYSTEM_SERVER_OK" -eq 1 ]]; then
    ANDROID_READY_OK=1
    return 0
  fi
  return 1
}

run_root_no_prompt() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
  else
    return 1
  fi
}

restart_waydroid_container_if_allowed() {
  local now
  [[ "$AUTO_CONTAINER_RESTART" == "1" ]] || {
    log "Automatic Waydroid container restart is disabled"
    return 1
  }

  now="$(date +%s)"
  if (( now - LAST_CONTAINER_RESTART_AT < CONTAINER_RESTART_COOLDOWN_SECONDS )); then
    log "Skipping Waydroid container restart; cooldown is still active"
    return 1
  fi

  if ! run_root_no_prompt true; then
    log "Passwordless root is unavailable; falling back to user-session recovery only"
    return 1
  fi

  LAST_ACTION="restart-container"
  LAST_CONTAINER_RESTART_AT="$now"
  log "Restarting Waydroid container after repeated Android health failures"
  timeout 20s waydroid session stop >/dev/null 2>&1 || true
  if command -v systemctl >/dev/null 2>&1; then
    run_root_no_prompt timeout 90s systemctl restart waydroid-container.service
  else
    run_root_no_prompt timeout 60s waydroid container stop >/dev/null 2>&1 || true
    run_root_no_prompt timeout 90s waydroid container start
  fi
}

write_graphical_env() {
  local tmp="$GRAPHICAL_ENV_FILE.tmp"
  {
    write_kv DISPLAY "${DISPLAY:-}"
    write_kv XAUTHORITY "${XAUTHORITY:-}"
    write_kv DBUS_SESSION_BUS_ADDRESS "${DBUS_SESSION_BUS_ADDRESS:-}"
    write_kv XDG_RUNTIME_DIR "$RUNTIME_DIR"
    write_kv WAYLAND_DISPLAY "$WESTON_SOCKET"
    write_kv OPENCLAW_ANDROID_WINDOW_BACKEND "$WINDOW_BACKEND_REQUESTED"
    write_kv OPENCLAW_ANDROID_WESTON_BACKEND "$WESTON_BACKEND"
    write_kv OPENCLAW_ANDROID_WESTON_SOCKET "$WESTON_SOCKET"
    write_kv OPENCLAW_ANDROID_STATE_DIR "$STATE_DIR"
  } >"$tmp"
  mv "$tmp" "$GRAPHICAL_ENV_FILE"
}

write_health() {
  local tmp="$HEALTH_FILE.tmp"
  {
    write_kv HEALTH_UPDATED_AT "$(date +%s)"
    write_kv DESIRED_STATE "$DESIRED_STATE"
    write_kv DISPLAY "${DISPLAY:-}"
    write_kv DBUS_SESSION_BUS_ADDRESS "${DBUS_SESSION_BUS_ADDRESS:-}"
    write_kv XDG_RUNTIME_DIR "$RUNTIME_DIR"
    write_kv WAYLAND_DISPLAY "$WESTON_SOCKET"
    write_kv XDG_SESSION_TYPE "${XDG_SESSION_TYPE:-}"
    write_kv WINDOW_BACKEND_REQUESTED "$WINDOW_BACKEND_REQUESTED"
    write_kv WESTON_BACKEND "$WESTON_BACKEND"
    write_kv PARENT_WAYLAND_DISPLAY "$PARENT_WAYLAND_DISPLAY"
    write_kv SUPERVISOR_PID "$$"
    write_kv WESTON_PID "${CURRENT_WESTON_PID:-}"
    write_kv WESTON_SOCKET_PRESENT "$WESTON_SOCKET_OK"
    write_kv WESTON_WINDOW_PRESENT "$WESTON_WINDOW_OK"
    write_kv WAYDROID_SESSION_PID "${CURRENT_SESSION_PID:-}"
    write_kv SESSION_BUS_READY "$SESSION_BUS_OK"
    write_kv WAYDROID_RUNNING "$WAYDROID_RUNNING_OK"
    write_kv ANDROID_ADB_SERIAL "${CURRENT_ADB_SERIAL:-}"
    write_kv ANDROID_ADB_STATE "${ANDROID_ADB_STATE:-}"
    write_kv ANDROID_ADB_DEVICE "$ANDROID_ADB_DEVICE_OK"
    write_kv ANDROID_BOOT_COMPLETED "$ANDROID_BOOT_COMPLETED_OK"
    write_kv ANDROID_SYSTEM_SERVER "$ANDROID_SYSTEM_SERVER_OK"
    write_kv ANDROID_READY "$ANDROID_READY_OK"
    write_kv ANDROID_HEALTH_FAILURES "$ANDROID_HEALTH_FAILURES"
    write_kv LAST_CONTAINER_RESTART_AT "$LAST_CONTAINER_RESTART_AT"
    write_kv LAST_ATTACH_AT "$LAST_ATTACH_AT"
    write_kv LAST_ATTACH_OK "$LAST_ATTACH_OK"
    write_kv LAST_ACTION "$LAST_ACTION"
    write_kv LAST_ERROR "$LAST_ERROR"
  } >"$tmp"
  mv "$tmp" "$HEALTH_FILE"
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))
  while (( SECONDS < deadline )); do
    pid_alive "$pid" || return 0
    sleep 1
  done
  return 1
}

stop_waydroid_ui() {
  local pids pid
  pids="$(pgrep -u "$(id -u)" -f '[/]waydroid show-full-ui' || true)"
  [[ -n "$pids" ]] || return 0

  LAST_ACTION="stop-ui"
  log "Stopping Waydroid UI processes: $pids"
  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] && kill "$pid" 2>/dev/null || true
  done <<<"$pids"

  local deadline=$((SECONDS + 3))
  while (( SECONDS < deadline )); do
    pids="$(pgrep -u "$(id -u)" -f '[/]waydroid show-full-ui' || true)"
    [[ -z "$pids" ]] && return 0
    sleep 1
  done

  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -KILL "$pid" 2>/dev/null || true
  done <<<"$pids"
}

stop_waydroid_session() {
  local pid
  stop_waydroid_ui
  pid="$(find_session_pid || true)"

  if [[ -n "$pid" ]] || waydroid_running; then
    LAST_ACTION="stop-session"
    log "Stopping Waydroid session${pid:+ pid $pid}"
    timeout "$SESSION_STOP_TIMEOUT" waydroid session stop >/dev/null 2>&1 || true
  fi

  [[ -n "$pid" ]] || {
    rm -f "$SESSION_PID_FILE"
    return 0
  }

  if pid_alive "$pid"; then
    kill "$pid" 2>/dev/null || true
    wait_for_pid_exit "$pid" "$SESSION_PID_STOP_SECONDS" || kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$SESSION_PID_FILE"
}

stop_weston() {
  local pid
  pid="$(find_weston_pid || true)"
  [[ -n "$pid" ]] || {
    rm -f "$WESTON_PID_FILE"
    rm -f "$RUNTIME_DIR/$WESTON_SOCKET" "$RUNTIME_DIR/$WESTON_SOCKET.lock"
    return 0
  }

  LAST_ACTION="stop-weston"
  log "Stopping Weston pid $pid"
  kill "$pid" 2>/dev/null || true
  wait_for_pid_exit "$pid" "$WESTON_PID_STOP_SECONDS" || kill -KILL "$pid" 2>/dev/null || true
  rm -f "$WESTON_PID_FILE"
  rm -f "$RUNTIME_DIR/$WESTON_SOCKET" "$RUNTIME_DIR/$WESTON_SOCKET.lock"
}

start_weston() {
  LAST_ACTION="start-weston"
  LAST_ERROR=""
  rm -f "$RUNTIME_DIR/$WESTON_SOCKET" "$RUNTIME_DIR/$WESTON_SOCKET.lock"
  rotate_log "$WESTON_LOG"

  log "Starting nested Weston backend=$WESTON_BACKEND socket=$WESTON_SOCKET"
  local -a weston_args=(
    weston
      --backend="$WESTON_BACKEND"
      --xwayland \
      --socket="$WESTON_SOCKET" \
      --width="$WESTON_WIDTH" \
      --height="$WESTON_HEIGHT" \
      --idle-time=0
  )
  if [[ "$WESTON_BACKEND" == "x11" ]]; then
    env -u WAYLAND_DISPLAY \
      DISPLAY="$DISPLAY" \
      XAUTHORITY="${XAUTHORITY:-}" \
      DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
      XDG_RUNTIME_DIR="$RUNTIME_DIR" \
      "${weston_args[@]}" >>"$WESTON_LOG" 2>&1 &
  else
    env -u DISPLAY \
      WAYLAND_DISPLAY="$PARENT_WAYLAND_DISPLAY" \
      DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
      XDG_RUNTIME_DIR="$RUNTIME_DIR" \
      "${weston_args[@]}" >>"$WESTON_LOG" 2>&1 &
  fi
  local pid=$!
  printf '%s\n' "$pid" >"$WESTON_PID_FILE"

  local deadline=$((SECONDS + START_TIMEOUT))
  while (( SECONDS < deadline )); do
    pid_alive "$pid" || break
    if weston_socket_present && weston_window_present; then
      return 0
    fi
    sleep 1
  done

  LAST_ERROR="weston-start-failed"
  log "Weston failed to become visible for backend $WESTON_BACKEND; see $WESTON_LOG"
  stop_weston
  return 1
}

start_waydroid_session() {
  LAST_ACTION="start-session"
  LAST_ERROR=""
  rotate_log "$SESSION_LOG"

  log "Starting Waydroid session on $WESTON_SOCKET"
  local -a session_env=(
    "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"
    "XDG_RUNTIME_DIR=$RUNTIME_DIR"
    "WAYLAND_DISPLAY=$WESTON_SOCKET"
    "OPENCLAW_ANDROID_WESTON_SOCKET=$WESTON_SOCKET"
  )
  [[ -n "${DISPLAY:-}" ]] && session_env+=("DISPLAY=$DISPLAY")
  [[ -n "${XAUTHORITY:-}" ]] && session_env+=("XAUTHORITY=$XAUTHORITY")
  env "${session_env[@]}" waydroid session start >>"$SESSION_LOG" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$SESSION_PID_FILE"

  local deadline=$((SECONDS + START_TIMEOUT))
  while (( SECONDS < deadline )); do
    pid_alive "$pid" || break
    if waydroid_running; then
      return 0
    fi
    sleep 1
  done

  LAST_ERROR="session-start-failed"
  log "Waydroid session failed to become ready; see $SESSION_LOG"
  stop_waydroid_session
  return 1
}

request_force_attach() {
  FORCE_ATTACH=1
  LAST_ATTACH_OK=0
}

refresh_health_flags() {
  CURRENT_WESTON_PID="$(find_weston_pid || true)"
  CURRENT_SESSION_PID="$(find_session_pid || true)"
  WESTON_SOCKET_OK=0
  WESTON_WINDOW_OK=0
  SESSION_BUS_OK=0
  WAYDROID_RUNNING_OK=0
  ANDROID_ADB_STATE=""
  ANDROID_ADB_DEVICE_OK=0
  ANDROID_BOOT_COMPLETED_OK=0
  ANDROID_SYSTEM_SERVER_OK=0
  ANDROID_READY_OK=0

  if weston_socket_present; then
    WESTON_SOCKET_OK=1
  fi
  if weston_window_present; then
    WESTON_WINDOW_OK=1
  fi
  if session_bus_ready; then
    SESSION_BUS_OK=1
  fi
  if waydroid_running; then
    WAYDROID_RUNNING_OK=1
  fi
  if [[ "$WAYDROID_RUNNING_OK" -eq 1 ]]; then
    refresh_android_health_flags || true
  else
    CURRENT_ADB_SERIAL=""
  fi
}

save_desired_state() {
  printf '%s\n' "$DESIRED_STATE" >"$DESIRED_STATE_FILE"
}

load_desired_state() {
  if [[ -f "$DESIRED_STATE_FILE" ]]; then
    DESIRED_STATE="$(<"$DESIRED_STATE_FILE")"
  else
    DESIRED_STATE="running"
    save_desired_state
  fi
}

handle_requests() {
  if [[ -f "$REQUEST_RESET_FILE" ]]; then
    rm -f "$REQUEST_RESET_FILE"
    DESIRED_STATE="running"
    save_desired_state
    log "Received reset request"
    stop_waydroid_session
    stop_weston
    rm -f "$ATTACH_STAMP_FILE"
    request_force_attach
  fi

  if [[ -f "$REQUEST_STOP_FILE" ]]; then
    rm -f "$REQUEST_STOP_FILE"
    DESIRED_STATE="stopped"
    save_desired_state
    log "Received stop request"
    stop_waydroid_session
    stop_weston
    rm -f "$ATTACH_STAMP_FILE"
  fi

  if [[ -f "$REQUEST_START_FILE" ]]; then
    rm -f "$REQUEST_START_FILE"
    DESIRED_STATE="running"
    save_desired_state
    log "Received start request"
    request_force_attach
  fi

  if [[ -f "$REQUEST_ATTACH_FILE" ]]; then
    rm -f "$REQUEST_ATTACH_FILE"
    log "Received attach request"
    request_force_attach
  fi
}

cleanup() {
  set +e
  log "Supervisor exiting; stopping owned runtime"
  stop_waydroid_session
  stop_weston
  rm -f \
    "$SUPERVISOR_PID_FILE" \
    "$HEALTH_FILE" \
    "$GRAPHICAL_ENV_FILE" \
    "$REQUEST_START_FILE" \
    "$REQUEST_STOP_FILE" \
    "$REQUEST_RESET_FILE" \
    "$REQUEST_ATTACH_FILE"
}

terminate() {
  trap - EXIT INT TERM
  cleanup
  exit 0
}

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  fatal_env "DBUS_SESSION_BUS_ADDRESS is not set; this supervisor must use the desktop session bus"
fi
if ! resolve_window_backend; then
  fatal_env "No usable desktop display found. Set OPENCLAW_ANDROID_WINDOW_BACKEND=x11 or wayland, then run scripts/import_graphical_env.sh from that desktop session."
fi
if [[ "$WESTON_BACKEND" == "x11" ]]; then
  [[ -n "${DISPLAY:-}" ]] || fatal_env "DISPLAY is not set; cannot start the X11 window backend"
  command -v xwininfo >/dev/null 2>&1 || fatal_env "xwininfo is missing; install x11-utils/xorg-xwininfo or use OPENCLAW_ANDROID_WINDOW_BACKEND=wayland"
else
  [[ -n "$PARENT_WAYLAND_DISPLAY" ]] || fatal_env "WAYLAND_DISPLAY is not set; cannot start the Wayland window backend"
  parent_wayland_ready || fatal_env "Wayland display $PARENT_WAYLAND_DISPLAY is not reachable from $RUNTIME_DIR"
fi

export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND_REQUESTED"
export OPENCLAW_ANDROID_WESTON_SOCKET="$WESTON_SOCKET"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Another Waydroid UI supervisor is already running"
  exit 0
fi

printf '%s\n' "$$" >"$SUPERVISOR_PID_FILE"
trap cleanup EXIT
trap terminate INT TERM
trap 'touch "$REQUEST_RESET_FILE"' USR1
trap 'touch "$REQUEST_ATTACH_FILE"' USR2

LAST_ATTACH_AT=0
LAST_ATTACH_OK=0
LAST_ACTION="startup"
LAST_ERROR=""
FORCE_ATTACH=1
ANDROID_HEALTH_FAILURES=0
ATTACH_FAILURES=0
WESTON_START_FAILURES=0
SESSION_START_FAILURES=0
LAST_CONTAINER_RESTART_AT=0

load_desired_state
write_graphical_env

log "Supervisor starting on DISPLAY=${DISPLAY:-unset}, DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unset}"

while true; do
  printf '%s\n' "$$" >"$SUPERVISOR_PID_FILE"
  write_graphical_env
  handle_requests

  if ! parent_display_ready; then
    existing_weston_pid="$(find_weston_pid || true)"
    existing_session_pid="$(find_session_pid || true)"
    if [[ -n "$existing_weston_pid" || -n "$existing_session_pid" || -S "$RUNTIME_DIR/$WESTON_SOCKET" ]]; then
      log "Parent desktop display is unavailable; stopping owned Waydroid runtime"
      stop_waydroid_session
      stop_weston
    fi
    LAST_ACTION="wait-display"
    LAST_ERROR="${WESTON_BACKEND}-display-not-ready"
    CURRENT_WESTON_PID=""
    CURRENT_SESSION_PID=""
    WESTON_SOCKET_OK=0
    WESTON_WINDOW_OK=0
    SESSION_BUS_OK=0
    WAYDROID_RUNNING_OK=0
    CURRENT_ADB_SERIAL=""
    ANDROID_ADB_STATE=""
    ANDROID_ADB_DEVICE_OK=0
    ANDROID_BOOT_COMPLETED_OK=0
    ANDROID_SYSTEM_SERVER_OK=0
    ANDROID_READY_OK=0
    write_health
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  refresh_health_flags

  if [[ "$DESIRED_STATE" == "stopped" ]]; then
    LAST_ACTION="stopped"
    LAST_ERROR=""
    write_health
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  if [[ -n "$CURRENT_WESTON_PID" ]] && [[ "$WESTON_WINDOW_OK" -ne 1 || "$WESTON_SOCKET_OK" -ne 1 ]]; then
    log "Weston pid $CURRENT_WESTON_PID has no visible surface or socket; recycling"
    stop_weston
    refresh_health_flags
    request_force_attach
  fi

  if [[ -z "$CURRENT_WESTON_PID" ]]; then
    start_weston || {
      WESTON_START_FAILURES=$((WESTON_START_FAILURES + 1))
      refresh_health_flags
      write_health
      backoff_sleep "$WESTON_START_FAILURES"
      continue
    }
    WESTON_START_FAILURES=0
    refresh_health_flags
    request_force_attach
  fi

  if [[ -n "$CURRENT_SESSION_PID" ]] && [[ "$WAYDROID_RUNNING_OK" -ne 1 ]]; then
    local_age="$(pid_age_seconds "$CURRENT_SESSION_PID" 2>/dev/null || printf '0')"
    if [[ "$local_age" =~ ^[0-9]+$ ]] && (( local_age >= SESSION_STALE_GRACE_SECONDS )); then
      log "Waydroid session pid $CURRENT_SESSION_PID is unhealthy; recycling"
      stop_waydroid_session
      refresh_health_flags
      request_force_attach
    fi
  fi

  if [[ -z "$CURRENT_SESSION_PID" && "$WAYDROID_RUNNING_OK" -eq 1 ]]; then
    if [[ "$SESSION_BUS_OK" -ne 1 ]]; then
      log "Waydroid session is running on a different or stale DBus session; recycling before attach"
      stop_waydroid_session
      refresh_health_flags
      request_force_attach
    elif [[ "$LAST_ATTACH_OK" -ne 1 ]]; then
      log "Waydroid session is already running without a persistent launcher pid; adopting the running session"
      request_force_attach
    fi
  elif [[ -z "$CURRENT_SESSION_PID" ]]; then
    start_waydroid_session || {
      SESSION_START_FAILURES=$((SESSION_START_FAILURES + 1))
      refresh_health_flags
      write_health
      backoff_sleep "$SESSION_START_FAILURES"
      continue
    }
    SESSION_START_FAILURES=0
    refresh_health_flags
    request_force_attach
  fi

  if [[ "$WAYDROID_RUNNING_OK" -eq 1 ]]; then
    if [[ "$ANDROID_READY_OK" -eq 1 ]]; then
      ANDROID_HEALTH_FAILURES=0
      LAST_ERROR=""
    elif [[ "${ANDROID_ADB_STATE:-}" == "unauthorized" ]]; then
      LAST_ERROR="adb-unauthorized"
    else
      if [[ -n "$CURRENT_SESSION_PID" ]]; then
        session_age="$(pid_age_seconds "$CURRENT_SESSION_PID" 2>/dev/null || printf '0')"
      else
        session_age="$ANDROID_BOOT_GRACE_SECONDS"
      fi
      if [[ "$ANDROID_BOOT_COMPLETED_OK" -ne 1 && "$session_age" =~ ^[0-9]+$ && "$session_age" -lt "$ANDROID_BOOT_GRACE_SECONDS" ]]; then
        LAST_ERROR="android-booting"
      else
        ANDROID_HEALTH_FAILURES=$((ANDROID_HEALTH_FAILURES + 1))
        LAST_ERROR="android-health-failed"
        log "Android health check failed ($ANDROID_HEALTH_FAILURES/$ANDROID_HEALTH_FAILURE_LIMIT): serial=${CURRENT_ADB_SERIAL:-unknown} adb=$ANDROID_ADB_DEVICE_OK boot=$ANDROID_BOOT_COMPLETED_OK system_server=$ANDROID_SYSTEM_SERVER_OK"
        if (( ANDROID_HEALTH_FAILURES >= ANDROID_HEALTH_FAILURE_LIMIT )); then
          log "Android stayed unhealthy; recycling Waydroid runtime"
          stop_waydroid_session
          stop_weston
          restart_waydroid_container_if_allowed || true
          ANDROID_HEALTH_FAILURES=0
          refresh_health_flags
          request_force_attach
          write_health
          sleep "$INTERVAL_SECONDS"
          continue
        fi
      fi
    fi

    if [[ "$LAST_ATTACH_OK" -eq 1 && "$FORCE_ATTACH" -ne 1 ]] && ! pgrep -u "$(id -u)" -f '[/]waydroid show-full-ui' >/dev/null 2>&1; then
      log "Waydroid UI process disappeared after a successful attach; re-attaching"
      request_force_attach
    fi

    if [[ "$FORCE_ATTACH" -eq 1 ]]; then
      LAST_ACTION="attach-ui"
      attach_rc=0
      OPENCLAW_ANDROID_WESTON_SOCKET="$WESTON_SOCKET" OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND_REQUESTED" \
        "$PROJECT_ROOT/scripts/ensure_waydroid_ui.sh" --force || attach_rc=$?
      if [[ "$attach_rc" -eq 0 ]]; then
        LAST_ATTACH_AT="$(date +%s)"
        LAST_ATTACH_OK=1
        LAST_ERROR=""
        FORCE_ATTACH=0
        ATTACH_FAILURES=0
      else
        LAST_ATTACH_OK=0
        ATTACH_FAILURES=$((ATTACH_FAILURES + 1))
        if [[ "$attach_rc" -eq 3 ]]; then
          LAST_ERROR="attach-bus-mismatch"
          log "Attach reported a session-bus mismatch; recycling the runtime"
          stop_waydroid_session
          stop_weston
          refresh_health_flags
          request_force_attach
          ATTACH_FAILURES=0
        elif (( ATTACH_FAILURES >= ATTACH_FAILURE_LIMIT )); then
          LAST_ERROR="attach-failed-recycling"
          log "Attach failed $ATTACH_FAILURES times in a row (last rc=$attach_rc); recycling the runtime"
          stop_waydroid_session
          stop_weston
          refresh_health_flags
          request_force_attach
          ATTACH_FAILURES=0
        else
          LAST_ERROR="attach-failed"
          log "Attach failed (rc=$attach_rc, attempt $ATTACH_FAILURES/$ATTACH_FAILURE_LIMIT)"
        fi
      fi
    fi
  fi

  LAST_ACTION="steady"
  refresh_health_flags
  write_health
  sleep "$INTERVAL_SECONDS"
done
