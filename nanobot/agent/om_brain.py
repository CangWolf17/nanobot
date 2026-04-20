from __future__ import annotations

from pathlib import Path

CANONICAL_BRAIN_FILES = (
    "North Star.md",
    "Memories.md",
    "Key Decisions.md",
    "Patterns.md",
    "Gotchas.md",
    "Skills.md",
)

DEFAULT_SCAFFOLD = {
    "North Star.md": "# North Star\n\n## Current Focus\n- \n\n## Priority Rules\n- \n\n## Explicit Non-Goals\n- \n",
    "Memories.md": "# Memories\n\n## Topics\n- [[Key Decisions]]\n- [[Patterns]]\n- [[Gotchas]]\n- [[Skills]]\n",
    "Key Decisions.md": "# Key Decisions\n\n",
    "Patterns.md": "# Patterns\n\n",
    "Gotchas.md": "# Gotchas\n\n",
    "Skills.md": "# Skills\n\n",
}

DEFAULT_MAX_CHARS = {
    "North Star.md": 1200,
    "Memories.md": 800,
    "Key Decisions.md": 1200,
    "Patterns.md": 1000,
    "Gotchas.md": 1200,
    "Skills.md": 600,
}


def resolve_agent_brain_root() -> Path:
    return Path("/home/admin/obsidian-vault/agent-brain")


def _clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _is_meaningful_brain_content(name: str, raw: str) -> bool:
    stripped = (raw or "").strip()
    if not stripped:
        return False
    return stripped != DEFAULT_SCAFFOLD[name].strip()


def build_agent_brain_context(
    max_chars_by_file: dict[str, int] | None = None,
    *,
    root: Path | None = None,
) -> str | None:
    brain_root = root if root is not None else resolve_agent_brain_root()
    budgets = {**DEFAULT_MAX_CHARS, **(max_chars_by_file or {})}
    sections: list[str] = []

    for name in CANONICAL_BRAIN_FILES:
        path = brain_root / name
        if not path.exists() or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        if not _is_meaningful_brain_content(name, raw):
            continue
        clipped = _clip(raw.strip(), int(budgets.get(name, DEFAULT_MAX_CHARS.get(name, 1000))))
        if not clipped.strip():
            continue
        sections.append(f"## {name}\n{clipped}")

    if not sections:
        return None
    return "\n\n".join(sections).strip()
