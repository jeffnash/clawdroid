from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def which(name: str) -> str | None:
    return shutil.which(name)


def run_cmd(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = 30.0,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=check,
        capture_output=capture,
        timeout=timeout,
        text=text,
    )


def try_cmd(args: list[str], timeout: float | None = 30.0) -> tuple[bool, str]:
    try:
        proc = run_cmd(args, check=True, timeout=timeout)
        return True, (proc.stdout or proc.stderr or "").strip()
    except Exception as exc:
        return False, str(exc)


def now_ms() -> int:
    return int(time.time() * 1000)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return value


_bounds_re = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_bounds(value: str | None) -> tuple[int, int, int, int]:
    if not value:
        return (0, 0, 0, 0)
    match = _bounds_re.search(value)
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def role_for(class_name: str, editable: bool, scrollable: bool, clickable: bool) -> str:
    name = (class_name or "").lower()
    if editable or "edittext" in name:
        return "textbox"
    if scrollable:
        return "scrollview"
    if "button" in name:
        return "button"
    if "image" in name and clickable:
        return "button"
    if "checkbox" in name or "switch" in name:
        return "checkbox"
    if clickable:
        return "button"
    return "text"
