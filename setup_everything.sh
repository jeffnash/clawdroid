#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PROJECT_ROOT/scripts/common.sh"

ORIGINAL_ARG_COUNT=$#
INSTALL_SYSTEM_DEPS=0
INSTALL_OPENCLAW=0
INSTALL_HERMES_PLUGIN=0
INIT_WAYDROID=0
ENABLE_ADMIN_TOOL=0
WITH_GAPPS=0
EXTRAS=""
ARM_TRANSLATION_EXPLICIT=0
[[ -n "${OPENCLAW_ANDROID_ARM_TRANSLATION:-}" ]] && ARM_TRANSLATION_EXPLICIT=1
ARM_TRANSLATION="${OPENCLAW_ANDROID_ARM_TRANSLATION:-libndk}"
SKIP_SDK_INSTALL=0
SKIP_APK_BUILD=0
SKIP_SYSTEMD=0
SUDO_MODE="inline"
INSTALL_DEFAULT_STORES=1
DEVICE_PROFILE="${OPENCLAW_ANDROID_DEVICE_PROFILE:-samsung-galaxy-s24-ultra}"
WINDOW_BACKEND="${OPENCLAW_ANDROID_WINDOW_BACKEND:-auto}"
STORE_INSTALL_REPORT=""
INTERACTIVE=0
YES=0
DAEMON_BASE_URL="${OPENCLAW_ANDROID_DAEMON_BASE_URL:-http://127.0.0.1:48765}"
OPENCLAW_HOME_WAS_SET=0
HERMES_HOME_WAS_SET=0
[[ -n "${OPENCLAW_HOME:-}" ]] && OPENCLAW_HOME_WAS_SET=1
[[ -n "${HERMES_HOME:-}" ]] && HERMES_HOME_WAS_SET=1
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-}"
OPENCLAW_EXTENSIONS_DIR="${OPENCLAW_EXTENSIONS_DIR:-}"
HERMES_HOME_OVERRIDE="${HERMES_HOME:-}"
HERMES_TARGET_USER=""
HERMES_SYSTEM=0
RUN_SMOKE_TEST=0
SMOKE_TEST_LAYER="${OPENCLAW_ANDROID_SMOKE_LAYER:-auto}"
SMOKE_TAP_X="${OPENCLAW_ANDROID_SMOKE_TAP_X:-24}"
SMOKE_TAP_Y="${OPENCLAW_ANDROID_SMOKE_TAP_Y:-24}"
SMOKE_VISIBLE_ACTION=1
OPENCLAW_HOME_EXPLICIT=$OPENCLAW_HOME_WAS_SET
OPENCLAW_CONFIG_EXPLICIT=0
OPENCLAW_EXTENSIONS_EXPLICIT=0
HERMES_HOME_EXPLICIT=$HERMES_HOME_WAS_SET
HERMES_TARGET_EXPLICIT=0
WINDOW_BACKEND_EXPLICIT=0
CONFIGURE_LLM_ONLY=0
LLM_PROVIDER="${OPENCLAW_ANDROID_SETUP_LLM_PROVIDER:-auto}"
LLM_MODEL="${OPENCLAW_ANDROID_SETUP_LLM_MODEL:-}"
LLM_BASE_URL="${OPENCLAW_ANDROID_SETUP_LLM_BASE_URL:-}"
LLM_API_KEY_ENV="${OPENCLAW_ANDROID_SETUP_LLM_API_KEY_ENV:-}"
LLM_API_KEY_VALUE="${OPENCLAW_ANDROID_SETUP_LLM_API_KEY:-}"
LLM_API_KEY_WRITE_ENV=""
LLM_CONFIG_PATH="${OPENCLAW_ANDROID_LLM_CONFIG_PATH:-}"
LLM_ENV_FILE="${OPENCLAW_ANDROID_LLM_ENV_FILE:-}"
LLM_PROVIDER_EXPLICIT=0

prompt_text() {
  local prompt="$1"
  local default="${2:-}"
  local answer
  if [[ $YES -eq 1 || ! -t 0 ]]; then
    printf '%s\n' "$default"
    return 0
  fi
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " answer
    printf '%s\n' "${answer:-$default}"
  else
    read -r -p "$prompt: " answer
    printf '%s\n' "$answer"
  fi
}

prompt_secret() {
  local prompt="$1"
  local answer
  if [[ $YES -eq 1 || ! -t 0 ]]; then
    printf '\n'
    return 0
  fi
  read -r -s -p "$prompt: " answer
  printf '\n' >&2
  printf '%s\n' "$answer"
}

can_sudo_noninteractive() {
  [[ "$(id -u)" == "0" ]] && return 0
  command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1
}

prompt_yes_no() {
  local prompt="$1"
  local default="$2"
  local answer suffix
  case "$default" in
    yes) suffix="[Y/n]" ;;
    no) suffix="[y/N]" ;;
    *) fatal "Invalid prompt default: $default" ;;
  esac
  if [[ $YES -eq 1 || ! -t 0 ]]; then
    [[ "$default" == "yes" ]]
    return
  fi
  while true; do
    read -r -p "$prompt $suffix " answer
    answer="${answer,,}"
    case "$answer" in
      "") [[ "$default" == "yes" ]]; return ;;
      y|yes) return 0 ;;
      n|no) return 1 ;;
      *) printf 'Please answer yes or no.\n' ;;
    esac
  done
}

prompt_choice() {
  local prompt="$1"
  local default="$2"
  shift 2
  local choices=("$@")
  local answer choice idx
  if [[ $YES -eq 1 || ! -t 0 ]]; then
    printf '%s\n' "$default"
    return 0
  fi
  printf '\n%s\n' "$prompt" >&2
  for idx in "${!choices[@]}"; do
    choice="${choices[$idx]}"
    if [[ "$choice" == "$default" ]]; then
      printf '  %d) %s  [default]\n' "$((idx + 1))" "$choice" >&2
    else
      printf '  %d) %s\n' "$((idx + 1))" "$choice" >&2
    fi
  done
  while true; do
    read -r -p "Select an option [${default}]: " answer
    answer="${answer:-$default}"
    for idx in "${!choices[@]}"; do
      choice="${choices[$idx]}"
      if [[ "$answer" == "$choice" || "$answer" == "$((idx + 1))" ]]; then
        printf '%s\n' "$choice"
        return 0
      fi
    done
    printf 'Please choose one of: %s.\n' "${choices[*]}" >&2
  done
}

append_unique() {
  local array_name="$1"
  local value="${2:-}"
  local -n target_array="$array_name"
  [[ -n "$value" ]] || return 0
  value="${value%/}"
  local existing
  for existing in "${target_array[@]}"; do
    [[ "$existing" == "$value" ]] && return 0
  done
  target_array+=("$value")
}

home_for_user_name() {
  getent passwd "$1" | cut -d: -f6
}

system_hermes_home() {
  local hermes_home
  command -v systemctl >/dev/null 2>&1 || return 0
  hermes_home="$(systemctl show -p Environment --value hermes-gateway.service 2>/dev/null \
    | tr ' ' '\n' \
    | sed -n 's/^HERMES_HOME=//p' \
    | head -n 1 || true)"
  printf '%s' "$hermes_home"
}

user_hermes_home() {
  local hermes_home
  command -v systemctl >/dev/null 2>&1 || return 0
  hermes_home="$(systemctl --user show -p Environment --value hermes-gateway.service 2>/dev/null \
    | tr ' ' '\n' \
    | sed -n 's/^HERMES_HOME=//p' \
    | head -n 1 || true)"
  printf '%s' "$hermes_home"
}

openclaw_cli_config_file() {
  local config_file
  command -v openclaw >/dev/null 2>&1 || return 0
  config_file="$(OPENCLAW_HOME="$OPENCLAW_HOME" openclaw config file 2>/dev/null || true)"
  printf '%s' "$config_file"
}

discover_openclaw_homes() {
  local candidates=()
  local config_file path
  append_unique candidates "$OPENCLAW_HOME"
  config_file="$(openclaw_cli_config_file)"
  if [[ -n "$config_file" ]]; then
    append_unique candidates "$(dirname "$config_file")"
  fi
  append_unique candidates "$HOME/.openclaw"
  [[ -d /root/.openclaw ]] && append_unique candidates "/root/.openclaw"
  for path in /home/*/.openclaw; do
    [[ -d "$path" ]] && append_unique candidates "$path"
  done
  printf '%s\n' "${candidates[@]}"
}

discover_openclaw_configs() {
  local candidates=()
  local config_file path
  [[ -n "$OPENCLAW_CONFIG_PATH" ]] && append_unique candidates "$OPENCLAW_CONFIG_PATH"
  config_file="$(openclaw_cli_config_file)"
  [[ -n "$config_file" ]] && append_unique candidates "$config_file"
  append_unique candidates "$OPENCLAW_HOME/openclaw.json"
  append_unique candidates "$HOME/.openclaw/openclaw.json"
  [[ -f /root/.openclaw/openclaw.json ]] && append_unique candidates "/root/.openclaw/openclaw.json"
  for path in /home/*/.openclaw/openclaw.json; do
    [[ -f "$path" ]] && append_unique candidates "$path"
  done
  printf '%s\n' "${candidates[@]}"
}

discover_hermes_homes() {
  local candidates=()
  local detected path
  [[ -n "$HERMES_HOME_OVERRIDE" ]] && append_unique candidates "$HERMES_HOME_OVERRIDE"
  detected="$(user_hermes_home)"
  [[ -n "$detected" ]] && append_unique candidates "$detected"
  detected="$(system_hermes_home)"
  [[ -n "$detected" ]] && append_unique candidates "$detected"
  append_unique candidates "$HOME/.hermes"
  [[ -d /root/.hermes ]] && append_unique candidates "/root/.hermes"
  for path in /home/*/.hermes; do
    [[ -d "$path" ]] && append_unique candidates "$path"
  done
  printf '%s\n' "${candidates[@]}"
}

prompt_path_choice() {
  local label="$1"
  local default_value="$2"
  shift 2
  local choices=("$@")
  local answer custom idx
  if [[ $YES -eq 1 || ! -t 0 ]]; then
    printf '%s\n' "$default_value"
    return 0
  fi

  printf '\n%s\n' "$label" >&2
  for idx in "${!choices[@]}"; do
    if [[ "${choices[$idx]}" == "$default_value" ]]; then
      printf '  %d) %s  [default]\n' "$((idx + 1))" "${choices[$idx]}" >&2
    else
      printf '  %d) %s\n' "$((idx + 1))" "${choices[$idx]}" >&2
    fi
  done
  printf '  c) Enter a custom path\n' >&2
  while true; do
    read -r -p "Select a path [1]: " answer
    answer="${answer:-1}"
    if [[ "$answer" == "c" || "$answer" == "C" ]]; then
      read -r -p "Path: " custom
      [[ -n "$custom" ]] && printf '%s\n' "$custom" && return 0
      printf 'Please enter a non-empty path.\n' >&2
      continue
    fi
    if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#choices[@]} )); then
      printf '%s\n' "${choices[$((answer - 1))]}"
      return 0
    fi
    printf 'Please choose one of the listed paths.\n' >&2
  done
}

prompt_optional_path_choice() {
  local label="$1"
  local default_value="$2"
  shift 2
  local choices=("$@")
  local answer custom idx
  if [[ $YES -eq 1 || ! -t 0 ]]; then
    printf '%s\n' "$default_value"
    return 0
  fi

  printf '\n%s\n' "$label" >&2
  printf '  0) Auto-detect/default  [default]\n' >&2
  for idx in "${!choices[@]}"; do
    printf '  %d) %s\n' "$((idx + 1))" "${choices[$idx]}" >&2
  done
  printf '  c) Enter a custom path\n' >&2
  while true; do
    read -r -p "Select a path [0]: " answer
    answer="${answer:-0}"
    if [[ "$answer" == "0" ]]; then
      printf '%s\n' "$default_value"
      return 0
    fi
    if [[ "$answer" == "c" || "$answer" == "C" ]]; then
      read -r -p "Path: " custom
      [[ -n "$custom" ]] && printf '%s\n' "$custom" && return 0
      printf 'Please enter a non-empty path.\n' >&2
      continue
    fi
    if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#choices[@]} )); then
      printf '%s\n' "${choices[$((answer - 1))]}"
      return 0
    fi
    printf 'Please choose one of the listed paths.\n' >&2
  done
}

configure_guided_paths() {
  local choices=()
  local path

  if [[ $OPENCLAW_HOME_EXPLICIT -eq 0 ]]; then
    mapfile -t choices < <(discover_openclaw_homes)
    [[ ${#choices[@]} -eq 0 ]] && choices=("$OPENCLAW_HOME")
    OPENCLAW_HOME="$(prompt_path_choice "OpenClaw home / install profile:" "$OPENCLAW_HOME" "${choices[@]}")"
  fi

  if [[ $OPENCLAW_CONFIG_EXPLICIT -eq 0 ]]; then
    mapfile -t choices < <(discover_openclaw_configs)
    if [[ ${#choices[@]} -gt 1 ]]; then
      OPENCLAW_CONFIG_PATH="$(prompt_optional_path_choice "OpenClaw config file to update:" "$OPENCLAW_CONFIG_PATH" "${choices[@]}")"
    fi
  fi

  if [[ $OPENCLAW_EXTENSIONS_EXPLICIT -eq 0 ]]; then
    OPENCLAW_EXTENSIONS_DIR="${OPENCLAW_EXTENSIONS_DIR:-$OPENCLAW_HOME/extensions}"
  fi

  if [[ $HERMES_HOME_EXPLICIT -eq 0 ]]; then
    mapfile -t choices < <(discover_hermes_homes)
    [[ ${#choices[@]} -eq 0 ]] && choices=("$HOME/.hermes")
    HERMES_HOME_OVERRIDE="$(prompt_path_choice "Hermes home for the clawdroid plugin:" "${HERMES_HOME_OVERRIDE:-${choices[0]}}" "${choices[@]}")"
  fi

  if [[ $HERMES_TARGET_EXPLICIT -eq 0 && -n "$HERMES_HOME_OVERRIDE" ]]; then
    case "$HERMES_HOME_OVERRIDE" in
      /root/.hermes) HERMES_SYSTEM=1 ;;
      /home/*/.hermes)
        path="${HERMES_HOME_OVERRIDE#/home/}"
        HERMES_TARGET_USER="${path%%/*}"
        ;;
    esac
  fi
}

llm_config_path_default() {
  printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/openclaw-android-waydroid/llm.json"
}

llm_env_file_default() {
  printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/openclaw-android-waydroid/env"
}

env_file_has_key() {
  local file="$1"
  local key_name="$2"
  [[ -f "$file" ]] || return 1
  grep -Eq "^${key_name}=" "$file"
}

env_file_key_value() {
  local file="$1"
  local key_name="$2"
  local line value
  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == "$key_name="* ]] || continue
    value="${line#*=}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
    return 0
  done < "$file"
  return 1
}

existing_llm_key_source() {
  local key_name="$1"
  local file
  for file in \
    "${LLM_ENV_FILE:-$(llm_env_file_default)}" \
    "${HERMES_HOME_OVERRIDE:-$HOME/.hermes}/.env" \
    "$HOME/.hermes/.env"; do
    if env_file_has_key "$file" "$key_name"; then
      printf '%s' "$file"
      return 0
    fi
  done
  [[ -n "${!key_name:-}" ]] && { printf 'environment'; return 0; }
  return 1
}

llm_key_source_is_service_loaded() {
  local source_path="$1"
  case "$source_path" in
    "$(llm_env_file_default)"|"${LLM_ENV_FILE:-$(llm_env_file_default)}"|"$HOME/.hermes/.env") return 0 ;;
    *) return 1 ;;
  esac
}

llm_api_key_env_candidates() {
  [[ -n "$LLM_API_KEY_ENV" ]] && printf '%s\n' "$LLM_API_KEY_ENV"
  if [[ "$LLM_PROVIDER" == "openrouter" ]]; then
    [[ "$LLM_API_KEY_ENV" != "OPENCLAW_ANDROID_OPENROUTER_API_KEY" ]] && printf '%s\n' "OPENCLAW_ANDROID_OPENROUTER_API_KEY"
    [[ "$LLM_API_KEY_ENV" != "OPENROUTER_API_KEY" ]] && printf '%s\n' "OPENROUTER_API_KEY"
  fi
}

persist_environment_llm_key_if_needed() {
  local key_name key_source
  [[ -z "$LLM_API_KEY_VALUE" ]] || return 0
  [[ -n "$LLM_API_KEY_ENV" ]] || return 0
  while IFS= read -r key_name; do
    [[ -n "$key_name" ]] || continue
    key_source="$(existing_llm_key_source "$key_name" || true)"
    if [[ "$key_source" == "environment" && -n "${!key_name:-}" ]]; then
      LLM_API_KEY_VALUE="${!key_name}"
      LLM_API_KEY_WRITE_ENV="$key_name"
      return 0
    elif [[ -n "$key_source" ]] && ! llm_key_source_is_service_loaded "$key_source"; then
      LLM_API_KEY_VALUE="$(env_file_key_value "$key_source" "$key_name" || true)"
      if [[ -n "$LLM_API_KEY_VALUE" ]]; then
        LLM_API_KEY_WRITE_ENV="$key_name"
        return 0
      fi
    fi
  done < <(llm_api_key_env_candidates)
}

llm_api_key_is_configured() {
  local key_name
  [[ -n "$LLM_API_KEY_VALUE" ]] && return 0
  [[ -n "$LLM_API_KEY_ENV" ]] || return 1
  while IFS= read -r key_name; do
    [[ -n "$key_name" ]] || continue
    if existing_llm_key_source "$key_name" >/dev/null; then
      return 0
    fi
  done < <(llm_api_key_env_candidates)
  return 1
}

configure_guided_llm() {
  local provider_source key_source
  if [[ "$LLM_PROVIDER" == "auto" ]]; then
    if prompt_yes_no "Configure Android visual fallback with UI-TARS/OpenRouter or a local vision model?" yes; then
      LLM_PROVIDER="$(prompt_choice "Android visual fallback provider:" openrouter openrouter local custom skip)"
    else
      LLM_PROVIDER="skip"
    fi
  fi

  case "$LLM_PROVIDER" in
    openrouter)
      LLM_MODEL="${LLM_MODEL:-$(prompt_text "OpenRouter vision model" "bytedance/ui-tars-1.5-7b")}"
      LLM_API_KEY_ENV="${LLM_API_KEY_ENV:-$(prompt_text "OpenRouter API key environment variable" "OPENROUTER_API_KEY")}"
      key_source="$(existing_llm_key_source "$LLM_API_KEY_ENV" || true)"
      if [[ -z "$key_source" && "$LLM_API_KEY_ENV" != "OPENCLAW_ANDROID_OPENROUTER_API_KEY" ]]; then
        key_source="$(existing_llm_key_source "OPENCLAW_ANDROID_OPENROUTER_API_KEY" || true)"
      fi
      if [[ -z "$key_source" ]] && prompt_yes_no "Paste an OpenRouter API key now? It will be saved with 0600 permissions." yes; then
        LLM_API_KEY_VALUE="$(prompt_secret "OpenRouter API key")"
      fi
      ;;
    local)
      LLM_BASE_URL="${LLM_BASE_URL:-$(prompt_text "Local OpenAI-compatible vision endpoint" "http://127.0.0.1:8000/v1")}"
      LLM_MODEL="${LLM_MODEL:-$(prompt_text "Local vision model name" "bytedance/ui-tars-1.5-7b")}"
      LLM_API_KEY_ENV="${LLM_API_KEY_ENV:-$(prompt_text "Optional local endpoint API key env var, blank for none" "")}"
      if [[ -n "$LLM_API_KEY_ENV" && -z "$(existing_llm_key_source "$LLM_API_KEY_ENV" || true)" ]] \
        && prompt_yes_no "Paste the local endpoint API key now? It will be saved with 0600 permissions." no; then
        LLM_API_KEY_VALUE="$(prompt_secret "Local endpoint API key")"
      fi
      ;;
    custom)
      LLM_BASE_URL="${LLM_BASE_URL:-$(prompt_text "OpenAI-compatible vision endpoint URL" "")}"
      LLM_MODEL="${LLM_MODEL:-$(prompt_text "Vision model name" "")}"
      LLM_API_KEY_ENV="${LLM_API_KEY_ENV:-$(prompt_text "Optional API key env var, blank for none" "OPENCLAW_ANDROID_VISION_API_KEY")}"
      if [[ -n "$LLM_API_KEY_ENV" && -z "$(existing_llm_key_source "$LLM_API_KEY_ENV" || true)" ]] \
        && prompt_yes_no "Paste the API key now? It will be saved with 0600 permissions." no; then
        LLM_API_KEY_VALUE="$(prompt_secret "Vision endpoint API key")"
      fi
      ;;
    skip) ;;
    *) fatal "Invalid LLM provider: $LLM_PROVIDER (expected: auto, openrouter, local, custom, or skip)" ;;
  esac
}

configure_llm_runtime() {
  [[ "$LLM_PROVIDER" != "auto" && "$LLM_PROVIDER" != "skip" ]] || return 0

  LLM_CONFIG_PATH="${LLM_CONFIG_PATH:-$(llm_config_path_default)}"
  LLM_ENV_FILE="${LLM_ENV_FILE:-$(llm_env_file_default)}"
  mkdir -p "$(dirname "$LLM_CONFIG_PATH")" "$(dirname "$LLM_ENV_FILE")"

  case "$LLM_PROVIDER" in
    openrouter)
      LLM_MODEL="${LLM_MODEL:-bytedance/ui-tars-1.5-7b}"
      LLM_API_KEY_ENV="${LLM_API_KEY_ENV:-OPENROUTER_API_KEY}"
      LLM_BASE_URL="${LLM_BASE_URL:-https://openrouter.ai/api/v1}"
      ;;
    local)
      LLM_MODEL="${LLM_MODEL:-bytedance/ui-tars-1.5-7b}"
      LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8000/v1}"
      if [[ -n "$LLM_API_KEY_VALUE" && -z "$LLM_API_KEY_ENV" ]]; then
        LLM_API_KEY_ENV="OPENCLAW_ANDROID_LOCAL_LLM_API_KEY"
      fi
      ;;
    custom)
      [[ -n "$LLM_BASE_URL" ]] || fatal "--llm-base-url is required when --llm-provider custom"
      [[ -n "$LLM_MODEL" ]] || fatal "--llm-model is required when --llm-provider custom"
      if [[ -n "$LLM_API_KEY_VALUE" && -z "$LLM_API_KEY_ENV" ]]; then
        LLM_API_KEY_ENV="OPENCLAW_ANDROID_VISION_API_KEY"
      fi
      ;;
  esac

  persist_environment_llm_key_if_needed

  python3 - "$LLM_PROVIDER" "$LLM_MODEL" "$LLM_BASE_URL" "$LLM_API_KEY_ENV" "$LLM_CONFIG_PATH" <<'PY'
import json
import sys

provider, model, base_url, api_key_env, config_path = sys.argv[1:]
provider_name = "openrouter" if provider == "openrouter" else "local"
provider_config = {
    "base_url": base_url.rstrip("/"),
    "api": "openai-completions",
    "models": [{"id": model, "name": model, "input": ["text", "image"]}],
}
if api_key_env:
    env_names = [api_key_env]
    if provider == "openrouter":
        env_names = ["OPENCLAW_ANDROID_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"]
        if api_key_env not in env_names:
            env_names.insert(0, api_key_env)
    provider_config["api_key_env"] = env_names
else:
    provider_config["api_key"] = "local"

payload = {
    "default_provider": provider_name,
    "default_model": model,
    "providers": {provider_name: provider_config},
}
with open(config_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY

  local key_to_write="${LLM_API_KEY_WRITE_ENV:-$LLM_API_KEY_ENV}"
  if [[ -n "$LLM_API_KEY_VALUE" && -n "$key_to_write" ]]; then
    python3 - "$LLM_ENV_FILE" "$key_to_write" 3<<<"$LLM_API_KEY_VALUE" <<'PY'
import os
import stat
import sys

path, key = sys.argv[1:]
with os.fdopen(3, "r", encoding="utf-8") as secret_handle:
    value = secret_handle.read()
if value.endswith("\n"):
    value = value[:-1]
lines = []
if os.path.exists(path):
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
prefix = f"{key}="
updated = False
next_lines = []
for line in lines:
    if line.startswith(prefix):
        next_lines.append(f"{key}={value}")
        updated = True
    else:
        next_lines.append(line)
if not updated:
    next_lines.append(f"{key}={value}")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write("\n".join(next_lines).rstrip() + "\n")
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
PY
  elif [[ -f "$LLM_ENV_FILE" ]]; then
    chmod 600 "$LLM_ENV_FILE" || true
  fi

  log_step "Configured Android visual fallback"
  printf '  provider: %s\n' "$LLM_PROVIDER"
  printf '  model: %s\n' "$LLM_MODEL"
  printf '  base URL: %s\n' "$LLM_BASE_URL"
  printf '  config: %s\n' "$LLM_CONFIG_PATH"
  if [[ -n "$LLM_API_KEY_ENV" ]]; then
    if llm_api_key_is_configured; then
      printf '  API key env: %s (configured)\n' "$LLM_API_KEY_ENV"
    else
      warn "API key env $LLM_API_KEY_ENV was not found. Add it to $LLM_ENV_FILE or your Hermes env before using LLM vision."
    fi
  fi
}

waydroid_initialized() {
  [[ -f /var/lib/waydroid/waydroid.cfg && -f /var/lib/waydroid/images/system.img && -f /var/lib/waydroid/images/vendor.img ]]
}

run_guided_setup() {
  log_step "Interactive setup"
  cat <<'EOF'
If Android shows an "Allow USB debugging?" prompt during setup, check
"Always allow from this computer" and tap "Allow". Setup waits and retries, but
ADB will stay blocked until that prompt is accepted.
EOF
  configure_guided_paths
  printf '\nDetected desktop session: %s\n' "${XDG_SESSION_TYPE:-unknown}" >&2
  if [[ $WINDOW_BACKEND_EXPLICIT -eq 0 ]]; then
    WINDOW_BACKEND="$(prompt_choice "Waydroid window backend:" "$WINDOW_BACKEND" auto x11 wayland)"
  fi
  if prompt_yes_no "Install/update system packages and Waydroid if needed?" yes; then
    INSTALL_SYSTEM_DEPS=1
  fi
  if ! command -v waydroid >/dev/null 2>&1 || ! waydroid_initialized; then
    if prompt_yes_no "Initialize Waydroid Android images if needed?" yes; then
      INIT_WAYDROID=1
    fi
  elif prompt_yes_no "Re-run Waydroid init if images are already present?" no; then
    INIT_WAYDROID=1
  fi
  if prompt_yes_no "Install/link the Hermes plugin?" yes; then
    INSTALL_HERMES_PLUGIN=1
  fi
  configure_guided_llm
  if prompt_yes_no "Enable the android_admin tool? This allows gated install/recovery operations." no; then
    ENABLE_ADMIN_TOOL=1
  fi
  if ! prompt_yes_no "Install default Android app stores?" yes; then
    INSTALL_DEFAULT_STORES=0
  fi
  if prompt_yes_no "Install Google Play / GApps? This may require Google device certification after setup." no; then
    WITH_GAPPS=1
  fi
  if prompt_yes_no "Run a smoke test after setup, including a harmless visible tap?" yes; then
    RUN_SMOKE_TEST=1
  fi
  if [[ $ARM_TRANSLATION_EXPLICIT -eq 0 ]]; then
    if prompt_yes_no "Install ARM translation support with waydroid_script? This may require sudo." yes; then
      ARM_TRANSLATION="libndk"
    else
      ARM_TRANSLATION="none"
    fi
  fi
  if [[ $INSTALL_SYSTEM_DEPS -eq 1 || $INIT_WAYDROID -eq 1 ]] && ! can_sudo_noninteractive; then
    SUDO_MODE="manual"
    warn "Passwordless sudo is not available; setup will pause and ask you to run the root step manually."
  fi
}

resolve_python_bin() {
  if command -v /usr/bin/python3.12 >/dev/null 2>&1; then
    printf '/usr/bin/python3.12'
  else
    command -v python3
  fi
}

wait_for_adb_device() {
  local serial="$1"
  local attempts="${2:-60}"
  local delay="${3:-2}"
  local state
  local unauthorized_warned=0
  for ((i = 1; i <= attempts; i++)); do
    state="$(adb_state_for_serial "$serial")"
    if [[ "$state" == "device" ]]; then
      return 0
    fi
    if [[ "$state" == "unauthorized" && $unauthorized_warned -eq 0 ]]; then
      warn "Android is waiting for USB debugging approval. In Waydroid, check 'Always allow from this computer' and tap 'Allow'."
      unauthorized_warned=1
    fi
    adb_quick connect "$serial" >/dev/null 2>&1 || true
    sleep "$delay"
  done
  return 1
}

adb_state_for_serial() {
  local serial="$1"
  local output state
  output="$(adb_quick -s "$serial" get-state 2>&1 | tr -d '\r' || true)"
  if grep -qi 'unauthorized' <<<"$output"; then
    printf 'unauthorized'
    return 0
  fi
  state="$(awk 'NF { line = $0 } END { print line }' <<<"$output")"
  printf '%s' "${state:-unknown}"
}

wait_for_waydroid_boot() {
  local serial="$1"
  local attempts="${2:-90}"
  local delay="${3:-2}"
  local boot_completed
  for ((i = 1; i <= attempts; i++)); do
    boot_completed="$(adb_quick -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
    if [[ "$boot_completed" == "1" ]]; then
      return 0
    fi
    sleep "$delay"
  done
  return 1
}

wait_for_waydroid_ip() {
  local attempts="${1:-90}"
  local delay="${2:-2}"
  local ip
  for ((i = 1; i <= attempts; i++)); do
    ip="$("$PROJECT_ROOT/scripts/install_waydroid.sh" --print-ip 2>/dev/null || true)"
    if [[ -n "$ip" ]]; then
      printf '%s\n' "$ip"
      return 0
    fi
    sleep "$delay"
  done
  return 1
}

wait_for_android_ready() {
  local serial="$1"
  local attempts="${2:-120}"
  local delay="${3:-2}"
  local state boot_completed
  local unauthorized_warned=0
  for ((i = 1; i <= attempts; i++)); do
    adb_quick connect "$serial" >/dev/null 2>&1 || true
    state="$(adb_state_for_serial "$serial")"
    if [[ "$state" != "device" ]]; then
      if [[ "$state" == "unauthorized" && $unauthorized_warned -eq 0 ]]; then
        warn "Android is waiting for USB debugging approval. In Waydroid, check 'Always allow from this computer' and tap 'Allow'."
        unauthorized_warned=1
      fi
      sleep "$delay"
      continue
    fi

    boot_completed="$(adb_quick -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
    if [[ "$boot_completed" != "1" ]]; then
      sleep "$delay"
      continue
    fi

    if adb_quick -s "$serial" shell 'cmd package list packages android >/dev/null 2>&1' >/dev/null 2>&1 &&
       adb_quick -s "$serial" shell 'settings get secure enabled_accessibility_services >/dev/null 2>&1' >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  return 1
}

reset_waydroid_runtime() {
  local status
  log_step "Resetting Waydroid runtime"
  if "$PROJECT_ROOT/scripts/waydroid_supervisor_ctl.sh" reset >/dev/null 2>&1; then
    return 0
  fi

  timeout 30s waydroid session stop >/dev/null 2>&1 || true
  sleep 2
  WAYLAND_DISPLAY="${OPENCLAW_ANDROID_WESTON_SOCKET:-${WAYLAND_DISPLAY:-wayland-1}}" timeout 90s waydroid session start >/dev/null 2>&1 &
  sleep 10
  status="$(timeout 5s waydroid status 2>/dev/null || true)"
  grep -q $'^Session:\tRUNNING' <<<"$status" && grep -q $'^Container:\tRUNNING' <<<"$status"
}

reboot_waydroid_guest() {
  local serial="$1"
  log_step "Rebooting the Waydroid guest so the accessibility service binds on fresh boot"
  adb_quick -s "$serial" reboot || true
  if ! wait_for_android_ready "$serial" 90 2; then
    warn "ADB reboot did not return cleanly; resetting the Waydroid runtime"
    reset_waydroid_runtime || return 1
    WAYDROID_IP="$(wait_for_waydroid_ip 90 2 || true)"
    [[ -n "$WAYDROID_IP" ]] || return 1
    ADB_SERIAL="$WAYDROID_IP:5555"
    serial="$ADB_SERIAL"
    wait_for_android_ready "$serial" 180 2 || return 1
  fi
  sleep 5
}

usage() {
  cat <<'EOF'
Usage: ./setup_everything.sh [options]

  --install-system-deps   Install OS packages when supported
  --install-openclaw      Install OpenClaw if missing
  --install-hermes-plugin Install/link the Hermes plugin into $HERMES_HOME/plugins
  --init-waydroid         Run waydroid init if needed
  --extras LIST           Comma-separated Waydroid extras (libndk,microg,gapps,libhoudini,...)
  --arm-translation NAME  ARM translation extra: libndk (default), libhoudini, both, or none
  --with-gapps            Add gapps to the extras set
  --enable-admin-tool     Allow the android_admin tool in OpenClaw config
  --sudo-mode MODE        Root-step handling: inline (default) or manual
  --skip-sdk-install      Skip Android SDK bootstrap
  --skip-apk-build        Skip building the companion APK
  --skip-systemd          Do not install the user systemd daemon service
  --skip-default-stores   Do not install default app stores (F-Droid, Aurora, Aptoide)
  --device-profile NAME   Android device profile to apply (default: samsung-galaxy-s24-ultra)
  --window-backend MODE   Windowed UI backend: auto, x11, or wayland (default: auto)
  --daemon-base-url URL   Daemon URL for OpenClaw/Hermes config (default: http://127.0.0.1:48765)
  --configure-llm-only    Only configure Android visual fallback, then exit
  --llm-provider NAME     Visual fallback provider: openrouter, local, custom, or skip
  --llm-model NAME        Vision model name (default for OpenRouter/local: bytedance/ui-tars-1.5-7b)
  --llm-base-url URL      OpenAI-compatible base URL for local/custom providers
  --llm-api-key-env NAME  Environment variable containing the provider API key
  --llm-api-key VALUE     Save an API key into the Clawdroid env file (prefer interactive entry)
  --llm-config PATH       LLM config file to write (default: ~/.config/openclaw-android-waydroid/llm.json)
  --llm-env-file PATH     Env file for saved API keys (default: ~/.config/openclaw-android-waydroid/env)
  --openclaw-home PATH    OpenClaw profile home (default: $OPENCLAW_HOME or ~/.openclaw)
  --openclaw-config PATH  OpenClaw config file to update
  --openclaw-extensions-dir PATH
                          Directory for CLI-less OpenClaw plugin links
  --hermes-home PATH      Hermes profile home for plugin install
  --hermes-user NAME      Install the Hermes plugin for this user
  --hermes-system         Install the Hermes plugin into root/system Hermes
  --smoke-test            Run daemon/Hermes smoke checks after setup
  --smoke-layer NAME      Smoke layer: auto, daemon, hermes, or openclaw (default: auto)
  --smoke-tap X,Y         Coordinate for the visible tap smoke check (default: 24,24)
  --no-smoke-visible-action
                          Skip the visible tap during smoke checks
  --interactive           Ask guided setup questions
  --yes                   Use guided defaults without prompting
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-system-deps) INSTALL_SYSTEM_DEPS=1 ;;
    --install-openclaw) INSTALL_OPENCLAW=1 ;;
    --install-hermes-plugin) INSTALL_HERMES_PLUGIN=1 ;;
    --init-waydroid) INIT_WAYDROID=1 ;;
    --extras) require_option_value "$1" "${2-}"; EXTRAS="$2"; shift ;;
    --arm-translation) require_option_value "$1" "${2-}"; ARM_TRANSLATION="$2"; ARM_TRANSLATION_EXPLICIT=1; shift ;;
    --with-gapps) WITH_GAPPS=1 ;;
    --enable-admin-tool) ENABLE_ADMIN_TOOL=1 ;;
    --sudo-mode) require_option_value "$1" "${2-}"; SUDO_MODE="$2"; shift ;;
    --skip-sdk-install) SKIP_SDK_INSTALL=1 ;;
    --skip-apk-build) SKIP_APK_BUILD=1 ;;
    --skip-systemd) SKIP_SYSTEMD=1 ;;
    --skip-default-stores) INSTALL_DEFAULT_STORES=0 ;;
    --device-profile) require_option_value "$1" "${2-}"; DEVICE_PROFILE="$2"; shift ;;
    --window-backend) require_option_value "$1" "${2-}"; WINDOW_BACKEND="$2"; WINDOW_BACKEND_EXPLICIT=1; shift ;;
    --daemon-base-url) require_option_value "$1" "${2-}"; DAEMON_BASE_URL="$2"; shift ;;
    --configure-llm-only) CONFIGURE_LLM_ONLY=1 ;;
    --llm-provider) require_option_value "$1" "${2-}"; LLM_PROVIDER="$2"; LLM_PROVIDER_EXPLICIT=1; shift ;;
    --llm-model) require_option_value "$1" "${2-}"; LLM_MODEL="$2"; shift ;;
    --llm-base-url) require_option_value "$1" "${2-}"; LLM_BASE_URL="$2"; shift ;;
    --llm-api-key-env) require_option_value "$1" "${2-}"; LLM_API_KEY_ENV="$2"; shift ;;
    --llm-api-key) require_option_value "$1" "${2-}"; LLM_API_KEY_VALUE="$2"; shift ;;
    --llm-config) require_option_value "$1" "${2-}"; LLM_CONFIG_PATH="$2"; shift ;;
    --llm-env-file) require_option_value "$1" "${2-}"; LLM_ENV_FILE="$2"; shift ;;
    --openclaw-home) require_option_value "$1" "${2-}"; OPENCLAW_HOME="$2"; OPENCLAW_HOME_EXPLICIT=1; shift ;;
    --openclaw-config) require_option_value "$1" "${2-}"; OPENCLAW_CONFIG_PATH="$2"; OPENCLAW_CONFIG_EXPLICIT=1; shift ;;
    --openclaw-extensions-dir) require_option_value "$1" "${2-}"; OPENCLAW_EXTENSIONS_DIR="$2"; OPENCLAW_EXTENSIONS_EXPLICIT=1; shift ;;
    --hermes-home) require_option_value "$1" "${2-}"; HERMES_HOME_OVERRIDE="$2"; HERMES_HOME_EXPLICIT=1; shift ;;
    --hermes-user) require_option_value "$1" "${2-}"; HERMES_TARGET_USER="$2"; HERMES_TARGET_EXPLICIT=1; shift ;;
    --hermes-system) HERMES_SYSTEM=1; HERMES_TARGET_EXPLICIT=1 ;;
    --smoke-test) RUN_SMOKE_TEST=1 ;;
    --smoke-layer) require_option_value "$1" "${2-}"; SMOKE_TEST_LAYER="$2"; shift ;;
    --smoke-tap)
      require_option_value "$1" "${2-}"
      if [[ "$2" == *,* ]]; then
        SMOKE_TAP_X="${2%,*}"
        SMOKE_TAP_Y="${2#*,}"
      else
        require_option_value "$1" "${3-}"
        SMOKE_TAP_X="$2"
        SMOKE_TAP_Y="$3"
        shift
      fi
      shift
      ;;
    --no-smoke-visible-action) SMOKE_VISIBLE_ACTION=0 ;;
    --interactive) INTERACTIVE=1 ;;
    --yes) YES=1; INTERACTIVE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ $ORIGINAL_ARG_COUNT -eq 0 && -t 0 ]]; then
  INTERACTIVE=1
fi
if [[ -n "$HERMES_TARGET_USER" ]] && ! id "$HERMES_TARGET_USER" >/dev/null 2>&1; then
  fatal "Unknown --hermes-user: $HERMES_TARGET_USER"
fi
if [[ $HERMES_HOME_EXPLICIT -eq 0 && -n "$HERMES_TARGET_USER" ]]; then
  HERMES_HOME_OVERRIDE="$(home_for_user_name "$HERMES_TARGET_USER")/.hermes"
  HERMES_HOME_EXPLICIT=1
fi
if [[ $HERMES_HOME_EXPLICIT -eq 0 && $HERMES_SYSTEM -eq 1 ]]; then
  HERMES_HOME_OVERRIDE="/root/.hermes"
  HERMES_HOME_EXPLICIT=1
fi
if [[ $INTERACTIVE -eq 1 ]]; then
  run_guided_setup
fi

if [[ $CONFIGURE_LLM_ONLY -eq 1 && "$LLM_PROVIDER" == "auto" ]]; then
  if [[ -t 0 && $YES -eq 0 ]]; then
    configure_guided_paths
    configure_guided_llm
  else
    LLM_PROVIDER="openrouter"
  fi
fi

OPENCLAW_EXTENSIONS_DIR="${OPENCLAW_EXTENSIONS_DIR:-$OPENCLAW_HOME/extensions}"

case "$SMOKE_TEST_LAYER" in
  auto|daemon|hermes|openclaw) ;;
  *) fatal "Invalid --smoke-layer value: $SMOKE_TEST_LAYER (expected: auto, daemon, hermes, or openclaw)" ;;
esac

if ! [[ "$SMOKE_TAP_X" =~ ^[0-9]+$ && "$SMOKE_TAP_Y" =~ ^[0-9]+$ ]]; then
  fatal "--smoke-tap coordinates must be non-negative integers"
fi

case "$SUDO_MODE" in
  inline|manual) ;;
  *) fatal "Invalid --sudo-mode value: $SUDO_MODE (expected: inline or manual)" ;;
esac

case "$WINDOW_BACKEND" in
  auto|x11|wayland) ;;
  *) fatal "Invalid --window-backend value: $WINDOW_BACKEND (expected: auto, x11, or wayland)" ;;
esac

case "$LLM_PROVIDER" in
  auto|openrouter|local|custom|skip) ;;
  *) fatal "Invalid --llm-provider value: $LLM_PROVIDER (expected: auto, openrouter, local, custom, or skip)" ;;
esac

if [[ $CONFIGURE_LLM_ONLY -eq 1 ]]; then
  require_cmd python3
  if [[ "$LLM_PROVIDER" != "auto" && "$LLM_PROVIDER" != "skip" ]]; then
    configure_llm_runtime
  fi
  log_step "LLM-only setup complete"
  exit 0
fi

append_extra_once() {
  local extra="$1"
  if [[ -z "$EXTRAS" ]]; then
    EXTRAS="$extra"
    return 0
  fi
  case ",$EXTRAS," in
    *,"$extra",* ) ;;
    * ) EXTRAS="$EXTRAS,$extra" ;;
  esac
}

extra_enabled() {
  local extra="$1"
  case ",$EXTRAS," in
    *,"$extra",*) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ $WITH_GAPPS -eq 1 ]]; then
  append_extra_once "gapps"
fi

case "$ARM_TRANSLATION" in
  libndk|libhoudini|both|none) ;;
  *) fatal "Invalid --arm-translation value: $ARM_TRANSLATION (expected: libndk, libhoudini, both, or none)" ;;
esac

case "$ARM_TRANSLATION" in
  libndk) append_extra_once "libndk" ;;
  libhoudini) append_extra_once "libhoudini" ;;
  both)
    append_extra_once "libndk"
    append_extra_once "libhoudini"
    ;;
  none) ;;
esac

log_step "Project root: $PROJECT_ROOT"
log_step "Resolved install targets"
printf '  OpenClaw home: %s\n' "$OPENCLAW_HOME"
printf '  OpenClaw config: %s\n' "${OPENCLAW_CONFIG_PATH:-auto}"
printf '  OpenClaw extensions: %s\n' "$OPENCLAW_EXTENSIONS_DIR"
printf '  Hermes home: %s\n' "${HERMES_HOME_OVERRIDE:-auto}"
printf '  Hermes target user: %s\n' "${HERMES_TARGET_USER:-auto}"
printf '  Hermes system mode: %s\n' "$([[ $HERMES_SYSTEM -eq 1 ]] && printf yes || printf no)"
printf '  Window backend: %s\n' "$WINDOW_BACKEND"
printf '  Daemon URL: %s\n' "$DAEMON_BASE_URL"

ROOT_ARGS=()
if [[ $INSTALL_SYSTEM_DEPS -eq 1 ]]; then
  ROOT_ARGS+=("--install-system-deps")
fi
if [[ $INIT_WAYDROID -eq 1 ]]; then
  ROOT_ARGS+=("--init-waydroid")
fi
ROOT_ARGS+=("--start-container")

if [[ "$SUDO_MODE" == "manual" ]]; then
  ROOT_CMD=(sudo env "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}" "OPENCLAW_ANDROID_WINDOW_BACKEND=$WINDOW_BACKEND" "$PROJECT_ROOT/scripts/setup_root_sudo.sh" "${ROOT_ARGS[@]}")
  log_step "Manual sudo mode enabled"
  printf 'Run this command in another terminal, then return here:\n  %q' "${ROOT_CMD[0]}"
  for ((i = 1; i < ${#ROOT_CMD[@]}; i++)); do
    printf ' %q' "${ROOT_CMD[i]}"
  done
  printf '\n'
  if [[ -t 0 ]]; then
    read -r -p "Press Enter after the sudo command completes..."
  else
    fatal "Manual sudo mode requires an interactive terminal so the script can pause and resume."
  fi
fi

if [[ $INSTALL_SYSTEM_DEPS -eq 1 ]]; then
  if [[ "$SUDO_MODE" == "inline" ]]; then
    OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND" "$PROJECT_ROOT/scripts/install_waydroid.sh" --install-system-deps
  else
    log_step "Skipping inline system dependency install; expecting prior manual sudo step"
  fi
fi

if [[ $INSTALL_OPENCLAW -eq 1 ]]; then
  if ! command -v openclaw >/dev/null 2>&1; then
    log_step "Installing OpenClaw via the official installer"
    curl_fetch https://openclaw.ai/install.sh | bash -s -- --no-onboard
  else
    log_step "OpenClaw already present: $(command -v openclaw)"
  fi
fi

require_cmd python3
require_cmd curl
require_cmd unzip
require_cmd adb

PYTHON_BIN="$(resolve_python_bin)"

if [[ "$LLM_PROVIDER" != "auto" && "$LLM_PROVIDER" != "skip" ]]; then
  configure_llm_runtime
fi

if ! command -v waydroid >/dev/null 2>&1; then
  fatal "Waydroid is not installed. Re-run with --install-system-deps or install Waydroid manually first."
fi

if [[ $INIT_WAYDROID -eq 1 ]]; then
  if [[ "$SUDO_MODE" == "inline" ]]; then
    OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND" "$PROJECT_ROOT/scripts/install_waydroid.sh" --init-waydroid
  else
    log_step "Skipping inline waydroid init; expecting prior manual sudo step"
  fi
fi

if [[ $SKIP_SYSTEMD -eq 0 ]]; then
  log_step "Installing desktop-session Waydroid supervisor unit"
  OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND" OPENCLAW_ANDROID_LLM_CONFIG_PATH="${LLM_CONFIG_PATH:-}" OPENCLAW_ANDROID_LLM_ENV_FILE="${LLM_ENV_FILE:-}" "$PROJECT_ROOT/scripts/install_user_service.sh"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
  fi
fi

log_step "Ensuring Waydroid is running"
OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND" "$PROJECT_ROOT/scripts/install_waydroid.sh" --start-waydroid

if [[ -n "$EXTRAS" ]]; then
  log_step "Installing requested Waydroid extras: $EXTRAS"
  if [[ "$(id -u)" != "0" ]] && ! can_sudo_noninteractive && [[ ! -t 0 ]]; then
    warn "Skipping Waydroid extras because sudo is unavailable in this noninteractive shell: $EXTRAS"
    warn "Install them later with: sudo \"$PROJECT_ROOT/scripts/install_waydroid_extras.sh\" --extras \"$EXTRAS\""
  else
    "$PROJECT_ROOT/scripts/install_waydroid_extras.sh" --extras "$EXTRAS"
  fi
fi

if [[ $SKIP_SDK_INSTALL -eq 0 ]]; then
  "$PROJECT_ROOT/scripts/bootstrap_android_sdk.sh"
fi

if [[ $SKIP_APK_BUILD -eq 0 ]]; then
  "$PROJECT_ROOT/scripts/build_companion_apk.sh"
fi

log_step "Connecting ADB to Waydroid"
WAYDROID_IP="$(wait_for_waydroid_ip 120 2 || true)"
if [[ -z "$WAYDROID_IP" ]]; then
  fatal "Failed to determine Waydroid IP after waiting for the session to start."
fi
ADB_SERIAL="$WAYDROID_IP:5555"
adb_quick connect "$ADB_SERIAL" || true
if ! wait_for_android_ready "$ADB_SERIAL" 180 2; then
  fatal "Waydroid ADB did not become ready at $ADB_SERIAL."
fi

APK_PATH="$PROJECT_ROOT/android-companion/app/build/outputs/apk/debug/app-debug.apk"
COMPANION_APK_UPDATED=0
if [[ -f "$APK_PATH" ]]; then
  log_step "Installing companion APK into Waydroid"
  if adb_install_cmd -s "$ADB_SERIAL" install -r "$APK_PATH"; then
    COMPANION_APK_UPDATED=1
  elif timeout "${OPENCLAW_ANDROID_ADB_INSTALL_TIMEOUT:-180s}" waydroid app install "$APK_PATH"; then
    COMPANION_APK_UPDATED=1
  else
    warn "Failed to install companion APK from $APK_PATH"
  fi
  sleep 5
else
  warn "APK not found at $APK_PATH; skipping APK installation"
fi

if [[ $COMPANION_APK_UPDATED -eq 1 ]]; then
  wait_for_android_ready "$ADB_SERIAL" 90 2 || true
  if reboot_waydroid_guest "$ADB_SERIAL"; then
    log_step "Waydroid guest rebooted after companion APK update"
  else
    warn "Waydroid guest reboot after companion APK update failed; the accessibility service may stay enabled-but-unbound until the next guest restart"
  fi
fi

log_step "Trying to enable the accessibility service through ADB secure settings"
if ! wait_for_android_ready "$ADB_SERIAL" 180 2; then
  fatal "Waydroid ADB did not become ready after APK installation."
fi
ENABLED_SERVICES="$(adb_quick -s "$ADB_SERIAL" shell settings get secure enabled_accessibility_services 2>/dev/null | tr -d '\r' || true)"
SERVICE_COMPONENT="ai.openclaw.androidbridge/ai.openclaw.androidbridge.OpenClawAccessibilityService"
if [[ "$ENABLED_SERVICES" == "null" || -z "$ENABLED_SERVICES" ]]; then
  NEW_ENABLED="$SERVICE_COMPONENT"
elif [[ ":$ENABLED_SERVICES:" == *":$SERVICE_COMPONENT:"* ]]; then
  NEW_ENABLED="$ENABLED_SERVICES"
else
  NEW_ENABLED="$ENABLED_SERVICES:$SERVICE_COMPONENT"
fi
adb_quick -s "$ADB_SERIAL" shell settings put secure enabled_accessibility_services "$NEW_ENABLED" || true
adb_quick -s "$ADB_SERIAL" shell settings put secure accessibility_enabled 1 || true
adb_quick -s "$ADB_SERIAL" forward tcp:49317 tcp:49317 || true
for ((i = 1; i <= 20; i++)); do
  if curl -fsS -m 5 http://127.0.0.1:49317/health >/dev/null 2>&1; then
    log_step "Accessibility bridge is listening on the forwarded host port"
    break
  fi
  sleep 1
done
adb_quick -s "$ADB_SERIAL" shell input keyevent KEYCODE_WAKEUP || true
adb_quick -s "$ADB_SERIAL" shell wm dismiss-keyguard || true
adb_quick -s "$ADB_SERIAL" shell input keyevent 82 || true
adb_quick -s "$ADB_SERIAL" shell input keyevent KEYCODE_HOME || true
adb_quick -s "$ADB_SERIAL" shell settings put global stay_on_while_plugged_in 7 || true
adb_quick -s "$ADB_SERIAL" shell settings put system screen_off_timeout 2147483647 || true
adb_quick -s "$ADB_SERIAL" shell svc power stayon true || true
adb_quick -s "$ADB_SERIAL" shell locksettings set-disabled true || true
OPENCLAW_ANDROID_ADB_SERIAL="$ADB_SERIAL" OPENCLAW_ANDROID_DEVICE_PROFILE="$DEVICE_PROFILE" "$PROJECT_ROOT/scripts/apply_device_profile.sh" || true

if [[ $INSTALL_DEFAULT_STORES -eq 1 ]]; then
  log_step "Installing default Android app stores"
  STORE_INSTALL_REPORT="$("$PROJECT_ROOT/scripts/install_default_stores.sh")"
  printf '%s\n' "$STORE_INSTALL_REPORT" | jq .
fi

log_step "Creating Python daemon virtualenv"
"$PYTHON_BIN" -m venv "$PROJECT_ROOT/python-daemon/.venv"
"$PROJECT_ROOT/python-daemon/.venv/bin/pip" install -U pip wheel setuptools
"$PROJECT_ROOT/python-daemon/.venv/bin/pip" install -r "$PROJECT_ROOT/python-daemon/requirements.txt"

if command -v openclaw >/dev/null 2>&1; then
  require_cmd pnpm
  log_step "Installing/linking the OpenClaw plugin"
  pnpm -C "$PROJECT_ROOT/openclaw-plugin" install
  OPENCLAW_HOME="$OPENCLAW_HOME" openclaw plugins install -l "$PROJECT_ROOT/openclaw-plugin" || OPENCLAW_HOME="$OPENCLAW_HOME" openclaw plugins install "$PROJECT_ROOT/openclaw-plugin"
  OPENCLAW_HOME="$OPENCLAW_HOME" openclaw plugins enable android-waydroid || true
else
  log_step "Installing/linking the OpenClaw plugin without the OpenClaw CLI"
  mkdir -p "$OPENCLAW_EXTENSIONS_DIR"
  rm -rf "$OPENCLAW_EXTENSIONS_DIR/android-waydroid"
  ln -s "$PROJECT_ROOT/openclaw-plugin" "$OPENCLAW_EXTENSIONS_DIR/android-waydroid"
  warn "OpenClaw CLI is not available; linked the plugin directly into $OPENCLAW_EXTENSIONS_DIR"
fi

if [[ $INSTALL_HERMES_PLUGIN -eq 1 ]]; then
  log_step "Installing/linking the Hermes plugin"
  HERMES_INSTALL_ARGS=()
  [[ -n "$HERMES_TARGET_USER" ]] && HERMES_INSTALL_ARGS+=(--user "$HERMES_TARGET_USER")
  [[ $HERMES_SYSTEM -eq 1 ]] && HERMES_INSTALL_ARGS+=(--system)
  if [[ -n "$HERMES_HOME_OVERRIDE" ]]; then
    HERMES_HOME="$HERMES_HOME_OVERRIDE" "$PROJECT_ROOT/scripts/install_hermes_plugin.sh" "${HERMES_INSTALL_ARGS[@]}"
  else
    "$PROJECT_ROOT/scripts/install_hermes_plugin.sh" "${HERMES_INSTALL_ARGS[@]}"
  fi
fi

log_step "Configuring OpenClaw"
OPENCLAW_CONFIG_ARGS=(
  --plugin-id android-waydroid
  --daemon-base-url "$DAEMON_BASE_URL"
  --adb-serial "$ADB_SERIAL"
)
[[ $ENABLE_ADMIN_TOOL -eq 1 ]] && OPENCLAW_CONFIG_ARGS+=(--enable-admin-tool)
[[ -n "$OPENCLAW_CONFIG_PATH" ]] && OPENCLAW_CONFIG_ARGS+=(--config "$OPENCLAW_CONFIG_PATH")
OPENCLAW_HOME="$OPENCLAW_HOME" "$PROJECT_ROOT/python-daemon/.venv/bin/python" "$PROJECT_ROOT/scripts/configure_openclaw.py" "${OPENCLAW_CONFIG_ARGS[@]}"
if command -v openclaw >/dev/null 2>&1 && [[ -z "$OPENCLAW_CONFIG_PATH" ]]; then
  OPENCLAW_HOME="$OPENCLAW_HOME" openclaw config validate || true
elif command -v openclaw >/dev/null 2>&1; then
  warn "OpenClaw config path was overridden; skipped OpenClaw CLI validation of the default profile"
else
  warn "OpenClaw CLI is not available; skipped OpenClaw config validation"
fi

if [[ $SKIP_SYSTEMD -eq 0 ]]; then
  OPENCLAW_ANDROID_ADB_SERIAL="$ADB_SERIAL" OPENCLAW_ANDROID_WINDOW_BACKEND="$WINDOW_BACKEND" OPENCLAW_ANDROID_LLM_CONFIG_PATH="${LLM_CONFIG_PATH:-}" OPENCLAW_ANDROID_LLM_ENV_FILE="${LLM_ENV_FILE:-}" "$PROJECT_ROOT/scripts/install_user_service.sh"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
    systemctl --user enable --now openclaw-android-waydroid.service || true
    systemctl --user disable openclaw-android-waydroid-ui.service >/dev/null 2>&1 || true
  fi
fi

restart_openclaw_gateway_if_running

if [[ $RUN_SMOKE_TEST -eq 1 ]]; then
  log_step "Running post-install smoke test"
  SMOKE_ARGS=(
    --daemon-base-url "$DAEMON_BASE_URL"
    --layer "$SMOKE_TEST_LAYER"
    --tap "$SMOKE_TAP_X,$SMOKE_TAP_Y"
    --openclaw-home "$OPENCLAW_HOME"
  )
  [[ -n "$HERMES_TARGET_USER" ]] && SMOKE_ARGS+=(--hermes-user "$HERMES_TARGET_USER")
  [[ $HERMES_SYSTEM -eq 1 ]] && SMOKE_ARGS+=(--hermes-system)
  [[ $SMOKE_VISIBLE_ACTION -eq 0 ]] && SMOKE_ARGS+=(--skip-visible-action)
  if [[ -n "$HERMES_HOME_OVERRIDE" ]]; then
    HERMES_HOME="$HERMES_HOME_OVERRIDE" "$PROJECT_ROOT/scripts/smoke_test_install.sh" "${SMOKE_ARGS[@]}"
  else
    "$PROJECT_ROOT/scripts/smoke_test_install.sh" "${SMOKE_ARGS[@]}"
  fi
fi

if [[ $INSTALL_HERMES_PLUGIN -eq 1 ]]; then
  HERMES_CHECK="hermes -t clawdroid -z 'Check Android status with the android tool'"
else
  HERMES_CHECK="./scripts/install_hermes_plugin.sh  # optional, then: hermes -t clawdroid"
fi

GOOGLE_PLAY_CERT_REPORT=""
if extra_enabled "gapps"; then
  log_step "Google Play certification"
  GOOGLE_PLAY_CERT_REPORT="$("$PROJECT_ROOT/scripts/google_play_certification.sh" 2>&1 || true)"
fi

cat <<EOF

Setup complete.

Next checks:
  1. systemctl --user status openclaw-android-waydroid.service
  2. curl "$DAEMON_BASE_URL/v1/status" | jq
  3. openclaw plugins inspect android-waydroid --json
  4. $HERMES_CHECK
  5. "$PROJECT_ROOT/scripts/smoke_test_install.sh" --daemon-base-url "$DAEMON_BASE_URL" --layer auto
  6. desktop-session bootstrap: "$PROJECT_ROOT/scripts/import_graphical_env.sh"
  7. desktop-session supervisor health: "$PROJECT_ROOT/scripts/waydroid_supervisor_ctl.sh" status

Default store install report:
$(if [[ -n "$STORE_INSTALL_REPORT" ]]; then printf '%s\n' "$STORE_INSTALL_REPORT" | jq .; else printf '  (default store install skipped)\n'; fi)

If the Waydroid UI is not visible on the desktop, run:
  sudo "$PROJECT_ROOT/scripts/restart_everything_sudo.sh"

If Android shows an "Allow USB debugging?" prompt, check "Always allow from this computer" and tap "Allow".

$(if extra_enabled "gapps"; then cat <<CERT
Google Play certification:
$(if [[ -n "$GOOGLE_PLAY_CERT_REPORT" ]]; then printf '%s\n' "$GOOGLE_PLAY_CERT_REPORT"; else printf '  Run "%s/scripts/google_play_certification.sh" after Google Play Services initializes.\n' "$PROJECT_ROOT"; fi)
CERT
fi)

If the accessibility bridge still shows disconnected, restart the Waydroid guest once. On this LineageOS/Waydroid stack, companion APK updates bind reliably on fresh guest boot, not always immediately after in-place package replacement.
EOF
