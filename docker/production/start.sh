#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${CLAWDROID_PROJECT_ROOT:-/opt/clawdroid}"
LOG_DIR="${CLAWDROID_LOG_DIR:-/var/log/clawdroid}"
SETUP_MARKER="${CLAWDROID_SETUP_MARKER:-/var/lib/clawdroid/.setup-complete}"
DAEMON_HOST="${OPENCLAW_ANDROID_DAEMON_HOST:-0.0.0.0}"
DAEMON_PORT="${OPENCLAW_ANDROID_DAEMON_PORT:-48765}"
DISPLAY="${DISPLAY:-:99}"
XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/clawdroid}"

mkdir -p "$LOG_DIR" "$XDG_RUNTIME_DIR" "$(dirname "$SETUP_MARKER")"
chmod 700 "$XDG_RUNTIME_DIR"
export DISPLAY XDG_RUNTIME_DIR

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  DBUS_SESSION_BUS_ADDRESS="$(dbus-daemon --session --fork --print-address)"
  export DBUS_SESSION_BUS_ADDRESS
fi

if [[ ! -S /run/dbus/system_bus_socket ]]; then
  mkdir -p /run/dbus
  dbus-daemon --system --fork || true
fi

if ! pgrep -f "Xvfb ${DISPLAY}" >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 "${CLAWDROID_XVFB_GEOMETRY:-1600x900x24}" -nolisten tcp \
    >"$LOG_DIR/xvfb.log" 2>&1 &
fi

if [[ ! -f "$SETUP_MARKER" || "${CLAWDROID_FORCE_SETUP:-0}" == "1" ]]; then
  "$PROJECT_ROOT/setup_everything.sh" \
    --install-system-deps \
    --init-waydroid \
    --sudo-mode inline \
    ${CLAWDROID_INSTALL_HERMES_PLUGIN:+--install-hermes-plugin} \
    ${CLAWDROID_INSTALL_OPENCLAW:+--install-openclaw} \
    ${CLAWDROID_ENABLE_ADMIN_TOOL:+--enable-admin-tool} \
    ${CLAWDROID_SKIP_DEFAULT_STORES:+--skip-default-stores} \
    ${CLAWDROID_WITH_GAPPS:+--with-gapps}
  touch "$SETUP_MARKER"
fi

"$PROJECT_ROOT/scripts/install_waydroid.sh" --start-waydroid || true

if [[ ! -x "$PROJECT_ROOT/python-daemon/.venv/bin/python" ]]; then
  python3 -m venv "$PROJECT_ROOT/python-daemon/.venv"
  "$PROJECT_ROOT/python-daemon/.venv/bin/pip" install -U pip wheel setuptools
  "$PROJECT_ROOT/python-daemon/.venv/bin/pip" install -r "$PROJECT_ROOT/python-daemon/requirements.txt"
fi

# The daemon package is run in-place, so the working directory must be
# python-daemon/ for `-m openclaw_android_daemon.main` to be importable
# (this matches WorkingDirectory= in the systemd unit).
cd "$PROJECT_ROOT/python-daemon"
exec "$PROJECT_ROOT/python-daemon/.venv/bin/python" \
  -m openclaw_android_daemon.main \
  --host "$DAEMON_HOST" \
  --port "$DAEMON_PORT"
