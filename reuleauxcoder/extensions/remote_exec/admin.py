"""Admin helpers for the remote relay HTTP service."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from reuleauxcoder.domain.config.models import (
    ModelProfileConfig,
    ProviderCapabilities,
    ProviderConfig,
    infer_provider_compat,
)
from reuleauxcoder.infrastructure.yaml.loader import load_yaml_config, save_yaml_config
from reuleauxcoder.services.config.loader import ConfigLoader
from reuleauxcoder.services.providers.manager import ProviderManager


ProviderTestHandler = Callable[[ProviderConfig, str, str], dict[str, Any]]
ProviderModelsHandler = Callable[[ProviderConfig], dict[str, Any]]
ConfigReloadHandler = Callable[[], None]


@dataclass(slots=True)
class AdminConfigResult:
    ok: bool
    payload: dict[str, Any]
    status: int = 200


class RemoteAdminConfigManager:
    """Read and update host-owned provider and model-profile config."""

    def __init__(
        self,
        config_path: Path | None = None,
        *,
        reload_handler: ConfigReloadHandler | None = None,
        provider_test_handler: ProviderTestHandler | None = None,
        provider_models_handler: ProviderModelsHandler | None = None,
    ) -> None:
        self.config_path = config_path or ConfigLoader.GLOBAL_CONFIG_PATH
        self.reload_handler = reload_handler
        self.provider_test_handler = provider_test_handler
        self.provider_models_handler = provider_models_handler
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "providers": self.list_providers()["providers"],
            "model_profiles": self.list_model_profiles()["model_profiles"],
            "active_main": self._models_data().get("active_main"),
            "active_sub": self._models_data().get("active_sub"),
        }

    def list_providers(self) -> dict[str, Any]:
        data = self._load_data()
        raw_items = (((data.get("providers") or {}).get("items")) or {})
        providers = []
        if isinstance(raw_items, dict):
            for provider_id in sorted(raw_items):
                item = raw_items.get(provider_id)
                if not isinstance(item, dict):
                    continue
                provider = ProviderConfig.from_dict(str(provider_id), item)
                provider_dict = provider.to_dict()
                provider_dict.pop("api_key", None)
                provider_dict["api_key_hint"] = _mask(str(item.get("api_key", "") or ""))
                provider_dict["id"] = provider.id
                providers.append(provider_dict)
        return {"providers": providers}

    def record_provider(self, payload: dict[str, Any]) -> AdminConfigResult:
        provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
        if not provider_id:
            return AdminConfigResult(False, {"error": "provider_id_required"}, 400)
        if payload.get("api_key") and payload.get("api_key_env"):
            return AdminConfigResult(False, {"error": "api_key_conflict"}, 400)
        if payload.get("base_url") and payload.get("base_url_env"):
            return AdminConfigResult(False, {"error": "base_url_conflict"}, 400)

        with self._lock:
            previous_data = self._load_data()
            data = deepcopy(previous_data)
            items = self._provider_items(data)
            previous = items.get(provider_id, {}) if isinstance(items.get(provider_id), dict) else {}
            provider_type = str(payload.get("type") or previous.get("type") or "openai_chat")
            base_url = _field_or_env(payload, "base_url", "base_url_env")
            if base_url is None:
                base_url = previous.get("base_url")
            api_key = _field_or_env(payload, "api_key", "api_key_env")
            if api_key is None:
                api_key = previous.get("api_key", "")
            provider_data = {
                "type": provider_type,
                "compat": payload.get("compat")
                or previous.get("compat")
                or infer_provider_compat(str(base_url or "")),
                "enabled": _bool_field(payload, "enabled", previous.get("enabled", True)),
                "api_key": str(api_key or ""),
                "base_url": base_url,
                "headers": _dict_field(payload, "headers", previous),
                "timeout_sec": int(payload.get("timeout_sec") or previous.get("timeout_sec") or 120),
                "max_retries": int(payload.get("max_retries") or previous.get("max_retries") or 3),
                "capabilities": ProviderCapabilities.from_dict(
                    _dict_field(payload, "capabilities", previous),
                    provider_type=provider_type,
                ).to_dict(),
                "extra": _dict_field(payload, "extra", previous),
            }
            provider = ProviderConfig.from_dict(provider_id, provider_data)
            items[provider_id] = provider.to_dict()
            reload_error = self._commit_config(data, previous_data)
            if reload_error:
                return reload_error
            return AdminConfigResult(
                True,
                {
                    "ok": True,
                    "provider": self._provider_view(provider_id, items[provider_id]),
                    "created": not previous,
                },
            )

    def test_provider(self, payload: dict[str, Any]) -> AdminConfigResult:
        provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
        model = str(payload.get("model") or "").strip()
        prompt = str(payload.get("prompt") or "ping")
        if not provider_id:
            return AdminConfigResult(False, {"error": "provider_id_required"}, 400)
        if not model:
            return AdminConfigResult(False, {"error": "model_required"}, 400)
        try:
            provider = self._expanded_provider(provider_id)
            if provider is None:
                return AdminConfigResult(False, {"error": "provider_not_found"}, 404)
            if self.provider_test_handler is not None:
                result = self.provider_test_handler(provider, model, prompt)
            else:
                response = ProviderManager().create(
                    provider, allow_disabled=True
                ).test(model=model, prompt=prompt)
                preview = response.content.strip().replace("\n", " ")
                if len(preview) > 200:
                    preview = preview[:197] + "..."
                result = {
                    "ok": True,
                    "provider_id": provider.id,
                    "model": model,
                    "tokens": response.prompt_tokens + response.completion_tokens,
                    "response": preview,
                }
        except Exception as exc:
            return AdminConfigResult(False, {"error": "provider_test_failed", "message": str(exc)}, 500)
        return AdminConfigResult(True, result)

    def delete_provider(self, payload: dict[str, Any]) -> AdminConfigResult:
        provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
        if not provider_id:
            return AdminConfigResult(False, {"error": "provider_id_required"}, 400)

        with self._lock:
            previous_data = self._load_data()
            data = deepcopy(previous_data)
            items = self._provider_items(data)
            if provider_id not in items:
                return AdminConfigResult(False, {"error": "provider_not_found"}, 404)
            blockers = self._provider_profile_blockers(data, provider_id)
            if blockers:
                return AdminConfigResult(
                    False,
                    {
                        "error": "provider_in_use",
                        "provider_id": provider_id,
                        "blockers": blockers,
                    },
                    409,
                )
            del items[provider_id]
            reload_error = self._commit_config(data, previous_data)
            if reload_error:
                return reload_error
            return AdminConfigResult(True, {"ok": True, "provider_id": provider_id})

    def copy_provider(self, payload: dict[str, Any]) -> AdminConfigResult:
        provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
        target_id = str(payload.get("target_id") or "").strip()
        if not provider_id:
            return AdminConfigResult(False, {"error": "provider_id_required"}, 400)

        with self._lock:
            previous_data = self._load_data()
            data = deepcopy(previous_data)
            items = self._provider_items(data)
            source = items.get(provider_id)
            if not isinstance(source, dict):
                return AdminConfigResult(False, {"error": "provider_not_found"}, 404)
            if target_id and target_id in items:
                return AdminConfigResult(False, {"error": "provider_exists"}, 409)
            new_id = target_id or self._unique_provider_copy_id(items, provider_id)
            copied = deepcopy(source)
            copied["enabled"] = True
            provider = ProviderConfig.from_dict(new_id, copied)
            items[new_id] = provider.to_dict()
            reload_error = self._commit_config(data, previous_data)
            if reload_error:
                return reload_error
            return AdminConfigResult(
                True,
                {
                    "ok": True,
                    "provider": self._provider_view(new_id, items[new_id]),
                    "copied_from": provider_id,
                },
            )

    def enable_provider(self, payload: dict[str, Any]) -> AdminConfigResult:
        provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
        if not provider_id:
            return AdminConfigResult(False, {"error": "provider_id_required"}, 400)
        enabled = _bool_field(payload, "enabled", True)

        with self._lock:
            previous_data = self._load_data()
            data = deepcopy(previous_data)
            items = self._provider_items(data)
            item = items.get(provider_id)
            if not isinstance(item, dict):
                return AdminConfigResult(False, {"error": "provider_not_found"}, 404)
            item["enabled"] = enabled
            provider = ProviderConfig.from_dict(provider_id, item)
            items[provider_id] = provider.to_dict()
            reload_error = self._commit_config(data, previous_data)
            if reload_error:
                return reload_error
            return AdminConfigResult(
                True,
                {
                    "ok": True,
                    "provider": self._provider_view(provider_id, items[provider_id]),
                },
            )

    def list_provider_models(self, payload: dict[str, Any]) -> AdminConfigResult:
        provider_id = str(payload.get("provider_id") or payload.get("id") or "").strip()
        if not provider_id:
            return AdminConfigResult(False, {"error": "provider_id_required"}, 400)
        try:
            provider = self._expanded_provider(provider_id)
            if provider is None:
                return AdminConfigResult(False, {"error": "provider_not_found"}, 404)
            if self.provider_models_handler is not None:
                result = self.provider_models_handler(provider)
            else:
                result = ProviderManager().list_models(provider)
        except Exception as exc:
            return AdminConfigResult(
                False, {"error": "provider_models_failed", "message": str(exc)}, 500
            )
        return AdminConfigResult(True, result)

    def list_model_profiles(self) -> dict[str, Any]:
        models = self._models_data()
        raw_profiles = models.get("profiles", {})
        profiles = []
        if isinstance(raw_profiles, dict):
            for profile_id in sorted(raw_profiles):
                item = raw_profiles.get(profile_id)
                if not isinstance(item, dict):
                    continue
                profile = ModelProfileConfig.from_dict(str(profile_id), item)
                profile_dict = profile.to_dict()
                profile_dict.pop("api_key", None)
                profile_dict["api_key_hint"] = _mask(str(item.get("api_key", "") or ""))
                profile_dict["id"] = profile.name
                profiles.append(profile_dict)
        return {
            "model_profiles": profiles,
            "active_main": models.get("active_main"),
            "active_sub": models.get("active_sub"),
        }

    def record_model_profile(self, payload: dict[str, Any]) -> AdminConfigResult:
        profile_id = str(payload.get("profile_id") or payload.get("id") or "").strip()
        if not profile_id:
            return AdminConfigResult(False, {"error": "profile_id_required"}, 400)
        if payload.get("api_key") and payload.get("api_key_env"):
            return AdminConfigResult(False, {"error": "api_key_conflict"}, 400)

        with self._lock:
            previous_data = self._load_data()
            data = deepcopy(previous_data)
            profiles = self._model_profiles(data)
            previous = profiles.get(profile_id, {}) if isinstance(profiles.get(profile_id), dict) else {}
            api_key = _field_or_env(payload, "api_key", "api_key_env")
            if api_key is None:
                api_key = previous.get("api_key", "")
            profile_data = {
                "model": str(payload.get("model") or previous.get("model") or "gpt-4o"),
                "api_key": str(api_key or ""),
                "provider": payload.get("provider", previous.get("provider")),
                "base_url": payload.get("base_url", previous.get("base_url")),
                "max_tokens": int(payload.get("max_tokens") or previous.get("max_tokens") or 4096),
                "temperature": float(payload.get("temperature") if payload.get("temperature") is not None else previous.get("temperature", 0.0)),
                "max_context_tokens": int(payload.get("max_context_tokens") or previous.get("max_context_tokens") or 128000),
                "preserve_reasoning_content": bool(payload.get("preserve_reasoning_content", previous.get("preserve_reasoning_content", True))),
                "backfill_reasoning_content_for_tool_calls": bool(payload.get("backfill_reasoning_content_for_tool_calls", previous.get("backfill_reasoning_content_for_tool_calls", False))),
                "reasoning_effort": payload.get("reasoning_effort", previous.get("reasoning_effort")),
                "thinking_enabled": payload.get("thinking_enabled", previous.get("thinking_enabled")),
                "reasoning_replay_mode": payload.get("reasoning_replay_mode", previous.get("reasoning_replay_mode")),
                "reasoning_replay_placeholder": payload.get("reasoning_replay_placeholder", previous.get("reasoning_replay_placeholder")),
            }
            profile = ModelProfileConfig.from_dict(profile_id, profile_data)
            profiles[profile_id] = profile.to_dict()
            reload_error = self._commit_config(data, previous_data)
            if reload_error:
                return reload_error
            return AdminConfigResult(
                True,
                {
                    "ok": True,
                    "model_profile": self._profile_view(profile_id, profiles[profile_id]),
                    "created": not previous,
                },
            )

    def activate_model_profile(self, payload: dict[str, Any]) -> AdminConfigResult:
        profile_id = str(payload.get("profile_id") or payload.get("id") or "").strip()
        target = str(payload.get("target") or "main").strip().lower()
        if not profile_id:
            return AdminConfigResult(False, {"error": "profile_id_required"}, 400)
        if target not in {"main", "sub", "both"}:
            return AdminConfigResult(False, {"error": "invalid_target"}, 400)
        with self._lock:
            previous_data = self._load_data()
            data = deepcopy(previous_data)
            models = data.setdefault("models", {})
            if not isinstance(models, dict):
                models = {}
                data["models"] = models
            profiles = models.setdefault("profiles", {})
            if not isinstance(profiles, dict) or profile_id not in profiles:
                return AdminConfigResult(False, {"error": "profile_not_found"}, 404)
            if target in {"main", "both"}:
                models["active_main"] = profile_id
                models["active"] = profile_id
            if target in {"sub", "both"}:
                models["active_sub"] = profile_id
            profile_data = profiles.get(profile_id)
            if isinstance(profile_data, dict) and profile_data.get("provider"):
                provider_id = str(profile_data.get("provider"))
                provider_item = self._provider_items(data).get(provider_id)
                if isinstance(provider_item, dict):
                    provider = ProviderConfig.from_dict(provider_id, provider_item)
                    if not provider.enabled:
                        return AdminConfigResult(
                            False,
                            {
                                "error": "provider_disabled",
                                "provider_id": provider_id,
                            },
                            409,
                        )
            reload_error = self._commit_config(data, previous_data)
            if reload_error:
                return reload_error
            return AdminConfigResult(
                True,
                {"ok": True, "active_main": models.get("active_main"), "active_sub": models.get("active_sub")},
            )

    def _reload(self) -> AdminConfigResult | None:
        if self.reload_handler is None:
            return None
        try:
            self.reload_handler()
        except Exception as exc:
            return AdminConfigResult(False, {"error": "config_reload_failed", "message": str(exc)}, 500)
        return None

    def _commit_config(
        self, data: dict[str, Any], previous_data: dict[str, Any]
    ) -> AdminConfigResult | None:
        save_yaml_config(self.config_path, data)
        reload_error = self._reload()
        if reload_error is None:
            return None
        save_yaml_config(self.config_path, previous_data)
        self._reload()
        return reload_error

    def _load_data(self) -> dict[str, Any]:
        try:
            data = load_yaml_config(self.config_path)
        except FileNotFoundError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _expanded_provider(self, provider_id: str) -> ProviderConfig | None:
        data = self._load_data()
        expanded = ConfigLoader()._expand_env_refs(data)
        raw = (((expanded.get("providers") or {}).get("items")) or {}).get(provider_id)
        if not isinstance(raw, dict):
            return None
        return ProviderConfig.from_dict(provider_id, raw)

    def _provider_items(self, data: dict[str, Any]) -> dict[str, Any]:
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            data["providers"] = providers
        items = providers.setdefault("items", {})
        if not isinstance(items, dict):
            items = {}
            providers["items"] = items
        return items

    def _provider_profile_blockers(
        self, data: dict[str, Any], provider_id: str
    ) -> list[dict[str, Any]]:
        models = data.get("models", {})
        if not isinstance(models, dict):
            return []
        profiles = models.get("profiles", {})
        if not isinstance(profiles, dict):
            return []
        blockers: list[dict[str, Any]] = []
        active_main = models.get("active_main")
        active_sub = models.get("active_sub")
        for profile_id, profile_data in sorted(profiles.items()):
            if not isinstance(profile_data, dict):
                continue
            if str(profile_data.get("provider") or "") != provider_id:
                continue
            blockers.append(
                {
                    "profile_id": str(profile_id),
                    "active_main": profile_id == active_main,
                    "active_sub": profile_id == active_sub,
                }
            )
        return blockers

    def _unique_provider_copy_id(
        self, items: dict[str, Any], provider_id: str
    ) -> str:
        base = f"{provider_id}-copy"
        if base not in items:
            return base
        index = 2
        while f"{base}-{index}" in items:
            index += 1
        return f"{base}-{index}"

    def _models_data(self) -> dict[str, Any]:
        data = self._load_data()
        models = data.get("models", {})
        return models if isinstance(models, dict) else {}

    def _model_profiles(self, data: dict[str, Any]) -> dict[str, Any]:
        models = data.setdefault("models", {})
        if not isinstance(models, dict):
            models = {}
            data["models"] = models
        profiles = models.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            models["profiles"] = profiles
        return profiles

    def _provider_view(self, provider_id: str, item: dict[str, Any]) -> dict[str, Any]:
        provider = ProviderConfig.from_dict(provider_id, item)
        view = provider.to_dict()
        view.pop("api_key", None)
        view["api_key_hint"] = _mask(str(item.get("api_key", "") or ""))
        view["id"] = provider_id
        return view

    def _profile_view(self, profile_id: str, item: dict[str, Any]) -> dict[str, Any]:
        profile = ModelProfileConfig.from_dict(profile_id, item)
        view = profile.to_dict()
        view.pop("api_key", None)
        view["api_key_hint"] = _mask(str(item.get("api_key", "") or ""))
        view["id"] = profile_id
        return view


def _field_or_env(payload: dict[str, Any], field_name: str, env_field_name: str) -> str | None:
    if env_field_name in payload and payload.get(env_field_name):
        return "${" + str(payload[env_field_name]).strip() + "}"
    if field_name in payload:
        value = payload.get(field_name)
        return str(value) if value is not None else ""
    return None


def _dict_field(payload: dict[str, Any], field_name: str, previous: dict[str, Any]) -> dict[str, Any]:
    value = payload.get(field_name, previous.get(field_name, {}))
    return dict(value) if isinstance(value, dict) else {}


def _bool_field(payload: dict[str, Any], field_name: str, default: Any) -> bool:
    if field_name not in payload:
        return bool(default)
    value = payload.get(field_name)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _mask(value: str) -> str:
    if not value:
        return "(empty)"
    if value.startswith("${") and value.endswith("}"):
        return value
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
