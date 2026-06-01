#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

OPEN_URL=0
WAIT_TIMEOUT="${OPENCLAW_ANDROID_GSF_WAIT_TIMEOUT:-180}"
CERT_URL="https://www.google.com/android/uncertified"

usage() {
  cat <<'EOF'
Usage: ./scripts/google_play_certification.sh [options]

Prints the Google Services Framework Android ID needed for Google's
uncertified-device registration page. This helps with Play Store certification
after installing GApps in Waydroid.

Options:
  --open-url             Open Google's registration page with xdg-open when available
  --wait-timeout SECONDS Wait for the GSF database to appear (default: 180)
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --open-url) OPEN_URL=1 ;;
    --wait-timeout) require_option_value "$1" "${2-}"; WAIT_TIMEOUT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

if ! [[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]]; then
  fatal "--wait-timeout must be a non-negative integer"
fi

require_cmd waydroid

waydroid_shell_quick() {
  timeout 20s waydroid shell -- "$@" 2>/dev/null | tr -d '\r'
}

package_installed() {
  local package="$1"
  waydroid_shell_quick pm list packages "$package" | grep -Fxq "package:${package}"
}

google_account_present() {
  local account_output
  account_output="$(
    waydroid_shell_quick cmd account list 2>/dev/null \
      || waydroid_shell_quick dumpsys account 2>/dev/null \
      || true
  )"
  grep -Eq 'com\.google|@gmail\.com|@googlemail\.com' <<<"$account_output"
}

fetch_gsf_id() {
  waydroid_shell_quick sh -c \
    "sqlite3 /data/data/com.google.android.gsf/databases/gservices.db 'select value from main where name = \"android_id\";'" \
    | sed '/^[[:space:]]*$/d' | tail -n 1
}

deadline=$((SECONDS + WAIT_TIMEOUT))
GSF_ID=""
while (( SECONDS <= deadline )); do
  GSF_ID="$(fetch_gsf_id || true)"
  if [[ "$GSF_ID" =~ ^[0-9]+$ ]]; then
    break
  fi
  sleep 3
done

if ! [[ "$GSF_ID" =~ ^[0-9]+$ ]]; then
  if package_installed com.google.android.gsf && ! google_account_present; then
    cat >&2 <<EOF
Unable to read a Google Services Framework Android ID yet.

GApps is installed, but no signed-in Google account was detected inside
Waydroid. Open Play Store in Waydroid, sign in with a Google account, wait for
Play Services to finish initializing, then rerun:
  "$PROJECT_ROOT/scripts/google_play_certification.sh"
EOF
    exit 1
  fi

  cat >&2 <<EOF
Unable to read a Google Services Framework Android ID yet.

Make sure GApps is installed, Waydroid has booted, and Google Play Services has
had time to initialize. Then rerun:
  "$PROJECT_ROOT/scripts/google_play_certification.sh"
EOF
  exit 1
fi

cat <<EOF
Google Play certification helper

GSF Android ID:
  $GSF_ID

Register it here:
  $CERT_URL

After submitting, wait for Google's registration to propagate. If Play Store
still reports the device as uncertified, restart Waydroid:
  sudo "$PROJECT_ROOT/scripts/restart_everything_sudo.sh"
EOF

if [[ $OPEN_URL -eq 1 ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$CERT_URL" >/dev/null 2>&1 || warn "Failed to open $CERT_URL"
  else
    warn "xdg-open is not installed; open $CERT_URL manually"
  fi
fi
