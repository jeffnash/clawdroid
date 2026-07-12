from __future__ import annotations

from collections import OrderedDict
import re
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .aptoide import AptoideClient
from .bridge import BridgeClient
from .config import Settings
from . import action_verify, decision, ref_utils, routing, screen_context as screen_context_utils, store as store_utils, targets
from .llm_decider import AndroidLlmDecider, LlmDecisionError
from .models import AppEntry, SnapshotState
from .snapshot import build_snapshot
from .utils import ensure_dir, now_ms, which
from .waydroid import WaydroidManager


__all__ = ["AndroidRuntime", "LlmDecisionError"]


class AndroidRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.waydroid = WaydroidManager(adb_serial=settings.adb_serial)
        self.aptoide = AptoideClient(
            meta_base_url=settings.aptoide_meta_url,
            search_url=settings.aptoide_search_url,
            timeout_s=settings.aptoide_timeout_s,
        )
        self.llm_decider = AndroidLlmDecider(settings)
        self._bridge: BridgeClient | None = None
        self._last_snapshot: SnapshotState | None = None
        self._snapshot_history: OrderedDict[str, SnapshotState] = OrderedDict()
        ensure_dir(self.settings.screenshot_dir)
        ensure_dir(self.settings.download_dir)

    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _ensure_admin_approved(self, approved: bool, message: str) -> dict[str, Any] | None:
        if approved:
            return None
        return {"ok": False, "error": message}

    def _adb_serial(self) -> str | None:
        serial = self.waydroid.adb_serial()
        if serial:
            return serial
        result = self.waydroid.ensure_adb_connected()
        if result.get("ok"):
            return result.get("serial")
        return None

    @staticmethod
    def _uiautomator2_disabled_reason() -> str:
        return (
            "uiautomator2 runtime fallback is disabled because it unbinds the "
            "OpenClaw AccessibilityService on this Waydroid/LineageOS image."
        )

    @staticmethod
    def _bounds_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
        return ref_utils.bounds_center(bounds)

    @staticmethod
    def _swipe_path(bounds: tuple[int, int, int, int], direction: str) -> tuple[int, int, int, int]:
        return ref_utils.swipe_path(bounds, direction)

    def _adb_clear_focused_text(self, max_chars: int = 64) -> dict[str, Any]:
        move_end = self.waydroid.press_key("KEYCODE_MOVE_END")
        cleared = 0
        for _ in range(max_chars):
            step = self.waydroid.press_key("KEYCODE_DEL")
            if not step.get("ok"):
                return {"ok": False, "move_end": move_end, "cleared": cleared, "error": step.get("stderr") or step.get("error")}
            cleared += 1
        return {"ok": True, "move_end": move_end, "cleared": cleared}

    @staticmethod
    def _extract_activity_record(text: str, prefixes: tuple[str, ...]) -> dict[str, Any] | None:
        pattern = re.compile(r"ActivityRecord\{[^\s]+\s+u\d+\s+([^/\s]+)/([^\}\s]+)")
        window_pattern = re.compile(r"Window\{[^\s]+\s+[^\s]+\s+([^/\s]+)/([^\}\s]+)")
        for line in text.splitlines():
            stripped = line.strip()
            if prefixes and not any(prefix in stripped for prefix in prefixes):
                continue
            match = pattern.search(stripped) or window_pattern.search(stripped)
            if match:
                return {"package": match.group(1), "activity": match.group(2)}
        return None

    def _current_app_via_adb(self) -> dict[str, Any]:
        candidates = (
            (["dumpsys", "activity", "activities"], ("mFocusedApp=", "topResumedActivity=", "ResumedActivity:", "mResumedActivity:")),
            (["dumpsys", "window", "windows"], ("mCurrentFocus=", "mFocusedApp=")),
            (["dumpsys", "activity", "top"], ("ACTIVITY ",)),
        )
        fallback: dict[str, Any] | None = None
        for args, prefixes in candidates:
            result = self.waydroid.adb_shell(args, timeout=15.0)
            if not result.get("ok"):
                continue
            text = "\n".join(
                part for part in (result.get("stdout"), result.get("stderr")) if isinstance(part, str) and part
            )
            if not text:
                continue
            parsed = self._extract_activity_record(text, prefixes)
            if parsed:
                return parsed
            if "ACTIVITY " in text:
                for line in text.splitlines():
                    stripped = line.strip()
                    if "ACTIVITY " not in stripped:
                        continue
                    fragment = stripped.split("ACTIVITY ", 1)[1].split(" ", 1)[0]
                    if "/" not in fragment:
                        continue
                    package, activity = fragment.split("/", 1)
                    fallback = {"package": package, "activity": activity}
                    break
        if fallback:
            return fallback
        return {"package": None, "activity": None}

    @property
    def bridge(self) -> BridgeClient | None:
        if self._bridge is not None:
            return self._bridge
        base = self.settings.bridge_url or f"http://127.0.0.1:{self.settings.bridge_port}"
        serial = self._adb_serial()
        if not serial:
            return None
        self._bridge = BridgeClient(serial=serial, base_url=base)
        return self._bridge

    def _invalidate_bridge(self) -> None:
        """Force bridge reconnection on next use (e.g. after guest restart)."""
        self._bridge = None

    def _clear_snapshot_state(self) -> None:
        self._last_snapshot = None
        self._snapshot_history.clear()

    def _invalidate_navigation_state(self) -> None:
        self._invalidate_bridge()
        self._clear_snapshot_state()

    def _remember_snapshot(self, snapshot: SnapshotState) -> None:
        self._last_snapshot = snapshot
        self._snapshot_history[snapshot.snapshot_id] = snapshot
        self._snapshot_history.move_to_end(snapshot.snapshot_id)
        while len(self._snapshot_history) > 8:
            self._snapshot_history.popitem(last=False)

    def ensure_bridge_ready(self) -> dict[str, Any]:
        status = self.waydroid.status()
        adb = self.waydroid.ensure_adb_connected()
        if not adb.get("ok"):
            return {"ok": False, "error": adb.get("error") or adb.get("output"), "waydroid": status.to_dict()}
        self.waydroid.forward_bridge(self.settings.bridge_port)
        self.waydroid.ensure_screen_ready()
        return {"ok": True, "serial": adb.get("serial"), "waydroid": status.to_dict()}

    def doctor(self) -> dict[str, Any]:
        status = self.waydroid.status()
        bridge_ok = False
        bridge_info: dict[str, Any] | None = None
        try:
            self.waydroid.forward_bridge(self.settings.bridge_port)
            bridge_client = self.bridge
            bridge_info = bridge_client.health() if bridge_client else {"ok": False, "error": "No ADB serial available"}
            bridge_ok = bool(bridge_info and bridge_info.get("ok"))
        except Exception as exc:
            bridge_info = {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "binaries": {
                "adb": which("adb"),
                "waydroid": which("waydroid"),
                "python": which("python3"),
            },
            "waydroid": status.to_dict(),
            "bridge": bridge_info,
            "bridge_ok": bridge_ok,
            "runtime_backend": "accessibility_bridge+adb",
            "uiautomator2": {
                "ok": False,
                "enabled": False,
                "reason": self._uiautomator2_disabled_reason(),
            },
            "uiautomator2_ok": False,
        }

    def recover(self, mode: str = "user", approved: bool = False) -> dict[str, Any]:
        mode = (mode or "user").strip().lower()
        if mode in {"user", "runtime", "soft"}:
            result = self.waydroid.recover_user_runtime()
        elif mode in {"system", "full", "sudo"}:
            blocked = self._ensure_admin_approved(
                approved,
                "recover mode=system requires approved=true because it runs the sudo restart helper.",
            )
            if blocked:
                return blocked
            result = self.waydroid.recover_system_runtime()
        else:
            return {"ok": False, "error": "mode must be one of: user, system"}

        self._invalidate_navigation_state()
        result["last_snapshot_cleared"] = True
        return result

    def status(self) -> dict[str, Any]:
        status = self.waydroid.status()
        bridge = None
        try:
            self.waydroid.forward_bridge(self.settings.bridge_port)
            bridge_client = self.bridge
            bridge = bridge_client.health() if bridge_client else {"ok": False, "error": "No ADB serial available"}
        except Exception as exc:
            bridge = {"ok": False, "error": str(exc)}
        current_app = None
        try:
            current_app = self._current_app_via_adb()
        except Exception as exc:
            current_app = {"package": None, "activity": None, "error": str(exc)}
        return {
            "ok": True,
            "waydroid": status.to_dict(),
            "bridge": bridge,
            "current_app": current_app,
            "llm": self.llm_decider.config_status(),
            "runtime_backend": "accessibility_bridge+adb",
            "last_snapshot_id": self._last_snapshot.snapshot_id if self._last_snapshot else None,
        }

    def apps_list(self) -> dict[str, Any]:
        packages_result = self.waydroid.list_packages()
        if not packages_result.get("ok"):
            return {"ok": False, "error": packages_result.get("error") or packages_result.get("stderr") or "Failed to list packages"}
        packages = packages_result.get("packages", [])
        apps: list[dict[str, Any]] = []
        for package in packages:
            entry = AppEntry(
                package=package,
                label=None,
                version_name=None,
                version_code=None,
            )
            apps.append(entry.to_dict())
        return {"ok": True, "apps": apps}

    def apps_search(self, query: str) -> dict[str, Any]:
        needle = (query or "").strip().lower()
        if not needle:
            return {"ok": False, "error": "query is required for apps_search"}
        apps = self.apps_list()
        if not apps.get("ok"):
            return apps
        matches = []
        for app in apps.get("apps", []):
            package = (app.get("package") or "").lower()
            label = (app.get("label") or "").lower()
            if needle in package or needle in label:
                matches.append(app)
        return {"ok": True, "query": query, "matches": matches}

    def _installed_package_set(self) -> set[str]:
        apps = self.apps_list()
        if not apps.get("ok"):
            return set()
        return {app.get("package") for app in apps.get("apps", []) if app.get("package")}

    def _route_backend_order(self) -> tuple[str, ...]:
        return routing.route_backend_order(
            self.settings.browser_backend_policy,
            self.settings.prefer_native_apps,
        )

    def _build_service_install_option(
        self,
        *,
        packages: list[str],
        installed_packages: set[str],
    ) -> dict[str, Any] | None:
        return routing.build_service_install_option(
            aptoide=self.aptoide,
            packages=packages,
            installed_packages=installed_packages,
            require_approval_for_install=self.settings.require_approval_for_install,
        )

    def _resolve_service_routes(self, query: str, *, include_install_option: bool) -> tuple[list[dict[str, Any]], str]:
        return routing.resolve_service_routes(
            query,
            installed_packages=self._installed_package_set(),
            backend_order=self._route_backend_order(),
            include_install_option=include_install_option,
            aptoide=self.aptoide,
            require_approval_for_install=self.settings.require_approval_for_install,
        )

    def service_resolve(self, query: str) -> dict[str, Any]:
        resolved, chosen_backend = self._resolve_service_routes(query, include_install_option=False)
        if not resolved:
            return {
                "ok": True,
                "action": "service_resolve",
                "query": query,
                "matches": [],
                "preferred_backend": "desktop_web",
                "reason": "No known native-app mapping found.",
            }
        return {
            "ok": True,
            "action": "service_resolve",
            "query": query,
            "matches": resolved,
            "preferred_backend": chosen_backend,
            "policy": self.settings.browser_backend_policy,
        }

    def task_route(self, goal: str) -> dict[str, Any]:
        if not goal:
            return {"ok": False, "error": "goal is required for task_route"}
        resolved, chosen_backend = self._resolve_service_routes(goal, include_install_option=True)
        if not resolved:
            return {
                "ok": True,
                "action": "task_route",
                "goal": goal,
                "matches": [],
                "selected_match": None,
                "preferred_backend": "desktop_web",
                "policy": self.settings.browser_backend_policy,
                "prefer_native_apps": self.settings.prefer_native_apps,
                "can_route_to_android": False,
                "reason": "No supported service mapping matched the request text.",
            }
        selected = resolved[0]
        return {
            "ok": True,
            "action": "task_route",
            "goal": goal,
            "service": selected.get("service"),
            "selected_match": selected,
            "matches": resolved,
            "preferred_backend": chosen_backend,
            "policy": self.settings.browser_backend_policy,
            "prefer_native_apps": self.settings.prefer_native_apps,
            "can_route_to_android": any(bool(item.get("can_route_to_android")) for item in resolved),
            "reason": selected.get("reason"),
        }

    @staticmethod
    def _file_md5(path: Path) -> str:
        return store_utils.file_md5(path)

    def _download_store_artifact(self, artifact) -> dict[str, Any]:
        return store_utils.download_store_artifact(artifact, self.settings.download_dir)

    @classmethod
    def _store_query_score(cls, query: str, item: dict[str, Any]) -> int:
        return store_utils.store_query_score(query, item)

    def store_search(self, query: str, store: str = "aptoide", limit: int = 10) -> dict[str, Any]:
        if not query:
            return {"ok": False, "error": "query is required for store_search"}
        store_name = str(store or "aptoide").strip().lower()
        if store_name != "aptoide":
            return {"ok": False, "error": f"Unsupported store: {store}"}
        artifacts = self.aptoide.search(query, limit=limit)
        results = store_utils.enrich_store_results(query, artifacts, limit)
        return {
            "ok": True,
            "action": "store_search",
            "store": store_name,
            "query": query,
            "results": results,
        }

    def store_install(
        self,
        *,
        store: str = "aptoide",
        package: str | None = None,
        query: str | None = None,
        limit: int = 10,
        approved: bool = False,
    ) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(approved, "store_install requires approved=true by policy")
            if blocked:
                return blocked
        store_name = str(store or "aptoide").strip().lower()
        if store_name != "aptoide":
            return {"ok": False, "error": f"Unsupported store: {store}"}
        if not package and not query:
            return {"ok": False, "error": "package or query is required for store_install"}

        artifact = None
        candidates: list[dict[str, Any]] = []
        selection_reason = None
        if package:
            artifact = self.aptoide.get_meta(package)
            selection_reason = "exact_package"
            if artifact is None:
                return {"ok": False, "error": f"No Aptoide artifact found for package {package}", "store": store_name, "package": package}
        else:
            search = self.store_search(str(query), store=store_name, limit=limit)
            if not search.get("ok"):
                return search
            candidates = list(search.get("results") or [])
            if not candidates:
                return {"ok": False, "error": f"No {store_name} results found for query {query!r}", "store": store_name, "query": query}
            chosen, selection_reason = store_utils.select_store_candidate(query, candidates)
            if chosen is None:
                return {
                    "ok": False,
                    "error": "store_install query is ambiguous; run store_search and pass an exact package.",
                    "store": store_name,
                    "query": query,
                    "candidates": candidates[:5],
                }
            artifact = self.aptoide.get_meta(str(chosen.get("package")))
            if artifact is None:
                return {
                    "ok": False,
                    "error": f"Chosen {store_name} candidate could not be resolved to a downloadable artifact.",
                    "store": store_name,
                    "query": query,
                    "candidate": chosen,
                }
            candidates = candidates[:5]

        adb = self.waydroid.ensure_adb_connected()
        if not adb.get("ok"):
            return {"ok": False, "error": adb.get("error") or adb.get("output") or "ADB connection failed"}
        downloaded = self._download_store_artifact(artifact)
        if not downloaded.get("ok"):
            return {
                "ok": False,
                "error": downloaded.get("error") or "Failed to download store artifact",
                "store": store_name,
                "package": artifact.package,
                "artifact": artifact.to_dict(),
            }
        install_result = self.waydroid.install_apk_adb(str(downloaded["path"]))
        verification = self.app_installed(artifact.package)
        installed = bool(verification.get("ok") and verification.get("installed"))
        self._invalidate_navigation_state()
        return {
            "ok": bool(install_result.get("ok")) and installed,
            "action": "store_install",
            "store": store_name,
            "package": artifact.package,
            "query": query,
            "selection_reason": selection_reason,
            "artifact": artifact.to_dict(),
            "download": downloaded,
            "install": install_result,
            "verification": verification,
            "candidates": candidates or None,
        }

    def app_installed(self, package: str) -> dict[str, Any]:
        if not package:
            return {"ok": False, "error": "package is required for app_installed"}
        probe = self.waydroid.adb_shell(["pm", "path", package], timeout=10.0)
        installed = probe.get("ok") and bool(probe.get("stdout", "").strip())
        return {"ok": True, "package": package, "installed": installed, "path": probe.get("stdout")}

    def current_app(self) -> dict[str, Any]:
        return {"ok": True, "current_app": self._current_app_via_adb()}

    @staticmethod
    def _activity_matches(expected: str | None, current: str | None) -> bool:
        if not expected:
            return True
        if not current:
            return False
        if current == expected:
            return True
        if expected.startswith(".") and current.endswith(expected):
            return True
        if current.startswith(".") and expected.endswith(current):
            return True
        return current.endswith(expected) or expected.endswith(current)

    def _wait_for_navigation_target(
        self,
        *,
        package: str | None = None,
        activity: str | None = None,
        timeout_s: float = 8.0,
    ) -> dict[str, Any]:
        if not package and not activity:
            current = self._current_app_via_adb()
            return {"ok": True, "matched": True, "current_app": current, "package": package, "activity": activity}
        deadline = time.time() + timeout_s
        last_current = {"package": None, "activity": None}
        while time.time() < deadline:
            current = self._current_app_via_adb()
            last_current = current
            package_ok = (not package) or current.get("package") == package
            activity_ok = self._activity_matches(activity, current.get("activity"))
            if package_ok and activity_ok:
                return {"ok": True, "matched": True, "current_app": current, "package": package, "activity": activity}
            time.sleep(0.35)
        return {
            "ok": False,
            "matched": False,
            "current_app": last_current,
            "package": package,
            "activity": activity,
            "error": "Timed out waiting for navigation target.",
        }

    def _navigation_success(
        self,
        *,
        action: str,
        launch_result: dict[str, Any],
        wait_package: str | None = None,
        wait_activity: str | None = None,
    ) -> dict[str, Any]:
        wait_result = self._wait_for_navigation_target(package=wait_package, activity=wait_activity)
        if not wait_result.get("ok"):
            return {
                "ok": False,
                "action": action,
                "error": wait_result.get("error") or "Navigation target did not become foreground.",
                "launch_result": launch_result,
                "current_app": wait_result.get("current_app"),
                "expected_package": wait_package,
                "expected_activity": wait_activity,
            }
        self._invalidate_navigation_state()
        return {
            "ok": True,
            "action": action,
            **launch_result,
            "current_app": wait_result.get("current_app"),
            "snapshot_stale": True,
            "next_step": "snapshot",
        }

    def app_open(self, package: str) -> dict[str, Any]:
        if self.settings.allowed_packages and package not in self.settings.allowed_packages:
            return {"ok": False, "error": f"Package {package} is not in the allowed package list."}
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error") or "Android bridge is not ready"}
        launched = self.waydroid.app_open(package)
        if not launched.get("ok"):
            return {"ok": False, "error": launched.get("error") or launched.get("result", {}).get("stderr") or "Failed to launch package", "package": package}
        # waydroid.app_open already waits for the PID; no need for a second wait here.
        self._invalidate_navigation_state()
        return {"ok": True, "current_app": self._current_app_via_adb(), "package": package, "snapshot_stale": True}

    def activity_start(self, package: str, activity: str, stop: bool = False) -> dict[str, Any]:
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error") or "Android bridge is not ready"}
        if self.settings.allowed_packages and package not in self.settings.allowed_packages:
            return {"ok": False, "error": f"Package {package} is not in the allowed package list."}
        result = self.waydroid.start_activity(package, activity, stop=stop)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "Failed to start activity", **result}
        return self._navigation_success(
            action="activity_start",
            launch_result=result,
            wait_package=package,
            wait_activity=activity,
        )

    def intent_start(
        self,
        *,
        intent_action: str | None = None,
        data_url: str | None = None,
        package: str | None = None,
        activity: str | None = None,
        mime_type: str | None = None,
        categories: list[str] | None = None,
        extras: dict[str, str] | None = None,
        stop: bool = False,
        wait_package: str | None = None,
        wait_activity: str | None = None,
    ) -> dict[str, Any]:
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error") or "Android bridge is not ready"}
        if package and self.settings.allowed_packages and package not in self.settings.allowed_packages:
            return {"ok": False, "error": f"Package {package} is not in the allowed package list."}
        result = self.waydroid.start_intent(
            action=intent_action,
            data_url=data_url,
            package=package,
            activity=activity,
            mime_type=mime_type,
            categories=categories,
            extras=extras,
            stop=stop,
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "Intent launch failed", **result}
        return self._navigation_success(
            action="intent_start",
            launch_result=result,
            wait_package=wait_package or package,
            wait_activity=wait_activity or activity,
        )

    def url_open(self, url: str, package: str | None = None) -> dict[str, Any]:
        return self.intent_start(
            intent_action="android.intent.action.VIEW",
            data_url=url,
            package=package,
            wait_package=package,
        )

    def settings_open(self, settings_action: str | None = None) -> dict[str, Any]:
        return self.intent_start(
            intent_action=settings_action or "android.settings.SETTINGS",
            wait_package="com.android.settings",
        )

    def app_details_open(self, package: str) -> dict[str, Any]:
        return self.intent_start(
            intent_action="android.settings.APPLICATION_DETAILS_SETTINGS",
            data_url=f"package:{package}",
            wait_package="com.android.settings",
        )

    def market_open(self, package: str | None = None, query: str | None = None) -> dict[str, Any]:
        if package:
            data_url = f"market://details?id={urllib.parse.quote(package, safe='')}"
        elif query:
            data_url = f"market://search?q={urllib.parse.quote(query, safe='')}"
        else:
            return {"ok": False, "error": "package or query is required for market_open"}
        store_target = None
        aptoide = self.app_installed("cm.aptoide.pt")
        if aptoide.get("ok") and aptoide.get("installed"):
            store_target = "cm.aptoide.pt"
        return self.intent_start(
            intent_action="android.intent.action.VIEW",
            data_url=data_url,
            package=store_target,
            wait_package=store_target,
        )

    def install_apk(self, apk_path: str, approved: bool = False) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(approved, "app_install requires approved=true by policy")
            if blocked:
                return blocked
        return self.waydroid.install_apk(apk_path)

    def install_apk_url(self, apk_url: str, approved: bool = False) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(approved, "app_install_url requires approved=true by policy")
            if blocked:
                return blocked
        parsed = urllib.parse.urlparse(apk_url)
        if parsed.scheme not in {"http", "https"}:
            return {"ok": False, "error": "apk_url must use http or https"}
        suffix = Path(parsed.path).suffix or ".apk"
        with tempfile.NamedTemporaryFile(prefix="openclaw-android-", suffix=suffix, delete=False) as handle:
            tmp_path = Path(handle.name)
        try:
            proc = subprocess.run(
                ["curl", "-fsSL", apk_url, "-o", str(tmp_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=300.0,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip(), "apk_url": apk_url}
            result = self.waydroid.install_apk(str(tmp_path))
            result["apk_url"] = apk_url
            return result
        finally:
            tmp_path.unlink(missing_ok=True)

    def remove_app(self, package: str, approved: bool = False) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(approved, "app_remove requires approved=true by policy")
            if blocked:
                return blocked
        return self.waydroid.remove_app(package)

    def manage_extras(self, extras: list[str], uninstall: bool = False, approved: bool = False) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(
                approved,
                f"extras_{'uninstall' if uninstall else 'install'} requires approved=true by policy",
            )
            if blocked:
                return blocked
        project_root = self._project_root()
        script = project_root / "scripts" / "install_waydroid_extras.sh"
        if not script.exists():
            return {"ok": False, "error": f"Missing helper script: {script}"}
        joined = ",".join(extras)
        if not joined:
            return {"ok": False, "error": "extras list is empty"}
        if uninstall:
            return {"ok": False, "error": "Extras uninstall is not implemented by the waydroid_script wrapper in this project."}
        try:
            proc = subprocess.run(
                [str(script), "--extras", joined],
                capture_output=True,
                text=True,
                timeout=1800.0,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "Extras installation timed out after 30 minutes.",
                "extras": extras,
            }
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "extras": extras,
        }

    def install_default_stores(self, stores: list[str] | None = None, approved: bool = False) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(
                approved,
                "default_stores_install requires approved=true by policy",
            )
            if blocked:
                return blocked
        project_root = self._project_root()
        script = project_root / "scripts" / "install_default_stores.sh"
        selected = stores or self.settings.default_stores
        proc = subprocess.run(
            [str(script), "--stores", ",".join(selected)],
            capture_output=True,
            text=True,
            timeout=600.0,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "stores": selected,
        }

    def apply_device_profile(self, profile: str | None = None, approved: bool = False) -> dict[str, Any]:
        if self.settings.require_approval_for_install:
            blocked = self._ensure_admin_approved(
                approved,
                "device_profile_apply requires approved=true by policy",
            )
            if blocked:
                return blocked
        project_root = self._project_root()
        script = project_root / "scripts" / "apply_device_profile.sh"
        serial = self._adb_serial()
        if not serial:
            return {"ok": False, "error": "Unable to determine Waydroid ADB serial."}
        chosen = profile or self.settings.device_profile
        proc = subprocess.run(
            [str(script), "--profile", chosen, "--adb-serial", serial],
            capture_output=True,
            text=True,
            timeout=120.0,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "profile": chosen,
        }

    def configure_bridge(self, allowed_packages: list[str]) -> dict[str, Any]:
        bridge = self.bridge
        if bridge is None:
            return {
                "ok": False,
                "error": "Android bridge is unavailable (no Waydroid ADB serial). Start Waydroid and retry.",
            }
        result = bridge.configure(allowed_packages)
        self.settings.allowed_packages = list(allowed_packages)
        return result

    def _bridge_tree_raw(self, mode: str = "interactive") -> dict[str, Any]:
        """
        Fetch the raw tree response from the bridge, which includes:
        - nodes (with window_rank, package, confidence metadata)
        - foreground_package
        - event_seq
        - windows_total
        """
        return self.bridge.tree(mode=mode)

    def _bridge_tree_nodes(
        self, mode: str = "interactive"
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Returns (nodes, raw_tree) from the bridge.
        Raises RuntimeError if the bridge is unavailable.
        """
        raw = self._bridge_tree_raw(mode=mode)
        if not raw.get("ok"):
            raise RuntimeError(raw.get("error") or "Bridge tree request failed")
        return list(raw.get("nodes", [])), raw

    def _needs_screenshot_fallback(
        self, nodes: list[dict[str, Any]], fg_package: str | None
    ) -> bool:
        """
        Return True when the tree is too sparse/noisy to act on reliably and a
        screenshot-based multimodal path is recommended.
        """
        if not nodes:
            return True
        actionable = [n for n in nodes if n.get("clickable") or n.get("scrollable") or n.get("editable")]
        labeled = [n for n in nodes if n.get("text") or n.get("content_desc") or n.get("hint_text")]
        foreground_nodes = [n for n in nodes if n.get("package") == fg_package] if fg_package else []
        foreground_actionable = [
            n for n in foreground_nodes if n.get("clickable") or n.get("scrollable") or n.get("editable")
        ]
        foreground_labeled_actionable = [
            n for n in foreground_actionable if n.get("text") or n.get("content_desc") or n.get("hint_text")
        ]
        foreground_webviews = [
            n for n in foreground_nodes if "webview" in str(n.get("class_name") or n.get("className") or "").lower()
        ]
        # If fewer than 20% of nodes have labels, the UI is likely icon-driven.
        if labeled and len(labeled) < len(nodes) * 0.2:
            return True
        # If there are almost no actionable nodes, fall back to screenshot.
        if len(actionable) == 0:
            return True
        if foreground_nodes and len(foreground_nodes) <= 3:
            return True
        if foreground_actionable and len(foreground_labeled_actionable) <= 1:
            return True
        return bool(foreground_webviews and len(foreground_labeled_actionable) <= 2)

    @staticmethod
    def _ref_label(ref: dict[str, Any]) -> str:
        return ref_utils.ref_label(ref)

    @classmethod
    def _ref_signature(cls, ref: dict[str, Any]) -> str:
        return ref_utils.ref_signature(ref)

    @classmethod
    def _ref_detail_labels(cls, ref: dict[str, Any] | Any) -> tuple[str, ...]:
        return ref_utils.ref_detail_labels(ref)

    @staticmethod
    def _bounds_area(bounds: tuple[int, int, int, int] | list[int] | None) -> int:
        return ref_utils.bounds_area(bounds)

    @classmethod
    def _is_chrome_ref(cls, ref: dict[str, Any]) -> bool:
        return targets.is_chrome_ref(ref)

    @classmethod
    def _container_priority(cls, ref: dict[str, Any], fg_package: str | None) -> float:
        return targets.container_priority(ref, fg_package)

    @classmethod
    def _action_priority(cls, ref: dict[str, Any]) -> int:
        return targets.action_priority(ref)

    @classmethod
    def _rank_best_target_items(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return targets.rank_best_target_items(items)

    @classmethod
    def _best_targets(cls, flattened: list[dict[str, Any]], fg_package: str | None, limit: int = 5) -> list[dict[str, Any]]:
        return targets.best_targets(flattened, fg_package, limit=limit)

    @classmethod
    def _screen_signature(cls, screen_context: dict[str, Any] | None) -> tuple[str, str, str]:
        return screen_context_utils.screen_signature(screen_context)

    @classmethod
    def _target_signature(
        cls,
        flattened: list[dict[str, Any]],
        fg_package: str | None,
        limit: int = 5,
    ) -> list[str]:
        return screen_context_utils.target_signature(flattened, fg_package, limit=limit)

    def _bridge_event_seq(self) -> int | None:
        try:
            self.waydroid.forward_bridge(self.settings.bridge_port)
            health = self.bridge.health() if self.bridge else None
        except Exception:
            self._invalidate_bridge()
            return None
        if not health or not health.get("ok"):
            return None
        try:
            return int(health.get("event_seq"))
        except Exception:
            return None

    def _wait_for_ui_settle(
        self,
        *,
        timeout_s: float = 2.2,
        stable_s: float = 0.45,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        last_marker: tuple[str | None, str | None, int | None] | None = None
        stable_since: float | None = None
        last_current = self._current_app_via_adb()
        last_seq = self._bridge_event_seq()

        while time.time() < deadline:
            current = self._current_app_via_adb()
            seq = self._bridge_event_seq()
            marker = (current.get("package"), current.get("activity"), seq)
            now = time.time()
            last_current = current
            last_seq = seq
            if marker != last_marker:
                last_marker = marker
                stable_since = now
            elif stable_since is not None and now - stable_since >= stable_s:
                return {
                    "settled": True,
                    "current_app": current,
                    "event_seq": seq,
                }
            time.sleep(0.15)

        return {
            "settled": False,
            "current_app": last_current,
            "event_seq": last_seq,
        }

    @classmethod
    def _match_post_action_ref(
        cls,
        handle,
        refs: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return action_verify.match_post_action_ref(handle, refs)

    def _verify_action_result(
        self,
        *,
        op: str,
        text: str | None,
        handle,
        before_snapshot: SnapshotState,
        before_flattened: list[dict[str, Any]],
        before_screen: dict[str, Any],
        before_current: dict[str, Any],
        before_event_seq: int | None,
        post_state: dict[str, Any],
        post_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return action_verify.verify_action_result(
            op=op,
            text=text,
            handle=handle,
            before_snapshot=before_snapshot,
            before_flattened=before_flattened,
            before_screen=before_screen,
            before_current=before_current,
            before_event_seq=before_event_seq,
            post_state=post_state,
            post_snapshot=post_snapshot,
        )

    @classmethod
    def _screen_context(
        cls,
        flattened: list[dict[str, Any]],
        package: str | None,
        activity: str | None,
        fg_package: str | None,
    ) -> dict[str, Any]:
        return screen_context_utils.build_screen_context(flattened, package, activity, fg_package)

    @staticmethod
    def _normalize_decision_mode(mode: str | None, default: str) -> str:
        return decision.normalize_decision_mode(mode, default)

    @staticmethod
    def _goal_terms(text: str | None) -> set[str]:
        return decision.goal_terms(text)

    @classmethod
    def _is_generic_ui_label(cls, text: str | None) -> bool:
        return targets.is_generic_ui_label(text)

    @classmethod
    def _is_headerish_ref(cls, ref: dict[str, Any]) -> bool:
        return targets.is_headerish_ref(ref)

    @classmethod
    def _is_backward_ref(cls, ref: dict[str, Any]) -> bool:
        return targets.is_backward_ref(ref)

    @classmethod
    def _looks_install_progress_label(cls, text: str | None) -> bool:
        return targets.looks_install_progress_label(text)

    @classmethod
    def _goal_match_score(cls, ref: dict[str, Any], goal_terms: set[str]) -> int:
        return decision.goal_match_score(ref, goal_terms)

    @classmethod
    def _deterministic_decision(
        cls,
        snapshot_result: dict[str, Any],
        goal: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        return decision.deterministic_decision(snapshot_result, goal)

    @classmethod
    def _should_use_vision_for_decision(cls, snapshot_result: dict[str, Any], goal: str) -> bool:
        return decision.should_use_vision_for_decision(snapshot_result, goal)

    def decide_next(
        self,
        *,
        goal: str | None = None,
        snapshot_mode: str = "hybrid",
        decision_mode: str | None = None,
        auto_execute: bool = False,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        goal = (goal or "Choose the safest single next UI action that advances the current Android task.").strip()
        normalized_mode = self._normalize_decision_mode(decision_mode, self.settings.llm_decision_mode_default)
        snapshot_result = self.snapshot(mode=snapshot_mode, include_screenshot=False)
        if not snapshot_result.get("ok"):
            return snapshot_result

        warnings: list[str] = []
        deterministic, deterministic_warnings = self._deterministic_decision(snapshot_result, goal)
        warnings.extend(deterministic_warnings)

        if normalized_mode == "deterministic":
            if not deterministic:
                return {
                    "ok": False,
                    "action": "decide_next",
                    "error": "Deterministic selection was ambiguous on this screen.",
                    "decision_mode_requested": normalized_mode,
                    "decision_mode_used": normalized_mode,
                    "decision_source": "deterministic",
                    "snapshot_id": snapshot_result.get("snapshot_id"),
                    "current_app": snapshot_result.get("current_app"),
                    "screen_context": snapshot_result.get("screen_context"),
                    "top_refs": snapshot_result.get("top_refs"),
                    "warnings": warnings,
                    "screenshot_path": snapshot_result.get("screenshot_path"),
                }
            decision = deterministic
            decision_source = "deterministic"
            decision_mode_used = "deterministic"
            provider_used = None
            model_used = None
            latency_ms = 0
        else:
            llm_mode = normalized_mode
            if llm_mode == "auto":
                llm_mode = "llm_vision" if self._should_use_vision_for_decision(snapshot_result, goal) else "llm_text"
            if llm_mode in {"llm_text", "llm_vision"}:
                snapshot_result = self.snapshot(mode=snapshot_mode, include_screenshot=True)
                if not snapshot_result.get("ok"):
                    return snapshot_result

            if normalized_mode == "auto" and deterministic:
                decision = deterministic
                decision_source = "deterministic"
                decision_mode_used = "deterministic"
                provider_used = None
                model_used = None
                latency_ms = 0
            else:
                try:
                    llm_result = self.llm_decider.decide(
                        snapshot=snapshot_result,
                        goal=goal,
                        mode="vision" if llm_mode == "llm_vision" else "text",
                        provider_name=provider_name,
                        model_name=model_name,
                    )
                except LlmDecisionError as exc:
                    return {
                        "ok": False,
                        "action": "decide_next",
                        "error": str(exc),
                        "decision_mode_requested": normalized_mode,
                        "decision_mode_used": llm_mode,
                        "decision_source": "llm",
                        "goal": goal,
                        "snapshot_id": snapshot_result.get("snapshot_id"),
                        "current_app": snapshot_result.get("current_app"),
                        "screen_context": snapshot_result.get("screen_context"),
                        "top_refs": snapshot_result.get("top_refs"),
                        "source": snapshot_result.get("source"),
                        "screenshot_path": snapshot_result.get("screenshot_path"),
                        "provider": provider_name,
                        "model": model_name,
                        "latency_ms": 0,
                        "decision": None,
                        "warnings": warnings,
                        "execution": None,
                    }
                decision = llm_result.get("decision") or {}
                decision_source = "llm"
                decision_mode_used = llm_mode
                provider_used = llm_result.get("provider")
                model_used = llm_result.get("model")
                latency_ms = int(llm_result.get("latency_ms") or 0)
                warnings.extend(llm_result.get("warnings") or [])

        execution = None
        if auto_execute and decision.get("decision") == "click" and decision.get("ref") and snapshot_result.get("snapshot_id"):
            execution = self.act(snapshot_result["snapshot_id"], str(decision["ref"]), "click")

        return {
            "ok": True,
            "action": "decide_next",
            "decision_mode_requested": normalized_mode,
            "decision_mode_used": decision_mode_used,
            "decision_source": decision_source,
            "goal": goal,
            "snapshot_id": snapshot_result.get("snapshot_id"),
            "current_app": snapshot_result.get("current_app"),
            "screen_context": snapshot_result.get("screen_context"),
            "top_refs": snapshot_result.get("top_refs"),
            "source": snapshot_result.get("source"),
            "screenshot_path": snapshot_result.get("screenshot_path"),
            "provider": provider_used,
            "model": model_used,
            "latency_ms": latency_ms,
            "decision": decision,
            "warnings": warnings,
            "execution": execution,
        }

    def snapshot(self, mode: str = "interactive", include_screenshot: bool = False) -> dict[str, Any]:
        mode = mode if mode in {"interactive", "hybrid", "full"} else "interactive"
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error")}

        current_app = self._current_app_via_adb()
        package = current_app.get("package")
        activity = current_app.get("activity")
        if self.settings.allowed_packages and package and package not in self.settings.allowed_packages:
            return {"ok": False, "error": f"Foreground package {package} is not allowed."}

        nodes: list[dict[str, Any]]
        raw_tree: dict[str, Any] = {}
        warnings: list[str] = []
        source = "unknown"
        fg_package: str | None = None
        event_seq = 0
        windows_total = 0
        auto_screenshot = False
        root_debug: dict[str, Any] | None = None

        try:
            nodes, raw_tree = self._bridge_tree_nodes(mode=mode)
            source = "bridge"
            fg_package = raw_tree.get("foreground_package") or package
            event_seq = raw_tree.get("event_seq", 0)
            windows_total = raw_tree.get("windows_total", 0)
            if isinstance(raw_tree.get("root_debug"), dict):
                root_debug = raw_tree.get("root_debug")

            # Check if the tree is too sparse for reliable navigation.
            if self._needs_screenshot_fallback(nodes, fg_package):
                auto_screenshot = True
                warnings.append(
                    "Tree is sparse/icon-driven; screenshot assistance is recommended."
                )
                if root_debug and not nodes:
                    empty_reason = root_debug.get("empty_reason")
                    attempts = root_debug.get("attempts")
                    windows_observed = root_debug.get("windows_observed")
                    windows_with_root = root_debug.get("windows_with_root")
                    warnings.append(
                        "Bridge returned no accessible roots "
                        f"(reason={empty_reason or 'unknown'}, attempts={attempts or 0}, "
                        f"windows_observed={windows_observed or 0}, windows_with_root={windows_with_root or 0})."
                    )

        except Exception as exc:
            self._invalidate_bridge()
            shot_path: str | None = None
            shot = self._screenshot_path("snapshot_bridge_error")
            self.ensure_bridge_ready()
            shot_result = self.waydroid.screenshot(shot)
            if shot_result.get("ok"):
                shot_path = shot_result.get("path")
            error = (
                f"Accessibility bridge unavailable ({exc}). "
                f"{self._uiautomator2_disabled_reason()}"
            )
            warnings.append(error)
            if not shot_result.get("ok"):
                warnings.append(
                    "ADB screenshot capture also failed: "
                    f"{shot_result.get('error') or shot_result.get('stderr') or 'unknown error'}"
                )
            return {
                "ok": False,
                "error": error,
                "package": package,
                "activity": activity,
                "foreground_package": package,
                "mode": mode,
                "refs": [],
                "summary": [],
                "top_refs": [],
                "stats": {
                    "refs": 0,
                    "windows_total": 0,
                    "event_seq": 0,
                    "screenshot_auto": bool(shot_path),
                },
                "source": "adb_screenshot_only" if shot_path else "bridge_unavailable",
                "screenshot_path": shot_path,
                "warnings": warnings,
            }

        screenshot_path: str | None = None
        capture_screenshot = include_screenshot or auto_screenshot
        if capture_screenshot:
            shot = self._screenshot_path("snapshot")
            self.ensure_bridge_ready()
            shot_result = self.waydroid.screenshot(shot)
            if shot_result.get("ok"):
                screenshot_path = shot_result.get("path")
                if auto_screenshot and not include_screenshot:
                    warnings.append("Attached a screenshot automatically because the accessibility tree was weak.")
            else:
                warnings.append(
                    "ADB screenshot capture failed: "
                    f"{shot_result.get('error') or shot_result.get('stderr') or 'unknown error'}"
                )

        snapshot = build_snapshot(
            nodes=nodes,
            package=package,
            activity=activity,
            mode=mode,
            screenshot_path=screenshot_path,
            foreground_package=fg_package,
            event_seq=event_seq,
            windows_total=windows_total,
            source=source,
            warnings=warnings,
        )
        self._remember_snapshot(snapshot)

        flattened = snapshot.flattened()
        top_refs = self._best_targets(flattened, fg_package, limit=5)
        screen_context = self._screen_context(flattened, package, activity, fg_package)

        return {
            "ok": True,
            "snapshot_id": snapshot.snapshot_id,
            "current_app": current_app,
            "package": package,
            "activity": activity,
            "foreground_package": fg_package,
            "mode": mode,
            "refs": flattened,
            "summary": snapshot.summary_lines(),
            "top_refs": top_refs,  # highest-confidence refs for quick disambiguation
            "screen_context": screen_context,
            "root_debug": root_debug,
            "stats": {
                "refs": len(snapshot.refs),
                "windows_total": windows_total,
                "event_seq": event_seq,
                "screenshot_auto": bool(auto_screenshot and screenshot_path),
                "screenshot_recommended": auto_screenshot,
            },
            "source": source,
            "screenshot_path": screenshot_path,
            "warnings": warnings,
        }

    def screenshot(self) -> dict[str, Any]:
        self.ensure_bridge_ready()
        shot = self._screenshot_path("screenshot")
        shot_result = self.waydroid.screenshot(shot)
        if not shot_result.get("ok"):
            return {
                "ok": False,
                "error": shot_result.get("error") or shot_result.get("stderr") or "Screenshot capture failed",
            }
        current = self._current_app_via_adb()
        return {
            "ok": True,
            "path": shot_result.get("path"),
            "current_app": current,
            "source": shot_result.get("source") or "adb",
        }


    def _prune_screenshots(self, keep: int = 100) -> None:
        # Screenshots accumulate one PNG per capture forever otherwise;
        # keep the most recent `keep` and drop the rest.
        try:
            shots = sorted(
                self.settings.screenshot_dir.glob("*.png"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in shots[keep:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    def _screenshot_path(self, prefix: str) -> Path:
        self._prune_screenshots()
        return self.settings.screenshot_dir / f"{prefix}_{now_ms()}.png"

    def _protected_action_block(self, handle) -> str | None:
        label = f"{handle.text} {handle.content_desc} {handle.hint_text}".strip().lower()
        for token in self.settings.protected_texts:
            if token in label:
                return token
        return None

    def _protected_token_for_bridge_node(self, node: dict[str, Any]) -> str | None:
        # Raw bridge nodes are plain dicts (no NodeHandle attributes yet).
        label = " ".join(
            str(node.get(key) or "") for key in ("text", "content_desc", "hint_text")
        ).strip().lower()
        if not label:
            return None
        for token in self.settings.protected_texts:
            if token in label:
                return token
        return None

    @staticmethod
    def _point_in_bounds(x: int, y: int, bounds: tuple[int, int, int, int] | list[int] | None) -> bool:
        if not bounds or len(bounds) != 4:
            return False
        left, top, right, bottom = (int(value) for value in bounds)
        return left <= x <= right and top <= y <= bottom

    def _protected_coordinate_block(
        self,
        *,
        op: str,
        x: int | None = None,
        y: int | None = None,
        x1: int | None = None,
        y1: int | None = None,
        x2: int | None = None,
        y2: int | None = None,
    ) -> dict[str, Any] | None:
        points: list[tuple[int, int]] = []
        if op in {"tap", "long_press"} and x is not None and y is not None:
            points.append((x, y))
        elif op == "swipe":
            if x1 is not None and y1 is not None:
                points.append((x1, y1))
            if x2 is not None and y2 is not None:
                points.append((x2, y2))
        if not points:
            return None

        try:
            nodes, _raw = self._bridge_tree_nodes(mode="hybrid")
        except Exception as exc:
            return {
                "token": "unverified_screen",
                "error": f"Could not inspect the current screen for protected controls: {exc}",
            }

        candidates = []
        for node in nodes:
            token = self._protected_token_for_bridge_node(node)
            if not token:
                continue
            bounds = node.get("bounds")
            if any(self._point_in_bounds(px, py, bounds) for px, py in points):
                candidates.append((self._bounds_area(bounds), token, node))
        if not candidates:
            return None
        _area, token, node = min(candidates, key=lambda item: item[0])
        label = next(
            (str(node.get(key)) for key in ("text", "content_desc", "hint_text") if node.get(key)),
            "",
        )
        return {
            "token": token,
            "ref": node.get("ref"),
            "label": label,
            "bounds": node.get("bounds"),
        }

    def _require_snapshot(self, snapshot_id: str) -> SnapshotState:
        snapshot = self._snapshot_history.get(snapshot_id)
        if snapshot is None:
            raise RuntimeError("Snapshot is missing or stale. Re-run snapshot before acting.")
        return snapshot

    def _recover_handle_for_snapshot(self, handle, snapshot: SnapshotState):
        matched = self._match_post_action_ref(handle, snapshot.flattened())
        if not matched:
            return None
        recovered_ref = matched.get("ref")
        if not recovered_ref:
            return None
        return snapshot.refs.get(str(recovered_ref))

    @classmethod
    def _resolve_actionable_target(cls, snapshot: SnapshotState, handle, op: str):
        if op not in {"click", "click_center", "long_click"} or handle.is_actionable:
            return handle, None

        candidate_refs: list[str] = []
        seen: set[str] = set()

        def add_candidate(ref_id: str | None) -> None:
            if not ref_id or ref_id in seen or ref_id not in snapshot.refs:
                return
            seen.add(ref_id)
            candidate_refs.append(ref_id)

        add_candidate(handle.parent_ref)
        add_candidate(handle.container_ref)
        add_candidate(handle.section_ref)

        cursor_ref = handle.parent_ref
        while cursor_ref and cursor_ref in snapshot.refs:
            parent = snapshot.refs[cursor_ref]
            add_candidate(parent.ref)
            cursor_ref = parent.parent_ref

        if handle.parent_ref and handle.parent_ref in snapshot.refs:
            for sibling_ref in snapshot.refs[handle.parent_ref].child_refs:
                add_candidate(sibling_ref)
        if handle.container_ref and handle.container_ref in snapshot.refs:
            for member_ref in snapshot.refs[handle.container_ref].child_refs:
                add_candidate(member_ref)

        best = None
        best_score = -1
        source_label = handle.primary_label().strip().casefold()
        source_details = {label.casefold() for label in cls._ref_detail_labels(handle)}
        for candidate_ref in candidate_refs:
            candidate = snapshot.refs[candidate_ref]
            if not candidate.is_actionable:
                continue
            score = 0
            if candidate.package == handle.package:
                score += 40
            if candidate.is_direct_control:
                score += 30
            if candidate_ref in {handle.parent_ref, handle.container_ref, handle.section_ref}:
                score += 55
            if handle.ref in candidate.child_refs:
                score += 45
            candidate_label = candidate.primary_label().strip().casefold()
            if source_label and candidate_label == source_label:
                score += 80
            elif source_label and candidate_label and (source_label in candidate_label or candidate_label in source_label):
                score += 45
            score += 10 * len(source_details & {label.casefold() for label in cls._ref_detail_labels(candidate)})
            if candidate.active_window:
                score += 10
            if score > best_score:
                best_score = score
                best = candidate

        if best is None or best_score < 40:
            return handle, None
        return best, {
            "requested_ref": handle.ref,
            "resolved_ref": best.ref,
            "requested_label": handle.primary_label(),
            "resolved_label": best.primary_label(),
            "reason": "Resolved non-actionable child to an actionable row/control.",
        }

    @staticmethod
    def _verification_is_weak(verification: dict[str, Any]) -> bool:
        return action_verify.verification_is_weak(verification)

    def act(self, snapshot_id: str, ref: str, op: str, text: str | None = None) -> dict[str, Any]:
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error") or "Android bridge is not ready"}
        try:
            requested_snapshot = self._require_snapshot(snapshot_id)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        requested_handle = requested_snapshot.refs.get(ref)
        if requested_handle is None:
            return {"ok": False, "error": f"Unknown ref: {ref}"}

        snapshot = requested_snapshot
        handle = requested_handle
        stale_recovery = None
        if self._last_snapshot and self._last_snapshot.snapshot_id != requested_snapshot.snapshot_id:
            recovered = self._recover_handle_for_snapshot(requested_handle, self._last_snapshot)
            if recovered is None:
                return {
                    "ok": False,
                    "error": "Snapshot is stale and the target could not be recovered from the latest screen. Re-run snapshot before acting.",
                    "requested_snapshot_id": requested_snapshot.snapshot_id,
                    "current_snapshot_id": self._last_snapshot.snapshot_id,
                }
            snapshot = self._last_snapshot
            handle = recovered
            stale_recovery = {
                "requested_snapshot_id": requested_snapshot.snapshot_id,
                "current_snapshot_id": snapshot.snapshot_id,
                "requested_ref": requested_handle.ref,
                "recovered_ref": recovered.ref,
                "requested_label": requested_handle.primary_label(),
                "recovered_label": recovered.primary_label(),
            }

        handle, action_resolution = self._resolve_actionable_target(snapshot, handle, op)
        before_flattened = snapshot.flattened()
        before_screen = self._screen_context(
            before_flattened,
            snapshot.package,
            snapshot.activity,
            snapshot.foreground_package,
        )
        before_current = self._current_app_via_adb()
        before_event_seq = self._bridge_event_seq()

        if self.settings.require_approval_for_protected_actions and op in {"click", "long_click"}:
            token = self._protected_action_block(handle)
            if token:
                return {
                    "ok": False,
                    "error": f"Protected action blocked by policy ({token}). Approvals are required for this action.",
                    "ref": ref,
                    "op": op,
                }

        used: str | None = None
        action_ok = False
        retry_count = 0
        max_retries = 2

        while retry_count <= max_retries:
            try:
                if op in {"press_back", "press_home", "press_recents", "press_enter"}:
                    if self.bridge:
                        result = self.bridge.global_action(op)
                        if result.get("ok"):
                            used = "bridge.global_action"
                            action_ok = True
                    if not action_ok:
                        mapping = {
                            "press_back": "KEYCODE_BACK",
                            "press_home": "KEYCODE_HOME",
                            "press_recents": "KEYCODE_APP_SWITCH",
                            "press_enter": "KEYCODE_ENTER",
                        }
                        adb_result = self.waydroid.press_key(mapping[op])
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB key event failed")
                        used = "adb.keyevent"
                        action_ok = True

                elif handle.node_key and handle.source in {"bridge", "merged"} and self.bridge:
                    if op in {"click", "long_click", "scroll_forward", "scroll_backward", "set_text", "clear_text"}:
                        result = self.bridge.node_action(handle.node_key, op, text)
                        if result.get("ok"):
                            used = "bridge.node_action"
                            action_ok = True
                        elif retry_count < max_retries:
                            # Retry via bridge once more before falling through.
                            retry_count += 1
                            time.sleep(0.3)
                            continue
                    elif op in {"scroll_to_start", "scroll_to_end"}:
                        # Bridge doesn't support start/end natively; fall through to ADB swipe.
                        pass

                # Bounds fallback for all other cases.
                if not action_ok:
                    x, y = self._bounds_center(handle.bounds)
                    if op in {"click", "click_center"}:
                        adb_result = self.waydroid.tap(x, y)
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB tap failed")
                        used = "adb.tap_center" if op == "click_center" else "adb.tap"
                        action_ok = True
                    elif op == "long_click":
                        adb_result = self.waydroid.swipe(x, y, x, y, duration_ms=700)
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB long-press failed")
                        used = "adb.long_press"
                        action_ok = True
                    elif op == "set_text":
                        focus = self.waydroid.tap(x, y)
                        if not focus.get("ok"):
                            raise RuntimeError(focus.get("stderr") or focus.get("error") or "ADB focus tap failed")
                        time.sleep(0.2)
                        cleared = self._adb_clear_focused_text()
                        if not cleared.get("ok"):
                            raise RuntimeError(cleared.get("error") or "ADB text clear failed")
                        typed = self.waydroid.input_text(text or "")
                        if not typed.get("ok"):
                            raise RuntimeError(typed.get("stderr") or typed.get("error") or "ADB text input failed")
                        used = "adb.input_text"
                        action_ok = True
                    elif op == "clear_text":
                        focus = self.waydroid.tap(x, y)
                        if not focus.get("ok"):
                            raise RuntimeError(focus.get("stderr") or focus.get("error") or "ADB focus tap failed")
                        time.sleep(0.2)
                        cleared = self._adb_clear_focused_text()
                        if not cleared.get("ok"):
                            raise RuntimeError(cleared.get("error") or "ADB text clear failed")
                        used = "adb.clear_text"
                        action_ok = True
                    elif op == "scroll_forward":
                        x1, y1, x2, y2 = self._swipe_path(handle.bounds, "forward")
                        adb_result = self.waydroid.swipe(x1, y1, x2, y2, duration_ms=250)
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB swipe failed")
                        used = "adb.swipe"
                        action_ok = True
                    elif op == "scroll_backward":
                        x1, y1, x2, y2 = self._swipe_path(handle.bounds, "backward")
                        adb_result = self.waydroid.swipe(x1, y1, x2, y2, duration_ms=250)
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB swipe failed")
                        used = "adb.swipe"
                        action_ok = True
                    elif op == "scroll_to_start":
                        x1, y1, x2, y2 = self._swipe_path(handle.bounds, "start")
                        adb_result = self.waydroid.swipe(x1, y1, x2, y2, duration_ms=350)
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB swipe failed")
                        used = "adb.swipe"
                        action_ok = True
                    elif op == "scroll_to_end":
                        x1, y1, x2, y2 = self._swipe_path(handle.bounds, "end")
                        adb_result = self.waydroid.swipe(x1, y1, x2, y2, duration_ms=350)
                        if not adb_result.get("ok"):
                            raise RuntimeError(adb_result.get("stderr") or adb_result.get("error") or "ADB swipe failed")
                        used = "adb.swipe"
                        action_ok = True
                    else:
                        return {"ok": False, "error": f"Unsupported operation: {op}"}
            except Exception as exc:
                if retry_count < max_retries:
                    retry_count += 1
                    time.sleep(0.4)
                    self._invalidate_bridge()
                    continue
                return {"ok": False, "error": f"Action failed after {max_retries + 1} attempts: {exc}"}
            break

        if not action_ok:
            return {"ok": False, "error": f"Could not perform {op} on ref {ref}"}

        # Invalidate bridge after any action (window state may have changed).
        self._invalidate_bridge()
        post_state = self._wait_for_ui_settle()
        post_snapshot = self.snapshot(mode=snapshot.mode, include_screenshot=False)
        if not post_snapshot.get("ok"):
            self._clear_snapshot_state()
        verification = self._verify_action_result(
            op=op,
            text=text,
            handle=handle,
            before_snapshot=snapshot,
            before_flattened=before_flattened,
            before_screen=before_screen,
            before_current=before_current,
            before_event_seq=before_event_seq,
            post_state=post_state,
            post_snapshot=post_snapshot if post_snapshot.get("ok") else None,
        )
        retry_used = None
        if op == "click" and used == "bridge.node_action" and self._verification_is_weak(verification):
            retry_handle = handle
            if self._last_snapshot:
                recovered = self._recover_handle_for_snapshot(handle, self._last_snapshot)
                if recovered is not None:
                    retry_handle = recovered
            x, y = self._bounds_center(retry_handle.bounds)
            adb_retry = self.waydroid.tap(x, y)
            if adb_retry.get("ok"):
                retry_used = "adb.tap_center_retry"
                used = f"{used}+{retry_used}"
                self._invalidate_bridge()
                post_state = self._wait_for_ui_settle()
                post_snapshot = self.snapshot(mode=snapshot.mode, include_screenshot=False)
                if not post_snapshot.get("ok"):
                    self._clear_snapshot_state()
                verification = self._verify_action_result(
                    op="click_center",
                    text=text,
                    handle=retry_handle,
                    before_snapshot=snapshot,
                    before_flattened=before_flattened,
                    before_screen=before_screen,
                    before_current=before_current,
                    before_event_seq=before_event_seq,
                    post_state=post_state,
                    post_snapshot=post_snapshot if post_snapshot.get("ok") else None,
                )
        if (
            post_snapshot.get("ok")
            and self._verification_is_weak(verification)
            and not post_snapshot.get("screenshot_path")
        ):
            enriched_post_snapshot = self.snapshot(mode=snapshot.mode, include_screenshot=True)
            if enriched_post_snapshot.get("ok"):
                post_snapshot = enriched_post_snapshot
                verification = self._verify_action_result(
                    op=op,
                    text=text,
                    handle=handle,
                    before_snapshot=snapshot,
                    before_flattened=before_flattened,
                    before_screen=before_screen,
                    before_current=before_current,
                    before_event_seq=before_event_seq,
                    post_state=post_state,
                    post_snapshot=post_snapshot,
                )
        current = (
            post_snapshot.get("current_app")
            if post_snapshot.get("ok")
            else post_state.get("current_app") or self._current_app_via_adb()
        )
        return {
            "ok": True,
            "ref": handle.ref,
            "requested_ref": ref,
            "op": op,
            "used": used,
            "retry_used": retry_used,
            "current_app": current,
            "verified": verification.get("verified", False),
            "verification": verification,
            "stale_recovery": stale_recovery,
            "action_resolution": action_resolution,
            "snapshot_stale": not post_snapshot.get("ok"),
            "next_step": "use_post_action_snapshot" if post_snapshot.get("ok") else "snapshot",
            "post_action_snapshot": post_snapshot if post_snapshot.get("ok") else None,
            "next_snapshot_id": post_snapshot.get("snapshot_id") if post_snapshot.get("ok") else None,
        }

    def coordinate_act(
        self,
        op: str,
        *,
        x: int | None = None,
        y: int | None = None,
        x1: int | None = None,
        y1: int | None = None,
        x2: int | None = None,
        y2: int | None = None,
        duration_ms: int = 250,
        text: str | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        op = (op or "").strip().lower()
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error") or "Android bridge is not ready", "op": op}

        if self.settings.require_approval_for_protected_actions and op in {"tap", "long_press", "swipe"} and not approved:
            protected = self._protected_coordinate_block(op=op, x=x, y=y, x1=x1, y1=y1, x2=x2, y2=y2)
            if protected:
                token = protected.get("token")
                return {
                    "ok": False,
                    "error": f"Protected coordinate action blocked by policy ({token}). Use snapshot+act or pass approved=true only after explicit user approval.",
                    "op": op,
                    "protected": protected,
                }

        if op == "tap":
            if x is None or y is None:
                return {"ok": False, "error": "x and y are required for tap", "op": op}
            result = self.waydroid.tap(x, y)
        elif op == "long_press":
            if x is None or y is None:
                return {"ok": False, "error": "x and y are required for long_press", "op": op}
            result = self.waydroid.swipe(x, y, x, y, duration_ms=duration_ms or 700)
        elif op == "swipe":
            if x1 is None or y1 is None or x2 is None or y2 is None:
                return {"ok": False, "error": "x1, y1, x2, and y2 are required for swipe", "op": op}
            result = self.waydroid.swipe(x1, y1, x2, y2, duration_ms=duration_ms or 250)
        elif op == "type_text":
            if not text:
                return {"ok": False, "error": "text is required for type_text", "op": op}
            result = self.waydroid.input_text(text)
        elif op == "press_back":
            result = self.waydroid.press_key("KEYCODE_BACK")
        elif op == "press_home":
            result = self.waydroid.press_key("KEYCODE_HOME")
        else:
            return {"ok": False, "error": f"Unsupported coordinate op: {op}", "op": op}

        result = dict(result)
        result["op"] = op
        result["used"] = "daemon.coordinate_act"
        return result

    def wait(self, wait_for: str, wait_value: str | None = None, timeout_ms: int = 10000) -> dict[str, Any]:
        ready = self.ensure_bridge_ready()
        if not ready.get("ok"):
            return {"ok": False, "error": ready.get("error") or "Android bridge is not ready"}
        deadline = time.time() + (timeout_ms / 1000.0)
        matched = False
        last_event_seq = None
        idle_since = None
        while time.time() < deadline:
            current = self._current_app_via_adb()
            if wait_for == "idle":
                try:
                    health = self.bridge.health() if self.bridge else {"ok": False}
                except Exception:
                    health = {"ok": False}
                if health.get("ok"):
                    seq = health.get("event_seq")
                    now = time.time()
                    if seq == last_event_seq:
                        idle_since = idle_since or now
                        if now - idle_since >= 0.75:
                            matched = True
                            break
                    else:
                        last_event_seq = seq
                        idle_since = now
                else:
                    matched = True
                    break
            if wait_for == "package" and current.get("package") == wait_value:
                matched = True
                break
            if wait_for == "activity" and current.get("activity") == wait_value:
                matched = True
                break
            if wait_for == "text":
                try:
                    nodes, _ = self._bridge_tree_nodes(mode="hybrid")
                except Exception:
                    self._invalidate_bridge()
                    time.sleep(0.35)
                    continue
                if any(
                    wait_value
                    and wait_value.lower() in f"{n.get('text','')} {n.get('content_desc','')} {n.get('hint_text', '')}".lower()
                    for n in nodes
                ):
                    matched = True
                    break
            if wait_for in {"ref_appears", "ref_gone"} and self._last_snapshot:
                nodes_result = self.snapshot(mode=self._last_snapshot.mode, include_screenshot=False)
                refs = {item["ref"] for item in nodes_result.get("refs", [])}
                if wait_for == "ref_appears" and wait_value in refs:
                    matched = True
                    break
                if wait_for == "ref_gone" and wait_value not in refs:
                    matched = True
                    break
            time.sleep(0.35)
        return {"ok": True, "matched": matched, "wait_for": wait_for, "wait_value": wait_value, "timeout_ms": timeout_ms}
