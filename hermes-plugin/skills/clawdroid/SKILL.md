---
name: clawdroid
description: Control Android apps in Waydroid through Clawdroid. Use for Android-native app flows, Waydroid UI control, Android screenshots, and mobile app tasks.
---

# Clawdroid

Hermes controls Android through the local Clawdroid daemon at `http://127.0.0.1:48765`.

## Core Rules

- Use Clawdroid tools for Android screens; do not use generic `vision_analyze`, raw `adb`, `curl`, or terminal commands to interpret or click the Waydroid UI.
- Use `snapshot`/`android_snapshot` as the primary observation.
- Act only on refs from the latest snapshot.
- Re-snapshot after meaningful UI changes unless a tool returns `post_action_snapshot`.
- Use admin actions only for explicit device/package management requests.

## Preferred Tool Surface

If the `android` tool is available, prefer it:

```json
{"action": "snapshot", "snapshot_mode": "hybrid"}
```

```json
{"action": "act", "snapshot_id": "snap_...", "ref": "a4", "op": "click"}
```

For ambiguous screens or custom views, use daemon-backed visual fallback:

```json
{"action": "decide_next", "goal": "Continue the current Android task", "decision_mode": "llm_vision", "auto_execute": false}
```

The daemon owns the UI-TARS/OpenRouter configuration. If configuration is uncertain, check Android status first and use this fallback only when `llm.configured` and `llm.supports_images` are true. If no model is configured, keep using refs, ask the user to run setup's LLM configuration flow, or use deterministic non-vision actions. Do not call `vision_analyze` on the screenshot for Android UI targeting.

## Legacy Split Tools

Some Hermes installs also expose split tools. Use these names only when the generic `android` tool is unavailable:

- `android_info`
- `android_task_route`
- `android_snapshot`
- `android_act`
- `android_tars_target`
- `android_decide_next`
- `android_screenshot`
- `android_app_open`
- `android_apps_list`
- `android_apps_search`
- `android_store_search`
- `android_url_open`

For split tools, prefer `android_tars_target` over `android_decide_next` when screenshot-aware reasoning is needed:

```json
{"goal": "Continue the current Android task", "decision_mode": "llm_vision", "auto_execute": false}
```

## Normal Flow

1. Use `task_route` or `android_task_route` for branded app/service requests.
2. Take a hybrid snapshot.
3. Inspect `top_refs`, `refs`, and `screen_context`.
4. Click/type/scroll with `act` or `android_act`.
5. If still ambiguous, call `decide_next` or `android_tars_target` with `decision_mode: "llm_vision"`.

## Installs

For app installs, search first and install only with explicit approval:

```json
{"action": "store_search", "store": "aptoide", "query": "app name"}
```

```json
{"action": "store_install", "store": "aptoide", "package": "com.example", "approved": true}
```
