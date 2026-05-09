#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="${CLAWDROID_SMOKE_IMAGE:-clawdroid-setup-smoke:local}"
CONTAINER_NAME="${CLAWDROID_SMOKE_CONTAINER:-clawdroid-setup-smoke}"
LOG_DIR="${CLAWDROID_SMOKE_LOG_DIR:-$PROJECT_ROOT/debug}"
LOG_FILE="$LOG_DIR/docker-setup-smoke-$(date +%Y%m%d-%H%M%S).log"

usage() {
  cat <<'EOF'
Usage: scripts/docker_smoke_setup.sh [options]

Build and run a disposable Docker smoke test for the public setup guide.

Options:
  --inside-container   Internal entrypoint used by docker/smoke/Dockerfile
  --keep-container     Keep the stopped container for inspection
  -h, --help           Show this help

Environment:
  CLAWDROID_SMOKE_IMAGE       Docker image tag (default: clawdroid-setup-smoke:local)
  CLAWDROID_SMOKE_CONTAINER   Container name (default: clawdroid-setup-smoke)
  CLAWDROID_SMOKE_LOG_DIR     Host log directory (default: ./debug)
EOF
}

KEEP_CONTAINER=0
INSIDE_CONTAINER=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --inside-container) INSIDE_CONTAINER=1 ;;
    --keep-container) KEEP_CONTAINER=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

inside_container() {
  mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.cache"
  chmod 700 "$XDG_RUNTIME_DIR"

  ./setup_everything.sh --help >/tmp/clawdroid-setup-help.txt

  ./setup_everything.sh \
    --install-system-deps \
    --init-waydroid \
    --sudo-mode inline \
    --skip-sdk-install \
    --skip-apk-build \
    --skip-systemd \
    --skip-default-stores
}

run_host() {
  mkdir -p "$LOG_DIR"

  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or not on PATH" >&2
    exit 1
  fi

  docker build -f "$PROJECT_ROOT/docker/smoke/Dockerfile" -t "$IMAGE_NAME" "$PROJECT_ROOT"

  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

  set +e
  docker run \
    --name "$CONTAINER_NAME" \
    --privileged \
    --tmpfs /run \
    --tmpfs /run/lock \
    --tmpfs /tmp:exec,mode=1777 \
    "$IMAGE_NAME" 2>&1 | tee "$LOG_FILE"
  status=${PIPESTATUS[0]}
  set -e

  if [[ $KEEP_CONTAINER -eq 0 ]]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  else
    echo "Kept container: $CONTAINER_NAME"
  fi

  echo "Smoke log: $LOG_FILE"
  exit "$status"
}

if [[ $INSIDE_CONTAINER -eq 1 ]]; then
  inside_container
else
  run_host
fi
