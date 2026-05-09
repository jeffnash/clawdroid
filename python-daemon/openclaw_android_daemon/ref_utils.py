from __future__ import annotations

from typing import Any


def _get(ref: dict[str, Any] | Any, name: str) -> Any:
    if isinstance(ref, dict):
        return ref.get(name)
    return getattr(ref, name, None)


def ref_label(ref: dict[str, Any] | Any | None) -> str:
    if ref is None:
        return ""
    for value in (
        _get(ref, "semantic_label"),
        _get(ref, "text"),
        _get(ref, "content_desc"),
        _get(ref, "hint_text"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    resource_id = resource_leaf(str(_get(ref, "resource_id") or "")).strip()
    if resource_id:
        return resource_id.replace("_", " ")
    return str(_get(ref, "ref") or "").strip()


def resource_leaf(resource_id: str | None) -> str:
    text = str(resource_id or "")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return text


def ref_signature(ref: dict[str, Any] | Any) -> str:
    label = ref_label(ref)
    return "|".join(
        (
            str(_get(ref, "role") or ""),
            label.casefold(),
            str(_get(ref, "resource_id") or ""),
            str(_get(ref, "package") or ""),
            "1" if _get(ref, "selected") else "0",
            "1" if _get(ref, "checked") else "0",
        )
    )


def ref_detail_labels(ref: dict[str, Any] | Any) -> tuple[str, ...]:
    if isinstance(ref, dict):
        values = list(ref.get("context_labels") or [])
        values.extend(list(ref.get("secondary_labels") or []))
    else:
        values = list(getattr(ref, "context_labels", ()) or [])
        values.extend(list(getattr(ref, "secondary_labels", ()) or []))
    labels: list[str] = []
    seen: set[str] = set()
    primary = ref_label(ref).casefold()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.casefold()
        if lowered == primary or lowered in seen:
            continue
        seen.add(lowered)
        labels.append(text)
    return tuple(labels)


def bounds_area(bounds: tuple[int, int, int, int] | list[int] | None) -> int:
    if not bounds or len(bounds) != 4:
        return 0
    try:
        x1, y1, x2, y2 = [int(part) for part in bounds]
    except Exception:
        return 0
    return max(0, x2 - x1) * max(0, y2 - y1)


def bounds_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bounds
    return int((x1 + x2) / 2), int((y1 + y2) / 2)


def swipe_path(bounds: tuple[int, int, int, int], direction: str) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bounds
    mid_x = int((x1 + x2) / 2)
    mid_y = int((y1 + y2) / 2)
    height = max(1, y2 - y1)
    top = y1 + 5
    bottom = y2 - 5
    upper_y = int(y1 + height * 0.2)
    lower_y = int(y1 + height * 0.8)
    if direction == "forward":
        return mid_x, min(bottom, lower_y), mid_x, max(top, upper_y)
    if direction == "backward":
        return mid_x, max(top, upper_y), mid_x, min(bottom, lower_y)
    if direction == "start":
        return mid_x, mid_y, mid_x, y1 + 5
    if direction == "end":
        return mid_x, mid_y, mid_x, y2 - 5
    raise ValueError(f"Unsupported swipe direction: {direction}")
