from __future__ import annotations

from pathlib import Path

from .client import post_json
from .schemas import ANDROID_ADMIN_SCHEMA, ANDROID_SCHEMA


def _android(args: dict, **_: object) -> str:
    payload = dict(args or {})
    payload["tool"] = "android"
    return post_json("/v1/agent/dispatch", payload)


def _android_admin(args: dict, **_: object) -> str:
    payload = dict(args or {})
    payload["tool"] = "android_admin"
    return post_json("/v1/admin/dispatch", payload)


def register(ctx) -> None:
    ctx.register_tool(
        name="android",
        toolset="clawdroid",
        schema=ANDROID_SCHEMA,
        handler=_android,
        emoji="A",
    )
    ctx.register_tool(
        name="android_admin",
        toolset="clawdroid_admin",
        schema=ANDROID_ADMIN_SCHEMA,
        handler=_android_admin,
        emoji="!",
    )

    skills_dir = Path(__file__).parent / "skills"
    for skill_name in ("android", "clawdroid"):
        skill_path = skills_dir / skill_name / "SKILL.md"
        if skill_path.exists():
            ctx.register_skill(skill_name, skill_path)
