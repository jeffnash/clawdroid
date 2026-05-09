# Python daemon

The daemon is the real control plane for the stack. OpenClaw and Hermes plugins both call this daemon over the same local HTTP API.

It exposes a local HTTP API on `127.0.0.1:48765` by default and handles:

- Waydroid lifecycle checks
- ADB connection and port forwarding
- guest wake/unlock and bridge-forward recovery
- user-session and root-assisted runtime recovery
- ADB-backed screenshots and input fallback
- app listing/open/install/remove
- direct Aptoide search and store install over ADB
- direct intent/activity/url/settings launches
- app search / installed-package checks
- accessibility bridge calls
- snapshot creation and ref assignment
- ref-based actions and waits

## Create the virtualenv

```bash
cd python-daemon
/usr/bin/python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

If `/usr/bin/python3.12` is unavailable on your distro, use the package-manager Python 3.x that ships with the OS rather than a Homebrew Python `3.14+`. The pinned dependencies in this project are known-good on Python `3.12`.

## Run the daemon

```bash
.venv/bin/python -m openclaw_android_daemon.main --host 127.0.0.1 --port 48765
```

## Environment variables

- `OPENCLAW_ANDROID_DAEMON_HOST`
- `OPENCLAW_ANDROID_DAEMON_PORT`
- `OPENCLAW_ANDROID_ADB_SERIAL`
- `OPENCLAW_ANDROID_BRIDGE_PORT`
- `OPENCLAW_ANDROID_BRIDGE_URL`
- `OPENCLAW_ANDROID_ALLOWED_PACKAGES`
- `OPENCLAW_ANDROID_SCREENSHOT_DIR`
- `OPENCLAW_ANDROID_DOWNLOAD_DIR`
- `OPENCLAW_ANDROID_PREFER_NATIVE_APPS`
- `OPENCLAW_ANDROID_DEFAULT_STORES`
- `OPENCLAW_ANDROID_REQUIRE_APPROVAL_FOR_INSTALL`
- `OPENCLAW_ANDROID_REQUIRE_APPROVAL_FOR_PROTECTED_ACTIONS`
- `OPENCLAW_ANDROID_APTOIDE_META_URL`
- `OPENCLAW_ANDROID_APTOIDE_SEARCH_URL`
- `OPENCLAW_ANDROID_LLM_CONFIG_PATH`
- `OPENCLAW_ANDROID_LLM_MODELS_PATH`
- `OPENCLAW_ANDROID_LLM_SETTINGS_PATH`

LLM config is XDG-first:

- `$XDG_CONFIG_HOME/openclaw-android-waydroid/llm.json`
- `$XDG_CONFIG_HOME/openclaw-android-waydroid/models.json`
- `$XDG_CONFIG_HOME/openclaw-android-waydroid/settings.json`

If those files are absent, the daemon can still fall back to the legacy `$HOME/.pi/agent/*.json` files.

## API smoke tests

```bash
curl http://127.0.0.1:48765/v1/status | jq
curl -X POST http://127.0.0.1:48765/v1/agent/dispatch       -H 'content-type: application/json'       -d '{"action":"apps_list"}' | jq
```

Routine `status` and `current_app` probes intentionally use plain ADB. On this Waydroid image, starting `uiautomator2` can displace the accessibility service, so the normal runtime path avoids it entirely.

The daemon also reapplies `adb forward tcp:49317 tcp:49317` before bridge health checks. This matters after guest reboots, because the bridge server may be healthy inside Waydroid while the host-side forward has been lost.

`snapshot` is bridge-first by design. It uses the accessibility bridge for tree data and raw ADB for screenshots. If the bridge is unavailable, the daemon returns a bridge-unavailable error instead of auto-starting `uiautomator2`.

High-value runtime actions include:

- `task_route`
- `app_open`
- `activity_start`
- `intent_start`
- `url_open`
- `settings_open`
- `app_details_open`
- `market_open`
- `store_search`

Use these when the destination is already known. They are more reliable than walking through launcher, app drawer, Settings menus, or store chrome just to reach a target screen.

For service-style user requests such as "add this to my cart on Amazon" or "open Spotify", start with `task_route`. It resolves supported consumer services to the best Android surface:

- installed native app when present
- Android web when native app is not installed
- optional direct store-install metadata when native app behavior matters

For installs, prefer direct store/backend paths when possible:

- `store_search` resolves Aptoide candidates to concrete package names and APK URLs
- `store_install` downloads the chosen Aptoide artifact and installs it directly through `adb install -r`

That path avoids the Android package-installer dialog entirely.
