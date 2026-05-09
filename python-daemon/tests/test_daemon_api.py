from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from openclaw_android_daemon.config import Settings
from openclaw_android_daemon.server import create_app


class FakeWaydroid:
    def start(self) -> dict:
        return {"ok": True, "action": "waydroid_start"}

    def stop(self) -> dict:
        return {"ok": True, "action": "waydroid_stop"}


class FakeRuntime:
    last_instance: FakeRuntime | None = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.waydroid = FakeWaydroid()
        self.calls: list[tuple[str, tuple, dict]] = []
        FakeRuntime.last_instance = self

    def _record(self, name: str, *args, **kwargs) -> dict:
        self.calls.append((name, args, kwargs))
        return {"ok": True, "action": name, "args": list(args), "kwargs": kwargs}

    def status(self) -> dict:
        return {"ok": True, "action": "status"}

    def current_app(self) -> dict:
        return {"ok": True, "current_app": {"package": "com.example", "activity": ".Main"}}

    def apps_list(self) -> dict:
        return {"ok": True, "apps": []}

    def apps_search(self, query: str) -> dict:
        return self._record("apps_search", query)

    def service_resolve(self, query: str) -> dict:
        return self._record("service_resolve", query)

    def task_route(self, goal: str) -> dict:
        return self._record("task_route", goal)

    def app_installed(self, package: str) -> dict:
        return self._record("app_installed", package)

    def store_search(self, query: str, store: str = "aptoide", limit: int = 10) -> dict:
        return self._record("store_search", query, store=store, limit=limit)

    def app_open(self, package: str) -> dict:
        return self._record("app_open", package)

    def activity_start(self, package: str, activity: str, stop: bool = False) -> dict:
        return self._record("activity_start", package, activity, stop=stop)

    def intent_start(self, **kwargs) -> dict:
        return self._record("intent_start", **kwargs)

    def url_open(self, url: str, package: str | None = None) -> dict:
        return self._record("url_open", url, package=package)

    def settings_open(self, settings_action: str | None = None) -> dict:
        return self._record("settings_open", settings_action)

    def app_details_open(self, package: str) -> dict:
        return self._record("app_details_open", package)

    def market_open(self, package: str | None = None, query: str | None = None) -> dict:
        return self._record("market_open", package=package, query=query)

    def snapshot(self, mode: str = "interactive", include_screenshot: bool = False) -> dict:
        return self._record("snapshot", mode=mode, include_screenshot=include_screenshot)

    def screenshot(self) -> dict:
        return {"ok": True, "path": "/tmp/screenshot.png"}

    def decide_next(self, **kwargs) -> dict:
        return self._record("decide_next", **kwargs)

    def act(self, snapshot_id: str, ref: str, op: str, text: str | None = None) -> dict:
        return self._record("act", snapshot_id, ref, op, text)

    def coordinate_act(self, **kwargs) -> dict:
        return self._record("coordinate_act", **kwargs)

    def wait(self, wait_for: str, wait_value: str | None = None, timeout_ms: int = 10000) -> dict:
        return self._record("wait", wait_for, wait_value, timeout_ms)

    def doctor(self) -> dict:
        return {"ok": True, "action": "doctor"}

    def recover(self, mode: str = "user", approved: bool = False) -> dict:
        return self._record("recover", mode=mode, approved=approved)

    def install_apk(self, apk_path: str, approved: bool = False) -> dict:
        return self._record("app_install", apk_path, approved=approved)

    def install_apk_url(self, apk_url: str, approved: bool = False) -> dict:
        return self._record("app_install_url", apk_url, approved=approved)

    def store_install(self, **kwargs) -> dict:
        return self._record("store_install", **kwargs)

    def remove_app(self, package: str, approved: bool = False) -> dict:
        return self._record("app_remove", package, approved=approved)

    def install_default_stores(self, stores: list[str] | None = None, approved: bool = False) -> dict:
        return self._record("default_stores_install", stores, approved=approved)

    def apply_device_profile(self, approved: bool = False) -> dict:
        return self._record("device_profile_apply", approved=approved)

    def configure_bridge(self, allowed_packages: list[str]) -> dict:
        return self._record("bridge_configure", allowed_packages)

    def manage_extras(self, extras: list[str], uninstall: bool = False, approved: bool = False) -> dict:
        return self._record("manage_extras", extras, uninstall=uninstall, approved=approved)


class DaemonApiTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("openclaw_android_daemon.server.AndroidRuntime", FakeRuntime)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.client = TestClient(create_app(Settings()))

    def post_agent(self, payload: dict) -> dict:
        response = self.client.post("/v1/agent/dispatch", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post_admin(self, payload: dict) -> dict:
        response = self.client.post("/v1/admin/dispatch", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health_and_status_routes(self) -> None:
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})
        self.assertEqual(self.client.get("/v1/status").json(), {"ok": True, "action": "status"})

    def test_agent_dispatch_routes_public_actions(self) -> None:
        cases = [
            ({"action": "status"}, "status"),
            ({"action": "current_app"}, None),
            ({"action": "apps_list"}, None),
            ({"action": "apps_search", "query": "example"}, "apps_search"),
            ({"action": "service_resolve", "query": "amazon"}, "service_resolve"),
            ({"action": "task_route", "goal": "open amazon"}, "task_route"),
            ({"action": "app_installed", "package": "com.example"}, "app_installed"),
            ({"action": "store_search", "query": "example", "limit": 3}, "store_search"),
            ({"action": "app_open", "package": "com.example"}, "app_open"),
            ({"action": "activity_start", "package": "com.example", "activity": ".Main"}, "activity_start"),
            ({"action": "intent_start", "intent_action": "android.intent.action.VIEW"}, "intent_start"),
            ({"action": "url_open", "url": "https://example.com"}, "url_open"),
            ({"action": "settings_open"}, "settings_open"),
            ({"action": "app_details_open", "package": "com.example"}, "app_details_open"),
            ({"action": "market_open", "package": "com.example"}, "market_open"),
            ({"action": "snapshot", "snapshot_mode": "hybrid", "include_screenshot": True}, "snapshot"),
            ({"action": "screenshot"}, None),
            ({"action": "decide_next", "goal": "continue"}, "decide_next"),
            ({"action": "act", "snapshot_id": "snap", "ref": "r1", "op": "click"}, "act"),
            ({"action": "coordinate_act", "op": "tap", "x": 1, "y": 2}, "coordinate_act"),
            ({"action": "wait", "wait_for": "package", "wait_value": "com.example"}, "wait"),
        ]

        for payload, expected_action in cases:
            with self.subTest(action=payload["action"]):
                result = self.post_agent(payload)
                self.assertTrue(result["ok"])
                if expected_action:
                    self.assertEqual(result["action"], expected_action)

    def test_admin_dispatch_routes_public_actions(self) -> None:
        cases = [
            ({"action": "doctor"}, "doctor"),
            ({"action": "waydroid_start"}, "waydroid_start"),
            ({"action": "waydroid_stop"}, "waydroid_stop"),
            ({"action": "recover", "mode": "user", "approved": True}, "recover"),
            ({"action": "app_install", "apk_path": "/tmp/app.apk", "approved": True}, "app_install"),
            ({"action": "app_install_url", "apk_url": "https://example.com/app.apk", "approved": True}, "app_install_url"),
            ({"action": "store_install", "package": "com.example", "approved": True}, "store_install"),
            ({"action": "app_remove", "package": "com.example", "approved": True}, "app_remove"),
            ({"action": "default_stores_install", "stores": ["aptoide"], "approved": True}, "default_stores_install"),
            ({"action": "device_profile_apply", "approved": True}, "device_profile_apply"),
            ({"action": "bridge_configure", "allowed_packages": ["com.example"]}, "bridge_configure"),
            ({"action": "extras_install", "extras": ["gapps"], "approved": True}, "manage_extras"),
            ({"action": "extras_uninstall", "extras": ["gapps"], "approved": True}, "manage_extras"),
        ]

        for payload, expected_action in cases:
            with self.subTest(action=payload["action"]):
                result = self.post_admin(payload)
                self.assertTrue(result["ok"])
                self.assertEqual(result["action"], expected_action)

    def test_dispatch_validation_errors_preserve_http_error_shape(self) -> None:
        missing_action = self.client.post("/v1/agent/dispatch", json={})
        unknown = self.client.post("/v1/agent/dispatch", json={"action": "unknown"})
        missing_required = self.client.post("/v1/agent/dispatch", json={"action": "app_open"})

        self.assertEqual(missing_action.status_code, 422)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()["detail"], "Unknown action: unknown")
        self.assertEqual(missing_required.status_code, 400)
        self.assertEqual(missing_required.json()["detail"], "package is required for app_open")


if __name__ == "__main__":
    unittest.main()
