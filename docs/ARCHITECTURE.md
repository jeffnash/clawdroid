# Architecture

## Overview

The project has four runtime planes:

1. **OpenClaw plugin**
   - Registers `android` and `android_admin` as optional agent tools.
   - Ships an agent skill that teaches snapshot-first Android interaction.
   - Delegates all actual work to the local daemon over HTTP.

2. **Hermes plugin**
   - Registers the same `android` and `android_admin` tools in Hermes.
   - Ships an opt-in plugin skill with the same snapshot-first control loop.
   - Delegates all actual work to the local daemon over HTTP.
   - Uses `CLAWDROID_DAEMON_BASE_URL` when the daemon is not on `127.0.0.1:48765`.

3. **Python daemon**
   - Orchestrates Waydroid lifecycle, ADB connectivity, and screenshot capture.
   - Uses plain ADB for cheap health/current-app checks, bridge port-forward recovery, and guest wake/unlock.
   - Connects to the Android companion bridge (forwarded to localhost) for event-driven semantic tree inspection and node actions.
   - Owns snapshot state and ref assignment.

4. **Android companion app**
   - Hosts an `AccessibilityService`.
   - Builds a normalized UI tree from `AccessibilityNodeInfo`.
   - Executes semantic node/global actions.
   - Exposes a localhost HTTP bridge that the daemon accesses through `adb forward`.

## Why AccessibilityService plus ADB?

Plain ADB is strong for screenshots, foreground app inspection, simple key events, and coordinate fallback input.

The companion `AccessibilityService` gives:

- better semantic structure than raw XML dumps,
- event-driven change detection,
- node-level actions (`ACTION_CLICK`, `ACTION_SET_TEXT`, `ACTION_SCROLL_*`),
- global actions (`BACK`, `HOME`, `RECENTS`).

On this Waydroid/LineageOS image, starting `uiautomator2` can unbind the companion accessibility service. The daemon therefore keeps normal runtime interaction on the accessibility bridge plus ADB only, and does not auto-start `uiautomator2`.

In practice that means:

- `status`, `current_app`, `app_open`, and screenshots stay on ADB
- direct navigation actions such as `activity_start`, `intent_start`, `url_open`, `settings_open`, `app_details_open`, and `market_open` also stay on ADB
- `snapshot` is bridge-first and returns a hard bridge-unavailable error instead of silently starting `uiautomator2`
- runtime action fallback uses ADB key events, taps, and swipes
- bridge health checks reapply `adb forward` because guest reboots can invalidate the host-side forward while leaving the service healthy inside Waydroid

## Snapshot model

A snapshot is an ephemeral mapping of visible/interactable UI nodes to short refs:

- `a1`, `a2`, `a3`, …

Each ref stores:

- bridge node key or hierarchy path,
- parent/child/sibling structure,
- semantic id and derived semantic label,
- container and section context,
- bounds,
- role,
- text,
- class name,
- resource id,
- supported actions,
- source (`bridge`, or `adb_screenshot_only` on bridge failure).

Refs are considered stale after:

- `app_open`
- `click`
- `long_click`
- `set_text`
- `scroll_*`
- any detected package/activity/window change

## Admin vs runtime tooling

The project keeps runtime usage generic and browser-like, while separating host mutations into a second tool.

### Runtime tool

`android`:

- inspect
- route branded service tasks to native app, Android web, or install options
- open app
- open a known activity, intent, URL, settings screen, app-details page, or market page directly
- snapshot
- screenshot
- act on a ref
- wait

### Admin tool

`android_admin`:

- start/stop Waydroid
- recover stuck user-session or root-assisted system runtime
- install/remove APKs
- install extras through `waydroid_script`
- reconfigure bridge allowlists

`recover mode=user` resets the user-owned UI supervisor, reconnects ADB, refreshes the bridge forward, wakes/unlocks the guest, and clears stale snapshot refs. `recover mode=system` is reserved for explicit user approval because it invokes the sudo restart helper when passwordless sudo is available.

## Bridge transport

The Android companion listens on `127.0.0.1:49317` inside Waydroid.

The daemon runs:

```bash
adb -s <serial> forward tcp:49317 tcp:49317
```

Then accesses the bridge locally at:

```text
http://127.0.0.1:49317
```

That forward is not durable across guest restarts, so the daemon refreshes it before bridge probes instead of assuming it is still present.

## Host integrations

OpenClaw and Hermes both call the same daemon API:

```text
POST /v1/agent/dispatch
POST /v1/admin/dispatch
```

The OpenClaw plugin is JavaScript because OpenClaw plugins are ESM packages. The Hermes plugin is Python because Hermes plugins register tools through `register(ctx)`.

Both plugins intentionally stay thin:

- no direct ADB calls,
- no Waydroid lifecycle logic,
- no duplicate snapshot/ref state,
- no app-specific adapters.

That keeps behavior consistent across agents and makes daemon tests the source of truth.

## Build/install expectations

- Plugin: ESM JavaScript package installable by OpenClaw.
- Hermes plugin: Python drop-in directory under `$HERMES_HOME/plugins/clawdroid`.
- Daemon: Python 3.10+ virtualenv, FastAPI + Uvicorn.
- Companion app: Gradle + Android SDK, built from source in `android-companion/`.

## Host/session assumptions

- On Wayland hosts, the bundle reuses the existing compositor socket.
- On X11 hosts, it starts nested Weston on `wayland-1` only when needed.
- Startup configures the Android guest to stay awake and disables the lock screen so UI recovery does not get blocked by sleep/lock prompts.
