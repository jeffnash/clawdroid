# Headless Server Setup

Clawdroid can run on a headless Linux host, but it still needs the same things Waydroid needs on a desktop:

- a Linux kernel with Android binder support and memfd or ashmem support
- root access for Waydroid setup
- a working user session bus
- a virtual display for nested Weston
- enough container privileges to run Waydroid

This is usually a good fit for a VPS, bare-metal server, workstation, or privileged VM.

Railway-style app containers can run headed browser automation with Xvfb, but that is a different problem than running Waydroid. A Railway probe on May 9, 2026 confirmed that a standard Railway Docker service can run as root, but does not expose the Waydroid-specific pieces Clawdroid needs:

- no binder devices such as `/dev/binder`, `/dev/hwbinder`, or `/dev/vndbinder`
- no `binder` filesystem support available to mount
- no `binder_linux` kernel module visible inside the service
- no `CAP_SYS_ADMIN` or `CAP_SYS_MODULE`
- read-only cgroup/sysfs surfaces

That means Railway is a reasonable place to run Hermes/OpenClaw with Chromium under Xvfb, or to run an agent that talks to a separate Clawdroid host. It is not currently a verified target for running Waydroid itself inside the same standard Railway service.

## Recommended Shape

Use one Linux user to own the agent session:

```text
systemd system services
  waydroid-container

systemd user services
  virtual X display
  Clawdroid Waydroid UI supervisor
  Clawdroid daemon
  Hermes or OpenClaw agent
```

The virtual display only needs to host Weston and Waydroid. Agents interact through the daemon API, not by watching the display directly.

## Install Host Packages

On Ubuntu or Debian-like hosts:

```bash
sudo apt-get update
sudo apt-get install -y \
  dbus-user-session \
  x11-utils \
  xvfb \
  weston \
  adb \
  curl \
  jq
```

Then run Clawdroid setup as the agent user:

```bash
./setup_everything.sh \
  --install-system-deps \
  --init-waydroid \
  --install-hermes-plugin
```

Add `--install-openclaw` and `--enable-admin-tool` if this host is for OpenClaw.

## Check Host Kernel Guards First

Some hosts carry "reboot on any kernel problem" sysctl guards (often left
behind by earlier debugging). `kernel.panic_on_warn=1` is the dangerous one:
any benign kernel WARNING instantly panics and reboots the host, and **Weston
reliably triggers such a WARNING on kernel 6.17+** (an executable mmap probe
hits the `path_noexec` warning). On a guarded host that autostarts the
display stack, that produces an infinite boot → Weston → panic → reboot loop
roughly every 45 seconds, and the machine becomes almost unreachable over
SSH. This exact failure took down a production Clawdroid host in July 2026.

`./doctor.sh` fails loudly when it sees `panic_on_warn=1` and warns about
recent kernel panic dumps in pstore. To fix it persistently:

```bash
./scripts/install_headless_display.sh --fix-kernel-guards
```

## Start A Virtual Display

Use the installer; it creates a supervised user service for Xvfb, persists
`DISPLAY` for boot-time user units via `~/.config/environment.d/`, and checks
the kernel guards described above:

```bash
./scripts/install_headless_display.sh --now
./scripts/install_user_service.sh --headless
```

`--headless` rebinds the UI supervisor unit to `default.target` (a headless
host never activates `graphical-session.target`) and bakes `DISPLAY` into the
unit, so everything starts at boot with lingering enabled. Override the
display or geometry with `OPENCLAW_ANDROID_DISPLAY` and
`OPENCLAW_ANDROID_HEADLESS_RESOLUTION`.

Manual alternative for a throwaway host:

```bash
export DISPLAY=:99
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

Xvfb :99 -screen 0 1600x900x24 -nolisten tcp >/tmp/clawdroid-xvfb.log 2>&1 &
systemctl --user import-environment DISPLAY XDG_RUNTIME_DIR
```

If the host does not already have a user DBus session, start one before running user services:

```bash
export DBUS_SESSION_BUS_ADDRESS="$(dbus-daemon --session --fork --print-address)"
systemctl --user import-environment DBUS_SESSION_BUS_ADDRESS
```

If you run a full desktop session (e.g. XFCE) on the virtual display instead
of bare Xvfb — for example to get a Secret Service keyring — disable its
power manager and screensaver in that session; a desktop power manager on a
headless display can suspend the whole host.

## Start Clawdroid

Start the UI supervisor and daemon:

```bash
systemctl --user restart openclaw-android-waydroid-ui.service
systemctl --user restart openclaw-android-waydroid.service
```

The UI supervisor starts nested Weston on `wayland-1` inside the virtual X display, starts the Waydroid session against that Weston socket, and attaches the Android UI.

Verify:

```bash
systemctl --user status openclaw-android-waydroid-ui.service
systemctl --user status openclaw-android-waydroid.service
curl http://127.0.0.1:48765/v1/status | jq
```

## Keep It Running

`scripts/install_headless_display.sh` already installs the supervised Xvfb
unit (`clawdroid-headless-display.service`, `Restart=always`) and the
`environment.d` entry that user units need at boot, so there is nothing else
to hand-roll.

If the user service manager is not active after logout, enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

## Platform Notes

Railway-style PaaS containers are fine for Xvfb plus Chromium-style browser automation. They are normally the wrong layer for Waydroid because Waydroid depends on Android binder support and host/container privileges that the app container may not expose. Use them to host an agent that talks to a separate Clawdroid machine, or use a VM/VPS where you control the kernel and services.

## Docker

The repository includes two Docker paths:

- `docker/smoke/Dockerfile` runs the setup guide in a disposable Ubuntu container and records where host-level requirements fail.
- `docker/production/Dockerfile` runs the real setup flow, starts Xvfb, starts Waydroid, and serves the Clawdroid daemon from inside the container.

Build and run the production-style image:

```bash
sudo docker build -f docker/production/Dockerfile -t clawdroid:local .
sudo docker run --privileged --name clawdroid \
  -p 127.0.0.1:48765:48765 \
  -v clawdroid-data:/var/lib/clawdroid \
  -v clawdroid-waydroid:/var/lib/waydroid \
  -v clawdroid-cache:/root/.cache \
  clawdroid:local
```

Run the faster setup probe:

```bash
sudo ./scripts/docker_smoke_setup.sh
```

These containers intentionally require `--privileged`. If the host kernel does not provide Android binder support to Docker, the image can build successfully while Waydroid still fails to start.

To test a platform yourself, run a small probe that checks:

```bash
id
capsh --print
ls -la /dev/binder* /dev/hwbinder /dev/vndbinder /dev/binderfs 2>/dev/null
modprobe binder_linux devices=binder,hwbinder,vndbinder
mkdir -p /tmp/binderfs && mount -t binder binder /tmp/binderfs
findmnt /sys/fs/cgroup
```

If those checks cannot expose binder devices or mount binderfs, Waydroid will not start there even if Xvfb and Chromium work.

If Clawdroid is remote from the agent, keep the daemon bound to localhost and expose it through a private tunnel such as Tailscale, WireGuard, or SSH forwarding. Do not expose `127.0.0.1:48765` directly to the public internet.

## Recovery

For normal stuck UI or bridge state:

```bash
curl -s http://127.0.0.1:48765/v1/admin/dispatch \
  -H 'content-type: application/json' \
  -d '{"action":"recover","mode":"user"}' | jq
```

For host-level Waydroid/container breakage, use the sudo helper from the repo root:

```bash
sudo ./scripts/restart_everything_sudo.sh
```
