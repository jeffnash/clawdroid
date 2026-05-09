#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import json5


def locate_config() -> Path:
    try:
        out = subprocess.run(
            ["openclaw", "config", "file"],
            check=True,
            capture_output=True,
            text=True,
        )
        path = out.stdout.strip()
        if path:
            return Path(path).expanduser()
    except Exception:
        pass
    if os.environ.get("OPENCLAW_HOME"):
        return Path(os.environ["OPENCLAW_HOME"]).expanduser() / "openclaw.json"
    return Path.home() / ".openclaw" / "openclaw.json"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    return json5.loads(raw)


def save_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ensure_list(value) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--daemon-base-url", required=True)
    parser.add_argument("--adb-serial", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        help="OpenClaw config path to update. Defaults to `openclaw config file` or ~/.openclaw/openclaw.json.",
    )
    parser.add_argument("--enable-admin-tool", action="store_true")
    args = parser.parse_args()

    path = args.config.expanduser() if args.config else locate_config()
    cfg = load_config(path)

    cfg.setdefault("plugins", {})
    cfg["plugins"].setdefault("entries", {})
    cfg["plugins"]["entries"].setdefault(args.plugin_id, {})
    entry = cfg["plugins"]["entries"][args.plugin_id]
    entry["enabled"] = True
    entry["config"] = {
        "daemonBaseUrl": args.daemon_base_url,
        "adbSerial": args.adb_serial,
        "defaultDevice": "default",
        "allowHostControl": True,
        "allowAgentAppOpen": True,
        "allowAgentAppsList": True,
        "allowAgentScreenshots": True,
        "allowAgentInstall": False,
        "requireApprovalForInstall": True,
        "requireApprovalForProtectedActions": True,
    }

    cfg.setdefault("tools", {})
    allow = set(ensure_list(cfg["tools"].get("allow")))
    allow.add("android")
    if args.enable_admin_tool:
        allow.add("android_admin")
    cfg["tools"]["allow"] = sorted(allow)

    save_config(path, cfg)
    print(path)


if __name__ == "__main__":
    main()
