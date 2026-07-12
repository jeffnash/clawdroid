from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openclaw_android_daemon.config import Settings
from openclaw_android_daemon.controller import AndroidRuntime, LlmDecisionError
from openclaw_android_daemon.models import NodeHandle, SnapshotState


def make_node(
    ref: str,
    *,
    label: str = "",
    actions: list[str] | None = None,
    clickable: bool = True,
    parent_ref: str | None = None,
    child_refs: list[str] | None = None,
) -> NodeHandle:
    return NodeHandle(
        ref=ref,
        role="button" if clickable else "text",
        text=label,
        content_desc="",
        hint_text="",
        class_name="android.widget.Button" if clickable else "android.widget.TextView",
        resource_id="",
        bounds=(0, 0, 100, 80),
        actions=actions if actions is not None else (["click"] if clickable else []),
        source="test",
        node_key=f"node-{ref}",
        clickable=clickable,
        package="com.example",
        active_window=True,
        parent_ref=parent_ref,
        child_refs=child_refs or [],
    )


class ControllerCompatibilityImportTests(unittest.TestCase):
    def test_android_runtime_importable_from_controller(self) -> None:
        from openclaw_android_daemon.controller import AndroidRuntime

        self.assertIsNotNone(AndroidRuntime)

    def test_llm_decision_error_importable_from_controller(self) -> None:
        from openclaw_android_daemon.controller import LlmDecisionError

        self.assertTrue(issubclass(LlmDecisionError, Exception))


class RuntimeSafetyTests(unittest.TestCase):
    def make_runtime(self, *, require_install_approval: bool = True, require_action_approval: bool = True) -> AndroidRuntime:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        settings = Settings(
            screenshot_dir=str(root / "screenshots"),
            download_dir=str(root / "downloads"),
            llm_config_path=str(root / "llm.json"),
            llm_models_path=str(root / "models.json"),
            llm_settings_path=str(root / "settings.json"),
            require_approval_for_install=require_install_approval,
            require_approval_for_protected_actions=require_action_approval,
        )
        return AndroidRuntime(settings)

    def test_settings_string_paths_are_coerced_to_path_objects(self) -> None:
        runtime = self.make_runtime()

        self.assertIsInstance(runtime.settings.screenshot_dir, Path)
        self.assertIsInstance(runtime.settings.download_dir, Path)
        self.assertIsInstance(runtime.settings.llm_config_path, Path)

    def test_install_apk_is_blocked_without_approval_by_default(self) -> None:
        runtime = self.make_runtime(require_install_approval=True)
        with patch.object(runtime.waydroid, "install_apk", return_value={"ok": True}) as install:
            result = runtime.install_apk("/tmp/example.apk", approved=False)

        self.assertFalse(result["ok"])
        self.assertIn("approved=true", result["error"])
        install.assert_not_called()

    def test_system_recovery_is_blocked_without_explicit_approval(self) -> None:
        runtime = self.make_runtime()
        with patch.object(runtime.waydroid, "recover_system_runtime", return_value={"ok": True}) as recover:
            result = runtime.recover(mode="system", approved=False)

        self.assertFalse(result["ok"])
        self.assertIn("requires approved=true", result["error"])
        recover.assert_not_called()

    def test_user_recovery_invalidates_bridge_and_snapshot_history(self) -> None:
        runtime = self.make_runtime()
        snapshot = SnapshotState(
            snapshot_id="snap-1",
            package="com.example",
            activity=".Main",
            mode="interactive",
        )
        runtime._bridge = Mock()
        runtime._remember_snapshot(snapshot)

        with patch.object(runtime.waydroid, "recover_user_runtime", return_value={"ok": True, "mode": "user_runtime"}):
            result = runtime.recover(mode="user", approved=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["last_snapshot_cleared"])
        self.assertIsNone(runtime._bridge)
        self.assertIsNone(runtime._last_snapshot)
        self.assertEqual(list(runtime._snapshot_history), [])

    def test_snapshot_history_keeps_only_recent_entries(self) -> None:
        runtime = self.make_runtime()
        for index in range(10):
            runtime._remember_snapshot(
                SnapshotState(
                    snapshot_id=f"snap-{index}",
                    package="com.example",
                    activity=".Main",
                    mode="interactive",
                )
            )

        self.assertEqual(len(runtime._snapshot_history), 8)
        self.assertEqual(list(runtime._snapshot_history), [f"snap-{index}" for index in range(2, 10)])
        self.assertEqual(runtime._last_snapshot.snapshot_id, "snap-9")

    def test_snapshot_state_ref_for_label_is_preserved(self) -> None:
        node = make_node("continue", label="Continue")
        snapshot = SnapshotState(
            snapshot_id="snap-1",
            package="com.example",
            activity=".Main",
            mode="interactive",
            refs={"continue": node},
            foreground_package="com.example",
        )

        self.assertEqual(snapshot.ref_for_label("Continue"), "continue")

    def test_protected_action_block_detects_purchase_like_labels(self) -> None:
        runtime = self.make_runtime()
        handle = make_node("r1", label="Place your order")

        self.assertEqual(runtime._protected_action_block(handle), "place your order")

    def test_coordinate_tap_on_protected_control_is_blocked_without_approval(self) -> None:
        runtime = self.make_runtime(require_action_approval=True)
        # The real bridge returns plain dicts, not NodeHandle objects; the
        # guard must work against exactly that shape.
        protected = {
            "text": "Place your order",
            "content_desc": "",
            "hint_text": "",
            "bounds": [0, 0, 100, 80],
        }
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime, "_bridge_tree_nodes", return_value=([protected], {"ok": True})),
            patch.object(runtime.waydroid, "tap", return_value={"ok": True}) as tap,
        ):
            result = runtime.coordinate_act("tap", x=50, y=40)

        self.assertFalse(result["ok"])
        self.assertIn("Protected coordinate action blocked", result["error"])
        self.assertEqual(result["protected"]["token"], "place your order")
        self.assertEqual(result["protected"]["label"], "Place your order")
        tap.assert_not_called()

    def test_coordinate_tap_outside_protected_control_is_allowed(self) -> None:
        runtime = self.make_runtime(require_action_approval=True)
        protected = {
            "text": "Place your order",
            "content_desc": "",
            "hint_text": "",
            "bounds": [0, 0, 100, 80],
        }
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime, "_bridge_tree_nodes", return_value=([protected], {"ok": True})),
            patch.object(runtime.waydroid, "tap", return_value={"ok": True}) as tap,
        ):
            result = runtime.coordinate_act("tap", x=500, y=400)

        self.assertTrue(result["ok"])
        tap.assert_called_once_with(500, 400)

    def test_coordinate_tap_on_protected_control_can_use_explicit_approval(self) -> None:
        runtime = self.make_runtime(require_action_approval=True)
        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime.waydroid, "tap", return_value={"ok": True}) as tap,
        ):
            result = runtime.coordinate_act("tap", x=50, y=40, approved=True)

        self.assertTrue(result["ok"])
        tap.assert_called_once_with(50, 40)

    def test_navigation_clears_snapshot_history_so_old_refs_are_rejected(self) -> None:
        runtime = self.make_runtime()
        node = make_node("continue", label="Continue")
        snapshot = SnapshotState(
            snapshot_id="snap-before",
            package="com.example.old",
            activity=".Old",
            mode="interactive",
            refs={"continue": node},
            foreground_package="com.example.old",
        )
        runtime._remember_snapshot(snapshot)

        with (
            patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}),
            patch.object(runtime.waydroid, "app_open", return_value={"ok": True}),
            patch.object(runtime, "_current_app_via_adb", return_value={"package": "com.example.new", "activity": ".Main"}),
        ):
            opened = runtime.app_open("com.example.new")

        self.assertTrue(opened["ok"])
        self.assertEqual(list(runtime._snapshot_history), [])
        with patch.object(runtime, "ensure_bridge_ready", return_value={"ok": True}):
            result = runtime.act("snap-before", "continue", "click")

        self.assertFalse(result["ok"])
        self.assertIn("Snapshot is missing or stale", result["error"])

    def test_decide_next_returns_shaped_error_when_llm_config_is_missing(self) -> None:
        runtime = self.make_runtime()
        snapshot = {
            "ok": True,
            "snapshot_id": "snap-1",
            "current_app": {"package": "com.example", "activity": ".Main"},
            "screen_context": {"summary": "empty"},
            "top_refs": [],
            "source": "bridge",
            "screenshot_path": None,
        }
        with (
            patch.object(runtime, "snapshot", return_value=snapshot),
            patch.object(runtime.llm_decider, "decide", side_effect=LlmDecisionError("No LLM provider configuration is available.")),
        ):
            result = runtime.decide_next(goal="continue", decision_mode="llm_text")

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "decide_next")
        self.assertEqual(result["decision_mode_requested"], "llm_text")
        self.assertEqual(result["decision_source"], "llm")
        self.assertEqual(result["snapshot_id"], "snap-1")
        self.assertIn("No LLM provider", result["error"])

    def test_resolve_actionable_target_preserves_direct_clickable_ref(self) -> None:
        runtime = self.make_runtime()
        node = make_node("r1", label="Continue")
        snapshot = SnapshotState(
            snapshot_id="snap-1",
            package="com.example",
            activity=".Main",
            mode="interactive",
            refs={"r1": node},
            foreground_package="com.example",
        )

        handle, resolution = runtime._resolve_actionable_target(snapshot, node, "click")

        self.assertEqual(handle.ref, "r1")
        self.assertIsNone(resolution)

    def test_resolve_actionable_target_promotes_non_actionable_child_to_parent(self) -> None:
        runtime = self.make_runtime()
        parent = make_node("row", label="Continue", child_refs=["label"])
        child = make_node("label", label="Continue", clickable=False, parent_ref="row")
        snapshot = SnapshotState(
            snapshot_id="snap-1",
            package="com.example",
            activity=".Main",
            mode="interactive",
            refs={"row": parent, "label": child},
            foreground_package="com.example",
        )

        handle, resolution = runtime._resolve_actionable_target(snapshot, child, "click")

        self.assertEqual(handle.ref, "row")
        self.assertEqual(resolution["requested_ref"], "label")
        self.assertEqual(resolution["resolved_ref"], "row")


if __name__ == "__main__":
    unittest.main()
