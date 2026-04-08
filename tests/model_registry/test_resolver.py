from __future__ import annotations

from copy import deepcopy

import pytest

from nanobot.config.schema import Config
from nanobot.model_registry.provider_factory import build_provider_from_spec
from nanobot.model_registry.resolver import (
    ModelRegistrySemanticError,
    RegistryResolver,
)
from nanobot.model_registry.schema import ModelRegistry, ResolvedModelSpec


def _base_registry_payload() -> dict[str, object]:
    return {
        "version": 2,
        "profile_defaults": {
            "chat": {"ref": "standard-gpt-5.4-high-tokenx"},
            "archive": {"ref": "archive-gpt-4.1-mini"},
            "compact": {"ref": "compact-gpt-5.4-high-tokenx"},
            "subagent": {"ref": "subagent-gpt-5.4-high-tokenx"},
        },
        "routes": {
            "tokenx": {
                "config_provider_ref": "custom",
                "adapter": "openai_compat",
                "api_base_override": "https://tokenx24.com/v1",
                "extra_headers_override": {"X-Route": "tokenx"},
            },
            "responses": {
                "config_provider_ref": "openai",
                "adapter": "openai_responses",
            },
        },
        "models": {
            "gpt-5.4-template": {
                "family": "gpt-5.4",
                "tier": "standard",
                "effort": "high",
                "route_ref": "tokenx",
                "provider_model": "gpt-5.4",
                "enabled": True,
                "template": True,
                "capabilities": {"tool_calls": True},
                "runtime_defaults": {"temperature": 0.2, "max_tokens": 8192},
                "protocol": "chat_completions",
            },
            "standard-gpt-5.4-high-tokenx": {
                "extends": "gpt-5.4-template",
                "effort": "high",
                "aliases": ["gpt-5.4-high"],
                "runtime_defaults": {"temperature": 0.25},
                "capabilities": {"chat": True},
                "protocol": "chat_completions",
            },
            "compact-gpt-5.4-high-tokenx": {
                "extends": "standard-gpt-5.4-high-tokenx",
                "aliases": ["gpt-5.4-compact"],
                "runtime_defaults": {"temperature": 0.3},
                "capabilities": {"compact": True},
                "protocol": "chat_completions",
            },
            "subagent-gpt-5.4-high-tokenx": {
                "extends": "standard-gpt-5.4-high-tokenx",
                "aliases": ["gpt-5.4-subagent"],
                "runtime_defaults": {"temperature": 0.35},
                "capabilities": {"subagent": True},
                "protocol": "chat_completions",
            },
            "archive-gpt-4.1-mini": {
                "family": "gpt-4.1",
                "tier": "standard",
                "effort": "high",
                "route_ref": "responses",
                "provider_model": "gpt-4.1-mini",
                "aliases": ["archive-mini"],
                "enabled": True,
                "template": False,
                "capabilities": {"archive": True, "tool_calls": True},
                "runtime_defaults": {"temperature": 0.1, "max_tokens": 4096},
            },
            "disabled-gpt-5.4-high-tokenx": {
                "extends": "standard-gpt-5.4-high-tokenx",
                "enabled": False,
                "aliases": ["disabled-chat"],
            },
        },
    }


def _registry(payload: dict[str, object] | None = None) -> ModelRegistry:
    return ModelRegistry.from_dict(payload or _base_registry_payload())


@pytest.fixture
def registry() -> ModelRegistry:
    return _registry()


@pytest.fixture
def registry_without_archive_capability() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    archive_model = payload["models"]["archive-gpt-4.1-mini"]
    archive_model["capabilities"].pop("archive")
    return _registry(payload)


def _registry_without_tool_call_capability(profile: str) -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    model_id = {
        "compact": "compact-gpt-5.4-high-tokenx",
        "subagent": "subagent-gpt-5.4-high-tokenx",
    }[profile]
    payload["models"][model_id]["capabilities"]["tool_calls"] = False
    return _registry(payload)


def _registry_missing_profile_capability(profile: str) -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    model_id = {
        "archive": "archive-gpt-4.1-mini",
        "compact": "compact-gpt-5.4-high-tokenx",
        "subagent": "subagent-gpt-5.4-high-tokenx",
    }[profile]
    payload["models"][model_id]["capabilities"].pop(profile)
    return _registry(payload)


@pytest.fixture
def registry_with_bad_route() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    payload["routes"]["bad-route"] = {
        "config_provider_ref": "anthropic",
        "adapter": "openai_compat",
    }
    payload["models"]["compact-gpt-5.4-high-tokenx"]["route_ref"] = "bad-route"
    return _registry(payload)


@pytest.fixture
def registry_with_bad_protocol() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    payload["models"]["archive-gpt-4.1-mini"]["protocol"] = "chat_completions"
    return _registry(payload)


@pytest.fixture
def registry_with_codex_responses_route() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    payload["routes"]["responses"]["config_provider_ref"] = "openai_codex"
    return _registry(payload)


@pytest.fixture
def registry_with_mixed_case_adapter() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    payload["routes"]["responses"]["adapter"] = "OpenAI_Responses"
    return _registry(payload)


@pytest.fixture
def registry_with_mixed_case_provider_ref() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    payload["routes"]["responses"]["config_provider_ref"] = "OPENAI"
    return _registry(payload)


@pytest.fixture
def registry_with_disabled_profile_default() -> ModelRegistry:
    payload = deepcopy(_base_registry_payload())
    payload["profile_defaults"]["chat"]["ref"] = "disabled-gpt-5.4-high-tokenx"
    return _registry(payload)


@pytest.fixture
def registry_with_disabled_explicit_ref() -> ModelRegistry:
    return _registry()


def test_resolver_returns_archive_and_compact_as_distinct_profiles(registry: ModelRegistry) -> None:
    resolver = RegistryResolver(registry)

    archive_spec = resolver.resolve_profile("archive")
    compact_spec = resolver.resolve_profile("compact")

    assert archive_spec.profile == "archive"
    assert compact_spec.profile == "compact"
    assert archive_spec.model_id == "archive-gpt-4.1-mini"
    assert compact_spec.model_id == "compact-gpt-5.4-high-tokenx"
    assert archive_spec.model_id != compact_spec.model_id
    assert archive_spec.protocol == "responses"
    assert compact_spec.protocol == "chat_completions"


@pytest.mark.parametrize(
    ("profile", "capability"),
    [
        ("archive", "archive"),
        ("compact", "compact"),
        ("subagent", "subagent"),
    ],
)
def test_resolver_rejects_profile_when_required_capability_is_missing(
    profile: str,
    capability: str,
) -> None:
    resolver = RegistryResolver(_registry_missing_profile_capability(profile))

    with pytest.raises(ModelRegistrySemanticError, match=capability):
        resolver.resolve_profile(profile)


@pytest.mark.parametrize("profile", ["compact", "subagent"])
def test_resolver_rejects_profile_when_tool_calls_are_missing(profile: str) -> None:
    resolver = RegistryResolver(_registry_without_tool_call_capability(profile))

    with pytest.raises(ModelRegistrySemanticError, match="tool_calls"):
        resolver.resolve_profile(profile)


def test_resolver_rejects_route_provider_incompatibility(
    registry_with_bad_route: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_bad_route)

    with pytest.raises(ModelRegistrySemanticError, match="provider"):
        resolver.resolve_profile("compact")


def test_resolver_rejects_adapter_protocol_mismatch(
    registry_with_bad_protocol: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_bad_protocol)

    with pytest.raises(ModelRegistrySemanticError, match="protocol"):
        resolver.resolve_profile("archive")


def test_resolver_rejects_oauth_only_codex_backend_for_generic_openai_responses_route(
    registry_with_codex_responses_route: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_codex_responses_route)

    with pytest.raises(ModelRegistrySemanticError, match="incompatible"):
        resolver.resolve_profile("archive")


def test_resolver_rejects_openai_responses_route_for_non_responses_capable_provider() -> None:
    payload = deepcopy(_base_registry_payload())
    payload["routes"]["responses"]["config_provider_ref"] = "ollama"
    resolver = RegistryResolver(_registry(payload))

    with pytest.raises(ModelRegistrySemanticError, match="incompatible"):
        resolver.resolve_profile("archive")


def test_resolver_rejects_disabled_profile_default(
    registry_with_disabled_profile_default: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_disabled_profile_default)

    with pytest.raises(ModelRegistrySemanticError, match="disabled"):
        resolver.resolve_profile("chat")


def test_resolver_rejects_disabled_explicit_ref(
    registry_with_disabled_explicit_ref: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_disabled_explicit_ref)

    with pytest.raises(ModelRegistrySemanticError, match="disabled"):
        resolver.resolve_ref("disabled-gpt-5.4-high-tokenx", profile_hint="chat")


def test_resolver_supports_alias_family_and_extends(registry: ModelRegistry) -> None:
    resolver = RegistryResolver(registry)

    alias_spec = resolver.resolve_ref("gpt-5.4-high", profile_hint="chat")
    family_spec = resolver.resolve_ref("family=gpt-5.4", profile_hint="chat")
    compact_spec = resolver.resolve_ref("compact-gpt-5.4-high-tokenx", profile_hint="compact")

    assert alias_spec.model_id == "standard-gpt-5.4-high-tokenx"
    assert family_spec.model_id == "standard-gpt-5.4-high-tokenx"
    assert compact_spec.model_id == "compact-gpt-5.4-high-tokenx"
    assert compact_spec.runtime_defaults["temperature"] == 0.3
    assert compact_spec.runtime_defaults["max_tokens"] == 8192
    assert compact_spec.capabilities["tool_calls"] is True
    assert compact_spec.capabilities["compact"] is True
    assert compact_spec.route_name == "tokenx"


def test_build_provider_from_spec_accepts_valid_openai_responses_archive_spec(
    registry: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry)
    spec = resolver.resolve_profile("archive")
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": {
                "openai": {
                    "apiKey": "sk-test-openai",
                    "apiBase": "https://api.openai.com/v1",
                }
            },
        }
    )

    provider, model = build_provider_from_spec(spec, config)

    assert model == spec.provider_model
    assert provider.get_default_model() == spec.provider_model
    assert provider.__class__.__name__ == "OpenAIResponsesProvider"


def test_build_provider_from_spec_prefers_route_api_base_over_provider_config_api_base(
    registry: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry)
    spec = resolver.resolve_ref("standard-gpt-5.4-high-tokenx", profile_hint="chat")
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": {
                "custom": {
                    "apiBase": "https://provider.example/v1",
                }
            },
        }
    )

    provider, _ = build_provider_from_spec(spec, config)

    assert provider.api_base == "https://tokenx24.com/v1"


def test_build_provider_from_spec_uses_provider_config_api_base_when_route_override_is_absent(
    registry: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry)
    spec = resolver.resolve_profile("archive")
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": {
                "openai": {
                    "apiKey": "sk-test-openai",
                    "apiBase": "https://provider.example/v1",
                }
            },
        }
    )

    provider, _ = build_provider_from_spec(spec, config)

    assert provider.api_base == "https://provider.example/v1"


def test_build_provider_from_spec_uses_provider_registry_default_api_base_and_applies_generation_defaults() -> None:
    spec = ResolvedModelSpec(
        profile="chat",
        model_id="ollama-gpt-4.1-mini",
        family="gpt-4.1",
        tier="standard",
        effort="high",
        route_name="local",
        config_provider_ref="ollama",
        adapter="openai_compat",
        protocol="chat_completions",
        provider_model="gpt-4.1-mini",
        api_base=None,
        runtime_defaults={"temperature": 0.25, "max_tokens": 2048},
    )
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "model": "unused",
                    "temperature": 0.5,
                    "max_tokens": 1024,
                    "reasoning_effort": "low",
                }
            }
        }
    )

    provider, _ = build_provider_from_spec(spec, config)

    assert provider.api_base == "http://localhost:11434/v1"
    assert provider.generation.temperature == 0.25
    assert provider.generation.max_tokens == 2048
    assert provider.generation.reasoning_effort == "low"


def test_build_provider_from_spec_accepts_mixed_case_adapter_values(
    registry_with_mixed_case_adapter: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_mixed_case_adapter)
    spec = resolver.resolve_profile("archive")
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": {
                "openai": {
                    "apiKey": "sk-test-openai",
                    "apiBase": "https://api.openai.com/v1",
                }
            },
        }
    )

    provider, model = build_provider_from_spec(spec, config)

    assert model == "gpt-4.1-mini"
    assert provider.__class__.__name__ == "OpenAIResponsesProvider"


def test_build_provider_from_spec_accepts_mixed_case_config_provider_ref(
    registry_with_mixed_case_provider_ref: ModelRegistry,
) -> None:
    resolver = RegistryResolver(registry_with_mixed_case_provider_ref)
    spec = resolver.resolve_profile("archive")
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": {
                "openai": {
                    "apiKey": "sk-test-openai",
                    "apiBase": "https://api.openai.com/v1",
                }
            },
        }
    )

    provider, model = build_provider_from_spec(spec, config)

    assert spec.config_provider_ref == "openai"
    assert model == "gpt-4.1-mini"
    assert provider.__class__.__name__ == "OpenAIResponsesProvider"


@pytest.mark.parametrize(
    (
        "route_key",
        "route_updates",
        "profile",
        "provider_config",
        "expected_provider_ref",
        "expected_provider_class",
    ),
    [
        (
            "responses",
            {"config_provider_ref": "OpenAI"},
            "archive",
            {
                "openai": {
                    "apiKey": "sk-test-openai",
                    "apiBase": "https://api.openai.com/v1",
                }
            },
            "openai",
            "OpenAIResponsesProvider",
        ),
        (
            "tokenx",
            {
                "config_provider_ref": "AzureOpenAI",
                "adapter": "azure_openai",
                "api_base_override": "https://example.azure.openai/v1",
            },
            "chat",
            {
                "azure_openai": {
                    "apiKey": "sk-test-azure",
                    "apiBase": "https://example.azure.openai/v1",
                }
            },
            "azure_openai",
            "AzureOpenAIProvider",
        ),
        (
            "responses",
            {
                "config_provider_ref": "OpenAICodex",
                "adapter": "openai_codex",
            },
            "archive",
            {},
            "openai_codex",
            "OpenAICodexProvider",
        ),
    ],
)
def test_build_provider_from_spec_accepts_camel_case_provider_refs(
    route_key: str,
    route_updates: dict[str, object],
    profile: str,
    provider_config: dict[str, object],
    expected_provider_ref: str,
    expected_provider_class: str,
) -> None:
    payload = deepcopy(_base_registry_payload())
    payload["routes"][route_key].update(route_updates)

    resolver = RegistryResolver(_registry(payload))
    spec = resolver.resolve_profile(profile)
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": provider_config,
        }
    )

    provider, model = build_provider_from_spec(spec, config)

    assert spec.config_provider_ref == expected_provider_ref
    assert model == spec.provider_model
    assert provider.get_default_model() == spec.provider_model
    assert provider.__class__.__name__ == expected_provider_class


@pytest.mark.parametrize("adapter", ["OpenAIResponses", "openai-responses"])
def test_build_provider_from_spec_accepts_openai_responses_adapter_spellings(
    adapter: str,
) -> None:
    payload = deepcopy(_base_registry_payload())
    payload["routes"]["responses"]["adapter"] = adapter

    resolver = RegistryResolver(_registry(payload))
    spec = resolver.resolve_profile("archive")
    config = Config.model_validate(
        {
            "agents": {"defaults": {"model": "unused"}},
            "providers": {
                "openai": {
                    "apiKey": "sk-test-openai",
                    "apiBase": "https://api.openai.com/v1",
                }
            },
        }
    )

    provider, model = build_provider_from_spec(spec, config)

    assert model == spec.provider_model
    assert provider.get_default_model() == spec.provider_model
    assert provider.__class__.__name__ == "OpenAIResponsesProvider"
