#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

PLUGIN_NAME="${CLAWDROID_HERMES_PLUGIN_NAME:-clawdroid}"
PLUGIN_SOURCE="$PROJECT_ROOT/hermes-plugin"
SKILL_NAME="${CLAWDROID_HERMES_SKILL_NAME:-clawdroid}"

usage() {
  cat <<'EOF'
Usage: ./scripts/install_hermes_plugin.sh [--copy] [--user NAME] [--system]

Installs the Clawdroid Hermes plugin into $HERMES_HOME/plugins/clawdroid.
By default this creates or updates a symlink to the repository checkout.
Use --copy to copy files instead.

When run as root and HERMES_HOME is not set, the installer uses the system
hermes-gateway.service HERMES_HOME when one is configured. Use --system to
force /root/.hermes or --user NAME to install for a specific desktop user.
EOF
}

MODE="symlink"
TARGET_USER=""
SYSTEM_HERMES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --copy) MODE="copy" ;;
    --user)
      [[ $# -ge 2 ]] || fatal "--user requires a username"
      TARGET_USER="$2"
      shift
      ;;
    --system) SYSTEM_HERMES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

[[ -f "$PLUGIN_SOURCE/plugin.yaml" ]] || fatal "Missing Hermes plugin source: $PLUGIN_SOURCE"

system_hermes_home() {
  local hermes_home
  command -v systemctl >/dev/null 2>&1 || return 0
  hermes_home="$(systemctl show -p Environment --value hermes-gateway.service 2>/dev/null \
    | tr ' ' '\n' \
    | sed -n 's/^HERMES_HOME=//p' \
    | head -n 1 || true)"
  printf '%s' "$hermes_home"
}

home_for_user() {
  getent passwd "$1" | cut -d: -f6
}

user_for_home() {
  local hermes_home="$1"
  local parent
  parent="$(dirname "$hermes_home")"
  getent passwd | awk -F: -v home="$parent" '$6 == home { print $1; exit }'
}

if [[ -n "$TARGET_USER" ]]; then
  id "$TARGET_USER" >/dev/null 2>&1 || fatal "Unknown target user: $TARGET_USER"
fi

if [[ -n "${HERMES_HOME:-}" ]]; then
  RESOLVED_HERMES_HOME="$HERMES_HOME"
elif [[ $SYSTEM_HERMES -eq 1 ]]; then
  RESOLVED_HERMES_HOME="$(home_for_user root)/.hermes"
elif [[ -n "$TARGET_USER" ]]; then
  RESOLVED_HERMES_HOME="$(home_for_user "$TARGET_USER")/.hermes"
elif [[ "$(id -u)" == "0" ]]; then
  RESOLVED_HERMES_HOME="$(system_hermes_home)"
  RESOLVED_HERMES_HOME="${RESOLVED_HERMES_HOME:-$(home_for_user root)/.hermes}"
else
  RESOLVED_HERMES_HOME="$HOME/.hermes"
fi
[[ -n "$RESOLVED_HERMES_HOME" ]] || fatal "Unable to determine HERMES_HOME"

INSTALL_USER="${TARGET_USER:-$(user_for_home "$RESOLVED_HERMES_HOME")}"
if [[ -z "$INSTALL_USER" && "$RESOLVED_HERMES_HOME" == "$(home_for_user root)/.hermes" ]]; then
  INSTALL_USER="root"
fi
INSTALL_USER="${INSTALL_USER:-$(id -un)}"
id "$INSTALL_USER" >/dev/null 2>&1 || fatal "Unknown install user: $INSTALL_USER"
INSTALL_HOME="$(home_for_user "$INSTALL_USER")"
INSTALL_UID="$(id -u "$INSTALL_USER")"
PLUGIN_TARGET="$RESOLVED_HERMES_HOME/plugins/$PLUGIN_NAME"

run_install_user() {
  if [[ "$(id -u)" == "$INSTALL_UID" ]]; then
    env HOME="$INSTALL_HOME" HERMES_HOME="$RESOLVED_HERMES_HOME" "$@"
  elif [[ "$INSTALL_USER" == "root" ]]; then
    sudo_maybe env HOME="$INSTALL_HOME" HERMES_HOME="$RESOLVED_HERMES_HOME" "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$INSTALL_USER" env HOME="$INSTALL_HOME" HERMES_HOME="$RESOLVED_HERMES_HOME" "$@"
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u "$INSTALL_USER" -- env HOME="$INSTALL_HOME" HERMES_HOME="$RESOLVED_HERMES_HOME" "$@"
  else
    fatal "Need sudo or runuser to install as $INSTALL_USER"
  fi
}

run_install_user mkdir -p "$RESOLVED_HERMES_HOME/plugins"
if [[ -e "$PLUGIN_TARGET" || -L "$PLUGIN_TARGET" ]]; then
  run_install_user rm -rf "$PLUGIN_TARGET"
fi

case "$MODE" in
  symlink)
    run_install_user ln -s "$PLUGIN_SOURCE" "$PLUGIN_TARGET"
    ;;
  copy)
    run_install_user mkdir -p "$PLUGIN_TARGET"
    run_install_user cp -a "$PLUGIN_SOURCE"/. "$PLUGIN_TARGET"/
    ;;
esac

SKILL_SOURCE="$PLUGIN_SOURCE/skills/clawdroid"
SKILL_TARGET="$RESOLVED_HERMES_HOME/skills/$SKILL_NAME"
if [[ -f "$SKILL_SOURCE/SKILL.md" ]]; then
  run_install_user mkdir -p "$RESOLVED_HERMES_HOME/skills"
  if [[ -e "$SKILL_TARGET" || -L "$SKILL_TARGET" ]]; then
    run_install_user rm -rf "$SKILL_TARGET"
  fi
  case "$MODE" in
    symlink)
      run_install_user ln -s "$SKILL_SOURCE" "$SKILL_TARGET"
      ;;
    copy)
      run_install_user mkdir -p "$SKILL_TARGET"
      run_install_user cp -a "$SKILL_SOURCE"/. "$SKILL_TARGET"/
      ;;
  esac
fi

cat <<EOF
Installed Clawdroid Hermes plugin:
  $PLUGIN_TARGET -> $PLUGIN_SOURCE
  $RESOLVED_HERMES_HOME/skills/$SKILL_NAME -> $SKILL_SOURCE
  HERMES_HOME=$RESOLVED_HERMES_HOME
  install user=$INSTALL_USER

Restart Hermes before using the new tools.
Try:
  hermes -t clawdroid
EOF
