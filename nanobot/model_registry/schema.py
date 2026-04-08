"""Static registry schema for runtime-native model resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class ModelRegistryError(ValueError):
    """Schema/load error for registry payloads."""


class ModelRegistrySemanticError(ModelRegistryError):
    """Semantic validation error raised during resolution."""


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _parse_str_dict(value: Any, *, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ModelRegistryError(f"{label} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        key_text = _clean_text(key)
        if key_text is None:
            raise ModelRegistryError(f"{label} contains an empty key")
        result[key_text] = "" if item is None else str(item)
    return result


def _parse_capabilities(value: Any, *, label: str) -> dict[str, bool]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ModelRegistryError(f"{label} must be a mapping")
    result: dict[str, bool] = {}
    for key, item in value.items():
        key_text = _clean_text(key)
        if key_text is None:
            raise ModelRegistryError(f"{label} contains an empty key")
        result[key_text] = _coerce_bool(item, default=False)
    return result


def _parse_runtime_defaults(value: Any, *, label: str) -> dict[str, int | float | str | None]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ModelRegistryError(f"{label} must be a mapping")
    result: dict[str, int | float | str | None] = {}
    for key, item in value.items():
        key_text = _clean_text(key)
        if key_text is None:
            raise ModelRegistryError(f"{label} contains an empty key")
        key_name = key_text.casefold()
        if key_name not in {"temperature", "max_tokens", "reasoning_effort"}:
            raise ModelRegistryError(f"{label} unsupported runtime_defaults key '{key_text}'")
        if item is None:
            result[key_name] = None
            continue
        if key_name == "temperature":
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ModelRegistryError(
                    f"{label} runtime_defaults key 'temperature' must be numeric"
                )
            result[key_name] = item
            continue
        if key_name == "max_tokens":
            if isinstance(item, bool) or not isinstance(item, int):
                raise ModelRegistryError(
                    f"{label} runtime_defaults key 'max_tokens' must be an integer"
                )
            result[key_name] = item
            continue
        text = _clean_text(item)
        if text is None:
            raise ModelRegistryError(
                f"{label} runtime_defaults key 'reasoning_effort' must be a string"
            )
        result[key_name] = text
    return result


def _merge_aliases(base: tuple[str, ...], extra: Any) -> tuple[str, ...]:
    if extra is None:
        return base
    if isinstance(extra, str):
        items = [extra]
    elif isinstance(extra, (list, tuple)):
        items = list(extra)
    else:
        raise ModelRegistryError("aliases must be a list, tuple, or string")

    seen = {alias.casefold() for alias in base}
    merged = list(base)
    for item in items:
        alias = _clean_text(item)
        if alias is None:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        merged.append(alias)
        seen.add(key)
    return tuple(merged)


@dataclass(frozen=True, slots=True)
class ProfileDefault:
    ref: str


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    config_provider_ref: str
    adapter: str
    api_base_override: str | None = None
    extra_headers_override: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    family: str
    tier: str
    effort: str
    route_ref: str
    provider_model: str
    aliases: tuple[str, ...] = ()
    enabled: bool = True
    template: bool = False
    extends: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    runtime_defaults: dict[str, int | float | str | None] = field(default_factory=dict)
    protocol: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedModelSpec:
    profile: str
    model_id: str
    family: str
    tier: str
    effort: str
    route_name: str
    config_provider_ref: str
    adapter: str
    protocol: str
    provider_model: str
    api_base: str | None
    extra_headers: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    runtime_defaults: dict[str, int | float | str | None] = field(default_factory=dict)


def _merge_model_payload(
    base: dict[str, Any], raw: Mapping[str, Any], *, model_id: str
) -> dict[str, Any]:
    effective = dict(base)

    for field_name in ("family", "tier", "effort", "route_ref", "provider_model"):
        if field_name in raw:
            value = _clean_text(raw.get(field_name))
            if value is None:
                raise ModelRegistryError(f"Model '{model_id}' field '{field_name}' cannot be empty")
            effective[field_name] = value
        elif field_name not in effective:
            raise ModelRegistryError(f"Model '{model_id}' is missing required field '{field_name}'")

    if "protocol" in raw:
        effective["protocol"] = _clean_text(raw.get("protocol"))
    elif "protocol" not in effective:
        effective["protocol"] = None

    if "enabled" in raw:
        effective["enabled"] = _coerce_bool(raw.get("enabled"), default=True)
    else:
        effective["enabled"] = True

    if "template" in raw:
        effective["template"] = _coerce_bool(raw.get("template"), default=False)
    else:
        effective["template"] = False

    if "extends" in raw:
        effective["extends"] = _clean_text(raw.get("extends"))
    elif "extends" not in effective:
        effective["extends"] = None

    if "aliases" in raw:
        effective["aliases"] = _merge_aliases(tuple(), raw.get("aliases"))
    else:
        effective.setdefault("aliases", tuple())

    if "capabilities" in raw:
        merged = dict(effective.get("capabilities", {}))
        merged.update(
            _parse_capabilities(raw.get("capabilities"), label=f"model '{model_id}' capabilities")
        )
        effective["capabilities"] = merged
    else:
        effective.setdefault("capabilities", {})

    if "runtime_defaults" in raw:
        merged_runtime = dict(effective.get("runtime_defaults", {}))
        merged_runtime.update(
            _parse_runtime_defaults(
                raw.get("runtime_defaults"), label=f"model '{model_id}' runtime_defaults"
            )
        )
        effective["runtime_defaults"] = merged_runtime
    else:
        effective.setdefault("runtime_defaults", {})

    return effective


def _resolve_model_effective_payload(
    model_id: str,
    raw_models: Mapping[str, Any],
    cache: dict[str, dict[str, Any]],
    stack: list[str],
) -> dict[str, Any]:
    if model_id in cache:
        return cache[model_id]
    if model_id in stack:
        cycle = " -> ".join(stack + [model_id])
        raise ModelRegistryError(f"Cycle detected in model inheritance: {cycle}")

    raw = raw_models.get(model_id)
    if not isinstance(raw, Mapping):
        raise ModelRegistryError(f"Model '{model_id}' must be a mapping")

    stack.append(model_id)
    parent_payload: dict[str, Any] = {}
    parent_ref = _clean_text(raw.get("extends"))
    if parent_ref:
        if parent_ref not in raw_models:
            raise ModelRegistryError(f"Model '{model_id}' extends missing model '{parent_ref}'")
        parent_payload = _resolve_model_effective_payload(parent_ref, raw_models, cache, stack)
    stack.pop()

    effective = _merge_model_payload(parent_payload, raw, model_id=model_id)
    cache[model_id] = effective
    return effective


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    version: int
    profile_defaults: dict[str, ProfileDefault] = field(default_factory=dict)
    routes: dict[str, RouteDefinition] = field(default_factory=dict)
    models: dict[str, ModelDefinition] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | ModelRegistry) -> ModelRegistry:
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise ModelRegistryError("Registry payload must be a mapping")

        version_raw = payload.get("version")
        try:
            version = int(version_raw)
        except (TypeError, ValueError) as exc:
            raise ModelRegistryError("Registry version must be an integer") from exc
        if version != 2:
            raise ModelRegistryError(f"Unsupported registry version: {version}")

        profile_defaults_raw = payload.get("profile_defaults")
        routes_raw = payload.get("routes")
        models_raw = payload.get("models")

        if not isinstance(profile_defaults_raw, Mapping):
            raise ModelRegistryError("profile_defaults must be a mapping")
        if not isinstance(routes_raw, Mapping):
            raise ModelRegistryError("routes must be a mapping")
        if not isinstance(models_raw, Mapping):
            raise ModelRegistryError("models must be a mapping")

        profile_defaults: dict[str, ProfileDefault] = {}
        for profile_name, raw in profile_defaults_raw.items():
            profile_key = _clean_text(profile_name)
            if profile_key is None:
                raise ModelRegistryError("profile_defaults contains an empty profile name")
            if isinstance(raw, str):
                ref = _clean_text(raw)
                if ref is None:
                    raise ModelRegistryError(f"Profile '{profile_key}' default ref cannot be empty")
                profile_defaults[profile_key] = ProfileDefault(ref=ref)
                continue
            if not isinstance(raw, Mapping):
                raise ModelRegistryError(f"Profile '{profile_key}' must be a mapping")
            ref = _clean_text(raw.get("ref"))
            if ref is None:
                raise ModelRegistryError(f"Profile '{profile_key}' default ref is required")
            profile_defaults[profile_key] = ProfileDefault(ref=ref)

        routes: dict[str, RouteDefinition] = {}
        for route_name, raw in routes_raw.items():
            route_key = _clean_text(route_name)
            if route_key is None:
                raise ModelRegistryError("routes contains an empty route name")
            if not isinstance(raw, Mapping):
                raise ModelRegistryError(f"Route '{route_key}' must be a mapping")
            config_provider_ref = _clean_text(raw.get("config_provider_ref"))
            adapter = _clean_text(raw.get("adapter"))
            if config_provider_ref is None:
                raise ModelRegistryError(f"Route '{route_key}' config_provider_ref is required")
            if adapter is None:
                raise ModelRegistryError(f"Route '{route_key}' adapter is required")
            routes[route_key] = RouteDefinition(
                config_provider_ref=config_provider_ref,
                adapter=adapter,
                api_base_override=_clean_text(raw.get("api_base_override")),
                extra_headers_override=_parse_str_dict(
                    raw.get("extra_headers_override"),
                    label=f"route '{route_key}' extra_headers_override",
                ),
            )

        resolved_payloads: dict[str, dict[str, Any]] = {}
        models: dict[str, ModelDefinition] = {}
        for model_id in models_raw:
            model_key = _clean_text(model_id)
            if model_key is None:
                raise ModelRegistryError("models contains an empty model id")
            effective = _resolve_model_effective_payload(
                model_key, models_raw, resolved_payloads, []
            )
            models[model_key] = ModelDefinition(
                family=effective["family"],
                tier=effective["tier"],
                effort=effective["effort"],
                route_ref=effective["route_ref"],
                provider_model=effective["provider_model"],
                aliases=tuple(effective.get("aliases", ())),
                enabled=bool(effective.get("enabled", True)),
                template=bool(effective.get("template", False)),
                extends=effective.get("extends"),
                capabilities=dict(effective.get("capabilities", {})),
                runtime_defaults=dict(effective.get("runtime_defaults", {})),
                protocol=_clean_text(effective.get("protocol")),
            )

        return cls(
            version=version,
            profile_defaults=profile_defaults,
            routes=routes,
            models=models,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "profile_defaults": {
                profile: {"ref": default.ref} for profile, default in self.profile_defaults.items()
            },
            "routes": {
                route: {
                    "config_provider_ref": definition.config_provider_ref,
                    "adapter": definition.adapter,
                    **(
                        {"api_base_override": definition.api_base_override}
                        if definition.api_base_override is not None
                        else {}
                    ),
                    **(
                        {"extra_headers_override": dict(definition.extra_headers_override)}
                        if definition.extra_headers_override
                        else {}
                    ),
                }
                for route, definition in self.routes.items()
            },
            "models": {
                model_id: {
                    "family": definition.family,
                    "tier": definition.tier,
                    "effort": definition.effort,
                    "route_ref": definition.route_ref,
                    "provider_model": definition.provider_model,
                    **({"aliases": list(definition.aliases)} if definition.aliases else {}),
                    **({"enabled": definition.enabled} if not definition.enabled else {}),
                    **({"template": definition.template} if definition.template else {}),
                    **({"extends": definition.extends} if definition.extends else {}),
                    **(
                        {"capabilities": dict(definition.capabilities)}
                        if definition.capabilities
                        else {}
                    ),
                    **(
                        {"runtime_defaults": dict(definition.runtime_defaults)}
                        if definition.runtime_defaults
                        else {}
                    ),
                    **({"protocol": definition.protocol} if definition.protocol else {}),
                }
                for model_id, definition in self.models.items()
            },
        }


__all__ = [
    "ModelDefinition",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelRegistrySemanticError",
    "ProfileDefault",
    "ResolvedModelSpec",
    "RouteDefinition",
]
