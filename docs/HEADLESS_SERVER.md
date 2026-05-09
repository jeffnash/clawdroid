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

## Start A Virtual Display

For a simple headless host, start Xvfb on `:99`:

```bash
export DISPLAY=:99
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

Xvfb :99 -screen 0 1600x900x24 -nolisten tcp >/tmp/clawdroid-xvfb.log 2>&1 &
```

Import that display into the user service environment:

```bash
systemctl --user import-environment DISPLAY XDG_RUNTIME_DIR
```

If the host does not already have a user DBus session, start one before running user services:

```bash
export DBUS_SESSION_BUS_ADDRESS="$(dbus-daemon --session --fork --print-address)"
systemctl --user import-environment DBUS_SESSION_BUS_ADDRESS
```

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

For long-lived headless hosts, create a small user service for Xvfb instead of starting it by hand. Example:

```ini
[Unit]
Description=Clawdroid virtual X display

[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1600x900x24 -nolisten tcp
Restart=always

[Install]
WantedBy=default.target
```

Save it as `~/.config/systemd/user/clawdroid-xvfb.service`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now clawdroid-xvfb.service
systemctl --user import-environment DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
```

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
