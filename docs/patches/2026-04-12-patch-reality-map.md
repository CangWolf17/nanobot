# 2026-04-12 Patch Reality Map On Top Of Refreshed Upstream

## Purpose

Answer the practical questions:

1. **what patches are actually alive on the current fork mainline**
2. **how their implementation chain is wired today**
3. **whether they fit the refreshed upstream-based Nanobot or fight it**

Scope here is the live branch:

- `cangwolf/runtime-patches-2026-04-12-refresh`

This document is about the **current code reality**, not historical intent alone.

## Executive Summary

The current fork is **not** “a random pile of patches”.
It is a fairly coherent self-use runtime fork with five live patch themes:

1. **dual-track context compression**
2. **provider retry / empty-success / route-health handling**
3. **runtime-owned harness state + workspace bridge continuation**
4. **real subagent queue + guarded nested delegation**
5. **small operational guardrails** such as misarchived-session recovery and channel/completion fixes

The biggest source of confusion is this:

- the **current mainline does use** `model_registry.json` as a workspace/runtime input for subagent routing
- but the **bigger V2 in-repo `nanobot/model_registry/` architecture was not merged**

So the live fork is **not** on the full V2 model-registry branch.
It is on a smaller, more practical runtime patch line that still reads the workspace registry file directly.

## What Is Actually Live Now

Ignore the donor branches for a moment.
On the current refresh branch, the live patch surface is roughly:

### A. Context compression + compact session state

Main files:
- `nanobot/agent/loop.py`
- `nanobot/agent/memory.py`
- `nanobot/agent/compact_state.py`
- `nanobot/agent/context.py`
- `nanobot/command/builtin.py`

Main docs:
- `docs/patches/2026-04-01-context-compression-p0-minimal.md`
- `docs/patches/2026-04-03-runtime-control-plane-followups.md`

### B. Provider retry + empty-success + route/provider health

Main files:
- `nanobot/providers/base.py`
- `nanobot/providers/openai_compat_provider.py`
- `nanobot/providers/azure_openai_provider.py`
- `nanobot/providers/anthropic_provider.py`
- `nanobot/agent/subagent_resources.py`
- `nanobot/agent/subagent.py`

Main docs:
- `docs/patches/2026-04-02-self-use-runtime-patch-playbook.md`
- `docs/patches/2026-04-03-runtime-control-plane-followups.md`
- `docs/patches/2026-04-11-runtime-subagent-probe-boundary.md`

### C. Runtime-owned harness engine + projections + bridge continuation

Main files:
- `nanobot/harness/models.py`
- `nanobot/harness/store.py`
- `nanobot/harness/projections.py`
- `nanobot/harness/service.py`
- `nanobot/harness/workflows.py`
- `nanobot/command/harness.py`
- `nanobot/command/workspace_bridge.py`
- `nanobot/command/builtin.py`

Main docs:
- `docs/patches/2026-04-03-runtime-control-plane-followups.md`
- `docs/patches/2026-04-02-self-use-runtime-patch-playbook.md`

### D. Real subagent queue + policy-gated nested delegation

Main files:
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`
- `nanobot/agent/subagent_policy.py`
- `nanobot/agent/subagent_types.py`
- `nanobot/agent/tools/spawn.py`
- `nanobot/agent/tools/guarded.py`
- `nanobot/agent/tools/message.py`
- `nanobot/harness/service.py`

Main docs:
- `docs/patches/2026-04-11-runtime-subagent-v3-cleanup-execution-plan.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md`

### E. Small guardrails / glue fixes

Main files:
- `nanobot/session/manager.py`
- channel files such as `nanobot/channels/feishu.py`, `nanobot/channels/discord.py`, `nanobot/channels/weixin.py`
- CLI/command glue in `nanobot/cli/commands.py`, `nanobot/command/builtin.py`

## Design Chain By Patch Theme

## 1. Dual-track context compression

### What problem it solves

The fork does **not** want to rely on only one compression mechanism.
It splits long-session handling into:

1. **archive memory** for old turns
2. **compact session state** for the active resume summary

### Current implementation chain

1. `AgentLoop` owns both:
   - `memory_consolidator`
   - `compact_state`
2. pre-reply/background paths call:
   - `_maybe_consolidate_and_sync_compact_state()`
3. archive happens through:
   - `MemoryConsolidator.maybe_consolidate_by_tokens()`
   - `archive_messages()`
4. if archive succeeds, compact state is regenerated through:
   - `CompactStateManager.sync_session()`
5. active compact state is injected back into prompts through:
   - `ContextBuilder.build_messages(..., compact_state=...)`
6. `/new` uses a compatibility shim:
   - `loop.consolidator.archive(...)`
   - which is backed by `memory_consolidator.archive_messages`

### Why this is coherent

This is a clean chain:

- **archive** handles old transcript reduction
- **compact_state** carries the live short resume state
- prompt building consumes compact state explicitly instead of pretending archived history is enough

### Fit with refreshed upstream

**Fit: medium-high**

Why:
- it is invasive, but the layering is internally clean
- it mostly extends runtime behavior rather than replacing the whole upstream agent architecture
- the compatibility shim around `loop.consolidator.archive` helps it survive upstream differences

Main caution:
- this is still a fork opinionated memory model, not an upstream-minimal patch

## 2. Provider retry / empty-success / route health

### What problem it solves

The fork assumes real providers are messy:

- transient 429/5xx/timeout failures happen
- some providers return empty-success / malformed-success responses
- route health matters for subagent scheduling

### Current implementation chain

#### Core retry path

1. all providers flow through `LLMProvider.chat_with_retry()` / `chat_stream_with_retry()`
2. `_run_with_retry()` centralizes retry logic
3. it handles:
   - empty model response classification
   - retry-after parsing from body/headers
   - streamed delta buffering so failed attempts do not leak partial output
   - image stripping retry for non-transient image-related failures
4. retry progress can be surfaced through:
   - `on_retry`
   - `on_retry_wait`

#### Route-health path for subagents

1. `subagent_resources.py` classifies provider failures into:
   - transient unavailable
   - hard unavailable
   - quota exhausted / billing unavailable
2. it persists/refreshes route status in workspace files such as `model_registry.json`
3. `SubagentResourceManager` uses those route states when choosing/acquiring candidates
4. `SubagentManager` reports provider failures back into that route-health layer and can probe routes again

### Why this is coherent

This theme has a clear split:

- retry mechanics live in `providers/base.py`
- subagent routing policy lives in `subagent_resources.py`
- execution-time fallback lives in `subagent.py`

### Fit with refreshed upstream

**Fit: high for retry core, medium-high for route-health coupling**

Why:
- centralized retry in provider base is a good extension point and matches upstream shape reasonably well
- the route-health layer is more fork-specific, but it stays mostly inside subagent resource management instead of leaking everywhere

Main caution:
- route truth currently depends on workspace-side registry/status files, which is practical but fork-local

## 3. Runtime-owned harness engine + workspace bridge

### What problem it solves

The fork wants harness/workflow state to be owned by runtime code, not by fragile workspace helper scripts.

### Current implementation chain

1. canonical truth is:
   - `harnesses/store.json`
   - managed by `HarnessStore`
2. runtime behavior lives in:
   - `HarnessService`
3. `/harness` is now a native runtime command through:
   - `nanobot/command/harness.py`
4. projections are regenerated by runtime through:
   - `harness/projections.py`
   - markdown files become readouts, not truth
5. old `index.json` / `control.json` / per-harness `state.json` are migration/compatibility inputs, then cleaned up
6. runtime metadata is exposed through:
   - `HarnessService.runtime_metadata()`
   - including `active_harness`, `main_harness`, `delegation_level`, `risk_level`, `subagent_profile`
7. `workspace_bridge.py` uses this to prepare workflow continuation behavior
8. builtin command/status/interrupt flows also read harness runtime state

### Why this is coherent

This is one of the cleanest patch lines in the fork:

- canonical JSON store = truth
- markdown = projection
- command layer = thin wrapper
- bridge/auto-continue consume runtime metadata instead of inventing a second source of truth

### Fit with refreshed upstream

**Fit: high**

Why:
- it is broad, but architecturally coherent
- it reduces dependence on ad hoc workspace runtime scripts
- it creates cleaner truth boundaries than the older arrangement

Main caution:
- this is still a major fork-only subsystem, so upstream changes around command/workflow shape can still create future merge pressure

## 4. Real subagent queue + guarded nested delegation

### What problem it solves

Old semantics had a fake queue story.
The fork wants:

- real queued pending tasks
- route/tier-aware admission
- nested delegation with runtime-enforced boundaries
- sensitive tools only when policy allows them

### Current implementation chain

1. `SpawnTool` calls `SubagentManager.spawn()`
2. spawn request is normalized into:
   - `RuntimeSubagentSpawnRequest`
3. `SubagentResourceManager.resolve_spawn_request()` chooses candidate model chain from:
   - explicit model
   - typed subagent role
   - compatibility tier
4. `acquire_candidates()` returns:
   - granted
   - queued
   - rejected
5. queued tasks are stored in:
   - `_pending_tasks`
   - `_pending_order`
6. after a lease releases, `_drain_pending_queue()` tries to start queued work for real
7. running subagents build guarded tool surfaces through:
   - `subagent_policy.py`
   - `GuardedTool`
   - same-chat message policy
   - nested spawn budget / depth / type restrictions
8. harness runtime metadata feeds policy resolution through:
   - `delegation_level`
   - `risk_level`
   - `subagent_profile`
9. if a provider fails **before tool side effects**, `SubagentManager` can switch to a fallback lease/model

### Why this is coherent

This patch line is the most ambitious one, but it is internally understandable:

- **resource selection** = `subagent_resources.py`
- **runtime execution** = `subagent.py`
- **permission policy** = `subagent_policy.py`
- **harness-level truth** = `harness/service.py`

### Fit with refreshed upstream

**Fit: medium**

Why:
- this is the largest semantic fork from upstream behavior
- but it is not random: the chain is now explicit and test-backed
- the V3 docs and the code are broadly aligned on the queue/policy direction

Main caution:
- this area has the highest long-term merge cost
- it is also the place where old V2 docs/branches cause the most confusion

## 5. Misarchived session recovery and small operational guardrails

### What problem it solves

The fork adds some practical runtime self-healing so daily usage is less fragile.

### Current implementation chain

- `SessionManager._load()` checks the active workspace session path
- if missing, it can recover a uniquely misarchived session from `sessions.migrated*`
- the logic is intentionally conservative: if multiple archived copies exist, it refuses to guess

### Fit with refreshed upstream

**Fit: high**

Why:
- very localized
- low conceptual cost
- solves a real failure mode without distorting the rest of the system

## The Most Important Truth Boundary

The live branch currently has this reality:

### Live today

- `subagent_resources.py` reads workspace registry/state files directly
- `model_registry.json` is part of live routing/resource truth for subagents
- harness runtime metadata feeds subagent policy/runtime decisions

### Not live today

- the larger in-repo `nanobot/model_registry/` V2 architecture from donor branches
- the full responses-provider/model-registry migration line

This is the main thing future readers must not confuse.

## Does It Match The New Upstream-Based Nanobot?

### Yes, where it matches well

These patch lines still feel compatible with the refreshed upstream base:

1. **provider retry centralization**
2. **runtime-owned harness store/projections**
3. **misarchived-session recovery**
4. **compact-state overlay on top of archive memory**

These all look like deliberate runtime extensions, not accidental drift.

### Partly, with merge pressure

These fit conceptually but are the most fork-heavy:

1. **subagent queue + nested delegation policy**
2. **workspace-registry-driven route/resource management**

These still make sense, but they create the most ongoing divergence from upstream.

### No, for the deferred old line

These do **not** match the current refreshed mainline and should not be confused with it:

1. the full V2 `nanobot/model_registry/` branch
2. the donor branch responses-provider architecture
3. old “let model_state drive runtime startup truth” ideas

## Plain-Language Bottom Line

If you ask “what did we actually patch?”, the answer is:

- we patched **runtime behavior**, not just provider adapters
- the fork currently revolves around **memory/compression**, **provider resilience**, **harness runtime truth**, and **subagent runtime control**
- most of the current mainline is internally consistent
- the main thing that does **not** belong to current reality is the older V2 model-registry branch family

So the right mental model is:

> current fork = latest upstream Nanobot + a self-use runtime control-plane overlay

not:

> current fork = latest upstream + half-merged V2 registry experiment

## Recommended Next Analysis Step

If later work wants to go deeper, the next useful breakdown is:

1. map **each live patch theme** to the exact tests that currently protect it
2. then decide which themes are worth keeping fork-local long term and which should be reduced before the next upstream refresh
