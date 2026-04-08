"""Runtime-native model registry package."""

from nanobot.model_registry.provider_factory import build_provider_from_spec
from nanobot.model_registry.resolver import (
    ModelRegistryError,
    ModelRegistrySemanticError,
    RegistryResolver,
    ResolvedModelSpec,
)
from nanobot.model_registry.schema import (
    ModelDefinition,
    ModelRegistry,
    ProfileDefault,
    RouteDefinition,
)
from nanobot.model_registry.store import ModelRegistryStore

__all__ = [
    "ModelDefinition",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelRegistrySemanticError",
    "ModelRegistryStore",
    "ProfileDefault",
    "RegistryResolver",
    "ResolvedModelSpec",
    "RouteDefinition",
    "build_provider_from_spec",
]
