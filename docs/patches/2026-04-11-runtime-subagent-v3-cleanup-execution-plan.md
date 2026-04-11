# 2026-04-11 Runtime Subagent V3 — Cleanup + Planning + Execution Plan

## Recommendation

先整理当前 runtime repo 的脏改动边界，再按 V3 方案分阶段实施。

不要直接在当前混合脏树上继续写真队列。

原因很简单：
- 现在 runtime repo 同时混着 subagent 主线、provider probe 主线、streaming 修复、以及若干草稿文档
- 不先整理边界，后续 commit 会把 unrelated changes 缠死
- 真队列 + guarded delegation 本身已经够复杂，不值得再把 Feishu streaming 混进来

---

## 1. Current Cleanup Findings

## A. Workspace repo status

Workspace 已单独完成并提交 model / registry / runtime projection 相关改动。

Committed:
- `2f8ceaf feat(model): harden registry and runtime projection contracts`

Meaning:
- model / registry 这一坨已经从当前后续工作中剥离出去
- 接下来 V3 方案不需要再回头处理 workspace 那批未提交问题

## B. Runtime repo dirty tree themes

Current dirty runtime tree contains **three distinct themes**.

### Theme 1 — Subagent / provider-health / route-resolution mainline

Relevant files:
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`
- `nanobot/agent/tools/spawn.py`
- `nanobot/agent/loop.py`
- `nanobot/agent/subagent_types.py`
- `tests/agent/test_loop_workspace_progress.py`
- `tests/agent/test_subagent_resources.py`
- `tests/agent/test_task_cancel.py`
- `tests/agent/test_subagent_types.py`
- docs under `docs/patches/2026-04-11-runtime-subagent-*`

What this theme already includes:
- `worker` / `explorer` type surface
- spawn API shift toward `type + model`
- preferred route inheritance via `main_agent_model_ref` / `main_agent_route`
- runtime-native provider probing
- route-aware candidate ordering and fallback semantics

Assessment:
- **Keep** this as the base mainline for V3
- it is directly relevant to the real queue / guarded delegation direction

### Theme 2 — Feishu / channel streaming patch

Relevant files:
- `nanobot/channels/feishu.py`
- `nanobot/channels/manager.py`
- `tests/channels/test_channel_plugins.py`
- `tests/channels/test_feishu_streaming.py`
- plus the streaming-related hunks inside `nanobot/agent/loop.py`

What it does:
- introduces `_stream_start` handling
- makes Feishu streaming card creation explicit before first visible delta
- updates manager dispatch to send `_stream_start` through `send_delta`

Assessment:
- **Valid work, but unrelated to V3 queue/policy core**
- should be split into its own commit/branch before V3 implementation continues

### Theme 3 — Draft/planning docs

Relevant files:
- `docs/patches/2026-04-11-runtime-subagent-probe-boundary.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-draft.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-implementation-plan.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md`

Assessment:
- **Keep**
- these are useful architecture anchors
- but do not treat all of them as simultaneously normative

Recommended doc truth after cleanup:
- `probe-boundary.md` = accepted boundary note
- `v2-draft.md` = historical design context
- `v2-implementation-plan.md` = historical execution context
- `v3-real-queue-plan.md` = current architecture target
- this document = current cleanup/execution order

---

## 2. Cleanup Decision

## What to keep as implementation base

Keep as V3 implementation base:
- typed spawn contract (`worker` / `explorer`)
- preferred-route metadata injection from main agent
- runtime-native provider probing and route health handling
- current subagent resource-manager direction

## What to isolate before V3 implementation

Split out before V3 queue work proceeds:
- Feishu/channel streaming patch

Why:
- it touches `loop.py` too
- it will cause ugly merge/conflict noise exactly where queue work also needs changes
- queue correctness and streaming correctness should be reviewable independently

## What to leave as docs only

Leave docs unbundled from code implementation until code catches up:
- `v2-draft`
- `v2-implementation-plan`
- `v3-real-queue-plan`
- this cleanup plan

---

## 3. Recommended Git Cleanup Sequence

## Step 1 — Freeze the architecture docs

Create a docs-only commit in runtime repo containing:
- `docs/patches/2026-04-11-runtime-subagent-probe-boundary.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-draft.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-implementation-plan.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md`
- this file

Why first:
- preserves intent
- lets later code commits reference stable docs

## Step 2 — Split out Feishu/channel streaming patch

Commit separately:
- `nanobot/channels/feishu.py`
- `nanobot/channels/manager.py`
- `tests/channels/test_channel_plugins.py`
- `tests/channels/test_feishu_streaming.py`
- only the streaming-specific hunks in `nanobot/agent/loop.py`

Do **not** leave this mixed with V3 subagent work.

## Step 3 — Rebase/refresh subagent mainline patch set

Then keep only the subagent mainline files dirty:
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`
- `nanobot/agent/tools/spawn.py`
- `nanobot/agent/loop.py` (subagent metadata bits only)
- `nanobot/agent/subagent_types.py`
- corresponding tests

After that, V3 can be implemented on a relatively clean subagent surface.

---

## 4. Planning Summary

## Product direction

The runtime should become capable of:

1. true queued subagent admission
2. typed subagent intent (`worker` / `explorer`)
3. guarded future exposure of `message` / `spawn`
4. bounded nested delegation using `task_budget` and `level_limit`

## Explicit product stance

### Main agent
- can spawn subagents with `task + type` or `task + model`
- receives immediate started / queued / rejected feedback

### Subagent
- default: no sensitive tools
- selected profiles may get:
  - `message`
  - `spawn`
- nested delegation remains runtime-controlled, not model-self-controlled

### Queue
- queued means a real pending runtime object
- queued tasks must eventually start when capacity returns
- queued tasks must be cancellable by session

---

## 5. Execution Plan

## Phase 0 — Repo cleanup / savepoint

### Goal
Separate unrelated runtime dirty work so V3 can proceed cleanly.

### Deliverables
- docs-only savepoint commit
- streaming-only commit
- subagent dirty tree reduced to subagent mainline files only

### Verification
- `git status` in runtime repo shows only subagent-mainline files still dirty after cleanup

---

## Phase 1 — Runtime metadata completion

### Goal
Make runtime policy inputs actually available to subagent resolution.

### Files
- `nanobot/harness/service.py`
- `tests/agent/test_loop_workspace_progress.py`

### Changes
Extend harness runtime payload to include:
- `delegation_level`
- `risk_level`
- `subagent_profile`

### Acceptance
- runtime metadata tests assert these fields are present
- `SpawnTool` context continues to receive `workspace_runtime`

---

## Phase 2 — Policy skeleton + guarded tools

### Goal
Create the enforcement layer before exposing sensitive tools.

### Files
- `nanobot/agent/subagent_policy.py` (new)
- `nanobot/agent/tools/guarded.py` (new)
- `nanobot/agent/tools/spawn.py`
- unit tests

### Changes
Add:
- `SubagentRunContext`
- `SubagentToolPolicy`
- profile resolver
- `GuardedTool`
- `subagent_runtime` metadata pass-through in `SpawnTool`

### Acceptance
- policy tests pass
- guarded wrapper tests pass
- no runtime behavior change yet for default profile

---

## Phase 3 — Real queue core

### Goal
Turn queued status into a real executable pending queue.

### Files
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`
- tests

### Changes
Add pending queue data structures:
- `_pending_tasks`
- `_pending_order`
- `_session_pending`

Add:
- queue admission path in `spawn()`
- `_drain_pending_queue()`
- cancellation support for pending tasks
- queue-aware introspection helpers

### Acceptance
- over-capacity spawn returns queued instead of rejected
- queued task starts after lease release
- `cancel_by_session()` removes pending tasks too
- queue limit enforced

---

## Phase 4 — Nested delegation control

### Goal
Wire `task_budget` and `level_limit` into real nested runtime control.

### Files
- `nanobot/agent/subagent.py`
- `nanobot/agent/tools/spawn.py`
- tests

### Changes
Add `subagent_runtime` propagation:
- `depth`
- `remaining_budget`
- `profile`
- `parent_task_id`

Enforce in both:
- tool wrapper checker
- manager admission checker

### Acceptance
- `level_limit=1` blocks nested spawn
- `level_limit=2` allows one nested layer
- budget decrements on child context
- budget exhaustion blocks further nested spawn

---

## Phase 5 — Controlled exposure of `message` / `spawn`

### Goal
Expose sensitive tools only under profile control.

### Files
- `nanobot/agent/subagent.py`
- `nanobot/agent/tools/message.py` (only if metadata tagging tweak is needed)
- tests

### Changes
Conditional registration:
- `notify` => guarded `message`
- `delegate` => guarded `spawn`
- `orchestrator` => both

Phase-1 restrictions:
- `message`: same-chat, text-only, no media
- `spawn`: only `type=worker|explorer`, no explicit `model`

### Acceptance
- same-chat message allowed under notify/orchestrator
- cross-chat blocked
- media blocked
- explicit spawn model blocked
- disallowed type blocked

---

## Phase 6 — Operational polish

### Goal
Make the system debuggable and maintainable.

### Candidate follow-ups
- queue length/status surfaces
- richer queue logs
- dedupe repeated queue requests in same session
- optional later fairness improvements
- optional suppression of duplicate final summary after direct subagent message

---

## 6. Test Matrix

| area | tests |
|---|---|
| runtime metadata | `tests/agent/test_loop_workspace_progress.py` |
| policy resolution | `tests/agent/test_subagent_policy.py` |
| guarded tools | `tests/agent/test_subagent_guarded_tools.py` |
| queue core | `tests/agent/test_subagent_queue.py` or expanded existing tests |
| nested limits | `tests/agent/test_task_cancel.py` or dedicated nested tests |
| typed spawn | `tests/agent/test_subagent_types.py`, `tests/agent/test_subagent_resources.py` |

Focused verification command after subagent phases:

```bash
uv run pytest \
  tests/agent/test_loop_workspace_progress.py \
  tests/agent/test_subagent_resources.py \
  tests/agent/test_task_cancel.py \
  tests/agent/test_subagent_types.py
```

If new queue/policy files are added, include them explicitly.

---

## 7. Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| queue logic and streaming logic conflict in `loop.py` | both touch runtime message flow | split streaming patch first |
| fake fairness in v1 queue | top queue item may block later work | start with FIFO correctness, then improve with telemetry |
| nested spawn runaway | exposing `spawn` without hard caps is dangerous | enforce `level_limit` + `task_budget` twice |
| capability/model truth bleed | stuffing policy into registry will rot fast | keep execution policy in runtime/harness layer |
| cancellation regressions | `/stop` and `/interrupt` rely on current manager behavior | extend existing manager cancellation tests before changing semantics |

---

## 8. Recommendation Order

Do this first:

1. docs savepoint commit
2. streaming patch split
3. metadata completion
4. policy skeleton
5. real queue core
6. nested limits
7. controlled tool exposure

This order minimizes review pain and keeps correctness ahead of surface area.

**Key Principle:** 先把 repo 脏改动拆干净，再把 queue 做真、权限做硬；否则你会一边修架构，一边在 unrelated patch 噪音里找死。