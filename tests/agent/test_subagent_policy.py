from __future__ import annotations

from nanobot.agent.subagent_policy import (
    SubagentRunContext,
    build_child_subagent_runtime,
    build_root_subagent_runtime,
    resolve_subagent_tool_policy,
)


def test_resolve_subagent_tool_policy_default_profile_blocks_sensitive_tools() -> None:
    run_context, policy = resolve_subagent_tool_policy(
        workspace_runtime={
            "active_harness": {
                "subagent_allowed": True,
                "delegation_level": "assist",
                "risk_level": "normal",
                "subagent_profile": "default",
            }
        },
        subagent_runtime={"depth": 1, "remaining_budget": 3, "profile": "default"},
    )

    assert run_context.depth == 1
    assert run_context.remaining_budget == 3
    assert policy.profile == "default"
    assert policy.allow_message is False
    assert policy.allow_spawn is False


def test_resolve_subagent_tool_policy_sensitive_risk_downgrades_orchestrator() -> None:
    _run_context, policy = resolve_subagent_tool_policy(
        workspace_runtime={
            "active_harness": {
                "subagent_allowed": True,
                "delegation_level": "required",
                "risk_level": "sensitive",
                "subagent_profile": "orchestrator",
            }
        },
        subagent_runtime={"depth": 1, "remaining_budget": 3, "profile": "orchestrator"},
    )

    assert policy.allow_message is False
    assert policy.allow_spawn is False
    assert policy.message_scope == "none"


def test_resolve_subagent_tool_policy_delegation_none_disables_spawn_only() -> None:
    _run_context, policy = resolve_subagent_tool_policy(
        workspace_runtime={
            "active_harness": {
                "subagent_allowed": True,
                "delegation_level": "none",
                "risk_level": "normal",
                "subagent_profile": "orchestrator",
            }
        },
        subagent_runtime={"depth": 1, "remaining_budget": 3, "profile": "orchestrator"},
    )

    assert policy.allow_message is True
    assert policy.allow_spawn is False


def test_build_child_subagent_runtime_decrements_budget_and_increments_depth() -> None:
    parent = build_root_subagent_runtime(profile="delegate", remaining_budget=3)
    child = build_child_subagent_runtime(
        SubagentRunContext(
            depth=parent["depth"],
            remaining_budget=parent["remaining_budget"],
            profile=parent["profile"],
        ),
        parent_task_id="sub-1",
    )

    assert child["depth"] == 2
    assert child["remaining_budget"] == 2
    assert child["profile"] == "delegate"
    assert child["parent_task_id"] == "sub-1"
