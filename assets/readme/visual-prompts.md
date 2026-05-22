# README Visual Communication Briefs

These prompts describe what each README visual should communicate. They are intentionally not layout specifications. A designer or agent should use them to make independent visual decisions while preserving the product message, hierarchy, and technical truth.

## Overall README Visual System

Create a cohesive set of visuals for a public GitHub repository that introduces Clawdroid as production-grade infrastructure for using real Android apps from AI agents on Linux. The visuals should make the project feel credible, useful, and safe without looking like a toy demo or an internal engineering sketch.

The reader should understand these points after scanning the visuals:

- Clawdroid turns Waydroid Android apps into a local tool surface for agents.
- Hermes and OpenClaw do not control Android directly; they call the Clawdroid daemon.
- The Clawdroid daemon is the central boundary for state, routing, safety checks, snapshots, actions, recovery, and optional vision decisions.
- Android interaction happens through an Android-side bridge, accessibility data, screenshots, and ADB fallback.
- Agents act on short-lived snapshot references such as `REF A1` and `REF A2`, not permanent selectors or direct pixel clicks.
- Risky operations are gated. Installs, recovery, protected labels, and host/device mutation require explicit approval.
- The setup story should feel approachable: install, initialize Android, enable the bridge, configure agents, run doctor, run smoke tests.
- Optional visual fallback is daemon-owned. Vision models can suggest a daemon reference, but they do not bypass the daemon or safety checks.

Avoid visuals that imply:

- Hermes/OpenClaw directly control the Android UI.
- A model clicks pixels directly.
- References are stable global IDs.
- `android_admin` is part of the normal action path.
- Safety is only a documentation promise rather than an enforced runtime boundary.
- Waydroid setup is manual-only or fragile.

Use professional terminology that a new user can understand. Prefer phrases like "snapshot references", "Android bridge", "approval gate", "host daemon", "Waydroid Android", and "validated action". Avoid unexplained internals such as `adbd`, raw class names, or lowercase `a1/a2/a3` labels unless a literal code example is required. When showing references visually, use a polished label such as `REF A1`, `REF A2`, or `REF A3`.

## `clawdroid-hero.svg`

Communicate the core product idea in one glance: an agent asks for Android work, Clawdroid sits in the middle as the daemon/control boundary, and a real Android UI in Waydroid is controlled safely through that daemon.

The most important message is directional truth. Hermes/OpenClaw should be visually upstream of Clawdroid, and Waydroid Android should be downstream. The daemon should clearly be between them, not a sidecar. The agent should appear to issue high-level Android tool requests, while the daemon translates those requests into routing, snapshots, reference actions, verification, and safety enforcement.

The visual should make snapshot references feel intentional and polished. If references are shown, they should be presented as short-lived targets returned by a snapshot, using labels like `REF A1`, `REF A2`, and `REF A3`. The image should not make references look like casual lowercase scraps or random debug output.

The Waydroid side should feel like a real Android app surface, but the exact app content is not important. The user should come away understanding that Clawdroid can open apps, observe screens, choose targets, perform actions, and verify results. The safety boundary should be evident but not overwhelming.

Do not make this a generic cloud architecture diagram. It should feel local, concrete, and agent-centric: a local agent talks to a local daemon, which controls local Waydroid.

## `live-amazon-toothbrush.webp`

Communicate proof that the system is real, not only a conceptual diagram. The image should show a captured Hermes or Telegram conversation where tool calls happen, alongside a Waydroid Android app window showing Amazon search results.

The visual should make the relationship between the real conversation and the real Android UI clear:

- The conversation is the agent-facing surface.
- The Android app is actually running inside Waydroid.
- The host Clawdroid daemon mediates between the two.
- Inside Waydroid, the Android bridge and ADB path are the mechanism the daemon uses to observe and act.

The screenshot should preserve enough of the Telegram conversation to show the user request, the `android_*` tool-call sequence, and the agent's interpretation of the result. It should not include unrelated headers, personal chrome, or excessive surrounding desktop area. The Waydroid crop should include enough title/context to show it is running under Waydroid/Weston, but should avoid dead black space or irrelevant desktop windows.

The annotation should be explanatory, not cluttered. It should label the host `Clawdroid daemon` and the Android-side `Android bridge + ADB` relationship in plain language. It should not use unexplained internals such as `adbd`. The label should not cover important Amazon or Telegram content.

The image should feel like a real-world validation artifact: "This is what it looks like when an agent uses Clawdroid to search Amazon in Android."

## `architecture.svg`

Communicate the system architecture at a high level. The diagram should answer: "What are the major components, and where does responsibility live?"

The central message is that agents call the Python daemon, and the daemon owns Android state and execution. The daemon should be the focal point. It should group responsibilities such as routing, snapshots, actions, verification, safety gates, and optional decision fallback.

The agent layer should show Hermes and OpenClaw as clients of the daemon, using the normal runtime and optional admin tools. The public API should be visible as an external surface, but not mistaken for a separate runtime. The Waydroid side should show Android bridge, ADB fallback, apps, stores, and settings as execution targets controlled by the daemon.

Host services should be separate from Android app execution. The diagram should make it clear that Waydroid UI supervision and Weston-on-X11 support are host-side responsibilities, not app-level Android capabilities.

The reader should be able to scan this diagram and understand that Clawdroid is not a browser plugin, not a cloud service, and not a direct UI automation script. It is a local daemon coordinating agent tools, Android state, safety, Waydroid, and host services.

## `agent-loop.svg`

Communicate the normal runtime loop agents should follow:

1. Start from a user goal.
2. Route to the right Android or web/service option.
3. Observe the Android screen through a fresh snapshot.
4. Select a target from the snapshot references.
5. Act on that reference.
6. Verify the result.
7. Return to observation after meaningful UI changes.

The most important behavior to convey is that snapshot references are short-lived. The agent should not treat `REF A1` or `REF A2` as permanent identifiers. The loop should make it obvious that observation happens repeatedly, and that action without a fresh view is not the intended pattern.

The safety gate should appear as part of the action/verification path, not as a decorative warning. The ambiguous-screen path should show that `decide_next` is a daemon fallback when deterministic target selection is not enough.

Avoid making the loop look like a generic flowchart for any agent. It should be specific to Clawdroid: route, snapshot, reference selection, action, verification, and resnapshot.

## `safety-model.svg`

Communicate the safety model without making it look scary or bureaucratic. The visual should show that normal runtime actions are ergonomic, while sensitive actions and protected operations are gated.

The diagram should distinguish at least these paths:

- Normal runtime actions: snapshot, wait, app open, ref action.
- Daemon checks: fresh snapshot ID, visible reference, valid target.
- Android action execution: Android bridge or ADB, followed by verification.
- Sensitive runtime or mutation paths: installs, removal, recovery, bridge configuration, host/device recovery.
- Approval gate: explicit approval and protected label handling.

The key message is not "Clawdroid blocks everything". The key message is "Clawdroid lets normal work flow quickly, while dangerous or sensitive operations require explicit approval and cannot be bypassed through casual tool use."

The phrase "blocked without approval" should be clearly associated with protected/sensitive flow. It should not float ambiguously or look like a loose annotation outside a controlled path.

Avoid implying that coordinate actions can bypass protected action policy. Avoid implying that admin operations are normal runtime operations.

## `install-flow.svg`

Communicate that setup is guided, staged, and verifiable. The visual should reduce anxiety for new users by showing the major steps in a clean sequence.

The setup story should include:

- Host package and dependency setup.
- Waydroid image initialization and UI startup.
- Android bridge APK build/install/enablement.
- Optional extras such as stores, GApps, and ARM translation.
- Hermes/OpenClaw agent configuration.
- Optional visual fallback configuration.
- Doctor checks.
- Smoke test.

The reader should understand that setup is not just copying plugin files. Clawdroid has host dependencies, Android image concerns, an Android-side bridge, service integration, and verification steps. At the same time, it should feel manageable because the setup script guides the flow.

Do not overemphasize every flag or edge case in this visual. The README text can explain flags. The visual should communicate the staged install lifecycle and the existence of doctor/smoke validation.

## `vision-fallback.svg`

Communicate optional daemon-owned vision fallback for ambiguous screens. The image should show that the daemon combines screenshots and accessibility references, sends bounded context to a configured model, receives a proposed reference/action, validates that decision, and routes execution back through the daemon safety path.

The most important non-negotiable message: there is no generic direct vision-click path. The model should not look like it controls pixels. It proposes a daemon reference such as `REF A2`, and the daemon validates and acts.

The visual should include the inputs:

- Screenshot.
- Accessibility references.
- Bounded screen context.

It should include the provider:

- UI-TARS / OpenRouter.
- A local compatible endpoint.

It should include the output:

- Validated action.
- Target reference.

It should show the forbidden/non-goal path:

- No generic `vision_analyze` bypass.

The reader should understand that visual fallback is optional, configured, bounded, and subordinate to the daemon's safety model.

## `doctor-output.svg`

Communicate operational confidence. This visual should feel like a clean terminal-style health report that tells a user: "You can verify whether the install is healthy."

The doctor output should cover representative checks such as:

- Project path.
- Waydroid container.
- ADB authorization.
- Android bridge reachability.
- Google Play or stores when present.
- ARM translation when present.
- LLM/vision configuration when present.
- Optional stores or extras as warnings rather than failures.

The visual should distinguish pass, warning, and fail states clearly. It should not look like a raw dump of logs. The goal is to make doctor feel like a polished diagnostic tool that helps users recover from setup issues.

Keep terminal realism, but avoid making the text too small or too dense. It should be readable in a GitHub README at typical desktop widths.

## `clawdroid-logo.webp`

Use the logo to establish brand presence early in the README. The logo should feel prominent, but it should not waste vertical space with excessive empty padding. It should be tightly cropped enough that the mark and wordmark are visible at README width without pushing the quick-start content too far down.

The logo should not be treated as a diagram. It is a brand anchor before the technical explanation begins.
