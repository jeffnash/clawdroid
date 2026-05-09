from __future__ import annotations

from typing import Any

from . import ref_utils, screen_context, targets
from .models import SnapshotState


def match_post_action_ref(
    handle: Any,
    refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    best_score = -1
    best_match: dict[str, Any] | None = None
    expected_label = handle.primary_label().strip().casefold()
    expected_details = {label.casefold() for label in ref_utils.ref_detail_labels(handle)}
    expected_parent = str(getattr(handle, "parent_label", "") or "").strip().casefold()
    expected_container = str(getattr(handle, "container_label", "") or "").strip().casefold()
    expected_path = {
        str(part or "").strip().casefold()
        for part in getattr(handle, "path_labels", ()) or ()
        if str(part or "").strip()
    }
    for item in refs:
        score = 0
        if handle.node_key and item.get("node_key") == handle.node_key:
            score += 100
        if handle.semantic_id and item.get("semantic_id") == handle.semantic_id:
            score += 90
        if handle.resource_id and item.get("resource_id") == handle.resource_id:
            score += 80
        if handle.package and item.get("package") == handle.package:
            score += 15
        if item.get("role") == handle.role:
            score += 15
        label = ref_utils.ref_label(item).strip().casefold()
        if expected_label and label == expected_label:
            score += 25
        elif expected_label and label and (expected_label in label or label in expected_label):
            score += 12
        item_details = {label.casefold() for label in ref_utils.ref_detail_labels(item)}
        score += 8 * len(expected_details & item_details)
        item_parent = str(item.get("parent_label") or "").strip().casefold()
        if expected_parent and item_parent == expected_parent:
            score += 10
        item_container = str(item.get("container_label") or "").strip().casefold()
        if expected_container and item_container == expected_container:
            score += 12
        item_path = {
            str(part or "").strip().casefold()
            for part in (item.get("path_labels") or [])
            if str(part or "").strip()
        }
        score += 4 * len(expected_path & item_path)
        bounds = tuple(item.get("bounds") or ())
        if bounds == handle.bounds:
            score += 10
        if score > best_score:
            best_score = score
            best_match = item
    if best_score < 25:
        return None
    return best_match


def verify_action_result(
    *,
    op: str,
    text: str | None,
    handle: Any,
    before_snapshot: SnapshotState,
    before_flattened: list[dict[str, Any]],
    before_screen: dict[str, Any],
    before_current: dict[str, Any],
    before_event_seq: int | None,
    post_state: dict[str, Any],
    post_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    after_current = (post_snapshot or {}).get("current_app") or post_state.get("current_app") or {"package": None, "activity": None}
    after_event_seq = (
        (post_snapshot or {}).get("stats", {}).get("event_seq")
        if post_snapshot and post_snapshot.get("ok")
        else post_state.get("event_seq")
    )
    before_pkg = before_current.get("package")
    before_activity = before_current.get("activity")
    after_pkg = after_current.get("package")
    after_activity = after_current.get("activity")
    package_changed = bool(before_pkg != after_pkg)
    activity_changed = bool(before_activity != after_activity)
    event_seq_changed = (
        before_event_seq is not None
        and after_event_seq is not None
        and before_event_seq != after_event_seq
    )

    post_flattened = list((post_snapshot or {}).get("refs") or [])
    post_screen = (post_snapshot or {}).get("screen_context") or {}
    screen_changed = (
        bool(post_snapshot and post_snapshot.get("ok"))
        and screen_context.screen_signature(before_screen) != screen_context.screen_signature(post_screen)
    )
    top_targets_changed = (
        bool(post_snapshot and post_snapshot.get("ok"))
        and screen_context.target_signature(before_flattened, before_snapshot.foreground_package)
        != screen_context.target_signature(post_flattened, (post_snapshot or {}).get("foreground_package"))
    )

    matched_post_ref = match_post_action_ref(handle, post_flattened) if post_flattened else None
    target_missing_after = bool(post_snapshot and post_snapshot.get("ok") and matched_post_ref is None)
    target_state_changed = False
    text_applied = False
    cleared_text = False

    if matched_post_ref is not None:
        target_state_changed = any(
            (
                bool(matched_post_ref.get("selected")) != bool(handle.selected),
                bool(matched_post_ref.get("checked")) != bool(handle.checked),
                bool(matched_post_ref.get("focused")) != bool(handle.focused),
            )
        )
        after_label = ref_utils.ref_label(matched_post_ref).strip()
        after_text = str(matched_post_ref.get("text") or "").strip()
        after_desc = str(matched_post_ref.get("content_desc") or "").strip()
        if op == "set_text":
            needle = str(text or "").strip()
            if needle:
                lowered = needle.casefold()
                haystacks = [after_text.casefold(), after_label.casefold(), after_desc.casefold()]
                text_applied = any(lowered in hay for hay in haystacks if hay)
                target_state_changed = target_state_changed or text_applied
        elif op == "clear_text":
            cleared_text = not after_text
            target_state_changed = target_state_changed or cleared_text

    reasons: list[str] = []
    if package_changed:
        reasons.append("package_changed")
    if activity_changed:
        reasons.append("activity_changed")
    if event_seq_changed:
        reasons.append("event_seq_changed")
    if screen_changed:
        reasons.append("screen_changed")
    if top_targets_changed:
        reasons.append("top_targets_changed")
    if target_missing_after and op in {"click", "long_click"}:
        reasons.append("target_consumed")
    if target_state_changed:
        reasons.append("target_state_changed")
    if text_applied:
        reasons.append("text_applied")
    if cleared_text:
        reasons.append("text_cleared")

    normalized_op = "click" if op == "click_center" else op
    installer_no_progress = False
    if before_screen.get("archetype") == "installer_dialog" and normalized_op in {"click", "long_click"}:
        after_primary_label = str(post_screen.get("primary_action_label") or "").strip()
        after_best_label = str(post_screen.get("best_target_label") or "").strip()
        if any(targets.looks_install_progress_label(label) for label in (after_primary_label, after_best_label)):
            reasons.append("installer_progressed")
        elif after_primary_label.casefold() == "install" or after_best_label.casefold() == "install":
            installer_no_progress = True
            reasons.append("installer_no_progress")
    if normalized_op in {"click", "long_click", "press_back", "press_home", "press_recents", "press_enter"}:
        verified = bool(
            package_changed
            or activity_changed
            or screen_changed
            or top_targets_changed
            or target_missing_after
            or target_state_changed
            or event_seq_changed
        )
        if installer_no_progress:
            verified = False
    elif op in {"scroll_forward", "scroll_backward", "scroll_to_start", "scroll_to_end"}:
        verified = bool(screen_changed or top_targets_changed or event_seq_changed or target_state_changed)
    elif op == "set_text":
        verified = bool(text_applied or target_state_changed or event_seq_changed)
    elif op == "clear_text":
        verified = bool(cleared_text or target_state_changed or event_seq_changed)
    else:
        verified = bool(reasons)

    return {
        "verified": verified,
        "settled": bool(post_state.get("settled")),
        "package_changed": package_changed,
        "activity_changed": activity_changed,
        "event_seq_changed": event_seq_changed,
        "screen_changed": screen_changed,
        "top_targets_changed": top_targets_changed,
        "target_missing_after": target_missing_after,
        "target_state_changed": target_state_changed,
        "matched_post_ref": matched_post_ref.get("ref") if matched_post_ref else None,
        "matched_post_label": ref_utils.ref_label(matched_post_ref) if matched_post_ref else None,
        "text_applied": text_applied,
        "text_cleared": cleared_text,
        "reasons": reasons,
        "before": {
            "package": before_pkg,
            "activity": before_activity,
            "event_seq": before_event_seq,
            "screen": before_screen,
        },
        "after": {
            "package": after_pkg,
            "activity": after_activity,
            "event_seq": after_event_seq,
            "screen": post_screen if post_screen else None,
        },
    }


def verification_is_weak(verification: dict[str, Any]) -> bool:
    reasons = set(verification.get("reasons") or [])
    if not verification.get("verified"):
        return True
    return reasons.issubset({"event_seq_changed"})
