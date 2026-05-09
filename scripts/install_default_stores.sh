#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

STORES="${OPENCLAW_ANDROID_DEFAULT_STORES:-f-droid,aurora-store,aptoide}"
DOWNLOAD_DIR="${OPENCLAW_ANDROID_STORE_DOWNLOAD_DIR:-$PROJECT_ROOT/.cache/default-stores}"

usage() {
  cat <<'EOF'
Usage: ./scripts/install_default_stores.sh [options]

  --stores LIST         Comma-separated stores to install
  --download-dir PATH   Cache/download directory for APKs
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stores) require_option_value "$1" "${2-}"; STORES="$2"; shift ;;
    --download-dir) require_option_value "$1" "${2-}"; DOWNLOAD_DIR="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "Unknown argument: $1" ;;
  esac
  shift
done

require_cmd curl
require_cmd adb
require_cmd jq

mkdir -p "$DOWNLOAD_DIR"

WAYDROID_IP="$("$PROJECT_ROOT/scripts/install_waydroid.sh" --print-ip 2>/dev/null || true)"
[[ -n "$WAYDROID_IP" ]] || fatal "Unable to determine Waydroid IP."
ADB_SERIAL="${OPENCLAW_ANDROID_ADB_SERIAL:-${WAYDROID_IP}:5555}"
adb_quick connect "$ADB_SERIAL" >/dev/null 2>&1 || true

download_fdroid() {
  local out="$DOWNLOAD_DIR/F-Droid.apk"
  curl_fetch "https://f-droid.org/F-Droid.apk" -o "$out"
  printf '%s\n' "$out"
}

download_aurora_store() {
  local out="$DOWNLOAD_DIR/AuroraStore.apk"
  local api="https://gitlab.com/api/v4/projects/AuroraOSS%2FAuroraStore/releases/permalink/latest"
  local payload url
  payload="$(curl_fetch "$api")"
  url="$(printf '%s' "$payload" | jq -r '
    [
      .assets.links[]?.direct_asset_url,
      .assets.links[]?.url
    ]
    | map(select(type == "string" and test("\\.apk($|\\?)")))
    | first // empty
  ')"
  if [[ -z "$url" ]]; then
    url="$(printf '%s' "$payload" | jq -r '.description // ""' \
      | grep -Eo '/uploads/[^)]*AuroraStore[^)]*\.apk' | head -n 1 || true)"
    if [[ -n "$url" && "$url" == /* ]]; then
      url="https://gitlab.com${url}"
    fi
  fi
  [[ -n "$url" ]] || fatal "Unable to locate latest Aurora Store APK URL."
  curl_fetch "$url" -o "$out"
  printf '%s\n' "$out"
}

download_aptoide() {
  local out="$DOWNLOAD_DIR/Aptoide.apk"
  local api="${OPENCLAW_ANDROID_APTOIDE_META_URL:-https://ws2.aptoide.com/api/7/app/getMeta/package_name=cm.aptoide.pt}"
  local payload url
  payload="$(curl_fetch "$api")"
  url="$(printf '%s' "$payload" | jq -r '.data.file.path // .data.file.path_alt // empty')"
  if [[ -z "$url" ]]; then
    warn "Unable to resolve the official Aptoide APK URL from $api. Set OPENCLAW_ANDROID_APTOIDE_APK_URL to override."
    url="${OPENCLAW_ANDROID_APTOIDE_APK_URL:-}"
  fi
  [[ -n "$url" ]] || return 1
  curl_fetch "$url" -o "$out"
  printf '%s\n' "$out"
}

install_apk_file() {
  local apk="$1"
  adb_install_cmd -s "$ADB_SERIAL" install -r "$apk"
}

package_for_store() {
  case "$1" in
    f-droid) printf '%s\n' "org.fdroid.fdroid" ;;
    aurora-store|aurora) printf '%s\n' "com.aurora.store" ;;
    aptoide) printf '%s\n' "cm.aptoide.pt" ;;
    *) return 1 ;;
  esac
}

package_installed() {
  local package="$1"
  adb_quick -s "$ADB_SERIAL" shell pm list packages "$package" 2>/dev/null | tr -d '\r' | grep -Fxq "package:${package}"
}

IFS=',' read -r -a store_list <<<"$STORES"
results=()
for raw in "${store_list[@]}"; do
  store="$(printf '%s' "$raw" | xargs)"
  [[ -n "$store" ]] || continue
  log_step "Installing default store: $store" >&2
  apk_path=""
  package_name="$(package_for_store "$store" || true)"
  case "$store" in
    f-droid) apk_path="$(download_fdroid)" ;;
    aurora-store|aurora) apk_path="$(download_aurora_store || true)" ;;
    aptoide) apk_path="$(download_aptoide || true)" ;;
    *)
      warn "Unknown store key: $store"
      results+=("$(jq -cn --arg store "$store" '{store:$store, ok:false, status:"unknown-store"}')")
      continue
      ;;
  esac
  if [[ -z "$apk_path" || ! -f "$apk_path" ]]; then
    warn "Skipping $store; APK could not be resolved."
    results+=("$(jq -cn --arg store "$store" --arg package "${package_name:-}" '{store:$store, package:$package, ok:false, status:"apk-unresolved"}')")
    continue
  fi
  install_stdout=""
  install_stderr=""
  if install_stdout="$(install_apk_file "$apk_path" 2> >(cat >&2))"; then
    if [[ -n "$package_name" ]] && package_installed "$package_name"; then
      results+=("$(jq -cn \
        --arg store "$store" \
        --arg package "$package_name" \
        --arg apk_path "$apk_path" \
        --arg stdout "$install_stdout" \
        '{store:$store, package:$package, ok:true, status:"installed", apk_path:$apk_path, install_stdout:$stdout}')")
    else
      warn "Install command succeeded for $store but package verification failed."
      results+=("$(jq -cn \
        --arg store "$store" \
        --arg package "${package_name:-}" \
        --arg apk_path "$apk_path" \
        --arg stdout "$install_stdout" \
        '{store:$store, package:$package, ok:false, status:"verification-failed", apk_path:$apk_path, install_stdout:$stdout}')")
    fi
  else
    status=$?
    results+=("$(jq -cn \
      --arg store "$store" \
      --arg package "${package_name:-}" \
      --arg apk_path "$apk_path" \
      --argjson exit_code "$status" \
      '{store:$store, package:$package, ok:false, status:"install-failed", apk_path:$apk_path, exit_code:$exit_code}')")
  fi
done

printf '%s\n' "${results[@]}" | jq -cs '{ok: all(.[]; .ok), results: .}'
