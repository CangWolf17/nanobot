"""Filesystem store for the runtime-native model registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from nanobot.model_registry.schema import ModelRegistry, ModelRegistryError


class ModelRegistryStore:
    """Persist and load the v2 registry JSON document."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> ModelRegistry:
        if not self.path.exists():
            raise FileNotFoundError(self.path)

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelRegistryError(f"Failed to parse registry JSON: {exc}") from exc

        return ModelRegistry.from_dict(payload)

    def save(self, registry: ModelRegistry | Mapping[str, Any]) -> None:
        payload = ModelRegistry.from_dict(registry).to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


__all__ = ["ModelRegistryStore"]
