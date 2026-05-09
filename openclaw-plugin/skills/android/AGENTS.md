# OpenClaw Android (Waydroid) Agent Rules

## System Overview

The agent controls Android apps inside a **Waydroid** container by talking to an **OpenClaw Accessibility Bridge** running inside the Android guest. The daemon runs on the host at `http://127.0.0.1:48765`.

```
Local host  <--HTTP-->  Python daemon  <--ADB-->  Bridge HTTP server  <--A11y API-->  Android apps
(127.0.0.1:48765)       (bridge.py)     (exec-out)  (port 49317)          (OpenClawAccessibilityService)
```

## Prerequisites

- Waydroid running with ADB enabled (`waydroid adb enable`)
- OpenClaw accessibility service enabled in Android settings
- Daemon running (`systemctl --user start openclaw-android-waydroid`)

## Operating Model

The OpenClaw agent is the primary reasoner.

- For branded consumer-service requests such as Amazon, Uber, DoorDash, Instacart, Airbnb, Reddit, or Spotify, start with `task_route`.
- If the user request names a supported consumer brand or app and the task plausibly belongs on Android, call `task_route` before defaulting to desktop web.
- Use `snapshot` as the main decision surface.
- Read `current_app`, `screen_context`, `top_refs`, `refs`, and any attached screenshot yourself.
- Use `act` to execute the chosen step.
- Use `post_action_snapshot` / `next_snapshot_id` to continue without re-querying when available.
- Prefer direct package/store backends for installs when the package is already known.
- Treat `decide_next` as a fallback helper, not the normal path.
- When web is difficult, use this flow:
  1. call `task_route`
  2. check whether a native app is available
  3. install the app only if it materially improves the task
  4. use the app instead of forcing the browser flow

Android should be the default surface when the task is naturally app-shaped:

- messaging, calls, and chat apps
- maps, rides, delivery, and travel
- shopping, subscriptions, and account management
- banking, authentication, and other login-heavy flows
- any task where the mobile app is simpler, more constrained, or less cluttered than the website

Prefer Android over desktop web when:

- the native app is installed and usable
- the mobile app has fewer prompts or fewer modal layers
- the app preserves session state better than the browser
- the task benefits from app-specific controls or device permissions
- the web route is blocked by a captcha and the app route avoids or reduces that friction
- the Android browser route is easier than the desktop browser because it bypasses or reduces captcha friction

Use desktop web only when:

- the Android route is unavailable
- the app is clearly worse than the web flow
- the user explicitly asked for the desktop browser

Do not start with `decide_next` when a normal snapshot and route choice are enough.

In short: the daemon should expose structure and execute deterministically; the agent LLM should interpret the screen.

Routing policy:

- Prefer `native_app` when the requested service is installed and the task is app-native, especially commerce, delivery, travel, or media-account flows.
- Prefer `android_web` when the app is missing but the service is supported and the mobile web route is adequate.
- Only use `desktop_web` immediately when `task_route` cannot match the service or the Android route is explicitly unsuitable.
- Only use `decide_next` when you still cannot resolve the screen after reading the snapshot and screenshot yourself.

## Actions

### `snapshot`

```json
{"action": "snapshot", "snapshot_mode": "hybrid", "include_screenshot": false}
```

Returns a ranked UI tree with:
- `foreground_package` — package of the foreground app
- `window_rank` — per-node window priority (0=active, 10=foreground, 20=launcher when active, 30=noise when app foreground, 40=other)
- `refs` — sorted list of UI nodes (best first)
- `details=` in the rendered summary — row context such as developer, rating, downloads, or sibling labels
- `confidence_score` — 0.12–1.00 tiered score
- `has_semantic_label` — has text/content_desc/hint
- `is_actionable` — clickable/focusable/editable/scrollable
- `is_foreground_package` — same package as `foreground_package`
- `is_contextual_noise` — noise package AND not the current foreground package
- `screenshot_path` — path to PNG screenshot (when `include_screenshot: true`)
- `source` — `"bridge"` (accessibility tree) or `"adb_screenshot_only"` (fallback)
- `top_refs` — 5 highest-confidence refs for quick disambiguation
- `screen_context` — structured summary with `kind`, `archetype`, dominant container, and primary action hints

This is the primary payload you should reason over directly.
If your current model can read images and the screen looks visually ambiguous, request `include_screenshot: true` so the screenshot is attached to the tool result.

Request a screenshot when:
- `screenshot_recommended` is true
- the screen is icon-heavy or custom-drawn
- multiple refs have similar labels
- the visible state matters more than the accessibility text
- a previous action verified weakly

**Modes:**
- `interactive` (default) — only the active/focused window
- `hybrid` — active window + its children; recommended for most tasks
- `full` — all windows

**Sorting priority (best first):**
1. `has_semantic_label` = true
2. `is_foreground_package` = true
3. `is_actionable` = true
4. NOT `is_contextual_noise`
5. Lower `window_rank`
6. Lower `resource_id` count (fewer implies more specific)
7. Lower `sort_key` tiebreaker
8. Higher `confidence_score`

### `act` — click / type / scroll

```json
{"action": "act", "snapshot_id": "snap_...", "ref": "a4", "op": "click", "text": "hello"}
```

**Operations:**
- `click` — tap a ref
- `click_center` — tap the center of a ref's bounds, bypassing semantic node actions
- `long_click` — long press
- `set_text` — type text into a textbox
- `scroll_forward` / `scroll_backward` — scroll
- `press_back` / `press_home` / `press_recents` / `press_enter` — global actions

Use refs from the **most recent snapshot only**. Stale refs are invalid.

When `act` succeeds it may return:
- `verified` — whether the daemon observed a real UI change
- `verification.reasons` — why it believes the action landed
- `stale_recovery` — the daemon recovered your stale ref onto the latest screen
- `action_resolution` — the daemon resolved a non-actionable text/child ref to an actionable row/control
- `retry_used` — the daemon retried a weak semantic click with a center tap
- `post_action_snapshot` — a fresh snapshot captured after the action
- `next_snapshot_id` — the new snapshot id, if `post_action_snapshot` exists

If `post_action_snapshot` is present, prefer it immediately instead of calling `snapshot` again.
Only re-run `snapshot` when `snapshot_stale: true` or verification is weak/false.
If verification is weak, inspect the returned `post_action_snapshot` carefully. The daemon may attach a screenshot automatically on that resnapshot when the tree is weak.

### `decide_next` (fallback only)

```json
{"action": "decide_next", "goal": "Continue installing the app", "decision_mode": "auto", "auto_execute": false}
```

Returns one recommended next step with:
- `decision_source` — `deterministic` or `llm`
- `decision_mode_used` — `deterministic`, `llm_text`, or `llm_vision`
- `decision` — `{ decision, ref, label, confidence, reason }`
- `execution` — only when `auto_execute: true`

Use this only when you have already inspected the snapshot yourself and still cannot choose confidently.

Recommended usage:
- `decision_mode: "llm_vision"` when the active OpenClaw model is text-only or you want a specialized vision fallback
- `decision_mode: "llm_text"` only when the screenshot is not needed
- `decision_mode: "auto"` only if you explicitly want the daemon to pick the fallback mode
The daemon captures a screenshot automatically before any LLM-backed `decide_next` call.

### `task_route`

```json
{"action": "task_route", "goal": "add this to my cart on amazon"}
```

Resolves branded service requests to the best Android entry path. The response includes:

- `service` / `selected_match` — the highest-confidence supported service match
- `preferred_backend` — `native_app`, `android_web`, `direct_store_install`, or `desktop_web`
- `recommended_action` — the next Android tool payload when the route stays on Android
- `install_option` — optional direct `store_install` payload when a native app is not installed
- `browser_url` — Android-web route when native app is unavailable

Use this before general app search when the user intent clearly targets a known consumer service.
Do not install an app automatically just because `install_option` exists. Use it when native-app behavior materially helps and install approval is allowed.

### `app_open`

```json
{"action": "app_open", "package": "cm.aptoide.pt"}
```

Launches or brings to front an app by package name. Returns `{"ok": true, "package": "cm.aptoide.pt", "snapshot_stale": true}`. Call `snapshot` after `app_open` before acting.

### `store_search`

```json
{"action": "store_search", "store": "aptoide", "query": "amazon kindle"}
```

Returns direct Aptoide candidates with package names, trust rank, and download metadata. Use this when you want to bypass fragile store UI and install directly over ADB through `android_admin`.

### `screenshot`

```json
{"action": "screenshot"}
```

Captures a full PNG screenshot via `screencap`. Returns `{"ok": true, "path": "/path/to/screenshot.png"}`.

## Confidence Tiers

| Score | Meaning |
|-------|---------|
| 1.00 | Labeled + actionable + foreground package |
| 0.95 | Labeled + actionable |
| 0.90 | Labeled + foreground package |
| 0.82 | Labeled + actionable + overlay |
| 0.78 | Labeled |
| 0.65 | Actionable + foreground |
| 0.55 | Actionable |
| 0.45 | Labeled + overlay |
| 0.25 | Actionable + overlay |
| 0.12 | Everything else |

**Golden rule:** A labeled item beats an unlabeled item of any kind. If you need to click an unlabeled button, use its `bounds` + `click_center` op.

## Generic Interpretation Rules

- Prefer the rendered `details=` context to distinguish repeated row items in lists, stores, installers, and menus.
- On list or grid screens, inspect `screen_context.dominant_container_*` first; that tells you which collection is likely the real content area.
- On dialog screens, inspect `screen_context.primary_action_ref` / `primary_action_label` before guessing from raw position alone.
- If you want the row item but only a child text label is obvious, it is fine to click that child ref; the daemon can often resolve it to the actionable parent row.
- If the action result shows `stale_recovery` or `action_resolution`, trust the resolved/current ref for the next step instead of the original one.
- If a click verifies weakly, use the returned `post_action_snapshot` rather than repeating the same click immediately.
- If the same screen comes back after a weak click, switch targets using row context or `click_center` before assuming the UI is frozen.
- When `task_route` returns an install option and native behavior matters, prefer direct store install over browsing store chrome or the Android package-installer UI.

## Navigation Patterns

### Pattern 0: Service task routing

```
task_route(goal="add this to my cart on amazon") →
if preferred_backend == native_app: use recommended_action / app_open(package)
if preferred_backend == android_web: use recommended_action / url_open(url)
if install_option exists and native app is materially better: ask for approval, then store_install
snapshot → reason over refs → act
```

### Pattern 1: Open app → snapshot → reason → act

```
snapshot(hybrid) → inspect `top_refs` + `refs` yourself → act(click, ref=a4)
→ if post_action_snapshot exists: use it
→ else snapshot → verify
```

### Pattern 2: Home → open app → navigate

```
snapshot → find ref → act(click, app icon) → app_open(package) →
snapshot → find ref → act(click)
→ prefer post_action_snapshot
→ fallback to snapshot if stale
```

### Pattern 3: Ambiguous screen → screenshot-aware reasoning

```
snapshot(hybrid, include_screenshot=true) →
reason over refs + screenshot yourself →
act(click, ref=...)
```

Use this especially when:
- `screen_context.archetype` is generic but the visible UI is obviously richer than the tree
- `details=` are missing on repeated rows
- the top targets are icon-heavy, generic, or too close in meaning

### Pattern 4: Agent still unsure → decide_next fallback

```
snapshot(hybrid, include_screenshot=true) →
if still ambiguous after reasoning:
decide_next(llm_vision) →
if decision returns ref: act(click) or auto_execute=true
if execution.post_action_snapshot exists: use it immediately
```

### Pattern 5: Sparse tree (icons/images only)

```
snapshot(include_screenshot=true) → if source == "adb_screenshot_only" or refs < 5:
  screenshot → use visual analysis on screenshot
```

### Pattern 6: Click by bounds (unlabeled button)

```
snapshot → find ref with matching bounds or container context →
act(click_center, ref=a18)
```

### Pattern 7: Direct install without store UI

```
store_search(store="aptoide", query="amazon kindle") →
choose exact package →
android_admin { action: "store_install", store: "aptoide", package: "com.amazon.kindle", approved: true }
```

Use this when the package is known or when a trusted direct store candidate is available. Prefer it over clicking Aptoide `INSTALL` and then fighting the Android package installer dialog.

### Pattern 8: Runtime stuck or bridge stale

```
android_admin { action: "recover", mode: "user" }
```

Use this when the UI stops responding, the bridge looks stale, refs are repeatedly invalid, or the Waydroid session needs a non-root reset. If the container itself is broken and the user explicitly approves root-side recovery:

```
android_admin { action: "recover", mode: "system", approved: true }
```

## Noise Filtering

**Always noise (filtered by ranking):**
- `com.android.launcher3` (launcher) when foreground is an app
- `com.android.systemui` (system UI) when foreground is an app

**Contextual noise (OVERLAY flag in summary):**
- Launcher or system UI elements that appear on top of an app

**Keep (don't filter from tree):**
- Launcher/system UI when the launcher IS the foreground app
- All elements of the foreground app

## Error Recovery

| Error | Recovery |
|-------|---------|
| `"exec-out returned no PNG"` | Retry; forward will be re-established |
| `"Accessibility bridge unavailable"` | Retry snapshot; bridge may be restarting |
| `"Snapshot is missing or stale"` | Re-run snapshot before acting |
| `verified: false` after `act` | Prefer `post_action_snapshot` if present; otherwise run `snapshot(hybrid, include_screenshot=true)` |
| `"SYS_KEYS has no physical keys"` | `app_open` used monkey; use am start instead |
| Screen is blank/black | `waydroid restart`; check Weston compositor |
| `foreground_package: null` | Bridge not bound; use `adb shell settings` as fallback |

## Reference Labels

When a UI node has no text/content_desc, the agent tries these in order:
1. `hint_text`
2. `resource_id` token (e.g., `btn_search` → "btn search")
3. `class_name` token (e.g., `android.widget.Button` → "button")

A `resource_id` on an actionable node implies an implicit label.

## Protected Actions

These labels are blocklisted from automated clicks:
- `protected`, `lock`, `delete`, `remove`, `uninstall`, `dial`, `emergency`

Always verify the target label before clicking.

## Screenshot Fallback Triggers

Request a standalone `screenshot` action when:
- `source` is `"adb_screenshot_only"`
- `refs` count is very low (< 5 meaningful nodes)
- The UI contains primarily icons or image-based content
- Navigation fails after 2 attempts using refs
- `act` returns `verified: false` and the post-action snapshot is sparse or ambiguous

If your active OpenClaw model can already consume the attached screenshot, prefer reasoning over the normal `snapshot` result first. Use `decide_next` only when that still does not resolve the ambiguity.

## Implementation Notes

- `bridge.py` uses `adb forward` with retry logic (up to 3 attempts, forward refresh on retry)
- `waydroid.py` uses `adb shell screencap` (NOT `exec-out`) for reliable PNG output
- `ensure_bridge_ready()` calls `forward_bridge()` to set up the ADB forward before every bridge operation
- The Android accessibility service runs as `ai.openclaw.androidbridge` inside Waydroid
- `node_key` is stable within a session but may change after app restart
