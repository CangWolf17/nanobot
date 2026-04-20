# 2026-04-12 Subagent Simplification Review

## Purpose

This note answers one practical question against the **current live refresh branch**:

> if we want to keep the self-use subagent line but reduce future upstream-sync pain, what should stay, what should shrink, and what should gradually leave the hot path?

Scope:
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`
- `nanobot/agent/subagent_policy.py`
- `nanobot/agent/tools/spawn.py`
- `nanobot/agent/tools/guarded.py`
- `nanobot/agent/subagent_types.py`

This is based on the **actual code and current protecting tests**, not on donor-branch intent.

## Executive Summary

The current subagent patch is carrying **one good core idea** and **three layers of extra fork weight**.

### The good core idea

Keep a real queued subagent runtime with:
- bounded spawn selectors (`type` or `model`)
- minimal nested-delegation policy
- guarded message/spawn tools
- provider fallback before tool side effects

That core is coherent and test-backed.

### The extra fork weight

Most maintenance burden comes from these add-ons:
1. **compatibility surfaces** (`label`, `tier`, compatibility-tier resolution)
2. **route-health and provider-probe machinery living inside the same module as queue admission**
3. **rich runtime prompt/context injection duplicated into both system prompt and task payload**
4. **multiple policy gates for the same decision spread across spawn tool, manager authorization, and harness metadata checks**

So the right move is **not** “delete subagents”.
The right move is:

> keep the queue + guardrails, then reduce the compatibility and orchestration bulk around them.

## What Is Essential And Should Stay

### 1. Real queue lifecycle in `SubagentManager`

Keep:
- `spawn`
- `_enqueue_pending`
- `_dequeue_pending`
- `_drain_pending_queue`
- `cancel_by_session`

Why:
- this is the real behavioral difference the fork wanted
- it is directly protected by `tests/agent/test_subagent_queue.py` and `tests/agent/test_task_cancel.py`
- it solves a real operational problem: subagents can wait for capacity instead of failing immediately

Recommendation:
- keep the queue semantics
- do **not** try to “simplify” by falling back to fake immediate spawns

### 2. Minimal nested-tool policy

Keep:
- `SubagentRunContext`
- `SubagentToolPolicy`
- `resolve_subagent_tool_policy`
- `GuardedTool`
- `_check_message_policy`
- `_check_spawn_policy`

Why:
- this is the smallest viable guardrail layer for nested delegation
- tests already lock down the intended policy behavior:
  - `tests/agent/test_subagent_policy.py`
  - `tests/agent/test_subagent_guarded_tools.py`
  - guarded-tool registration checks in `tests/agent/test_subagent_queue.py`

Recommendation:
- keep the policy matrix small
- resist adding new profiles unless there is a real operational case

### 3. Type-based selection (`worker`, `explorer`) plus explicit model override

Keep:
- `subagent_types.py`
- `resolve_spawn_request(... requested_type / requested_model ...)`
- built-in `worker` and `explorer` types

Why:
- this gives a clean public shape: callers can say “do work” or “go inspect”, or override with an explicit model
- it is already constrained and understandable
- it is covered by `tests/agent/test_subagent_types.py` and parts of `tests/agent/test_subagent_resources.py`

Recommendation:
- keep `type` and `model` as the only first-class selectors

### 4. Fallback before tool side effects

Keep:
- `_acquire_fallback_lease`
- provider failure feedback into resource state
- release/drain interactions after failures

Why:
- this is one of the most practically valuable subagent-specific runtime protections
- it prevents a bad provider from half-executing tools before the fallback path kicks in
- it aligns with the broader retry/route-health patch line

Recommendation:
- keep this behavior even if other parts of the subagent stack are reduced

## What Should Be Simplified Next

### 1. Remove compatibility inputs from the public spawn surface

Current burden:
- `SpawnTool` still advertises and accepts deprecated `label`
- `SpawnTool` still accepts deprecated `tier`
- `SubagentManager.spawn` still carries `label` and `tier`
- `RuntimeSubagentSpawnRequest` still carries `compatibility_tier`
- `resolve_spawn_request` has a full compatibility tier branch

Why this is expensive:
- it creates two APIs at once: the new `type/model` world and the old compatibility world
- it forces resource resolution to support a path we already do not want as the primary mental model

Recommended reduction:
1. keep reading `label`/`tier` only as short-term compatibility inputs
2. stop documenting them as meaningful public routing paths
3. remove `compatibility_tier` resolution after callers are migrated
4. reduce `spawn` to one public contract: `task + type` or `task + model`

Tests most affected:
- `tests/agent/test_subagent_resources.py`
- `tests/agent/test_subagent_queue.py`

### 2. Collapse duplicate spawn-policy enforcement into one canonical gate

Current burden:
- `SpawnTool.execute` blocks on harness metadata
- `_authorize_spawn_request` blocks on harness metadata again
- `_check_spawn_policy` also checks nested depth/budget/type/model
- `_resolve_subagent_policy` also clamps by `level_limit`

Why this is expensive:
- same decision is encoded in multiple layers
- future changes can drift and accidentally create contradictory behavior

Recommended reduction:
- keep one canonical authorization path inside `SubagentManager`
- let `SpawnTool` stay a thin transport wrapper
- reserve `GuardedTool` for local tool-call policy only

Tests most affected:
- `tests/agent/test_subagent_policy.py`
- `tests/agent/test_subagent_queue.py`

### 3. Make subagent execution context much smaller

Current burden:
- `_build_subagent_execution_context` emits a long structured block
- that block is injected into the system prompt
- then it can also be injected again into the task payload
- runtime metadata is verbose and tightly coupled to harness internals

Why this is expensive:
- high merge pressure against future upstream prompt changes
- more prompt surface means more hidden behavioral coupling than the queue itself actually needs
- duplicated injection into both prompt and task payload is unnecessary bulk

Recommended reduction:
- keep only a small context block with:
  - workspace path
  - active harness id/type if present
  - depth / remaining budget / profile
  - one sentence on reporting expectations
- inject it once, not twice
- treat the rest of the harness/runtime metadata as optional observability, not required prompt truth

Tests most affected:
- any prompt-shape assertions around subagent context
- queue/policy tests should mostly survive unchanged

### 4. Narrow route-preference logic before touching queue semantics

Current burden:
- `SubagentResourceManager` handles:
  - queue admission
  - model ref resolution
  - type-based candidate selection
  - preferred-route ranking
  - availability tiering (`healthy` / `transient` / `unhealthy`)
  - concurrency quotas
  - reserved request windows

Why this is expensive:
- resource selection is carrying more scheduler policy than the queue actually needs to exist
- every extra branch here increases upstream-refresh review cost

Recommended reduction:
- keep route availability buckets and queue admission
- simplify preferred-route behavior to a smaller rule set:
  - prefer explicitly requested main route when healthy
  - otherwise fall back by stable registry order or tier policy order
- avoid growing special-case ranking behavior further

Tests most affected:
- `tests/agent/test_subagent_resources.py`

## What Should Move Out Of The Hot Path Later

### 1. Provider probing and workspace-script fallback

Current burden in `subagent_resources.py`:
- `run_runtime_quick_provider_probe`
- `run_default_provider_probe`
- `run_legacy_workspace_provider_probe`
- workspace file persistence around provider status

Judgment:
- useful, but this is no longer “just subagent resource selection”
- it is really a separate provider-health subsystem

Recommendation:
- keep the behavior for now
- later split provider probing / status persistence away from the queue-and-selection core
- especially treat the legacy workspace-script fallback as a deprecation candidate

### 2. Rich provider reconstruction from leases

Current burden in `subagent.py`:
- `_build_provider_for_lease` rebuilds providers from registry records and connection metadata
- multiple provider backends and custom/openai-compatible fallbacks are handled here

Judgment:
- functionally important, but it makes `SubagentManager` own too much provider-construction detail

Recommendation:
- keep the capability
- later move provider-construction details into a narrower helper/factory boundary so the manager only orchestrates

## What Should Not Be Touched Yet

Do **not** rip out these pieces during the first simplification pass:
- the real queue itself
- guarded nested spawn/message checks
- explicit type/model selection
- fallback-before-tool-side-effects behavior

Those are the parts most tied to the actual self-use value of the patch.

## Recommended Reduction Order

### Pass 1 — low-risk shrink
1. de-emphasize then remove `label`/`tier` compatibility paths
2. keep `SpawnTool` thin and centralize authorization in `SubagentManager`
3. shrink subagent prompt/context injection

### Pass 2 — medium-risk boundary cleanup
4. split provider probing/status persistence concerns away from queue admission logic
5. narrow route-preference logic to the smallest rule set that still preserves healthy fallback behavior

### Pass 3 — high-value de-fork pass
6. review whether the current nested delegation policy needs all existing harness couplings, or whether it can be reduced to depth/budget/profile plus one runtime allow/deny bit
7. only after that, revisit whether any queue-specific behavior can converge toward upstream

## Bottom Line

The main simplification target is **not** the existence of queued subagents.
The main simplification target is the amount of **compatibility, routing, and metadata orchestration** currently wrapped around them.

In plain terms:
- keep the queue
- keep the guardrails
- keep fallback safety
- shrink the compatibility layer
- shrink the prompt/context layer
- gradually separate provider-health machinery from core queue admission
