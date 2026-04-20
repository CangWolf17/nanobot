# Runtime Subagent V2 Draft

## Goal

Define a runtime-native subagent mechanism that does **not** depend on workspace `autopilot` semantics.

This draft treats subagents as a first-class runtime capability with:

1. stable built-in subagent types
2. simple main-agent spawn inputs
3. route inheritance from the main agent by default
4. explicit fallback chain behavior when the preferred route is unavailable
5. compatibility with the existing runtime `spawn` tool, `SubagentManager`, and provider-health/resource machinery

---

## Problem

The runtime already has a real background-subagent execution path:

1. `SpawnTool`
2. `SubagentManager`
3. `SubagentResourceManager`
4. `AgentRunner`

That part is good.

But the current public spawn contract is still shaped like an older resource-policy surface:

1. `task`
2. optional `label`
3. optional `tier` (`lite|standard`)
4. optional `model`

This leaves several gaps:

1. the interface is still `tier`-first instead of `type`-first
2. runtime does not expose built-in semantic subagent types like `worker` / `explorer`
3. route inheritance from the main agent is not yet a formal first-class input
4. fallback selection is partly available in resource management, but not yet expressed in the runtime API in a way that matches the intended product behavior
5. old workspace-local subagent/autopilot conventions are too easy to treat as design anchors, even though future subagent work should be runtime-native

---

## Non-Goals

This draft intentionally does **not** do the following:

1. use `autopilot` as the architecture anchor
2. preserve workspace-local `architect/developer/tester/reviewer` semantics as the primary runtime subagent taxonomy
3. require workspace harness metadata for normal runtime subagent resolution
4. redesign the whole provider layer
5. require immediate deletion of the old `tier` parameter on day one

---

## Product Intent

### Built-in Runtime Subagent Types

The runtime should ship with two built-in default subagent types:

| type | intended use | default model intent |
|---|---|---|
| `worker` | execution, implementation, bounded delivery | `gpt-5.4-mini` + `xhigh` |
| `explorer` | exploration, search, reconnaissance, option discovery | `gpt-5.4-mini` + `medium` |

Key point:

- **type determines family + effort**
- **route is resolved separately**

### Main-Agent-Friendly Spawn UX

The main agent should only need one of these two forms:

#### Form A — name + built-in type

```json
{
  "task": "inspect the current workspace routing gap and report the smallest safe patch",
  "name": "routing-gap-checker",
  "type": "explorer"
}
```

#### Form B — name + explicit model from registry

```json
{
  "task": "implement the focused patch and run verification",
  "name": "routing-worker",
  "model": "standard-gpt-5.4-mini-xhigh-tokenx"
}
```

Practical rule:

- `task` is required
- `name` is optional but recommended
- at least one of `type` or `model` must be present
- `model` overrides `type` when both are present

### Route Selection Intent

By default, runtime subagents should prefer the same route/provider family as the main agent currently uses, when that route is healthy.

Examples:

- main agent currently using `pro-gpt-5.4-xhigh-tokenx`
  - `worker` should prefer `standard-gpt-5.4-mini-xhigh-tokenx`
  - `explorer` should prefer `standard-gpt-5.4-mini-medium-tokenx`

- main agent currently using `pro-gpt-5.4-xhigh-sisct2`
  - `worker` should prefer `standard-gpt-5.4-mini-xhigh-sisct2`
  - `explorer` should prefer `standard-gpt-5.4-mini-medium-sisct2`

If the preferred route is unavailable, runtime should automatically walk a fallback chain using provider health and registry availability.

---

## Current Runtime Reality

The fork already has strong reusable foundations.

### Existing Runtime Pieces

#### 1. `SpawnTool`

Current file:

- `nanobot/agent/tools/spawn.py`

Current public surface:

- `task`
- `label`
- `tier`
- `model`

This is the main entry point exposed to the agent loop.

#### 2. `SubagentManager`

Current file:

- `nanobot/agent/subagent.py`

Current responsibilities:

1. accept spawn requests
2. acquire resource leases from `SubagentResourceManager`
3. run background subagents through `AgentRunner`
4. announce results back to the main loop

#### 3. `SubagentResourceManager`

Current file:

- `nanobot/agent/subagent_resources.py`

Current responsibilities:

1. candidate model selection
2. route policy application
3. concurrency / queue / reserved quota checks
4. provider health interpretation
5. workspace snapshot ingestion
6. provider probe result application

#### 4. Provider Health / Probe Loop

Current runtime already has machinery for:

1. recording provider failures
2. distinguishing transient vs hard unavailable
3. refreshing provider status
4. probing due routes from workspace model runtime

That is important because route fallback should build on this existing truth instead of inventing a parallel health model.

---

## Proposed Runtime Model

## 1. Introduce Runtime-Native Subagent Types

Add a formal runtime type registry.

Example shape:

```python
@dataclass(frozen=True)
class SubagentTypeSpec:
    name: str
    family: str
    effort: str
```

Default built-ins:

```python
worker = SubagentTypeSpec(
    name="worker",
    family="gpt-5.4-mini",
    effort="xhigh",
)

explorer = SubagentTypeSpec(
    name="explorer",
    family="gpt-5.4-mini",
    effort="medium",
)
```

### Why

This moves the runtime from the old policy language:

- `tier=lite|standard`

into the intended product language:

- `worker`
- `explorer`

That is a better fit for main-agent decision making.

---

## 2. Add a New Spawn Request Shape

Introduce a runtime-internal request object that matches the intended public contract.

Example:

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

Resolution rules:

1. `task` required
2. `model` wins over `subagent_type`
3. if neither `model` nor `subagent_type` is provided, reject
4. `preferred_route` defaults to the main agent's current effective route when available

### Public Tool Contract Direction

The `spawn` tool should evolve toward:

```json
{
  "task": "...",
  "name": "...",
  "type": "worker|explorer",
  "model": "..."
}
```

with the existing `label` and `tier` kept temporarily for compatibility only.

Recommended compatibility plan:

1. add `name` and `type`
2. keep `label` as a deprecated alias for `name`
3. keep `tier` as deprecated compatibility input
4. move all new resolution logic to `type/model`

---

## 3. Formalize Main-Route Inheritance

The runtime needs a first-class notion of the main agent's current effective route.

Today that information is implicit and recoverable from current model / provider state, but not yet a formal subagent resolution input.

### Proposed behavior

When a subagent request does not specify an explicit model route, resolution should prefer:

1. the current main agent route, if healthy
2. fallback routes from runtime candidate order

Example:

- main route: `tokenx`
- subagent type: `worker`
- worker intent: `gpt-5.4-mini xhigh`
- first candidate: `standard-gpt-5.4-mini-xhigh-tokenx`

### Why

This keeps subagents aligned with:

1. the same provider family the main agent is already successfully using
2. the same route characteristics
3. the same current operational truth

It also reduces weird behavior where the main agent is healthy on one provider while subagents silently default somewhere else.

---

## 4. Add Type-Aware Resolution Before Resource Acquire

The runtime should add a resolution layer **before** `SubagentResourceManager.acquire(...)`.

That layer should translate:

- built-in type intent
- explicit model override
- preferred route
- health policy

into an ordered candidate chain.

### Proposed resolution result

```python
@dataclass(frozen=True)
class SubagentResolution:
    requested_name: str | None
    requested_type: str | None
    requested_model: str | None
    preferred_route: str | None
    resolved_model_id: str
    candidate_chain: tuple[str, ...]
    reason: str
```

### Resolution algorithm

#### Case A — explicit model given

1. resolve explicit model ref to canonical model id
2. if that model is healthy and routable, use it first
3. if explicit-route fallback is allowed, derive same-family fallback candidates behind it
4. otherwise fail fast when the explicit model cannot be used

#### Case B — built-in type given

1. map `type` -> `(family, effort)`
2. derive same-family same-effort candidates
3. sort candidates by:
   - preferred route first
   - then healthy fallback routes
   - then runtime route preference order
4. skip hard-unavailable routes
5. allow transient-unavailable routes only behind healthy candidates

### Candidate examples

#### `worker`

Target intent:

- family: `gpt-5.4-mini`
- effort: `xhigh`

If main route is `tokenx` and all routes are enabled:

```text
1. standard-gpt-5.4-mini-xhigh-tokenx
2. standard-gpt-5.4-mini-xhigh-sisct2
3. standard-gpt-5.4-mini-xhigh-aizhiwen-top
```

with hard-unavailable routes removed and transient routes pushed back.

#### `explorer`

Target intent:

- family: `gpt-5.4-mini`
- effort: `medium`

If main route is `tokenx`:

```text
1. standard-gpt-5.4-mini-medium-tokenx
2. standard-gpt-5.4-mini-medium-sisct2
3. standard-gpt-5.4-mini-medium-aizhiwen-top
```

---

## 5. Adapt `SubagentResourceManager` Instead of Replacing It

The current runtime resource manager is already useful.

It should remain responsible for:

1. concurrency limits
2. queue policy
3. reserved quota protection
4. provider availability policy
5. final lease grant / reject decision

### Recommended split

#### New resolution layer owns:

1. `type -> family+effort`
2. preferred route inheritance
3. candidate chain construction
4. explicit model override semantics

#### Existing resource manager owns:

1. is this candidate allowed right now?
2. does this route have capacity?
3. should this request queue or reject?
4. lease lifecycle

That is the least disruptive path.

---

## 6. Derive Preferred Route From Main-Agent Runtime State

The runtime needs a stable source for the main agent's current effective route.

Possible sources, in order:

1. current effective model metadata already known inside the loop/provider context
2. session/runtime state that captures the current resolved main model
3. workspace snapshot fallback only when runtime-native state is unavailable

### Strong recommendation

Do **not** make workspace `model_state.json` the long-term primary truth for runtime subagent route inheritance.

Use runtime-held current model state when available, and only fall back to workspace-side files in compatibility situations.

---

## 7. Proposed Spawn Tool API Evolution

### Target contract

```json
{
  "task": "The task for the subagent to complete",
  "name": "Optional short identifier for the subagent",
  "type": "worker|explorer",
  "model": "Optional explicit registry model ref overriding type"
}
```

### Compatibility bridge

Existing fields can map as follows:

| old field | temporary meaning |
|---|---|
| `label` | alias of `name` |
| `tier=standard` | compatibility hint only; maps to default `worker` only if no `type/model` is present |
| `tier=lite` | compatibility hint only; likely maps to `explorer` or explicit light model fallback, but should not become the long-term public abstraction |

### Recommended compatibility rule

If request shape is old-only:

1. explicit `model` still works exactly as before
2. `tier` maps into a compatibility resolution path
3. new code paths should prefer `type`

---

## 8. Route Fallback Policy

### Route health policy

Subagent resolution should use the same provider health semantics already present in runtime/workspace truth:

1. `available` -> normal candidate
2. `transient_unavailable` -> keep in chain but behind healthy same-intent candidates
3. `hard_unavailable` -> remove from candidate chain
4. `manual_outage` -> remove from candidate chain

### Probe integration

The existing runtime probe loop (`probe_due_provider_routes`, workspace quick probe, provider failure refresh) should remain the feedback channel that keeps candidate ordering accurate over time.

### Important constraint

Route fallback must remain **route-aware**, not just model-name-aware.

That means:

- explicit model resolution is not enough
- same-family alternate-route candidates must be derivable
- route health should be visible in resolution output for debugging and tests

---

## 9. Current Code Touchpoints

A runtime-native implementation will likely need changes in these places.

### `nanobot/agent/tools/spawn.py`

Needed changes:

1. add `name`
2. add `type`
3. keep `label` and `tier` temporarily
4. pass the richer request contract into `SubagentManager.spawn(...)`

### `nanobot/agent/subagent.py`

Needed changes:

1. update `spawn(...)` signature
2. replace `_resolve_subagent_request(...)` with a typed resolution path
3. capture preferred main route from runtime state
4. pass resolution output into resource acquisition
5. preserve completion/reporting behavior

### `nanobot/agent/subagent_resources.py`

Needed changes:

1. support candidate-chain-based acquire, not only tier/model lookup
2. keep route policy / queue / quota behavior
3. possibly add helper methods for route-aware candidate filtering

### `nanobot/agent/loop.py`

Needed changes:

1. expose current effective main model / route to spawn context
2. make preferred route available without relying on workspace-only metadata
3. keep spawn tool context propagation thin and metadata-only

---

## 10. Open Design Decisions

These decisions should be made explicitly during implementation.

### 1. Explicit model fallback strictness

Question:

If the caller provides an explicit model ref and that model's route is unavailable, should runtime:

1. fail immediately
2. or walk same-family fallback candidates automatically

Recommendation:

- default to **strict explicit model resolution** for the first iteration
- add optional broader fallback later if needed

Reason:

If the caller explicitly chooses a model, silently moving to another route/model may violate intent.

### 2. Effort fallback policy for built-in types

Question:

If `worker` cannot get `gpt-5.4-mini xhigh`, should it fall back to `medium`?

Recommendation:

- first iteration: **same family + same effort only**
- optional secondary effort fallback later behind a clear policy flag

Reason:

This keeps behavior predictable while the new type system is landing.

### 3. Long-term fate of `tier`

Recommendation:

- deprecate, do not expand
- keep only as compatibility input during migration

### 4. Long-term type extensibility

Question:

Should runtime later allow user-defined subagent types?

Recommendation:

- first iteration: built-ins only (`worker`, `explorer`)
- later: possibly registry-defined types, but only after the base contract is stable

---

## 11. Suggested Implementation Sequence

### Phase 1 — Resolution Layer

1. add built-in runtime subagent types
2. add typed spawn request / resolution structures
3. derive preferred main route
4. generate candidate chains from `type/model + preferred_route`
5. keep existing lease/acquire path underneath

### Phase 2 — Public Spawn Tool Migration

1. add `name`
2. add `type`
3. keep `label` / `tier` compatibility
4. update tool schema and tests

### Phase 3 — Runtime Traceability

1. expose chosen route/model in subagent debug logs
2. surface resolution reason in tests and diagnostics
3. make route fallback decisions easy to inspect

### Phase 4 — Compatibility Cleanup

1. reduce dependence on `tier`
2. remove `tier` from prompt guidance and docs once callers migrate
3. stop treating workspace-local subagent semantics as runtime truth

---

## Risks

| Risk | Why it matters | Recommended mitigation |
|---|---|---|
| silent behavior drift when explicit `model` is provided | caller may expect exact route/model | keep explicit model strict in v1 |
| route inheritance tied to workspace files instead of runtime truth | can become stale or fork-specific | derive preferred route from runtime effective model when possible |
| expanding compatibility forever | `tier` can keep leaking into new code | mark `tier` deprecated and forbid new features from depending on it |
| overloading `type` with route or provider policy | mixes concerns | keep `type -> family+effort` only |
| fallback becoming opaque | debugging route behavior gets ugly fast | add explicit resolution result and logs |

---

## Recommendation

Implement runtime subagent V2 as a **typed resolution layer on top of the existing runtime subagent executor**, not as a rewrite.

That means:

1. keep `SpawnTool`, `SubagentManager`, `AgentRunner`, and the provider-health machinery
2. add `worker` and `explorer` as formal runtime-native subagent types
3. make the spawn interface `name + type` or `name + model`
4. make route inheritance from the main agent a first-class resolution rule
5. keep fallback chain behavior explicit and route-aware

This is the smallest path that matches the intended product direction while reusing the parts of the runtime that already work.

**Key Principle:** subagent V2 should make `type` express *what kind of work to do* and make route resolution express *where to run it*; if those concerns stay separated, the runtime stays understandable instead of collapsing back into ad-hoc spawn heuristics.
