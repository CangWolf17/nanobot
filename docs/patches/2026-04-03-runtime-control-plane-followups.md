# Runtime Control-Plane Follow-ups

## Goal

Record the second-wave self-use runtime patches added after fork cutover, so future rebases can preserve both behavior and architecture intent.

These follow-ups turn several prompt-only local conventions into explicit runtime behavior:

1. unified empty-success recovery
2. dual-track compacting (`archive memory` vs `session compact state`)
3. lightweight runtime protocol state for work mode / gates / skill hints
4. workspace-driven help fastlane plus narrow L4 exec fastlane
5. minimal runtime-side readiness for moving `/笔记` from script-first to workflow

---

## 1. Unified Empty-Success Recovery

### Problem

Some provider calls completed without tool calls and without usable text content. Different layers reacted differently:

1. provider retry logic only retried explicit `finish_reason="error"`
2. runner could treat empty-success as a normal completion
3. API / loop fallback strings diverged

### Patch

`nanobot/providers/base.py` now classifies empty-success as a runtime error (`empty model response`) and routes it through the same retry path as transient provider failures.

`nanobot/agent/runner.py`, `nanobot/agent/loop.py`, and `nanobot/api/server.py` now share the same user-facing policy string family for final fallback handling.

### Effect

The runtime now behaves consistently when the model returns no usable text:

1. retry in provider layer
2. surface a stable user message if retries still fail
3. stop using the older English fallback string in API / loop safety nets

---

## 2. Dual-Track Compacting

### Problem

The older runtime only had one practical compression track: archive old turns into `memory/MEMORY.md` and `memory/HISTORY.md`, then drop them from active prompt history.

That preserved durable memory, but it did not preserve a session-scoped resume state comparable to a compacted working context.

### Patch

The fork now keeps two distinct tracks:

1. `MemoryConsolidator` remains archive-only
2. `CompactStateManager` maintains a session-local compact state in session metadata

Key points:

1. archived history still writes to `MEMORY.md` and `HISTORY.md`
2. compact state is incremental and tracks its own offset
3. compact state is injected into the system prompt separately from long-term memory
4. prompt token estimation includes compact state so budgeting matches real prompt shape
5. when `compact_state_enabled=false`, the runtime now disables compact-state sync, prompt injection, and prompt token estimation consistently

### Effect

Long-term facts and short-lived working state are no longer conflated.

---

## 3. Lightweight Runtime Protocol State

### Problem

Before this patch, the runtime mostly inferred work mode and discipline from loosely formatted prompt text and workspace `dev_state` fields.

### Patch

Workspace `dev_state` now normalizes a small `runtime_protocol` block. The runtime reads that protocol and exposes it explicitly in the system prompt.

Current protocol fields:

1. `version`
2. `strict_dev_mode`
3. `task_kind`
4. `phase`
5. `work_mode`
6. `current_step`
7. gate summaries

The runtime also maps protocol phase to skill hints:

1. `planning -> writing-plans`
2. `debug_required -> systematic-debugging`
3. `red_required -> test-driven-development`
4. `verify_required -> verification-before-completion`

### Effect

Prompt guidance and tool guards now share the same small control-plane vocabulary instead of depending only on informal prose.

---

## 4. Workspace Fastlane

### Problem

The workspace router was already authoritative for slash commands, but the runtime did not have a pure pre-lock path for workspace help and safe read-only script commands.

### Patch

The workspace command registry is now the capability source for fastlane eligibility. The router exposes a pure `--route-json` decision surface, and the runtime consumes that surface in `nanobot/command/fastlane.py`.

Two lanes exist:

1. `help_fastlane`
2. `exec_fastlane`

`help_fastlane` handles:

1. `/help`
2. `/help <cmd>`
3. `/<cmd> help`

`exec_fastlane` is intentionally narrow and currently limited to exact L4 read-only script calls declared in workspace metadata.

Current whitelist:

1. `/model current`
2. `/model list`
3. `/model health`
4. `/idea list`
5. `/idea check`
6. `/idea snaps`
7. `/体重 list`
8. `/体重 stats`

### Effect

These commands can be answered before the normal session dispatch lock, while everything else still falls back to the existing workspace router path.

---

## 5. Minimal `/笔记` Workflow Readiness

### Problem

The runtime bridge already supported workflow-style slash commands, but command-specific prepare/postprocess handling was still gated by a runtime-side allowlist.

That meant workspace-only changes were not enough to move `/笔记` from `script` to `[AGENT]笔记`: the router could emit the marker, but the runtime would not preserve the command metadata or request prepared input.

### Patch

The fork now treats `笔记` as a workflow-capable workspace command at the runtime boundary:

1. `nanobot/command/workspace_bridge.py` now accepts `笔记` in the prepare-input and postprocessable command sets
2. `nanobot/agent/loop.py` now emits a stable workflow progress hint for `workspace_agent_cmd="笔记"`

### Effect

Workspace can now continue `/笔记` workflow work without another runtime bridge patch, as long as the workspace side later provides:

1. router output of `[AGENT]笔记`
2. optional `--prepare-agent-input 笔记`
3. optional `--postprocess-agent 笔记`

This is intentionally minimal. It does **not** implement note drafting / pending state / confirm-write behavior inside the fork runtime.

---

## 6. Heartbeat Execution Contract Hardening

### Problem

Heartbeat already had a two-phase structure:

1. Phase 1 decides whether active tasks exist
2. Phase 2 executes the returned task summary through the normal agent loop

But Phase 2 previously passed the raw task summary into a long-lived `heartbeat` session without any execution-specific framing.

That created two practical failures:

1. the model could misread the heartbeat task summary as background metadata instead of an execution order
2. stale `heartbeat` session history could reinforce the bad pattern (`just record context, do not start work`)

### Patch

`nanobot/cli/commands.py` now hardens the Phase 2 contract:

1. builds an explicit heartbeat execution message that says this is **not background metadata** and should execute immediately
2. uses an isolated execution session key (`heartbeat:exec`) instead of the older bare `heartbeat` key
3. retains recent history on that isolated execution session, preserving bounded short-term continuity without reusing the polluted legacy session

### Effect

Heartbeat-triggered work is much less likely to stall in a fake "context acknowledged, waiting for authorization" mode.

The runtime now makes the execution intent explicit instead of hoping the model infers it from a task summary alone.

---

## Patch Surface

### Runtime

1. `nanobot/providers/base.py`
2. `nanobot/agent/runner.py`
3. `nanobot/agent/loop.py`
4. `nanobot/agent/memory.py`
5. `nanobot/agent/compact_state.py`
6. `nanobot/agent/context.py`
7. `nanobot/agent/policy/dev_discipline.py`
8. `nanobot/agent/skills.py`
9. `nanobot/command/fastlane.py`
10. `nanobot/command/workspace_bridge.py`
11. `nanobot/config/schema.py`

### Runtime tests

1. `tests/providers/test_provider_retry.py`
2. `tests/agent/test_empty_success.py`
3. `tests/agent/test_compact_state.py`
4. `tests/agent/test_protocol_state.py`
5. `tests/command/test_fastlane.py`
6. `tests/command/test_workspace_bridge.py`
7. `tests/test_openai_api.py`
8. `tests/agent/test_loop_consolidation_tokens.py`
9. `tests/agent/test_loop_workspace_progress.py`

### Workspace dependencies

1. `~/.nanobot/workspace/scripts/command_registry.py`
2. `~/.nanobot/workspace/scripts/router.py`
3. `~/.nanobot/workspace/scripts/router_help.py`
4. `~/.nanobot/workspace/scripts/router_task_adapter.py`
5. `~/.nanobot/workspace/scripts/dev_state.py`

These workspace files are still external companion assets, not vendored into the fork repo.

---

## Focused Verification

### Runtime

```bash
/home/admin/.nanobot-fork/venv/bin/python -m pytest \
  tests/agent/test_empty_success.py \
  tests/providers/test_provider_retry.py \
  tests/agent/test_runner.py \
  tests/test_openai_api.py \
  tests/agent/test_compact_state.py \
  tests/agent/test_loop_consolidation_tokens.py \
  tests/agent/test_memory_consolidation_types.py \
  tests/agent/test_protocol_state.py \
  tests/command/test_fastlane.py \
  tests/command/test_workspace_bridge.py -q
```

### Workspace

```bash
/home/admin/.nanobot/workspace/venv/bin/python -m pytest \
  tests/test_session_store.py \
  tests/test_router_task_adapter.py \
  tests/test_router.py \
  tests/test_command_flows.py \
  tests/test_command_matrix.py -q
```

---

## Rebase Guidance

When rebasing this fork onto a newer upstream base, preserve these rules:

1. empty-success classification belongs in shared provider/runtime code, not ad hoc per-entrypoint fallbacks
2. archive memory and session compact state must remain separate tracks
3. runtime protocol should stay explicit and small; do not bury it back into prose-only prompt blocks
4. fastlane eligibility should stay declared by workspace command metadata, not hardcoded in runtime-only tables
5. if more workflow commands move from script-first to agent-first, keep the runtime boundary generic and minimal; workspace should own command semantics
