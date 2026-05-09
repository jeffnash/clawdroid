from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _xdg_dir(env_name: str, fallback: Path) -> Path:
    return Path(os.environ.get(env_name, str(fallback)))


def _xdg_cache_home() -> Path:
    return _xdg_dir("XDG_CACHE_HOME", Path.home() / ".cache")


def _xdg_config_home() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config")


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value not in {"0", "false", "False"}


@dataclass(slots=True)
class Settings:
    host: str = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_DAEMON_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("OPENCLAW_ANDROID_DAEMON_PORT", "48765")))
    adb_serial: str | None = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_ADB_SERIAL") or None)
    bridge_port: int = field(default_factory=lambda: int(os.environ.get("OPENCLAW_ANDROID_BRIDGE_PORT", "49317")))
    bridge_url: str | None = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_BRIDGE_URL") or None)
    screenshot_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "OPENCLAW_ANDROID_SCREENSHOT_DIR",
                str(_xdg_cache_home() / "openclaw-android-waydroid" / "screenshots"),
            )
        )
    )
    download_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "OPENCLAW_ANDROID_DOWNLOAD_DIR",
                str(_xdg_cache_home() / "openclaw-android-waydroid" / "downloads"),
            )
        )
    )
    allowed_packages: list[str] = field(default_factory=lambda: _split_csv(os.environ.get("OPENCLAW_ANDROID_ALLOWED_PACKAGES")))
    require_approval_for_install: bool = field(
        default_factory=lambda: _env_bool("OPENCLAW_ANDROID_REQUIRE_APPROVAL_FOR_INSTALL", True)
    )
    require_approval_for_protected_actions: bool = field(
        default_factory=lambda: _env_bool("OPENCLAW_ANDROID_REQUIRE_APPROVAL_FOR_PROTECTED_ACTIONS", True)
    )
    prefer_native_apps: bool = field(default_factory=lambda: _env_bool("OPENCLAW_ANDROID_PREFER_NATIVE_APPS", True))
    default_stores: list[str] = field(
        default_factory=lambda: _split_csv(os.environ.get("OPENCLAW_ANDROID_DEFAULT_STORES", "f-droid,aurora-store,aptoide"))
    )
    browser_backend_policy: str = field(
        default_factory=lambda: os.environ.get(
            "OPENCLAW_ANDROID_BROWSER_BACKEND_POLICY",
            "native_then_android_web_then_desktop",
        )
    )
    device_profile: str = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_DEVICE_PROFILE", "samsung-galaxy-s24-ultra"))
    llm_provider: str | None = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_LLM_PROVIDER") or None)
    llm_base_url: str | None = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_LLM_BASE_URL") or None)
    llm_api_key: str | None = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_LLM_API_KEY") or None)
    llm_model: str | None = field(default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_LLM_MODEL") or None)
    llm_config_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "OPENCLAW_ANDROID_LLM_CONFIG_PATH",
                str(_xdg_config_home() / "openclaw-android-waydroid" / "llm.json"),
            )
        )
    )
    llm_models_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "OPENCLAW_ANDROID_LLM_MODELS_PATH",
                str(_xdg_config_home() / "openclaw-android-waydroid" / "models.json"),
            )
        )
    )
    llm_settings_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "OPENCLAW_ANDROID_LLM_SETTINGS_PATH",
                str(_xdg_config_home() / "openclaw-android-waydroid" / "settings.json"),
            )
        )
    )
    llm_timeout_s: float = field(default_factory=lambda: float(os.environ.get("OPENCLAW_ANDROID_LLM_TIMEOUT_S", "45")))
    llm_decision_mode_default: str = field(
        default_factory=lambda: os.environ.get("OPENCLAW_ANDROID_LLM_DECISION_MODE_DEFAULT", "auto")
    )
    llm_max_refs: int = field(default_factory=lambda: int(os.environ.get("OPENCLAW_ANDROID_LLM_MAX_REFS", "20")))
    aptoide_meta_url: str = field(
        default_factory=lambda: os.environ.get(
            "OPENCLAW_ANDROID_APTOIDE_META_URL",
            "https://ws2.aptoide.com/api/7/app/getMeta/package_name=",
        )
    )
    aptoide_search_url: str = field(
        default_factory=lambda: os.environ.get(
            "OPENCLAW_ANDROID_APTOIDE_SEARCH_URL",
            "https://ws2.aptoide.com/api/7/apps/search",
        )
    )
    aptoide_timeout_s: float = field(default_factory=lambda: float(os.environ.get("OPENCLAW_ANDROID_APTOIDE_TIMEOUT_S", "20")))
    protected_texts: tuple[str, ...] = (
        "place your order",
        "buy now",
        "submit order",
        "continue to payment",
        "confirm purchase",
        "sign in",
        "verify",
    )

    def __post_init__(self) -> None:
        for field_name in (
            "screenshot_dir",
            "download_dir",
            "llm_config_path",
            "llm_models_path",
            "llm_settings_path",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Path):
                setattr(self, field_name, Path(value))
