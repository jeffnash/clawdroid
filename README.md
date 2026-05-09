# Clawdroid

<p align="center">
  <img src="assets/clawdroid-logo.webp" alt="Clawdroid logo" width="720">
</p>

Android apps as a local tool surface for AI agents on Linux.

Clawdroid lets **OpenClaw** and **Hermes** operate real Android apps through Waydroid. It exposes Android as generic agent tools: route to an app, take a UI `snapshot`, act on short-lived refs such as `a1`, then snapshot again. The runtime uses an Android accessibility bridge, screenshots, and ADB fallbacks; no app-specific adapters are required.

Use Android when the native app is simpler than the desktop web flow: messaging, maps, rides, delivery, shopping, banking/auth, subscriptions, or web flows blocked by captcha. Use desktop web when Android is unavailable, worse, or explicitly requested.

<p align="center">
  <img src="assets/readme/clawdroid-hero.svg" alt="Clawdroid controlling a Waydroid Android app through agent refs" width="100%">
</p>

## Visual Guide

<p align="center">
  <img src="assets/readme/architecture.svg" alt="Clawdroid architecture: agents call the daemon, which controls Waydroid through bridge, ADB, and safety gates" width="100%">
</p>

<p align="center">
  <img src="assets/readme/agent-loop.svg" alt="Clawdroid agent loop: route, snapshot, choose ref, act, verify, and snapshot again" width="100%">
</p>

<p align="center">
  <img src="assets/readme/safety-model.svg" alt="Clawdroid safety model with approval gates for installs, recovery, and protected actions" width="100%">
</p>

## Quick Start

For most users, run guided setup from an interactive terminal:

```bash
./setup_everything.sh
```

Guided setup detects OpenClaw and Hermes homes/configs, asks what to install, and can run the post-install smoke test.

<p align="center">
  <img src="assets/readme/install-flow.svg" alt="Clawdroid setup flow from host packages to doctor and smoke tests" width="100%">
</p>

Noninteractive examples:

```bash
# OpenClaw
./setup_everything.sh --install-system-deps --install-openclaw --init-waydroid --extras microg --enable-admin-tool

# Hermes
./setup_everything.sh --install-system-deps --install-hermes-plugin --init-waydroid --extras microg

# Both
./setup_everything.sh --install-system-deps --install-openclaw --install-hermes-plugin --init-waydroid --extras microg --enable-admin-tool
```

After installing the Hermes plugin, restart Hermes:

```bash
sudo hermes gateway restart --system
```

## Common Setup Options

Defaults:

- Daemon: `http://127.0.0.1:48765`
- Android bridge forward: `127.0.0.1:49317`
- ARM translation: `libndk`
- Device profile: `samsung-galaxy-s24-ultra`
- Default stores: F-Droid, Aurora Store, and Aptoide

Useful flags:

```text
--interactive                      Guided mode
--yes                              Guided defaults without prompts
--sudo-mode manual                 Print root commands instead of running sudo inline
--arm-translation libndk|libhoudini|both|none
--with-gapps / --extras gapps      Install Google framework / Play Store support
--skip-default-stores              Skip F-Droid, Aurora Store, and Aptoide
--device-profile NAME              Apply a different Android profile
--daemon-base-url URL              Configure a non-default daemon URL
--llm-provider openrouter|local|custom|skip
                                   Configure Android visual fallback
--configure-llm-only               Configure visual fallback without reinstalling Waydroid
--smoke-test                       Run post-install smoke checks
```

Existing OpenClaw/Hermes installs can be pinned explicitly:

```bash
./setup_everything.sh \
  --openclaw-home /home/alice/.openclaw \
  --openclaw-config /home/alice/.openclaw/openclaw.json \
  --openclaw-extensions-dir /home/alice/.openclaw/extensions \
  --hermes-home /home/alice/.hermes \
  --hermes-user alice \
  --daemon-base-url http://127.0.0.1:48765 \
  --install-hermes-plugin \
  --smoke-test
```

For root/system Hermes, use `--hermes-system` and, if needed, `--hermes-home /root/.hermes`.

## Android Visual Fallback

Clawdroid can use a daemon-owned vision model for ambiguous screens and custom views. Guided setup prompts for this. OpenRouter UI-TARS is the default:

```bash
./setup_everything.sh --configure-llm-only --llm-provider openrouter --llm-api-key-env OPENROUTER_API_KEY
```

For a local OpenAI-compatible endpoint:

```bash
./setup_everything.sh --configure-llm-only --llm-provider local --llm-base-url http://127.0.0.1:8000/v1 --llm-model bytedance/ui-tars-1.5-7b
```

The config lives at `~/.config/openclaw-android-waydroid/llm.json`. Saved keys go in `~/.config/openclaw-android-waydroid/env` with user-only permissions. Hermes/OpenClaw skills should use daemon-backed `decide_next` only when `status.llm.configured` and `status.llm.supports_images` are true.

<p align="center">
  <img src="assets/readme/vision-fallback.svg" alt="Clawdroid daemon-owned vision fallback combines screenshot, refs, model output, and validation" width="100%">
</p>

## Google Play / GApps

Install Play Store support with:

```bash
./setup_everything.sh --with-gapps
```

or on an existing Waydroid image:

```bash
./scripts/install_waydroid_extras.sh --extras gapps
```

After image-level changes such as GApps or ARM translation, restart with:

```bash
sudo ./scripts/restart_everything_sudo.sh
```

If Android shows `Allow USB debugging?`, check **Always allow from this computer** and tap **Allow**. Fresh images, GApps changes, and host ADB state can surface this prompt even though setup configures local Waydroid ADB for noninteractive use.

Play Store may still require normal Google device certification or sign-in recovery. To certify a GApps Waydroid guest, get the Google Services Framework Android ID:

```bash
sudo waydroid shell -- sh -c "sqlite3 /data/data/*/*/gservices.db 'select value from main where name = \"android_id\";'"
```

Register it at <https://www.google.com/android/uncertified>, wait for propagation, then restart Waydroid if Play Store still reports the device as uncertified. Reference: [Waydroid Google Play Certification](https://docs.waydro.id/faq/google-play-certification).

You can also let Clawdroid fetch and print the ID:

```bash
./scripts/google_play_certification.sh --open-url
```

## Smoke Tests

Start with doctor when debugging an install:

<p align="center">
  <img src="assets/readme/doctor-output.svg" alt="Terminal style Clawdroid doctor output with pass and warning rows" width="100%">
</p>

```bash
./doctor.sh
./doctor.sh --repair --user "$USER"
```

The smoke script waits for Waydroid, ADB, the daemon, and the accessibility bridge before acting.

```bash
./scripts/smoke_test_install.sh --layer auto
```

Useful variants:

```bash
./scripts/smoke_test_install.sh --layer daemon --skip-visible-action
./scripts/smoke_test_install.sh --layer hermes --hermes-user alice --hermes-home /home/alice/.hermes
./scripts/smoke_test_install.sh --layer auto --wait-timeout 360
```

Manual checks:

```bash
curl http://127.0.0.1:48765/v1/status | jq
openclaw plugins inspect android-waydroid --json
hermes -t clawdroid -z 'Check Android status with the android tool'
hermes -t clawdroid -z 'Check Android status with the android tool, then use coordinate_act to tap 24,24, then report the current app.'
```

Docker setup probe:

```bash
sudo ./scripts/docker_smoke_setup.sh
```

The Docker probe validates the public setup path without touching the host Waydroid install. It still depends on host binder/cgroup/privilege support.

## Uninstall / Reset

Remove Clawdroid services/plugins while keeping the Waydroid image:

```bash
./scripts/uninstall_everything.sh --user "$USER"
```

Reset everything, including Waydroid and repo-local caches:

```bash
./scripts/uninstall_everything.sh \
  --user "$USER" \
  --purge-waydroid \
  --purge-repo-cache
```

After a purge, rerun guided setup or a fully flagged setup command.

## Agent Tools

`android` is the normal runtime tool for status, routing, launching, snapshots, screenshots, ref actions, coordinate actions, waits, and optional `decide_next` fallback. `android_admin` is the opt-in host-control tool for recovery, Waydroid start/stop, installs, store helpers, extras, profiles, and bridge allowlists.

Typical flow:

```text
task_route(goal) -> app_open/url_open/store option
snapshot(snapshot_mode="interactive")
act(snapshot_id, ref, op)
snapshot again
```

Recovery examples:

```json
{"action": "recover", "mode": "user"}
```

```json
{"action": "recover", "mode": "system", "approved": true}
```

## Runtime Notes

Waydroid needs a Wayland compositor. On X11, Clawdroid starts nested Weston on `wayland-1`; on Wayland, it reuses the session compositor. If setup succeeds but the Android UI is not visible:

```bash
sudo ./scripts/restart_everything_sudo.sh
```

`restart_everything_sudo.sh` performs a real Waydroid container restart by default and waits for Android/daemon readiness. Use `--soft` for a lighter UI/service reset that keeps the running container.

The daemon and UI supervisor are user-session services. For servers without a real desktop session, see [Headless server setup](docs/HEADLESS_SERVER.md).

## More Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Headless server setup](docs/HEADLESS_SERVER.md)
- [Public release checklist](docs/PUBLIC_RELEASE.md)
- [OpenClaw plugin](openclaw-plugin/README.md)
- [Hermes plugin](hermes-plugin/README.md)
- [Python daemon](python-daemon/README.md)
- [Android companion app](android-companion/README.md)
