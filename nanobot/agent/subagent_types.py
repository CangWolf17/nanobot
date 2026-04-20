from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubagentTypeSpec:
    name: str
    tier: str
    family: str
    effort: str


_BUILTIN_SUBAGENT_TYPES: dict[str, SubagentTypeSpec] = {
    "worker": SubagentTypeSpec(
        name="worker",
        tier="standard",
        family="gpt-5.4-mini",
        effort="xhigh",
    ),
    "explorer": SubagentTypeSpec(
        name="explorer",
        tier="standard",
        family="gpt-5.4-mini",
        effort="medium",
    ),
}


def list_builtin_subagent_types() -> tuple[str, ...]:
    return tuple(_BUILTIN_SUBAGENT_TYPES.keys())


def get_subagent_type_spec(name: str) -> SubagentTypeSpec:
    cleaned = str(name or "").strip().lower()
    if not cleaned or cleaned not in _BUILTIN_SUBAGENT_TYPES:
        supported = ", ".join(list_builtin_subagent_types())
        raise ValueError(f"unsupported subagent type: {name}. supported: {supported}")
    return _BUILTIN_SUBAGENT_TYPES[cleaned]
