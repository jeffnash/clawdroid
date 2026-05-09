#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

DAEMON_BASE_URL="${OPENCLAW_ANDROID_DAEMON_BASE_URL:-http://127.0.0.1:48765}"
LAYER="auto"
TAP_X="${OPENCLAW_ANDROID_SMOKE_TAP_X:-24}"
TAP_Y="${OPENCLAW_ANDROID_SMOKE_TAP_Y:-24}"
SKIP_VISIBLE_ACTION=0
OPENCLAW_HOME_OVERRIDE="${OPENCLAW_HOME:-$HOME/.openclaw}"
HERMES_HOME_OVERRIDE="${HERMES_HOME:-}"
HERMES_USER=""
HERMES_SYSTEM=0
WAIT_TIMEOUT="${OPENCLAW_ANDROID_SMOKE_WAIT_TIMEOUT:-240}"

usage() {
  cat <<'EOF'
Usage: ./scripts/smoke_test_install.sh [options]

Runs a post-install smoke test through the same daemon dispatch endpoint used by
the OpenClaw and Hermes plugins. In auto mode, Hermes/OpenClaw CLI checks are
attempted when their commands are installed.

Options:
  --daemon-base-url URL   Daemon URL (default: http://127.0.0.1:48765)
  --layer NAME            auto, daemon, hermes, or openclaw (default: auto)
  --tap X,Y               Coordinate for the visible tap check (default: 24,24)
  --skip-visible-action   Do not perform the visible tap
  --openclaw-home PATH    OpenClaw home/profile to use for CLI checks
  --hermes-home PATH      Hermes home/profile to use for CLI checks
  --hermes-user NAME      Run Hermes smoke check as this user
  --hermes-system         Run Hermes smoke check as root/system Hermes
  --wait-timeout SECONDS  Wait for daemon/ADB/bridge readiness (default: 240)
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --daemon-base-url) require_option_value "$1" "${2-}"; DAEMON_BASE_URL="$2"; shift ;;
    --layer) require_option_value "$1" "${2-}"; LAYER="$2"; shift ;;
    --tap)
      require_option_value "$1" "${2-}"
      if [[ "$2" == *,* ]]; then
        TAP_X="${2%,*}"
        TAP_Y="${2#*,}"
      else
        require_option_value "$1" "${3-}"
        TAP_X="$2"
        TAP_Y="$3"
        shift
      fi
      shift
      ;;
    --skip-visible-action) SKIP_VISIBLE_ACTION=1 ;;
    --openclaw-home) require_option_value "$1" "${2-}"; OPENCLAW_HOME_OVERRIDE="$2"; shift ;;
    --hermes-home) require_option_value "$1" "${2-}"; HERMES_HOME_OVERRIDE="$2"; shift ;;
    --hermes-user) require_option_value "$1" "${2-}"; HERMES_USER="$2"; shift ;;
    --hermes-system) HERMES_SYSTEM=1 ;;
    --wait-timeout) require_option_value "$1" "${2-}"; WAIT_TIMEOUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

case "$LAYER" in
  auto|daemon|hermes|openclaw) ;;
  *) fatal "Invalid --layer value: $LAYER (expected: auto, daemon, hermes, or openclaw)" ;;
esac

if ! [[ "$TAP_X" =~ ^[0-9]+$ && "$TAP_Y" =~ ^[0-9]+$ ]]; then
  fatal "--tap coordinates must be non-negative integers"
fi
if ! [[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]]; then
  fatal "--wait-timeout must be a non-negative integer"
fi

require_cmd curl
require_cmd python3

DAEMON_BASE_URL="$(printf '%s' "$DAEMON_BASE_URL" | sed 's:/*$::')"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

http_get() {
  local path="$1"
  local output="$2"
  local status
  status="$(curl -sS -m 30 -o "$output" -w '%{http_code}' "$DAEMON_BASE_URL$path")" || {
    cat "$output" 2>/dev/null || true
    fatal "GET $path failed"
  }
  if [[ "$status" != 2* ]]; then
    cat "$output" >&2 || true
    fatal "GET $path returned HTTP $status"
  fi
}

post_agent() {
  local payload="$1"
  local output="$2"
  local status
  status="$(curl -sS -m 60 -o "$output" -w '%{http_code}' \
    -H 'content-type: application/json' \
    -d "$payload" \
    "$DAEMON_BASE_URL/v1/agent/dispatch")" || {
    cat "$output" 2>/dev/null || true
    fatal "POST /v1/agent/dispatch failed"
  }
  if [[ "$status" != 2* ]]; then
    cat "$output" >&2 || true
    fatal "POST /v1/agent/dispatch returned HTTP $status"
  fi
}

assert_ok_json() {
  local file="$1"
  local label="$2"
  python3 - "$file" "$label" <<'PY'
import json
import sys

path, label = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("ok") is not True:
    detail = data.get("error") or data.get("detail") or data
    raise SystemExit(f"{label} did not return ok=true: {detail}")
PY
}

summarize_status() {
  local file="$1"
  python3 - "$file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
current = data.get("current_app") or {}
bridge = data.get("bridge") or {}
waydroid = data.get("waydroid") or {}
if not isinstance(current, dict):
    current = {}
if not isinstance(bridge, dict):
    bridge = {}
if not isinstance(waydroid, dict):
    waydroid = {}
print(f"  current_app={current.get('package') or '(unknown)'}")
print(f"  waydroid_session={waydroid.get('session') or waydroid.get('session_state') or '(unknown)'}")
print(f"  bridge_ok={bridge.get('ok')}")
PY
}

status_is_ready() {
  local file="$1"
  python3 - "$file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
waydroid = data.get("waydroid") or {}
bridge = data.get("bridge") or {}
current = data.get("current_app") or {}
if not isinstance(waydroid, dict):
    waydroid = {}
if not isinstance(bridge, dict):
    bridge = {}
if not isinstance(current, dict):
    current = {}
ready = (
    data.get("ok") is True
    and waydroid.get("running") is True
    and waydroid.get("session") is True
    and bridge.get("ok") is True
    and bool(current.get("package"))
)
if ready:
    raise SystemExit(0)
parts = [
    f"ok={data.get('ok')}",
    f"waydroid_running={waydroid.get('running')}",
    f"waydroid_session={waydroid.get('session')}",
    f"bridge_ok={bridge.get('ok')}",
    f"current_app={current.get('package')}",
]
if bridge.get("error"):
    parts.append(f"bridge_error={bridge.get('error')}")
print("; ".join(parts))
raise SystemExit(1)
PY
}

wait_for_daemon_ready() {
  local status_file="$1"
  local health_file="$2"
  local deadline=$((SECONDS + WAIT_TIMEOUT))
  local status http_code last_status
  while (( SECONDS <= deadline )); do
    if curl -sS -m 10 -o "$health_file" "$DAEMON_BASE_URL/healthz" >/dev/null 2>&1; then
      status="$(curl -sS -m 20 -o "$status_file" -w '%{http_code}' \
        -H 'content-type: application/json' \
        -d '{"action":"status"}' \
        "$DAEMON_BASE_URL/v1/agent/dispatch" 2>/dev/null || true)"
      if [[ "$status" == 2* ]] && last_status="$(status_is_ready "$status_file" 2>&1)"; then
        return 0
      fi
      if [[ "$status" == 2* ]]; then
        last_status="$(status_is_ready "$status_file" 2>&1 || true)"
      else
        last_status="HTTP ${status:-unavailable}"
      fi
    else
      last_status="healthz unavailable"
    fi
    sleep 2
  done

  if [[ -s "$status_file" ]]; then
    cat "$status_file" >&2 || true
  fi
  fatal "Daemon did not become Android-ready within ${WAIT_TIMEOUT}s: ${last_status:-unknown status}"
}

run_hermes() {
  if ! command -v hermes >/dev/null 2>&1; then
    [[ "$LAYER" == "hermes" ]] && fatal "Hermes CLI is not installed"
    warn "Hermes CLI is not installed; skipping Hermes layer smoke check"
    return 0
  fi

  local cmd=(hermes -t clawdroid -z "Check Android status with the android tool")
  if command -v timeout >/dev/null 2>&1; then
    cmd=(timeout 180s "${cmd[@]}")
  fi

  log_step "Hermes layer smoke check"
  if [[ -n "$HERMES_USER" ]]; then
    local hermes_home user_home
    user_home="$(getent passwd "$HERMES_USER" | cut -d: -f6)"
    [[ -n "$user_home" ]] || fatal "Unknown Hermes user: $HERMES_USER"
    hermes_home="${HERMES_HOME_OVERRIDE:-$user_home/.hermes}"
    if [[ "$(id -un)" == "$HERMES_USER" ]]; then
      env HOME="$user_home" HERMES_HOME="$hermes_home" "${cmd[@]}"
    elif command -v sudo >/dev/null 2>&1; then
      sudo -u "$HERMES_USER" env HOME="$user_home" HERMES_HOME="$hermes_home" "${cmd[@]}"
    elif command -v runuser >/dev/null 2>&1; then
      runuser -u "$HERMES_USER" -- env HOME="$user_home" HERMES_HOME="$hermes_home" "${cmd[@]}"
    else
      fatal "Need sudo or runuser to run Hermes as $HERMES_USER"
    fi
  elif [[ $HERMES_SYSTEM -eq 1 ]]; then
    env HOME=/root HERMES_HOME="${HERMES_HOME_OVERRIDE:-/root/.hermes}" "${cmd[@]}"
  elif [[ -n "$HERMES_HOME_OVERRIDE" ]]; then
    env HERMES_HOME="$HERMES_HOME_OVERRIDE" "${cmd[@]}"
  else
    "${cmd[@]}"
  fi
}

run_openclaw_cli_check() {
  if ! command -v openclaw >/dev/null 2>&1; then
    [[ "$LAYER" == "openclaw" ]] && fatal "OpenClaw CLI is not installed"
    warn "OpenClaw CLI is not installed; skipping OpenClaw CLI smoke check"
    return 0
  fi

  log_step "OpenClaw CLI plugin/config smoke check"
  OPENCLAW_HOME="$OPENCLAW_HOME_OVERRIDE" openclaw plugins inspect android-waydroid --json >/dev/null
  OPENCLAW_HOME="$OPENCLAW_HOME_OVERRIDE" openclaw config validate >/dev/null
}

log_step "Daemon dispatch smoke check"
printf '  daemon=%s\n' "$DAEMON_BASE_URL"

health_file="$TMP_DIR/health.json"
status_file="$TMP_DIR/status.json"
snapshot_file="$TMP_DIR/snapshot.json"
tap_file="$TMP_DIR/tap.json"
wait_file="$TMP_DIR/wait.json"

wait_for_daemon_ready "$status_file" "$health_file"
summarize_status "$status_file"

post_agent '{"action":"snapshot","snapshot_mode":"interactive","include_screenshot":false}' "$snapshot_file"
assert_ok_json "$snapshot_file" "dispatch snapshot"

if [[ $SKIP_VISIBLE_ACTION -eq 0 ]]; then
  post_agent "{\"action\":\"coordinate_act\",\"op\":\"tap\",\"x\":$TAP_X,\"y\":$TAP_Y,\"duration_ms\":50,\"approved\":true}" "$tap_file"
  assert_ok_json "$tap_file" "dispatch coordinate_act tap"
  post_agent '{"action":"wait","wait_for":"idle","timeout_ms":1500}' "$wait_file"
  assert_ok_json "$wait_file" "dispatch wait"
  printf '  visible_tap=%s,%s\n' "$TAP_X" "$TAP_Y"
else
  printf '  visible_tap=skipped\n'
fi

case "$LAYER" in
  auto)
    run_openclaw_cli_check || warn "OpenClaw CLI smoke check failed"
    run_hermes || warn "Hermes layer smoke check failed"
    ;;
  daemon) ;;
  openclaw) run_openclaw_cli_check ;;
  hermes) run_hermes ;;
esac

log_step "Smoke test passed"
