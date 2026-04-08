from __future__ import annotations

import pytest

from nanobot.model_registry.schema import ModelRegistryError
from nanobot.model_registry.store import ModelRegistryStore


def test_registry_v2_requires_profile_defaults_routes_and_models(tmp_path) -> None:
    store = ModelRegistryStore(tmp_path / "model_registry.json")

    store.save(
        {
            "version": 2,
            "profile_defaults": {
                "chat": {"ref": "standard-gpt-5.4-high-tokenx"},
            },
            "routes": {
                "tokenx": {
                    "config_provider_ref": "custom",
                    "adapter": "openai_compat",
                    "api_base_override": "https://tokenx24.com/v1",
                    "extra_headers_override": {"X-Route": "tokenx"},
                }
            },
            "models": {
                "standard-gpt-5.4-high-tokenx": {
                    "family": "gpt-5.4",
                    "tier": "standard",
                    "effort": "high",
                    "route_ref": "tokenx",
                    "provider_model": "gpt-5.4",
                    "aliases": ["gpt-5.4-high"],
                    "enabled": True,
                    "template": False,
                    "capabilities": {"tool_calls": True},
                    "runtime_defaults": {"temperature": 0.2, "max_tokens": 8192},
                }
            },
        }
    )

    loaded = store.load()

    assert loaded.version == 2
    assert loaded.profile_defaults["chat"].ref == "standard-gpt-5.4-high-tokenx"
    assert loaded.routes["tokenx"].adapter == "openai_compat"
    assert loaded.routes["tokenx"].extra_headers_override["X-Route"] == "tokenx"
    assert loaded.models["standard-gpt-5.4-high-tokenx"].provider_model == "gpt-5.4"


def test_registry_v2_does_not_require_protocol_field_for_chat_completions_routes(tmp_path) -> None:
    store = ModelRegistryStore(tmp_path / "model_registry.json")

    store.save(
        {
            "version": 2,
            "profile_defaults": {"chat": {"ref": "standard-gpt-5.4-high-tokenx"}},
            "routes": {
                "tokenx": {
                    "config_provider_ref": "custom",
                    "adapter": "openai_compat",
                }
            },
            "models": {
                "standard-gpt-5.4-high-tokenx": {
                    "family": "gpt-5.4",
                    "tier": "standard",
                    "effort": "high",
                    "route_ref": "tokenx",
                    "provider_model": "gpt-5.4",
                    "enabled": True,
                    "template": False,
                    "capabilities": {"chat": True, "tool_calls": True},
                }
            },
        }
    )

    loaded = store.load()

    assert loaded.models["standard-gpt-5.4-high-tokenx"].protocol is None


@pytest.mark.parametrize(
    "bad_value",
    [[], (), False],
)
def test_registry_v2_rejects_non_mapping_top_level_sections(tmp_path, bad_value) -> None:
    store = ModelRegistryStore(tmp_path / "model_registry.json")

    with pytest.raises(ModelRegistryError, match="profile_defaults"):
        store.save(
            {
                "version": 2,
                "profile_defaults": bad_value,
                "routes": {},
                "models": {},
            }
        )


@pytest.mark.parametrize(
    ("runtime_defaults", "match"),
    [
        ({"max_tokenz": 8192}, "unsupported runtime_defaults key"),
        ({"max_tokens": "many"}, "max_tokens"),
        ({"temperature": "warm"}, "temperature"),
    ],
)
def test_registry_v2_rejects_invalid_runtime_defaults(tmp_path, runtime_defaults, match) -> None:
    store = ModelRegistryStore(tmp_path / "model_registry.json")

    with pytest.raises(ModelRegistryError, match=match):
        store.save(
            {
                "version": 2,
                "profile_defaults": {"chat": {"ref": "standard-gpt-5.4-high-tokenx"}},
                "routes": {
                    "tokenx": {
                        "config_provider_ref": "custom",
                        "adapter": "openai_compat",
                    }
                },
                "models": {
                    "standard-gpt-5.4-high-tokenx": {
                        "family": "gpt-5.4",
                        "tier": "standard",
                        "effort": "high",
                        "route_ref": "tokenx",
                        "provider_model": "gpt-5.4",
                        "runtime_defaults": runtime_defaults,
                    }
                },
            }
        )
