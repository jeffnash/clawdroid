#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

require_cmd curl
require_cmd unzip

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/.local/share/android-sdk}"
export ANDROID_SDK_ROOT
export ANDROID_HOME="$ANDROID_SDK_ROOT"
SDKMANAGER="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"

android_sdk_ready() {
  [[ -x "$SDKMANAGER" ]] || return 1
  [[ -x "$ANDROID_SDK_ROOT/platform-tools/adb" ]] || return 1
  [[ -f "$ANDROID_SDK_ROOT/platforms/android-34/android.jar" ]] || return 1
  [[ -x "$ANDROID_SDK_ROOT/build-tools/34.0.0/aapt" || -x "$ANDROID_SDK_ROOT/build-tools/34.0.0/aapt2" ]] || return 1
  return 0
}

if ! android_sdk_ready; then
  "${OPENCLAW_ANDROID_BOOTSTRAP_SDK_SCRIPT:-$SCRIPT_DIR/bootstrap_android_sdk.sh}"
fi

if [[ "${OPENCLAW_ANDROID_BUILD_COMPANION_CHECK_SDK_ONLY:-0}" == "1" ]]; then
  android_sdk_ready || fatal "Android SDK bootstrap did not install required SDK components"
  log_step "Android SDK components are present"
  exit 0
fi

GRADLE_VERSION="8.7"
GRADLE_DIR="$PROJECT_ROOT/.cache/gradle-$GRADLE_VERSION"
GRADLE_BIN="$GRADLE_DIR/bin/gradle"
if [[ ! -x "$GRADLE_BIN" ]]; then
  log_step "Downloading Gradle $GRADLE_VERSION"
  mkdir -p "$PROJECT_ROOT/.cache"
  ZIP="$PROJECT_ROOT/.cache/gradle-$GRADLE_VERSION-bin.zip"
  curl_fetch "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip" -o "$ZIP"
  unzip -q "$ZIP" -d "$PROJECT_ROOT/.cache"
fi

pushd "$PROJECT_ROOT/android-companion" >/dev/null
"$GRADLE_BIN" --no-daemon assembleDebug
popd >/dev/null

log_step "APK output"
ls -lah "$PROJECT_ROOT/android-companion/app/build/outputs/apk/debug/"
