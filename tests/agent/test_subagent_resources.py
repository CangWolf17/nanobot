from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.spawn import SpawnTool


def _registry() -> dict:
    return {
        "version": 1,
        "subagent_defaults": {
            "model": "gpt-5.4",
            "task_budget": 3,
            "level_limit": 2,
        },
        "profile_defaults": {
            "archive": {"ref": "lite-minimax-m2.7-high-minimax"},
        },
        "models": {
            "standard-gpt-5.4-high-aizhiwen-top": {
                "tier": "standard",
                "family": "gpt-5.4",
                "effort": "high",
                "route": "aizhiwen-top",
                "provider": "custom",
                "provider_model": "gpt-5.4",
                "connection": {
                    "api_base": "https://aizhiwen.top/v1",
                    "api_key": "k-aizhiwen",
                    "extra_headers": {},
                },
                "agent": {"temperature": 0.3, "max_tokens": 8192},
                "enabled": True,
                "template": False,
            },
            "standard-gpt-5.4-xhigh-aizhiwen-top": {
                "tier": "standard",
                "family": "gpt-5.4",
                "effort": "xhigh",
                "route": "aizhiwen-top",
                "provider": "custom",
                "provider_model": "gpt-5.4",
                "connection": {
                    "api_base": "https://aizhiwen.top/v1",
                    "api_key": "k-aizhiwen",
                    "extra_headers": {},
                },
                "agent": {"temperature": 0.3, "max_tokens": 8192},
                "enabled": True,
                "template": False,
            },
            "standard-gpt-5.4-high-tokenx": {
                "tier": "standard",
                "family": "gpt-5.4",
                "effort": "high",
                "route": "tokenx",
                "provider": "custom",
                "provider_model": "gpt-5.4",
                "connection": {
                    "api_base": "https://tokenx24.com/v1",
                    "api_key": "k-tokenx",
                    "extra_headers": {},
                },
                "agent": {"temperature": 0.3, "max_tokens": 8192},
                "enabled": True,
                "template": False,
            },
            "standard-gpt-5.4-xhigh-tokenx": {
                "tier": "standard",
                "family": "gpt-5.4",
                "effort": "xhigh",
                "route": "tokenx",
                "provider": "custom",
                "provider_model": "gpt-5.4",
                "connection": {
                    "api_base": "https://tokenx24.com/v1",
                    "api_key": "k-tokenx",
                    "extra_headers": {},
                },
                "agent": {"temperature": 0.3, "max_tokens": 8192},
                "enabled": True,
                "template": False,
            },
            "lite-minimax-m2.7-high-minimax": {
                "tier": "lite",
                "family": "minimax-m2.7",
                "effort": "high",
                "route": "minimax",
                "provider": "minimax",
                "provider_model": "MiniMax-M2.7",
                "connection": {
                    "api_base": "https://api.minimaxi.com/v1",
                    "api_key": "k-minimax",
                    "extra_headers": {},
                },
                "agent": {"temperature": 0.1, "max_tokens": 8192},
                "enabled": True,
                "template": False,
            },
        },
    }


def _manager():
    from nanobot.agent.subagent_resources import (
        RoutePolicy,
        RouteState,
        SubagentResourceManager,
        TierPolicy,
    )

    return SubagentResourceManager(
        registry=_registry(),
        tier_policies={
            "standard": TierPolicy(
                default_effort="high",
                route_preferences=("aizhiwen-top", "tokenx"),
                allow_queue=True,
                queue_limit=10,
            ),
            "lite": TierPolicy(
                default_effort="high",
                route_preferences=("minimax",),
                allow_queue=True,
                queue_limit=5,
            ),
        },
        route_policies={
            "aizhiwen-top": RoutePolicy(max_concurrency=10),
            "tokenx": RoutePolicy(
                max_concurrency=3,
                availability="hard_unavailable",
                unavailable_reason="exhausted_today",
            ),
            "minimax": RoutePolicy(
                max_concurrency=1,
                window_request_limit=600,
                reserved_requests=50,
            ),
        },
        route_states={
            "aizhiwen-top": RouteState(),
            "tokenx": RouteState(),
            "minimax": RouteState(),
        },
    )


def test_explicit_model_overrides_harness_and_manager_defaults() -> None:
    from nanobot.agent.subagent_resources import SubagentRequest

    manager = _manager()
    decision = manager.acquire(
        SubagentRequest(
            model="standard-gpt-5.4-xhigh-aizhiwen-top",
            harness_tier="standard",
            manager_model="standard-gpt-5.4-high-aizhiwen-top",
        )
    )

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-xhigh-aizhiwen-top"


def test_standard_tier_defaults_to_high_and_skips_exhausted_tokenx() -> None:
    from nanobot.agent.subagent_resources import SubagentRequest

    manager = _manager()
    decision = manager.acquire(
        SubagentRequest(
            tier="standard",
            harness_model="standard-gpt-5.4-xhigh-tokenx",
            manager_model="standard-gpt-5.4-xhigh-tokenx",
        )
    )

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"
    assert decision.lease.effort == "high"
    assert decision.lease.route == "aizhiwen-top"


def test_lite_request_can_queue_when_waiting_below_threshold() -> None:
    from nanobot.agent.subagent_resources import RouteState, SubagentRequest

    manager = _manager()
    manager.route_states["minimax"] = RouteState(inflight=1, waiting=4, window_used_requests=100)

    decision = manager.acquire(SubagentRequest(tier="lite"))

    assert decision.status == "queued"
    assert decision.reason == "queue_wait"
    assert manager.route_states["minimax"].waiting == 5


def test_lite_request_is_rejected_when_queue_threshold_is_full() -> None:
    from nanobot.agent.subagent_resources import RouteState, SubagentRequest

    manager = _manager()
    manager.route_states["minimax"] = RouteState(inflight=1, waiting=5, window_used_requests=100)

    decision = manager.acquire(SubagentRequest(tier="lite"))

    assert decision.status == "rejected"
    assert decision.reason == "queue_limit"


def test_minimax_reserve_blocks_new_lite_allocation_once_window_floor_is_hit() -> None:
    from nanobot.agent.subagent_resources import RouteState, SubagentRequest

    manager = _manager()
    manager.route_states["minimax"] = RouteState(inflight=0, waiting=0, window_used_requests=550)

    decision = manager.acquire(SubagentRequest(tier="lite"))

    assert decision.status == "rejected"
    assert decision.reason == "reserved_quota_exhausted"


def test_release_frees_inflight_slot_for_granted_lease() -> None:
    from nanobot.agent.subagent_resources import SubagentRequest

    manager = _manager()
    decision = manager.acquire(SubagentRequest(tier="standard"))

    assert decision.status == "granted"
    assert manager.route_states["aizhiwen-top"].inflight == 1

    manager.release(decision.lease)

    assert manager.route_states["aizhiwen-top"].inflight == 0


def test_classify_provider_failure_distinguishes_transient_from_hard_unavailable() -> None:
    from nanobot.agent.subagent_resources import classify_provider_failure

    transient = classify_provider_failure("HTTP 502 upstream timeout")
    hard = classify_provider_failure("余额不足，今日额度已用完")

    assert transient.availability == "transient_unavailable"
    assert transient.reason == "http_502"
    assert hard.availability == "hard_unavailable"
    assert hard.reason == "quota_exhausted"


def test_build_manager_from_workspace_snapshot_uses_workspace_truth(tmp_path) -> None:
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": "gpt-5.4",
                        "provider": "custom",
                        "reasoningEffort": "xhigh",
                        "temperature": 0.3,
                        "maxTokens": 8192,
                    }
                },
                "providers": {
                    "custom": {
                        "apiKey": "k-tokenx",
                        "apiBase": "https://tokenx24.com/v1",
                        "extraHeaders": None,
                    },
                    "minimax": {
                        "apiKey": "k-minimax",
                        "apiBase": "https://api.minimaxi.com/v1",
                        "extraHeaders": None,
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(
        workspace=tmp_path,
        provider_status={"tokenx": {"availability": "hard_unavailable", "reason": "exhausted_today"}},
    )

    request = manager.default_request()
    assert request.manager_model == "gpt-5.4"
    assert request.manager_tier == "standard"

    decision = manager.acquire(request)
    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"
    assert decision.lease.effort == "high"


def test_record_provider_failure_persists_hard_status_and_future_snapshot_skips_route(tmp_path) -> None:
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot, record_provider_failure

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    status = record_provider_failure(
        workspace=tmp_path,
        route="aizhiwen-top",
        error_text="quota exceeded",
        updated_at="2026-04-06T09:40:00+00:00",
    )

    assert status is not None
    assert status.availability == "hard_unavailable"
    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["aizhiwen-top"]["availability"] == "hard_unavailable"
    assert updated["provider_status"]["aizhiwen-top"]["reason"] == "quota_exhausted"

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-tokenx"


def test_refresh_provider_status_restores_route_for_future_snapshot(tmp_path) -> None:
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot, refresh_provider_status

    registry = _registry()
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "hard_unavailable",
            "reason": "quota_exhausted",
            "source": "runtime_error",
            "updated_at": "2026-04-06T09:40:00+00:00",
        }
    }

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    refresh_provider_status(
        workspace=tmp_path,
        route="aizhiwen-top",
        updated_at="2026-04-06T10:00:00+00:00",
    )

    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["aizhiwen-top"]["availability"] == "available"
    assert updated["provider_status"]["aizhiwen-top"]["reason"] == ""

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"


@pytest.mark.asyncio
async def test_spawn_tool_blocks_when_active_harness_disallows_subagent() -> None:
    manager = MagicMock()
    manager.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(manager)
    tool.set_context(
        "feishu",
        "chat1",
        {
            "workspace_agent_cmd": "harness",
            "workspace_runtime": {"active_harness": {"subagent_allowed": False}},
        },
    )

    result = await tool.execute("do it")

    assert "spawn blocked by harness policy" in result
    manager.spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_tool_passes_workspace_runtime_metadata_to_manager() -> None:
    manager = MagicMock()
    manager.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(manager)
    tool.set_context(
        "feishu",
        "chat1",
        {
            "workspace_agent_cmd": "harness",
            "workspace_harness_id": "har_0001",
            "workspace_work_mode": "build",
            "workspace_runtime": {"active_harness": {"subagent_allowed": True}},
        },
    )

    result = await tool.execute("do it", label="run", tier="lite", model="lite-model")

    assert result == "started"
    manager.spawn.assert_awaited_once_with(
        task="do it",
        label="run",
        tier="lite",
        model="lite-model",
        origin_channel="feishu",
        origin_chat_id="chat1",
        session_key="feishu:chat1",
        origin_metadata={
            "workspace_agent_cmd": "harness",
            "workspace_harness_id": "har_0001",
            "workspace_work_mode": "build",
            "workspace_runtime": {"active_harness": {"subagent_allowed": True}},
        },
    )
