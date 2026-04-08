from __future__ import annotations

import json


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



def test_explicit_model_overrides_harness_and_manager_defaults():
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



def test_standard_tier_defaults_to_high_and_skips_exhausted_tokenx():
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



def test_lite_request_can_queue_when_waiting_below_threshold():
    from nanobot.agent.subagent_resources import RouteState, SubagentRequest

    manager = _manager()
    manager.route_states["minimax"] = RouteState(inflight=1, waiting=4, window_used_requests=100)

    decision = manager.acquire(SubagentRequest(tier="lite"))

    assert decision.status == "queued"
    assert decision.reason == "queue_wait"
    assert manager.route_states["minimax"].waiting == 5



def test_lite_request_is_rejected_when_queue_threshold_is_full():
    from nanobot.agent.subagent_resources import RouteState, SubagentRequest

    manager = _manager()
    manager.route_states["minimax"] = RouteState(inflight=1, waiting=5, window_used_requests=100)

    decision = manager.acquire(SubagentRequest(tier="lite"))

    assert decision.status == "rejected"
    assert decision.reason == "queue_limit"



def test_minimax_reserve_blocks_new_lite_allocation_once_window_floor_is_hit():
    from nanobot.agent.subagent_resources import RouteState, SubagentRequest

    manager = _manager()
    manager.route_states["minimax"] = RouteState(inflight=0, waiting=0, window_used_requests=550)

    decision = manager.acquire(SubagentRequest(tier="lite"))

    assert decision.status == "rejected"
    assert decision.reason == "reserved_quota_exhausted"



def test_release_frees_inflight_slot_for_granted_lease():
    from nanobot.agent.subagent_resources import SubagentRequest

    manager = _manager()
    decision = manager.acquire(SubagentRequest(tier="standard"))

    assert decision.status == "granted"
    assert manager.route_states["aizhiwen-top"].inflight == 1

    manager.release(decision.lease)

    assert manager.route_states["aizhiwen-top"].inflight == 0



def test_classify_provider_failure_distinguishes_transient_from_hard_unavailable():
    from nanobot.agent.subagent_resources import classify_provider_failure

    transient = classify_provider_failure("HTTP 502 upstream timeout")
    hard = classify_provider_failure("余额不足，今日额度已用完")

    assert transient.availability == "transient_unavailable"
    assert transient.reason == "http_502"
    assert hard.availability == "hard_unavailable"
    assert hard.reason == "quota_exhausted"



def test_build_manager_from_workspace_snapshot_uses_workspace_truth(tmp_path):
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



def test_build_manager_snapshot_reads_v2_route_names_and_profile_defaults(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    registry = {
        "version": 2,
        "profile_defaults": {
            "chat": {"ref": "standard-gpt-5.4-high-tokenx"},
            "archive": {"ref": "archive-gpt-4.1-mini"},
        },
        "routes": {
            "tokenx": {"config_provider_ref": "custom", "adapter": "openai_compat"},
            "responses": {"config_provider_ref": "openai", "adapter": "openai_responses"},
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
            },
            "archive-gpt-4.1-mini": {
                "family": "gpt-4.1",
                "tier": "lite",
                "effort": "high",
                "route_ref": "responses",
                "provider_model": "gpt-4.1-mini",
                "enabled": True,
                "template": False,
            },
        },
    }

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)

    assert "tokenx" in manager.route_policies
    assert "responses" in manager.route_policies

    decision = manager.acquire(manager.default_request(tier="lite"))
    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.route == "responses"



def test_build_manager_snapshot_keeps_legacy_registry_path_working(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"



def test_build_manager_snapshot_prefers_current_model_from_model_state(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_state.json").write_text(
        json.dumps({"current_model": "standard-gpt-5.4-xhigh-tokenx"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    request = manager.default_request()

    assert request.manager_model == "standard-gpt-5.4-xhigh-tokenx"
    assert request.manager_tier == "standard"



def test_build_manager_snapshot_defaults_lite_to_minimax_archive_route(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request(tier="lite"))

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "lite-minimax-m2.7-high-minimax"



def test_build_manager_snapshot_uses_persisted_provider_status_for_candidate_selection(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    registry = _registry()
    registry["provider_status"] = {
        "aizhiwen-top": {"availability": "hard_unavailable", "reason": "quota_exhausted"},
        "tokenx": {"availability": "available", "reason": ""},
    }

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-tokenx"



def test_build_manager_snapshot_uses_registry_policy_overrides_instead_of_hardcoded_defaults(tmp_path):
    from nanobot.agent.subagent_resources import RouteState, build_manager_from_workspace_snapshot

    registry = _registry()
    registry["provider_policies"] = {
        "minimax": {
            "max_concurrency": 1,
            "window_request_limit": 600,
            "reserved_requests": 70,
        }
    }
    registry["tier_policies"] = {
        "lite": {
            "default_effort": "high",
            "route_preferences": ["minimax"],
            "allow_queue": True,
            "queue_limit": 2,
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

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    manager.route_states["minimax"] = RouteState(inflight=1, waiting=2, window_used_requests=530)
    decision = manager.acquire(manager.default_request(tier="lite"))

    assert manager.tier_policies["lite"].queue_limit == 2
    assert manager.route_policies["minimax"].reserved_requests == 70
    assert decision.status == "rejected"
    assert decision.reason == "reserved_quota_exhausted"





def test_record_provider_failure_persists_hard_status_and_future_snapshot_skips_route(tmp_path):
    from nanobot.agent.subagent_resources import (
        build_manager_from_workspace_snapshot,
        record_provider_failure,
    )

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
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "runtime_error"
    assert updated["provider_status"]["aizhiwen-top"]["updated_at"] == "2026-04-06T09:40:00+00:00"

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-tokenx"



def test_record_provider_failure_persists_transient_status_without_shrinking_candidate_pool(tmp_path):
    from nanobot.agent.subagent_resources import (
        build_manager_from_workspace_snapshot,
        record_provider_failure,
    )

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
        error_text="HTTP 502 upstream timeout",
        updated_at="2026-04-06T09:40:00+00:00",
    )

    assert status is not None
    assert status.availability == "transient_unavailable"
    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["aizhiwen-top"]["availability"] == "transient_unavailable"
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "runtime_error"
    assert updated["provider_status"]["aizhiwen-top"]["updated_at"] == "2026-04-06T09:40:00+00:00"

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"



def test_build_manager_snapshot_ignores_stale_transient_provider_status(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    registry = _registry()
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "transient_unavailable",
            "reason": "http_502",
            "source": "runtime_error",
            "updated_at": "2026-04-06T00:00:00+00:00",
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

    manager = build_manager_from_workspace_snapshot(
        workspace=tmp_path,
        now="2026-04-06T09:40:00+00:00",
    )
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"





def test_refresh_provider_status_restores_route_for_future_snapshot(tmp_path):
    from nanobot.agent.subagent_resources import (
        build_manager_from_workspace_snapshot,
        refresh_provider_status,
    )

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
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "monitor_refresh"
    assert updated["provider_status"]["aizhiwen-top"]["updated_at"] == "2026-04-06T10:00:00+00:00"

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"



def test_build_manager_snapshot_uses_registry_configured_transient_ttl_seconds(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    registry = _registry()
    registry["provider_status_policy"] = {"transient_ttl_seconds": 60}
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "transient_unavailable",
            "reason": "http_502",
            "source": "runtime_error",
            "updated_at": "2026-04-06T09:00:00+00:00",
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

    manager = build_manager_from_workspace_snapshot(
        workspace=tmp_path,
        now="2026-04-06T09:02:00+00:00",
    )
    decision = manager.acquire(manager.default_request())

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "standard-gpt-5.4-high-aizhiwen-top"



def test_record_provider_failure_uses_registry_default_runtime_error_source(tmp_path):
    from nanobot.agent.subagent_resources import record_provider_failure

    registry = _registry()
    registry["provider_status_policy"] = {"runtime_error_source": "lease_runtime"}

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    record_provider_failure(
        workspace=tmp_path,
        route="aizhiwen-top",
        error_text="quota exceeded",
        updated_at="2026-04-06T09:40:00+00:00",
    )

    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "lease_runtime"





def test_refresh_provider_status_uses_registry_default_refresh_source(tmp_path):
    from nanobot.agent.subagent_resources import refresh_provider_status

    registry = _registry()
    registry["provider_status_policy"] = {"refresh_source": "health_probe"}

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
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "health_probe"



def test_apply_provider_probe_result_refreshes_status_from_api_base(tmp_path):
    from nanobot.agent.subagent_resources import apply_provider_probe_result

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    route = apply_provider_probe_result(
        workspace=tmp_path,
        probe={
            "ok": True,
            "provider": "custom",
            "api_base": "https://aizhiwen.top/v1",
            "reason": "OK",
        },
        updated_at="2026-04-06T10:00:00+00:00",
    )

    assert route == "aizhiwen-top"
    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["aizhiwen-top"]["availability"] == "available"
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "monitor_refresh"



def test_apply_provider_probe_result_records_failure_from_api_base(tmp_path):
    from nanobot.agent.subagent_resources import apply_provider_probe_result

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    route = apply_provider_probe_result(
        workspace=tmp_path,
        probe={
            "ok": False,
            "provider": "custom",
            "api_base": "https://tokenx24.com/v1",
            "reason": "quota exceeded",
        },
        updated_at="2026-04-06T10:00:00+00:00",
    )

    assert route == "tokenx"
    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["tokenx"]["availability"] == "hard_unavailable"
    assert updated["provider_status"]["tokenx"]["reason"] == "quota_exhausted"
    assert updated["provider_status"]["tokenx"]["source"] == "runtime_error"





def test_run_workspace_quick_provider_probe_loads_workspace_model_runtime(tmp_path):
    from nanobot.agent.subagent_resources import run_workspace_quick_provider_probe

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "model_runtime.py").write_text(
        "def quick_health_check(**kwargs):\n"
        "    return {\n"
        "        'ok': True,\n"
        "        'provider': 'custom',\n"
        "        'api_base': 'https://tokenx24.com/v1',\n"
        "        'reason': 'OK',\n"
        "        'ref': kwargs.get('ref'),\n"
        "    }\n",
        encoding="utf-8",
    )

    probe = run_workspace_quick_provider_probe(tmp_path, ref="gpt-5.4")

    assert probe is not None
    assert probe["ok"] is True
    assert probe["api_base"] == "https://tokenx24.com/v1"





def test_run_workspace_quick_provider_probe_supports_model_runtime_sibling_imports(tmp_path):
    from nanobot.agent.subagent_resources import run_workspace_quick_provider_probe

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "model_registry.py").write_text(
        "VALUE = 'tokenx'\n",
        encoding="utf-8",
    )
    (scripts_dir / "model_runtime.py").write_text(
        "try:\n"
        "    from scripts import model_registry\n"
        "except ModuleNotFoundError:\n"
        "    import model_registry\n"
        "\n"
        "def quick_health_check(**kwargs):\n"
        "    return {\n"
        "        'ok': True,\n"
        "        'provider': 'custom',\n"
        "        'api_base': 'https://tokenx24.com/v1',\n"
        "        'reason': model_registry.VALUE,\n"
        "    }\n",
        encoding="utf-8",
    )

    probe = run_workspace_quick_provider_probe(tmp_path, ref="gpt-5.4")

    assert probe is not None
    assert probe["ok"] is True
    assert probe["reason"] == "tokenx"





def test_run_workspace_quick_provider_probe_does_not_reuse_sibling_module_from_previous_workspace(tmp_path):
    from nanobot.agent.subagent_resources import run_workspace_quick_provider_probe

    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    for ws, value in ((ws1, "tokenx"), (ws2, "aizhiwen")):
        scripts_dir = ws / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "model_registry.py").write_text(
            f"VALUE = '{value}'\n",
            encoding="utf-8",
        )
        (scripts_dir / "model_runtime.py").write_text(
            "try:\n"
            "    from scripts import model_registry\n"
            "except ModuleNotFoundError:\n"
            "    import model_registry\n"
            "\n"
            "def quick_health_check(**kwargs):\n"
            "    return {\n"
            "        'ok': True,\n"
            "        'provider': 'custom',\n"
            "        'api_base': 'https://tokenx24.com/v1',\n"
            "        'reason': model_registry.VALUE,\n"
            "    }\n",
            encoding="utf-8",
        )

    first = run_workspace_quick_provider_probe(ws1, ref="gpt-5.4")
    second = run_workspace_quick_provider_probe(ws2, ref="gpt-5.4")

    assert first is not None
    assert second is not None
    assert first["reason"] == "tokenx"
    assert second["reason"] == "aizhiwen"



def test_probe_provider_route_status_skips_when_not_due(tmp_path):
    from nanobot.agent.subagent_resources import probe_provider_route_status

    registry = _registry()
    registry["provider_status_policy"] = {"probe_interval_seconds": 3600}
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "available",
            "reason": "",
            "source": "monitor_refresh",
            "updated_at": "2026-04-06T09:30:00+00:00",
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

    called: list[str] = []

    def _probe(workspace, *, ref):
        called.append(ref)
        return {"ok": True, "provider": "custom", "api_base": "https://aizhiwen.top/v1", "reason": "OK"}

    result = probe_provider_route_status(
        workspace=tmp_path,
        route="aizhiwen-top",
        now="2026-04-06T10:00:00+00:00",
        probe_runner=_probe,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "not_due"
    assert called == []



def test_probe_provider_route_status_runs_probe_when_due_and_refreshes_status(tmp_path):
    from nanobot.agent.subagent_resources import probe_provider_route_status

    registry = _registry()
    registry["provider_status_policy"] = {"probe_interval_seconds": 3600}
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "hard_unavailable",
            "reason": "quota_exhausted",
            "source": "runtime_error",
            "updated_at": "2026-04-06T08:00:00+00:00",
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

    called: list[str] = []

    def _probe(workspace, *, ref):
        called.append(ref)
        return {"ok": True, "provider": "custom", "api_base": "https://aizhiwen.top/v1", "reason": "OK"}

    result = probe_provider_route_status(
        workspace=tmp_path,
        route="aizhiwen-top",
        now="2026-04-06T10:00:00+00:00",
        probe_runner=_probe,
    )

    assert result["status"] == "updated"
    assert result["route"] == "aizhiwen-top"
    assert called == ["standard-gpt-5.4-high-aizhiwen-top"]
    updated = json.loads((tmp_path / "model_registry.json").read_text(encoding="utf-8"))
    assert updated["provider_status"]["aizhiwen-top"]["availability"] == "available"
    assert updated["provider_status"]["aizhiwen-top"]["source"] == "monitor_refresh"





def test_probe_provider_route_status_uses_archive_profile_default_for_minimax(tmp_path):
    from nanobot.agent.subagent_resources import probe_provider_route_status

    registry = _registry()
    registry["provider_status_policy"] = {"probe_interval_seconds": 3600}
    registry["provider_status"] = {
        "minimax": {
            "availability": "transient_unavailable",
            "reason": "http_502",
            "source": "runtime_error",
            "updated_at": "2026-04-06T08:00:00+00:00",
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

    called: list[str] = []

    def _probe(workspace, *, ref):
        called.append(ref)
        return {"ok": True, "provider": "minimax", "api_base": "https://api.minimaxi.com/v1", "reason": "OK"}

    result = probe_provider_route_status(
        workspace=tmp_path,
        route="minimax",
        now="2026-04-06T10:00:00+00:00",
        probe_runner=_probe,
    )

    assert result["status"] == "updated"
    assert called == ["lite-minimax-m2.7-high-minimax"]



def test_probe_due_provider_routes_only_runs_due_routes(tmp_path):
    from nanobot.agent.subagent_resources import probe_due_provider_routes

    registry = _registry()
    registry["provider_status_policy"] = {"probe_interval_seconds": 3600}
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "hard_unavailable",
            "reason": "quota_exhausted",
            "source": "runtime_error",
            "updated_at": "2026-04-06T08:00:00+00:00",
        },
        "tokenx": {
            "availability": "available",
            "reason": "",
            "source": "monitor_refresh",
            "updated_at": "2026-04-06T09:30:00+00:00",
        },
    }

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    called: list[str] = []

    def _probe(workspace, *, ref):
        called.append(ref)
        if "aizhiwen" in ref:
            return {"ok": True, "provider": "custom", "api_base": "https://aizhiwen.top/v1", "reason": "OK"}
        return {"ok": True, "provider": "custom", "api_base": "https://tokenx24.com/v1", "reason": "OK"}

    results = probe_due_provider_routes(
        workspace=tmp_path,
        now="2026-04-06T10:00:00+00:00",
        probe_runner=_probe,
    )

    assert [item["route"] for item in results] == ["aizhiwen-top", "tokenx"]
    assert results[0]["status"] == "updated"
    assert results[1]["status"] == "skipped"
    assert results[1]["reason"] == "not_due"
    assert called == ["standard-gpt-5.4-high-aizhiwen-top"]



def test_probe_due_provider_routes_respects_explicit_route_filter(tmp_path):
    from nanobot.agent.subagent_resources import probe_due_provider_routes

    registry = _registry()
    registry["provider_status_policy"] = {"probe_interval_seconds": 3600}
    registry["provider_status"] = {
        "aizhiwen-top": {
            "availability": "hard_unavailable",
            "reason": "quota_exhausted",
            "source": "runtime_error",
            "updated_at": "2026-04-06T08:00:00+00:00",
        },
        "minimax": {
            "availability": "transient_unavailable",
            "reason": "http_502",
            "source": "runtime_error",
            "updated_at": "2026-04-06T08:00:00+00:00",
        },
    }

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    called: list[str] = []

    def _probe(workspace, *, ref):
        called.append(ref)
        return {"ok": True, "provider": "minimax", "api_base": "https://api.minimaxi.com/v1", "reason": "OK"}

    results = probe_due_provider_routes(
        workspace=tmp_path,
        routes=["minimax"],
        now="2026-04-06T10:00:00+00:00",
        probe_runner=_probe,
    )

    assert [item["route"] for item in results] == ["minimax"]
    assert called == ["lite-minimax-m2.7-high-minimax"]
