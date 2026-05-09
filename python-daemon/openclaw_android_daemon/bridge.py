from __future__ import annotations

from contextlib import suppress
import os
import subprocess
import time
import urllib.parse
from typing import Any

import requests


class BridgeClient:
    _FORWARD_PORT = 49317
    _BRIDGE_PACKAGE = "ai.openclaw.androidbridge"
    _TOKEN_FILE = "files/bridge_token"
    _TOKEN_HEADER = "X-OpenClaw-Bridge-Token"

    def __init__(self, serial: str, base_url: str | None = None, bridge_token: str | None = None) -> None:
        self.serial = serial
        self.base_url = (base_url or f"http://127.0.0.1:{self._FORWARD_PORT}").rstrip("/")
        self._bridge_token = (bridge_token or os.environ.get("OPENCLAW_ANDROID_BRIDGE_TOKEN") or "").strip() or None
        self._last_forward_error: str | None = None
        self._last_token_error: str | None = None

    def _ensure_forward(self) -> bool:
        """Ensure the adb forward exists. Returns False if the device is unreachable."""
        try:
            # Waydroid is exposed as a TCP adb target, so refresh the transport
            # before forwarding in case the daemon-side adb connection went stale.
            connect = subprocess.run(
                ["adb", "connect", self.serial],
                check=False, capture_output=True, timeout=10, text=True,
            )
            forward = subprocess.run(
                ["adb", "-s", self.serial, "forward",
                 f"tcp:{self._FORWARD_PORT}", f"tcp:{self._FORWARD_PORT}"],
                check=False, capture_output=True, timeout=10, text=True,
            )
            if forward.returncode == 0:
                self._last_forward_error = None
                return True
            details = (forward.stderr or forward.stdout or "").strip()
            if connect.returncode != 0:
                connect_details = (connect.stderr or connect.stdout or "").strip()
                details = f"adb connect failed: {connect_details or f'exit {connect.returncode}'}; adb forward failed: {details or f'exit {forward.returncode}'}"
            else:
                details = f"adb forward failed: {details or f'exit {forward.returncode}'}"
            self._last_forward_error = details
            return False
        except Exception as exc:
            self._last_forward_error = str(exc)
            return False

    def _read_bridge_token(self) -> str | None:
        if self._bridge_token:
            return self._bridge_token
        token = self._read_bridge_token_file()
        if token:
            return token
        with suppress(Exception):
            requests.get(f"{self.base_url}/health", timeout=5)
        return self._read_bridge_token_file()

    def _read_bridge_token_file(self) -> str | None:
        try:
            proc = subprocess.run(
                ["adb", "-s", self.serial, "shell", "run-as", self._BRIDGE_PACKAGE, "cat", self._TOKEN_FILE],
                check=False,
                capture_output=True,
                timeout=10,
                text=True,
            )
        except Exception as exc:
            self._last_token_error = str(exc)
            return None
        token = (proc.stdout or "").strip()
        if proc.returncode == 0 and len(token) >= 32 and not any(ch.isspace() for ch in token):
            self._bridge_token = token
            self._last_token_error = None
            return token
        details = (proc.stderr or proc.stdout or "").strip()
        self._last_token_error = details or f"adb run-as exited {proc.returncode}"
        return None

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last_err: Exception | None = None
        max_attempts = 3
        attempts_made = 0
        for attempt in range(1, max_attempts + 1):
            attempts_made = attempt
            # Ensure forward is live (refresh on retry).
            if not self._ensure_forward():
                last_err = RuntimeError(self._last_forward_error or "ADB device unreachable")
                time.sleep(0.5)
                continue
            token = None if path == "/health" else self._read_bridge_token()
            headers = {self._TOKEN_HEADER: token} if token else None
            try:
                resp = requests.request(
                    method, url, json=payload, params=None, headers=headers,
                    timeout=10,
                )
                try:
                    data = resp.json()
                except Exception:
                    data = {"ok": False, "raw": resp.text[:200]}
                if not resp.ok:
                    raise RuntimeError(f"Bridge {method} {path} failed: {resp.status_code} {data}")
                return data
            except requests.exceptions.ConnectionError as exc:
                last_err = exc
                with suppress(Exception):
                    subprocess.run(
                        ["adb", "-s", self.serial, "forward",
                         "--remove", f"tcp:{self._FORWARD_PORT}"],
                        check=False, capture_output=True, timeout=5,
                    )
                time.sleep(0.5)
            except requests.exceptions.Timeout as exc:
                last_err = exc
                time.sleep(0.5)
            except Exception as exc:
                last_err = exc
                break
        if last_err is None:
            last_err = RuntimeError("unknown bridge request failure")
        raise RuntimeError(f"Bridge request failed after {attempts_made} attempts: {last_err}") from last_err

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def tree(self, mode: str = "interactive") -> dict[str, Any]:
        return self._request("GET", "/tree", params={"mode": mode})

    def configure(self, allowed_packages: list[str]) -> dict[str, Any]:
        return self._request("POST", "/configure", {"allowed_packages": allowed_packages})

    def node_action(self, node_key: str, action: str, text: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/node_action", {"node_key": node_key, "action": action, "text": text})

    def global_action(self, action: str) -> dict[str, Any]:
        return self._request("POST", "/global_action", {"action": action})
