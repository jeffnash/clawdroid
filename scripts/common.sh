#!/usr/bin/env bash
set -Eeuo pipefail

log_step() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

fatal() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fatal "Missing required command: $1"
}

require_option_value() {
  local option="$1"
  local value="${2-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    fatal "$option requires a value"
  fi
}

adb_quick() {
  timeout "${OPENCLAW_ANDROID_ADB_COMMAND_TIMEOUT:-20s}" adb "$@"
}

adb_install_cmd() {
  timeout "${OPENCLAW_ANDROID_ADB_INSTALL_TIMEOUT:-180s}" adb "$@"
}

curl_fetch() {
  curl -fsSL \
    --connect-timeout "${OPENCLAW_ANDROID_CURL_CONNECT_TIMEOUT:-20}" \
    --max-time "${OPENCLAW_ANDROID_CURL_MAX_TIME:-300}" \
    "$@"
}

detect_distro() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    printf '%s' "${ID:-unknown}"
  else
    printf 'unknown'
  fi
}

sudo_maybe() {
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

sudo_noninteractive_or_plain() {
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true >/dev/null 2>&1; then
      sudo -n "$@"
    else
      "$@"
    fi
  else
    "$@"
  fi
}

openclaw_android_runtime_dir() {
  local runtime_root="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  printf '%s\n' "${OPENCLAW_ANDROID_STATE_DIR:-$runtime_root/openclaw-android-waydroid}"
}

openclaw_android_state_home() {
  local state_root="${XDG_STATE_HOME:-$HOME/.local/state}"
  printf '%s\n' "${OPENCLAW_ANDROID_PERSISTENT_STATE_DIR:-$state_root/openclaw-android-waydroid}"
}

openclaw_android_log_dir() {
  local state_home
  state_home="$(openclaw_android_state_home)"
  printf '%s\n' "${OPENCLAW_ANDROID_LOG_DIR:-$state_home/logs}"
}

waydroid_status_field() {
  local key="$1"
  timeout 5s waydroid status 2>/dev/null | awk -F '\t' -v key="$key" '$1 == key {print $2; exit}' | tr -d '\r'
}

waydroid_container_state() {
  waydroid_status_field "Container:"
}

waydroid_session_state() {
  waydroid_status_field "Session:"
}

waydroid_container_running() {
  [[ "$(waydroid_container_state || true)" == "RUNNING" ]]
}

waydroid_session_running() {
  [[ "$(waydroid_session_state || true)" == "RUNNING" ]]
}

waydroid_ip_address() {
  waydroid_status_field "IP address:" | cut -d/ -f1
}

ensure_waydroid_network_rules() {
  local subnet="${OPENCLAW_ANDROID_WAYDROID_SUBNET:-192.168.240.0/24}"
  local bridge="${OPENCLAW_ANDROID_WAYDROID_BRIDGE:-waydroid0}"

  if [[ "$(id -u)" != "0" ]]; then
    warn "Skipping Waydroid network rule setup because root privileges are required"
    return 0
  fi
  if ! command -v iptables >/dev/null 2>&1; then
    warn "Skipping Waydroid network rule setup because iptables is unavailable"
    return 0
  fi

  sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 ||
    warn "Failed to enable IPv4 forwarding for Waydroid"

  if ! iptables -t nat -C POSTROUTING -s "$subnet" ! -o "$bridge" -j MASQUERADE >/dev/null 2>&1; then
    iptables -t nat -A POSTROUTING -s "$subnet" ! -o "$bridge" -j MASQUERADE ||
      warn "Failed to add Waydroid NAT masquerade rule"
  fi

  if ! iptables -C FORWARD -i "$bridge" -j ACCEPT >/dev/null 2>&1; then
    iptables -A FORWARD -i "$bridge" -j ACCEPT ||
      warn "Failed to add Waydroid outbound forwarding rule"
  fi

  if ! iptables -C FORWARD -o "$bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT >/dev/null 2>&1; then
    iptables -A FORWARD -o "$bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT ||
      warn "Failed to add Waydroid return forwarding rule"
  fi
}

restart_openclaw_gateway_if_running() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  if systemctl --user is-active --quiet openclaw-gateway.service; then
    log_step "Restarting openclaw-gateway.service to reload the Android plugin/runtime"
    systemctl --user restart openclaw-gateway.service || warn "Failed to restart openclaw-gateway.service"
  fi
}
