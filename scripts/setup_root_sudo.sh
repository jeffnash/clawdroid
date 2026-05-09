#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

INSTALL_SYSTEM_DEPS=0
INIT_WAYDROID=0
START_CONTAINER=0

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/setup_root_sudo.sh [options]

  --install-system-deps   Install OS packages when supported
  --init-waydroid         Run waydroid init if needed
  --start-container       Start the Waydroid container service/container
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-system-deps) INSTALL_SYSTEM_DEPS=1 ;;
    --init-waydroid) INIT_WAYDROID=1 ;;
    --start-container) START_CONTAINER=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ $INSTALL_SYSTEM_DEPS -eq 1 ]]; then
  "$PROJECT_ROOT/scripts/install_waydroid.sh" --install-system-deps
fi

if [[ $INIT_WAYDROID -eq 1 ]]; then
  "$PROJECT_ROOT/scripts/install_waydroid.sh" --init-waydroid
fi

if [[ $START_CONTAINER -eq 1 ]]; then
  log_step "Ensuring Waydroid container is running"
  if waydroid_container_running; then
    warn "Waydroid container already running; skipping redundant root-side start"
  else
    waydroid container start || warn "failed to start the Waydroid container"
  fi
fi

log_step "Root-side setup complete"
