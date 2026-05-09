from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from openclaw_android_daemon.bridge import BridgeClient


class FakeResponse:
    def __init__(self, *, ok: bool = True, status_code: int = 200, payload=None, text: str = "", json_raises: bool = False) -> None:
        self.ok = ok
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.text = text
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload


class BridgeClientTests(unittest.TestCase):
    def test_successful_adb_forward_path(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with patch(
            "openclaw_android_daemon.bridge.subprocess.run",
            side_effect=[
                Mock(returncode=0, stdout="connected", stderr=""),
                Mock(returncode=0, stdout="", stderr=""),
            ],
        ) as run:
            self.assertTrue(client._ensure_forward())

        self.assertEqual(run.call_count, 2)
        self.assertIsNone(client._last_forward_error)

    def test_adb_connect_failure_is_reported_with_forward_failure(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with patch(
            "openclaw_android_daemon.bridge.subprocess.run",
            side_effect=[
                Mock(returncode=1, stdout="", stderr="connect failed"),
                Mock(returncode=1, stdout="", stderr="forward failed"),
            ],
        ):
            self.assertFalse(client._ensure_forward())

        self.assertIn("adb connect failed", client._last_forward_error)
        self.assertIn("adb forward failed", client._last_forward_error)

    def test_adb_forward_failure_is_reported(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with patch(
            "openclaw_android_daemon.bridge.subprocess.run",
            side_effect=[
                Mock(returncode=0, stdout="connected", stderr=""),
                Mock(returncode=1, stdout="", stderr="cannot forward"),
            ],
        ):
            self.assertFalse(client._ensure_forward())

        self.assertIn("adb forward failed", client._last_forward_error)

    def test_timeout_retry_can_succeed(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with (
            patch.object(client, "_ensure_forward", return_value=True),
            patch("openclaw_android_daemon.bridge.time.sleep", return_value=None),
            patch(
                "openclaw_android_daemon.bridge.requests.request",
                side_effect=[requests.exceptions.Timeout("slow"), FakeResponse(payload={"ok": True, "event_seq": 1})],
            ) as request,
        ):
            result = client.health()

        self.assertTrue(result["ok"])
        self.assertEqual(result["event_seq"], 1)
        self.assertEqual(request.call_count, 2)

    def test_connection_retry_removes_forward_and_can_succeed(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with (
            patch.object(client, "_ensure_forward", return_value=True),
            patch("openclaw_android_daemon.bridge.time.sleep", return_value=None),
            patch("openclaw_android_daemon.bridge.subprocess.run", return_value=Mock(returncode=0)) as run,
            patch(
                "openclaw_android_daemon.bridge.requests.request",
                side_effect=[requests.exceptions.ConnectionError("refused"), FakeResponse(payload={"ok": True})],
            ),
        ):
            result = client.health()

        self.assertTrue(result["ok"])
        self.assertTrue(any("--remove" in call.args[0] for call in run.call_args_list))

    def test_final_error_includes_real_attempt_count(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with (
            patch.object(client, "_ensure_forward", return_value=False),
            patch("openclaw_android_daemon.bridge.time.sleep", return_value=None),
            self.assertRaisesRegex(RuntimeError, "after 3 attempts") as ctx,
        ):
            client.health()

        self.assertNotIn("after 0 attempts", str(ctx.exception))

    def test_non_json_bridge_response_handling(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with (
            patch.object(client, "_ensure_forward", return_value=True),
            patch(
                "openclaw_android_daemon.bridge.requests.request",
                return_value=FakeResponse(ok=True, text="not-json", json_raises=True),
            ),
        ):
            result = client.health()

        self.assertFalse(result["ok"])
        self.assertEqual(result["raw"], "not-json")

    def test_http_error_response_handling(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with (
            patch.object(client, "_ensure_forward", return_value=True),
            patch(
                "openclaw_android_daemon.bridge.requests.request",
                return_value=FakeResponse(ok=False, status_code=422, payload={"detail": "bad request"}),
            ),
            self.assertRaisesRegex(RuntimeError, "after 1 attempts: Bridge GET /health failed: 422"),
        ):
            client.health()

    def test_protected_routes_send_bridge_token_header(self) -> None:
        client = BridgeClient("127.0.0.1:5555", bridge_token="x" * 32)
        with (
            patch.object(client, "_ensure_forward", return_value=True),
            patch(
                "openclaw_android_daemon.bridge.requests.request",
                return_value=FakeResponse(payload={"ok": True, "nodes": []}),
            ) as request,
        ):
            result = client.tree()

        self.assertTrue(result["ok"])
        self.assertEqual(request.call_args.kwargs["headers"], {"X-OpenClaw-Bridge-Token": "x" * 32})

    def test_bridge_token_can_be_read_from_companion_private_storage(self) -> None:
        client = BridgeClient("127.0.0.1:5555")
        with patch(
            "openclaw_android_daemon.bridge.subprocess.run",
            return_value=Mock(returncode=0, stdout=f"{'y' * 32}\n", stderr=""),
        ) as run:
            self.assertEqual(client._read_bridge_token(), "y" * 32)

        self.assertIn("run-as", run.call_args.args[0])
        self.assertIn("ai.openclaw.androidbridge", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
