# 2026-04-12 Donor Branch Triage After Upstream Refresh

## Purpose

Record whether the remaining non-merged fork branches should be replayed on top of `cangwolf/runtime-patches-2026-04-12-refresh`.

## Decision

Do **not** auto-merge the remaining `runtime-model-subagent-*` branches into the refreshed mainline.

Treat them as **historical donor branches** that require selective replay only if a later task explicitly revives their design goals.

Commit-level screening is recorded in `docs/patches/2026-04-12-donor-commit-screen.md`.

## Branch-by-Branch Outcome

### `runtime-model-subagent-refactor`

Status: **deferred / not aligned with the current V3 patch direction**

Unique commits relative to the refreshed branch:

- `52dba38` — Add runtime model registry and responses provider
- `4eccf77` — Prefer model state when seeding manager defaults
- `9ca7d21` — Teach subagent snapshot builder the v2 model registry
- `30513b9` — Wire runtime model state into subagent orchestration
- `72d7a3b` — Harden runtime model orchestration fallbacks
- `f53516e` — Restore repo pytest baseline after runtime drift
- `8229020` — Normalize repository lint baseline

Why it is deferred:

1. it introduces a new `nanobot/model_registry/` surface and a responses provider stack that are **not** part of the refreshed branch today
2. the current V3 design doc explicitly says to keep **subagent capability policy out of the model registry** in phase 1
3. the branch mixes architectural changes, runtime behavior changes, and repo-wide baseline cleanup, which is too broad to replay blindly after the upstream refresh
4. the refreshed branch already carries the validated runtime queue / fallback / recovery line that the fork was actively using before the refresh

### `runtime-model-subagent-refactor-checkpoint-2026-04-08`

Status: **historical checkpoint only**

Unique commit relative to the refreshed branch:

- `f5b55cd` — checkpoint model-subagent context refactor

Why it is deferred:

1. it predates the later V3 queue/policy direction
2. it is a checkpoint-style context refactor rather than the validated current mainline
3. the later `runtime-model-subagent-refactor` branch supersedes its theme but is itself deferred

## Alignment With Current Patch Docs

### Aligned with the refreshed branch

The refreshed branch continues to align with the active self-use fork documents:

- `docs/patches/2026-04-02-self-use-runtime-patch-playbook.md`
- `docs/patches/2026-04-03-runtime-control-plane-followups.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-cleanup-execution-plan.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md`

### Not aligned enough to merge now

`docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md` says:

- keep the main-agent / subagent model truth boundary intact
- keep capability policy in the runtime / harness layer
- do **not** move capability policy into model registry in phase 1

That guidance conflicts with replaying the V2-style model-registry donor branch wholesale.

## Practical Replay Guidance

If later work needs ideas from the donor branches, replay them selectively instead of merging the branches whole:

1. replay focused tests first
2. isolate any still-useful runtime behavior from model-registry architecture work
3. keep repo-wide lint / baseline cleanup in separate commits
4. re-check the V3 queue/policy docs before reviving any model-state coupling

## Evidence Used

- `git cherry -v cangwolf/runtime-patches-2026-04-12-refresh runtime-model-subagent-refactor`
- `git cherry -v cangwolf/runtime-patches-2026-04-12-refresh runtime-model-subagent-refactor-checkpoint-2026-04-08`
- `git diff --stat cangwolf/runtime-patches-2026-04-12-refresh..runtime-model-subagent-refactor -- nanobot/agent/subagent.py nanobot/agent/subagent_resources.py tests/agent/test_task_cancel.py tests/agent/test_subagent_resources.py`
- `git diff --stat cangwolf/runtime-patches-2026-04-12-refresh..runtime-model-subagent-refactor-checkpoint-2026-04-08 -- nanobot/agent/subagent.py nanobot/agent/context.py tests/agent/test_runner.py tests/test_build_status.py`
