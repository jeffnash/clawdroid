#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

require_cmd curl
require_cmd unzip

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/.local/share/android-sdk}"
CMDLINE_DIR="$ANDROID_SDK_ROOT/cmdline-tools/latest"
SDKMANAGER="$CMDLINE_DIR/bin/sdkmanager"

mkdir -p "$ANDROID_SDK_ROOT"

if [[ ! -x "$SDKMANAGER" ]]; then
  log_step "Downloading Android command-line tools"
  mkdir -p "$PROJECT_ROOT/.cache"
  ZIP="$PROJECT_ROOT/.cache/android-commandlinetools.zip"
  URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
  curl_fetch "$URL" -o "$ZIP"
  rm -rf "$ANDROID_SDK_ROOT/cmdline-tools"
  mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"
  unzip -q "$ZIP" -d "$ANDROID_SDK_ROOT/cmdline-tools"
  mv "$ANDROID_SDK_ROOT/cmdline-tools/cmdline-tools" "$CMDLINE_DIR"
fi

yes | "$SDKMANAGER" --licenses >/dev/null || true
"$SDKMANAGER" --install       "platform-tools"       "platforms;android-34"       "build-tools;34.0.0"       >/dev/null

cat <<EOF
Android SDK ready:
  ANDROID_SDK_ROOT=$ANDROID_SDK_ROOT
  sdkmanager=$SDKMANAGER
EOF
