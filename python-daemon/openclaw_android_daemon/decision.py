from __future__ import annotations

import re
from typing import Any

from . import ref_utils, targets


def normalize_decision_mode(mode: str | None, default: str) -> str:
    candidate = str(mode or default or "auto").strip().lower()
    aliases = {
        "llm": "llm_text",
        "vision": "llm_vision",
        "text": "llm_text",
        "image": "llm_vision",
    }
    normalized = aliases.get(candidate, candidate)
    return normalized if normalized in {"deterministic", "auto", "llm_text", "llm_vision"} else "auto"


def goal_terms(text: str | None) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "android",
        "app",
        "best",
        "button",
        "choose",
        "click",
        "continue",
        "current",
        "for",
        "from",
        "in",
        "next",
        "of",
        "on",
        "or",
        "screen",
        "single",
        "step",
        "task",
        "the",
        "this",
        "to",
        "ui",
        "with",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").casefold())
        if len(token) >= 3 and token not in stop_words
    }


def goal_match_score(ref: dict[str, Any], terms: set[str]) -> int:
    if not terms:
        return 0
    label_terms = set(goal_terms(ref_utils.ref_label(ref)))
    for detail in ref_utils.ref_detail_labels(ref):
        label_terms.update(goal_terms(detail))
    if not label_terms:
        return 0
    return 35 * len(terms & label_terms)


def deterministic_decision(
    snapshot_result: dict[str, Any],
    goal: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    top_refs = list(snapshot_result.get("top_refs") or [])
    if not top_refs:
        warnings.append("No ranked actionable refs were available for deterministic selection.")
        return None, warnings

    terms = goal_terms(goal)
    scored: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for item in top_refs[:8]:
        label = ref_utils.ref_label(item)
        confidence = float(item.get("confidence_score") or 0.0)
        action_bias = targets.action_priority(item)
        score = int(confidence * 100) + action_bias + goal_match_score(item, terms)
        if targets.is_generic_ui_label(label):
            score -= 55
        if targets.is_headerish_ref(item):
            score -= 80
        if targets.is_chrome_ref(item):
            score -= 90
        if targets.is_backward_ref(item):
            score -= 120
        if item.get("package") == snapshot_result.get("foreground_package"):
            score += 15
        scored.append(
            (
                score,
                item,
                {
                    "label": label,
                    "confidence": confidence,
                    "action_bias": action_bias,
                    "goal_match": goal_match_score(item, terms),
                },
            )
        )

    scored.sort(key=lambda entry: entry[0], reverse=True)
    best_score, best_item, best_meta = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else None
    strong_forward_candidate = bool(best_meta["action_bias"] >= 80)
    strong_goal_match = bool(best_meta["goal_match"] >= 70)
    strong_label = not targets.is_generic_ui_label(best_meta["label"]) and not targets.is_headerish_ref(best_item)
    clear_margin = second_score is None or (best_score - second_score) >= 40
    confident = best_meta["confidence"] >= 0.95 or strong_forward_candidate or strong_goal_match

    if strong_label and clear_margin and confident and not targets.is_backward_ref(best_item):
        return (
            {
                "decision": "click",
                "ref": best_item.get("ref"),
                "label": best_meta["label"],
                "confidence": min(1.0, max(0.0, best_meta["confidence"] or 0.0)),
                "reason": "Deterministic selector found a clear forward-progress target.",
            },
            warnings,
        )

    warnings.append(
        "Deterministic selection was ambiguous; escalating to LLM reasoning."
    )
    return None, warnings


def should_use_vision_for_decision(snapshot_result: dict[str, Any], goal: str) -> bool:
    stats = snapshot_result.get("stats") or {}
    if stats.get("screenshot_recommended"):
        return True
    current_app = snapshot_result.get("current_app") or {}
    if current_app.get("package") == "com.android.settings":
        return True
    top_refs = list(snapshot_result.get("top_refs") or [])
    if not top_refs:
        return True
    first_labels = [ref_utils.ref_label(item) for item in top_refs[:3]]
    if any(targets.is_generic_ui_label(label) for label in first_labels):
        return True
    if any(targets.is_headerish_ref(item) for item in top_refs[:2]):
        return True
    if len(top_refs) >= 2:
        gap = abs(
            int((top_refs[0].get("confidence_score") or 0.0) * 100)
            - int((top_refs[1].get("confidence_score") or 0.0) * 100)
        )
        if gap <= 15:
            return True
    terms = goal_terms(goal)
    return bool(terms and not any(goal_match_score(item, terms) for item in top_refs[:4]))
