"""Resolve registry refs into runtime-ready model specs."""

from __future__ import annotations

from typing import Any

from nanobot.model_registry.schema import (
    ModelRegistry,
    ModelRegistryError,
    ModelRegistrySemanticError,
    ResolvedModelSpec,
)
from nanobot.providers.registry import find_by_name, normalize_lookup_name

_ADAPTER_BACKENDS: dict[str, frozenset[str]] = {
    "openai_compat": frozenset({"openai_compat"}),
    "anthropic": frozenset({"anthropic"}),
    "azure_openai": frozenset({"azure_openai"}),
    "openai_responses": frozenset({"openai_compat"}),
    "openai_codex": frozenset({"openai_codex"}),
}

_ADAPTER_PROTOCOLS: dict[str, str] = {
    "openai_compat": "chat_completions",
    "anthropic": "anthropic_messages",
    "azure_openai": "chat_completions",
    "openai_responses": "responses",
    "openai_codex": "responses",
}

_ADAPTER_NAMES = {
    normalize_lookup_name(name): name for name in _ADAPTER_BACKENDS
}

_PROFILE_REQUIRED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "chat": ("tool_calls",),
    "compact": ("compact", "tool_calls"),
    "subagent": ("subagent", "tool_calls"),
    "archive": ("archive",),
}


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _normalize_profile(profile: str | None) -> str:
    return (_clean_text(profile) or "chat").casefold()


def _required_capabilities(profile: str) -> tuple[str, ...]:
    return _PROFILE_REQUIRED_CAPABILITIES.get(profile.casefold(), ())


def _normalize_adapter_name(adapter: str) -> str:
    return _ADAPTER_NAMES.get(normalize_lookup_name(adapter), adapter.casefold())


class RegistryResolver:
    """Resolve profile defaults and explicit refs against a registry."""

    def __init__(self, registry: ModelRegistry):
        self.registry = registry
        self._profile_lookup = {
            profile.casefold(): profile for profile in registry.profile_defaults
        }
        self._model_lookup = {model_id.casefold(): model_id for model_id in registry.models}
        self._family_lookup: dict[str, list[str]] = {}
        self._alias_lookup: dict[str, str] = {}

        for model_id, model in registry.models.items():
            self._family_lookup.setdefault(model.family.casefold(), []).append(model_id)
            for alias in model.aliases:
                alias_key = alias.casefold()
                existing = self._alias_lookup.get(alias_key)
                if existing and existing != model_id:
                    raise ModelRegistryError(
                        f"Alias '{alias}' is defined on both '{existing}' and '{model_id}'"
                    )
                if alias_key in self._model_lookup and self._model_lookup[alias_key] != model_id:
                    raise ModelRegistryError(
                        f"Alias '{alias}' collides with model id '{self._model_lookup[alias_key]}'"
                    )
                self._alias_lookup[alias_key] = model_id

    def resolve_profile(self, profile: str) -> ResolvedModelSpec:
        profile_name = _normalize_profile(profile)
        actual_profile = self._profile_lookup.get(profile_name)
        if actual_profile is None:
            raise ModelRegistrySemanticError(f"Unknown profile '{profile_name}'")

        ref = self.registry.profile_defaults[actual_profile].ref
        return self.resolve_ref(ref, profile_hint=actual_profile)

    def resolve_ref(self, ref: str, profile_hint: str = "chat") -> ResolvedModelSpec:
        profile = _normalize_profile(profile_hint)
        cleaned_ref = _clean_text(ref)
        if cleaned_ref is None:
            raise ModelRegistrySemanticError("Model ref cannot be empty")

        selector_key, selector_value = self._parse_selector(cleaned_ref)
        if selector_key == "family":
            return self._resolve_family_selector(selector_value, profile)

        exact_model_id = self._lookup_exact_model_id(cleaned_ref)
        if exact_model_id is not None:
            return self._resolve_model_id(exact_model_id, profile)

        family_key = cleaned_ref.casefold()
        if family_key in self._family_lookup:
            return self._resolve_family_selector(cleaned_ref, profile)

        raise ModelRegistrySemanticError(f"Unknown model ref '{cleaned_ref}'")

    def _resolve_family_selector(self, family: str, profile: str) -> ResolvedModelSpec:
        family_name = _clean_text(family)
        if family_name is None:
            raise ModelRegistrySemanticError("Family selector cannot be empty")

        family_key = family_name.casefold()
        candidate_ids = list(self._family_lookup.get(family_key, []))
        if not candidate_ids:
            raise ModelRegistrySemanticError(f"No models found for family '{family_name}'")

        preferred = self._profile_default_model_id(profile)
        if preferred and preferred in candidate_ids:
            candidate_ids = [preferred] + [
                model_id for model_id in candidate_ids if model_id != preferred
            ]

        last_error: ModelRegistrySemanticError | None = None
        for model_id in candidate_ids:
            try:
                return self._resolve_model_id(model_id, profile)
            except ModelRegistrySemanticError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise ModelRegistrySemanticError(f"No valid models found for family '{family_name}'")

    def _resolve_model_id(self, model_id: str, profile: str) -> ResolvedModelSpec:
        model = self.registry.models.get(model_id)
        if model is None:
            raise ModelRegistrySemanticError(f"Unknown model '{model_id}'")
        if not model.enabled:
            raise ModelRegistrySemanticError(f"Model '{model_id}' is disabled")
        if model.template:
            raise ModelRegistrySemanticError(
                f"Model '{model_id}' is a template and cannot be selected"
            )

        route = self.registry.routes.get(model.route_ref)
        if route is None:
            raise ModelRegistrySemanticError(
                f"Model '{model_id}' references missing route '{model.route_ref}'"
            )

        provider_spec = find_by_name(route.config_provider_ref)
        if provider_spec is None:
            raise ModelRegistrySemanticError(
                f"Route '{model.route_ref}' references unknown provider config '{route.config_provider_ref}'"
            )

        adapter_name = _normalize_adapter_name(route.adapter)

        expected_backends = _ADAPTER_BACKENDS.get(adapter_name)
        if expected_backends is None:
            raise ModelRegistrySemanticError(
                f"Route '{model.route_ref}' uses unsupported adapter '{route.adapter}'"
            )
        if provider_spec.backend not in expected_backends:
            raise ModelRegistrySemanticError(
                f"Route '{model.route_ref}' adapter '{route.adapter}' is incompatible with provider '{route.config_provider_ref}'"
            )

        if adapter_name == "openai_responses" and not provider_spec.supports_responses_api:
            raise ModelRegistrySemanticError(
                f"Route '{model.route_ref}' adapter '{route.adapter}' is incompatible with provider '{route.config_provider_ref}'"
            )

        expected_protocol = _ADAPTER_PROTOCOLS.get(adapter_name)
        if expected_protocol is None:
            raise ModelRegistrySemanticError(
                f"Route '{model.route_ref}' uses unsupported adapter '{route.adapter}'"
            )
        actual_protocol = model.protocol or expected_protocol
        if actual_protocol != expected_protocol:
            raise ModelRegistrySemanticError(
                f"Model '{model_id}' protocol '{actual_protocol}' is incompatible with adapter '{route.adapter}'"
            )

        missing_capabilities = [
            capability
            for capability in _required_capabilities(profile)
            if not bool(model.capabilities.get(capability))
        ]
        if missing_capabilities:
            required = ", ".join(missing_capabilities)
            raise ModelRegistrySemanticError(
                f"Model '{model_id}' is missing capability '{required}' for profile '{profile}'"
            )

        return ResolvedModelSpec(
            profile=profile,
            model_id=model_id,
            family=model.family,
            tier=model.tier,
            effort=model.effort,
            route_name=model.route_ref,
            config_provider_ref=provider_spec.name,
            adapter=adapter_name,
            protocol=actual_protocol,
            provider_model=model.provider_model,
            api_base=route.api_base_override,
            extra_headers=dict(route.extra_headers_override),
            capabilities=dict(model.capabilities),
            runtime_defaults=dict(model.runtime_defaults),
        )

    def _lookup_exact_model_id(self, ref: str) -> str | None:
        cleaned = ref.casefold()
        if cleaned in self._model_lookup:
            return self._model_lookup[cleaned]
        return self._alias_lookup.get(cleaned)

    def _profile_default_model_id(self, profile: str) -> str | None:
        actual_profile = self._profile_lookup.get(profile.casefold())
        if actual_profile is None:
            return None
        default_ref = self.registry.profile_defaults[actual_profile].ref
        return self._lookup_exact_model_id(default_ref)

    @staticmethod
    def _parse_selector(ref: str) -> tuple[str | None, str]:
        if "=" not in ref:
            return None, ref
        key, value = ref.split("=", 1)
        key_text = _clean_text(key)
        value_text = _clean_text(value) or ""
        if key_text is None:
            return None, ref
        return key_text.casefold(), value_text


__all__ = [
    "ModelRegistryError",
    "ModelRegistrySemanticError",
    "RegistryResolver",
    "ResolvedModelSpec",
]
