from __future__ import annotations

from contextlib import suppress
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .utils import run_cmd, try_cmd, which


@dataclass(slots=True)
class WaydroidStatus:
    installed: bool
    running: bool
    session: bool
    ip: str | None
    adb_serial: str | None
    raw_status: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class WaydroidManager:
    _PNG_HEADER = b"\x89PNG\r\n\x1a\n"

    def __init__(self, adb_serial: str | None = None) -> None:
        self._configured_adb_serial = adb_serial

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _graphical_exec_script(self) -> Path:
        return self._project_root() / "scripts" / "waydroid_graphical_exec.sh"

    def _control_script(self) -> Path:
        return self._project_root() / "scripts" / "waydroid_supervisor_ctl.sh"

    def _graphical_try_cmd(self, args: list[str], timeout: float | None = 30.0) -> tuple[bool, str]:
        script = self._graphical_exec_script()
        if not script.exists():
            return False, f"missing graphical exec helper: {script}"
        return try_cmd([str(script), *args], timeout=timeout)

    def _graphical_run_cmd(
        self, args: list[str], timeout: float | None = 30.0
    ) -> tuple[bool, str]:
        ok, out = self._graphical_try_cmd(args, timeout=timeout)
        if ok:
            return ok, out
        return try_cmd(args, timeout=timeout)

    def _control_runtime(self, action: str, timeout: float | None = 90.0) -> tuple[bool, str]:
        script = self._control_script()
        if not script.exists():
            return False, f"missing supervisor control helper: {script}"
        return try_cmd([str(script), action], timeout=timeout)

    @staticmethod
    def is_installed() -> bool:
        return which("waydroid") is not None

    @staticmethod
    def _status_fields(raw: str | None) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in (raw or "").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
        return fields

    def status(self) -> WaydroidStatus:
        installed = self.is_installed()
        if not installed:
            return WaydroidStatus(False, False, False, None, self._configured_adb_serial, None)

        ok, out = self._graphical_run_cmd(["waydroid", "status"])
        raw = out if ok else None
        fields = self._status_fields(raw)
        running = fields.get("container", "").upper() == "RUNNING"
        session = fields.get("session", "").upper() == "RUNNING"
        ip = self.get_ip()
        adb_serial = self._configured_adb_serial or (f"{ip}:5555" if ip else None)
        return WaydroidStatus(installed, running, session, ip, adb_serial, raw)

    def start(self) -> dict:
        container_ok, container_out = try_cmd(["sudo", "-n", "waydroid", "container", "start"], timeout=60.0)
        if not container_ok:
            container_ok, container_out = try_cmd(["waydroid", "container", "start"], timeout=60.0)
        session_ok, session_out = self._control_runtime("start", timeout=120.0)
        return {
            "container_ok": container_ok,
            "container_out": container_out,
            "session_ok": session_ok,
            "session_out": session_out,
            "status": self.status().to_dict(),
        }

    def stop(self) -> dict:
        session_ok, session_out = self._control_runtime("stop", timeout=60.0)
        container_ok, container_out = try_cmd(["sudo", "-n", "waydroid", "container", "stop"], timeout=30.0)
        if not container_ok:
            container_ok, container_out = try_cmd(["waydroid", "container", "stop"], timeout=30.0)
        return {
            "session_ok": session_ok,
            "session_out": session_out,
            "container_ok": container_ok,
            "container_out": container_out,
            "status": self.status().to_dict(),
        }

    def recover_user_runtime(self) -> dict:
        reset_ok, reset_out = self._control_runtime("reset", timeout=120.0)
        adb = self.ensure_adb_connected()
        status = self.status()
        bridge = self.forward_bridge(49317)
        screen = self.ensure_screen_ready()
        return {
            "ok": bool(reset_ok and adb.get("ok") and bridge.get("ok")),
            "mode": "user_runtime",
            "reset_ok": reset_ok,
            "reset_out": reset_out,
            "adb": adb,
            "bridge": bridge,
            "screen": screen,
            "status": status.to_dict(),
        }

    def recover_system_runtime(self) -> dict:
        script = self._project_root() / "scripts" / "restart_everything_sudo.sh"
        if not script.exists():
            return {"ok": False, "mode": "system", "error": f"missing restart helper: {script}"}
        ok, out = try_cmd(["sudo", "-n", str(script)], timeout=180.0)
        adb = self.ensure_adb_connected()
        bridge = self.forward_bridge(49317)
        screen = self.ensure_screen_ready()
        return {
            "ok": bool(ok and adb.get("ok") and bridge.get("ok")),
            "mode": "system",
            "restart_ok": ok,
            "restart_out": out,
            "adb": adb,
            "bridge": bridge,
            "screen": screen,
            "status": self.status().to_dict(),
        }

    def get_ip(self) -> str | None:
        ok, out = self._graphical_run_cmd(["waydroid", "status"], timeout=20.0)
        if ok:
            match = re.search(r"^IP address:\s+(\d+\.\d+\.\d+\.\d+)", out, re.MULTILINE)
            if match:
                return match.group(1)

        ok, out = self._graphical_run_cmd(["waydroid", "shell", "ip", "-f", "inet", "addr", "show", "eth0"], timeout=20.0)
        if ok:
            match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", out)
            if match:
                return match.group(1)
        ok, out = self._graphical_run_cmd(["waydroid", "shell", "ip", "route", "get", "1.1.1.1"], timeout=20.0)
        if ok:
            match = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", out)
            if match:
                return match.group(1)
        return None

    def adb_serial(self) -> str | None:
        if self._configured_adb_serial:
            return self._configured_adb_serial
        ip = self.get_ip()
        return f"{ip}:5555" if ip else None

    def ensure_adb_connected(self) -> dict:
        serial = self.adb_serial()
        if not serial:
            return {"ok": False, "error": "Unable to determine Waydroid ADB serial."}
        ok, out = try_cmd(["adb", "connect", serial], timeout=20.0)
        return {"ok": ok, "serial": serial, "output": out}

    def adb_shell(self, args: list[str], timeout: float | None = 15.0) -> dict:
        serial = self.adb_serial()
        if not serial:
            return {"ok": False, "error": "Unable to determine Waydroid ADB serial."}
        try:
            proc = run_cmd(["adb", "-s", serial, "shell", *args], check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "serial": serial,
                "error": f"adb shell timed out after {timeout}s",
                "returncode": -1,
            }
        return {
            "ok": proc.returncode == 0,
            "serial": serial,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }

    def adb(self, args: list[str], timeout: float | None = 15.0, *, text: bool = True) -> dict[str, Any]:
        serial = self.adb_serial()
        if not serial:
            return {"ok": False, "error": "Unable to determine Waydroid ADB serial."}
        try:
            proc = run_cmd(["adb", "-s", serial, *args], check=False, timeout=timeout, text=text)
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "serial": serial,
                "error": f"adb timed out after {timeout}s",
                "returncode": -1,
            }
        return {
            "ok": proc.returncode == 0,
            "serial": serial,
            "stdout": proc.stdout if not text else (proc.stdout or "").strip(),
            "stderr": proc.stderr if not text else (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }

    @staticmethod
    def _component_name(package: str, activity: str) -> str:
        if "/" in activity:
            return activity
        if activity.startswith("."):
            return f"{package}/{activity}"
        if activity.startswith(package):
            return f"{package}/{activity}"
        return f"{package}/.{activity.lstrip('.')}"

    @staticmethod
    def _am_start_error(result: dict[str, Any]) -> str | None:
        output = "\n".join(
            part for part in (result.get("stdout"), result.get("stderr")) if isinstance(part, str) and part.strip()
        ).strip()
        if not result.get("ok"):
            return output or result.get("error") or "am start failed"
        lowered = output.lower()
        error_markers = (
            "error:",
            "exception occurred",
            "does not exist",
            "permission denial",
            "securityexception",
            "unable to resolve intent",
            "activity class",
            "not exported from uid",
            "java.lang.",
        )
        if any(marker in lowered for marker in error_markers):
            return output or "am start reported an Android error"
        return None

    @staticmethod
    def _decode_adb_output(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore").strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    @classmethod
    def _extract_png_bytes(cls, payload: bytes) -> tuple[bytes | None, str | None]:
        if not payload:
            return None, None
        idx = payload.find(cls._PNG_HEADER)
        if idx < 0:
            return None, cls._decode_adb_output(payload[:256])
        warning = cls._decode_adb_output(payload[:idx]) if idx > 0 else None
        return payload[idx:], warning or None

    def am_start(self, args: list[str], timeout: float | None = 20.0) -> dict[str, Any]:
        result = self.adb_shell(["am", "start", "-W", *args], timeout=timeout)
        error = self._am_start_error(result)
        return {
            **result,
            "ok": error is None,
            "error": error,
            "command": ["am", "start", "-W", *args],
        }

    def ensure_screen_ready(self) -> dict:
        adb = self.ensure_adb_connected()
        if not adb.get("ok"):
            return adb
        serial = adb["serial"]
        steps: list[dict] = []
        for args in (
            ["input", "keyevent", "KEYCODE_WAKEUP"],
            ["wm", "dismiss-keyguard"],
            ["input", "keyevent", "82"],
        ):
            proc = run_cmd(["adb", "-s", serial, "shell", *args], check=False, timeout=10.0)
            steps.append(
                {
                    "args": args,
                    "ok": proc.returncode == 0,
                    "stdout": (proc.stdout or "").strip(),
                    "stderr": (proc.stderr or "").strip(),
                }
            )
        return {"ok": True, "serial": serial, "steps": steps}

    def forward_bridge(self, port: int) -> dict:
        serial = self.adb_serial()
        if not serial:
            return {"ok": False, "error": "No ADB serial available for bridge forward."}
        self.ensure_adb_connected()
        ok, out = try_cmd(["adb", "-s", serial, "forward", f"tcp:{port}", f"tcp:{port}"], timeout=10.0)
        return {"ok": ok, "serial": serial, "output": out, "port": port}

    def screenshot(self, path: str | Path) -> dict:
        serial = self.adb_serial()
        if not serial:
            return {"ok": False, "error": "Unable to determine Waydroid ADB serial."}
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shell_error = ""
        try:
            proc = run_cmd(
                ["adb", "-s", serial, "shell", "screencap", "-p"],
                check=False,
                timeout=15.0,
                text=False,
            )
            payload = proc.stdout or b""
            png_payload, shell_warning = self._extract_png_bytes(payload)
            if proc.returncode == 0 and png_payload:
                output_path.write_bytes(png_payload)
                result: dict[str, Any] = {"ok": True, "serial": serial, "path": str(output_path), "source": "adb_shell"}
                if shell_warning:
                    result["warning"] = shell_warning
                return result
            shell_error = self._decode_adb_output(proc.stderr) or shell_warning or "shell returned no PNG data"
        except Exception as exc:
            shell_error = str(exc)

        remote_path = f"/sdcard/openclaw-screenshot-{int(time.time() * 1000)}.png"
        try:
            capture = self.adb(
                ["shell", "screencap", "-p", remote_path],
                timeout=3.0,
                text=False,
            )
        except Exception as exc:
            capture = {"ok": False, "error": str(exc)}
        if not capture.get("ok"):
            return {
                "ok": False,
                "serial": serial,
                "error": (
                    self._decode_adb_output(capture.get("stderr"))
                    or self._decode_adb_output(capture.get("stdout"))
                    or capture.get("error")
                    or shell_error
                    or "Screenshot capture failed"
                ),
                "fallback_error": shell_error,
            }

        try:
            pull = self.adb(["pull", remote_path, str(output_path)], timeout=3.0)
        except Exception as exc:
            pull = {"ok": False, "error": str(exc)}
        with suppress(Exception):
            self.adb_shell(["rm", "-f", remote_path], timeout=2.0)
        if not pull.get("ok") or not output_path.exists() or output_path.stat().st_size == 0:
            return {
                "ok": False,
                "serial": serial,
                "error": pull.get("stderr") or pull.get("error") or "ADB pull failed",
                "fallback_error": shell_error,
            }
        return {"ok": True, "serial": serial, "path": str(output_path), "source": "adb_pull", "fallback_error": shell_error or None}

    def press_key(self, key: str) -> dict:
        return self.adb_shell(["input", "keyevent", key], timeout=10.0)

    def tap(self, x: int, y: int) -> dict:
        return self.adb_shell(["input", "tap", str(int(x)), str(int(y))], timeout=10.0)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 250) -> dict:
        return self.adb_shell(
            ["input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms))],
            timeout=10.0,
        )

    def input_text(self, text: str) -> dict:
        escaped = (text or "").replace(" ", "%s")
        return self.adb_shell(["input", "text", escaped], timeout=15.0)

    def list_packages(self, *, third_party_only: bool = False) -> dict[str, Any]:
        args = ["pm", "list", "packages"]
        if third_party_only:
            args.append("-3")
        result = self.adb_shell(args, timeout=20.0)
        if not result.get("ok"):
            return result
        packages: list[str] = []
        for line in (result.get("stdout") or "").splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.append(line.split(":", 1)[1])
        return {"ok": True, "packages": sorted(set(packages)), "serial": result.get("serial")}

    def app_open(self, package: str) -> dict:
        """
        Launch an app and wait for its process to appear.
        Resolves the real launcher activity first, then falls back to common
        activity suffixes and finally monkey.
        """
        launch_attempts: list[dict[str, Any]] = []

        resolved = self.adb_shell(["cmd", "package", "resolve-activity", "--brief", package], timeout=15.0)
        if resolved.get("ok"):
            lines = [line.strip() for line in (resolved.get("stdout") or "").splitlines() if line.strip()]
            for line in reversed(lines):
                if "/" not in line:
                    continue
                pkg_name, activity = line.split("/", 1)
                direct = self.adb_shell(["am", "start", "-W", "-n", f"{pkg_name}/{activity}"], timeout=20.0)
                direct_error = self._am_start_error(direct)
                result = {
                    **direct,
                    "ok": direct_error is None,
                    "error": direct_error,
                    "package": pkg_name,
                    "activity": activity,
                    "command": ["am", "start", "-W", "-n", f"{pkg_name}/{activity}"],
                }
                launch_attempts.append({"type": "resolved_activity", **result})
                if result.get("ok"):
                    package = pkg_name
                    break
            else:
                result = None
        else:
            result = None

        activity_suffixes = [
            ".view.MainActivity",  # Aptoide and many apps
            ".MainActivity",  # standard
            f"{package}.MainActivity",
            ".ui.MainActivity",
        ]

        last_stderr = ""
        launched_ok = bool(result and result.get("ok"))

        if not launched_ok:
            seen_activity_names: set[str] = set()
            for suffix in activity_suffixes:
                activity_name = suffix if suffix.startswith(package) else f"{package}{suffix}"
                if activity_name in seen_activity_names:
                    continue
                seen_activity_names.add(activity_name)
                result = self.adb_shell(
                    ["am", "start", "-n", f"{package}/{activity_name}", "-S"],
                    timeout=15.0,
                )
                launch_attempts.append({"type": "suffix_guess", "activity_suffix": suffix, "activity": activity_name, **result})
                last_stderr = result.get("stderr", "")
                if "does not exist" in last_stderr:
                    continue
                if result.get("ok"):
                    launched_ok = True
                    break
                last_stderr = result.get("stderr", "")

        if not launched_ok:
            monkey = self.adb_shell(
                ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                timeout=20.0,
            )
            launch_attempts.append({"type": "monkey", **monkey})
            if monkey.get("ok"):
                launched_ok = True
            else:
                last_stderr = monkey.get("stderr", "") or monkey.get("stdout", "")

        if not launched_ok:
            return {
                "ok": False,
                "package": package,
                "error": last_stderr or "Failed to start activity",
                "attempts": launch_attempts,
            }

        deadline = time.time() + 15.0
        while time.time() < deadline:
            pg = self.adb_shell(["pgrep", "-x", package], timeout=3.0)
            if pg.get("ok") and pg.get("stdout", "").strip():
                return {"ok": True, "package": package, "attempts": launch_attempts}
            current = self.adb_shell(["dumpsys", "activity", "activities"], timeout=5.0)
            if current.get("ok") and package in ((current.get("stdout") or "") + (current.get("stderr") or "")):
                return {"ok": True, "package": package, "attempts": launch_attempts}
            time.sleep(0.5)

        return {
            "ok": False,
            "package": package,
            "error": "App started but PID not found within timeout.",
            "attempts": launch_attempts,
        }

    def start_activity(self, package: str, activity: str, *, stop: bool = False) -> dict[str, Any]:
        args: list[str] = []
        if stop:
            args.append("-S")
        args.extend(["-n", self._component_name(package, activity)])
        result = self.am_start(args, timeout=20.0)
        return {
            "ok": result.get("ok", False),
            "package": package,
            "activity": activity,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "error": result.get("error"),
            "command": result.get("command"),
        }

    def start_intent(
        self,
        *,
        action: str | None = None,
        data_url: str | None = None,
        package: str | None = None,
        activity: str | None = None,
        mime_type: str | None = None,
        categories: list[str] | None = None,
        extras: dict[str, str] | None = None,
        stop: bool = False,
    ) -> dict[str, Any]:
        args: list[str] = []
        if stop:
            args.append("-S")
        if action:
            args.extend(["-a", action])
        if data_url:
            args.extend(["-d", data_url])
        if mime_type:
            args.extend(["-t", mime_type])
        for category in categories or []:
            if category:
                args.extend(["-c", category])
        for key, value in (extras or {}).items():
            if key:
                args.extend(["--es", key, str(value)])
        if package and activity:
            args.extend(["-n", self._component_name(package, activity)])
        elif package:
            args.extend(["-p", package])
        if not args:
            return {"ok": False, "error": "No intent arguments were provided."}
        result = self.am_start(args, timeout=20.0)
        return {
            "ok": result.get("ok", False),
            "package": package,
            "activity": activity,
            "intent_action": action,
            "data_url": data_url,
            "mime_type": mime_type,
            "categories": categories or [],
            "extras": extras or {},
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "error": result.get("error"),
            "command": result.get("command"),
        }

    def app_wait(self, package: str, timeout: float = 15.0) -> dict:
        # Use pgrep as the primary check — it's fast (~20ms vs 10s for dumpsys).
        # Start immediately with a pgrep check (app may already be running).
        deadline = time.time() + timeout
        while time.time() < deadline:
            # Fast package process check.
            pg = self.adb_shell(["pgrep", "-x", package], timeout=3.0)
            if pg.get("ok") and pg.get("stdout", "").strip():
                return {"ok": True, "package": package}
            time.sleep(0.5)
        return {"ok": False, "package": package, "error": f"Timed out waiting for {package}."}

    def install_apk(self, apk_path: str) -> dict:
        script = self._graphical_exec_script()
        args = [str(script), "waydroid", "app", "install", apk_path] if script.exists() else ["waydroid", "app", "install", apk_path]
        proc = run_cmd(args, check=False, timeout=120.0)
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "apk_path": apk_path,
        }

    def install_apk_adb(self, apk_path: str, *, replace: bool = True) -> dict[str, Any]:
        args = ["install"]
        if replace:
            args.append("-r")
        args.append(apk_path)
        result = self.adb(args, timeout=600.0)
        output = "\n".join(
            part for part in (result.get("stdout"), result.get("stderr")) if isinstance(part, str) and part
        ).strip()
        ok = bool(result.get("ok")) and "Failure" not in output
        if not ok and not result.get("error"):
            result["error"] = output or "ADB install failed"
        return {
            **result,
            "ok": ok,
            "apk_path": apk_path,
            "command": ["adb", "-s", result.get("serial") or "", *args],
        }

    def install_apks_adb(self, apk_paths: list[str], *, replace: bool = True) -> dict[str, Any]:
        args = ["install-multiple"]
        if replace:
            args.append("-r")
        args.extend(apk_paths)
        result = self.adb(args, timeout=900.0)
        output = "\n".join(
            part for part in (result.get("stdout"), result.get("stderr")) if isinstance(part, str) and part
        ).strip()
        ok = bool(result.get("ok")) and "Failure" not in output
        if not ok and not result.get("error"):
            result["error"] = output or "ADB install-multiple failed"
        return {
            **result,
            "ok": ok,
            "apk_paths": apk_paths,
            "command": ["adb", "-s", result.get("serial") or "", *args],
        }

    def remove_app(self, package: str) -> dict:
        script = self._graphical_exec_script()
        args = [str(script), "waydroid", "app", "remove", package] if script.exists() else ["waydroid", "app", "remove", package]
        proc = run_cmd(args, check=False, timeout=60.0)
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "package": package,
        }
