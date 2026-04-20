# 2026-04-11 Runtime Subagent V3 — Real Queue + Guarded Delegation Plan

## Recommendation

先做真正可用的长期方案，不再维持“queued 但其实直接 reject”的半成品语义。

推荐主线：

1. **落地真实 subagent queue**，把 `queued` 从假状态变成可执行状态
2. **同时引入 subagent tool policy**，为将来开放 `message` / `spawn` 提供 runtime 级闸门
3. **先开放骨架，不默认放权**：默认 profile 仍无 `message` / `spawn`
4. **把 `task_budget` / `level_limit` 接成 nested subagent 的硬边界**
5. **保留主 agent / subagent 的模型真相边界**，不把权限设计和 model registry 糊成一锅

这条线更重，但这是唯一不会越改越屎的路径。

---

## Current Problems

### 1. Queue is fake

当前 `SubagentResourceManager.acquire_candidates()` 会返回：

- `granted`
- `queued`
- `rejected`

但 `SubagentManager.spawn()` 只接受 `granted`，其余状态直接返回 rejected 文本。

结果：

- `allow_queue` / `queue_limit` 看起来存在
- 实际 runtime 没有 pending queue
- release 也不会触发排队任务调度

这是假队列。

### 2. Sensitive subagent tools have no runtime policy system

当前 subagent 固定拥有：

- filesystem tools
- exec
- web search/fetch

固定没有：

- `message`
- `spawn`

问题不是“没给就安全”，问题是：

- 想要后续开放时，没有统一 policy 层
- 权限字段在 harness 模型里已经存在，但 runtime metadata 没完整透出

### 3. Nested subagent control is not wired

`model_registry.json.subagent_defaults` 里已经有：

- `task_budget`
- `level_limit`

但 runtime 当前没有真正消费它们。

### 4. Truth boundaries are still easy to confuse

当前事实：

- 主 agent 启动模型真相：`config.json.agents.defaults.model`
- subagent 默认模型真相：`model_registry.json.subagent_defaults.model`
- route health / concurrency / quotas：`model_registry.json.provider_status` + `provider_policies`
- `model_state.json`：状态缓存，不应成为 runtime 启动真相

后续若加 delegation profiles，必须保住这条边界。

---

## Target State

### User-visible semantics

#### Main agent spawning subagents

- 主 agent 调 `spawn(...)`
- 若资源可立刻获得：subagent 立即启动
- 若资源暂不可用但 route/tier 允许排队：返回 queued，并在后台等待调度
- 若策略不允许或资源不可满足：明确 rejected

#### Subagent spawning subagents

- 默认仍不允许
- 只有 profile 明确允许时，subagent 才拥有 `spawn`
- nested spawn obey：
  - `subagent_allowed`
  - `subagent_profile`
  - `delegation_level`
  - `risk_level`
  - `task_budget`
  - `level_limit`

#### Subagent direct messaging

- 默认仍不允许
- 只有 profile 明确允许时，subagent 才拥有 `message`
- 第一阶段仅允许 same-chat、text-only

---

## Architecture

## A. Runtime metadata contract

### Extend harness runtime payload

File:
- `nanobot/harness/service.py`

Current `_runtime_payload()` exposes too little.

Add:
- `delegation_level`
- `risk_level`
- `subagent_profile`

Target payload fields:

```json
{
  "id": "har_0001",
  "type": "feature",
  "status": "active",
  "phase": "executing",
  "awaiting_user": false,
  "blocked": false,
  "auto": false,
  "executor_mode": "main",
  "delegation_level": "assist",
  "risk_level": "normal",
  "subagent_allowed": true,
  "subagent_profile": "default",
  "runner": "main"
}
```

### Add subagent runtime context

New metadata block carried through spawn chain:

```json
{
  "subagent_runtime": {
    "depth": 1,
    "remaining_budget": 3,
    "profile": "delegate",
    "parent_task_id": "abcd1234"
  }
}
```

This is the runtime-only context for nested delegation control.

---

## B. Subagent policy layer

New file:
- `nanobot/agent/subagent_policy.py`

### Data structures

```python
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
    message_scope: str = "none"   # none | same_chat | explicit_target
    allow_spawn: bool = False
    max_spawn_depth: int = 0
    allowed_spawn_types: tuple[str, ...] = ()
    allow_explicit_spawn_model: bool = False
```

### Built-in profiles

Initial profiles:

- `default`
- `notify`
- `delegate`
- `orchestrator`

Recommended semantics:

| profile | message | spawn | intended use |
|---|---:|---:|---|
| default | no | no | safe default |
| notify | same-chat text only | no | direct user notice |
| delegate | no | yes, type-only | bounded nested work |
| orchestrator | same-chat text only | yes, type-only | future planner/orchestrator |

### Policy resolution rules

Resolver inputs:

- `workspace_runtime.active_harness`
- `subagent_runtime`

Resolution model:

1. profile defines capability ceiling
2. `subagent_allowed=false` hard-disables nested sensitive power
3. `delegation_level=none` disables spawn
4. `risk_level=sensitive` disables both `message` and `spawn`
5. depth/budget further constrain runtime behavior

Important rule:
- risk fields may **downgrade** permissions
- risk fields must **not upgrade** permissions

---

## C. Guarded sensitive tools

New file:
- `nanobot/agent/tools/guarded.py`

Implement a thin tool wrapper:

```python
class GuardedTool(Tool):
    def __init__(self, inner: Tool, checker: Callable[[dict[str, Any]], str | None]):
        ...
```

Behavior:
- delegates schema/name/description to inner tool
- invokes checker before execute
- returns policy error string if blocked

Why:
- registration-time hiding is not enough
- execution-time enforcement is mandatory for sensitive tools

---

## D. Real queue in SubagentManager

File:
- `nanobot/agent/subagent.py`

### New queue model

Add a real pending queue, not just status strings.

Suggested structures:

```python
@dataclass
class PendingSubagent:
    task_id: str
    task: str
    label: str
    origin: dict[str, Any]
    session_key: str | None
    candidate_chain: tuple[str, ...]
    requested_type: str | None
    requested_model: str | None
    preferred_route: str | None
```

Manager state:

```python
self._pending_tasks: dict[str, PendingSubagent] = {}
self._pending_order: list[str] = []
self._session_pending: dict[str, set[str]] = {}
```

### Spawn flow target behavior

#### If `decision.status == granted`
- start task immediately

#### If `decision.status == queued`
- create `PendingSubagent`
- append to queue
- track by session
- return queued response text

#### If `decision.status == rejected`
- return rejected response text

### Scheduler

Add manager-internal scheduler:

```python
async def _drain_pending_queue(self) -> None:
    ...
```

Trigger points:
- after lease release
- after cancellation cleanup
- optionally after explicit route refresh

Scheduling policy v1:
- FIFO queue by insertion order
- try each pending item in order
- if top item still cannot run, continue to next only if reason is route-local temporary denial
- preserve fairness by not starving earlier items indefinitely

### Cancellation semantics

Existing `cancel_by_session()` must also:
- remove pending queued items for that session
- return combined cancelled count

### Queue observability

Add:
- `get_pending_count()`
- queued status in logs
- task labels in queue logs

Optional future:
- surface queued count in restart/status command

---

## E. Subagent tool registration becomes policy-driven

File:
- `nanobot/agent/subagent.py`

Refactor `_run_subagent()` to use helper:

```python
tools = self._build_subagent_tools(origin=origin, task_id=task_id)
```

### Base subagent tool set remains

Always allowed:
- `read_file`
- `write_file`
- `edit_file`
- `list_dir`
- `exec`
- `web_search`
- `web_fetch`

### Conditionally allowed

#### `message`
Register only if resolved policy allows it.
Use guarded wrapper with rules:
- same chat only in phase 1
- no media in phase 1
- stamp outbound metadata with source identifiers

Suggested outbound metadata:

```json
{
  "source": "subagent",
  "subagent_task_id": "abcd1234"
}
```

#### `spawn`
Register only if resolved policy allows it.
Use guarded wrapper with rules:
- only `type=worker|explorer` in phase 1
- no explicit `model=...` in phase 1
- enforce depth/budget locally before manager admission

---

## F. Manager-level admission control

Even with guarded tools, manager must re-check.

Add manager method:

```python
def _authorize_spawn_request(...):
    ...
```

Checks:
- active harness `subagent_allowed`
- resolved policy allows spawn
- `level_limit`
- `task_budget`
- explicit model usage if disallowed
- requested type in allowed set

Why both layers:
- tool wrapper = local gate
- manager admission = authoritative runtime gate

---

## G. Wire `task_budget` and `level_limit`

### `level_limit`

Definition:
- maximum subagent nesting depth
- main agent does not count
- first spawned subagent = depth 1

Rules:
- `level_limit=1` => no nested subagent spawn
- `level_limit=2` => one nested layer allowed

### `task_budget`

Definition:
- remaining number of nested delegation spawns available in this spawn tree

Rules:
- root subagent inherits `task_budget`
- each nested spawn decrements by 1 for child context
- when `remaining_budget <= 0`, child spawn is blocked

Why this definition:
- simple
- composable
- tied directly to delegation tree growth

---

## H. Real queue interaction with route policies

Current route policy fields already exist:

- `max_concurrency`
- `window_request_limit`
- `reserved_requests`
- `allow_queue`
- `queue_limit`

### Keep them, but make them real

#### `max_concurrency`
- still gates immediate grants

#### `allow_queue`
- now controls whether an over-capacity request enters pending queue

#### `queue_limit`
- limit pending count for that tier/route family path

#### `window_request_limit` / `reserved_requests`
- continue to deny lease if request budget exhausted
- queued tasks do not bypass reserved quota

### Important design rule

Queued tasks are admission-delayed, not quota-bypassing.

If quota is hard exhausted:
- reject
- do not queue forever

---

## I. Model truth boundaries remain unchanged

This plan must preserve:

| concern | truth |
|---|---|
| main agent startup model | `config.json.agents.defaults.model` |
| subagent default model | `model_registry.json.subagent_defaults.model` |
| route health / runtime availability | `model_registry.json.provider_status` + `provider_policies` |
| switch history / last known good | `model_state.json` |

Do **not** move capability policy into model registry in phase 1.

Reason:
- model registry should stay about model definition / routing / provider health
- subagent capability policy is execution-context policy
- execution-context policy belongs closer to harness/runtime metadata

Possible future extension:
- optional registry-backed profile catalog for centralized policy defaults
- not required for first real-queue implementation

---

## Implementation Plan

## Phase 0 — Savepoint and docs

1. commit existing workspace model/registry changes separately
2. keep runtime queue/policy work isolated in `nanobot-fork-live`
3. record this doc as implementation anchor

Done/now:
- workspace model/registry hardening committed separately

## Phase 1 — Metadata + policy foundation

Files:
- `nanobot/harness/service.py`
- `nanobot/agent/subagent_policy.py` (new)
- `nanobot/agent/tools/guarded.py` (new)
- `nanobot/agent/tools/spawn.py`

Deliverables:
- runtime payload exposes `delegation_level`, `risk_level`, `subagent_profile`
- `subagent_runtime` metadata pass-through
- policy resolver + guarded tool wrapper landed

Verification:
- runtime metadata tests
- profile resolution unit tests
- guarded tool unit tests

## Phase 2 — Real queue core

Files:
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`

Deliverables:
- pending queue structures
- queue-aware spawn flow
- release-triggered drain
- cancel-by-session removes pending queued tasks

Verification:
- queue entry test
- queue drain on release test
- queue cancellation test
- queue limit test

## Phase 3 — Nested spawn runtime control

Files:
- `nanobot/agent/subagent.py`
- `nanobot/agent/tools/spawn.py`

Deliverables:
- `task_budget` wired
- `level_limit` wired
- nested spawn admission checks
- child runtime context propagation

Verification:
- depth cap tests
- budget exhaustion tests
- child context inheritance tests

## Phase 4 — Conditional sensitive tools

Files:
- `nanobot/agent/subagent.py`
- `nanobot/agent/tools/message.py` (only if metadata tweaks needed)

Deliverables:
- `message` available for `notify` / `orchestrator`
- `spawn` available for `delegate` / `orchestrator`
- phase-1 restrictions preserved:
  - same-chat message only
  - no media
  - type-only spawn

Verification:
- same-chat allowed
- cross-chat blocked
- media blocked
- explicit model blocked
- disallowed type blocked

## Phase 5 — Operational polish

Optional but recommended:
- queue introspection methods
- queue count in status/restart surfaces
- better logging around route-local queue pressure
- dedupe / anti-storm guard for repeated queued requests from same conversation

---

## Test Plan

### Metadata tests

Update:
- `tests/agent/test_loop_workspace_progress.py`

Add assertions for:
- `delegation_level`
- `risk_level`
- `subagent_profile`

### Policy tests

New file:
- `tests/agent/test_subagent_policy.py`

Cases:
- default profile has no sensitive tools
- notify profile enables same-chat text-only message
- delegate profile enables type-only spawn
- orchestrator enables both
- sensitive risk disables both
- delegation none disables spawn

### Queue tests

Either extend:
- `tests/agent/test_task_cancel.py`
- `tests/agent/test_subagent_resources.py`

Or add dedicated:
- `tests/agent/test_subagent_queue.py`

Cases:
- over-capacity request enters queue when queue allowed
- queued request starts after running lease releases
- queued request removed by `cancel_by_session`
- queue limit enforced
- quota-exhausted request rejects instead of queueing forever

### Nested spawn tests

Cases:
- `level_limit=1` blocks subagent->spawn
- `level_limit=2` permits one nested layer
- `task_budget` decrements for child context
- budget exhaustion blocks nested spawn

### Sensitive tool tests

New file:
- `tests/agent/test_subagent_guarded_tools.py`

Cases:
- same-chat message allowed
- cross-chat message blocked
- media blocked
- explicit spawn model blocked
- non-whitelisted type blocked
- manager admission rejects even if tool wrapper path is bypassed

---

## Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| queue starvation | naive FIFO can block later runnable work | v1 FIFO + selective skip only for clearly temporary route-local denial; revisit fairness after telemetry |
| runaway nested delegation | giving subagent spawn without hard caps is dangerous | enforce `level_limit` + `task_budget` in both wrapper and manager admission |
| duplicate user messaging | subagent direct message can overlap with main-agent final summary | tag outbound messages with subagent metadata; phase 2 decide if final summary suppression needed |
| policy drift | harness fields exist but runtime payload may fall behind | add tests on exact runtime metadata contract |
| truth-boundary confusion | mixing model config with capability policy will rot fast | keep capability policy in runtime/harness layer, not model registry |

---

## Metrics / Success Criteria

| Metric | Target |
|---|---|
| queued request eventually starts after lease release | yes |
| queued task cancellation works by session | yes |
| nested spawn blocked at configured level limit | yes |
| sensitive tools absent by default | yes |
| same-chat-only message enforcement works | yes |
| explicit model spawn denied in phase 1 | yes |
| focused runtime tests pass | yes |

---

## Non-Goals For This Round

Do not include in the first implementation wave:

- arbitrary cross-chat subagent messaging
- media attachments from subagent message
- user-facing queue management commands
- arbitrary explicit model selection from nested subagents
- global scheduler fairness optimization beyond basic correctness
- moving model truth into a new storage layer

---

## Recommended First Execution Slice

Start with:

1. `runtime metadata` completion
2. `subagent policy` module
3. `guarded tool` wrapper
4. `real queue core`

Then land nested limits and sensitive-tool exposure.

Reason:
- queue correctness is the core long-term requirement
- policy skeleton must exist before we safely expose `message` / `spawn`
- once those foundations are in, tool exposure becomes incremental instead of scary

**Key Principle:** 先把 queue 做真、权限做硬，再开放 subagent 的敏感能力；别再维护“看起来支持，实际上没接线”的假系统。
