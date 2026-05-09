from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_DAEMON_BASE_URL = "http://127.0.0.1:48765"


def daemon_base_url() -> str:
    return (
        os.environ.get("CLAWDROID_DAEMON_BASE_URL")
        or os.environ.get("CLAWDROID_DAEMON_URL")
        or os.environ.get("OPENCLAW_ANDROID_DAEMON_BASE_URL")
        or DEFAULT_DAEMON_BASE_URL
    )


def post_json(path: str, payload: dict[str, Any], *, timeout: float = 60.0) -> str:
    url = daemon_base_url().rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        return json.dumps(
            {
                "success": False,
                "error": f"Clawdroid daemon returned HTTP {exc.code}",
                "detail": _decode_json_or_text(raw_error),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "success": False,
                "error": f"Clawdroid daemon is unavailable at {url}: {type(exc).__name__}: {exc}",
            },
            ensure_ascii=False,
        )

    decoded = _decode_json_or_text(raw)
    if isinstance(decoded, str):
        return json.dumps({"success": True, "text": decoded}, ensure_ascii=False)
    return json.dumps(decoded, ensure_ascii=False)


def _decode_json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw
