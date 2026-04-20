# 2026-04-12 Live Patch → Entry Point → Test → Risk Map

## Purpose

Make the current refreshed fork readable as an operating system, not as branch folklore.

For each live patch theme, this note answers:

1. where the runtime entry points are
2. which tests currently protect it
3. how risky it is to carry across future upstream syncs

Scope:
- `cangwolf/runtime-patches-2026-04-12-refresh`

Risk scale used below:
- **low** = local patch, low merge pressure
- **medium** = coherent extension, moderate merge pressure
- **high** = deep behavioral fork, likely sync hotspot

## 1. Dual-track context compression

### What it does

Keeps long-session handling split into:
- archived history / memory consolidation
- compact session resume state

### Main runtime entry points

- `nanobot/agent/loop.py::_maybe_consolidate_and_sync_compact_state`
- `nanobot/agent/loop.py::_run_pre_reply_consolidation`
- `nanobot/agent/loop.py::_run_background_consolidation`
- `nanobot/agent/memory.py::archive_messages`
- `nanobot/agent/memory.py::maybe_consolidate_by_tokens`
- `nanobot/agent/compact_state.py::CompactStateManager.sync_session`
- `nanobot/command/builtin.py::cmd_new`

### Main protecting tests

- `tests/agent/test_compact_state.py`
- `tests/agent/test_loop_consolidation_tokens.py`
- `tests/agent/test_memory_consolidation_types.py`
- `tests/agent/test_consolidate_offset.py`
- `tests/agent/test_auto_compact.py`

### Why these tests matter

They lock down:
- compact-state prompt/update behavior
- token-budget consolidation paths
- archive fallback behavior
- `/new` archive semantics
- auto-compact background scheduling

### Carry risk

**medium**

Reason:
- the chain is internally clean
- but it changes how runtime memory works, so upstream prompt/memory changes can collide here

## 2. Provider retry / empty-success / route health

### What it does

Adds resilient provider retry behavior and lets subagent resource routing react to provider health.

### Main runtime entry points

- `nanobot/providers/base.py::chat_with_retry`
- `nanobot/providers/base.py::chat_stream_with_retry`
- `nanobot/providers/base.py::_run_with_retry`
- `nanobot/agent/subagent_resources.py::classify_provider_failure`
- `nanobot/agent/subagent_resources.py::apply_provider_probe_result`
- `nanobot/agent/subagent_resources.py::apply_provider_failure_to_manager`
- `nanobot/agent/subagent_resources.py::build_manager_from_workspace_snapshot`
- `nanobot/agent/subagent.py::_acquire_fallback_lease`

### Main protecting tests

- `tests/providers/test_provider_retry.py`
- `tests/providers/test_provider_error_metadata.py`
- `tests/providers/test_provider_retry_after_hints.py`
- `tests/agent/test_empty_success.py`
- `tests/agent/test_runner.py`
- `tests/agent/test_task_cancel.py`

### Why these tests matter

They lock down:
- transient retry rules
- retry-after parsing
- structured error metadata
- empty-success handling
- stream retry buffering without leaked deltas
- route-health persistence/update behavior for subagents

### Carry risk

**medium**

Reason:
- retry centralization is a strong design fit
- but route health is fork-specific and depends on workspace registry/status files

## 3. Runtime-owned harness + workspace bridge continuation

### What it does

Moves harness truth into runtime-managed store/projections and uses runtime metadata to drive continuation behavior.

### Main runtime entry points

- `nanobot/harness/store.py::HarnessStore`
- `nanobot/harness/service.py::HarnessService`
- `nanobot/harness/service.py::runtime_metadata`
- `nanobot/harness/service.py::_runtime_payload`
- `nanobot/harness/service.py::build_auto_continue_metadata`
- `nanobot/harness/projections.py::sync_workspace_projections`
- `nanobot/command/harness.py::cmd_harness`
- `nanobot/command/workspace_bridge.py::prepare_active_workflow_continuation`
- `nanobot/command/workspace_bridge.py::cmd_workspace_bridge`

### Main protecting tests

- `tests/harness/test_store.py`
- `tests/harness/test_service.py`
- `tests/harness/test_projections.py`
- `tests/command/test_harness_command.py`
- `tests/command/test_workspace_bridge.py`
- `tests/command/test_workspace_bridge_harness.py`
- `tests/command/test_workspace_workflow_continuation.py`
- `tests/agent/test_loop_workspace_progress.py`

### Why these tests matter

They lock down:
- canonical harness store behavior
- workflow/harness command semantics
- markdown projection regeneration
- runtime metadata payload shape
- workspace bridge continuation routing
- loop-side workspace progress / auto-continue behavior

### Carry risk

**medium-high**

Reason:
- architecturally coherent and valuable
- but broad enough that upstream command/workflow churn can hit many edges at once

## 4. Real subagent queue + guarded nested delegation

### What it does

Turns queued subagents into real runtime objects, adds typed/model-based selection, and enforces nested delegation/message boundaries through policy.

### Main runtime entry points

- `nanobot/agent/subagent.py::SubagentManager`
- `nanobot/agent/subagent.py::spawn`
- `nanobot/agent/subagent.py::_drain_pending_queue`
- `nanobot/agent/subagent_resources.py::SubagentResourceManager`
- `nanobot/agent/subagent_resources.py::acquire_candidates`
- `nanobot/agent/subagent_resources.py::resolve_spawn_request`
- `nanobot/agent/subagent_policy.py::SubagentRunContext`
- `nanobot/agent/subagent_policy.py::SubagentToolPolicy`
- `nanobot/agent/tools/guarded.py::GuardedTool`
- `nanobot/agent/tools/spawn.py::SpawnTool`

### Main protecting tests

- `tests/agent/test_subagent_queue.py`
- `tests/agent/test_subagent_policy.py`
- `tests/agent/test_subagent_guarded_tools.py`
- `tests/agent/test_subagent_resources.py`
- `tests/agent/test_subagent_types.py`
- `tests/agent/test_task_cancel.py`

### Why these tests matter

They lock down:
- real queue semantics
- spawn request resolution
- route/tier/model selection
- nested budget/depth enforcement
- guarded message/spawn rules
- cancellation and fallback behavior across subagent paths

### Carry risk

**high**

Reason:
- this is the deepest semantic fork from upstream behavior
- it touches runtime policy, resource routing, execution, and nested-tool surfaces at once

## 5. Small operational guardrails / channel and CLI glue

### What it does

Carries practical self-healing and UX fixes around sessions, channels, and command glue.

### Main runtime entry points

- `nanobot/session/manager.py::_recover_misarchived_session`
- channel implementations such as:
  - `nanobot/channels/feishu.py`
  - `nanobot/channels/discord.py`
  - `nanobot/channels/weixin.py`
- command/CLI glue such as:
  - `nanobot/cli/commands.py`
  - `nanobot/command/builtin.py`

### Main protecting tests

- `tests/agent/test_consolidate_offset.py`
- `tests/channels/test_feishu_streaming.py`
- `tests/channels/test_channel_plugins.py`
- `tests/channels/test_matrix_channel.py`
- `tests/channels/test_telegram_channel.py`
- `tests/channels/test_weixin_channel.py`
- `tests/cli/test_commands.py`

### Why these tests matter

They lock down:
- unique misarchived-session recovery
- streaming/completion placeholder behavior
- channel plugin/config paths
- message/channel command wiring

### Carry risk

**low-medium**

Reason:
- many of these fixes are local and practical
- but channel code can still drift quickly if upstream refactors transport behavior

## Practical Recommendation

If future work must reduce fork burden, the order should be:

1. keep and stabilize **provider retry / empty-success**
2. keep and stabilize **runtime harness / workspace bridge**
3. keep **small guardrails**
4. keep **dual-track compression** only if it continues to prove operational value
5. treat **subagent queue / guarded delegation** as the primary high-maintenance fork surface and review it explicitly before every major upstream refresh

## Bottom Line

The fork is currently protected by a real test surface, not by hope.

The most important thing to remember is:
- the **highest-value / most coherent patches** are retry + harness/runtime truth
- the **highest-maintenance patch** is subagent queue/policy
- the **most confusing non-live line** is still the unmerged V2 model-registry experiment
