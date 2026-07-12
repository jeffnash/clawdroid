#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

DISPLAY_NUM="${OPENCLAW_ANDROID_DISPLAY:-:99}"
RESOLUTION="${OPENCLAW_ANDROID_HEADLESS_RESOLUTION:-1920x1080x24}"
SERVICE_DIR="$HOME/.config/systemd/user"
ENVIRONMENT_D_DIR="$HOME/.config/environment.d"
UNIT_NAME="clawdroid-headless-display.service"
START_NOW=0
FIX_KERNEL_GUARDS=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install_headless_display.sh [--now] [--fix-kernel-guards]

Installs a user-level virtual X display (Xvfb) service for headless
Clawdroid hosts, wires DISPLAY into the systemd user environment so units
see it at boot, and checks the host kernel for panic tripwires.

Options:
  --now                Enable and start the display service immediately
  --fix-kernel-guards  With sudo, persistently disable kernel.panic_on_warn
                       and kernel.hung_task_panic (see the warning this
                       script prints for why)

Environment:
  OPENCLAW_ANDROID_DISPLAY               X display to serve (default :99)
  OPENCLAW_ANDROID_HEADLESS_RESOLUTION   Xvfb screen geometry
                                         (default 1920x1080x24)

After installing, rerun ./scripts/install_user_service.sh --headless so the
UI supervisor unit targets this display and starts at boot.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --now) START_NOW=1 ;;
    --fix-kernel-guards) FIX_KERNEL_GUARDS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

require_cmd Xvfb
require_cmd systemctl

[[ "$DISPLAY_NUM" =~ ^:[0-9]+$ ]] || fatal "OPENCLAW_ANDROID_DISPLAY must look like :99 (got: $DISPLAY_NUM)"
[[ "$RESOLUTION" =~ ^[0-9]+x[0-9]+x[0-9]+$ ]] || fatal "OPENCLAW_ANDROID_HEADLESS_RESOLUTION must look like 1920x1080x24 (got: $RESOLUTION)"
DISPLAY_INDEX="${DISPLAY_NUM#:}"

# A kernel that panics on warnings turns any benign WARN into a host
# reboot. Weston reliably triggers one on kernel >= 6.17 (an executable
# mmap probe hits the path_noexec WARN), which on a guarded host produces
# an infinite boot -> weston -> panic -> reboot loop roughly every 45
# seconds. This exact failure took down a production Clawdroid host on
# 2026-07-11, so refuse to let it pass silently.
check_kernel_guards() {
  local panic_on_warn hung_task_panic
  panic_on_warn="$(cat /proc/sys/kernel/panic_on_warn 2>/dev/null || printf '0')"
  hung_task_panic="$(cat /proc/sys/kernel/hung_task_panic 2>/dev/null || printf '0')"

  if [[ "$panic_on_warn" == "1" || "$hung_task_panic" == "1" ]]; then
    warn "This host has kernel panic tripwires enabled:"
    [[ "$panic_on_warn" == "1" ]] && warn "  kernel.panic_on_warn=1 (any benign kernel WARNING reboots the host; Weston triggers one on kernel >= 6.17)"
    [[ "$hung_task_panic" == "1" ]] && warn "  kernel.hung_task_panic=1 (a slow disk/IO wait can reboot the host)"
    if [[ "$FIX_KERNEL_GUARDS" -eq 1 ]]; then
      fix_kernel_guards
    else
      warn "Re-run with --fix-kernel-guards to disable them persistently, or fix /etc/sysctl.d yourself."
      return 1
    fi
  fi
  return 0
}

fix_kernel_guards() {
  local conf=/etc/sysctl.d/99-zz-clawdroid-kernel-guards.conf
  log_step "Disabling kernel panic tripwires (requires sudo)"
  sudo_maybe tee "$conf" >/dev/null <<'CONF'
# Installed by clawdroid scripts/install_headless_display.sh.
# panic_on_warn turns any benign kernel WARNING into a host reboot; Weston
# triggers one on kernel >= 6.17, which boot-loops a host that autostarts
# a display. hung_task_panic reboots on long-but-harmless IO waits. This
# file sorts after other sysctl.d entries so it wins over stale guards.
kernel.panic_on_warn = 0
kernel.hung_task_panic = 0
CONF
  sudo_maybe sysctl -p "$conf" >/dev/null ||
    warn "Failed to apply $conf at runtime; it will apply on next boot"
  log_step "Kernel panic tripwires disabled (persisted in $conf)"
}

check_kernel_guards || true

mkdir -p "$SERVICE_DIR" "$ENVIRONMENT_D_DIR"

cat > "$SERVICE_DIR/$UNIT_NAME" <<EOF
[Unit]
Description=Clawdroid virtual X display (Xvfb $DISPLAY_NUM)

[Service]
Type=simple
ExecStartPre=/bin/sh -c 'rm -f /tmp/.X${DISPLAY_INDEX}-lock /tmp/.X11-unix/X${DISPLAY_INDEX}'
ExecStart=/usr/bin/Xvfb $DISPLAY_NUM -screen 0 $RESOLUTION -nolisten tcp
Restart=always
RestartSec=5
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
EOF

# environment.d is what makes DISPLAY visible to user units started at
# boot (with lingering); systemctl --user import-environment only lasts
# for the current manager lifetime.
cat > "$ENVIRONMENT_D_DIR/90-clawdroid-headless.conf" <<EOF
DISPLAY=$DISPLAY_NUM
EOF

systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME" >/dev/null 2>&1 || warn "Failed to enable $UNIT_NAME"
systemctl --user set-environment "DISPLAY=$DISPLAY_NUM" 2>/dev/null || true

if [[ "$START_NOW" -eq 1 ]]; then
  systemctl --user restart "$UNIT_NAME"
  log_step "Started $UNIT_NAME on $DISPLAY_NUM ($RESOLUTION)"
fi

log_step "Installed $SERVICE_DIR/$UNIT_NAME"
log_step "Installed $ENVIRONMENT_D_DIR/90-clawdroid-headless.conf (DISPLAY=$DISPLAY_NUM)"

if command -v loginctl >/dev/null 2>&1; then
  linger="$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || true)"
  if [[ "$linger" != "yes" ]]; then
    warn "User lingering is disabled; the display will stop at logout. Enable with: sudo loginctl enable-linger $USER"
  fi
fi

log_step "Next: ./scripts/install_user_service.sh --headless (targets this display at boot)"
