#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

PROFILE="${OPENCLAW_ANDROID_DEVICE_PROFILE:-samsung-galaxy-s24-ultra}"
ADB_SERIAL="${OPENCLAW_ANDROID_ADB_SERIAL:-}"

usage() {
  cat <<'EOF'
Usage: ./scripts/apply_device_profile.sh [options]

  --profile NAME        Device profile name (default: samsung-galaxy-s24-ultra)
  --adb-serial SERIAL   adb serial override
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) require_option_value "$1" "${2-}"; PROFILE="$2"; shift ;;
    --adb-serial) require_option_value "$1" "${2-}"; ADB_SERIAL="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

require_cmd adb

if [[ -z "$ADB_SERIAL" ]]; then
  WAYDROID_IP="$("$PROJECT_ROOT/scripts/install_waydroid.sh" --print-ip 2>/dev/null || true)"
  [[ -n "$WAYDROID_IP" ]] || fatal "Unable to determine Waydroid IP."
  ADB_SERIAL="${WAYDROID_IP}:5555"
fi

adb_quick connect "$ADB_SERIAL" >/dev/null 2>&1 || true

case "$PROFILE" in
  samsung-galaxy-s24-ultra|s24-ultra|galaxy-s24-ultra)
    # Based on Samsung's official S24 Ultra display specs: 3120x1440 on a 6.8" display.
    # We use 480 logical density as a practical Android bucket close to this class of device.
    # Keep the default profile in portrait because forced landscape rotation can desync
    # the visible surface and pointer/input mapping in some Waydroid hosts.
    adb_quick -s "$ADB_SERIAL" shell wm size 1440x3120 || true
    adb_quick -s "$ADB_SERIAL" shell wm density 480 || true
    adb_quick -s "$ADB_SERIAL" shell settings put system accelerometer_rotation 0 || true
    adb_quick -s "$ADB_SERIAL" shell settings put system user_rotation 0 || true
    ;;
  reset|default)
    adb_quick -s "$ADB_SERIAL" shell wm size reset || true
    adb_quick -s "$ADB_SERIAL" shell wm density reset || true
    adb_quick -s "$ADB_SERIAL" shell settings put system accelerometer_rotation 1 || true
    ;;
  *)
    fatal "Unknown device profile: $PROFILE"
    ;;
esac

printf 'Applied device profile: %s\n' "$PROFILE"
