#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

SERVICE_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/openclaw-android-waydroid"
LLM_CONFIG_PATH="${OPENCLAW_ANDROID_LLM_CONFIG_PATH:-$CONFIG_DIR/llm.json}"
LLM_ENV_FILE="${OPENCLAW_ANDROID_LLM_ENV_FILE:-$CONFIG_DIR/env}"
ADB_SERIAL="${OPENCLAW_ANDROID_ADB_SERIAL:-}"
WINDOW_BACKEND="${OPENCLAW_ANDROID_WINDOW_BACKEND:-auto}"
RUNTIME_DIR_VALUE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DBUS_SESSION_BUS_VALUE="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${RUNTIME_DIR_VALUE}/bus}"
HEADLESS=0
HEADLESS_DISPLAY="${OPENCLAW_ANDROID_DISPLAY:-:99}"

usage() {
  cat <<'EOF'
Usage: ./scripts/install_user_service.sh [--headless]

Installs (and now also enables) the Clawdroid user units.

  --headless   Target a headless host: bake DISPLAY into the UI supervisor
               unit and hook it to default.target so it starts at boot
               without a desktop login. Pair with
               scripts/install_headless_display.sh, which provides the
               virtual display. Override the display with
               OPENCLAW_ANDROID_DISPLAY (default :99).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --headless) HEADLESS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

systemd_unit_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '"%s"' "$value"
}

systemd_env_file_path() {
  local value="$1"
  value="${value//\\/\\x5c}"
  value="${value//%/%%}"
  value="${value//$'\t'/\\x09}"
  value="${value// /\\x20}"
  printf '%s' "$value"
}

LLM_ENV_FILE_UNIT_LINE=""
if [[ "$LLM_ENV_FILE" != "$CONFIG_DIR/env" ]]; then
  LLM_ENV_FILE_UNIT_LINE="EnvironmentFile=-$(systemd_env_file_path "$LLM_ENV_FILE")"
fi
ADB_SERIAL_UNIT_LINE=""
if [[ -z "$ADB_SERIAL" ]]; then
  WAYDROID_IP="$("$PROJECT_ROOT/scripts/install_waydroid.sh" --print-ip 2>/dev/null || true)"
  if [[ -n "$WAYDROID_IP" ]]; then
    ADB_SERIAL="${WAYDROID_IP}:5555"
  fi
fi
if [[ -n "$ADB_SERIAL" ]]; then
  ADB_SERIAL_UNIT_LINE="Environment=$(systemd_unit_quote "OPENCLAW_ANDROID_ADB_SERIAL=$ADB_SERIAL")"
fi
mkdir -p "$CONFIG_DIR" "$(dirname "$LLM_CONFIG_PATH")" "$(dirname "$LLM_ENV_FILE")"
if [[ ! -f "$LLM_CONFIG_PATH" && -f "$PROJECT_ROOT/docs/llm.example.json" ]]; then
  cp "$PROJECT_ROOT/docs/llm.example.json" "$LLM_CONFIG_PATH"
fi
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_DIR/openclaw-android-waydroid.service" <<EOF
[Unit]
Description=OpenClaw Android Waydroid daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT/python-daemon
EnvironmentFile=-$(systemd_env_file_path "$HOME/.hermes/.env")
EnvironmentFile=-$(systemd_env_file_path "$CONFIG_DIR/env")
$LLM_ENV_FILE_UNIT_LINE
Environment=$(systemd_unit_quote "PYTHONUNBUFFERED=1")
Environment=$(systemd_unit_quote "XDG_RUNTIME_DIR=$RUNTIME_DIR_VALUE")
Environment=$(systemd_unit_quote "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_VALUE")
Environment=$(systemd_unit_quote "OPENCLAW_ANDROID_DAEMON_HOST=127.0.0.1")
Environment=$(systemd_unit_quote "OPENCLAW_ANDROID_DAEMON_PORT=48765")
Environment=$(systemd_unit_quote "OPENCLAW_ANDROID_LLM_CONFIG_PATH=$LLM_CONFIG_PATH")
$ADB_SERIAL_UNIT_LINE
ExecStart=$PROJECT_ROOT/python-daemon/.venv/bin/python -m openclaw_android_daemon.main --host 127.0.0.1 --port 48765
Restart=on-failure
RestartSec=3
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
EOF

UI_UNIT_DEPS="After=graphical-session.target
Wants=graphical-session.target
PartOf=graphical-session.target"
UI_UNIT_ENV_LINES="Environment=OPENCLAW_ANDROID_WINDOW_BACKEND=$WINDOW_BACKEND"
UI_UNIT_WANTED_BY="graphical-session.target"
if [[ "$HEADLESS" -eq 1 ]]; then
  # graphical-session.target never activates without a desktop login, so a
  # headless host hooks the supervisor to default.target and bakes the
  # virtual display in. The supervisor itself waits until the display is
  # actually reachable.
  UI_UNIT_DEPS="After=default.target"
  UI_UNIT_ENV_LINES="Environment=OPENCLAW_ANDROID_WINDOW_BACKEND=x11
Environment=$(systemd_unit_quote "DISPLAY=$HEADLESS_DISPLAY")
Environment=$(systemd_unit_quote "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_VALUE")
Environment=$(systemd_unit_quote "XDG_RUNTIME_DIR=$RUNTIME_DIR_VALUE")"
  UI_UNIT_WANTED_BY="default.target"
fi

cat > "$SERVICE_DIR/openclaw-android-waydroid-ui.service" <<EOF
[Unit]
Description=OpenClaw Waydroid UI supervisor
$UI_UNIT_DEPS

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
$UI_UNIT_ENV_LINES
ExecStart=$PROJECT_ROOT/scripts/run_waydroid_ui_supervisor.sh
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=0
TimeoutStopSec=45
KillMode=mixed

[Install]
WantedBy=$UI_UNIT_WANTED_BY
EOF

mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/openclaw-android-waydroid-ui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=OpenClaw Waydroid UI Bootstrap
Exec=$PROJECT_ROOT/scripts/import_graphical_env.sh
X-GNOME-Autostart-enabled=true
NoDisplay=true
Terminal=false
EOF

log_step "Installed user service at $SERVICE_DIR/openclaw-android-waydroid.service"
log_step "Installed default LLM config at $LLM_CONFIG_PATH"
log_step "Installed user service LLM env path at $LLM_ENV_FILE"
log_step "Installed graphical UI supervisor unit at $SERVICE_DIR/openclaw-android-waydroid-ui.service"
log_step "Installed desktop-session env bootstrap at $AUTOSTART_DIR/openclaw-android-waydroid-ui.desktop"

# Writing unit files does nothing until systemd rereads them and the units
# are enabled; skipping this was why fresh installs never started on boot.
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable openclaw-android-waydroid.service >/dev/null 2>&1 ||
    warn "Failed to enable openclaw-android-waydroid.service"
  systemctl --user enable openclaw-android-waydroid-ui.service >/dev/null 2>&1 ||
    warn "Failed to enable openclaw-android-waydroid-ui.service"
  log_step "Enabled both user units (start them with systemctl --user start, or reboot)"
  if command -v loginctl >/dev/null 2>&1; then
    linger="$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || true)"
    if [[ "$linger" != "yes" ]]; then
      warn "User lingering is disabled; services will stop at logout. Enable with: sudo loginctl enable-linger $USER"
    fi
  fi
else
  warn "systemd user manager is unavailable; enable the units manually with systemctl --user daemon-reload && systemctl --user enable openclaw-android-waydroid.service openclaw-android-waydroid-ui.service"
fi
