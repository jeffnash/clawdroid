# Public Release Checklist

Use this before pushing Clawdroid to a public GitHub repository.

## Required Checks

```bash
./scripts/release_check.sh
```

Run the Android build when Gradle, Android SDK, companion app, or setup changes are involved:

```bash
./scripts/release_check.sh --include-android-build
```

## Repository Hygiene

- Keep `.cache/`, `node_modules/`, `.venv/`, Gradle build output, APKs, logs, and Python bytecode untracked.
- Do not commit local `$HOME`, ADB serial, token, API key, or provider credential values.
- Keep OpenClaw, Hermes, daemon, and companion docs aligned when tool actions change.
- Keep `openclaw-plugin/index.js`, `hermes-plugin/schemas.py`, and `python-daemon/openclaw_android_daemon/server.py` action schemas in sync.
- Keep recovery semantics documented in both agent skills when `android_admin recover` changes.

## Public Setup Story

A new user should be able to understand these paths from the README:

- OpenClaw only: run setup with `--install-openclaw`.
- Hermes only: run setup and `./scripts/install_hermes_plugin.sh`, or pass `--install-hermes-plugin`.
- Both agents: pass both `--install-openclaw` and `--install-hermes-plugin`.
- Manual privilege mode: use `--sudo-mode manual`.
- No default stores: use `--skip-default-stores`.

## Smoke Tests

Daemon:

```bash
curl -fsS http://127.0.0.1:48765/v1/status | jq .
```

OpenClaw:

```bash
openclaw plugins inspect android-waydroid --json
```

Hermes:

```bash
hermes -t clawdroid -z 'Check Android status with the android tool'
```
