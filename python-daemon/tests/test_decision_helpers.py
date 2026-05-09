from __future__ import annotations

import unittest

from openclaw_android_daemon.decision import deterministic_decision, normalize_decision_mode, should_use_vision_for_decision


def ref(ref_id: str, label: str, confidence: float = 0.98, resource_id: str = "com.example:id/continue") -> dict:
    return {
        "ref": ref_id,
        "semantic_label": label,
        "resource_id": resource_id,
        "package": "com.example",
        "confidence_score": confidence,
        "is_direct_control": True,
        "is_actionable": True,
        "window_rank": 0,
    }


class DecisionHelperTests(unittest.TestCase):
    def test_normalize_decision_mode_aliases(self) -> None:
        self.assertEqual(normalize_decision_mode("vision", "auto"), "llm_vision")
        self.assertEqual(normalize_decision_mode("unknown", "auto"), "auto")

    def test_deterministic_decision_returns_clear_forward_target(self) -> None:
        result, warnings = deterministic_decision(
            {
                "top_refs": [ref("continue", "Continue"), ref("cancel", "Cancel", 0.2, "android:id/button2")],
                "foreground_package": "com.example",
            },
            "continue setup",
        )

        self.assertEqual(result["decision"], "click")
        self.assertEqual(result["ref"], "continue")
        self.assertEqual(warnings, [])

    def test_deterministic_decision_ambiguous_path_warns(self) -> None:
        result, warnings = deterministic_decision(
            {
                "top_refs": [ref("one", "Item", 0.4), ref("two", "Item details", 0.4)],
                "foreground_package": "com.example",
            },
            "choose item",
        )

        self.assertIsNone(result)
        self.assertTrue(any("ambiguous" in warning for warning in warnings))

    def test_auto_mode_uses_vision_when_screenshot_is_recommended(self) -> None:
        self.assertTrue(
            should_use_vision_for_decision(
                {"stats": {"screenshot_recommended": True}, "current_app": {}, "top_refs": [ref("continue", "Continue")]},
                "continue",
            )
        )


if __name__ == "__main__":
    unittest.main()
