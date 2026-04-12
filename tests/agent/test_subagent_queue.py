from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_spawn_returns_queued_and_starts_after_drain(tmp_path) -> None:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.subagent_resources import AcquireDecision, SubagentLease
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

    lease = SubagentLease(
        model_id="standard-gpt-5.4-mini-xhigh-aizhiwen-top",
        tier="standard",
        route="aizhiwen-top",
        effort="xhigh",
    )
    resource_manager = MagicMock()
    resource_manager.resolve_spawn_request.return_value = MagicMock(
        reason="builtin_type:worker",
        requested_type="worker",
        requested_model=None,
        preferred_route="aizhiwen-top",
        candidate_chain=("m1",),
    )
    resource_manager.acquire_candidates.side_effect = [
        AcquireDecision(status="queued", reason="queue_wait", queue_route="aizhiwen-top", queue_tier="standard"),
        AcquireDecision(status="granted", lease=lease),
    ]
    resource_manager.release_waiting_route = MagicMock()
    resource_manager.release = MagicMock()
    mgr.resource_manager = resource_manager
    mgr._run_subagent = AsyncMock()

    result = await mgr.spawn(task="do task", label="bg", subagent_type="worker", session_key="test:c1")

    assert "queued" in result.lower()
    assert mgr.get_pending_count() == 1
    assert mgr.get_running_count() == 0

    await mgr._drain_pending_queue()

    assert mgr.get_pending_count() == 0
    assert mgr.get_running_count() == 1
    resource_manager.release_waiting_route.assert_called_with("aizhiwen-top")
    await list(mgr._running_tasks.values())[0]
    mgr._run_subagent.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_by_session_removes_pending_tasks(tmp_path) -> None:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.subagent_resources import AcquireDecision
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

    resource_manager = MagicMock()
    resource_manager.resolve_spawn_request.return_value = MagicMock(
        reason="builtin_type:worker",
        requested_type="worker",
        requested_model=None,
        preferred_route="aizhiwen-top",
        candidate_chain=("m1",),
    )
    resource_manager.acquire_candidates.return_value = AcquireDecision(
        status="queued",
        reason="queue_wait",
        queue_route="aizhiwen-top",
        queue_tier="standard",
    )
    resource_manager.release_waiting_route = MagicMock()
    mgr.resource_manager = resource_manager

    result = await mgr.spawn(task="do task", label="bg", subagent_type="worker", session_key="test:c1")

    assert "queued" in result.lower()
    count = await mgr.cancel_by_session("test:c1")



@pytest.mark.asyncio
async def test_orchestrator_profile_registers_guarded_message_and_spawn(tmp_path) -> None:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.guarded import GuardedTool
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

    tools = mgr._build_subagent_tools(
        task_id="sub-1",
        origin={
            "channel": "feishu",
            "chat_id": "chat-1",
            "metadata": {
                "workspace_runtime": {
                    "active_harness": {
                        "subagent_allowed": True,
                        "delegation_level": "required",
                        "risk_level": "normal",
                        "subagent_profile": "orchestrator",
                    }
                }
            },
        },
    )

    assert isinstance(tools.get("message"), GuardedTool)
    assert isinstance(tools.get("spawn"), GuardedTool)


@pytest.mark.asyncio
async def test_default_profile_does_not_register_sensitive_tools(tmp_path) -> None:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

    tools = mgr._build_subagent_tools(
        task_id="sub-1",
        origin={
            "channel": "feishu",
            "chat_id": "chat-1",
            "metadata": {
                "workspace_runtime": {
                    "active_harness": {
                        "subagent_allowed": True,
                        "delegation_level": "assist",
                        "risk_level": "normal",
                        "subagent_profile": "default",
                    }
                }
            },
        },
    )

    assert tools.get("message") is None
    assert tools.get("spawn") is None
