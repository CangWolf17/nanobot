# 2026-04-12 Upstream Refresh Ledger

## Purpose

This document is the current truth ledger for the refreshed self-use fork after reapplying the validated patch surface onto the latest fetched upstream base.

## Baseline

- Refresh branch: `cangwolf/runtime-patches-2026-04-12-refresh`
- Upstream base at branch creation: `upstream/main` = `217e1fc957513c9e6804f5ab1fd3bc66cf105b4b`
- Refresh integration commit: see the first refresh commit on `cangwolf/runtime-patches-2026-04-12-refresh`
- Final ahead/behind vs `upstream/main`: inspect with `git rev-list --left-right --count upstream/main...HEAD` after any later follow-up commits

## What Was Reapplied

The refreshed branch reuses the current `cangwolf/runtime-patches-2026-04-02` patch surface on top of the latest fetched upstream mainline, then fixes compatibility gaps exposed by the newer upstream baseline.

Patch themes now present on the refresh branch include:

1. self-use runtime control-plane patches and docs
2. workspace bridge / fastlane / harness runtime surface
3. provider retry and empty-success behavior expected by the fork
4. subagent resource management / fallback patches from the current mainline
5. session recovery guardrails for misarchived workspace sessions
6. test coverage that locks the above behavior down

## Dirty Slice Closure Before Refresh

The pre-refresh dirty tree was split and committed on `cangwolf/runtime-patches-2026-04-02` as:

1. `f073b23` — queued provider overload retry UX
2. `9e123e9` — subagent fallback before tool side effects + quota classification
3. `5d49196` — unique misarchived session recovery

These commits are included in the refresh branch via the merge from the current patch mainline.

## Branch Disposition

### Fully superseded by the refresh branch

- `cangwolf/runtime-patches-2026-04-02` — source of the refreshed patch surface; keep as rollback/reference until the refresh branch is promoted
- `cangwolf/upstream-sync-2026-04-09` — no unique commits remain beyond the refresh branch; treat as historical
- `runtime-upstream-sync-2026-04-08` — historical intermediate integration branch already absorbed by the main patch line

### Still separate donor branches (not auto-merged here)

- `runtime-model-subagent-refactor`
- `runtime-model-subagent-refactor-checkpoint-2026-04-08`

Reason they remain separate:

1. they are not part of the validated current patch mainline
2. they carry additional model/subagent orchestration work not covered by the refresh verification buckets run in this pass
3. they should be triaged deliberately as follow-up donor branches instead of being merged blindly into the refreshed mainline

## Historical vs Current Docs

### Historical reference docs

- `docs/patches/2026-04-08-upstream-sync-integration.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-draft.md`
- `docs/patches/2026-04-11-runtime-subagent-v2-implementation-plan.md`

These remain useful for intent/history, but they are not the current operational truth ledger after the 2026-04-12 refresh.

### Current truth docs

- this ledger
- `docs/patches/2026-04-12-donor-branch-triage.md`
- `docs/patches/2026-04-12-donor-commit-screen.md`
- `docs/patches/2026-04-02-self-use-runtime-patch-playbook.md`
- `docs/patches/2026-04-03-runtime-control-plane-followups.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-cleanup-execution-plan.md`
- `docs/patches/2026-04-11-runtime-subagent-v3-real-queue-plan.md`

## Verification Evidence

### Dirty bucket

```bash
./.venv/bin/python -m pytest \
  tests/agent/test_runner.py \
  tests/agent/test_subagent_resources.py \
  tests/agent/test_task_cancel.py \
  tests/providers/test_provider_retry.py \
  tests/agent/test_consolidate_offset.py \
  -q
```

Observed: `181 passed in 7.06s`

### Historical focused runtime bridge bucket

```bash
./.venv/bin/python -m pytest \
  tests/agent/test_empty_success.py \
  tests/providers/test_provider_retry.py \
  tests/agent/test_runner.py \
  tests/test_openai_api.py \
  tests/agent/test_compact_state.py \
  tests/agent/test_loop_consolidation_tokens.py \
  tests/agent/test_memory_consolidation_types.py \
  tests/agent/test_protocol_state.py \
  tests/agent/test_heartbeat_service.py \
  tests/command/test_fastlane.py \
  tests/command/test_workspace_bridge.py \
  -q
```

Observed: `127 passed, 44 warnings in 5.98s`

### CLI/channel config-path bucket

```bash
./.venv/bin/python -m pytest \
  tests/channels/test_channel_plugins.py \
  tests/cli/test_commands.py \
  -k "channels or config_path" \
  -q
```

Observed: `38 passed, 55 deselected in 4.20s`

## Follow-up Work

1. if later work revives the `runtime-model-subagent-*` ideas, replay them selectively using `docs/patches/2026-04-12-donor-branch-triage.md` instead of merging the donor branches wholesale
2. if the refresh branch stays stable, promote it and archive superseded historical branches
3. revisit the local `.venv` provisioning path so refresh verification does not depend on ad hoc dependency repair
