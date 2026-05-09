from __future__ import annotations

import re
from typing import Any

from . import ref_utils


def is_chrome_ref(ref: dict[str, Any]) -> bool:
    package = str(ref.get("package") or "")
    if package.startswith("com.android.systemui"):
        return True
    resource_id = ref_utils.resource_leaf(str(ref.get("resource_id") or "")).lower()
    if resource_id in {
        "action_home",
        "action_apps",
        "action_stores",
        "action_curation",
        "menu_item_search",
        "menu_remote_install",
        "toolbar",
        "back",
        "recent_apps",
        "home",
    }:
        return True
    if (
        "toolbar" in resource_id
        or "navigation_bar" in resource_id
        or "bottom_navigation" in resource_id
        or resource_id.endswith("_back")
        or resource_id.startswith("back_")
    ):
        return True
    haystack = " ".join(
        str(part).lower()
        for part in (
            ref.get("container_role"),
            ref.get("container_label"),
            ref.get("section_role"),
            ref.get("section_label"),
            ref.get("window_type"),
            ref.get("semantic_id"),
            ref.get("semantic_label"),
            ref.get("content_desc"),
            " / ".join(ref.get("path_labels") or []),
        )
        if part
    )
    chrome_markers = (
        "toolbar",
        "app bar",
        "action bar",
        "bottom navigation",
        "navigation bar",
        "status bar",
        "nav buttons",
        "system icons",
        "center group",
        "ends group",
    )
    return any(marker in haystack for marker in chrome_markers)


def container_priority(ref: dict[str, Any], fg_package: str | None) -> float:
    role = str(ref.get("role") or ref.get("container_role") or ref.get("section_role") or "").casefold()
    label = ref_utils.ref_label(ref).casefold()
    resource_id = ref_utils.resource_leaf(str(ref.get("resource_id") or "")).casefold()
    path = " / ".join(str(part).casefold() for part in (ref.get("path_labels") or []))
    score = float(min(250, ref_utils.bounds_area(ref.get("bounds")) / 12000.0))
    if ref.get("package") == fg_package:
        score += 80.0
    if ref.get("active_window"):
        score += 25.0
    if not is_chrome_ref(ref):
        score += 40.0
    else:
        score -= 140.0
    if role in {"list", "grid", "scrollview"}:
        score += 110.0
    elif role == "drawer":
        score += 95.0
    elif role in {"pager", "tabs"}:
        score += 40.0
    elif role == "group":
        score -= 15.0
    if ref.get("is_container"):
        score += 25.0
    score += min(60.0, float(len(ref.get("child_refs") or [])) * 8.0)
    if "filter" in label or "filter" in resource_id or "filter" in path:
        score -= 90.0
    if any(marker in f"{label} {resource_id} {path}" for marker in ("similar", "recommended", "related", "sponsored")):
        score -= 85.0
    if any(marker in f"{label} {resource_id} {path}" for marker in ("install group", "appview full", "details", "content")):
        score += 35.0
    if any(marker in f"{label} {resource_id} {path}" for marker in ("toolbar", "app bar", "bottom navigation", "navigation bar")):
        score -= 110.0
    return score


def action_priority(ref: dict[str, Any]) -> int:
    label = ref_utils.ref_label(ref).strip().casefold()
    resource_id = ref_utils.resource_leaf(str(ref.get("resource_id") or "")).casefold()
    path = " / ".join(str(part).casefold() for part in (ref.get("path_labels") or []))
    label_tokens = set(re.findall(r"[a-z0-9]+", label))
    resource_tokens = set(re.findall(r"[a-z0-9]+", resource_id))
    path_tokens = set(re.findall(r"[a-z0-9]+", path))

    def has_term(term: str) -> bool:
        lowered = term.casefold()
        if " " in lowered:
            return lowered in label or lowered in resource_id or lowered in path
        return lowered in label_tokens or lowered in resource_tokens or lowered in path_tokens

    positive_terms = (
        "install",
        "open",
        "update",
        "allow",
        "continue",
        "ok",
        "next",
        "accept",
        "confirm",
        "download",
        "resume",
        "play",
        "retry",
        "settings",
    )
    negative_terms = (
        "cancel",
        "don't allow",
        "dont allow",
        "deny",
        "decline",
        "later",
        "close",
        "dismiss",
        "skip",
        "back",
        "no thanks",
    )
    low_value_terms = (
        "trusted",
        "beta",
        "followed stores",
        "other versions",
        "share",
        "install on tv",
    )
    score = 0
    if any(has_term(term) for term in positive_terms):
        score += 120
    if any(has_term(term) for term in negative_terms):
        score -= 160
    if any(has_term(term) for term in low_value_terms):
        score -= 70
    if has_term("filter"):
        score -= 40
    if has_term("install group"):
        score += 40
    if resource_id == "aerr_close":
        score += 180
    if resource_id == "aerr_app_info":
        score -= 80
    if resource_id.endswith("button1") or resource_id in {
        "permission_allow_button",
        "permission_allow_foreground_only_button",
        "continue_button",
        "ok_button",
    }:
        score += 140
    if resource_id.endswith(("button2", "button3")):
        score -= 100
    if "button panel" in path and not any(has_term(term) for term in negative_terms):
        score += 70
    if has_term("button panel") and has_term("settings"):
        score += 20
    return score


def is_generic_ui_label(text: str | None) -> bool:
    lowered = ref_utils.ref_label({"semantic_label": text}).casefold()
    return lowered in {
        "",
        "linear layout",
        "frame layout",
        "relative layout",
        "view group",
        "group",
        "content parent",
        "scroll view",
        "scrollview",
        "recycler view",
        "switch widget",
        "button",
        "image button",
    }


def is_headerish_ref(ref: dict[str, Any]) -> bool:
    resource_id = str(ref.get("resource_id") or "").casefold()
    path = " / ".join(str(part).casefold() for part in (ref.get("path_labels") or []))
    label = ref_utils.ref_label(ref).casefold()
    return any(
        marker in f"{resource_id} {path} {label}"
        for marker in (
            "header",
            "entity_header",
            "toolbar",
            "action bar",
            "app bar",
            "share",
        )
    )


def is_backward_ref(ref: dict[str, Any]) -> bool:
    label = ref_utils.ref_label(ref).casefold()
    return any(
        marker in label
        for marker in (
            "navigate up",
            "back",
            "cancel",
            "dismiss",
            "close",
            "later",
            "skip",
            "don't allow",
            "dont allow",
            "deny",
        )
    )


def looks_install_progress_label(text: str | None) -> bool:
    label = ref_utils.ref_label({"semantic_label": text}).casefold()
    if not label:
        return False
    tokens = set(re.findall(r"[a-z0-9]+", label))
    if tokens == {"install"}:
        return False
    if "open" in tokens or "update" in tokens or "installing" in tokens or "uninstall" in tokens:
        return True
    if "pause" in tokens or "resume" in tokens:
        return True
    return "download" in tokens and "install" not in tokens


def rank_best_target_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            action_priority(item),
            float(item.get("confidence_score") or 0.0),
            min(4, len(ref_utils.ref_detail_labels(item))),
            0 if is_generic_ui_label(ref_utils.ref_label(item)) else 1,
            1 if item.get("is_direct_control") else 0,
            -int(item.get("window_rank") or 999),
        ),
        reverse=True,
    )


def best_targets(flattened: list[dict[str, Any]], fg_package: str | None, limit: int = 5) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add_refs(items: list[dict[str, Any]], *, rank: bool = True) -> bool:
        ordered_items = rank_best_target_items(items) if rank else items
        for item in ordered_items:
            label = ref_utils.ref_label(item).casefold()
            key = (
                str(item.get("package") or ""),
                label,
                str(item.get("container_ref") or item.get("section_ref") or item.get("parent_ref") or ""),
                str(item.get("window_type") or ""),
            )
            if label and key in seen:
                continue
            if label:
                seen.add(key)
            results.append(item)
            if len(results) >= limit:
                return True
        return False

    actionable = [item for item in flattened if item.get("is_actionable")]
    foreground_direct: list[dict[str, Any]] = []
    foreground_direct_chrome: list[dict[str, Any]] = []
    foreground_containers: list[dict[str, Any]] = []
    foreground_containers_chrome: list[dict[str, Any]] = []
    foreground_other: list[dict[str, Any]] = []
    foreground_other_chrome: list[dict[str, Any]] = []
    other_direct: list[dict[str, Any]] = []
    other_direct_chrome: list[dict[str, Any]] = []
    other_containers: list[dict[str, Any]] = []
    other_containers_chrome: list[dict[str, Any]] = []
    other_actionable: list[dict[str, Any]] = []
    other_actionable_chrome: list[dict[str, Any]] = []
    for item in actionable:
        is_foreground = bool(fg_package and item.get("package") == fg_package)
        is_direct = bool(item.get("is_direct_control"))
        is_container = bool(item.get("is_container"))
        is_chrome = is_chrome_ref(item)
        if is_foreground and is_direct and not is_chrome:
            foreground_direct.append(item)
        elif is_foreground and is_direct:
            foreground_direct_chrome.append(item)
        elif is_foreground and is_container and not is_chrome:
            foreground_containers.append(item)
        elif is_foreground and is_container:
            foreground_containers_chrome.append(item)
        elif is_foreground and not is_chrome:
            foreground_other.append(item)
        elif is_foreground:
            foreground_other_chrome.append(item)
        elif is_direct and not is_chrome:
            other_direct.append(item)
        elif is_direct:
            other_direct_chrome.append(item)
        elif is_container and not is_chrome:
            other_containers.append(item)
        elif is_container:
            other_containers_chrome.append(item)
        elif not is_chrome:
            other_actionable.append(item)
        else:
            other_actionable_chrome.append(item)

    if add_refs(foreground_direct):
        return results[:limit]
    if add_refs(foreground_other):
        return results[:limit]
    if add_refs(foreground_containers):
        return results[:limit]
    if add_refs(other_direct):
        return results[:limit]
    if add_refs(other_actionable):
        return results[:limit]
    if add_refs(other_containers):
        return results[:limit]
    if add_refs(foreground_direct_chrome):
        return results[:limit]
    if add_refs(foreground_other_chrome):
        return results[:limit]
    if add_refs(foreground_containers_chrome):
        return results[:limit]
    if add_refs(other_direct_chrome):
        return results[:limit]
    if add_refs(other_actionable_chrome):
        return results[:limit]
    if add_refs(other_containers_chrome):
        return results[:limit]
    add_refs(flattened, rank=False)
    return results[:limit]
