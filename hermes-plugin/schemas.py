from __future__ import annotations


ANDROID_SCHEMA = {
    "name": "android",
    "description": (
        "Control Android apps through the local Clawdroid Waydroid daemon. "
        "Use task_route for branded service requests, snapshot plus act for "
        "normal UI interaction, and direct open/url/settings actions when the "
        "destination is already known. Re-snapshot after meaningful UI changes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "current_app",
                    "apps_list",
                    "apps_search",
                    "service_resolve",
                    "task_route",
                    "app_installed",
                    "store_search",
                    "app_open",
                    "activity_start",
                    "intent_start",
                    "url_open",
                    "settings_open",
                    "app_details_open",
                    "market_open",
                    "snapshot",
                    "screenshot",
                    "decide_next",
                    "act",
                    "coordinate_act",
                    "wait",
                ],
            },
            "device": {"type": "string"},
            "store": {"type": "string"},
            "package": {"type": "string"},
            "activity": {"type": "string"},
            "query": {"type": "string"},
            "url": {"type": "string"},
            "intent_action": {"type": "string"},
            "data_url": {"type": "string"},
            "mime_type": {"type": "string"},
            "categories": {"type": "array", "items": {"type": "string"}},
            "extras": {"type": "object", "additionalProperties": {"type": "string"}},
            "settings_action": {"type": "string"},
            "stop": {"type": "boolean"},
            "snapshot_mode": {"type": "string", "enum": ["interactive", "full", "hybrid"]},
            "include_screenshot": {"type": "boolean"},
            "goal": {"type": "string"},
            "decision_mode": {
                "type": "string",
                "enum": ["deterministic", "auto", "llm_text", "llm_vision"],
            },
            "auto_execute": {"type": "boolean"},
            "llm_provider": {"type": "string"},
            "llm_model": {"type": "string"},
            "snapshot_id": {"type": "string"},
            "ref": {"type": "string"},
            "op": {
                "type": "string",
                "enum": [
                    "click",
                    "click_center",
                    "long_click",
                    "set_text",
                    "clear_text",
                    "scroll_forward",
                    "scroll_backward",
                    "scroll_to_start",
                    "scroll_to_end",
                    "press_back",
                    "press_home",
                    "press_recents",
                    "press_enter",
                    "tap",
                    "long_press",
                    "swipe",
                    "type_text",
                ],
            },
            "text": {"type": "string"},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "x1": {"type": "integer"},
            "y1": {"type": "integer"},
            "x2": {"type": "integer"},
            "y2": {"type": "integer"},
            "duration_ms": {"type": "integer", "minimum": 0},
            "approved": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            "timeout_ms": {"type": "integer", "minimum": 0},
            "wait_for": {
                "type": "string",
                "enum": ["idle", "package", "activity", "text", "ref_appears", "ref_gone"],
            },
            "wait_value": {"type": "string"},
        },
        "required": ["action"],
    },
}


ANDROID_ADMIN_SCHEMA = {
    "name": "android_admin",
    "description": (
        "Administrative Clawdroid/Waydroid actions for Hermes. Use only when "
        "the user explicitly asks for host or Android package changes. Direct "
        "APK, store install, remove, extras, and bridge allowlist actions require "
        "approved=true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "doctor",
                    "recover",
                    "waydroid_start",
                    "waydroid_stop",
                    "app_install",
                    "app_install_url",
                    "store_install",
                    "app_remove",
                    "default_stores_install",
                    "device_profile_apply",
                    "extras_install",
                    "extras_uninstall",
                    "bridge_configure",
                ],
            },
            "mode": {"type": "string", "enum": ["user", "system"]},
            "store": {"type": "string"},
            "package": {"type": "string"},
            "query": {"type": "string"},
            "apk_path": {"type": "string"},
            "apk_url": {"type": "string"},
            "extras": {"type": "array", "items": {"type": "string"}},
            "stores": {"type": "array", "items": {"type": "string"}},
            "allowed_packages": {"type": "array", "items": {"type": "string"}},
            "approved": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            "timeout_ms": {"type": "integer", "minimum": 0},
        },
        "required": ["action"],
    },
}
