from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from openclaw_android_daemon.waydroid import WaydroidManager


class FakeWaydroidManager(WaydroidManager):
    def __init__(self, responses: dict[tuple[str, ...], dict], *, serial: str = "127.0.0.1:5555") -> None:
        super().__init__(adb_serial=serial)
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def adb_shell(self, args: list[str], timeout: float | None = 15.0) -> dict:
        key = tuple(args)
        self.calls.append(key)
        response = self.responses.get(key)
        if response is None:
            return {"ok": False, "stdout": "", "stderr": f"unexpected call: {' '.join(args)}", "serial": self.adb_serial()}
        return {"serial": self.adb_serial(), **response}


class WaydroidManagerAppOpenTests(unittest.TestCase):
    def test_app_open_uses_resolved_launcher_activity_first(self) -> None:
        manager = FakeWaydroidManager(
            {
                ("cmd", "package", "resolve-activity", "--brief", "com.example.app"): {
                    "ok": True,
                    "stdout": "com.example.app/.MainActivity",
                    "stderr": "",
                },
                ("am", "start", "-W", "-n", "com.example.app/.MainActivity"): {
                    "ok": True,
                    "stdout": "Status: ok",
                    "stderr": "",
                },
                ("pgrep", "-x", "com.example.app"): {
                    "ok": True,
                    "stdout": "4242",
                    "stderr": "",
                },
            }
        )

        result = manager.app_open("com.example.app")

        self.assertTrue(result["ok"])
        self.assertEqual(result["package"], "com.example.app")
        self.assertEqual(result["attempts"][0]["type"], "resolved_activity")
        self.assertEqual(
            result["attempts"][0]["command"],
            ["am", "start", "-W", "-n", "com.example.app/.MainActivity"],
        )
        self.assertNotIn(("monkey", "-p", "com.example.app", "-c", "android.intent.category.LAUNCHER", "1"), manager.calls)

    def test_app_open_falls_back_to_monkey_after_failed_guesses(self) -> None:
        manager = FakeWaydroidManager(
            {
                ("cmd", "package", "resolve-activity", "--brief", "com.example.app"): {
                    "ok": False,
                    "stdout": "",
                    "stderr": "not found",
                },
                ("am", "start", "-n", "com.example.app/com.example.app.view.MainActivity", "-S"): {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Error: Activity class does not exist.",
                },
                ("am", "start", "-n", "com.example.app/com.example.app.MainActivity", "-S"): {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Error: Activity class does not exist.",
                },
                ("am", "start", "-n", "com.example.app/com.example.app.ui.MainActivity", "-S"): {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Error: Activity class does not exist.",
                },
                ("monkey", "-p", "com.example.app", "-c", "android.intent.category.LAUNCHER", "1"): {
                    "ok": True,
                    "stdout": "Events injected: 1",
                    "stderr": "",
                },
                ("pgrep", "-x", "com.example.app"): {
                    "ok": True,
                    "stdout": "31337",
                    "stderr": "",
                },
            }
        )

        result = manager.app_open("com.example.app")

        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"][-1]["type"], "monkey")


class WaydroidManagerRecoveryTests(unittest.TestCase):
    def test_recover_user_runtime_resets_supervisor_and_bridge(self) -> None:
        manager = WaydroidManager(adb_serial="127.0.0.1:5555")

        with (
            patch.object(manager, "_control_runtime", return_value=(True, "reset ok")) as control,
            patch.object(manager, "ensure_adb_connected", return_value={"ok": True, "serial": "127.0.0.1:5555"}),
            patch.object(manager, "status", return_value=Mock(to_dict=lambda: {"running": True})),
            patch.object(manager, "forward_bridge", return_value={"ok": True, "port": 49317}) as forward,
            patch.object(manager, "ensure_screen_ready", return_value={"ok": True}),
        ):
            result = manager.recover_user_runtime()

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "user_runtime")
        control.assert_called_once_with("reset", timeout=120.0)
        forward.assert_called_once_with(49317)


class WaydroidStatusParsingTests(unittest.TestCase):
    def test_container_running_session_stopped_is_not_session_ready(self) -> None:
        manager = WaydroidManager(adb_serial="127.0.0.1:5555")
        status_text = "Container:\tRUNNING\nSession:\tSTOPPED\n"
        with (
            patch.object(manager, "is_installed", return_value=True),
            patch.object(manager, "_graphical_run_cmd", return_value=(True, status_text)),
            patch.object(manager, "get_ip", return_value="192.168.240.112"),
        ):
            status = manager.status()

        self.assertTrue(status.running)
        self.assertFalse(status.session)

    def test_container_stopped_session_running_is_not_container_ready(self) -> None:
        manager = WaydroidManager(adb_serial="127.0.0.1:5555")
        status_text = "Container:\tSTOPPED\nSession:\tRUNNING\n"
        with (
            patch.object(manager, "is_installed", return_value=True),
            patch.object(manager, "_graphical_run_cmd", return_value=(True, status_text)),
            patch.object(manager, "get_ip", return_value=None),
        ):
            status = manager.status()

        self.assertFalse(status.running)
        self.assertTrue(status.session)


if __name__ == "__main__":
    unittest.main()
