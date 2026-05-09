#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"

INCLUDE_ANDROID_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-android-build)
      INCLUDE_ANDROID_BUILD=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/release_check.sh [--include-android-build]

Runs shell, Python, and plugin validation checks for repository release readiness.
Add --include-android-build to build the Android companion APK too.
EOF
      exit 0
      ;;
    *)
      fatal "Unknown argument: $1"
      ;;
  esac
  shift
done

require_cmd node
require_cmd python3
require_cmd git

CHECK_TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$CHECK_TMP_DIR"
}
trap cleanup EXIT

log_step "Checking shell scripts"
while IFS= read -r file; do
  bash -n "$file"
done < <(find "$PROJECT_ROOT/scripts" -name '*.sh' -type f | sort)
bash -n "$PROJECT_ROOT/doctor.sh"
bash -n "$PROJECT_ROOT/setup_everything.sh"

log_step "Checking README local references"
python3 - "$PROJECT_ROOT" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
readme = root / "README.md"
text = readme.read_text(encoding="utf-8")
paths: set[str] = set()


def add_path(raw: str) -> None:
    value = raw.strip().strip("'\"")
    if not value or value.startswith("#"):
        return
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return
    value = value.split("#", 1)[0].split("?", 1)[0]
    if value.startswith("./"):
        value = value[2:]
    if not value or value.startswith(("../", "/", "#")):
        return
    paths.add(value.rstrip("/"))


for match in re.finditer(r"""(?:src|href)=["']([^"']+)["']""", text):
    add_path(match.group(1))
for match in re.finditer(r"""!?\[[^\]]*\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)""", text):
    add_path(match.group(1))
for match in re.finditer(r"""(?<![\w/.-])\./([A-Za-z0-9._/-]+)""", text):
    add_path(match.group(1))

missing: list[str] = []
untracked: list[str] = []
for rel in sorted(paths):
    path = root / rel
    if not path.exists():
        missing.append(rel)
        continue
    if path.is_dir():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", rel],
            check=False,
            capture_output=True,
            text=True,
        )
        tracked = bool(result.stdout.strip())
    else:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
            check=False,
            capture_output=True,
            text=True,
        )
        tracked = result.returncode == 0
    if not tracked:
        untracked.append(rel)

if missing or untracked:
    if missing:
        print("README references missing local paths:", ", ".join(missing), file=sys.stderr)
    if untracked:
        print("README references untracked local paths:", ", ".join(untracked), file=sys.stderr)
    raise SystemExit(1)
PY

log_step "Checking LLM setup/service wiring"
LLM_CHECK_DIR="$CHECK_TMP_DIR/llm"
LLM_CONFIG_PATH="$LLM_CHECK_DIR/custom/config/llm.json"
LLM_ENV_FILE="$LLM_CHECK_DIR/custom/env/llm.env"
env \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY_ENV \
  OPENROUTER_API_KEY=fake-release-check-key \
  HOME="$LLM_CHECK_DIR/home" \
  HERMES_HOME="$LLM_CHECK_DIR/hermes" \
  XDG_CONFIG_HOME="$LLM_CHECK_DIR/xdg" \
  "$PROJECT_ROOT/setup_everything.sh" \
    --configure-llm-only \
    --llm-provider openrouter \
    --llm-config "$LLM_CONFIG_PATH" \
    --llm-env-file "$LLM_ENV_FILE" >/dev/null
grep -qx 'OPENROUTER_API_KEY=fake-release-check-key' "$LLM_ENV_FILE" \
  || fatal "LLM setup did not persist an environment-only OpenRouter key"
[[ "$(stat -c %a "$LLM_ENV_FILE")" == "600" ]] \
  || fatal "LLM setup did not write the env file with 0600 permissions"
python3 - "$LLM_CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
env_names = config["providers"]["openrouter"]["api_key_env"]
for name in ("OPENCLAW_ANDROID_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"):
    if name not in env_names:
        raise SystemExit(f"missing OpenRouter API key env fallback: {name}")
PY
CUSTOM_HERMES_HOME="$LLM_CHECK_DIR/custom-hermes-home"
CUSTOM_HERMES_LLM_CONFIG="$LLM_CHECK_DIR/custom-hermes/config/llm.json"
CUSTOM_HERMES_LLM_ENV="$LLM_CHECK_DIR/custom-hermes/env/llm.env"
mkdir -p "$CUSTOM_HERMES_HOME"
printf 'OPENROUTER_API_KEY=fake-custom-hermes-key\n' > "$CUSTOM_HERMES_HOME/.env"
env \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY_ENV \
  HOME="$LLM_CHECK_DIR/custom-hermes-home-root" \
  HERMES_HOME="$CUSTOM_HERMES_HOME" \
  XDG_CONFIG_HOME="$LLM_CHECK_DIR/custom-hermes-xdg" \
  "$PROJECT_ROOT/setup_everything.sh" \
    --configure-llm-only \
    --llm-provider openrouter \
    --llm-config "$CUSTOM_HERMES_LLM_CONFIG" \
    --llm-env-file "$CUSTOM_HERMES_LLM_ENV" >/dev/null
grep -qx 'OPENROUTER_API_KEY=fake-custom-hermes-key' "$CUSTOM_HERMES_LLM_ENV" \
  || fatal "LLM setup did not persist a key found only in a custom Hermes env"
LOCAL_LLM_CONFIG_PATH="$LLM_CHECK_DIR/local/config/llm.json"
LOCAL_LLM_ENV_FILE="$LLM_CHECK_DIR/local/env/llm.env"
env \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY_ENV \
  HOME="$LLM_CHECK_DIR/local-home" \
  HERMES_HOME="$LLM_CHECK_DIR/local-hermes" \
  XDG_CONFIG_HOME="$LLM_CHECK_DIR/local-xdg" \
  "$PROJECT_ROOT/setup_everything.sh" \
    --configure-llm-only \
    --llm-provider local \
    --llm-api-key fake-local-key \
    --llm-config "$LOCAL_LLM_CONFIG_PATH" \
    --llm-env-file "$LOCAL_LLM_ENV_FILE" >/dev/null
grep -qx 'OPENCLAW_ANDROID_LOCAL_LLM_API_KEY=fake-local-key' "$LOCAL_LLM_ENV_FILE" \
  || fatal "LLM setup did not preserve a supplied local provider API key"
[[ "$(stat -c %a "$LOCAL_LLM_ENV_FILE")" == "600" ]] \
  || fatal "LLM setup did not write the local provider env file with 0600 permissions"
python3 - "$LOCAL_LLM_CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
env_names = config["providers"]["local"]["api_key_env"]
if "OPENCLAW_ANDROID_LOCAL_LLM_API_KEY" not in env_names:
    raise SystemExit("missing local provider API key env")
PY
CUSTOM_LLM_CONFIG_PATH="$LLM_CHECK_DIR/custom-provider/config/llm.json"
CUSTOM_LLM_ENV_FILE="$LLM_CHECK_DIR/custom-provider/env/llm.env"
env \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY \
  -u OPENCLAW_ANDROID_SETUP_LLM_API_KEY_ENV \
  HOME="$LLM_CHECK_DIR/custom-home" \
  HERMES_HOME="$LLM_CHECK_DIR/custom-hermes" \
  XDG_CONFIG_HOME="$LLM_CHECK_DIR/custom-xdg" \
  "$PROJECT_ROOT/setup_everything.sh" \
    --configure-llm-only \
    --llm-provider custom \
    --llm-base-url http://127.0.0.1:8000/v1 \
    --llm-model custom-vision-model \
    --llm-api-key fake-custom-key \
    --llm-config "$CUSTOM_LLM_CONFIG_PATH" \
    --llm-env-file "$CUSTOM_LLM_ENV_FILE" >/dev/null
grep -qx 'OPENCLAW_ANDROID_VISION_API_KEY=fake-custom-key' "$CUSTOM_LLM_ENV_FILE" \
  || fatal "LLM setup did not preserve a supplied custom provider API key"
[[ "$(stat -c %a "$CUSTOM_LLM_ENV_FILE")" == "600" ]] \
  || fatal "LLM setup did not write the custom provider env file with 0600 permissions"
python3 - "$CUSTOM_LLM_CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
provider = config["providers"]["local"]
if "OPENCLAW_ANDROID_VISION_API_KEY" not in provider["api_key_env"]:
    raise SystemExit("missing custom provider API key env")
if provider["base_url"] != "http://127.0.0.1:8000/v1":
    raise SystemExit("custom provider base URL was not preserved")
PY
ARGV_CHECK_DIR="$LLM_CHECK_DIR/helper-argv"
REAL_PYTHON_BIN="$(command -v python3)"
mkdir -p "$ARGV_CHECK_DIR/bin"
cat > "$ARGV_CHECK_DIR/bin/python3" <<EOF
#!/usr/bin/env bash
for arg in "\$@"; do
  if [[ "\$arg" == *argv-sentinel-key* ]]; then
    echo "secret leaked into python argv: \$*" >&2
    exit 97
  fi
done
exec "$REAL_PYTHON_BIN" "\$@"
EOF
chmod +x "$ARGV_CHECK_DIR/bin/python3"
PATH="$ARGV_CHECK_DIR/bin:$PATH" \
  HOME="$ARGV_CHECK_DIR/home" \
  HERMES_HOME="$ARGV_CHECK_DIR/hermes" \
  XDG_CONFIG_HOME="$ARGV_CHECK_DIR/xdg" \
  OPENCLAW_ANDROID_SETUP_LLM_API_KEY=argv-sentinel-key \
  "$PROJECT_ROOT/setup_everything.sh" \
    --configure-llm-only \
    --llm-provider local \
    --llm-config "$ARGV_CHECK_DIR/config/llm.json" \
    --llm-env-file "$ARGV_CHECK_DIR/env/llm.env" >/dev/null
grep -qx 'OPENCLAW_ANDROID_LOCAL_LLM_API_KEY=argv-sentinel-key' "$ARGV_CHECK_DIR/env/llm.env" \
  || fatal "LLM setup did not preserve the argv regression sentinel key"

SERVICE_CHECK_DIR="$CHECK_TMP_DIR/service with spaces"
SERVICE_LLM_CONFIG="$SERVICE_CHECK_DIR/custom config/llm.json"
SERVICE_LLM_ENV="$SERVICE_CHECK_DIR/custom env/llm.env"
HOME="$SERVICE_CHECK_DIR/home dir" \
  XDG_CONFIG_HOME="$SERVICE_CHECK_DIR/xdg config" \
  OPENCLAW_ANDROID_ADB_SERIAL=127.0.0.1:5555 \
  OPENCLAW_ANDROID_LLM_CONFIG_PATH="$SERVICE_LLM_CONFIG" \
  OPENCLAW_ANDROID_LLM_ENV_FILE="$SERVICE_LLM_ENV" \
  "$PROJECT_ROOT/scripts/install_user_service.sh" >/dev/null
SERVICE_FILE="$SERVICE_CHECK_DIR/home dir/.config/systemd/user/openclaw-android-waydroid.service"
SERVICE_LLM_ENV_UNIT_PATH="${SERVICE_LLM_ENV// /\\x20}"
[[ -f "$SERVICE_LLM_CONFIG" ]] \
  || fatal "User service install did not create the custom LLM config parent/path"
grep -Fqx "EnvironmentFile=-$SERVICE_LLM_ENV_UNIT_PATH" "$SERVICE_FILE" \
  || fatal "User service does not load the custom LLM env file"
grep -Fqx "Environment=\"OPENCLAW_ANDROID_LLM_CONFIG_PATH=$SERVICE_LLM_CONFIG\"" "$SERVICE_FILE" \
  || fatal "User service does not point at the custom LLM config path"

log_step "Checking installer security invariants"
grep -Eq 'WAYDROID_SCRIPT_REV=.*[0-9a-f]{40}' "$PROJECT_ROOT/scripts/install_waydroid_extras.sh" \
  || fatal "waydroid_script must be pinned to a reviewed commit"
grep -Fq 'git -C "$WORKDIR" checkout --detach "$WAYDROID_SCRIPT_REV"' "$PROJECT_ROOT/scripts/install_waydroid_extras.sh" \
  || fatal "waydroid_script installer must checkout the pinned revision"
if grep -Fq 'git -C "$WORKDIR" pull' "$PROJECT_ROOT/scripts/install_waydroid_extras.sh"; then
  fatal "waydroid_script installer must not pull latest before sudo execution"
fi
grep -Fq 'remove_path_best_effort "$hermes_home/skills/$HERMES_SKILL_NAME"' "$PROJECT_ROOT/scripts/uninstall_everything.sh" \
  || fatal "Uninstall must remove the Hermes skill as well as the plugin"

log_step "Checking Android SDK bootstrap guard"
SDK_CHECK_DIR="$CHECK_TMP_DIR/empty-sdk"
FAKE_BOOTSTRAP="$CHECK_TMP_DIR/fake-bootstrap-android-sdk.sh"
cat > "$FAKE_BOOTSTRAP" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin" \
  "$ANDROID_SDK_ROOT/platform-tools" \
  "$ANDROID_SDK_ROOT/platforms/android-34" \
  "$ANDROID_SDK_ROOT/build-tools/34.0.0"
cat > "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$ANDROID_SDK_ROOT/platform-tools/adb" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$ANDROID_SDK_ROOT/build-tools/34.0.0/aapt2" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" \
  "$ANDROID_SDK_ROOT/platform-tools/adb" \
  "$ANDROID_SDK_ROOT/build-tools/34.0.0/aapt2"
touch "$ANDROID_SDK_ROOT/platforms/android-34/android.jar"
EOF
chmod +x "$FAKE_BOOTSTRAP"
ANDROID_SDK_ROOT="$SDK_CHECK_DIR" \
  OPENCLAW_ANDROID_BOOTSTRAP_SDK_SCRIPT="$FAKE_BOOTSTRAP" \
  OPENCLAW_ANDROID_BUILD_COMPANION_CHECK_SDK_ONLY=1 \
  "$PROJECT_ROOT/scripts/build_companion_apk.sh" >/dev/null

log_step "Checking Android bridge auth guard"
python3 - "$PROJECT_ROOT/android-companion/app/src/main/java/ai/openclaw/androidbridge/BridgeHttpServer.kt" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
required = [
    '"/tree"',
    '"/configure"',
    '"/node_action"',
    '"/global_action"',
    '"x-openclaw-bridge-token"',
    "MessageDigest.isEqual",
    "Unauthorized bridge request",
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"bridge auth guard missing expected markers: {missing}")
PY

log_step "Checking Python sources"
while IFS= read -r py_file; do
  python3 -m py_compile "$py_file"
done < <(find "$PROJECT_ROOT/python-daemon/openclaw_android_daemon" "$PROJECT_ROOT/hermes-plugin" -name '*.py' -type f | sort)

if ! python3 -m ruff --version >/dev/null 2>&1; then
  echo "Missing ruff. Install dev dependencies with:" >&2
  echo "  python3 -m pip install -r \"$PROJECT_ROOT/python-daemon/requirements-dev.txt\"" >&2
  exit 1
fi

log_step "Running Ruff undefined-name checks"
python3 -m ruff check --select F821,F822,F823 \
  "$PROJECT_ROOT/python-daemon/openclaw_android_daemon" \
  "$PROJECT_ROOT/python-daemon/tests"

log_step "Checking JSON manifests"
python3 -m json.tool "$PROJECT_ROOT/openclaw-plugin/openclaw.plugin.json" >/dev/null
python3 -m json.tool "$PROJECT_ROOT/docs/llm.example.json" >/dev/null
[[ -f "$PROJECT_ROOT/hermes-plugin/plugin.yaml" ]] || fatal "Missing Hermes plugin manifest"

log_step "Running Python tests"
python3 -m unittest discover \
  -s "$PROJECT_ROOT/python-daemon/tests" \
  -t "$PROJECT_ROOT/python-daemon"

log_step "Checking plugin sources"
while IFS= read -r js_file; do
  node --check "$js_file"
done < <(find "$PROJECT_ROOT/openclaw-plugin" -path "$PROJECT_ROOT/openclaw-plugin/node_modules" -prune -o -name '*.js' -type f -print | sort)
bash -n "$PROJECT_ROOT/scripts/install_hermes_plugin.sh"

if [[ "$INCLUDE_ANDROID_BUILD" -eq 1 ]]; then
  log_step "Building Android companion APK"
  "$PROJECT_ROOT/scripts/build_companion_apk.sh"
fi

log_step "Release checks passed"
