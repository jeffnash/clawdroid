from __future__ import annotations

import unittest

from openclaw_android_daemon.action_verify import match_post_action_ref, verification_is_weak, verify_action_result
from openclaw_android_daemon.models import NodeHandle, SnapshotState


def make_node(ref: str, label: str = "Continue") -> NodeHandle:
    return NodeHandle(
        ref=ref,
        role="button",
        text=label,
        content_desc="",
        hint_text="",
        class_name="android.widget.Button",
        resource_id="com.example:id/continue",
        bounds=(0, 0, 100, 100),
        actions=["click"],
        source="test",
        node_key="node-1",
        clickable=True,
        package="com.example",
        active_window=True,
    )


class ActionVerifyTests(unittest.TestCase):
    def test_match_post_action_ref_uses_stable_node_identity(self) -> None:
        handle = make_node("before")
        refs = [
            {"ref": "other", "semantic_label": "Other", "node_key": "node-2", "bounds": (5, 5, 10, 10)},
            {
                "ref": "after",
                "semantic_label": "Continue",
                "node_key": "node-1",
                "resource_id": "com.example:id/continue",
                "role": "button",
                "package": "com.example",
                "bounds": (0, 0, 100, 100),
            },
        ]

        self.assertEqual(match_post_action_ref(handle, refs)["ref"], "after")

    def test_verify_action_result_reports_navigation_change(self) -> None:
        handle = make_node("continue")
        before = SnapshotState(
            snapshot_id="before",
            package="com.example",
            activity=".Main",
            mode="interactive",
            refs={"continue": handle},
            foreground_package="com.example",
        )

        verification = verify_action_result(
            op="click",
            text=None,
            handle=handle,
            before_snapshot=before,
            before_flattened=before.flattened(),
            before_screen={"kind": "screen", "label": "Home", "path": None},
            before_current={"package": "com.example", "activity": ".Main"},
            before_event_seq=1,
            post_state={"settled": True, "current_app": {"package": "com.example", "activity": ".Next"}, "event_seq": 2},
            post_snapshot={
                "ok": True,
                "current_app": {"package": "com.example", "activity": ".Next"},
                "foreground_package": "com.example",
                "screen_context": {"kind": "screen", "label": "Next", "path": None},
                "stats": {"event_seq": 2},
                "refs": [],
            },
        )

        self.assertTrue(verification["verified"])
        self.assertIn("activity_changed", verification["reasons"])
        self.assertIn("screen_changed", verification["reasons"])

    def test_verification_is_weak_only_for_event_sequence_changes(self) -> None:
        self.assertTrue(verification_is_weak({"verified": True, "reasons": ["event_seq_changed"]}))
        self.assertFalse(verification_is_weak({"verified": True, "reasons": ["screen_changed"]}))


if __name__ == "__main__":
    unittest.main()
