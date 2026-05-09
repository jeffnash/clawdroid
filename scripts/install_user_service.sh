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

[Install]
WantedBy=default.target
EOF

cat > "$SERVICE_DIR/openclaw-android-waydroid-ui.service" <<EOF
[Unit]
Description=OpenClaw Waydroid UI supervisor
After=graphical-session.target
Wants=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
Environment=OPENCLAW_ANDROID_WINDOW_BACKEND=$WINDOW_BACKEND
ExecStart=$PROJECT_ROOT/scripts/run_waydroid_ui_supervisor.sh
Restart=on-failure
RestartSec=2
TimeoutStopSec=45
KillMode=mixed

[Install]
WantedBy=graphical-session.target
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
