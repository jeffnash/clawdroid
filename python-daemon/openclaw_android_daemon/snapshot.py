from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from typing import Any

from .models import NodeHandle, SnapshotState, _humanize_token


WEAK_LABEL_SOURCES = {"", "resource_id", "class_name", "role"}
STRONG_LABEL_SOURCES = {"text", "content_desc", "hint_text"}
PRIMARY_LABEL_HINTS = (
    "app_name",
    "title",
    "label",
    "header",
    "subject",
    "summary",
    "query",
)
SECONDARY_LABEL_HINTS = (
    "developer",
    "author",
    "publisher",
    "vendor",
)
NOISY_LABEL_HINTS = (
    "rating",
    "download",
    "size",
    "version",
    "count",
    "badge",
    "icon",
    "image",
    "logo",
)
GENERIC_CONTEXT_LABELS = frozenset((
    "frame layout",
    "linear layout",
    "relative layout",
    "view group",
    "group",
    "content parent",
    "scroll view",
    "scrollview",
    "recycler view",
))


def _normalize_label(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _center(bounds: tuple[int, int, int, int]) -> tuple[float, float]:
    x1, y1, x2, y2 = bounds
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _overlap(a1: int, a2: int, b1: int, b2: int) -> int:
    return max(0, min(a2, b2) - max(a1, b1))


def _base_label(handle: NodeHandle) -> tuple[str, str]:
    candidates = (
        (_normalize_label(handle.semantic_label), handle.label_source),
        (_normalize_label(handle.text), "text"),
        (_normalize_label(handle.content_desc), "content_desc"),
        (_normalize_label(handle.hint_text), "hint_text"),
    )
    for label, source in candidates:
        if label:
            return label, source
    if handle.resource_id:
        token = _humanize_token(handle.resource_id.rsplit("/", 1)[-1])
        if token:
            return token, "resource_id"
    if handle.class_name:
        token = _humanize_token(handle.class_name.rsplit(".", 1)[-1])
        if token:
            return token, "class_name"
    return "", ""


def _is_strong_source(source: str) -> bool:
    return source in STRONG_LABEL_SOURCES or source.startswith(("child_", "sibling_"))


def _looks_numericish(label: str) -> bool:
    compact = label.replace(" ", "")
    if not compact:
        return True
    if re.fullmatch(r"[-+]?[\d.,]+[A-Za-z%]*", compact):
        return True
    letters = sum(ch.isalpha() for ch in compact)
    digits = sum(ch.isdigit() for ch in compact)
    return digits > letters and letters <= 2


def _is_generic_context_label(label: str) -> bool:
    return _normalize_label(label).casefold() in GENERIC_CONTEXT_LABELS


def _label_candidate_score(handle: NodeHandle, candidate: NodeHandle, label: str, source: str) -> float:
    score = 0.0
    if not label:
        return score
    if _is_generic_context_label(label):
        return 0.0
    if source in STRONG_LABEL_SOURCES:
        score += 200.0
    elif source.startswith(("child_", "sibling_")):
        score += 160.0
    elif source == "resource_id":
        score += 50.0
    if candidate.package == handle.package:
        score += 40.0
    if candidate.role == "text":
        score += 80.0
    if not candidate.is_actionable:
        score += 35.0
    rid = candidate.resource_id.rsplit("/", 1)[-1].lower() if candidate.resource_id else ""
    if rid:
        rid_parts = {part for part in re.split(r"[^a-z0-9]+", rid) if part}
        if rid == "app_name" or rid.endswith("_app_name"):
            score += 420.0
        elif any(token in rid for token in PRIMARY_LABEL_HINTS):
            score += 320.0
        if rid in {"developer_name", "author_name", "publisher_name", "vendor_name"} or any(
            token in rid_parts for token in SECONDARY_LABEL_HINTS
        ):
            score += 120.0
        if any(token in rid for token in NOISY_LABEL_HINTS):
            score -= 180.0
    if candidate.selected or candidate.focused:
        score += 40.0
    if _looks_numericish(label):
        score -= 140.0
    area = max(1, (candidate.bounds[2] - candidate.bounds[0]) * (candidate.bounds[3] - candidate.bounds[1]))
    score += min(area / 50000.0, 40.0)
    return score


def _label_bucket(candidate: NodeHandle, label: str) -> str:
    if not label:
        return "primary"
    rid = candidate.resource_id.rsplit("/", 1)[-1].lower() if candidate.resource_id else ""
    rid_parts = {part for part in re.split(r"[^a-z0-9]+", rid) if part}
    if rid == "app_name" or rid.endswith("_app_name") or any(token in rid for token in PRIMARY_LABEL_HINTS):
        return "primary"
    if rid in {"developer_name", "author_name", "publisher_name", "vendor_name"}:
        return "secondary"
    if any(token in rid_parts for token in SECONDARY_LABEL_HINTS):
        return "secondary"
    if any(token in rid for token in NOISY_LABEL_HINTS):
        return "secondary"
    if _looks_numericish(label):
        return "secondary"
    return "primary"


def _collect_related_labels(handle: NodeHandle, candidate_refs: list[str], refs: dict[str, NodeHandle]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    primary_label = handle.primary_label().casefold()
    best_by_label: dict[str, tuple[float, str, str]] = {}
    for candidate_ref in candidate_refs:
        if candidate_ref == handle.ref:
            continue
        candidate = refs[candidate_ref]
        if not _is_local_related_candidate(handle, candidate, refs):
            continue
        label, source = _base_label(candidate)
        normalized = label.casefold()
        if not label or normalized == primary_label:
            continue
        score = _label_candidate_score(handle, candidate, label, source)
        if score <= 0:
            continue
        bucket = _label_bucket(candidate, label)
        current = best_by_label.get(normalized)
        if current is None or score > current[0]:
            best_by_label[normalized] = (score, label, bucket)

    primary: list[str] = []
    secondary: list[str] = []
    for _, label, bucket in sorted(best_by_label.values(), key=lambda item: item[0], reverse=True):
        if bucket == "secondary":
            if len(secondary) < 4:
                secondary.append(label)
            continue
        if len(primary) < 4:
            primary.append(label)
    return tuple(primary), tuple(secondary)


def _bounds_overlap_ratio(bounds_a: tuple[int, int, int, int], bounds_b: tuple[int, int, int, int], axis: str) -> float:
    if axis == "x":
        overlap = _overlap(bounds_a[0], bounds_a[2], bounds_b[0], bounds_b[2])
        span = min(max(1, bounds_a[2] - bounds_a[0]), max(1, bounds_b[2] - bounds_b[0]))
    else:
        overlap = _overlap(bounds_a[1], bounds_a[3], bounds_b[1], bounds_b[3])
        span = min(max(1, bounds_a[3] - bounds_a[1]), max(1, bounds_b[3] - bounds_b[1]))
    return overlap / float(span)


def _is_descendant(refs: dict[str, NodeHandle], descendant_ref: str | None, ancestor_ref: str | None) -> bool:
    cursor_ref = descendant_ref
    while cursor_ref and cursor_ref in refs:
        if cursor_ref == ancestor_ref:
            return True
        cursor_ref = refs[cursor_ref].parent_ref
    return False


def _is_local_related_candidate(handle: NodeHandle, candidate: NodeHandle, refs: dict[str, NodeHandle]) -> bool:
    if candidate.ref == handle.ref:
        return False

    if _is_descendant(refs, candidate.ref, handle.ref) or _is_descendant(refs, handle.ref, candidate.ref):
        return True

    same_parent = bool(handle.parent_ref and handle.parent_ref == candidate.parent_ref)
    same_container = bool(handle.container_ref and handle.container_ref == candidate.container_ref)
    same_section = bool(handle.section_ref and handle.section_ref == candidate.section_ref)
    same_row = _bounds_overlap_ratio(handle.bounds, candidate.bounds, "y") >= 0.55
    same_column = _bounds_overlap_ratio(handle.bounds, candidate.bounds, "x") >= 0.55
    vertical_form = bool(handle.editable or handle.checkable or handle.role in {"textbox", "checkbox"})

    if same_parent:
        if same_row:
            return True
        return bool(vertical_form and same_column and not candidate.is_actionable)

    if same_container or same_section:
        if same_row:
            return True
        return bool(vertical_form and same_column and not candidate.is_actionable)

    return False


def _best_child_label(handle: NodeHandle, child_refs: list[str], refs: dict[str, NodeHandle]) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []
    for child_ref in child_refs:
        child = refs[child_ref]
        label, source = _base_label(child)
        if not label:
            continue
        if source in WEAK_LABEL_SOURCES and not child.has_strong_label:
            continue
        candidates.append((label, source))
    if not candidates:
        return "", ""
    unique_labels: list[tuple[str, str]] = []
    seen = set()
    for label, source in candidates:
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_labels.append((label, source))
    if len(unique_labels) == 1:
        label, source = unique_labels[0]
        return label, f"child_{source}"
    if handle.is_actionable and len(unique_labels) <= 2:
        joined = " ".join(label for label, _ in unique_labels if label)
        if joined:
            return joined, "child_text"
    return unique_labels[0][0], f"child_{unique_labels[0][1]}"


def _best_sibling_label(handle: NodeHandle, sibling_refs: list[str], refs: dict[str, NodeHandle]) -> tuple[str, str]:
    node_cx, node_cy = _center(handle.bounds)
    node_w = max(1, handle.bounds[2] - handle.bounds[0])
    node_h = max(1, handle.bounds[3] - handle.bounds[1])
    best: tuple[float, str, str] | None = None
    for sibling_ref in sibling_refs:
        sibling = refs[sibling_ref]
        if sibling_ref == handle.ref:
            continue
        label, source = _base_label(sibling)
        if not label or source in WEAK_LABEL_SOURCES:
            continue
        if sibling.is_actionable and sibling.role != "text":
            continue
        sib_cx, sib_cy = _center(sibling.bounds)
        vertical_overlap = _overlap(handle.bounds[1], handle.bounds[3], sibling.bounds[1], sibling.bounds[3])
        horizontal_overlap = _overlap(handle.bounds[0], handle.bounds[2], sibling.bounds[0], sibling.bounds[2])
        same_row = vertical_overlap >= min(node_h, max(1, sibling.bounds[3] - sibling.bounds[1])) * 0.4
        same_col = horizontal_overlap >= min(node_w, max(1, sibling.bounds[2] - sibling.bounds[0])) * 0.4
        if not same_row and not same_col:
            continue
        distance = abs(node_cx - sib_cx) + abs(node_cy - sib_cy)
        score = 1000.0 - distance
        if same_row:
            score += 180.0
        if same_col:
            score += 100.0
        if sibling.role == "text":
            score += 80.0
        if sibling.package == handle.package:
            score += 40.0
        if not sibling.is_actionable:
            score += 30.0
        if best is None or score > best[0]:
            best = (score, label, f"sibling_{source}")
    if best is None:
        return "", ""
    return best[1], best[2]


def _best_member_label(handle: NodeHandle, member_refs: list[str], refs: dict[str, NodeHandle], *, source_prefix: str) -> tuple[str, str]:
    best: tuple[float, str, str] | None = None
    for member_ref in member_refs:
        if member_ref == handle.ref:
            continue
        member = refs[member_ref]
        label, source = _base_label(member)
        if not label:
            continue
        if source in WEAK_LABEL_SOURCES and not member.has_strong_label:
            continue
        score = _label_candidate_score(handle, member, label, source)
        if best is None or score > best[0]:
            best = (score, label, f"{source_prefix}_{source}")
    if best is None or best[0] <= 0:
        return "", ""
    return best[1], best[2]


def _enrich_structure(refs: dict[str, NodeHandle]) -> None:
    key_to_ref = {node.node_key: ref for ref, node in refs.items() if node.node_key}
    children_by_parent_key: dict[str, list[str]] = defaultdict(list)
    for ref, node in refs.items():
        if node.parent_key:
            children_by_parent_key[node.parent_key].append(ref)

    for parent_key, child_refs in children_by_parent_key.items():
        child_refs.sort(key=lambda ref: (refs[ref].sibling_index, refs[ref].depth, ref))
        parent_ref = key_to_ref.get(parent_key)
        if parent_ref:
            refs[parent_ref].child_refs = list(child_refs)
        for idx, ref in enumerate(child_refs):
            node = refs[ref]
            node.parent_ref = parent_ref
            node.prev_sibling_ref = child_refs[idx - 1] if idx > 0 else None
            node.next_sibling_ref = child_refs[idx + 1] if idx + 1 < len(child_refs) else None

    for node in refs.values():
        if node.container_key:
            node.container_ref = key_to_ref.get(node.container_key)
        if node.section_key:
            node.section_ref = key_to_ref.get(node.section_key)

    members_by_container_ref: dict[str, list[str]] = defaultdict(list)
    members_by_section_ref: dict[str, list[str]] = defaultdict(list)
    for ref, node in refs.items():
        if node.container_ref:
            members_by_container_ref[node.container_ref].append(ref)
        if node.section_ref:
            members_by_section_ref[node.section_ref].append(ref)

    for ref, node in refs.items():
        current_label, current_source = _base_label(node)
        if not current_label or current_source in WEAK_LABEL_SOURCES:
            child_label, child_source = _best_child_label(node, node.child_refs, refs)
            container_label, container_source = _best_member_label(
                node,
                members_by_container_ref.get(ref, []),
                refs,
                source_prefix="container",
            )
            section_label, section_source = _best_member_label(
                node,
                members_by_section_ref.get(ref, []),
                refs,
                source_prefix="section",
            )
            sibling_candidates = []
            if node.parent_ref:
                sibling_candidates = refs[node.parent_ref].child_refs
            sibling_label, sibling_source = _best_sibling_label(node, sibling_candidates, refs)

            preferred_label = current_label
            preferred_source = current_source
            if node.editable or node.checkable or node.role in {"textbox", "checkbox"}:
                if sibling_label:
                    preferred_label, preferred_source = sibling_label, sibling_source
                elif child_label:
                    preferred_label, preferred_source = child_label, child_source
                elif container_label:
                    preferred_label, preferred_source = container_label, container_source
            else:
                if child_label:
                    preferred_label, preferred_source = child_label, child_source
                elif container_label:
                    preferred_label, preferred_source = container_label, container_source
                elif section_label:
                    preferred_label, preferred_source = section_label, section_source
                elif sibling_label:
                    preferred_label, preferred_source = sibling_label, sibling_source

            if preferred_label:
                node.semantic_label = preferred_label
                node.label_source = preferred_source
            elif current_label:
                node.semantic_label = current_label
                node.label_source = current_source
        else:
            node.semantic_label = current_label
            node.label_source = current_source

        if not node.parent_label and node.parent_ref:
            parent = refs[node.parent_ref]
            node.parent_label = parent.primary_label()
            node.parent_role = parent.role
        candidate_refs: list[str] = []
        candidate_refs.extend(node.child_refs)
        if node.parent_ref and node.parent_ref in refs:
            parent = refs[node.parent_ref]
            if not (node.is_actionable and parent.role in {"scrollview", "list", "grid"}):
                candidate_refs.extend(parent.child_refs)
        if ref in members_by_container_ref:
            candidate_refs.extend(members_by_container_ref.get(ref, []))
        if ref in members_by_section_ref:
            candidate_refs.extend(members_by_section_ref.get(ref, []))
        if node.container_ref and node.container_ref in members_by_container_ref:
            container_scope = list(members_by_container_ref.get(node.container_ref, []))
            container_node = refs.get(node.container_ref)
            if container_node and container_node.role in {"scrollview", "list", "grid"}:
                container_scope = [
                    member_ref
                    for member_ref in container_scope
                    if refs[member_ref].parent_ref == node.parent_ref
                ]
            candidate_refs.extend(container_scope)
        if node.section_ref and node.section_ref in members_by_section_ref:
            section_scope = list(members_by_section_ref.get(node.section_ref, []))
            section_node = refs.get(node.section_ref)
            if section_node and section_node.role in {"scrollview", "list", "grid"}:
                section_scope = [
                    member_ref
                    for member_ref in section_scope
                    if refs[member_ref].parent_ref == node.parent_ref
                ]
            candidate_refs.extend(section_scope)
        context_labels, secondary_labels = _collect_related_labels(node, candidate_refs, refs)
        node.context_labels = context_labels
        node.secondary_labels = secondary_labels


def build_snapshot(
    *,
    nodes: list[dict[str, Any]],
    package: str | None,
    activity: str | None,
    mode: str,
    screenshot_path: str | None,
    foreground_package: str | None = None,
    event_seq: int = 0,
    windows_total: int = 0,
    source: str = "unknown",
    warnings: list[str] | None = None,
) -> SnapshotState:
    snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
    refs: dict[str, NodeHandle] = {}
    for idx, node in enumerate(nodes, start=1):
        ref = f"a{idx}"
        refs[ref] = NodeHandle(
            ref=ref,
            role=node.get("role", "text"),
            text=node.get("text", "") or "",
            content_desc=node.get("content_desc", "") or node.get("contentDescription", "") or "",
            hint_text=node.get("hint_text", "") or node.get("hintText", "") or "",
            class_name=node.get("class_name", "") or node.get("className", "") or "",
            resource_id=node.get("resource_id", "") or node.get("view_id_resource_name", "") or "",
            bounds=tuple(node.get("bounds", (0, 0, 0, 0))),
            actions=list(node.get("actions", [])),
            source=node.get("source", source),
            node_key=node.get("node_key"),
            editable=bool(node.get("editable", False)),
            scrollable=bool(node.get("scrollable", False)),
            enabled=bool(node.get("enabled", True)),
            selected=bool(node.get("selected", False)),
            checked=bool(node.get("checked", False)),
            clickable=bool(node.get("clickable", False)),
            long_clickable=bool(node.get("long_clickable", False)),
            checkable=bool(node.get("checkable", False)),
            focusable=bool(node.get("focusable", False)),
            focused=bool(node.get("focused", False)),
            visible=bool(node.get("visible", True)),
            child_count=int(node.get("child_count", 0) or 0),
            package=node.get("package") or package,
            window_rank=int(node.get("window_rank", 50)),
            window_type=node.get("window_type"),
            active_window=bool(node.get("active_window", False)),
            depth=int(node.get("depth", 0) or 0),
            sibling_index=int(node.get("sibling_index", 0) or 0),
            parent_key=node.get("parent_key"),
            semantic_id=node.get("semantic_id", "") or "",
            semantic_label=_normalize_label(node.get("semantic_label", "") or ""),
            label_source=node.get("label_source", "") or "",
            parent_label=_normalize_label(node.get("parent_label", "") or ""),
            parent_role=node.get("parent_role", "") or "",
            container_key=node.get("container_key"),
            container_role=node.get("container_role"),
            container_label=_normalize_label(node.get("container_label", "") or ""),
            section_key=node.get("section_key"),
            section_role=node.get("section_role"),
            section_label=_normalize_label(node.get("section_label", "") or ""),
            path_labels=tuple(_normalize_label(item) for item in node.get("path_labels", []) if _normalize_label(item)),
        )

    _enrich_structure(refs)

    return SnapshotState(
        snapshot_id=snapshot_id,
        package=package,
        activity=activity,
        mode=mode,
        refs=refs,
        created_at=time.time(),
        screenshot_path=screenshot_path,
        foreground_package=foreground_package or package,
        event_seq=event_seq,
        windows_total=windows_total,
        source=source,
        warnings=list(warnings) if warnings else [],
    )
