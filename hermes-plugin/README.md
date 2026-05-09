# Clawdroid Hermes plugin

This plugin registers two Hermes tools:

- `android`: runtime Android interaction through the local daemon
- `android_admin`: opt-in administrative actions such as APK install/remove and Waydroid extras

`android_admin` also exposes `recover` for stuck runtime recovery. Use `mode: "user"` first; it resets the user-session supervisor, reconnects ADB, refreshes the bridge forward, wakes the guest, and clears stale snapshots. Use `mode: "system"` only with explicit approval because it runs the sudo restart helper when passwordless sudo is available.

The plugin is intentionally thin. It forwards JSON requests to the Python daemon at `http://127.0.0.1:48765` by default.

## Install

From the repository root:

```bash
./scripts/install_hermes_plugin.sh
```

Then restart Hermes so it reloads plugins:

```bash
hermes gateway restart
```

For a system gateway service, run the restart command appropriate for your install, usually:

```bash
sudo hermes gateway restart --system
```

## Configuration

Set `CLAWDROID_DAEMON_BASE_URL` if the daemon is not listening on the default URL:

```bash
export CLAWDROID_DAEMON_BASE_URL=http://127.0.0.1:48765
```

The legacy `CLAWDROID_DAEMON_URL` and `OPENCLAW_ANDROID_DAEMON_BASE_URL` variables are also honored.

## Usage

Start Hermes with the `clawdroid` toolset or enable it in `hermes tools`:

```bash
hermes -t clawdroid
```

Recommended control loop:

1. `android {"action": "task_route", "goal": "..."}`
2. `android {"action": "snapshot", "snapshot_mode": "interactive"}`
3. Choose a ref from the returned snapshot.
4. `android {"action": "act", "snapshot_id": "...", "ref": "a1", "op": "click"}`
5. Re-snapshot after each meaningful transition.
