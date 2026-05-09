from __future__ import annotations

from typing import Any

from . import ref_utils, targets


def screen_signature(screen_context: dict[str, Any] | None) -> tuple[str, str, str]:
    screen_context = screen_context or {}
    return (
        str(screen_context.get("kind") or ""),
        str(screen_context.get("label") or ""),
        str(screen_context.get("path") or ""),
    )


def target_signature(
    flattened: list[dict[str, Any]],
    fg_package: str | None,
    limit: int = 5,
) -> list[str]:
    items = targets.best_targets(flattened, fg_package, limit=limit) or flattened[:limit]
    return [ref_utils.ref_signature(item) for item in items]


def build_screen_context(
    flattened: list[dict[str, Any]],
    package: str | None,
    activity: str | None,
    fg_package: str | None,
) -> dict[str, Any]:
    scope = [
        item for item in flattened
        if item.get("active_window") and (not fg_package or item.get("package") == fg_package)
    ]
    if not scope:
        scope = flattened[:50]

    package_names = {str(item.get("package") or "") for item in scope if item.get("package")}
    resource_ids = {str(item.get("resource_id") or "").casefold() for item in scope if item.get("resource_id")}
    ranked_scope = targets.rank_best_target_items(scope)

    dialog_title = next(
        (
            ref_utils.ref_label(item)
            for item in scope
            if str(item.get("resource_id") or "").casefold() in {
                "android:id/alerttitle",
                "android:id/message",
                "com.android.packageinstaller:id/install_confirm_question",
                "android:id/aerr_restart",
                "android:id/aerr_mute",
                "android:id/aerr_close",
            }
            and ref_utils.ref_label(item)
        ),
        None,
    )
    has_dialog_buttons = any(
        str(item.get("resource_id") or "").casefold().endswith(("button1", "button2", "button3"))
        or str(item.get("resource_id") or "").casefold().startswith("android:id/aerr_")
        for item in scope
    )
    crash_dialog = any(rid.startswith("android:id/aerr_") for rid in resource_ids)
    permission_dialog = (
        any(pkg == "com.android.permissioncontroller" for pkg in package_names)
        or any(
            rid.endswith((
                "permission_allow_button",
                "permission_allow_foreground_only_button",
                "permission_deny_button",
                "permission_deny_and_dont_ask_again_button",
            ))
            for rid in resource_ids
        )
    )
    installer_dialog = (
        any(pkg == "com.android.packageinstaller" for pkg in package_names)
        or "com.android.packageinstaller:id/install_confirm_question" in resource_ids
    )

    container_items = [
        item for item in scope
        if item.get("is_container")
        or str(item.get("role") or "").casefold() in {"scrollview", "list", "grid", "drawer", "pager", "tabs"}
        or str(item.get("container_role") or "").casefold() in {"scrollview", "list", "grid", "drawer", "pager", "tabs"}
        or str(item.get("section_role") or "").casefold() in {"scrollview", "list", "grid", "drawer", "pager", "tabs"}
    ]
    dominant_container = None
    if container_items:
        dominant_container = max(container_items, key=lambda item: targets.container_priority(item, fg_package))
        if targets.container_priority(dominant_container, fg_package) < 40.0:
            dominant_container = None

    container_candidates: list[dict[str, Any]] = []
    seen_containers: set[str] = set()
    for item in sorted(container_items, key=lambda ref: targets.container_priority(ref, fg_package), reverse=True):
        ref_id = str(item.get("ref") or "")
        if ref_id in seen_containers:
            continue
        seen_containers.add(ref_id)
        role = str(item.get("role") or item.get("container_role") or item.get("section_role") or "").strip()
        label_text = ref_utils.ref_label(item).strip()
        if targets.is_generic_ui_label(label_text):
            label_text = str(item.get("container_label") or item.get("section_label") or "").strip()
        path = [str(part).strip() for part in (item.get("path_labels") or []) if str(part).strip()]
        container_candidates.append({
            "ref": ref_id or None,
            "role": role or None,
            "label": label_text or None,
            "path": path,
        })
        if len(container_candidates) >= 5:
            break

    dominant_role = str(
        (dominant_container or {}).get("role")
        or (dominant_container or {}).get("container_role")
        or (dominant_container or {}).get("section_role")
        or ""
    ).casefold()
    dominant_label = ref_utils.ref_label(dominant_container) if dominant_container else None
    if dominant_label and targets.is_generic_ui_label(dominant_label):
        dominant_label = (
            str((dominant_container or {}).get("container_label") or "").strip()
            or str((dominant_container or {}).get("section_label") or "").strip()
            or None
        )
    dominant_path = (
        " / ".join(str(part).strip() for part in ((dominant_container or {}).get("path_labels") or []) if str(part).strip())
        or None
    )

    kind = "screen"
    archetype = "screen"
    label = dominant_label
    if has_dialog_buttons or any(
        pkg in {"com.android.packageinstaller", "com.android.permissioncontroller"} for pkg in package_names
    ):
        kind = "dialog"
        archetype = (
            "crash_dialog"
            if crash_dialog
            else "permission_dialog"
            if permission_dialog
            else "installer_dialog"
            if installer_dialog
            else "confirmation_dialog"
        )
        label = dialog_title or dominant_label or "dialog"
    else:
        for candidate in container_candidates:
            haystack = " ".join(
                part for part in [
                    candidate.get("role") or "",
                    candidate.get("label") or "",
                    " / ".join(candidate.get("path") or []),
                ] if part
            ).lower()
            if "popup" in haystack or "shortcut" in haystack:
                kind = "popup_menu"
                archetype = "context_menu"
                label = candidate.get("label") or "popup menu"
                break
            if "dialog" in haystack:
                kind = "dialog"
                archetype = "dialog"
                label = candidate.get("label") or "dialog"
                break
        if kind == "screen" and dominant_role == "drawer":
            kind = "drawer"
            archetype = "navigation_drawer"
            label = label or "navigation drawer"
        elif kind == "screen" and dominant_role == "grid":
            kind = "grid"
            archetype = "results_grid" if any(ref_utils.ref_detail_labels(item) for item in ranked_scope[:8]) else "grid"
        elif kind == "screen" and dominant_role in {"list", "scrollview"}:
            kind = "list"
            archetype = "results_list" if any(ref_utils.ref_detail_labels(item) for item in ranked_scope[:8]) else "collection_list"

    if kind == "screen" and fg_package and fg_package.startswith("com.android.launcher"):
        kind = "launcher"
        archetype = "home_launcher"
        label = label or "home screen"

    best_target = next(
        (
            item for item in ranked_scope
            if item.get("is_actionable") and ref_utils.ref_label(item) and not targets.is_chrome_ref(item)
        ),
        next(
            (item for item in ranked_scope if item.get("is_actionable") and ref_utils.ref_label(item)),
            scope[0] if scope else None,
        ),
    )

    primary_action = None
    secondary_action_refs: list[str] = []
    actionable_ranked = [item for item in ranked_scope if item.get("is_actionable") and ref_utils.ref_label(item)]
    if kind == "dialog":
        primary_action = next(
            (
                item for item in actionable_ranked
                if targets.action_priority(item) >= 0 and not targets.is_backward_ref(item)
            ),
            actionable_ranked[0] if actionable_ranked else None,
        )
        secondary_action_refs = [
            str(item.get("ref"))
            for item in actionable_ranked
            if primary_action is None or item.get("ref") != primary_action.get("ref")
        ][:3]
    elif best_target and best_target.get("is_actionable"):
        primary_action = best_target
        secondary_action_refs = [
            str(item.get("ref"))
            for item in actionable_ranked
            if item.get("ref") != best_target.get("ref")
        ][:3]

    return {
        "kind": kind,
        "archetype": archetype,
        "label": label,
        "package": fg_package or package,
        "activity": activity,
        "path": dominant_path or next(
            (" / ".join(candidate["path"]) for candidate in container_candidates if candidate.get("path")),
            None,
        ),
        "best_target_label": ref_utils.ref_label(best_target) if best_target else None,
        "primary_action_ref": primary_action.get("ref") if primary_action else None,
        "primary_action_label": ref_utils.ref_label(primary_action) if primary_action else None,
        "dominant_container_ref": dominant_container.get("ref") if dominant_container else None,
        "dominant_container_role": dominant_role or None,
        "dominant_container_label": dominant_label,
        "secondary_action_refs": secondary_action_refs,
        "containers": container_candidates,
    }
