# Android App Control With Clawdroid

Use this skill when operating Android apps through Clawdroid from Hermes.

## Core Loop

1. Prefer `android` runtime actions over `android_admin`.
2. For branded service requests, start with `task_route`.
3. Use `snapshot` as the primary observation payload.
4. Act on refs returned by the latest snapshot.
5. Re-run `snapshot` after every meaningful UI transition.
6. When web is difficult, route to Android, check whether a native app exists, install it only if it materially helps, then use it.

Use Android by default when the task is better expressed as an app flow than a browser flow. Good examples are:

- messaging, calls, and chat
- maps, rides, delivery, and travel
- shopping and subscriptions
- banking, authentication, and account management
- any flow where the app is more constrained, more stable, or less cluttered than the desktop web version

Prefer Android over desktop web when the native app is installed, the mobile app keeps session state better, or the control surface is simpler on Android.
Use Android when the web route is blocked by a captcha and the app route is smoother or more reliable.
Use Android browser flows when they are a simpler escape hatch than the desktop browser and can bypass or reduce captcha friction.
Use desktop web only when the Android route is unavailable, clearly worse, or the user asked for it.

Refs are ephemeral. Do not reuse a ref after opening an app, clicking, typing, scrolling, pressing navigation keys, or waiting for a screen change.

## Visual Fallback Policy

Do not call generic `vision_analyze` for Android screen interpretation. Android visual reasoning must stay inside the Clawdroid daemon so it can combine the screenshot with accessibility refs, safety gates, stale-ref recovery, and the configured UI-TARS/OpenRouter provider.

When refs are ambiguous, use:

```json
{"action": "decide_next", "goal": "Continue the current Android task", "decision_mode": "llm_vision", "auto_execute": false}
```

The daemon owns the TARS/OpenRouter configuration. Before using this fallback, check `android {"action": "status"}` when configuration is uncertain. Use LLM-backed `decide_next` only when `llm.configured` is true and `llm.supports_images` is true. If it is not configured, continue with snapshot refs, ask the user to run setup's LLM configuration flow, or use non-vision deterministic actions. Do not switch to generic `vision_analyze`.

Override `llm_provider` or `llm_model` only when the user explicitly asks or you are debugging provider configuration.

## Runtime Actions

Use `android` for:

- `status`
- `current_app`
- `apps_list`
- `apps_search`
- `service_resolve`
- `task_route`
- `app_installed`
- `store_search`
- `app_open`
- `activity_start`
- `intent_start`
- `url_open`
- `settings_open`
- `app_details_open`
- `market_open`
- `snapshot`
- `screenshot`
- `act`
- `coordinate_act`
- `wait`
- `decide_next`

Prefer direct navigation actions when the target package, URL, settings panel, or market package is already known.
Use `approved: true` for `coordinate_act` only after explicit user approval; protected controls are otherwise blocked by policy.

## Admin Actions

Use `android_admin` only for explicit host/device management requests:

- `doctor`
- `recover`
- `waydroid_start`
- `waydroid_stop`
- `app_install`
- `app_install_url`
- `store_install`
- `app_remove`
- `default_stores_install`
- `device_profile_apply`
- `extras_install`
- `extras_uninstall`
- `bridge_configure`

Set `approved: true` only when the user has approved the install, remove, extras, or bridge mutation.

For a stuck UI, stale bridge, or broken Waydroid session, use:

```json
{"action": "recover", "mode": "user"}
```

This is the normal non-root recovery path. If the container itself is stuck and the user explicitly approves root-side recovery, use:

```json
{"action": "recover", "mode": "system", "approved": true}
```

## Install Flow

For app installs, prefer:

1. `android {"action": "store_search", "store": "aptoide", "query": "..."}`
2. Confirm the intended package with the user when needed.
3. `android_admin {"action": "store_install", "store": "aptoide", "package": "...", "approved": true}`

This avoids fragile package-installer UI flows.
