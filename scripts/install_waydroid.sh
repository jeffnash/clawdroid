#!/usr/bin/env bash
    set -Eeuo pipefail
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    source "$SCRIPT_DIR/common.sh"

    INSTALL_SYSTEM_DEPS=0
    INIT_WAYDROID=0
    START_WAYDROID=0
    PRINT_IP=0
    CONFIGURE_ADB=0
    WINDOW_BACKEND="${OPENCLAW_ANDROID_WINDOW_BACKEND:-auto}"

    set_waydroid_property_file_value() {
      local file="$1"
      local key="$2"
      local value="$3"
      [[ -f "$file" ]] || return 0
      if grep -q "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
      else
        printf '%s=%s\n' "$key" "$value" >>"$file"
      fi
    }

    configure_waydroid_adb() {
      log_step "Configuring Waydroid for local noninteractive ADB"
      set_waydroid_property_file_value /var/lib/waydroid/waydroid_base.prop ro.adb.secure 0
      set_waydroid_property_file_value /var/lib/waydroid/waydroid_base.prop ro.debuggable 1
      set_waydroid_property_file_value /var/lib/waydroid/waydroid.prop ro.adb.secure 0
      set_waydroid_property_file_value /var/lib/waydroid/waydroid.prop ro.debuggable 1
    }

    window_backend_needs_xwininfo() {
      case "$WINDOW_BACKEND" in
        x11) return 0 ;;
        wayland) return 1 ;;
        auto)
          [[ "${XDG_SESSION_TYPE:-}" == "x11" ]] && return 0
          [[ -n "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]] && return 0
          return 1
          ;;
        *) fatal "Invalid OPENCLAW_ANDROID_WINDOW_BACKEND: $WINDOW_BACKEND" ;;
      esac
    }

    graphical_session_available() {
      [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] || return 1
      case "$WINDOW_BACKEND" in
        x11) [[ -n "${DISPLAY:-}" ]] ;;
        wayland) [[ -n "${WAYLAND_DISPLAY:-}" ]] ;;
        auto) [[ -n "${WAYLAND_DISPLAY:-}" || -n "${DISPLAY:-}" ]] ;;
        *) fatal "Invalid OPENCLAW_ANDROID_WINDOW_BACKEND: $WINDOW_BACKEND" ;;
      esac
    }

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --install-system-deps) INSTALL_SYSTEM_DEPS=1 ;;
        --init-waydroid) INIT_WAYDROID=1 ;;
        --start-waydroid) START_WAYDROID=1 ;;
        --print-ip) PRINT_IP=1 ;;
        --configure-adb) CONFIGURE_ADB=1 ;;
        *) fatal "Unknown argument: $1" ;;
      esac
      shift
    done

    distro="$(detect_distro)"

    if [[ $CONFIGURE_ADB -eq 1 ]]; then
      configure_waydroid_adb
      exit 0
    fi

    if [[ $INSTALL_SYSTEM_DEPS -eq 1 ]]; then
      log_step "Installing system dependencies for distro: $distro"
      need_weston=0
      if ! command -v weston >/dev/null 2>&1; then
        need_weston=1
      fi
      need_xwininfo=0
      if window_backend_needs_xwininfo && ! command -v xwininfo >/dev/null 2>&1; then
        need_xwininfo=1
      fi
      case "$distro" in
        ubuntu|debian|linuxmint|pop|zorin)
          sudo_maybe apt update
          packages=(curl ca-certificates unzip jq git python3 python3-venv python3-pip adb openjdk-17-jdk lzip)
          [[ $need_weston -eq 1 ]] && packages+=(weston)
          [[ $need_xwininfo -eq 1 ]] && packages+=(x11-utils)
          sudo_maybe apt install -y "${packages[@]}"
          if ! command -v waydroid >/dev/null 2>&1; then
            curl_fetch https://repo.waydro.id | sudo_maybe bash
            sudo_maybe apt update
            sudo_maybe apt install -y waydroid
          fi
          ;;
        arch|manjaro|endeavouros)
          packages=(curl ca-certificates unzip jq git python python-pip android-tools jdk17-openjdk lzip waydroid)
          [[ $need_weston -eq 1 ]] && packages+=(weston)
          [[ $need_xwininfo -eq 1 ]] && packages+=(xorg-xwininfo)
          sudo_maybe pacman -Sy --needed --noconfirm "${packages[@]}"
          ;;
        fedora)
          packages=(curl ca-certificates unzip jq git python3 python3-pip python3-virtualenv android-tools java-17-openjdk-devel lzip waydroid)
          [[ $need_weston -eq 1 ]] && packages+=(weston)
          [[ $need_xwininfo -eq 1 ]] && packages+=(xorg-x11-utils)
          sudo_maybe dnf install -y "${packages[@]}"
          ;;
        opensuse*|opensuse-tumbleweed)
          packages=(curl ca-certificates unzip jq git python3 python3-pip java-17-openjdk lzip adb waydroid)
          [[ $need_weston -eq 1 ]] && packages+=(weston)
          [[ $need_xwininfo -eq 1 ]] && packages+=(xwininfo)
          sudo_maybe zypper install -y "${packages[@]}"
          ;;
        *)
          warn "Unsupported distro for automated dependency installation: $distro"
          ;;
      esac
    fi

    if [[ $INIT_WAYDROID -eq 1 ]]; then
      log_step "Initializing Waydroid if needed"
      if [[ ! -f /var/lib/waydroid/waydroid.cfg || ! -f /var/lib/waydroid/images/system.img || ! -f /var/lib/waydroid/images/vendor.img ]]; then
        sudo_maybe waydroid init
      else
        warn "/var/lib/waydroid already exists; skipping init"
      fi
      sudo_maybe "$BASH" "$0" --configure-adb
    fi

    if [[ $START_WAYDROID -eq 1 ]]; then
      log_step "Ensuring Waydroid container is running"
      if [[ ! -S /run/dbus/system_bus_socket ]] && command -v dbus-daemon >/dev/null 2>&1 && [[ "$(id -u)" == "0" ]]; then
        mkdir -p /run/dbus
        dbus-daemon --system --fork || true
      fi
      container_state="$(waydroid_container_state || true)"
      if [[ "$container_state" == "RUNNING" ]] || { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet waydroid-container.service; }; then
        log_step "Waydroid container is already running"
      elif [[ "$(id -u)" != "0" ]] && { ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; }; then
        warn "Waydroid container is not running and passwordless sudo is unavailable; start it with sudo ./scripts/setup_root_sudo.sh --start-container"
      else
        sudo_noninteractive_or_plain timeout 90s waydroid container start || warn "failed to start the Waydroid container from setup"
      fi
      sleep 2
      if graphical_session_available; then
        log_step "Starting the desktop-session Waydroid supervisor"
        "$SCRIPT_DIR/import_graphical_env.sh" >/dev/null 2>&1 || warn "failed to import the graphical session environment into the UI supervisor service"
        "$SCRIPT_DIR/waydroid_supervisor_ctl.sh" start >/dev/null 2>&1 ||
          log_step "Desktop-session supervisor is still starting; continuing with ADB readiness checks"
      else
        warn "No usable desktop session environment is available for OPENCLAW_ANDROID_WINDOW_BACKEND=$WINDOW_BACKEND; skipping session/UI start. The desktop autostart supervisor will start Waydroid on login."
      fi
    fi

    if [[ $PRINT_IP -eq 1 ]]; then
      if ! command -v waydroid >/dev/null 2>&1; then
        exit 1
      fi
      ip_line="$(waydroid_ip_address || true)"
      if [[ -n "$ip_line" ]]; then
        printf '%s\n' "${ip_line%%/*}"
        exit 0
      fi
      exit 1
    fi
