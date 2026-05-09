#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request
from typing import Any


DEFAULT_DAEMON_URL = "http://127.0.0.1:48765/v1/agent/dispatch"


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 120.0, retries: int = 12) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for _ in range(retries):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.4)
    raise RuntimeError(f"request failed: {last_error}")


def _apply_env(name: str, value: str | None) -> None:
    if value:
        os.environ[name] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the configured LLM to choose the next Android UI action.")
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--snapshot-file")
    parser.add_argument("--snapshot-mode", default="hybrid")
    parser.add_argument("--task", default="Choose the single best next UI action to continue the current Android task.")
    parser.add_argument("--mode", choices=["text", "vision"], default="vision")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--config-path")
    parser.add_argument("--max-refs", type=int, default=None)
    args = parser.parse_args()

    _apply_env("OPENCLAW_ANDROID_LLM_PROVIDER", args.provider)
    _apply_env("OPENCLAW_ANDROID_LLM_MODEL", args.model)
    _apply_env("OPENCLAW_ANDROID_LLM_BASE_URL", args.base_url)
    _apply_env("OPENCLAW_ANDROID_LLM_API_KEY", args.api_key)
    _apply_env("OPENCLAW_ANDROID_LLM_CONFIG_PATH", args.config_path)

    project_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "python-daemon"))

    from openclaw_android_daemon.config import Settings
    from openclaw_android_daemon.llm_decider import AndroidLlmDecider

    if args.snapshot_file:
        snapshot = json.loads(pathlib.Path(args.snapshot_file).read_text())
    else:
        snapshot = _post_json(
            args.daemon_url,
            {
                "action": "snapshot",
                "snapshot_mode": args.snapshot_mode,
                "include_screenshot": args.mode == "vision",
            },
        )

    if not snapshot.get("ok"):
        raise RuntimeError(f"snapshot failed: {snapshot.get('error')}")

    decider = AndroidLlmDecider(Settings())
    result = decider.decide(
        snapshot=snapshot,
        goal=args.task,
        mode=args.mode,
        provider_name=args.provider,
        model_name=args.model,
        max_refs=args.max_refs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
