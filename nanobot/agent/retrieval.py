"""Core retrieval seam for optional auxiliary memory context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class RetrievalRequest:
    """Normalized retrieval input owned by core runtime."""

    workspace: Path
    current_message: str
    history: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)
    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    channel: str | None = None
    chat_id: str | None = None


class RetrievalProvider(Protocol):
    """Async provider contract for optional retrieved context."""

    async def build_context(self, request: RetrievalRequest) -> str | None:
        """Return auxiliary context text or None when no retrieval is available."""


class NullRetrievalProvider:
    """Safe default provider that never injects retrieval context."""

    async def build_context(self, request: RetrievalRequest) -> str | None:  # noqa: ARG002
        return None


class MetadataRetrievalProvider:
    """Bridge retrieval snippets already prepared by an outer adapter."""

    _TEXT_KEY = "retrieval_context"
    _SNIPPETS_KEY = "retrieval_snippets"

    async def build_context(self, request: RetrievalRequest) -> str | None:
        metadata = request.metadata or {}
        explicit = metadata.get(self._TEXT_KEY)
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        snippets = metadata.get(self._SNIPPETS_KEY)
        if not isinstance(snippets, list) or not snippets:
            return None

        rendered: list[str] = []
        for snippet in snippets:
            if isinstance(snippet, str):
                text = snippet.strip()
                if text:
                    rendered.append(f"- {text}")
                continue
            if not isinstance(snippet, dict):
                continue
            title = str(snippet.get("title") or "").strip()
            content = str(snippet.get("content") or "").strip()
            source = str(snippet.get("source") or "").strip()
            parts = [part for part in (title, content) if part]
            if not parts:
                continue
            line = " — ".join(parts)
            if source:
                line = f"{line} ({source})"
            rendered.append(f"- {line}")

        return "\n".join(rendered) if rendered else None


def normalize_retrieval_context(text: str, *, max_chars: int) -> str | None:
    """Normalize and clip retrieved context for prompt injection."""

    cleaned_lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not cleaned_lines:
        return None
    normalized = "\n".join(cleaned_lines).strip()
    if max_chars <= 0:
        return None
    if len(normalized) <= max_chars:
        return normalized
    clipped = normalized[:max_chars].rstrip()
    return clipped + "\n... (retrieval truncated)"
