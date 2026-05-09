from __future__ import annotations

from dataclasses import dataclass, field, asdict
import re
from typing import Any


# System-noise packages that should be de-prioritized unless they are the foreground app.
SYSTEM_NOISE_PACKAGES = frozenset((
    "com.android.launcher",
    "com.android.launcher3",
    "com.android.launcher2",
    "com.android.systemui",
    "com.google.android.apps.nexuslauncher",
    "com.google.android.launcher",
    "org.lineageos.jelly",
    "com.miui.home",
    "com.huawei.android.launcher",
    "com.samsung.android.launcher",
))

SYSTEM_UI_PACKAGES = frozenset((
    "com.android.systemui",
))

CONTAINER_ROLES = frozenset((
    "scrollview",
    "list",
    "grid",
    "group",
    "drawer",
    "pager",
    "tabs",
    "toolbar",
))

DIRECT_CONTROL_ROLES = frozenset((
    "button",
    "checkbox",
    "textbox",
))


def _is_noise_package(pkg: str | None) -> bool:
    if not pkg:
        return False
    return any(pkg.startswith(n) for n in SYSTEM_NOISE_PACKAGES)


def _is_system_ui_package(pkg: str | None) -> bool:
    if not pkg:
        return False
    return any(pkg.startswith(n) for n in SYSTEM_UI_PACKAGES)


def _is_launcher_package(pkg: str | None) -> bool:
    if not pkg:
        return False
    return pkg.startswith(
        (
            "com.android.launcher",
            "com.google.android.apps.nexuslauncher",
            "com.google.android.launcher",
            "org.lineageos.jelly",
            "com.miui.home",
            "com.huawei.android.launcher",
            "com.samsung.android.launcher",
        )
    )


def _humanize_token(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass(slots=True)
class NodeHandle:
    ref: str
    role: str
    text: str
    content_desc: str
    hint_text: str
    class_name: str
    resource_id: str
    bounds: tuple[int, int, int, int]
    actions: list[str]
    source: str
    node_key: str | None = None
    editable: bool = False
    scrollable: bool = False
    enabled: bool = True
    selected: bool = False
    checked: bool = False
    clickable: bool = False
    long_clickable: bool = False
    checkable: bool = False
    focusable: bool = False
    focused: bool = False
    visible: bool = True
    child_count: int = 0
    package: str | None = None
    # Window rank from the bridge: lower = more foreground.
    # 0=active, 10=foreground pkg, 20=launcher (active launcher only), 30=noise, 40=other.
    window_rank: int = 50
    window_type: str | None = None
    active_window: bool = False
    depth: int = 0
    sibling_index: int = 0
    parent_key: str | None = None
    parent_ref: str | None = None
    child_refs: list[str] = field(default_factory=list)
    prev_sibling_ref: str | None = None
    next_sibling_ref: str | None = None
    semantic_id: str = ""
    semantic_label: str = ""
    label_source: str = ""
    parent_label: str = ""
    parent_role: str = ""
    container_key: str | None = None
    container_ref: str | None = None
    container_role: str | None = None
    container_label: str = ""
    section_key: str | None = None
    section_ref: str | None = None
    section_role: str | None = None
    section_label: str = ""
    path_labels: tuple[str, ...] = ()
    context_labels: tuple[str, ...] = ()
    secondary_labels: tuple[str, ...] = ()

    def to_dict(self, foreground_package: str | None = None) -> dict[str, Any]:
        # Include both declared fields (via asdict) and runtime-computed fields.
        d = asdict(self)
        contextual_noise = self.is_noise and self.package != foreground_package
        d["is_noise"] = self.is_noise
        d["is_contextual_noise"] = contextual_noise
        d["is_actionable"] = self.is_actionable
        d["is_container"] = self.is_container
        d["is_direct_control"] = self.is_direct_control
        d["has_semantic_label"] = self.has_semantic_label
        d["primary_label"] = self.primary_label()
        d["is_foreground_package"] = bool(foreground_package and self.package == foreground_package)
        d["confidence_score"] = self.confidence_score(foreground_package)
        return d

    def primary_label(self) -> str:
        for value in (
            self.semantic_label,
            self.text,
            self.content_desc,
            self.hint_text,
            _humanize_token(self.resource_id.rsplit("/", 1)[-1]) if self.resource_id else "",
            _humanize_token(self.class_name.rsplit(".", 1)[-1]) if self.class_name else "",
            self.role,
        ):
            if value:
                return value
        return self.ref

    def detail_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        seen: set[str] = set()
        primary = self.primary_label().casefold()
        for value in (*self.context_labels, *self.secondary_labels):
            text = str(value or "").strip()
            if not text:
                continue
            lowered = text.casefold()
            if lowered == primary or lowered in seen:
                continue
            seen.add(lowered)
            labels.append(text)
        return tuple(labels)

    @property
    def is_noise(self) -> bool:
        """True if this node belongs to a known launcher/SystemUI package."""
        return _is_noise_package(self.package)

    @property
    def is_actionable(self) -> bool:
        """True if this node supports any meaningful interaction."""
        return bool(self.actions) and self.enabled and self.visible

    @property
    def is_container(self) -> bool:
        if self.role in CONTAINER_ROLES:
            return True
        return bool(self.scrollable and self.child_count > 0)

    @property
    def is_direct_control(self) -> bool:
        if not self.is_actionable:
            return False
        if self.role in DIRECT_CONTROL_ROLES:
            return True
        if self.editable or self.checkable:
            return True
        return bool(self.clickable and not self.is_container)

    @property
    def has_semantic_label(self) -> bool:
        """True if the node carries a meaningful text/content-desc label."""
        return bool(self.semantic_label or self.text or self.content_desc or self.hint_text)

    @property
    def has_strong_label(self) -> bool:
        return self.label_source not in {"", "resource_id", "class_name", "role"} and self.has_semantic_label

    def sort_key(self, foreground_package: str | None) -> tuple:
        """
        Sort key for the agent's ref list. Priority (highest first):

        RULE 1: Labeled ALWAYS beats unlabeled, regardless of package or actionability.
                 A labeled text item can be clicked by text match; an unlabeled button cannot.

        RULE 2: Among labeled items:
                 - Foreground beats overlay
                 - Actionable beats non-actionable
                 - Lower window_rank wins

        RULE 3: Among unlabeled items:
                 - Foreground beats overlay
                 - Actionable beats non-actionable
                 - Resource ID beats no-ID
        """
        has_label = int(self.has_strong_label)
        has_any_label = int(self.has_semantic_label)
        is_actionable = int(self.is_actionable)
        is_direct_control = int(self.is_direct_control)
        is_container = int(self.is_container)
        is_foreground = int(bool(foreground_package and self.package == foreground_package))
        is_noise = int(self.is_noise and self.package != foreground_package)
        has_rid = int(bool(self.resource_id and "/" in self.resource_id))
        win_score = max(0, 50 - self.window_rank)
        is_active_window = int(self.active_window)

        return (
            # Tier 1: labeled always beats unlabeled
            has_label,
            has_any_label,
            # Tier 2: among labeled, foreground beats overlay
            is_foreground if has_label else (is_foreground if not is_noise else 0),
            # Tier 3: direct controls beat generic containers.
            is_direct_control,
            0 if is_container else 1,
            # Tier 4: actionable beats non-actionable
            is_actionable,
            # Tier 5: active window beats sibling/overlay window
            is_active_window,
            # Tier 6: no noise beats noise
            0 if is_noise else 1,
            # Tier 7: has resource ID
            has_rid,
            # Tier 8: window rank
            win_score,
            # Tier 9: precomputed confidence
            self.confidence_score(foreground_package),
        )

    def confidence_score(self, foreground_package: str | None) -> float:
        """
        Score 0.0–1.0 that DIRECTLY mirrors the sort_key priority:
        labeled + actionable + foreground > labeled + foreground > labeled > actionable > unlabeled.

        This ensures the displayed confidence always matches the ref ordering.
        """
        has_label = self.has_strong_label
        has_any_label = self.has_semantic_label
        is_actionable = self.is_actionable
        is_direct_control = self.is_direct_control
        is_container = self.is_container
        is_fg = bool(foreground_package and self.package == foreground_package)
        is_foreground_noise = self.is_noise and self.package != foreground_package

        if not self.enabled or not self.visible:
            return 0.0

        # Tier 1: labeled, actionable, foreground — the gold standard.
        if has_label and is_actionable and is_fg and is_direct_control:
            return 1.0 if self.active_window else 0.98
        if has_label and is_actionable and is_fg and is_container:
            return 0.86
        # Tier 2: labeled, actionable, not foreground — very reliable.
        if has_label and is_actionable and not is_fg and not is_foreground_noise and is_direct_control:
            return 0.95
        if has_label and is_actionable and not is_fg and not is_foreground_noise and is_container:
            return 0.80
        # Tier 3: labeled, foreground — good but not interactive.
        if has_label and is_fg:
            return 0.90 if not is_container else 0.76
        # Tier 4: labeled, actionable, foreign noise — labeled but from overlay.
        if has_label and is_actionable and is_foreground_noise:
            return 0.82 if is_direct_control else 0.60
        # Tier 5: labeled, not foreground, not noise — usable.
        if has_label:
            return 0.78 if not is_container else 0.64
        # Tier 5.5: weakly labeled via resource/class fallback.
        if has_any_label and is_fg and is_actionable:
            return 0.72 if not is_container else 0.58
        if has_any_label and is_fg:
            return 0.62 if not is_container else 0.52
        if has_any_label:
            return 0.48 if not is_container else 0.38
        # Tier 6: actionable, not labeled, foreground — icon button in the app.
        if is_actionable and is_fg:
            return 0.65 if not is_container else 0.44
        # Tier 7: actionable, not labeled, not noise — icon in overlay.
        if is_actionable and not is_foreground_noise:
            return 0.55 if not is_container else 0.34
        # Tier 8: labeled but from noise package.
        if has_label and is_foreground_noise:
            return 0.45 if not is_container else 0.28
        # Tier 9: actionable, foreign noise — low value overlay.
        if is_actionable and is_foreground_noise:
            return 0.25 if not is_container else 0.16
        # Tier 10: everything else — unlabeled, non-actionable, noise.
        return 0.12

    def summary_line(self, foreground_package: str | None = None) -> str:
        parts = [self.ref, f"[{self.role}]"]
        label = self.primary_label()
        if label:
            parts.append(repr(label))
        if self.actions:
            parts.append(f"actions={','.join(self.actions)}")
        if self.resource_id:
            parts.append(f"id={self.resource_id}")
        if self.content_desc and self.content_desc != label:
            parts.append(f"desc={self.content_desc!r}")
        if self.hint_text and self.hint_text != label:
            parts.append(f"hint={self.hint_text!r}")
        if self.checked:
            parts.append("checked=true")
        if self.selected:
            parts.append("selected=true")
        if self.focused:
            parts.append("focused=true")
        if self.label_source and self.label_source not in {"text", "content_desc", "hint_text"}:
            parts.append(f"via={self.label_source}")
        if self.package == foreground_package:
            parts.append("FG")
        elif self.package:
            parts.append(f"pkg={self.package}")
        if self.parent_ref:
            parts.append(f"parent={self.parent_ref}")
        if self.is_direct_control:
            parts.append("CONTROL")
        elif self.is_container:
            parts.append("CONTAINER")
        if self.container_role:
            if self.container_label:
                parts.append(f"in={self.container_role}:{self.container_label!r}")
            else:
                parts.append(f"in={self.container_role}")
        elif self.section_label:
            parts.append(f"section={self.section_label!r}")
        detail_labels = self.detail_labels()
        if detail_labels:
            parts.append(f"details={' | '.join(repr(label) for label in detail_labels[:4])}")
        if self.is_noise and self.package != foreground_package:
            parts.append("OVERLAY")
        conf = self.confidence_score(foreground_package)
        if conf < 0.5:
            parts.append(f"conf={conf:.2f}⚠")
        elif conf >= 0.85:
            parts.append(f"conf={conf:.2f}✓")
        return " ".join(parts)


@dataclass(slots=True)
class SnapshotState:
    snapshot_id: str
    package: str | None
    activity: str | None
    mode: str
    refs: dict[str, NodeHandle] = field(default_factory=dict)
    created_at: float = 0.0
    screenshot_path: str | None = None
    foreground_package: str | None = None
    event_seq: int = 0
    windows_total: int = 0
    warnings: list[str] = field(default_factory=list)
    source: str = "unknown"

    def _sorted_items(self) -> list[tuple[str, NodeHandle]]:
        return sorted(
            self.refs.items(),
            key=lambda item: item[1].sort_key(self.foreground_package),
            reverse=True,
        )

    def flattened(self) -> list[dict[str, Any]]:
        return [node.to_dict(self.foreground_package) for _, node in self._sorted_items()]

    def summary_lines(self) -> list[str]:
        return [node.summary_line(self.foreground_package) for _, node in self._sorted_items()]

    def ref_for_label(self, label: str, must_be_actionable: bool = True) -> str | None:
        """Find the best ref matching a label (text, content_desc, hint, or resource_id)."""
        needle = label.lower()
        candidates: list[tuple[float, str, NodeHandle]] = []
        for ref, node in self.refs.items():
            if must_be_actionable and not node.is_actionable:
                continue
            if node.primary_label().lower() == needle:
                candidates.append((node.confidence_score(self.foreground_package), ref, node))
            elif needle in node.primary_label().lower():
                candidates.append((node.confidence_score(self.foreground_package) * 0.9, ref, node))
        if not candidates:
            # Fall back: scan all fields
            for ref, node in self.refs.items():
                if must_be_actionable and not node.is_actionable:
                    continue
                for field_val in (node.text, node.content_desc, node.hint_text, node.resource_id):
                    if field_val and needle in field_val.lower():
                        candidates.append((node.confidence_score(self.foreground_package) * 0.7, ref, node))
                        break
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], *x[2].sort_key(self.foreground_package)), reverse=True)
        return candidates[0][1]


@dataclass(slots=True)
class AppEntry:
    package: str
    label: str | None = None
    version_name: str | None = None
    version_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
