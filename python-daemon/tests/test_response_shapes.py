from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openclaw_android_daemon.aptoide import AptoideArtifact
from openclaw_android_daemon.config import Settings
from openclaw_android_daemon.controller import AndroidRuntime


def make_runtime(test_case: unittest.TestCase) -> AndroidRuntime:
    tempdir = tempfile.TemporaryDirectory()
    test_case.addCleanup(tempdir.cleanup)
    root = Path(tempdir.name)
    return AndroidRuntime(
        Settings(
            screenshot_dir=root / "screenshots",
            download_dir=root / "downloads",
            llm_config_path=root / "llm.json",
            llm_models_path=root / "models.json",
            llm_settings_path=root / "settings.json",
            require_approval_for_install=False,
        )
    )


def artifact(package: str = "com.example.app", name: str = "Example") -> AptoideArtifact:
    return AptoideArtifact(
        package=package,
        name=name,
        store_name="Aptoide",
        version_code=1,
        version_name="1.0",
        download_url="https://example.invalid/app.apk",
        md5sum=None,
        filesize=1,
        malware_rank="TRUSTED",
        source="test",
    )


class ResponseShapeTests(unittest.TestCase):
    def assert_has_keys(self, result: dict, keys: set[str]) -> None:
        self.assertTrue(keys.issubset(result.keys()), f"missing keys: {keys - set(result.keys())}")

    def test_status_shape(self) -> None:
        runtime = make_runtime(self)
        runtime._bridge = Mock(health=Mock(return_value={"ok": True, "event_seq": 1}))
        with (
            patch.object(runtime.waydroid, "status", return_value=Mock(to_dict=lambda: {"running": True})),
            patch.object(runtime.waydroid, "forward_bridge", return_value={"ok": True}),
            patch.object(runtime, "_current_app_via_adb", return_value={"package": "com.example", "activity": ".Main"}),
        ):
            result = runtime.status()

        self.assert_has_keys(result, {"ok", "waydroid", "bridge", "current_app", "runtime_backend", "last_snapshot_id"})

    def test_apps_list_and_search_shapes(self) -> None:
        runtime = make_runtime(self)
        with patch.object(runtime.waydroid, "list_packages", return_value={"ok": True, "packages": ["com.example"]}):
            listed = runtime.apps_list()
        with patch.object(runtime, "apps_list", return_value=listed):
            searched = runtime.apps_search("example")

        self.assert_has_keys(listed, {"ok", "apps"})
        self.assert_has_keys(searched, {"ok", "query", "matches"})

    def test_route_shapes(self) -> None:
        runtime = make_runtime(self)
        with patch.object(runtime, "apps_list", return_value={"ok": True, "apps": []}):
            service = runtime.service_resolve("amazon")
            task = runtime.task_route("amazon")

        self.assert_has_keys(service, {"ok", "action", "query", "matches", "preferred_backend", "policy"})
        self.assert_has_keys(task, {"ok", "action", "goal", "matches", "selected_match", "preferred_backend", "policy"})

    def test_store_search_and_install_shapes(self) -> None:
        runtime = make_runtime(self)
        item = artifact()
        with patch.object(runtime.aptoide, "search", return_value=[item]):
            searched = runtime.store_search("Example")
        with (
            patch.object(runtime.aptoide, "get_meta", return_value=item),
            patch.object(runtime.waydroid, "ensure_adb_connected", return_value={"ok": True, "serial": "127.0.0.1:5555"}),
            patch.object(runtime, "_download_store_artifact", return_value={"ok": True, "path": "/tmp/app.apk", "cached": True}),
            patch.object(runtime.waydroid, "install_apk_adb", return_value={"ok": True, "command": ["adb", "install"]}),
            patch.object(runtime, "app_installed", return_value={"ok": True, "package": item.package, "installed": True}),
        ):
            installed = runtime.store_install(package=item.package, approved=True)

        self.assert_has_keys(searched, {"ok", "action", "store", "query", "results"})
        self.assert_has_keys(installed, {"ok", "action", "store", "package", "artifact", "download", "install", "verification"})

    def test_navigation_response_shapes(self) -> None:
        runtime = make_runtime(self)
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime.waydroid, "app_open", return_value={"ok": True}),
            patch.object(runtime, "_current_app_via_adb", return_value={"package": "com.example", "activity": ".Main"}),
        ):
            opened = runtime.app_open("com.example")
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime.waydroid, "start_activity", return_value={"ok": True, "command": ["am"]}),
            patch.object(runtime, "_wait_for_navigation_target", return_value={"ok": True, "matched": True, "current_app": {"package": "com.example", "activity": ".Main"}}),
        ):
            activity = runtime.activity_start("com.example", ".Main")
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime.waydroid, "start_intent", return_value={"ok": True, "command": ["am"]}),
            patch.object(runtime, "_wait_for_navigation_target", return_value={"ok": True, "matched": True, "current_app": {"package": "com.example", "activity": ".Main"}}),
        ):
            intent = runtime.intent_start(intent_action="android.intent.action.VIEW")

        self.assert_has_keys(opened, {"ok", "current_app", "package", "snapshot_stale"})
        self.assert_has_keys(activity, {"ok", "action", "current_app", "snapshot_stale", "next_step"})
        self.assert_has_keys(intent, {"ok", "action", "current_app", "snapshot_stale", "next_step"})

    def test_snapshot_decide_act_coordinate_and_wait_shapes(self) -> None:
        runtime = make_runtime(self)
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime, "_current_app_via_adb", return_value={"package": "com.example", "activity": ".Main"}),
            patch.object(runtime, "_bridge_tree_nodes", return_value=([], {"ok": True, "foreground_package": "com.example"})),
            patch.object(runtime, "_needs_screenshot_fallback", return_value=False),
        ):
            snapshot = runtime.snapshot()
        with patch.object(runtime, "snapshot", return_value={**snapshot, "top_refs": []}):
            decision = runtime.decide_next(decision_mode="deterministic")
        act = runtime.act("missing", "r1", "click")
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime.waydroid, "tap", return_value={"ok": True}),
        ):
            coordinate = runtime.coordinate_act("tap", x=1, y=2, approved=True)
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime, "_current_app_via_adb", return_value={"package": "com.example", "activity": ".Main"}),
        ):
            waited = runtime.wait("package", "com.example", timeout_ms=1)

        self.assert_has_keys(snapshot, {"ok", "snapshot_id", "current_app", "refs", "summary", "top_refs", "screen_context", "stats"})
        self.assert_has_keys(decision, {"ok", "action", "error", "decision_mode_requested", "decision_source", "snapshot_id"})
        self.assert_has_keys(act, {"ok", "error"})
        self.assert_has_keys(coordinate, {"ok", "op", "used"})
        self.assert_has_keys(waited, {"ok", "matched", "wait_for", "wait_value", "timeout_ms"})


if __name__ == "__main__":
    unittest.main()
