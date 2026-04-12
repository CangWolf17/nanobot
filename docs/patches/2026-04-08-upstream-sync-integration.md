# Runtime Upstream Sync Integration

> Historical note: This document records the 2026-04-08 intermediate upstream-sync pass. It is no longer the current truth ledger after the 2026-04-12 refresh; see `docs/patches/2026-04-12-upstream-refresh-ledger.md` for current branch status.


## Scope

This document records the manual upstream-sync work integrated on top of the self-use runtime fork during the 2026-04-08 Project 9 pass.

Integration branch:

- `runtime-upstream-sync-2026-04-08`

Relevant commits on that branch:

- `c700fda feat(runtime): checkpoint model-subagent context refactor`
- `1eb0a29 chore(runtime): integrate selected upstream changes`

## Why There Was A Pre-Step Before The Upstream Adaptation

During Project 9 verification, the settled fork baseline at `a6465d5` was not actually self-consistent for the focused runtime bucket:

- `nanobot/agent/loop.py` already passed `runtime_metadata=...` into `ContextBuilder.build_messages(...)`
- `nanobot/agent/context.py` on the branch head did not yet accept that parameter

That mismatch caused the runtime verification bucket to fail before the upstream CLI config-path adaptation could be trusted.

To stabilize the live fork baseline without losing prior GPT-authored work, the previously dirty runtime changes were first preserved on:

- `runtime-model-subagent-refactor-checkpoint-2026-04-08`
- commit: `f5b55cd feat(runtime): checkpoint model-subagent context refactor`

Then the same checkpoint was cherry-picked onto the Project 9 integration branch as `c700fda`.

## Upstream Commits Reviewed

### Absorbed via manual adaptation

- `7332d13 feat(cli): add --config option to channels login and status commands`
- `3558fe4 fix(cli): honor custom config path in channel commands`
- `11ba733 fix(test): update load_config mock to accept config_path parameter`

### Deferred

- `7a6416b test(matrix): skip cleanly when optional deps are missing`

Reason for deferral:

- test-only value for the current self-use workflow was lower
- the matrix test surface has already diverged materially in the fork
- the higher-value channel CLI config-path work was enough for this pass

## Compatibility Adaptations Applied

The upstream CLI change set was not cherry-picked blindly because `nanobot/cli/commands.py` already carries self-use fork logic for heartbeat, harness, and workspace bridge behavior.

Manual adaptations applied in `1eb0a29`:

1. `channels status` now accepts `--config` / `-c`
2. `channels login` now accepts `--config` / `-c`
3. both commands resolve the explicit path through `Path(...).expanduser().resolve()`
4. both commands call `set_config_path(resolved_path)` before loading config, so downstream config-path consumers stay aligned
5. `tests/channels/test_channel_plugins.py` now covers:
   - explicit config-path handling for `channels status`
   - explicit config-path handling for `channels login`
   - the updated `load_config(config_path=None)` mock signature

## Verification

### Channel / config-path bucket

```bash
"/home/admin/.nanobot-fork/venv/bin/python" -m pytest \
  tests/channels/test_channel_plugins.py \
  tests/cli/test_commands.py \
  -k "channels or config_path" \
  -q
```

Observed: `37 passed, 55 deselected in 1.02s`

### Focused runtime bridge bucket

```bash
"/home/admin/.nanobot-fork/venv/bin/python" -m pytest \
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

Observed: `112 passed, 44 warnings in 2.57s`

### Heartbeat CLI subset

```bash
"/home/admin/.nanobot-fork/venv/bin/python" -m pytest \
  tests/cli/test_commands.py \
  -k heartbeat \
  -q
```

Observed: `7 passed, 50 deselected in 0.49s`

### Manual smoke

```bash
"/home/admin/.nanobot-fork/venv/bin/python" -m nanobot.cli.commands agent -m "/help" --no-markdown
"/home/admin/.nanobot-fork/venv/bin/python" -m nanobot.cli.commands agent -m "/model help" --no-markdown
```

Observed:

- `/help` returned the expected workspace/runtime command surface summary
- `/model help` returned the runtime model command help text successfully

## Final State Of This Pass

- the fork now supports explicit config-path selection for `channels login` and `channels status`
- the self-use runtime baseline was stabilized first so the verification bucket reflects real runtime health, not a local branch inconsistency
- the matrix optional-deps test skip remains deferred for a later pass
