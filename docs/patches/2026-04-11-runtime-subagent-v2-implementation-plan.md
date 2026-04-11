# Runtime Subagent V2 — Implementation Plan

## Goal

Turn the existing runtime subagent executor into a **type-first, route-aware** platform capability.

This plan is intentionally execution-oriented. It fills in the missing **truth sources**, **expected behavior**, **file-level changes**, and **acceptance checks** so implementation can proceed without repeatedly re-reading scattered code.

This plan assumes the product direction already decided in `2026-04-11-runtime-subagent-v2-draft.md`:

1. `autopilot` is not the architecture anchor
2. runtime subagents are a platform/runtime feature
3. built-in default subagent types are `worker` and `explorer`
4. default route selection should follow the main agent's current route when healthy
5. fallback chain should be automatic and route-aware

---

## Executive Summary

### What exists already

The runtime already has a good execution spine:

1. `SpawnTool` is exposed to the main agent
2. `SubagentManager` runs background subagents through `AgentRunner`
3. `SubagentResourceManager` already knows about model candidates, route policies, provider health, concurrency, queueing, and leases
4. runtime/workspace provider health already flows through `provider_status`

### What is missing

The missing layer is **typed subagent resolution**:

1. `worker` / `explorer` are not first-class runtime concepts yet
2. the public spawn API is still `tier/model` instead of `type/model`
3. the runtime does not yet treat the main agent's current route as a first-class preferred route input
4. resource acquisition still starts from `tier` / fallback model conventions instead of typed intent

### Recommended implementation strategy

Do **not** rewrite the executor.

Instead:

1. keep `SpawnTool`
2. keep `SubagentManager`
3. keep `SubagentResourceManager`
4. insert a new typed resolution layer between spawn input and resource acquisition

That is the smallest change that matches the intended product direction.

---

## Truth Sources

This section defines the authoritative inputs implementation should use.

## 1. Main Agent Current Effective Model

### Desired truth

The preferred route for subagent resolution should come from the **main agent's current effective model**, not from a workspace guess when runtime already knows better.

### Current reality

Relevant facts in runtime source:

- `AgentLoop.__init__` stores `self.model = model or provider.get_default_model()`
- `SubagentManager.__init__` stores `self.model = model or provider.get_default_model()`
- providers expose `get_default_model()`

### Gap

Runtime currently has a reliable notion of the configured/default main model, but it does **not** yet expose a first-class, normalized `preferred_route` to spawn resolution.

### Execution decision

For V2 implementation, use this order:

1. **runtime-held current main model ref** when available
2. resolve route from runtime-held model ref against runtime-visible registry snapshot
3. only fall back to workspace snapshot/default model if runtime-native state is absent

### Explicit requirement

Do **not** make workspace `model_state.json` the long-term primary truth for runtime subagent route inheritance.

It can remain a compatibility fallback, but not the main source.

---

## 2. Registry Truth For Model Resolution

### Desired truth

Model family/effort/route availability should come from the canonical runtime-visible model registry data.

### Current reality

`SubagentResourceManager` already consumes workspace snapshot data and resolves:

- canonical model refs
- aliases
- route policies
- provider status
- queue/concurrency rules

This is already the right substrate for candidate filtering and leasing.

### Execution decision

Keep the registry/resource-manager snapshot as the source of truth for:

1. enabled models
2. family / effort / route metadata
3. route policy
4. provider status
5. fallback candidate eligibility

---

## 3. Provider Health Truth

### Desired truth

Fallback ordering should reuse the existing health semantics:

- `available`
- `transient_unavailable`
- `hard_unavailable`
- `manual_outage`

### Current reality

`nanobot/agent/subagent_resources.py` already supports:

1. `record_provider_failure(...)`
2. `refresh_provider_status(...)`
3. `apply_provider_probe_result(...)`
4. `probe_due_provider_routes(...)`
5. `build_manager_from_workspace_snapshot(...)`

### Execution decision

Do **not** invent a second health model.

Use the existing provider status model for candidate ordering.

### Expected selection semantics

1. `available` -> normal candidates
2. `transient_unavailable` -> remain eligible, but after healthy same-intent candidates
3. `hard_unavailable` -> removed from candidate chain
4. `manual_outage` -> removed from candidate chain

---

## 4. Spawn Input Truth

### Desired truth

The main agent should spawn subagents with a simple contract:

1. `name + type`
2. or `name + model`

### Current reality

`SpawnTool.parameters` currently exposes:

- `task`
- `label`
- `tier`
- `model`

### Execution decision

The new public direction is:

- `task` required
- `name` optional but recommended
- `type` optional
- `model` optional
- at least one of `type` or `model` required in the new-style path

### Compatibility truth

Keep these temporarily:

- `label` -> deprecated alias of `name`
- `tier` -> deprecated compatibility input only

---

## Product Expectations

## 1. Built-in Runtime Subagent Types

### Expected built-ins

| type | family | effort | intended use |
|---|---|---|---|
| `worker` | `gpt-5.4-mini` | `xhigh` | implementation, bounded execution, focused delivery |
| `explorer` | `gpt-5.4-mini` | `medium` | reconnaissance, search, exploration, option discovery |

### Explicit expectation

Type should only express:

1. model family
2. effort / reasoning depth intent

Type should **not** directly encode:

1. route
2. provider
3. queue policy
4. harness semantics

---

## 2. Main-Agent-Friendly Spawn UX

### Expected API shape

#### Built-in type path

```json
{
  "task": "inspect the routing gap and summarize the smallest safe patch",
  "name": "routing-gap-checker",
  "type": "explorer"
}
```

#### Explicit model path

```json
{
  "task": "implement the patch and run focused verification",
  "name": "routing-worker",
  "model": "standard-gpt-5.4-mini-xhigh-tokenx"
}
```

### Explicit expectations

1. `name` is display/trace identity, not model policy
2. `type` means built-in default intent
3. `model` is explicit override
4. if both `type` and `model` are present, `model` wins

---

## 3. Route Inheritance Behavior

### Expected behavior

By default, runtime should prefer the same route as the main agent's current effective model.

Examples:

- main agent route = `tokenx`
  - `worker` -> prefer `...mini-xhigh-tokenx`
  - `explorer` -> prefer `...mini-medium-tokenx`

- main agent route = `sisct2`
  - `worker` -> prefer `...mini-xhigh-sisct2`
  - `explorer` -> prefer `...mini-medium-sisct2`

### Explicit expectation

This is a **default preference**, not an absolute lock.

If the preferred route is unavailable, fallback should continue automatically.

---

## 4. Fallback Behavior

### Expected behavior

Fallback must be:

1. route-aware
2. health-aware
3. explainable in logs/tests

### First-iteration strictness

For V1 implementation of the new mechanism:

#### Explicit `model`

Recommended behavior:

- strict by default
- if explicit model cannot be used, fail fast instead of silently drifting to a different route/model

#### Built-in `type`

Recommended behavior:

- stay within the same `family + effort`
- vary route first
- do **not** introduce automatic effort fallback in the first pass

That means:

- `worker` stays on `gpt-5.4-mini xhigh`
- `explorer` stays on `gpt-5.4-mini medium`

Only routes change during normal fallback.

---

## Proposed Runtime Data Model

## 1. Built-in Type Registry

Add a runtime-native type registry.

### Proposed structure

```python
@dataclass(frozen=True)
class SubagentTypeSpec:
    name: str
    family: str
    effort: str
```

### Built-ins

```python
worker = SubagentTypeSpec(name="worker", family="gpt-5.4-mini", effort="xhigh")
explorer = SubagentTypeSpec(name="explorer", family="gpt-5.4-mini", effort="medium")
```

### Suggested location

One of:

- `nanobot/agent/subagent_types.py`
- or near `nanobot/agent/subagent_resources.py`

Recommendation:

- use a dedicated file: `nanobot/agent/subagent_types.py`

Reason:

It keeps type intent separate from resource acquisition and avoids stuffing more logic into `subagent_resources.py`.

---

## 2. Runtime Spawn Request

### Proposed structure

```python
@dataclass(frozen=True)
class RuntimeSubagentSpawnRequest:
    task: str
    name: str | None = None
    subagent_type: str | None = None
    model: str | None = None
    preferred_route: str | None = None
    session_key: str | None = None
```

### Interpretation

1. `model` overrides `subagent_type`
2. `preferred_route` comes from main-agent runtime state by default
3. `session_key` is execution/session bookkeeping only

---

## 3. Resolution Output

### Proposed structure

```python
@dataclass(frozen=True)
class SubagentResolution:
    requested_name: str | None
    requested_type: str | None
    requested_model: str | None
    preferred_route: str | None
    candidate_chain: tuple[str, ...]
    resolved_model_id: str
    reason: str
```

### Why this is required

Without a stable resolution object, debugging route and candidate behavior becomes guesswork.

This object should be loggable and testable.

---

## 4. Resource Request Compatibility Layer

Current `SubagentRequest` is still shaped like:

- `model`
- `tier`
- `harness_tier`
- `harness_model`
- `manager_tier`
- `manager_model`

### Plan

Do **not** delete this immediately.

Instead:

1. let typed resolution produce an ordered candidate chain
2. either:
   - extend `SubagentRequest` to carry `candidate_chain`
   - or add a new acquire method that accepts candidate chains directly

### Recommended choice

Add a new path rather than overloading `tier` more.

Example:

```python
AcquireDecision acquire_candidates(candidate_chain: list[str])
```

Reason:

The old request format is tier/default-model-centric. The new runtime policy is candidate-chain-centric.

---

## File-by-File Execution Plan

## 1. `nanobot/agent/tools/spawn.py`

### Current truth

Current tool parameters:

- `task`
- `label`
- `tier`
- `model`

### Change goal

Move public contract toward:

- `task`
- `name`
- `type`
- `model`

while temporarily preserving compatibility.

### Required changes

1. add `name` to schema
2. add `type` with enum `worker|explorer`
3. keep `label` and `tier`, but mark as compatibility-only in description
4. update `execute(...)` signature to accept:
   - `name`
   - `type`
   - `label`
   - `tier`
   - `model`
5. normalize:
   - `effective_name = name or label`
6. pass the richer request into `SubagentManager.spawn(...)`

### Explicit non-goal here

Do not put route resolution logic in the tool itself.

The tool should stay a thin adapter.

---

## 2. `nanobot/agent/subagent.py`

### Current truth

`SubagentManager.spawn(...)` currently takes:

- `task`
- `label`
- `tier`
- `model`
- origin/session metadata

and `_resolve_subagent_request(...)` still builds the old `SubagentRequest` shape.

### Change goal

Make `SubagentManager` the owner of typed runtime spawn resolution.

### Required changes

#### A. update `spawn(...)` signature

Add:

- `name`
- `subagent_type`

and keep:

- `label` / `tier` only for compatibility

#### B. replace `_resolve_subagent_request(...)`

Split it into two concepts:

1. build typed spawn request
2. resolve typed request into candidate chain

Suggested methods:

- `_build_spawn_request(...)`
- `_resolve_preferred_route(...)`
- `_resolve_subagent_candidates(...)`

#### C. derive preferred route from runtime truth

Use origin/runtime context if available; otherwise resolve from main model known by the manager.

#### D. acquire from candidate chain

Use the resource manager only for final grant/reject on ordered candidates.

#### E. keep execution behavior unchanged

Do not rewrite:

- background task lifecycle
- announcement path
- tool registration inside `_run_subagent(...)`
- provider failure/probe write-back behavior

### Explicit non-goal here

Do not mix in workspace `autopilot`-style role logic.

---

## 3. `nanobot/agent/subagent_resources.py`

### Current truth

This file already owns:

1. route policies
2. route states
3. provider status handling
4. queue/concurrency logic
5. workspace snapshot manager construction

But acquisition currently starts from:

- explicit `model`
- else `tier`
- else manager/harness defaults

### Change goal

Let the resource manager accept runtime-ordered candidate chains while preserving its current resource-policy role.

### Required changes

#### A. add candidate-chain acquire path

Suggested API:

```python
def acquire_candidates(self, candidates: list[str]) -> AcquireDecision:
    ...
```

Behavior:

1. iterate candidates in given order
2. apply route policy / health / quota / queue rules exactly once per candidate
3. grant first viable lease
4. preserve existing rejection semantics where reasonable

#### B. keep existing `acquire(request)` for compatibility

Do not break the old path immediately.

#### C. add helper for route-health ordering if needed

If resolution layer needs a reusable utility to classify routes by provider status:

- add helper here
- do not duplicate provider status parsing in multiple files

### Explicit non-goal here

Do not make this file own subagent built-in type semantics. It should remain resource-policy focused.

---

## 4. `nanobot/agent/loop.py`

### Current truth

The loop already:

1. registers `SpawnTool`
2. sets spawn context via `tool.set_context(channel, chat_id, metadata)`
3. propagates `workspace_runtime` metadata when present

### Change goal

Expose enough runtime context so spawn resolution can prefer the main agent route without needing workspace-only truth.

### Required changes

#### A. define a small spawn-relevant runtime metadata bundle

Recommended fields:

- `main_agent_model_ref`
- `main_agent_route` (if resolvable cheaply)

#### B. inject this into spawn context metadata

It should sit alongside existing metadata passthrough.

#### C. keep metadata thin

Only include what resolution needs.

### Explicit non-goal here

Do not dump full provider config or giant state blobs into spawn metadata.

---

## 5. Tests

### Existing test truth

The runtime repo already has good coverage around:

- `SpawnTool`
- `SubagentManager`
- `SubagentResourceManager`
- provider status persistence / refresh / probe behavior
- spawn context metadata propagation

That is enough to extend rather than start over.

### Required new tests

#### A. Spawn tool schema and parameter behavior

File area:

- `tests/agent/test_task_cancel.py`
- or a cleaner dedicated spawn tool test file if preferred

Add coverage for:

1. `name` appears in schema
2. `type` appears in schema with `worker|explorer`
3. `label` still accepted as compatibility input
4. `name` wins over `label` when both given
5. request rejected when neither `type` nor `model` is given in the new path (unless compatibility path is intentionally allowed)

#### B. Type resolution tests

New file recommended:

- `tests/agent/test_subagent_types.py`
- or extend `test_subagent_resources.py`

Cover:

1. `worker -> gpt-5.4-mini xhigh`
2. `explorer -> gpt-5.4-mini medium`
3. unknown type rejected

#### C. Preferred route inheritance tests

Cover:

1. main route `tokenx` -> worker prefers `...tokenx`
2. main route `sisct2` -> explorer prefers `...sisct2`
3. if preferred route is hard unavailable, next healthy route is chosen
4. transient routes are pushed behind healthy routes

#### D. Explicit model strictness tests

Cover:

1. explicit model resolves directly when healthy
2. explicit model failure returns predictable rejection if strict mode is chosen

#### E. Candidate-chain acquire tests

Cover:

1. healthy preferred candidate gets granted
2. hard-unavailable preferred candidate is skipped
3. transient preferred candidate is skipped behind healthy fallback when candidate ordering already reflects health
4. queue/concurrency behavior still works under candidate-chain acquisition

#### F. Runtime metadata propagation tests

Extend loop tests to prove:

1. spawn context includes enough info for preferred route resolution
2. runtime path does not depend on workspace harness metadata to resolve built-in types

---

## Explicit Acceptance Criteria

The work is done when all of the following are true.

## API / UX

1. main agent can spawn with `name + type`
2. main agent can spawn with `name + model`
3. built-in types `worker` and `explorer` are available
4. `label` and `tier` still work only as compatibility inputs

## Resolution

5. `worker` resolves to `gpt-5.4-mini xhigh`
6. `explorer` resolves to `gpt-5.4-mini medium`
7. subagent route defaults to the current main agent route when healthy
8. fallback chain automatically switches routes when the preferred route is unavailable
9. hard-unavailable/manual-outage routes are excluded
10. transient-unavailable routes remain behind healthy candidates

## Execution

11. subagents still run through the existing runtime executor path
12. lease acquire / release behavior remains intact
13. provider failure and refresh updates still persist correctly
14. subagent completion reporting still works

## Quality

15. tests clearly show chosen route/model behavior
16. debugging the chosen candidate chain no longer requires guessing hidden policy

---

## Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| old `tier` behavior leaks into new logic | keeps design muddy | isolate compatibility mapping at the edge only |
| preferred route source is ambiguous | unstable selection behavior | explicitly codify source priority in manager |
| explicit model fallback surprises callers | semantic drift | keep explicit model strict in v1 |
| resolution logic spreads across tool/manager/resource layers | hard to maintain | keep tool thin, manager owns typed resolution, resource manager owns leasing |
| route selection becomes opaque | debugging pain | add `SubagentResolution` and test candidate chains |

---

## Recommended Implementation Order

### Step 1 — add type definitions and resolution objects

Files:

- new `nanobot/agent/subagent_types.py`
- `nanobot/agent/subagent.py`

### Step 2 — extend spawn tool schema and manager signature

Files:

- `nanobot/agent/tools/spawn.py`
- `nanobot/agent/subagent.py`

### Step 3 — implement preferred-route-aware candidate resolution

Files:

- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`

### Step 4 — expose main-route hint from loop runtime context

Files:

- `nanobot/agent/loop.py`

### Step 5 — extend tests before compatibility cleanup

Files:

- `tests/agent/test_task_cancel.py`
- `tests/agent/test_subagent_resources.py`
- new `tests/agent/test_subagent_types.py` if cleaner

### Step 6 — only then decide how aggressively to deprecate `tier`

Do not front-load the cleanup. Land the real mechanism first.

---

## Final Recommendation

Implement V2 as a **typed resolution layer with runtime-owned preferred route inheritance**, sitting on top of the existing subagent executor and resource manager.

That gives the runtime exactly what the product wants:

1. main-agent-friendly spawn inputs
2. stable built-in subagent types
3. default route alignment with the main agent
4. explicit route-aware fallback
5. minimal architectural churn

**Key Principle:** the implementation should make the runtime answer two separate questions in order — **what kind of subagent do we want** (`worker` / `explorer` / explicit model), then **where should it run right now** (preferred main route, then healthy fallback). Keeping those truths separate is what stops the design from collapsing back into opaque `tier` heuristics.
