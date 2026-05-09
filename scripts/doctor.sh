#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

DAEMON_BASE_URL="${OPENCLAW_ANDROID_DAEMON_BASE_URL:-http://127.0.0.1:48765}"
BRIDGE_PORT="${OPENCLAW_ANDROID_BRIDGE_PORT:-49317}"
WINDOW_BACKEND="${OPENCLAW_ANDROID_WINDOW_BACKEND:-auto}"
TARGET_USER="${OPENCLAW_ANDROID_DOCTOR_USER:-}"
HERMES_HOME_OVERRIDE="${HERMES_HOME:-}"
OPENCLAW_HOME_OVERRIDE="${OPENCLAW_HOME:-}"
REPAIR=0
JSON_OUTPUT=0
VERBOSE=0
INCLUDE_LOGS=0

usage() {
  cat <<'EOF'
Usage: ./doctor.sh [options]

Diagnose a Clawdroid/Waydroid install without changing the machine by default.

Options:
  --repair                 Start installed user services and reconnect ADB when safe
  --json                   Emit machine-readable JSON instead of human output
  --verbose                Print extra details for each check
  --include-logs           Include short journal/log excerpts for failed services
  --user NAME              Check user-session services/plugins for NAME
  --daemon-base-url URL    Daemon URL (default: http://127.0.0.1:48765)
  --hermes-home PATH       Hermes home to inspect (default: target user's ~/.hermes)
  --openclaw-home PATH     OpenClaw home to inspect (default: target user's ~/.openclaw)
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repair) REPAIR=1 ;;
    --json) JSON_OUTPUT=1 ;;
    --verbose) VERBOSE=1 ;;
    --include-logs) INCLUDE_LOGS=1 ;;
    --user) require_option_value "$1" "${2-}"; TARGET_USER="$2"; shift ;;
    --daemon-base-url) require_option_value "$1" "${2-}"; DAEMON_BASE_URL="$2"; shift ;;
    --hermes-home) require_option_value "$1" "${2-}"; HERMES_HOME_OVERRIDE="$2"; shift ;;
    --openclaw-home) require_option_value "$1" "${2-}"; OPENCLAW_HOME_OVERRIDE="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

DAEMON_BASE_URL="$(printf '%s' "$DAEMON_BASE_URL" | sed 's:/*$::')"

if [[ -z "$TARGET_USER" ]]; then
  if [[ "$(id -u)" == "0" && -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
    TARGET_USER="$SUDO_USER"
  else
    TARGET_USER="$(id -un)"
  fi
fi

if ! id "$TARGET_USER" >/dev/null 2>&1; then
  fatal "Unknown target user: $TARGET_USER"
fi

TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" ]] || fatal "Unable to determine home for $TARGET_USER"

HERMES_HOME="${HERMES_HOME_OVERRIDE:-$TARGET_HOME/.hermes}"
OPENCLAW_HOME="${OPENCLAW_HOME_OVERRIDE:-$TARGET_HOME/.openclaw}"
if [[ -n "${OPENCLAW_ANDROID_XDG_RUNTIME_DIR:-}" ]]; then
  RUNTIME_DIR="$OPENCLAW_ANDROID_XDG_RUNTIME_DIR"
elif [[ "$(id -u)" == "$TARGET_UID" && -n "${XDG_RUNTIME_DIR:-}" ]]; then
  RUNTIME_DIR="$XDG_RUNTIME_DIR"
else
  RUNTIME_DIR="/run/user/$TARGET_UID"
fi
if [[ -n "${OPENCLAW_ANDROID_DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  USER_DBUS="$OPENCLAW_ANDROID_DBUS_SESSION_BUS_ADDRESS"
elif [[ "$(id -u)" == "$TARGET_UID" && -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  USER_DBUS="$DBUS_SESSION_BUS_ADDRESS"
else
  USER_DBUS="unix:path=$RUNTIME_DIR/bus"
fi

RESULTS_FILE="$(mktemp)"
TMP_DIR="$(mktemp -d)"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
INFO_COUNT=0
SKIP_COUNT=0
ADB_SERIAL="${OPENCLAW_ANDROID_ADB_SERIAL:-}"

cleanup() {
  rm -f "$RESULTS_FILE"
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

sanitize_field() {
  printf '%s' "${1:-}" | tr '\t\r\n' '   '
}

add_result() {
  local status="$1"
  local id="$2"
  local summary="$3"
  local detail="${4:-}"

  case "$status" in
    pass) PASS_COUNT=$((PASS_COUNT + 1)) ;;
    warn) WARN_COUNT=$((WARN_COUNT + 1)) ;;
    fail) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
    info) INFO_COUNT=$((INFO_COUNT + 1)) ;;
    skip) SKIP_COUNT=$((SKIP_COUNT + 1)) ;;
    *) status="info"; INFO_COUNT=$((INFO_COUNT + 1)) ;;
  esac

  printf '%s\t%s\t%s\t%s\n' \
    "$(sanitize_field "$status")" \
    "$(sanitize_field "$id")" \
    "$(sanitize_field "$summary")" \
    "$(sanitize_field "$detail")" >>"$RESULTS_FILE"

  if [[ "$JSON_OUTPUT" -eq 0 ]]; then
    printf '[%s] %-34s %s\n' "$(printf '%s' "$status" | tr '[:lower:]' '[:upper:]')" "$id" "$summary"
    if [[ "$VERBOSE" -eq 1 && -n "$detail" ]]; then
      printf '       %s\n' "$detail"
    fi
  fi
}

run_as_target_user() {
  if [[ "$(id -u)" == "$TARGET_UID" ]]; then
    env HOME="$TARGET_HOME" "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" "$@"
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u "$TARGET_USER" -- env HOME="$TARGET_HOME" "$@"
  else
    return 1
  fi
}

user_systemctl() {
  run_as_target_user env XDG_RUNTIME_DIR="$RUNTIME_DIR" DBUS_SESSION_BUS_ADDRESS="$USER_DBUS" systemctl --user "$@"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

check_command() {
  local cmd="$1"
  local severity="${2:-fail}"
  local hint="${3:-}"
  if have_cmd "$cmd"; then
    add_result pass "cmd.$cmd" "found $(command -v "$cmd")"
  elif [[ "$severity" == "fail" ]]; then
    add_result fail "cmd.$cmd" "missing required command" "$hint"
  else
    add_result warn "cmd.$cmd" "missing optional command" "$hint"
  fi
}

human_df() {
  df -hP "$1" 2>/dev/null | awk 'NR == 2 {print "size="$2" used="$3" avail="$4" use="$5" mount="$6}'
}

check_disk_path() {
  local id="$1"
  local path="$2"
  local warn_gb="${3:-20}"
  local fail_gb="${4:-8}"
  if [[ ! -e "$path" ]]; then
    add_result skip "disk.$id" "$path does not exist"
    return
  fi
  local avail_kb detail
  avail_kb="$(df -Pk "$path" 2>/dev/null | awk 'NR == 2 {print $4}')"
  detail="$(human_df "$path")"
  if [[ -z "$avail_kb" ]]; then
    add_result warn "disk.$id" "unable to read free space" "$path"
    return
  fi
  local avail_gb=$((avail_kb / 1024 / 1024))
  if (( avail_gb < fail_gb )); then
    add_result fail "disk.$id" "low free space: ${avail_gb}G" "$detail"
  elif (( avail_gb < warn_gb )); then
    add_result warn "disk.$id" "limited free space: ${avail_gb}G" "$detail"
  else
    add_result pass "disk.$id" "free space: ${avail_gb}G" "$detail"
  fi
}

check_user_service() {
  local unit="$1"
  local id="$2"
  if ! have_cmd systemctl; then
    add_result skip "service.$id" "systemctl is unavailable"
    return
  fi
  if ! user_systemctl status >/dev/null 2>&1; then
    add_result warn "service.$id" "target user's systemd session bus is unavailable" "user=$TARGET_USER xdg_runtime_dir=$RUNTIME_DIR"
    return
  fi
  if ! user_systemctl cat "$unit" >/dev/null 2>&1; then
    add_result warn "service.$id" "$unit is not installed" "run ./setup_everything.sh or ./scripts/install_user_service.sh"
    return
  fi

  local active enabled
  active="$(user_systemctl is-active "$unit" 2>/dev/null || true)"
  enabled="$(user_systemctl is-enabled "$unit" 2>/dev/null || true)"
  if [[ "$REPAIR" -eq 1 && "$active" != "active" ]]; then
    user_systemctl start "$unit" >/dev/null 2>&1 || true
    sleep 1
    active="$(user_systemctl is-active "$unit" 2>/dev/null || true)"
  fi

  if [[ "$active" == "active" ]]; then
    add_result pass "service.$id" "$unit is active" "enabled=$enabled"
  else
    add_result warn "service.$id" "$unit is $active" "enabled=$enabled"
    if [[ "$INCLUDE_LOGS" -eq 1 ]]; then
      local logs
      logs="$(user_systemctl status "$unit" --no-pager 2>&1 | tail -20 || true)"
      add_result info "service.$id.logs" "recent status excerpt" "$logs"
    fi
  fi
}

check_host() {
  add_result info "host.user" "checking as $TARGET_USER" "uid=$TARGET_UID home=$TARGET_HOME"
  add_result info "host.project" "$PROJECT_ROOT"
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    add_result info "host.os" "${PRETTY_NAME:-${ID:-unknown}}"
  fi
  check_disk_path root / 20 8
  check_disk_path waydroid /var/lib/waydroid 20 8
}

check_commands() {
  check_command python3 fail "Install Python 3."
  check_command curl fail "Install curl."
  check_command adb fail "Install Android platform tools."
  check_command waydroid fail "Install Waydroid."
  check_command systemctl warn "systemd user services are the default runtime path."
  check_command gdbus warn "Needed for desktop Waydroid session bus checks."
  check_command git warn "Needed for installing Waydroid extras/default stores."
  check_command node warn "Needed for OpenClaw plugin syntax checks."
  check_command java warn "Needed to build the Android companion APK."
  check_command lzip warn "Needed by some Waydroid image extra installers."

  local backend="$WINDOW_BACKEND"
  case "$backend" in
    x11)
      check_command weston fail "X11 sessions need nested Weston for Waydroid UI."
      check_command xwininfo warn "Used to verify the nested Weston window."
      ;;
    wayland)
      add_result info "desktop.backend" "wayland backend requested"
      ;;
    auto)
      if [[ "${XDG_SESSION_TYPE:-}" == "x11" || ( -n "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ) ]]; then
        check_command weston fail "X11 sessions need nested Weston for Waydroid UI."
        check_command xwininfo warn "Used to verify the nested Weston window."
      else
        add_result info "desktop.backend" "auto backend will prefer Wayland when available"
      fi
      ;;
    *)
      add_result fail "desktop.backend" "invalid OPENCLAW_ANDROID_WINDOW_BACKEND=$backend"
      ;;
  esac
}

check_desktop_env() {
  local systemd_env session_type display wayland
  systemd_env="$(user_systemctl show-environment 2>/dev/null || true)"
  session_type="$(printf '%s\n' "$systemd_env" | awk -F= '$1 == "XDG_SESSION_TYPE" {print $2; exit}')"
  display="$(printf '%s\n' "$systemd_env" | awk -F= '$1 == "DISPLAY" {print $2; exit}')"
  wayland="$(printf '%s\n' "$systemd_env" | awk -F= '$1 == "WAYLAND_DISPLAY" {print $2; exit}')"
  if [[ "$(id -u)" == "$TARGET_UID" ]]; then
    session_type="${session_type:-${XDG_SESSION_TYPE:-unknown}}"
    display="${display:-${DISPLAY:-}}"
    wayland="${wayland:-${WAYLAND_DISPLAY:-}}"
  else
    session_type="${session_type:-unknown}"
  fi
  add_result info "desktop.session" "session_type=$session_type" "DISPLAY=${display:-unset} WAYLAND_DISPLAY=${wayland:-unset}"
  if [[ -S "$RUNTIME_DIR/bus" ]]; then
    add_result pass "desktop.dbus" "target user session bus is present" "$USER_DBUS"
  else
    add_result warn "desktop.dbus" "target user session bus is unavailable" "$USER_DBUS"
  fi
  if [[ "$WINDOW_BACKEND" == "wayland" || ( "$WINDOW_BACKEND" == "auto" && -n "$wayland" ) ]]; then
    local socket="$RUNTIME_DIR/${wayland:-wayland-0}"
    if [[ -S "$socket" ]]; then
      add_result pass "desktop.wayland_socket" "Wayland socket exists" "$socket"
    else
      add_result warn "desktop.wayland_socket" "Wayland socket not found" "$socket"
    fi
  elif [[ -n "$display" ]]; then
    add_result pass "desktop.display" "DISPLAY is set" "$display"
  else
    add_result warn "desktop.display" "no graphical display detected" "Waydroid UI will start after a desktop login."
  fi
}

check_waydroid() {
  if [[ -f /var/lib/waydroid/waydroid.cfg && -f /var/lib/waydroid/images/system.img && -f /var/lib/waydroid/images/vendor.img ]]; then
    add_result pass "waydroid.initialized" "Waydroid image files are present"
  else
    add_result fail "waydroid.initialized" "Waydroid is not initialized" "Run ./setup_everything.sh --init-waydroid or sudo waydroid init."
  fi

  if ! have_cmd waydroid; then
    return
  fi

  local status container session ip
  status="$(timeout 8s waydroid status 2>&1 || true)"
  container="$(waydroid_container_state || true)"
  session="$(waydroid_session_state || true)"
  ip="$(waydroid_ip_address || true)"

  if [[ -n "$status" ]]; then
    add_result info "waydroid.status" "waydroid status returned" "$status"
  else
    add_result warn "waydroid.status" "waydroid status returned no output"
  fi
  if [[ "$container" == "RUNNING" ]]; then
    add_result pass "waydroid.container" "container is running"
  else
    add_result fail "waydroid.container" "container is ${container:-unknown}" "Try ./doctor.sh --repair, ./setup_everything.sh --start-waydroid, or sudo ./scripts/restart_everything_sudo.sh."
  fi
  if [[ "$session" == "RUNNING" ]]; then
    add_result pass "waydroid.session" "session is running"
  else
    add_result warn "waydroid.session" "session is ${session:-unknown}" "The UI supervisor should start it from your desktop session."
  fi
  if [[ -n "$ip" ]]; then
    add_result pass "waydroid.ip" "$ip"
    if [[ -z "$ADB_SERIAL" ]]; then
      ADB_SERIAL="$ip:5555"
    fi
  else
    add_result warn "waydroid.ip" "no Waydroid IP address yet"
  fi
}

adb_state_for_serial() {
  local serial="$1"
  adb devices 2>/dev/null | awk -v serial="$serial" '$1 == serial {print $2; exit}'
}

resolve_adb_serial_from_devices() {
  adb devices 2>/dev/null | awk 'NR > 1 && $1 != "" {print $1; exit}'
}

adb_shell_value() {
  local serial="$1"
  shift
  timeout "${OPENCLAW_ANDROID_ADB_COMMAND_TIMEOUT:-12s}" adb -s "$serial" shell "$@" 2>/dev/null | tr -d '\r'
}

package_installed() {
  local serial="$1"
  local package="$2"
  adb_shell_value "$serial" cmd package path "$package" 2>/dev/null | grep -q "^package:"
}

check_android_package() {
  local id="$1"
  local serial="$2"
  local package="$3"
  local severity="${4:-warn}"
  local label="${5:-$package}"
  if package_installed "$serial" "$package"; then
    add_result pass "android.pkg.$id" "$label installed" "$package"
  elif [[ "$severity" == "fail" ]]; then
    add_result fail "android.pkg.$id" "$label is not installed" "$package"
  elif [[ "$severity" == "skip" ]]; then
    add_result skip "android.pkg.$id" "$label is not installed" "$package"
  else
    add_result warn "android.pkg.$id" "$label is not installed" "$package"
  fi
}

check_adb_and_android() {
  if ! have_cmd adb; then
    return
  fi

  if [[ -z "$ADB_SERIAL" ]]; then
    ADB_SERIAL="$(resolve_adb_serial_from_devices || true)"
  fi
  if [[ -z "$ADB_SERIAL" ]]; then
    add_result fail "adb.serial" "no ADB device found" "If Android shows an authorization prompt, check Always allow from this computer and tap Allow."
    return
  fi

  if [[ "$REPAIR" -eq 1 ]]; then
    timeout 10s adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
  fi

  local state
  state="$(adb_state_for_serial "$ADB_SERIAL" || true)"
  if [[ "$state" == "device" ]]; then
    add_result pass "adb.device" "$ADB_SERIAL is authorized"
  elif [[ "$state" == "unauthorized" ]]; then
    add_result fail "adb.device" "$ADB_SERIAL is unauthorized" "In Android, check Always allow from this computer and tap Allow."
    return
  elif [[ -n "$state" ]]; then
    add_result fail "adb.device" "$ADB_SERIAL state is $state"
    return
  else
    add_result fail "adb.device" "$ADB_SERIAL is not listed by adb devices"
    return
  fi

  local boot system_server current storage native_bridge native_version
  boot="$(adb_shell_value "$ADB_SERIAL" getprop sys.boot_completed | tail -n 1 || true)"
  system_server="$(adb_shell_value "$ADB_SERIAL" getprop init.svc.system_server | tail -n 1 || true)"
  if [[ "$system_server" != "running" ]]; then
    system_server_pid="$(adb_shell_value "$ADB_SERIAL" pidof system_server | tail -n 1 || true)"
    [[ -n "$system_server_pid" ]] && system_server="running"
  fi
  current="$(adb_shell_value "$ADB_SERIAL" dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp' | head -1 || true)"
  storage="$(adb_shell_value "$ADB_SERIAL" df -h /storage/emulated 2>/dev/null || true)"
  native_bridge="$(adb_shell_value "$ADB_SERIAL" getprop ro.dalvik.vm.native.bridge | tail -n 1 || true)"
  native_version="$(adb_shell_value "$ADB_SERIAL" getprop ro.ndk_translation.version | tail -n 1 || true)"

  [[ "$boot" == "1" ]] && add_result pass "android.boot" "boot completed" || add_result warn "android.boot" "boot_completed=${boot:-unset}"
  [[ "$system_server" == "running" ]] && add_result pass "android.system_server" "system_server running" || add_result warn "android.system_server" "system_server=${system_server:-unset}"
  [[ -n "$current" ]] && add_result pass "android.focus" "foreground window detected" "$current" || add_result warn "android.focus" "foreground window unavailable"
  if [[ "$storage" == *" 100% "* ]]; then
    add_result fail "android.storage" "Android shared storage is full" "$storage"
  elif [[ -n "$storage" ]]; then
    add_result pass "android.storage" "Android shared storage has free space" "$storage"
  else
    add_result warn "android.storage" "unable to read Android shared storage"
  fi

  check_android_package bridge "$ADB_SERIAL" ai.openclaw.androidbridge fail "Clawdroid bridge"
  check_android_package fdroid "$ADB_SERIAL" org.fdroid.fdroid warn "F-Droid"
  check_android_package aurora "$ADB_SERIAL" com.aurora.store warn "Aurora Store"
  check_android_package aptoide "$ADB_SERIAL" cm.aptoide.pt warn "Aptoide"
  check_android_package play_store "$ADB_SERIAL" com.android.vending skip "Google Play Store"
  check_android_package play_services "$ADB_SERIAL" com.google.android.gms skip "Google Play Services"

  if [[ -n "$native_bridge" && "$native_bridge" != "0" ]]; then
    add_result pass "android.arm_translation" "native bridge enabled" "ro.dalvik.vm.native.bridge=$native_bridge version=${native_version:-unknown}"
  else
    add_result warn "android.arm_translation" "ARM translation is not enabled" "Install with --arm-translation libndk if ARM-only apps are required."
  fi

  local accessibility_enabled accessibility_services bridge_service
  bridge_service="ai.openclaw.androidbridge/ai.openclaw.androidbridge.OpenClawAccessibilityService"
  accessibility_enabled="$(adb_shell_value "$ADB_SERIAL" settings get secure accessibility_enabled | tail -n 1 || true)"
  accessibility_services="$(adb_shell_value "$ADB_SERIAL" settings get secure enabled_accessibility_services | tail -n 1 || true)"
  if [[ "$accessibility_enabled" == "1" && "$accessibility_services" == *"ai.openclaw.androidbridge"* ]]; then
    add_result pass "android.accessibility" "Clawdroid accessibility service is enabled" "$accessibility_services"
  else
    add_result fail "android.accessibility" "Clawdroid accessibility service is not enabled" "Expected $bridge_service; open Android Accessibility settings and enable Clawdroid."
  fi

  local forward_line
  forward_line="$(adb -s "$ADB_SERIAL" forward --list 2>/dev/null | awk -v serial="$ADB_SERIAL" -v port="tcp:$BRIDGE_PORT" '$1 == serial && $2 == port {print; exit}' || true)"
  if [[ -n "$forward_line" ]]; then
    add_result pass "adb.bridge_forward" "bridge forward exists" "$forward_line"
  else
    add_result warn "adb.bridge_forward" "bridge forward is not currently registered" "The daemon normally creates this on demand."
  fi
}

json_get() {
  local expr="$1"
  local file="$2"
  python3 - "$expr" "$file" <<'PY'
import json
import sys

expr, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
cur = data
for part in expr.split("."):
    if not part:
        continue
    if isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
        break
if isinstance(cur, bool):
    print("true" if cur else "false")
elif cur is None:
    print("")
else:
    print(cur)
PY
}

check_daemon() {
  local health_file="$TMP_DIR/health.json"
  local status_file="$TMP_DIR/status.json"
  local code

  if [[ "$REPAIR" -eq 1 ]]; then
    user_systemctl start openclaw-android-waydroid.service >/dev/null 2>&1 || true
    sleep 1
  fi

  code="$(curl -sS -m 8 -o "$health_file" -w '%{http_code}' "$DAEMON_BASE_URL/healthz" 2>/dev/null || true)"
  if [[ "$code" == 2* ]]; then
    add_result pass "daemon.health" "daemon health endpoint is reachable" "$DAEMON_BASE_URL/healthz"
  else
    add_result fail "daemon.health" "daemon health endpoint is not reachable" "HTTP ${code:-unavailable} at $DAEMON_BASE_URL/healthz"
    return
  fi

  code="$(curl -sS -m 30 -o "$status_file" -w '%{http_code}' \
    -H 'content-type: application/json' \
    -d '{"action":"status"}' \
    "$DAEMON_BASE_URL/v1/agent/dispatch" 2>/dev/null || true)"
  if [[ "$code" != 2* ]]; then
    add_result fail "daemon.dispatch" "agent dispatch status failed" "HTTP ${code:-unavailable}"
    return
  fi

  local ok bridge_ok current_pkg waydroid_running waydroid_session bridge_error
  ok="$(json_get ok "$status_file" || true)"
  bridge_ok="$(json_get bridge.ok "$status_file" || true)"
  current_pkg="$(json_get current_app.package "$status_file" || true)"
  waydroid_running="$(json_get waydroid.running "$status_file" || true)"
  waydroid_session="$(json_get waydroid.session "$status_file" || true)"
  bridge_error="$(json_get bridge.error "$status_file" || true)"

  [[ "$ok" == "true" ]] && add_result pass "daemon.dispatch" "agent dispatch returned ok=true" || add_result fail "daemon.dispatch" "agent dispatch did not return ok=true"
  [[ "$bridge_ok" == "true" ]] && add_result pass "daemon.bridge" "accessibility bridge is reachable through daemon" || add_result fail "daemon.bridge" "accessibility bridge is not reachable through daemon" "$bridge_error"
  [[ "$waydroid_running" == "true" ]] && add_result pass "daemon.waydroid_running" "daemon sees Waydroid running" || add_result warn "daemon.waydroid_running" "daemon does not see Waydroid running"
  [[ "$waydroid_session" == "true" ]] && add_result pass "daemon.waydroid_session" "daemon sees Waydroid session" || add_result warn "daemon.waydroid_session" "daemon does not see Waydroid session"
  [[ -n "$current_pkg" ]] && add_result pass "daemon.current_app" "current app: $current_pkg" || add_result warn "daemon.current_app" "daemon could not determine current app"
}

check_supervisor() {
  local ctl="$PROJECT_ROOT/scripts/waydroid_supervisor_ctl.sh"
  if [[ ! -x "$ctl" ]]; then
    add_result skip "ui.supervisor" "supervisor control script is missing"
    return
  fi
  local output
  if [[ "$REPAIR" -eq 1 ]]; then
    run_as_target_user env XDG_RUNTIME_DIR="$RUNTIME_DIR" DBUS_SESSION_BUS_ADDRESS="$USER_DBUS" "$ctl" start >/dev/null 2>&1 || true
  fi
  if output="$(run_as_target_user env XDG_RUNTIME_DIR="$RUNTIME_DIR" DBUS_SESSION_BUS_ADDRESS="$USER_DBUS" "$ctl" status 2>&1)"; then
    add_result pass "ui.supervisor" "supervisor health file is present"
    local android_ready weston_socket waydroid_running last_error
    android_ready="$(printf '%s\n' "$output" | awk -F= '$1 == "android_ready" {print $2; exit}')"
    weston_socket="$(printf '%s\n' "$output" | awk -F= '$1 == "weston_socket_present" {print $2; exit}')"
    waydroid_running="$(printf '%s\n' "$output" | awk -F= '$1 == "waydroid_running" {print $2; exit}')"
    last_error="$(printf '%s\n' "$output" | awk -F= '$1 == "last_error" {print $2; exit}')"
    [[ "$android_ready" == "1" ]] && add_result pass "ui.android_ready" "supervisor reports Android ready" || add_result warn "ui.android_ready" "supervisor android_ready=${android_ready:-unset}" "$last_error"
    [[ "$waydroid_running" == "1" ]] && add_result pass "ui.waydroid_running" "supervisor sees Waydroid running" || add_result warn "ui.waydroid_running" "supervisor waydroid_running=${waydroid_running:-unset}"
    if [[ "$WINDOW_BACKEND" == "x11" || ( "$WINDOW_BACKEND" == "auto" && -n "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ) ]]; then
      [[ "$weston_socket" == "1" ]] && add_result pass "ui.weston_socket" "nested Weston socket is present" || add_result warn "ui.weston_socket" "nested Weston socket is missing"
    fi
  else
    add_result warn "ui.supervisor" "supervisor status is unavailable" "$output"
  fi
}

check_plugins() {
  local config_dir="$TARGET_HOME/.config/openclaw-android-waydroid"
  local llm_config="${OPENCLAW_ANDROID_LLM_CONFIG_PATH:-$config_dir/llm.json}"
  if [[ -d "$HERMES_HOME" ]]; then
    add_result pass "hermes.home" "Hermes home found" "$HERMES_HOME"
    if [[ -f "$HERMES_HOME/plugins/clawdroid/plugin.yaml" ]]; then
      add_result pass "hermes.plugin" "Clawdroid Hermes plugin is installed" "$HERMES_HOME/plugins/clawdroid"
    else
      add_result warn "hermes.plugin" "Clawdroid Hermes plugin is not installed" "Run ./scripts/install_hermes_plugin.sh --user $TARGET_USER"
    fi
    if [[ -f "$HERMES_HOME/skills/clawdroid/SKILL.md" ]]; then
      if grep -q "android_tars_target\\|\"action\": \"decide_next\"" "$HERMES_HOME/skills/clawdroid/SKILL.md" \
        && grep -qi "do not .*generic.*vision_analyze" "$HERMES_HOME/skills/clawdroid/SKILL.md"; then
        add_result pass "hermes.clawdroid_skill" "Clawdroid skill routes Android vision through daemon/TARS"
      else
        add_result warn "hermes.clawdroid_skill" "Clawdroid skill may be stale" "Run ./scripts/install_hermes_plugin.sh --user $TARGET_USER"
      fi
    else
      add_result warn "hermes.clawdroid_skill" "Clawdroid flat skill is not installed" "Run ./scripts/install_hermes_plugin.sh --user $TARGET_USER"
    fi
  else
    add_result skip "hermes.home" "Hermes home not found" "$HERMES_HOME"
  fi
  if have_cmd hermes; then
    add_result pass "hermes.cli" "Hermes CLI found" "$(command -v hermes)"
  else
    add_result skip "hermes.cli" "Hermes CLI is not on PATH"
  fi

  if [[ -d "$OPENCLAW_HOME" ]]; then
    add_result pass "openclaw.home" "OpenClaw home found" "$OPENCLAW_HOME"
  else
    add_result skip "openclaw.home" "OpenClaw home not found" "$OPENCLAW_HOME"
  fi
  if [[ -f "$PROJECT_ROOT/openclaw-plugin/openclaw.plugin.json" ]]; then
    add_result pass "openclaw.plugin_source" "OpenClaw plugin source manifest exists"
  else
    add_result fail "openclaw.plugin_source" "OpenClaw plugin source manifest missing"
  fi

  if [[ -f "$llm_config" ]]; then
    if grep -q "openrouter" "$llm_config" && grep -q "bytedance/ui-tars-1.5-7b" "$llm_config"; then
      add_result pass "daemon.llm_config" "daemon LLM config points at OpenRouter UI-TARS" "$llm_config"
    else
      add_result warn "daemon.llm_config" "daemon LLM config exists but does not look like the OpenRouter UI-TARS default" "$llm_config"
    fi
  else
    add_result warn "daemon.llm_config" "daemon LLM config is missing" "Run ./scripts/install_user_service.sh as $TARGET_USER"
  fi
  if [[ -f "$HERMES_HOME/.env" ]] && grep -Eq '^(OPENROUTER_API_KEY|OPENCLAW_ANDROID_OPENROUTER_API_KEY)=' "$HERMES_HOME/.env"; then
    add_result pass "daemon.llm_env" "OpenRouter key env is available through Hermes env file" "$HERMES_HOME/.env"
  elif [[ -f "$config_dir/env" ]] && grep -Eq '^(OPENROUTER_API_KEY|OPENCLAW_ANDROID_OPENROUTER_API_KEY)=' "$config_dir/env"; then
    add_result pass "daemon.llm_env" "OpenRouter key env is available through Clawdroid env file" "$config_dir/env"
  else
    add_result warn "daemon.llm_env" "OpenRouter key env was not found for daemon service" "$HERMES_HOME/.env or $config_dir/env"
  fi
}

emit_json() {
  python3 - "$RESULTS_FILE" "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$INFO_COUNT" "$SKIP_COUNT" <<'PY'
import json
import sys

path, pass_count, warn_count, fail_count, info_count, skip_count = sys.argv[1:]
checks = []
with open(path, encoding="utf-8") as handle:
    for line in handle:
        status, check_id, summary, detail = line.rstrip("\n").split("\t", 3)
        checks.append({"status": status, "id": check_id, "summary": summary, "detail": detail})
payload = {
    "ok": int(fail_count) == 0,
    "summary": {
        "pass": int(pass_count),
        "warn": int(warn_count),
        "fail": int(fail_count),
        "info": int(info_count),
        "skip": int(skip_count),
    },
    "checks": checks,
}
print(json.dumps(payload, indent=2, sort_keys=True))
PY
}

print_summary() {
  if [[ "$JSON_OUTPUT" -eq 1 ]]; then
    emit_json
    return
  fi
  printf '\nSummary: %s pass, %s warn, %s fail, %s info, %s skip\n' \
    "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$INFO_COUNT" "$SKIP_COUNT"
  if [[ "$FAIL_COUNT" -gt 0 ]]; then
    cat <<EOF

Common next steps:
  ./setup_everything.sh --interactive
  ./doctor.sh --repair --user "$TARGET_USER"
  ./scripts/smoke_test_install.sh --layer auto --wait-timeout 360
EOF
  elif [[ "$WARN_COUNT" -gt 0 ]]; then
    cat <<EOF

Warnings may be acceptable when optional pieces are intentionally absent
(for example Google Play, OpenClaw, or the Hermes CLI on PATH).
EOF
  fi
}

if [[ "$JSON_OUTPUT" -eq 0 ]]; then
  printf 'Clawdroid doctor\n'
  printf '  project=%s\n' "$PROJECT_ROOT"
  printf '  user=%s\n' "$TARGET_USER"
  printf '  daemon=%s\n\n' "$DAEMON_BASE_URL"
fi

check_host
check_commands
check_desktop_env
check_user_service openclaw-android-waydroid.service daemon
check_user_service openclaw-android-waydroid-ui.service ui
check_waydroid
check_adb_and_android
check_supervisor
check_daemon
check_plugins
print_summary

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
