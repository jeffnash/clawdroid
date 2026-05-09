#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

EXTRAS=""
RESTART_AFTER=1
WAYDROID_SCRIPT_REPO="${OPENCLAW_ANDROID_WAYDROID_SCRIPT_REPO:-https://github.com/casualsnek/waydroid_script}"
WAYDROID_SCRIPT_REV="${OPENCLAW_ANDROID_WAYDROID_SCRIPT_REV:-d5289cfd8929e86e7f0dc89ecadcef8b66930eec}"

usage() {
  cat <<'EOF'
Usage: ./scripts/install_waydroid_extras.sh --extras LIST [options]

Installs Waydroid image extras through waydroid_script. The Waydroid container
is restarted after install by default so image-level changes take effect.

Options:
  --extras LIST   Comma-separated extras, such as gapps, microg, libndk
  --no-restart   Do not restart Waydroid after installing extras
  --restart      Restart Waydroid after installing extras (default)
  --script-rev REV
                Reviewed waydroid_script commit/tag to use
  -h, --help     Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --extras) require_option_value "$1" "${2-}"; EXTRAS="$2"; shift ;;
    --no-restart) RESTART_AFTER=0 ;;
    --restart) RESTART_AFTER=1 ;;
    --script-rev) require_option_value "$1" "${2-}"; WAYDROID_SCRIPT_REV="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

[[ -n "$EXTRAS" ]] || fatal "--extras is required"
require_cmd git
require_cmd python3

WORKDIR="$PROJECT_ROOT/.cache/waydroid_script"
mkdir -p "$PROJECT_ROOT/.cache"
if [[ ! -d "$WORKDIR/.git" ]]; then
  git clone "$WAYDROID_SCRIPT_REPO" "$WORKDIR"
else
  git -C "$WORKDIR" remote set-url origin "$WAYDROID_SCRIPT_REPO"
fi
git -C "$WORKDIR" fetch --tags --force origin "$WAYDROID_SCRIPT_REV"
git -C "$WORKDIR" checkout --detach "$WAYDROID_SCRIPT_REV"
actual_rev="$(git -C "$WORKDIR" rev-parse HEAD)"
expected_rev="$(git -C "$WORKDIR" rev-parse "$WAYDROID_SCRIPT_REV^{commit}")"
[[ "$actual_rev" == "$expected_rev" ]] || fatal "waydroid_script revision verification failed: expected $expected_rev, got $actual_rev"

if [[ ! -d "$WORKDIR/venv" ]]; then
  python3 -m venv "$WORKDIR/venv"
fi
"$WORKDIR/venv/bin/pip" install -U pip wheel setuptools
if [[ -f "$WORKDIR/requirements.txt" ]]; then
  "$WORKDIR/venv/bin/pip" install -r "$WORKDIR/requirements.txt"
fi

IFS=',' read -r -a extra_list <<< "$EXTRAS"
pushd "$WORKDIR" >/dev/null
for extra in "${extra_list[@]}"; do
  extra="${extra// /}"
  [[ -n "$extra" ]] || continue
  log_step "Installing Waydroid extra: $extra"
  sudo_maybe "$WORKDIR/venv/bin/python" main.py install "$extra"
done
popd >/dev/null

run_root() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 1
  fi
}

restart_waydroid_for_extras() {
  log_step "Restarting Waydroid so extras take effect"
  if [[ -x "$PROJECT_ROOT/scripts/restart_everything_sudo.sh" && ( "$(id -u)" != "0" || -n "${SUDO_USER:-}" ) ]]; then
    run_root "$PROJECT_ROOT/scripts/restart_everything_sudo.sh" || warn "Full stack restart did not complete cleanly; rerun: sudo \"$PROJECT_ROOT/scripts/restart_everything_sudo.sh\""
    return 0
  fi

  timeout 30s waydroid session stop >/dev/null 2>&1 || true
  if command -v systemctl >/dev/null 2>&1; then
    run_root systemctl restart waydroid-container.service
  else
    run_root timeout 60s waydroid container stop >/dev/null 2>&1 || true
    run_root timeout 90s waydroid container start
  fi
  sleep 5
  if [[ -n "${DISPLAY:-}" && -n "${DBUS_SESSION_BUS_ADDRESS:-}" && -x "$SCRIPT_DIR/waydroid_supervisor_ctl.sh" ]]; then
    "$SCRIPT_DIR/waydroid_supervisor_ctl.sh" reset >/dev/null 2>&1 || true
  else
    warn "If the Waydroid UI or bridge does not return, run: sudo \"$PROJECT_ROOT/scripts/restart_everything_sudo.sh\""
  fi
}

if [[ $RESTART_AFTER -eq 1 ]]; then
  restart_waydroid_for_extras
else
  warn "Waydroid extras were installed but the container was not restarted. Restart Waydroid before expecting the extras to appear."
fi
