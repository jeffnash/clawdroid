from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings


class LlmDecisionError(RuntimeError):
    pass


_REMOTE_MODEL_CACHE: dict[str, dict[str, Any]] = {}


GENERIC_LABELS = {
    "",
    "linear layout",
    "frame layout",
    "relative layout",
    "view group",
    "content parent",
    "button panel",
    "recycler view",
    "scroll view",
    "scrollview",
    "widget frame",
    "main content",
    "group",
}


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _is_generic_label(value: Any) -> bool:
    return _normalize_label(value).casefold() in GENERIC_LABELS


def _extract_json_block(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    candidate = re.search(r"\{.*\}", stripped, re.DOTALL)
    if candidate:
        data = json.loads(candidate.group(0))
        if isinstance(data, dict):
            return data
    raise LlmDecisionError("Model response did not contain valid JSON.")


def _extract_text_content(raw: dict[str, Any]) -> tuple[str, str | None]:
    choice = ((raw.get("choices") or [{}])[0].get("message") or {})
    content = choice.get("content")
    reasoning = choice.get("reasoning")
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in text_parts if part), str(reasoning or "") or None
    return str(content or ""), str(reasoning or "") or None


def _resolve_candidate_ref(raw_obj: dict[str, Any]) -> tuple[str | None, str | None]:
    if isinstance(raw_obj.get("action"), dict):
        action_obj = raw_obj.get("action") or {}
        return action_obj.get("ref") or action_obj.get("target"), action_obj.get("label")
    return raw_obj.get("ref") or raw_obj.get("target"), raw_obj.get("label")


def _resolve_candidate_decision(raw_obj: dict[str, Any]) -> str | None:
    decision = raw_obj.get("decision")
    if not decision and isinstance(raw_obj.get("action"), str):
        decision = raw_obj.get("action")
    if not decision and isinstance(raw_obj.get("action"), dict):
        decision = (raw_obj.get("action") or {}).get("action") or (raw_obj.get("action") or {}).get("decision")
    if not decision and (_resolve_candidate_ref(raw_obj)[0] or raw_obj.get("target")):
        decision = "click"
    if not decision:
        return None
    lowered = str(decision).strip().lower()
    aliases = {
        "tap": "click",
        "press": "click",
        "choose": "click",
        "select": "click",
    }
    return aliases.get(lowered, lowered)


def _resolve_reason(raw_obj: dict[str, Any], fallback_reasoning: str | None) -> str:
    if isinstance(raw_obj.get("action"), dict):
        action_obj = raw_obj.get("action") or {}
        for key in ("reason", "reasoning", "why"):
            value = action_obj.get(key)
            if value:
                return str(value)
    for key in ("reason", "reasoning", "why"):
        value = raw_obj.get(key)
        if value:
            return str(value)
    return str(fallback_reasoning or "").strip()


def _resolve_confidence(raw_obj: dict[str, Any], fallback: float) -> float:
    value = raw_obj.get("confidence")
    if isinstance(raw_obj.get("action"), dict) and value is None:
        value = (raw_obj.get("action") or {}).get("confidence")
    try:
        if value is not None:
            return max(0.0, min(1.0, float(value)))
    except Exception:  # noqa: BLE001
        pass
    return fallback


class AndroidLlmDecider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _package_default_provider() -> str:
        return "openrouter"

    @staticmethod
    def _package_default_model() -> str:
        return "bytedance/ui-tars-1.5-7b"

    @staticmethod
    def _load_json_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            return {}
        return payload if isinstance(payload, dict) else {}

    def _legacy_llm_settings_paths(self) -> tuple[Path, ...]:
        legacy_base = Path.home() / ".pi" / "agent"
        return (legacy_base / "settings.json",)

    def _legacy_llm_models_paths(self) -> tuple[Path, ...]:
        legacy_base = Path.home() / ".pi" / "agent"
        return (legacy_base / "models.json",)

    def _load_first_available_json(self, *paths: Path) -> tuple[dict[str, Any], Path | None]:
        for path in paths:
            payload = self._load_json_file(path)
            if payload:
                return payload, path
        return {}, None

    def _load_provider_configs(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        config_obj = self._load_json_file(self.settings.llm_config_path)
        settings_obj, _ = self._load_first_available_json(
            self.settings.llm_settings_path,
            *self._legacy_llm_settings_paths(),
        )
        models_obj, _ = self._load_first_available_json(
            self.settings.llm_models_path,
            *self._legacy_llm_models_paths(),
        )
        return config_obj, settings_obj, models_obj

    @staticmethod
    def _config_providers(config_obj: dict[str, Any]) -> dict[str, Any]:
        providers = config_obj.get("providers") or {}
        return providers if isinstance(providers, dict) else {}

    @staticmethod
    def _provider_models(provider: dict[str, Any]) -> list[dict[str, Any]]:
        models = provider.get("models") or []
        return [model for model in models if isinstance(model, dict)]

    @staticmethod
    def _provider_env_names(provider: dict[str, Any]) -> list[str]:
        raw = provider.get("api_key_env") or provider.get("apiKeyEnv") or ""
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return [item.strip() for item in str(raw).split(",") if item.strip()]

    @classmethod
    def _provider_api_key(cls, provider: dict[str, Any], explicit_key: str | None) -> str:
        if explicit_key:
            return explicit_key
        for env_name in cls._provider_env_names(provider):
            env_value = os.environ.get(env_name)
            if env_value:
                return env_value
        return str(provider.get("api_key") or provider.get("apiKey") or "")

    @staticmethod
    def _provider_base_url(provider: dict[str, Any], explicit_base: str | None) -> str:
        if explicit_base:
            return explicit_base.rstrip("/")
        return str(provider.get("base_url") or provider.get("baseUrl") or "").rstrip("/")

    @classmethod
    def _fetch_remote_models(cls, *, base_url: str, api_key: str) -> dict[str, Any]:
        cache_key = base_url.rstrip("/")
        cached = _REMOTE_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        req = urllib.request.Request(
            f"{cache_key}/models",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = json.load(response)
        except Exception:  # noqa: BLE001
            payload = {}
        index: dict[str, Any] = {}
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            for key in (
                item.get("id"),
                item.get("name"),
                item.get("display_name"),
                item.get("canonical_slug"),
            ):
                token = str(key or "").strip()
                if token:
                    index[token] = item
        _REMOTE_MODEL_CACHE[cache_key] = index
        return index

    @classmethod
    def _remote_model_info(cls, *, base_url: str, api_key: str, model_name: str | None) -> dict[str, Any]:
        if not base_url or not api_key or not model_name:
            return {}
        catalog = cls._fetch_remote_models(base_url=base_url, api_key=api_key)
        if model_name in catalog:
            return dict(catalog[model_name])
        lowered = model_name.casefold()
        for key, item in catalog.items():
            if lowered == str(key).casefold():
                return dict(item)
        for key, item in catalog.items():
            if lowered in str(key).casefold() or str(key).casefold() in lowered:
                return dict(item)
        return {}

    def _resolve_provider(
        self,
        *,
        provider_name: str | None = None,
        model_name: str | None = None,
        fetch_remote_models: bool = True,
    ) -> dict[str, Any]:
        config_obj, settings_obj, models_obj = self._load_provider_configs()
        providers: dict[str, Any] = {}
        providers.update((models_obj.get("providers") or {}) if isinstance(models_obj, dict) else {})
        providers.update(self._config_providers(config_obj))
        explicit_base = self.settings.llm_base_url
        explicit_key = self.settings.llm_api_key

        chosen_provider_name = (
            provider_name
            or self.settings.llm_provider
            or config_obj.get("default_provider")
            or config_obj.get("defaultProvider")
            or settings_obj.get("defaultProvider")
            or self._package_default_provider()
            or "cliproxy"
        )
        chosen_model = (
            model_name
            or self.settings.llm_model
            or config_obj.get("default_model")
            or config_obj.get("defaultModel")
            or settings_obj.get("defaultModel")
            or self._package_default_model()
        )

        if chosen_model and not provider_name:
            for name, provider in providers.items():
                for model in self._provider_models(provider):
                    if chosen_model in {model.get("id"), model.get("name")}:
                        chosen_provider_name = name
                        break
                if chosen_provider_name == name:
                    break

        provider = providers.get(chosen_provider_name)
        if not provider and not explicit_base:
            fallback_provider_name = (
                settings_obj.get("defaultProvider")
                or ("cliproxy" if "cliproxy" in providers else None)
                or next(iter(providers.keys()), None)
            )
            if fallback_provider_name:
                chosen_provider_name = str(fallback_provider_name)
                provider = providers.get(chosen_provider_name)
                if not model_name and not self.settings.llm_model and not config_obj.get("default_model") and not config_obj.get("defaultModel"):
                    chosen_model = settings_obj.get("defaultModel") or chosen_model
        if not provider and not explicit_base:
            searched = ", ".join(
                str(path)
                for path in (self.settings.llm_models_path, *self._legacy_llm_models_paths())
            )
            raise LlmDecisionError(f"Provider {chosen_provider_name!r} not found in any configured model catalog ({searched}).")

        if provider:
            selected_model = None
            if chosen_model:
                for model in self._provider_models(provider):
                    if chosen_model in {model.get("id"), model.get("name")}:
                        selected_model = model
                        break
            models = self._provider_models(provider)
            if not selected_model and models:
                selected_model = models[0]
            chosen_model = chosen_model or (selected_model or {}).get("id") or (selected_model or {}).get("name")
            base_url = self._provider_base_url(provider, explicit_base)
            api_key = self._provider_api_key(provider, explicit_key)
            if not base_url or not api_key or not chosen_model:
                raise LlmDecisionError(f"Provider {chosen_provider_name!r} is missing baseUrl, apiKey, or model.")
            model_info = dict(selected_model or {})
            if not model_info and fetch_remote_models:
                model_info = self._remote_model_info(base_url=base_url, api_key=api_key, model_name=chosen_model)
            return {
                "provider": chosen_provider_name,
                "base_url": base_url,
                "api_key": api_key,
                "model": chosen_model,
                "api": provider.get("api") or "openai-completions",
                "model_info": model_info,
            }

        if explicit_base and explicit_key:
            model_info = {}
            if fetch_remote_models:
                model_info = self._remote_model_info(base_url=explicit_base.rstrip("/"), api_key=explicit_key, model_name=chosen_model)
            return {
                "provider": chosen_provider_name,
                "base_url": explicit_base.rstrip("/"),
                "api_key": explicit_key,
                "model": chosen_model or "unknown-model",
                "api": "openai-completions",
                "model_info": model_info,
            }

        raise LlmDecisionError("No LLM provider configuration is available.")

    def config_status(self) -> dict[str, Any]:
        try:
            provider = self._resolve_provider(fetch_remote_models=False)
        except LlmDecisionError as exc:
            return {
                "configured": False,
                "error": str(exc),
                "config_path": str(self.settings.llm_config_path),
                "models_path": str(self.settings.llm_models_path),
                "settings_path": str(self.settings.llm_settings_path),
            }
        return {
            "configured": True,
            "provider": provider.get("provider"),
            "model": provider.get("model"),
            "base_url": provider.get("base_url"),
            "supports_images": self._model_supports_images(provider),
            "config_path": str(self.settings.llm_config_path),
        }

    @staticmethod
    def _model_supports_images(provider: dict[str, Any]) -> bool:
        model_info = provider.get("model_info", {}) or {}
        input_types = model_info.get("input") or model_info.get("input_modalities") or []
        if isinstance(model_info.get("architecture"), dict):
            input_types = input_types or model_info.get("architecture", {}).get("input_modalities") or []
            modality = str(model_info.get("architecture", {}).get("modality") or "").casefold()
            if "image" in modality or "vision" in modality:
                return True
        if any(str(item).casefold() in {"image", "vision"} for item in input_types):
            return True
        model_name = " ".join(
            str(part or "")
            for part in (
                provider.get("model"),
                model_info.get("id"),
                model_info.get("name"),
                model_info.get("display_name"),
            )
        ).casefold()
        return any(
            token in model_name
            for token in ("ui-tars", "vl", "vision", "pixtral", "internvl", "seed", "omni", "screen")
        )

    @staticmethod
    def _build_image_part(path: str | None) -> dict[str, Any] | None:
        if not path:
            return None
        image_path = Path(path)
        if not image_path.exists():
            return None
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{encoded}",
            },
        }

    @staticmethod
    def summarize_snapshot(snapshot: dict[str, Any], *, max_refs: int = 20) -> dict[str, Any]:
        top_refs = list(snapshot.get("top_refs") or [])
        refs = list(snapshot.get("refs") or [])
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in top_refs + refs:
            ref = str(item.get("ref") or "")
            if not ref or ref in seen:
                continue
            label = item.get("semantic_label") or item.get("text") or item.get("content_desc") or item.get("resource_id")
            role = item.get("role")
            actions = item.get("actions") or []
            resource_id = item.get("resource_id")
            package = item.get("package")
            confidence = item.get("confidence_score")
            keep = bool(actions)
            if role in {"text", "checkbox"} and label and not _is_generic_label(label):
                keep = True
            if resource_id in {"android:id/alertTitle", "android:id/message", "android:id/switch_widget"}:
                keep = True
            if not keep:
                continue
            selected.append(
                {
                    "ref": ref,
                    "label": label,
                    "role": role,
                    "actions": actions,
                    "resource_id": resource_id,
                    "package": package,
                    "confidence": confidence,
                }
            )
            seen.add(ref)
            if len(selected) >= max_refs:
                break

        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "current_app": snapshot.get("current_app"),
            "screen_context": snapshot.get("screen_context"),
            "top_refs": [
                {
                    "ref": item.get("ref"),
                    "label": item.get("semantic_label") or item.get("text") or item.get("content_desc") or item.get("resource_id"),
                    "role": item.get("role"),
                    "actions": item.get("actions"),
                    "resource_id": item.get("resource_id"),
                    "package": item.get("package"),
                    "confidence": item.get("confidence_score"),
                }
                for item in top_refs[:8]
            ],
            "refs": selected,
            "screenshot_path": snapshot.get("screenshot_path"),
        }

    @staticmethod
    def _build_prompts(
        *,
        goal: str,
        summary: dict[str, Any],
        mode: str,
        heuristic_guidance: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        screenshot_hint = (
            "Use the screenshot to resolve visual ownership and spatial ambiguity."
            if mode == "vision"
            else "Rely on the structured refs only; do not assume any unseen visual relationship."
        )
        system_prompt = (
            "You are an Android UI action selector for a Waydroid automation agent. "
            "Choose exactly one next step from the provided refs only. "
            "Optimize for forward task progress, not generic exploration. "
            "Never choose Back, Cancel, Home, Overview, Close, Dismiss, or another backward/destructive path "
            "if a forward-progress action exists. "
            "Prefer the foreground app over SystemUI overlays. "
            "Do not click decorative headers, app identity cards, toolbar icons, or share buttons when a setting row or confirmation button better advances the goal. "
            "On settings screens, if a labeled row owns a switch widget, choose the row container when that is the real tappable control. "
            "If the required setting already appears enabled, returning to the previous step can be the correct forward action. "
            "Differentiate between entity headers/app cards and actual setting rows or confirmation buttons. "
            "Heuristic guidance may be provided below; treat it as a hint, not a command. "
            f"{screenshot_hint} "
            "Return JSON only."
        )
        guidance_block = ""
        if heuristic_guidance:
            guidance_block = (
                "\n\nHeuristic guidance:\n"
                f"{json.dumps(heuristic_guidance, ensure_ascii=False, indent=2)}"
                "\nUse this to understand the daemon's ranked guess, but you must still reason from the refs and choose the best action yourself."
            )
        user_prompt = (
            f"Task goal: {goal}\n\n"
            "Pick exactly one next action from the refs in this snapshot.\n"
            "Allowed decisions: click, wait, stop.\n\n"
            f"{json.dumps(summary, ensure_ascii=False, indent=2)}"
            f"{guidance_block}"
        )
        return system_prompt, user_prompt

    def _request_chat_completion(
        self,
        *,
        provider: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        screenshot_path: str | None,
        mode: str,
    ) -> tuple[dict[str, Any], list[str], bool]:
        warnings: list[str] = []
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        supports_images = self._model_supports_images(provider)
        image_part = self._build_image_part(screenshot_path)
        image_used = False
        if mode == "vision" and image_part and supports_images:
            content.append(image_part)
            image_used = True
        elif mode == "vision" and image_part and not supports_images:
            warnings.append("Selected model does not advertise image input; falling back to text-only LLM.")
        payload = {
            "model": provider["model"],
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ui_action_decision",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "decision": {"type": "string", "enum": ["click", "wait", "stop"]},
                            "ref": {"type": ["string", "null"]},
                            "label": {"type": ["string", "null"]},
                            "confidence": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["decision", "ref", "label", "confidence", "reason"],
                    },
                },
            },
        }
        req = urllib.request.Request(
            f"{provider['base_url']}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {provider['api_key']}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.settings.llm_timeout_s) as response:
                return json.load(response), warnings, image_used
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {400, 404, 415, 422}:
                warnings.append(f"Provider rejected json_schema response_format; retrying without schema ({exc.code}).")
                payload.pop("response_format", None)
                req = urllib.request.Request(
                    f"{provider['base_url']}/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {provider['api_key']}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.settings.llm_timeout_s) as response:
                    return json.load(response), warnings, image_used
            raise LlmDecisionError(f"LLM request failed: HTTP {exc.code}: {body}") from exc

    @staticmethod
    def normalize_model_decision(
        *,
        raw: dict[str, Any],
        summary: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        content_text, reasoning = _extract_text_content(raw)
        try:
            parsed = _extract_json_block(content_text)
        except Exception as exc:  # noqa: BLE001
            ref_match = re.search(r"\ba\d+\b", content_text)
            decision = "wait" if "wait" in content_text.lower() else "click"
            parsed = {
                "decision": decision,
                "ref": ref_match.group(0) if ref_match else None,
                "label": None,
                "confidence": 0.5,
                "reason": str(exc),
            }
            warnings.append("LLM response was not valid JSON; used regex fallback parsing.")

        ref, label = _resolve_candidate_ref(parsed)
        decision = _resolve_candidate_decision(parsed)
        if not decision:
            raise LlmDecisionError("LLM response did not specify a decision.")

        ref_map = {str(item.get("ref")): item for item in summary.get("refs", [])}
        ref_map.update({str(item.get("ref")): item for item in summary.get("top_refs", [])})
        if ref and ref not in ref_map:
            raise LlmDecisionError(f"LLM chose unknown ref {ref!r}.")
        if decision == "click" and not ref:
            raise LlmDecisionError("LLM chose click without a ref.")

        fallback_confidence = 0.0
        if ref and ref in ref_map:
            try:
                fallback_confidence = float(ref_map[ref].get("confidence") or 0.0)
            except Exception:  # noqa: BLE001
                fallback_confidence = 0.0
            label = label or ref_map[ref].get("label")

        normalized = {
            "decision": decision,
            "ref": ref,
            "label": label,
            "confidence": _resolve_confidence(parsed, fallback_confidence),
            "reason": _resolve_reason(parsed, reasoning),
        }
        if isinstance(parsed.get("action"), dict) or "reasoning" in parsed or "action" in parsed or "target" in parsed:
            warnings.append("LLM response shape was normalized from a non-schema variant.")
        return normalized, warnings

    def decide(
        self,
        *,
        snapshot: dict[str, Any],
        goal: str,
        mode: str,
        provider_name: str | None = None,
        model_name: str | None = None,
        max_refs: int | None = None,
        heuristic_guidance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider = self._resolve_provider(provider_name=provider_name, model_name=model_name)
        summary = self.summarize_snapshot(snapshot, max_refs=max_refs or self.settings.llm_max_refs)
        system_prompt, user_prompt = self._build_prompts(
            goal=goal,
            summary=summary,
            mode=mode,
            heuristic_guidance=heuristic_guidance,
        )
        started = time.time()
        raw, request_warnings, image_used = self._request_chat_completion(
            provider=provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            screenshot_path=summary.get("screenshot_path"),
            mode=mode,
        )
        normalized, parse_warnings = self.normalize_model_decision(raw=raw, summary=summary)
        return {
            "provider": provider["provider"],
            "model": provider["model"],
            "mode": mode,
            "used_screenshot": image_used,
            "snapshot_id": summary.get("snapshot_id"),
            "decision": normalized,
            "warnings": request_warnings + parse_warnings,
            "latency_ms": int((time.time() - started) * 1000),
            "heuristic_guidance": heuristic_guidance,
            "raw": raw,
        }
