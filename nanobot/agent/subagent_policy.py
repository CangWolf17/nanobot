from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class SubagentRunContext:
    depth: int = 1
    remaining_budget: int = 0
    profile: str = "default"
    parent_task_id: str = ""


@dataclass(frozen=True)
class SubagentToolPolicy:
    profile: str = "default"
    allow_message: bool = False
    allow_message_media: bool = False
    message_scope: str = "none"  # none | same_chat | explicit_target
    allow_spawn: bool = False
    max_spawn_depth: int = 0
    allowed_spawn_types: tuple[str, ...] = ()
    allow_explicit_spawn_model: bool = False


_BUILTIN_POLICIES: dict[str, SubagentToolPolicy] = {
    "default": SubagentToolPolicy(profile="default"),
    "notify": SubagentToolPolicy(
        profile="notify",
        allow_message=True,
        allow_message_media=False,
        message_scope="same_chat",
    ),
    "delegate": SubagentToolPolicy(
        profile="delegate",
        allow_spawn=True,
        max_spawn_depth=2,
        allowed_spawn_types=("worker", "explorer"),
        allow_explicit_spawn_model=False,
    ),
    "orchestrator": SubagentToolPolicy(
        profile="orchestrator",
        allow_message=True,
        allow_message_media=False,
        message_scope="same_chat",
        allow_spawn=True,
        max_spawn_depth=2,
        allowed_spawn_types=("worker", "explorer"),
        allow_explicit_spawn_model=False,
    ),
}


def _clean_text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _clean_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("-").isdigit():
            return int(text)
    return default


def _active_harness(runtime_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(runtime_metadata, dict):
        return {}
    active = runtime_metadata.get("active_harness")
    if isinstance(active, dict):
        return active
    main = runtime_metadata.get("main_harness")
    return main if isinstance(main, dict) else {}


def normalize_subagent_run_context(
    data: dict[str, Any] | None,
    *,
    default_profile: str = "default",
    default_depth: int = 1,
    default_remaining_budget: int = 0,
) -> SubagentRunContext:
    payload = data if isinstance(data, dict) else {}
    return SubagentRunContext(
        depth=max(1, _clean_int(payload.get("depth"), default_depth)),
        remaining_budget=max(0, _clean_int(payload.get("remaining_budget"), default_remaining_budget)),
        profile=_clean_text(payload.get("profile"), default_profile),
        parent_task_id=_clean_text(payload.get("parent_task_id")),
    )


def get_subagent_tool_policy(profile: str | None) -> SubagentToolPolicy:
    cleaned = _clean_text(profile, "default").lower()
    return _BUILTIN_POLICIES.get(cleaned, _BUILTIN_POLICIES["default"])


def resolve_subagent_tool_policy(
    *,
    workspace_runtime: dict[str, Any] | None,
    subagent_runtime: dict[str, Any] | None,
) -> tuple[SubagentRunContext, SubagentToolPolicy]:
    active = _active_harness(workspace_runtime)
    default_profile = _clean_text(active.get("subagent_profile"), "default")
    run_context = normalize_subagent_run_context(
        subagent_runtime,
        default_profile=default_profile,
    )
    policy = get_subagent_tool_policy(run_context.profile)

    if active:
        if not bool(active.get("subagent_allowed", False)):
            policy = replace(
                policy,
                allow_message=False,
                allow_message_media=False,
                message_scope="none",
                allow_spawn=False,
                max_spawn_depth=0,
                allowed_spawn_types=(),
                allow_explicit_spawn_model=False,
            )
        if _clean_text(active.get("delegation_level"), "assist").lower() == "none":
            policy = replace(
                policy,
                allow_spawn=False,
                max_spawn_depth=0,
                allowed_spawn_types=(),
                allow_explicit_spawn_model=False,
            )
        if _clean_text(active.get("risk_level"), "normal").lower() == "sensitive":
            policy = replace(
                policy,
                allow_message=False,
                allow_message_media=False,
                message_scope="none",
                allow_spawn=False,
                max_spawn_depth=0,
                allowed_spawn_types=(),
                allow_explicit_spawn_model=False,
            )

    return run_context, policy


def build_root_subagent_runtime(
    *,
    profile: str,
    remaining_budget: int,
) -> dict[str, Any]:
    return {
        "depth": 1,
        "remaining_budget": max(0, int(remaining_budget)),
        "profile": _clean_text(profile, "default"),
        "parent_task_id": "",
    }


def build_child_subagent_runtime(
    parent: SubagentRunContext,
    *,
    parent_task_id: str,
) -> dict[str, Any]:
    return {
        "depth": max(1, int(parent.depth) + 1),
        "remaining_budget": max(0, int(parent.remaining_budget) - 1),
        "profile": _clean_text(parent.profile, "default"),
        "parent_task_id": _clean_text(parent_task_id),
    }
