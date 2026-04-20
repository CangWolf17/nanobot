# 2026-04-13 Standard Tier Compatibility Assessment

## Purpose

This note answers the next narrower question after retiring legacy `label` support:

> is `tier=standard` now just an old spawn input we can delete, or is it still carrying real runtime fallback behavior that would change outcomes if removed?

Short answer:

> `tier=standard` is no longer the preferred spawn API, but it is **not** dead weight yet.
> In the current implementation it still preserves a specific fallback path when `worker`-type resolution cannot produce a usable candidate chain.

## Current Runtime Chain

### Public entry surface

For spawn requests, the preferred surface is already:

- `task + type`
- `task + model`

The only remaining compatibility-shaped spawn input is:

- `tier`

That comes in through:

- `nanobot/agent/tools/spawn.py`
- `nanobot/agent/subagent.py::spawn(...)`
- `nanobot/agent/subagent.py::_build_spawn_request(...)`

### What actually happens for `tier="standard"`

Current logic in `_build_spawn_request(...)` is:

1. preserve `compatibility_tier = "standard"`
2. if no explicit `type` and no explicit `model`, also normalize to:
   - `subagent_type = "worker"`

So a legacy `tier="standard"` spawn does **not** go straight into the old tier path first.
It first tries to behave like a canonical worker-type request.

### Where real fallback still happens

In `SubagentResourceManager.resolve_spawn_request(...)`:

1. explicit model wins first
2. requested type (`worker`) is resolved next
3. only if typed candidate resolution fails to yield candidates does the code fall back to:
   - `compatibility_tier="standard"`

This means standard-tier compatibility still matters specifically when:

- worker candidates are absent in the registry, or
- worker candidates exist conceptually but are unusable after route-availability filtering

That is not theoretical. Current tests explicitly protect both cases.

## What The Tests Prove

### Case A — the happy path already prefers worker-type resolution

Protected by tests such as:

- `test_build_spawn_request_normalizes_standard_tier_to_worker_type`
- `test_resolve_subagent_resolution_prefers_main_route_for_worker_type`
- `test_spawn_acquires_resource_lease_before_starting_background_task`

Meaning:

- legacy `tier="standard"` no longer defines the main routing behavior
- the preferred live interpretation is already:
  - standard spawn intent -> `worker`

### Case B — compatibility fallback still changes behavior when worker candidates are missing

Protected by:

- `test_resolve_subagent_resolution_falls_back_to_legacy_standard_when_worker_candidates_absent`

Meaning:

- if the registry lacks `worker`-shape candidates (`gpt-5.4-mini` / expected effort chain),
- the system can still resolve a usable candidate from the legacy standard tier path

If we remove standard compatibility today, that case stops resolving and becomes an error instead.

### Case C — compatibility fallback still changes behavior when worker candidates are manually unavailable

Protected by:

- `test_resolve_subagent_resolution_falls_back_to_legacy_standard_when_worker_candidates_only_hit_manual_outage`

Meaning:

- even when a worker-shaped candidate exists,
- the old standard path can still produce a different usable chain if the worker candidate route is in `manual_outage`

If we remove standard compatibility today, this case also changes behavior.

## Important Boundary: Spawn Compatibility Is Not The Same As Standard Tier Resource Policy

There are two different things named "standard" in the current codebase.
They should not be conflated.

### 1. Spawn compatibility `tier="standard"`

This is the legacy user-facing compatibility input discussed above.
It exists only to help older spawn requests enter the new runtime.

### 2. Resource-manager standard tier behavior

Separate tests in `tests/agent/test_subagent_resources.py` still exercise generic resource allocation with:

- `SubagentRequest(tier="standard")`

Example protected behavior:

- `test_standard_tier_defaults_to_high_and_skips_exhausted_tokenx`

That is not just old spawn API baggage.
It is part of the resource-manager's internal tier policy surface.

So even if we later remove spawn-level `tier="standard"` compatibility,
we should **not** assume that all standard-tier concepts disappear from `subagent_resources.py` at the same time.

## Non-Test Caller Reality

The current repo no longer shows meaningful non-test code that prefers `tier="standard"` over the new spawn surface.
In practice:

- the front door is already `name/type/model`
- remaining standard-tier spawn behavior is mostly there for compatibility and fallback preservation

That is good news.
It means the main blocker is no longer caller migration.
The blocker is behavior preservation.

## Can We Delete It Now?

### Conservative answer: not safely, not in one pass

You could delete the spawn-level standard compatibility path today,
but that would knowingly change tested behavior in at least these two cases:

1. no worker candidates exist
2. worker candidates only resolve to manually unavailable routes

So the right framing is not:

> can we mechanically remove the code?

The right framing is:

> are we willing to drop those fallback behaviors, or do we want to replace them with a new typed fallback rule first?

## Safe Next-Step Options

### Option A — keep standard compatibility for now, document it as the only remaining typed-fallback bridge

Pros:

- zero behavior change
- keeps the current tested fallback story

Cons:

- compatibility branch remains in `resolve_spawn_request(...)`

### Option B — replace compatibility fallback with a typed fallback rule, then remove raw `tier="standard"`

This would mean:

- `worker` resolution remains primary
- if canonical worker candidates are missing/unavailable,
- a new explicit typed fallback rule decides what alternative chain is acceptable
- only after that do we remove raw `compatibility_tier="standard"`

Pros:

- cleaner design end-state
- removes the last spawn-level legacy selector

Cons:

- not a cleanup-only pass anymore
- requires policy design, not just deletion

### Option C — intentionally drop the old fallback behavior

This is the most aggressive option.
It means deciding that:

- no worker candidates -> hard failure
- worker route in manual outage -> hard failure or ordinary typed-route failure

Pros:

- simplest code path

Cons:

- explicit runtime behavior regression versus current tests
- likely too abrupt for the current branch without a stronger product/runtime decision

## Recommendation

My recommendation is:

> do **not** remove `standard` compatibility yet.

Instead:

1. treat it as the **last real spawn compatibility bridge**
2. keep `label` retired
3. if we want to delete `standard`, do it only after choosing between:
   - a typed replacement fallback rule, or
   - an explicit decision to drop the old fallback behavior

In other words:

- `label` was removable cleanup
- `standard` is now a behavior decision

## Bottom Line

After the label cleanup, the situation is clearer:

- `label` was just leftover API weight and is now gone
- `standard` is not just leftover API weight
- `standard` still protects a real fallback path between typed worker resolution and legacy tier-based candidate selection

So the next deletion, if it happens, should be treated as a small design change — not as another obvious cleanup pass.
