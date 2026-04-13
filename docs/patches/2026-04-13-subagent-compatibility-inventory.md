# 2026-04-13 Subagent Compatibility Inventory

## Purpose

This note answers one narrower follow-up question after the first cleanup sequence:

> what compatibility-shaped subagent surface is still live right now, where does it still exist in code/tests, and how much of it is already only legacy support rather than the real public API?

The goal is to separate:

- **current runtime truth**
- **test scaffolding that still protects compatibility**
- **historical docs that describe earlier stages**

from each other.

## Short Answer

Current reality is:

1. the **preferred public spawn surface is already `task + type` or `task + model`**
2. legacy `label` compatibility has now been retired from runtime spawn handling
3. `tier` is **still accepted as the remaining compatibility input**
4. current truth docs are **mostly aligned** with that reality
5. the main retirement decision is **not** "remove everything called `tier`"
6. the real decision is:

> how much longer do we want to keep `tier -> compatibility_tier -> fallback candidate resolution`, especially for `standard` and `lite`?

## What Is Actually Still Live In Runtime Code

### 1. `nanobot/agent/tools/spawn.py`

Current state:

- tool schema exposes:
  - `task`
  - `name`
  - `type`
  - `model`
- tool schema does **not** expose:
  - `label`
  - `tier`

But runtime compatibility still exists because:

- `execute(..., **kwargs)` still reads:
  - `tier = kwargs.get("tier")`
- legacy `label` kwargs are ignored rather than forwarded into `SubagentManager.spawn(...)`

Interpretation:

- `label` / `tier` are no longer first-class schema fields
- `label` no longer changes runtime behavior
- `tier` still works if an older caller or prompt payload sends it anyway

This means the public API is already mostly cleaned up.
The remaining compatibility here is now just `tier` passthrough, not front-door design.

### 2. `nanobot/agent/subagent.py`

Current live compatibility points:

- `SubagentManager.spawn(...)` still accepts:
  - `tier`
- selector validation still allows the legacy path:
  - request is valid if it has `type` or `model` or deprecated `tier`
- `_build_spawn_request(...)` still converts:
  - `tier -> compatibility_tier`
- special normalization still exists:
  - if no `type/model` and `tier == "standard"`, set `subagent_type = "worker"`

Interpretation:

- `label` is no longer part of manager-level spawn compatibility
- `tier=standard` is no longer a separate primary route; it is first normalized toward `worker`
- the manager still preserves old behavior in case worker candidates are not usable

### 3. `nanobot/agent/subagent_resources.py`

This is where compatibility still has real behavioral weight.

Current live compatibility logic:

- `RuntimeSubagentSpawnRequest` still includes `compatibility_tier`
- `resolve_spawn_request(...)` still has a compatibility branch
- if explicit model is absent and typed candidates fail or are missing, it can still resolve from:
  - `compatibility_tier`

The important detail is:

- `standard` compatibility is now a **fallback-adjacent path**, not the preferred route
- `lite` compatibility is still a more genuinely legacy-shaped resource path

So the remaining runtime burden is mostly here, not in the tool schema.

### 4. `nanobot/agent/subagent_types.py`

This file still contains `tier="standard"` on built-in type specs.

That is **not** the same problem as the deprecated public `tier` API.

Here `tier` is just part of the built-in type metadata:

- `worker` -> standard / gpt-5.4-mini / xhigh
- `explorer` -> standard / gpt-5.4-mini / medium

Recommendation:

- do **not** count this as public compatibility debt by itself
- only revisit it if type-spec structure changes for a separate reason

## Where The Remaining Compatibility Weight Actually Sits

### Runtime code footprint

Targeted files and rough identifier frequency:

- `nanobot/agent/tools/spawn.py`: `tier=5`
- `nanobot/agent/subagent.py`: `tier=8`, `compatibility_tier=4`
- `nanobot/agent/subagent_resources.py`: `tier=23`, `compatibility_tier=9`

Interpretation:

- most remaining real compatibility behavior is concentrated in:
  - `subagent_resources.py`
- `subagent.py` now mainly carries the standard-tier normalization bridge
- `spawn.py` is now just the new surface plus legacy tier passthrough

### Test footprint

Compatibility references remain much broader in tests:

- `tests/agent/test_task_cancel.py`: mostly `tier` / `compatibility_tier` coverage plus one regression test that legacy `label` is ignored
- `tests/agent/test_subagent_resources.py`: `tier=20`
- `tests/agent/test_subagent_queue.py`: `tier`-shaped queue coverage only

Interpretation:

- remaining compatibility is still heavily protected by tests
- `label` is no longer blocked by runtime or compatibility tests
- the real protected compatibility contract is now mostly tier-shaped

## What The Tests Say About The Current Design

The tests show the intended present-day contract very clearly:

### Already true

- spawn tool exposes `name`, `type`, `model`
- spawn tool hides `label`, `tier` from the schema
- legacy `label` no longer passes through into manager spawn handling
- `tier=standard` is normalized toward `type=worker`
- typed resolution is preferred when worker candidates exist

### Still intentionally supported

- calls with legacy `tier="standard"`
- compatibility-only resolution when typed worker candidates do not exist
- compatibility-only resolution when typed worker candidates are unusable because of route status

This means current tests are not just preserving old syntax accidentally.
They are preserving a deliberate remaining fallback story around `tier`, not around `label`.

## Are The Existing Docs Aligned With Reality?

### Yes: current truth docs are broadly aligned

The current truth docs already describe the live shape correctly:

- `docs/patches/2026-04-12-patch-reality-map.md`
  - says resolution chooses from explicit model, typed subagent role, then compatibility tier
- `docs/patches/2026-04-12-subagent-simplification-review.md`
  - says the preferred surface is `type/model`
  - reflects the earlier cleanup checkpoint before `label` retirement
- `docs/patches/2026-04-13-subagent-cleanup-review.md`
  - says the public spawn surface is narrower
  - says standard-tier compatibility now points earlier toward worker
- this inventory
  - records the post-cleanup, post-label-retirement state

So for the **current-truth document set**, reality and docs are aligned.

### Also yes: older planning docs are allowed to look older

Some 2026-04-11 draft/plan docs still describe the migration process or earlier API shape.

That is acceptable because:

- they are planning/history artifacts
- the ledger already separates historical docs from current-truth docs

So the problem is **not** "the docs are wrong".
The problem, if any, is only that older docs must keep being treated as historical context rather than current behavior.

## Practical Retirement Options

### Option A — `label` is done

This was the lowest-risk retirement target, and it is now complete:

- `SpawnTool` no longer forwards `label`
- `SubagentManager.spawn(...)` no longer accepts `label`
- `_build_spawn_request(...)` no longer maps `label -> name`

Why this was the easiest:

- schema already prefers `name`
- runtime behavior had already reduced `label` to mostly display-name compatibility
- this would mainly touch compatibility tests and manager plumbing

### Option B — retire `standard` compatibility only after caller inventory

This is the next meaningful step, but not as low-risk as removing `label`.

Why:

- `tier=standard` still backs a deliberate fallback path
- tests prove it is used to preserve behavior when worker candidates are absent or unusable

So retiring it safely requires a stronger statement:

> either worker candidates are now guaranteed wherever standard-tier fallback mattered, or we are willing to drop that compatibility behavior on purpose.

### Option C — keep `lite` compatibility longer

This is the area that looks least ready for casual removal.

Why:

- `lite` still participates in resource-manager selection behavior
- it is more obviously tied to older resource assumptions than the now-mostly-normalized `standard` path

Recommendation:

- if compatibility retirement starts, do it in this order:
  1. maybe `standard`
  2. `lite` last

## Bottom Line

The compatibility story is already narrower than it may feel from reading the code casually.

In plain language:

- the **front door is already new-style**
- the **`label` compatibility layer is gone**
- the **remaining old key with real behavior is `tier`**
- the **real remaining legacy behavior mostly survives inside resource resolution and tests**

So the next useful move is not a blind cleanup.
It is this:

> decide explicitly whether we still need `standard` and `lite` compatibility behavior in real runtime scenarios, then retire the remaining tier-shaped layers in order instead of treating every `tier` string as equal technical debt.
