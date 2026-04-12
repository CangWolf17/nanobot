# Nanobot Upstream Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the self-use fork on top of the latest `upstream/main` while preserving the required runtime, workspace, harness, provider, and channel behavior documented in the fork patch notes.

**Architecture:** Start from a fresh integration branch created from the latest fetched `upstream/main`, not from the current fork branch. Reapply fork behavior by wave, using the current fork branch and the existing `docs/patches/*.md` files as the contract. Keep work isolated in `.worktrees/`, verify each wave with targeted tests, and stop expansion of patch surface wherever upstream now already covers the requirement.

**Tech Stack:** Git, git worktrees, Python 3.11+, virtualenv, pytest, GitHub CLI, nanobot runtime, workspace bridge, harness subsystem.

---

### Task 1: Create The Isolated Integration Workspace

**Files:**
- Create: `.worktrees/upstream-sync-2026-04-09/`
- Create: `/home/admin/.nanobot-upstream-sync/venv/`
- Modify: none
- Test: baseline `pytest`

- [ ] **Step 1: Verify the worktree location is safe to use**

Run:

```bash
git check-ignore -v .worktrees
```

Expected: output shows `.gitignore` ignores `.worktrees/`.

- [ ] **Step 2: Refresh both remotes before branching**

Run:

```bash
git fetch origin
git fetch upstream
git rev-parse --short upstream/main
```

Expected: the final command prints the refreshed upstream head SHA.

- [ ] **Step 3: Create the integration worktree from refreshed upstream**

Run:

```bash
git worktree add ".worktrees/upstream-sync-2026-04-09" -b "cangwolf/upstream-sync-2026-04-09" upstream/main
```

Expected: a new worktree exists at `.worktrees/upstream-sync-2026-04-09` and `git status --short --branch` inside it shows a clean branch tracking the new integration branch.

- [ ] **Step 4: Create and install the isolated Python environment**

Run:

```bash
python3 -m venv /home/admin/.nanobot-upstream-sync/venv
/home/admin/.nanobot-upstream-sync/venv/bin/pip install --upgrade pip
/home/admin/.nanobot-upstream-sync/venv/bin/pip install -e ".[dev]"
```

Expected: editable install completes in the new environment. If dependency resolution fails, stop and report the exact package failure before continuing.

- [ ] **Step 5: Verify the upstream baseline before reapplying fork patches**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest -q
```

Expected: upstream baseline is green. If it is not green, capture the failing tests and ask whether to proceed with a non-green upstream baseline.

### Task 2: Write The Divergence Audit

**Files:**
- Create: `docs/patches/2026-04-09-upstream-sync-audit.md`
- Modify: none
- Test: audit commands only

- [ ] **Step 1: Capture the fork-only commit and file surface**

Run from `.worktrees/upstream-sync-2026-04-09`:

```bash
git log --oneline --left-right upstream/main...cangwolf/runtime-patches-2026-04-02
git diff --stat upstream/main...cangwolf/runtime-patches-2026-04-02
git diff --name-only upstream/main...cangwolf/runtime-patches-2026-04-02
git diff --dirstat=files,0 upstream/main...cangwolf/runtime-patches-2026-04-02
```

Expected: you have a concrete list of commit themes, changed files, and hotspot directories.

- [ ] **Step 2: Draft the audit document with explicit classification**

Write this structure into `docs/patches/2026-04-09-upstream-sync-audit.md` and replace the instruction text with the concrete repo-specific findings gathered in Step 1:

```md
# Upstream Sync Audit

## Refreshed Base

- upstream head: paste the exact output of `git rev-parse --short upstream/main`
- fork reference branch: `cangwolf/runtime-patches-2026-04-02`

## Must Keep

- one bullet per required invariant from the fork docs, starting with empty-success unification, dual-track memory, runtime protocol, workspace fastlane, and harness canonical-store behavior

## Likely Obsolete After Upstream

- one bullet per fork patch that upstream now already covers well enough that no fork-only code should survive

## Manual Conflict Hotspots

- `nanobot/agent/loop.py`: note the runtime contract conflicts and why they are high risk
- `nanobot/cli/commands.py`: note the overlap between upstream CLI work and fork-only runtime commands
- add one bullet for every other hotspot discovered in Step 1

## Wave Mapping

- Wave 1: list the shared contract files
- Wave 2: list the agent runtime files
- Wave 3: list the command, harness, and heartbeat files
- Wave 4: list the provider and channel files
```

Expected: every fork-only behavior is classified as `must keep`, `likely obsolete`, or `manual conflict hotspot`.

- [ ] **Step 3: Cross-check the audit against the existing fork docs**

Read and reconcile:

```text
docs/patches/2026-04-02-self-use-runtime-patch-playbook.md
docs/patches/2026-04-03-runtime-control-plane-followups.md
docs/patches/2026-04-08-upstream-sync-integration.md
```

Expected: every invariant from those docs is represented in the new audit.

### Task 3: Reconcile The Shared Contract Layer

**Files:**
- Modify: `nanobot/config/schema.py`
- Modify: `nanobot/utils/helpers.py`
- Modify: `nanobot/providers/base.py`
- Modify: `nanobot/providers/registry.py`
- Modify: `nanobot/cli/commands.py`
- Test: `tests/providers/test_provider_retry.py`
- Test: `tests/cli/test_commands.py`
- Test: `tests/test_openai_api.py`
- Test: `tests/tools/test_filesystem_tools.py`
- Test: `tests/tools/test_tool_validation.py`

- [ ] **Step 1: Diff the shared contract files against the fork reference**

Run:

```bash
git diff upstream/main..cangwolf/runtime-patches-2026-04-02 -- \
  nanobot/config/schema.py \
  nanobot/utils/helpers.py \
  nanobot/providers/base.py \
  nanobot/providers/registry.py \
  nanobot/cli/commands.py
```

Expected: you can see exactly what the old fork changed in the shared contract layer.

- [ ] **Step 2: Reapply the required contract behavior on top of upstream**

Preserve all of the following while keeping upstream as the base shape:

```text
1. empty-success is classified as a runtime/provider failure and flows through shared retry handling
2. assistant message content is never left as None in shared message helpers
3. provider registry and config schema stay aligned for aliases, custom providers, and persisted config output
4. channel CLI --config handling survives in commands.py without regressing heartbeat, harness, or workspace-bridge logic
```

Expected: the edited files reflect the latest upstream structure plus the listed fork contracts.

- [ ] **Step 3: Run focused verification for the shared contract layer**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest \
  tests/providers/test_provider_retry.py \
  tests/cli/test_commands.py \
  tests/test_openai_api.py \
  tests/tools/test_filesystem_tools.py \
  tests/tools/test_tool_validation.py -q
/home/admin/.nanobot-upstream-sync/venv/bin/python -m py_compile \
  nanobot/config/schema.py \
  nanobot/utils/helpers.py \
  nanobot/providers/base.py \
  nanobot/providers/registry.py \
  nanobot/cli/commands.py
```

Expected: the focused test bucket passes and the edited files compile cleanly.

- [ ] **Step 4: Update the audit document with any surviving divergence**

Add the exact remaining deltas for these files to `docs/patches/2026-04-09-upstream-sync-audit.md`.

Expected: the audit says which shared-contract changes are still fork-only after integration.

### Task 4: Reconcile The Runtime Prompt And Fallback Contract

**Files:**
- Modify: `nanobot/agent/context.py`
- Modify: `nanobot/agent/loop.py`
- Modify: `nanobot/agent/runner.py`
- Modify: `nanobot/api/server.py`
- Test: `tests/agent/test_empty_success.py`
- Test: `tests/agent/test_context_prompt_cache.py`
- Test: `tests/agent/test_runner.py`
- Test: `tests/test_openai_api.py`

- [ ] **Step 1: Check the runtime interface boundaries first**

Run:

```bash
git diff upstream/main..cangwolf/runtime-patches-2026-04-02 -- \
  nanobot/agent/context.py \
  nanobot/agent/loop.py \
  nanobot/agent/runner.py \
  nanobot/api/server.py
```

Expected: you can identify any signature drift before editing, especially around runtime metadata passed between loop and context.

- [ ] **Step 2: Reapply the prompt and fallback contract**

Preserve all of the following:

```text
1. runtime metadata is passed explicitly into message building
2. runtime context is prepended for the model but never echoed back to the user
3. internal reasoning-tag content is stripped from visible streamed and final output
4. empty direct responses use the same fallback family across provider, runner, loop, and API paths
5. per-session serialization and fixed-session API behavior still work
```

Expected: upstream runtime shape stays intact, but the fork-specific runtime contract is restored.

- [ ] **Step 3: Run the runtime prompt/fallback test bucket**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest \
  tests/agent/test_empty_success.py \
  tests/agent/test_context_prompt_cache.py \
  tests/agent/test_runner.py \
  tests/test_openai_api.py -q
/home/admin/.nanobot-upstream-sync/venv/bin/python -m py_compile \
  nanobot/agent/context.py \
  nanobot/agent/loop.py \
  nanobot/agent/runner.py \
  nanobot/api/server.py
```

Expected: the bucket passes and the loop/context/runner/API files compile.

### Task 5: Reconcile Memory, Protocol, Discipline, And Subagent Runtime Behavior

**Files:**
- Modify: `nanobot/agent/memory.py`
- Modify: `nanobot/agent/compact_state.py`
- Modify: `nanobot/agent/hook.py`
- Modify: `nanobot/agent/skills.py`
- Modify: `nanobot/agent/policy/dev_discipline.py`
- Modify: `nanobot/agent/subagent.py`
- Modify: `nanobot/agent/subagent_resources.py`
- Modify: `nanobot/agent/tools/spawn.py`
- Modify: `nanobot/agent/loop.py`
- Test: `tests/agent/test_compact_state.py`
- Test: `tests/agent/test_loop_consolidation_tokens.py`
- Test: `tests/agent/test_memory_consolidation_types.py`
- Test: `tests/agent/test_protocol_state.py`
- Test: `tests/agent/test_loop_workspace_progress.py`
- Test: `tests/agent/test_task_cancel.py`
- Test: `tests/agent/test_subagent_resources.py`

- [ ] **Step 1: Reapply the dual-track memory and protocol behavior**

Preserve all of the following:

```text
1. archive memory and compact state remain separate systems
2. compact state tracks its own offset and prompt injection state
3. compact-state token budgeting remains part of prompt estimation
4. runtime protocol fields stay explicit and small
5. protocol phase still maps to the expected skill hints
6. strict dev-discipline guards still follow the explicit runtime protocol
```

Expected: upstream memory/runtime changes are adopted without collapsing the fork's control-plane model.

- [ ] **Step 2: Reapply subagent and workspace-runtime metadata propagation**

Preserve all of the following:

```text
1. workspace_runtime, workspace_work_mode, and workspace_agent_cmd metadata still flow through loop, spawn, and subagent paths
2. subagent_allowed blocking remains enforced
3. subagent resource manager behavior survives upstream changes
4. workspace progress hints still surface for workflow-style commands including 笔记 and harness-related flows
```

Expected: the runtime still behaves like the current fork when workspace and subagent features are active.

- [ ] **Step 3: Run the runtime behavior test bucket**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest \
  tests/agent/test_compact_state.py \
  tests/agent/test_loop_consolidation_tokens.py \
  tests/agent/test_memory_consolidation_types.py \
  tests/agent/test_protocol_state.py \
  tests/agent/test_loop_workspace_progress.py \
  tests/agent/test_task_cancel.py \
  tests/agent/test_subagent_resources.py -q
/home/admin/.nanobot-upstream-sync/venv/bin/python -m py_compile \
  nanobot/agent/memory.py \
  nanobot/agent/compact_state.py \
  nanobot/agent/hook.py \
  nanobot/agent/skills.py \
  nanobot/agent/policy/dev_discipline.py \
  nanobot/agent/subagent.py \
  nanobot/agent/subagent_resources.py \
  nanobot/agent/tools/spawn.py \
  nanobot/agent/loop.py
```

Expected: the runtime-specific bucket passes and the touched files compile.

### Task 6: Reconcile Command, Harness, Workspace Bridge, And Heartbeat Behavior

**Files:**
- Modify: `nanobot/command/builtin.py`
- Modify: `nanobot/command/harness.py`
- Modify: `nanobot/command/fastlane.py`
- Modify: `nanobot/command/workspace_bridge.py`
- Modify: `nanobot/harness/models.py`
- Modify: `nanobot/harness/store.py`
- Modify: `nanobot/harness/workflows.py`
- Modify: `nanobot/harness/projections.py`
- Modify: `nanobot/harness/service.py`
- Modify: `nanobot/harness/cli.py`
- Modify: `nanobot/heartbeat/service.py`
- Test: `tests/command/test_fastlane.py`
- Test: `tests/command/test_workspace_bridge.py`
- Test: `tests/command/test_workspace_bridge_harness.py`
- Test: `tests/command/test_workspace_workflow_continuation.py`
- Test: `tests/command/test_harness_command.py`
- Test: `tests/harness/test_models.py`
- Test: `tests/harness/test_store.py`
- Test: `tests/harness/test_projections.py`
- Test: `tests/harness/test_service.py`
- Test: `tests/agent/test_heartbeat_service.py`

- [ ] **Step 1: Reapply the runtime-native command boundary**

Preserve all of the following:

```text
1. /harness remains runtime-native and is not delegated to the external workspace router
2. fastlane still uses the workspace router --route-json surface before the normal lock path
3. workspace metadata keys written by the bridge stay stable
4. prepare-agent-input and postprocess-agent flows survive for workflow commands
```

Expected: upstream command changes are integrated without breaking the fork's runtime/workspace split.

- [ ] **Step 2: Reapply the harness canonical-store model**

Preserve all of the following:

```text
1. harnesses/store.json remains the durable truth
2. markdown files remain projections only
3. projection sync removes obsolete legacy JSON after canonical state exists
4. stable workflow ids and apply/update semantics survive
5. auto-continue decisions come from service/store state, not markdown projections
```

Expected: harness behavior matches the fork's current runtime-owned model on top of upstream.

- [ ] **Step 3: Reapply the heartbeat execution contract**

Preserve all of the following:

```text
1. maintenance hooks still run before heartbeat file gating
2. heartbeat decision still uses the tool-call contract with skip/run
3. Current Time: stays in the heartbeat decision prompt
4. notification delivery still goes through the post-run evaluator gate
```

Expected: heartbeat service semantics stay aligned with the existing fork behavior.

- [ ] **Step 4: Run the command, harness, and heartbeat verification bucket**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest \
  tests/command/test_fastlane.py \
  tests/command/test_workspace_bridge.py \
  tests/command/test_workspace_bridge_harness.py \
  tests/command/test_workspace_workflow_continuation.py \
  tests/command/test_harness_command.py \
  tests/harness/test_models.py \
  tests/harness/test_store.py \
  tests/harness/test_projections.py \
  tests/harness/test_service.py \
  tests/agent/test_heartbeat_service.py -q
/home/admin/.nanobot-upstream-sync/venv/bin/python -m py_compile \
  nanobot/command/builtin.py \
  nanobot/command/harness.py \
  nanobot/command/fastlane.py \
  nanobot/command/workspace_bridge.py \
  nanobot/harness/models.py \
  nanobot/harness/store.py \
  nanobot/harness/workflows.py \
  nanobot/harness/projections.py \
  nanobot/harness/service.py \
  nanobot/harness/cli.py \
  nanobot/heartbeat/service.py
```

Expected: the command/harness/heartbeat bucket passes and all touched files compile.

### Task 7: Reconcile Provider Implementations

**Files:**
- Modify: `nanobot/providers/openai_compat_provider.py`
- Modify: `nanobot/providers/anthropic_provider.py`
- Modify: `nanobot/providers/azure_openai_provider.py`
- Modify: `nanobot/providers/openai_codex_provider.py`
- Modify: `nanobot/providers/github_copilot_provider.py`
- Modify: `nanobot/providers/openai_responses/converters.py`
- Modify: `nanobot/providers/openai_responses/parsing.py`
- Test: `tests/providers/test_openai_responses.py`
- Test: `tests/providers/test_cached_tokens.py`
- Test: `tests/providers/test_azure_openai_provider.py`
- Test: `tests/providers/test_mistral_provider.py`
- Test: `tests/providers/test_providers_init.py`

- [ ] **Step 1: Reapply the provider response contract**

Preserve all of the following:

```text
1. reasoning_content and thinking_blocks survive provider normalization
2. cached_tokens accounting survives provider parsing changes
3. prompt-caching markers and extra headers survive
4. tool-call payload sanitization still works
5. image-strip retry fallback still works where the fork depends on it
6. shared Responses API conversion/parsing behavior remains compatible for Codex and Azure paths
```

Expected: provider implementations use upstream improvements without losing the fork's response-contract behavior.

- [ ] **Step 2: Run the provider implementation verification bucket**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest \
  tests/providers/test_openai_responses.py \
  tests/providers/test_cached_tokens.py \
  tests/providers/test_azure_openai_provider.py \
  tests/providers/test_mistral_provider.py \
  tests/providers/test_providers_init.py -q
/home/admin/.nanobot-upstream-sync/venv/bin/python -m py_compile \
  nanobot/providers/openai_compat_provider.py \
  nanobot/providers/anthropic_provider.py \
  nanobot/providers/azure_openai_provider.py \
  nanobot/providers/openai_codex_provider.py \
  nanobot/providers/github_copilot_provider.py \
  nanobot/providers/openai_responses/converters.py \
  nanobot/providers/openai_responses/parsing.py
```

Expected: the provider implementation bucket passes and the touched files compile.

### Task 8: Reconcile Channel Framework, Adapters, And Channel CLI

**Files:**
- Modify: `nanobot/channels/base.py`
- Modify: `nanobot/channels/registry.py`
- Modify: `nanobot/channels/manager.py`
- Modify: `nanobot/channels/feishu.py`
- Modify: `nanobot/channels/telegram.py`
- Modify: `nanobot/cli/commands.py`
- Modify: `nanobot/utils/helpers.py`
- Test: `tests/channels/test_channel_plugins.py`
- Test: `tests/channels/test_channel_manager_delta_coalescing.py`
- Test: `tests/channels/test_feishu_streaming.py`
- Test: `tests/channels/test_feishu_reply.py`
- Test: `tests/channels/test_feishu_tool_hint_code_block.py`
- Test: `tests/channels/test_feishu_table_split.py`
- Test: `tests/channels/test_telegram_channel.py`
- Test: `tests/cli/test_commands.py`

- [ ] **Step 1: Reapply the channel framework contract**

Preserve all of the following:

```text
1. ChannelsConfig stays plugin-friendly and built-ins still shadow plugins
2. send_progress, send_tool_hints, bounded retries, and delta coalescing survive in the channel manager
3. channel CLI keeps explicit --config resolution and set_config_path behavior
```

Expected: upstream channel framework changes are integrated without losing the fork's manager and CLI semantics.

- [ ] **Step 2: Reapply the Feishu and Telegram adapter behavior**

Preserve all of the following:

```text
1. Feishu default streaming, CardKit card splitting, reply context, and optional completion notices
2. Telegram proxy separation, allowlist matching, topic/thread routing, mention-only group policy, reply/media context, and _stream_id-aware streaming behavior
3. helper changes needed for markdown, tables, and channel-safe message splitting
```

Expected: fork-specific channel behavior survives while upstream fixes are retained where they do not conflict.

- [ ] **Step 3: Run the channel verification bucket**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest \
  tests/channels/test_channel_plugins.py \
  tests/channels/test_channel_manager_delta_coalescing.py \
  tests/channels/test_feishu_streaming.py \
  tests/channels/test_feishu_reply.py \
  tests/channels/test_feishu_tool_hint_code_block.py \
  tests/channels/test_feishu_table_split.py \
  tests/channels/test_telegram_channel.py \
  tests/cli/test_commands.py -q
/home/admin/.nanobot-upstream-sync/venv/bin/python -m py_compile \
  nanobot/channels/base.py \
  nanobot/channels/registry.py \
  nanobot/channels/manager.py \
  nanobot/channels/feishu.py \
  nanobot/channels/telegram.py \
  nanobot/cli/commands.py \
  nanobot/utils/helpers.py
```

Expected: the channel bucket passes and the touched files compile.

### Task 9: Run Full Regression And Write The Final Integration Note

**Files:**
- Create: `docs/patches/2026-04-09-upstream-sync-integration.md`
- Modify: `docs/patches/2026-04-09-upstream-sync-audit.md`
- Modify: any touched `docs/patches/*.md` that need cross-reference updates
- Test: full `pytest`

- [ ] **Step 1: Run the full regression suite in the isolated worktree**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest -q
```

Expected: full test suite passes.

- [ ] **Step 2: Run the final smoke commands**

Run:

```bash
/home/admin/.nanobot-upstream-sync/venv/bin/python -m nanobot.cli.commands agent -m "/help" --no-markdown
/home/admin/.nanobot-upstream-sync/venv/bin/python -m nanobot.cli.commands agent -m "/model help" --no-markdown
```

Expected: both commands complete successfully and return the expected help surfaces.

- [ ] **Step 3: Write the final integration note**

Write this structure into `docs/patches/2026-04-09-upstream-sync-integration.md` and replace the instruction text with concrete repo-specific results:

```md
# Upstream Sync Integration

## Base

- upstream head: paste the exact output of `git rev-parse --short upstream/main`
- integration branch: `cangwolf/upstream-sync-2026-04-09`

## What Stayed Fork-Only

- one bullet per surviving fork-only behavior, with the exact file paths that still carry it

## What Upstream Replaced

- one bullet per old fork behavior that was deleted because upstream now covers it

## Verification

- list every targeted pytest command run during the wave work and the observed result
- record the final full `pytest -q` result
- record the observed result of `/help`, `/model help`, and `channels status --config /home/admin/.nanobot/config.json`

## Remaining Risks

- one bullet per unresolved risk that the reviewer should inspect before cutover
```

Expected: the note states exactly what still differentiates the fork from upstream and what was validated.

- [ ] **Step 4: Report handoff-ready review artifacts**

Return all of the following to the reviewer:

```text
1. final upstream head SHA used for the branch
2. files changed in the integration worktree
3. targeted test commands run and results
4. full pytest result
5. smoke command result
6. remaining risks and review questions
```

Expected: the reviewer has enough evidence to perform acceptance without rerunning basic discovery.

### Task 10: Reviewer Handoff Prompt

**Files:**
- Modify: none
- Test: none

- [ ] **Step 1: Use this prompt when dispatching another execution agent**

```text
You are implementing one task from `docs/superpowers/plans/2026-04-09-nanobot-upstream-sync.md` in `/home/admin/nanobot-fork-live`.

Before editing, read:
1. `docs/superpowers/specs/2026-04-09-nanobot-upstream-sync-design.md`
2. `docs/patches/2026-04-02-self-use-runtime-patch-playbook.md`
3. `docs/patches/2026-04-03-runtime-control-plane-followups.md`
4. `docs/patches/2026-04-08-upstream-sync-integration.md`
5. `docs/patches/2026-04-09-upstream-sync-audit.md` if it already exists

Work in an isolated git worktree under `.worktrees/`. Only work on the files listed for your assigned task. Preserve every listed fork invariant. Run the exact verification commands listed for your task. Do not create a git commit unless the user explicitly asks in this session.

When you stop, return only:
1. files changed
2. tests run and results
3. unresolved risks or blockers
4. anything the reviewer must inspect before the next task
```

Expected: every execution agent starts with the same context and returns the same review payload.
