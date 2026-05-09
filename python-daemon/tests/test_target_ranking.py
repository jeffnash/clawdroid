from __future__ import annotations

import unittest

from openclaw_android_daemon.targets import best_targets


def _ref(
    ref: str,
    *,
    package: str,
    semantic_label: str,
    resource_id: str,
    is_actionable: bool = True,
    is_direct_control: bool = True,
    is_container: bool = False,
    window_rank: int = 0,
    bounds: dict | None = None,
    confidence_score: float = 0.9,
    active_window: bool = True,
    container_ref: str | None = None,
) -> dict:
    return {
        "ref": ref,
        "package": package,
        "semantic_label": semantic_label,
        "resource_id": resource_id,
        "is_actionable": is_actionable,
        "is_direct_control": is_direct_control,
        "is_container": is_container,
        "window_rank": window_rank,
        "bounds": bounds or {"left": 0, "top": 0, "right": 100, "bottom": 100},
        "confidence_score": confidence_score,
        "active_window": active_window,
        "container_ref": container_ref,
    }


class TargetRankingParityTests(unittest.TestCase):
    def test_original_bucket_order_puts_other_non_chrome_before_foreground_chrome(self) -> None:
        items = [
            _ref(
                "fg_chrome",
                package="com.example.app",
                semantic_label="Back",
                resource_id="com.example.app:id/toolbar_back",
                is_actionable=True,
                is_direct_control=True,
                window_rank=0,
            ),
            _ref(
                "bg_direct",
                package="com.other.app",
                semantic_label="Install",
                resource_id="com.other.app:id/install_button",
                is_actionable=True,
                is_direct_control=True,
                window_rank=1,
            ),
        ]

        result = best_targets(items, fg_package="com.example.app", limit=2)

        self.assertEqual([item["ref"] for item in result], ["bg_direct", "fg_chrome"])

    def test_foreground_direct_actionable_target_wins_first_bucket(self) -> None:
        items = [
            _ref("other", package="com.other", semantic_label="Install", resource_id="com.other:id/install"),
            _ref("fg", package="com.example", semantic_label="Continue", resource_id="com.example:id/continue"),
        ]

        result = best_targets(items, fg_package="com.example", limit=1)

        self.assertEqual(result[0]["ref"], "fg")

    def test_duplicate_overlapping_refs_are_deduped_by_label_container_and_package(self) -> None:
        items = [
            _ref("primary", package="com.example", semantic_label="Continue", resource_id="com.example:id/a", container_ref="row"),
            _ref("duplicate", package="com.example", semantic_label="Continue", resource_id="com.example:id/b", container_ref="row"),
            _ref("next", package="com.example", semantic_label="Next", resource_id="com.example:id/next", container_ref="row2"),
        ]

        result = best_targets(items, fg_package="com.example", limit=5)

        self.assertEqual([item["ref"] for item in result], ["primary", "next"])

    def test_visible_disabled_controls_do_not_beat_actionable_controls(self) -> None:
        items = [
            _ref(
                "disabled",
                package="com.example",
                semantic_label="Install",
                resource_id="com.example:id/install",
                is_actionable=False,
                confidence_score=1.0,
            ),
            _ref("enabled", package="com.other", semantic_label="Open", resource_id="com.other:id/open"),
        ]

        result = best_targets(items, fg_package="com.example", limit=1)

        self.assertEqual(result[0]["ref"], "enabled")

    def test_useful_text_with_weak_role_metadata_is_still_ranked(self) -> None:
        items = [
            _ref("other_direct", package="com.other", semantic_label="Install", resource_id="com.other:id/install"),
            _ref(
                "weak_fg",
                package="com.example",
                semantic_label="Continue setup",
                resource_id="",
                is_direct_control=False,
            ),
        ]

        result = best_targets(items, fg_package="com.example", limit=2)

        self.assertEqual(result[0]["ref"], "weak_fg")

    def test_chrome_navigation_refs_are_deprioritized(self) -> None:
        items = [
            _ref("toolbar_back", package="com.example", semantic_label="Back", resource_id="com.example:id/back"),
            _ref("content_action", package="com.example", semantic_label="Save", resource_id="com.example:id/save"),
        ]

        result = best_targets(items, fg_package="com.example", limit=2)

        self.assertEqual([item["ref"] for item in result], ["content_action", "toolbar_back"])

    def test_fallback_preserves_flattened_order_when_no_actionable_bucket_matches(self) -> None:
        items = [
            _ref("first", package="com.example", semantic_label="Beta", resource_id="", is_actionable=False),
            _ref("second", package="com.example", semantic_label="Alpha", resource_id="", is_actionable=False),
        ]

        result = best_targets(items, fg_package="com.example", limit=2)

        self.assertEqual([item["ref"] for item in result], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
