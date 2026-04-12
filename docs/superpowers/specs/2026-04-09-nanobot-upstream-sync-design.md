# Nanobot Upstream Sync Design

## Goal

Move the self-use fork in `/home/admin/nanobot-fork-live` onto the latest `upstream/main` without losing the fork-only runtime, workspace-bridge, harness, and channel behaviors that the live workflow depends on.

## Current State

- Active fork branch: `cangwolf/runtime-patches-2026-04-02`
- Local upstream base previously integrated: `3558fe4`
- Current fork branch divergence from that base: `33` fork-only commits
- Latest upstream head observed during this planning pass: `3361ac9`
- Upstream drift since the old base: about `233` commits

The fork is no longer close enough to upstream for a low-risk direct rebase. The overlap is concentrated in `nanobot/agent/*`, `nanobot/providers/*`, `nanobot/channels/*`, `nanobot/cli/commands.py`, and `nanobot/config/schema.py`, which are also the exact places where the fork carries the most intentional local behavior.

## Design Decision

Use a fresh integration branch created from the latest `upstream/main`, then reapply the fork's required behaviors by domain.

Do not treat the current fork branch as the new long-term git base. Treat it as a reference branch and patch source.

Do not start with a direct history-preserving rebase on top of the current fork branch. That path is likely to produce dense conflicts in files where both sides have already been heavily edited, and it makes it too easy to keep accidental fork drift while losing the actual required runtime rules.

## Source Documents

The implementation must use these existing documents as the fork contract:

- `docs/patches/2026-04-02-self-use-runtime-patch-playbook.md`
- `docs/patches/2026-04-03-runtime-control-plane-followups.md`
- `docs/patches/2026-04-08-upstream-sync-integration.md`

These docs already record the intent behind the self-use fork and are the authoritative explanation for why some behavior must survive even if upstream now implements a different shape.

## Required Fork Invariants

The integration is only successful if these invariants still hold after the upgrade:

1. Empty-success classification stays unified in shared provider and runtime code.
2. Archive memory and session compact state remain separate tracks.
3. Runtime protocol stays explicit, small, and machine-readable rather than drifting back into prose-only prompts.
4. Fastlane eligibility stays declared by workspace command metadata, not by hardcoded runtime tables.
5. Runtime and workspace boundaries stay generic and minimal. Workspace keeps command semantics.

The domain-specific must-keep behavior also includes:

- unified empty-response retry and fallback behavior across provider, runner, loop, and API paths
- compact-state prompt injection, budgeting, and offset tracking
- strict runtime protocol and dev-discipline behavior
- workspace metadata propagation into loop, subagent, spawn, and progress UX
- runtime-owned `/harness`, canonical harness store, projection sync, and workflow continuation behavior
- `channels status` and `channels login` `--config` handling
- fork-specific provider response parsing, cached token accounting, and channel streaming semantics

## Non-Goals

This project does not include:

- production service cutover
- rollback execution on the live system
- refactoring unrelated upstream code only for style consistency
- deleting fork-only behavior just because upstream now has a similar feature name

The target is a validated integration branch that is ready for final acceptance review and later cutover.

## Execution Model

All implementation work should happen in an isolated git worktree under `.worktrees/`, which is already present and ignored by `.gitignore`.

Recommended primary branch and worktree names for this pass:

- branch: `cangwolf/upstream-sync-2026-04-09`
- worktree: `.worktrees/upstream-sync-2026-04-09`
- dedicated virtualenv: `/home/admin/.nanobot-upstream-sync/venv`

The current live worktree must remain untouched except for documentation updates approved in this planning session.

## Migration Architecture

The work should proceed in waves.

### Wave 0: Baseline Refresh And Divergence Audit

Refresh `upstream/main`, create the isolated worktree, verify the baseline, and write an audit document that classifies the fork's deltas into:

- must keep
- likely obsolete after upstream
- needs manual conflict review

This wave is the guardrail against blindly copying the old fork onto a new upstream head.

### Wave 1: Shared Contract Layer

Reconcile the shared contracts that affect multiple later domains:

- `nanobot/config/schema.py`
- `nanobot/utils/helpers.py`
- `nanobot/providers/base.py`
- `nanobot/providers/registry.py`
- overlapping CLI/config-path handling in `nanobot/cli/commands.py`

This wave establishes the common error semantics, message/content guarantees, and provider/config contract that later waves depend on.

### Wave 2: Agent Runtime Core

Reconcile the runtime loop and prompt contract first, then the memory and protocol layers:

- `nanobot/agent/context.py`
- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/memory.py`
- `nanobot/agent/compact_state.py`
- `nanobot/agent/hook.py`
- `nanobot/agent/skills.py`
- `nanobot/agent/policy/dev_discipline.py`
- `nanobot/agent/subagent.py`
- `nanobot/agent/subagent_resources.py`
- `nanobot/agent/tools/spawn.py`
- `nanobot/api/server.py`

This is the highest-risk wave because `nanobot/agent/loop.py` is the main concentration point for both upstream and fork behavior.

### Wave 3: Command, Harness, And Heartbeat Boundary

Reconcile the runtime-owned command boundary and harness stack:

- `nanobot/command/*`
- `nanobot/harness/*`
- `nanobot/heartbeat/service.py`

This wave must preserve the runtime-native `/harness` path, external workspace router contract, workflow continuation rules, canonical harness store, and projection sync behavior.

### Wave 4: Providers, Channels, And Channel CLI

Reconcile provider implementations before channel adapters.

This wave covers:

- `nanobot/providers/*`
- `nanobot/channels/*`
- remaining channel-facing paths in `nanobot/cli/commands.py`

The goal is to keep upstream improvements while reapplying fork-specific response parsing, retries, streaming, reply context, and config-path behavior.

### Wave 5: Regression, Surface Reduction, And Patch Notes

Run full validation, shrink redundant fork code where upstream already covers the requirement, and write a final integration note describing:

- what stayed fork-only
- what upstream now replaces
- what remains risky for later cutover

## Verification Strategy

Validation should be layered.

1. Run targeted tests after each wave.
2. Run `py_compile` on the files touched in each wave before moving on.
3. Run cross-domain regression buckets once Waves 1 through 4 land.
4. Run a final full test suite in the integration worktree.
5. Run a small set of manual smoke commands only after the full suite is green.

The minimum manual smoke set for final review is:

- `python -m nanobot.cli.commands agent -m "/help" --no-markdown`
- `python -m nanobot.cli.commands agent -m "/model help" --no-markdown`
- `python -m nanobot.cli.commands channels status --config /home/admin/.nanobot/config.json`

## Coordination Model For Other Agents

This project is suitable for multiple execution agents, but only when the ownership boundary is explicit.

- One agent should own one task from the implementation plan.
- Tasks that edit the same files should run sequentially.
- Runtime-core tasks should not run in parallel with other runtime-core tasks.
- Provider and channel tasks can run after the shared contract and command/harness waves are stable.
- Each execution agent must return files changed, tests run, test results, and unresolved risks.

## Acceptance Criteria

The upgraded branch is ready for final review only when all of the following are true:

1. The integration branch starts from the latest fetched `upstream/main`.
2. The fork invariants in this document still hold.
3. The targeted test buckets for each wave pass.
4. The full test suite passes in the isolated integration worktree.
5. The manual smoke commands succeed.
6. The final patch surface is smaller or cleaner than the current fork, not larger.
7. A new integration note records the surviving fork-only behaviors and remaining risks.

## Reviewer Boundary

The implementation can be delegated to other agents. Final acceptance should remain centralized.

Review work should focus on:

- whether the branch really starts from the refreshed upstream head
- whether required fork invariants survived in code and tests
- whether any task silently expanded patch surface instead of shrinking it
- whether the verification evidence is real and complete
