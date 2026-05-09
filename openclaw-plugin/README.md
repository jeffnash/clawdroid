# OpenClaw plugin

This package exposes two optional OpenClaw tools:

- `android`
- `android_admin`

The plugin itself is intentionally thin. It forwards requests to the local Python daemon.

The runtime `android` tool supports both snapshot/ref interaction and direct navigation actions such as:

- `task_route`
- `app_open`
- `activity_start`
- `intent_start`
- `url_open`
- `settings_open`
- `app_details_open`
- `market_open`
- `store_search`
- `decide_next`

The admin `android_admin` tool also supports direct Aptoide-backed installs over ADB:

- `recover`
- `store_install`

Use `recover` with `mode: "user"` for normal stuck UI/session recovery. Use `mode: "system"` only after user approval; it runs the sudo restart helper when passwordless sudo is available.

Recommended runtime model:

- `snapshot` is the primary reasoning payload for the OpenClaw agent.
- Use `task_route` first for branded consumer-service requests such as Amazon, Uber, DoorDash, Instacart, Airbnb, Reddit, and Spotify.
- The agent should interpret `top_refs`, `refs`, `screen_context`, and any attached screenshot itself.
- `act` executes the chosen step and returns verification plus `post_action_snapshot` when available.
- `decide_next` is an explicit fallback helper, not the normal control loop.
- For installs, prefer `store_search` plus `android_admin { action: "store_install", ... }` over fragile store UI and Android package-installer dialogs when the package is known.

## Install locally

```bash
pnpm install
openclaw plugins install -l ./openclaw-plugin
openclaw plugins enable android-waydroid
```

## Allow the tools

Safe runtime tool only:

```bash
openclaw config set tools.allow '["android"]' --strict-json
```

Runtime + admin:

```bash
openclaw config set tools.allow '["android", "android_admin"]' --strict-json
```

In practice the root setup script updates config for you.

## Plugin config

The plugin reads these config values under `plugins.entries.android-waydroid.config`:

- `daemonBaseUrl`
- `defaultDevice`
- `adbSerial`
- `allowedPackages`
- `allowHostControl`
- `allowAgentAppOpen`
- `allowAgentAppsList`
- `allowAgentScreenshots`
- `allowAgentInstall`
- `requireApprovalForInstall`
- `requireApprovalForProtectedActions`
- `defaultDecisionMode`
- `defaultLlmProvider`
- `defaultLlmModel`

These LLM-related config values only apply when the agent explicitly calls `decide_next`.
They are part of the published plugin manifest schema, so `openclaw plugins inspect android-waydroid --json` should report them.

## Development

```bash
cd openclaw-plugin
pnpm install
node --check index.js
```
