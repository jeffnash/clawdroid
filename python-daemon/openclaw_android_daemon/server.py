from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .controller import AndroidRuntime


class AgentDispatchRequest(BaseModel):
    action: str
    device: str | None = None
    store: str | None = None
    package: str | None = None
    activity: str | None = None
    query: str | None = None
    url: str | None = None
    intent_action: str | None = None
    data_url: str | None = None
    mime_type: str | None = None
    categories: list[str] | None = None
    extras: dict[str, str] | None = None
    settings_action: str | None = None
    stop: bool = False
    snapshot_mode: str = "interactive"
    include_screenshot: bool = False
    goal: str | None = None
    decision_mode: str | None = None
    auto_execute: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    snapshot_id: str | None = None
    ref: str | None = None
    op: str | None = None
    text: str | None = None
    x: int | None = None
    y: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int = 250
    approved: bool = False
    limit: int = Field(default=10, ge=1, le=25)
    timeout_ms: int = 10000
    wait_for: str | None = None
    wait_value: str | None = None


class AdminDispatchRequest(BaseModel):
    action: str
    mode: str | None = None
    store: str | None = None
    package: str | None = None
    query: str | None = None
    apk_path: str | None = None
    apk_url: str | None = None
    extras: list[str] | None = None
    stores: list[str] | None = None
    allowed_packages: list[str] | None = None
    approved: bool = False
    limit: int = Field(default=10, ge=1, le=25)
    timeout_ms: int = 10000


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    runtime = AndroidRuntime(settings)
    app = FastAPI(title="OpenClaw Android Waydroid Daemon", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/v1/status")
    def status() -> dict[str, Any]:
        return runtime.status()

    @app.post("/v1/agent/dispatch")
    def agent_dispatch(body: AgentDispatchRequest) -> dict[str, Any]:
        try:
            action = body.action
            if action == "status":
                return runtime.status()
            if action == "current_app":
                return runtime.current_app()
            if action == "apps_list":
                return runtime.apps_list()
            if action == "apps_search":
                if not body.query:
                    raise HTTPException(status_code=400, detail="query is required for apps_search")
                return runtime.apps_search(body.query)
            if action == "service_resolve":
                if not body.query:
                    raise HTTPException(status_code=400, detail="query is required for service_resolve")
                return runtime.service_resolve(body.query)
            if action == "task_route":
                route_goal = body.goal or body.query
                if not route_goal:
                    raise HTTPException(status_code=400, detail="goal or query is required for task_route")
                return runtime.task_route(route_goal)
            if action == "app_installed":
                if not body.package:
                    raise HTTPException(status_code=400, detail="package is required for app_installed")
                return runtime.app_installed(body.package)
            if action == "store_search":
                if not body.query:
                    raise HTTPException(status_code=400, detail="query is required for store_search")
                return runtime.store_search(body.query, store=body.store or "aptoide", limit=body.limit)
            if action == "app_open":
                if not body.package:
                    raise HTTPException(status_code=400, detail="package is required for app_open")
                return runtime.app_open(body.package)
            if action == "activity_start":
                if not body.package or not body.activity:
                    raise HTTPException(status_code=400, detail="package and activity are required for activity_start")
                return runtime.activity_start(body.package, body.activity, stop=body.stop)
            if action == "intent_start":
                return runtime.intent_start(
                    intent_action=body.intent_action,
                    data_url=body.data_url,
                    package=body.package,
                    activity=body.activity,
                    mime_type=body.mime_type,
                    categories=body.categories,
                    extras=body.extras,
                    stop=body.stop,
                )
            if action == "url_open":
                if not body.url:
                    raise HTTPException(status_code=400, detail="url is required for url_open")
                return runtime.url_open(body.url, package=body.package)
            if action == "settings_open":
                return runtime.settings_open(body.settings_action)
            if action == "app_details_open":
                if not body.package:
                    raise HTTPException(status_code=400, detail="package is required for app_details_open")
                return runtime.app_details_open(body.package)
            if action == "market_open":
                if not body.package and not body.query:
                    raise HTTPException(status_code=400, detail="package or query is required for market_open")
                return runtime.market_open(package=body.package, query=body.query)
            if action == "snapshot":
                return runtime.snapshot(mode=body.snapshot_mode, include_screenshot=body.include_screenshot)
            if action == "screenshot":
                return runtime.screenshot()
            if action == "decide_next":
                return runtime.decide_next(
                    goal=body.goal,
                    snapshot_mode=body.snapshot_mode,
                    decision_mode=body.decision_mode,
                    auto_execute=body.auto_execute,
                    provider_name=body.llm_provider,
                    model_name=body.llm_model,
                )
            if action == "act":
                if not body.snapshot_id or not body.ref or not body.op:
                    raise HTTPException(status_code=400, detail="snapshot_id, ref, and op are required for act")
                return runtime.act(body.snapshot_id, body.ref, body.op, body.text)
            if action == "coordinate_act":
                if not body.op:
                    raise HTTPException(status_code=400, detail="op is required for coordinate_act")
                return runtime.coordinate_act(
                    op=body.op,
                    x=body.x,
                    y=body.y,
                    x1=body.x1,
                    y1=body.y1,
                    x2=body.x2,
                    y2=body.y2,
                    duration_ms=body.duration_ms,
                    text=body.text,
                    approved=body.approved,
                )
            if action == "wait":
                if not body.wait_for:
                    raise HTTPException(status_code=400, detail="wait_for is required for wait")
                return runtime.wait(body.wait_for, body.wait_value, body.timeout_ms)
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/v1/admin/dispatch")
    def admin_dispatch(body: AdminDispatchRequest) -> dict[str, Any]:
        try:
            action = body.action
            if action == "doctor":
                return runtime.doctor()
            if action == "waydroid_start":
                return runtime.waydroid.start()
            if action == "waydroid_stop":
                return runtime.waydroid.stop()
            if action == "recover":
                return runtime.recover(mode=body.mode or "user", approved=body.approved)
            if action == "app_install":
                if not body.apk_path:
                    raise HTTPException(status_code=400, detail="apk_path is required for app_install")
                return runtime.install_apk(body.apk_path, approved=body.approved)
            if action == "app_install_url":
                if not body.apk_url:
                    raise HTTPException(status_code=400, detail="apk_url is required for app_install_url")
                return runtime.install_apk_url(body.apk_url, approved=body.approved)
            if action == "store_install":
                if not body.package and not body.query:
                    raise HTTPException(status_code=400, detail="package or query is required for store_install")
                return runtime.store_install(
                    store=body.store or "aptoide",
                    package=body.package,
                    query=body.query,
                    limit=body.limit,
                    approved=body.approved,
                )
            if action == "app_remove":
                if not body.package:
                    raise HTTPException(status_code=400, detail="package is required for app_remove")
                return runtime.remove_app(body.package, approved=body.approved)
            if action == "default_stores_install":
                return runtime.install_default_stores(body.stores, approved=body.approved)
            if action == "device_profile_apply":
                return runtime.apply_device_profile(approved=body.approved)
            if action == "bridge_configure":
                return runtime.configure_bridge(body.allowed_packages or [])
            if action in {"extras_install", "extras_uninstall"}:
                return runtime.manage_extras(
                    body.extras or [],
                    uninstall=(action == "extras_uninstall"),
                    approved=body.approved,
                )
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app
