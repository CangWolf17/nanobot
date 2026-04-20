# 2026-04-12 Donor Commit Screen After Upstream Refresh

## Purpose

Screen the remaining unique commits on the deferred donor branches and decide whether any should be replayed onto `cangwolf/runtime-patches-2026-04-12-refresh`.

## Bottom Line

After screening the remaining donor commits, the answer is:

- **do not replay any donor commit wholesale right now**
- **keep one narrow follow-up idea on file**: if the fork later adopts the V2 model-registry architecture, revisit the fallback tests/behavior from `72d7a3b`
- everything else is either **architecturally stale**, **already superseded by the refreshed branch**, or **too broad to cherry-pick safely**

## Commit-by-Commit Outcome

### `52dba38` — Add runtime model registry and responses provider

Decision: **do not replay now**

Why:

1. introduces a brand-new `nanobot/model_registry/` module tree that does not exist on the refreshed branch today
2. introduces `nanobot/providers/openai_responses_provider.py`, which is a new provider architecture rather than a small patch
3. adds nearly 1,900 lines across schema, resolver, provider factory, and tests, so this is a feature branch foundation, not a surgical fix
4. the refreshed branch is currently centered on the validated V3 queue/policy line, not on switching the runtime over to a new registry stack

### `4eccf77` — Prefer model state when seeding manager defaults

Decision: **drop**

Why:

1. it makes `model_state.json` drive runtime manager defaults
2. the current V3 design doc says `model_state.json` is a state cache and should **not** become runtime startup truth
3. this is a direct design conflict, not just an implementation detail

### `9ca7d21` — Teach subagent snapshot builder the V2 model registry

Decision: **do not replay now**

Why:

1. it depends on the V2 model-registry shape from `52dba38`
2. the refreshed branch does not carry that registry module stack
3. replaying it alone would create half-migrated behavior and more ambiguity, not less

### `30513b9` — Wire runtime model state into subagent orchestration

Decision: **mostly superseded; do not replay now**

Why:

1. the refreshed branch already has subagent execution-context injection and runtime metadata in prompts/tasks
2. the remaining parts of this commit depend on the deferred V2 model-registry path
3. the commit title and implementation still couple orchestration behavior to runtime model state, which is not the current direction for the refreshed mainline

### `72d7a3b` — Harden runtime model orchestration fallbacks

Decision: **keep as a future selective-salvage candidate, but do not replay now**

Why:

1. most of the commit still assumes the deferred V2 model-registry path
2. however, it contains the only clearly reusable future idea from the donor line: stronger fallback behavior/tests when registry resolution fails or when a V2 registry needs to degrade gracefully
3. those tests only become relevant if the fork later chooses to revive the V2 model-registry architecture

Practical meaning:

- do **not** cherry-pick this commit today
- if a later task explicitly revives V2 registry work, start by extracting its tests and fallback logic in small pieces

### `f53516e` — Restore repo pytest baseline after runtime drift

Decision: **drop**

Why:

1. it is a mixed cleanup/baseline commit, not a focused runtime patch
2. it includes large unrelated test rewrites (for example Discord tests) that are not part of the current refresh goal
3. the refreshed branch already has its own post-refresh verification baseline

### `8229020` — Normalize repository lint baseline

Decision: **drop**

Why:

1. it is repo-wide cleanup, not patch logic
2. it touches dozens of files with formatting/lint drift that should stay separate from runtime patch recovery
3. replaying it now would create noise and make future patch attribution harder

### `f5b55cd` — checkpoint model-subagent context refactor

Decision: **historical only / superseded**

Why:

1. it is an early checkpoint, not the validated current line
2. parts of its useful ideas already exist on the refreshed branch today (for example runtime metadata injection in context/subagent prompt building)
3. the rest is better treated as history than as replay material

## What Looks Reusable Later

Only one area looks worth keeping on the shelf for later:

1. **selective fallback tests/logic from `72d7a3b`** if and only if the fork later revives the V2 model-registry direction

Everything else should stay archived as history rather than merged into the refreshed mainline.

## Evidence Used

- `git log --reverse cangwolf/runtime-patches-2026-04-12-refresh..runtime-model-subagent-refactor`
- `git log --reverse cangwolf/runtime-patches-2026-04-12-refresh..runtime-model-subagent-refactor-checkpoint-2026-04-08`
- `git show --stat` for each unique donor commit
- current refreshed branch inspection of:
  - `nanobot/agent/subagent.py`
  - `nanobot/agent/subagent_resources.py`
  - `nanobot/agent/context.py`
  - `docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md`
