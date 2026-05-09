from __future__ import annotations

from typing import Any

from .service_catalog import resolve_services


def route_backend_order(browser_backend_policy: str | None, prefer_native_apps: bool) -> tuple[str, ...]:
    policy = str(browser_backend_policy or "").strip().lower()
    if policy.startswith("android_web_then_native"):
        order = ["android_web", "native_app", "desktop_web"]
    elif policy.startswith("desktop_web_only"):
        order = ["desktop_web", "android_web", "native_app"]
    else:
        order = ["native_app", "android_web", "desktop_web"]
    if not prefer_native_apps and "native_app" in order and "android_web" in order:
        native_idx = order.index("native_app")
        web_idx = order.index("android_web")
        if native_idx < web_idx:
            order[native_idx], order[web_idx] = order[web_idx], order[native_idx]
    return tuple(order)


def build_service_install_option(
    *,
    aptoide: Any,
    packages: list[str],
    installed_packages: set[str],
    require_approval_for_install: bool,
) -> dict[str, Any] | None:
    for package in packages:
        if package in installed_packages:
            continue
        try:
            artifact = aptoide.get_meta(package)
        except Exception:
            continue
        if artifact is None:
            continue
        return {
            "available": True,
            "store": "aptoide",
            "package": artifact.package,
            "name": artifact.name,
            "malware_rank": artifact.malware_rank,
            "download_url": artifact.download_url,
            "requires_approval": bool(require_approval_for_install),
            "recommended_action": {
                "tool": "android_admin",
                "action": "store_install",
                "store": "aptoide",
                "package": artifact.package,
                "approved": True,
            },
        }
    return None


def resolve_service_routes(
    query: str,
    *,
    installed_packages: set[str],
    backend_order: tuple[str, ...],
    include_install_option: bool,
    aptoide: Any,
    require_approval_for_install: bool,
) -> tuple[list[dict[str, Any]], str]:
    matches = resolve_services(query)
    resolved: list[dict[str, Any]] = []
    for match in matches:
        candidate = match.candidate
        packages = list(candidate.packages)
        installed = [pkg for pkg in packages if pkg in installed_packages]
        native_package = installed[0] if installed else None
        browser_url = candidate.browser_url()
        install_option = None
        if include_install_option and not native_package:
            install_option = build_service_install_option(
                aptoide=aptoide,
                packages=packages,
                installed_packages=installed_packages,
                require_approval_for_install=require_approval_for_install,
            )

        available: dict[str, dict[str, Any]] = {}
        if native_package:
            available["native_app"] = {
                "tool": "android",
                "action": "app_open",
                "package": native_package,
            }
        if browser_url:
            available["android_web"] = {
                "tool": "android",
                "action": "url_open",
                "url": browser_url,
            }
            available["desktop_web"] = {
                "surface": "desktop_web",
                "url": browser_url,
            }
        else:
            available["desktop_web"] = {
                "surface": "desktop_web",
                "url": None,
            }
        if install_option and "android_web" not in available and "native_app" not in available:
            available["direct_store_install"] = install_option["recommended_action"]

        preferred_backend = "desktop_web"
        recommended_action = available.get("desktop_web")
        for backend in backend_order:
            if backend in available:
                preferred_backend = backend
                recommended_action = available[backend]
                break
        if preferred_backend == "desktop_web" and "direct_store_install" in available and not browser_url:
            preferred_backend = "direct_store_install"
            recommended_action = available["direct_store_install"]

        if preferred_backend == "native_app":
            reason = "Installed native app matches the requested service."
        elif preferred_backend == "android_web":
            reason = "No installed native app matched; the Android web route is available immediately."
        elif preferred_backend == "direct_store_install":
            reason = "No installed app or Android web route matched; a direct store install is available."
        else:
            reason = "No Android-native route matched cleanly; fall back to desktop web if needed."
        if install_option and preferred_backend != "native_app":
            reason += " A direct store install is available if native app behavior is important."

        resolved.append(
            {
                **match.to_dict(),
                "installed_packages": installed,
                "candidate_packages": packages,
                "native_package": native_package,
                "browser_url": browser_url,
                "preferred_backend": preferred_backend,
                "recommended_action": recommended_action,
                "install_option": install_option,
                "can_route_to_android": preferred_backend in {"native_app", "android_web", "direct_store_install"} or install_option is not None,
                "reason": reason,
            }
        )
    chosen_backend = resolved[0]["preferred_backend"] if resolved else "desktop_web"
    return resolved, chosen_backend
