#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

require_cmd systemctl

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
WINDOW_BACKEND="${OPENCLAW_ANDROID_WINDOW_BACKEND:-auto}"

wayland_display_path() {
  local display="${1:-}"
  [[ -n "$display" ]] || return 1
  if [[ "$display" == /* ]]; then
    printf '%s\n' "$display"
  else
    printf '%s/%s\n' "$XDG_RUNTIME_DIR" "$display"
  fi
}

has_wayland_display() {
  local path
  path="$(wayland_display_path "${WAYLAND_DISPLAY:-}" 2>/dev/null || true)"
  [[ -n "$path" && -S "$path" ]]
}

has_x11_display() {
  [[ -n "${DISPLAY:-}" ]]
}

case "$WINDOW_BACKEND" in
  auto|x11|wayland) ;;
  *) fatal "Invalid OPENCLAW_ANDROID_WINDOW_BACKEND: $WINDOW_BACKEND" ;;
esac

[[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] || fatal "DBUS_SESSION_BUS_ADDRESS is not set; run this from the real desktop session"

case "$WINDOW_BACKEND" in
  x11)
    has_x11_display || fatal "DISPLAY is not set; cannot import an X11 graphical session"
    ;;
  wayland)
    has_wayland_display || fatal "WAYLAND_DISPLAY is not set or is not a socket; cannot import a Wayland graphical session"
    ;;
  auto)
    has_wayland_display || has_x11_display || fatal "Neither WAYLAND_DISPLAY nor DISPLAY is usable; run this from the real desktop session"
    ;;
esac

export OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND"
env_names=(
  DBUS_SESSION_BUS_ADDRESS
  XDG_RUNTIME_DIR
  OPENCLAW_ANDROID_WINDOW_BACKEND
)
[[ -n "${DISPLAY:-}" ]] && env_names+=(DISPLAY)
[[ -n "${XAUTHORITY:-}" ]] && env_names+=(XAUTHORITY)
[[ -n "${WAYLAND_DISPLAY:-}" ]] && env_names+=(WAYLAND_DISPLAY)
[[ -n "${XDG_SESSION_TYPE:-}" ]] && env_names+=(XDG_SESSION_TYPE)
[[ -n "${XDG_CURRENT_DESKTOP:-}" ]] && env_names+=(XDG_CURRENT_DESKTOP)
[[ -n "${OPENCLAW_ANDROID_WESTON_SOCKET:-}" ]] && env_names+=(OPENCLAW_ANDROID_WESTON_SOCKET)

systemctl --user import-environment "${env_names[@]}"
exec systemctl --user restart openclaw-android-waydroid-ui.service
