#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

TARGET_USER="${SUDO_USER:-$(id -un)}"
PURGE_WAYDROID=0
PURGE_REPO_CACHE=0
KEEP_OPENCLAW_CONFIG=0
KEEP_HERMES_PLUGIN=0
KEEP_OPENCLAW_PLUGIN=0
KEEP_DAEMON_VENV=0
HERMES_PLUGIN_NAME="${CLAWDROID_HERMES_PLUGIN_NAME:-clawdroid}"
HERMES_SKILL_NAME="${CLAWDROID_HERMES_SKILL_NAME:-clawdroid}"

usage() {
  cat <<'EOF'
Usage: ./scripts/uninstall_everything.sh [options]

Removes the local Clawdroid/OpenClaw Android install artifacts while keeping
the repository checkout itself.

Options:
  --user NAME             Target desktop user when running as root
  --purge-waydroid        Also remove Waydroid package, images, and user state
  --purge-repo-cache      Remove repo caches/build outputs used by setup
  --keep-openclaw-config  Leave ~/.openclaw/openclaw.json unchanged
  --keep-hermes-plugin    Leave ~/.hermes/plugins/clawdroid and ~/.hermes/skills/clawdroid installed
  --keep-openclaw-plugin  Leave ~/.openclaw/extensions/android-waydroid installed
  --keep-daemon-venv      Leave python-daemon/.venv in place
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      [[ $# -ge 2 ]] || fatal "--user requires a username"
      TARGET_USER="$2"
      shift
      ;;
    --purge-waydroid) PURGE_WAYDROID=1 ;;
    --purge-repo-cache) PURGE_REPO_CACHE=1 ;;
    --keep-openclaw-config) KEEP_OPENCLAW_CONFIG=1 ;;
    --keep-hermes-plugin) KEEP_HERMES_PLUGIN=1 ;;
    --keep-openclaw-plugin) KEEP_OPENCLAW_PLUGIN=1 ;;
    --keep-daemon-venv) KEEP_DAEMON_VENV=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if ! id "$TARGET_USER" >/dev/null 2>&1; then
  fatal "Unknown target user: $TARGET_USER"
fi

TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" && -d "$TARGET_HOME" ]] || fatal "Unable to determine home directory for $TARGET_USER"
USER_RUNTIME_DIR="/run/user/$TARGET_UID"
USER_BUS="unix:path=$USER_RUNTIME_DIR/bus"

run_user() {
  local env_args=(
    env
    "HOME=$TARGET_HOME"
    "XDG_RUNTIME_DIR=$USER_RUNTIME_DIR"
    "DBUS_SESSION_BUS_ADDRESS=$USER_BUS"
  )
  if [[ "$(id -u)" == "$TARGET_UID" ]]; then
    "${env_args[@]}" "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$TARGET_USER" "${env_args[@]}" "$@"
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u "$TARGET_USER" -- "${env_args[@]}" "$@"
  else
    fatal "Need sudo or runuser to execute commands as $TARGET_USER"
  fi
}

run_root() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  else
    sudo_maybe "$@"
  fi
}

user_systemctl() {
  [[ -S "$USER_RUNTIME_DIR/bus" ]] || return 1
  run_user systemctl --user "$@"
}

remove_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    rm -rf "$path"
  fi
}

remove_path_best_effort() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if rm -rf "$path" 2>/dev/null; then
    return 0
  fi
  run_root rm -rf "$path"
}

append_unique_path() {
  local value="$1"
  [[ -n "$value" ]] || return 0
  local existing
  for existing in "${HERMES_HOMES[@]}"; do
    [[ "$existing" == "$value" ]] && return 0
  done
  HERMES_HOMES+=("$value")
}

system_hermes_home() {
  local hermes_home
  command -v systemctl >/dev/null 2>&1 || return 0
  hermes_home="$(systemctl show -p Environment --value hermes-gateway.service 2>/dev/null \
    | tr ' ' '\n' \
    | sed -n 's/^HERMES_HOME=//p' \
    | head -n 1 || true)"
  printf '%s' "$hermes_home"
}

restart_active_hermes_gateways() {
  [[ $KEEP_HERMES_PLUGIN -eq 0 ]] || return 0
  log_step "Reloading active Hermes gateway services"
  if user_systemctl is-active --quiet hermes-gateway.service; then
    user_systemctl restart hermes-gateway.service || warn "Failed to restart user hermes-gateway.service"
  fi
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet hermes-gateway.service; then
    run_root systemctl restart hermes-gateway.service || warn "Failed to restart system hermes-gateway.service"
  fi
}

stop_owned_weston() {
  local pid_file="$USER_RUNTIME_DIR/openclaw-android-waydroid/weston.pid"
  local pid owner cmdline

  [[ -f "$pid_file" ]] || return 0
  pid="$(tr -dc '0-9' <"$pid_file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  [[ -d "/proc/$pid" ]] || return 0

  owner="$(stat -c '%u' "/proc/$pid" 2>/dev/null || true)"
  if [[ "$owner" != "$TARGET_UID" ]]; then
    warn "Skipping Weston pid $pid from $pid_file because it is not owned by $TARGET_USER"
    return 0
  fi

  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  if [[ "$cmdline" != *"weston"* || "$cmdline" != *"--socket="* ]]; then
    warn "Skipping stale Weston pid $pid from $pid_file because it does not look like an OpenClaw Weston process"
    return 0
  fi

  kill "$pid" >/dev/null 2>&1 || true
  sleep 1
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill -KILL "$pid" >/dev/null 2>&1 || true
  fi
}

stop_user_services() {
  log_step "Stopping Clawdroid user services for $TARGET_USER"
  if user_systemctl stop openclaw-android-waydroid.service openclaw-android-waydroid-ui.service; then
    :
  else
    warn "No user systemd bus available or user services were not running"
  fi
  user_systemctl disable openclaw-android-waydroid.service openclaw-android-waydroid-ui.service >/dev/null 2>&1 || true
  user_systemctl reset-failed openclaw-android-waydroid.service openclaw-android-waydroid-ui.service >/dev/null 2>&1 || true

  pkill -u "$TARGET_UID" -f "openclaw_android_daemon.main" >/dev/null 2>&1 || true
  pkill -u "$TARGET_UID" -f "$PROJECT_ROOT/scripts/run_waydroid_ui_supervisor.sh" >/dev/null 2>&1 || true
  stop_owned_weston
}

remove_user_install_artifacts() {
  log_step "Removing Clawdroid service, autostart, plugin, and runtime artifacts"
  remove_path "$TARGET_HOME/.config/systemd/user/openclaw-android-waydroid.service"
  remove_path "$TARGET_HOME/.config/systemd/user/openclaw-android-waydroid-ui.service"
  remove_path "$TARGET_HOME/.config/systemd/user/default.target.wants/openclaw-android-waydroid.service"
  remove_path "$TARGET_HOME/.config/systemd/user/graphical-session.target.wants/openclaw-android-waydroid-ui.service"
  remove_path "$TARGET_HOME/.config/autostart/openclaw-android-waydroid-ui.desktop"
  user_systemctl daemon-reload >/dev/null 2>&1 || true

  if [[ $KEEP_OPENCLAW_PLUGIN -eq 0 ]]; then
    remove_path "$TARGET_HOME/.openclaw/extensions/android-waydroid"
  fi
  if [[ $KEEP_HERMES_PLUGIN -eq 0 ]]; then
    local HERMES_HOMES=()
    append_unique_path "$TARGET_HOME/.hermes"
    append_unique_path "${HERMES_HOME:-}"
    append_unique_path "$(system_hermes_home)"
    if [[ "$(id -u)" == "0" ]]; then
      append_unique_path "$(getent passwd root | cut -d: -f6)/.hermes"
    fi

    local hermes_home
    for hermes_home in "${HERMES_HOMES[@]}"; do
      remove_path_best_effort "$hermes_home/plugins/$HERMES_PLUGIN_NAME"
      remove_path_best_effort "$hermes_home/skills/$HERMES_SKILL_NAME"
    done
  fi
  remove_path "$TARGET_HOME/.local/state/openclaw-android-waydroid"
  remove_path "$TARGET_HOME/.cache/openclaw-android-waydroid"
  remove_path "$USER_RUNTIME_DIR/openclaw-android-waydroid"

  if [[ $KEEP_DAEMON_VENV -eq 0 ]]; then
    remove_path "$PROJECT_ROOT/python-daemon/.venv"
  fi
}

remove_openclaw_config() {
  [[ $KEEP_OPENCLAW_CONFIG -eq 0 ]] || return 0
  log_step "Removing android-waydroid entries from OpenClaw config"
  run_user python3 - <<'PY'
from __future__ import annotations

import json
from pathlib import Path

path = Path.home() / ".openclaw" / "openclaw.json"
if not path.exists():
    raise SystemExit(0)
data = json.loads(path.read_text(encoding="utf-8"))
data.get("plugins", {}).get("entries", {}).pop("android-waydroid", None)
tools = data.get("tools", {})
allow = tools.get("allow")
if isinstance(allow, list):
    tools["allow"] = [item for item in allow if item not in {"android", "android_admin"}]
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

purge_repo_cache() {
  [[ $PURGE_REPO_CACHE -eq 1 ]] || return 0
  log_step "Removing repo caches and build outputs"
  remove_path "$PROJECT_ROOT/.cache/android-commandlinetools.zip"
  remove_path "$PROJECT_ROOT/.cache/gradle-8.7"
  remove_path "$PROJECT_ROOT/.cache/gradle-8.7-bin.zip"
  remove_path "$PROJECT_ROOT/.cache/default-stores"
  remove_path "$PROJECT_ROOT/.cache/waydroid_script"
  remove_path "$PROJECT_ROOT/android-companion/.gradle"
  remove_path "$PROJECT_ROOT/android-companion/build"
  remove_path "$PROJECT_ROOT/android-companion/app/build"
}

stop_waydroid() {
  log_step "Stopping Waydroid session and container"
  run_user waydroid session stop >/dev/null 2>&1 || true
  timeout 30s waydroid session stop >/dev/null 2>&1 || true
  run_root timeout 30s waydroid container stop >/dev/null 2>&1 || true
  if command -v systemctl >/dev/null 2>&1; then
    run_root systemctl stop waydroid-container.service >/dev/null 2>&1 || true
    run_root systemctl disable waydroid-container.service >/dev/null 2>&1 || true
  fi
  pkill -f "waydroid session start" >/dev/null 2>&1 || true
  pkill -f "waydroid container start" >/dev/null 2>&1 || true
}

purge_waydroid() {
  [[ $PURGE_WAYDROID -eq 1 ]] || return 0
  stop_waydroid
  log_step "Purging Waydroid package and state"
  local distro
  distro="$(detect_distro)"
  case "$distro" in
    ubuntu|debian|linuxmint|pop|zorin)
      if command -v apt-get >/dev/null 2>&1; then
        run_root apt-get purge -y waydroid || true
        run_root apt-get autoremove -y || true
      fi
      ;;
    arch|manjaro|endeavouros)
      if command -v pacman >/dev/null 2>&1; then
        run_root pacman -Rns --noconfirm waydroid || true
      fi
      ;;
    fedora)
      if command -v dnf >/dev/null 2>&1; then
        run_root dnf remove -y waydroid || true
      fi
      ;;
    opensuse*|opensuse-tumbleweed)
      if command -v zypper >/dev/null 2>&1; then
        run_root zypper remove -y waydroid || true
      fi
      ;;
    *)
      warn "Unsupported distro for package purge: $distro; removing state only"
      ;;
  esac

  run_root rm -rf /var/lib/waydroid /etc/waydroid /var/cache/waydroid
  remove_path "$TARGET_HOME/.local/share/waydroid"
  remove_path "$TARGET_HOME/.config/waydroid"
  remove_path "$TARGET_HOME/.cache/waydroid"
  run_root ip link delete waydroid0 >/dev/null 2>&1 || true
}

stop_user_services
remove_user_install_artifacts
remove_openclaw_config
purge_repo_cache
purge_waydroid

restart_active_hermes_gateways

log_step "Uninstall complete"
cat <<EOF
Removed local Clawdroid/OpenClaw Android install artifacts for $TARGET_USER.
Waydroid purge: $([[ $PURGE_WAYDROID -eq 1 ]] && printf yes || printf no)

To reinstall from this checkout:
  ./setup_everything.sh --install-system-deps --install-hermes-plugin --init-waydroid --enable-admin-tool
EOF
