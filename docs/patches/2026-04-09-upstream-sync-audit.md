# Upstream Sync Audit

## Refreshed Base

- upstream head: `ba8bce0`
- fork reference branch: `cangwolf/runtime-patches-2026-04-02`

## Must Keep

- Unified empty-success recovery across `nanobot/providers/base.py`, `nanobot/agent/runner.py`, `nanobot/agent/loop.py`, and `nanobot/api/server.py`, so empty model output is treated as a runtime failure and uses the same fallback family everywhere.
- Dual-track compaction: archive memory in `nanobot/agent/memory.py` stays separate from session compact state in `nanobot/agent/compact_state.py`, with independent offsets, budgeting, and prompt injection.
- Explicit runtime protocol state stays small and machine-readable in the runtime core, including `strict_dev_mode`, `task_kind`, `phase`, `work_mode`, `current_step`, and gate summaries.
- Protocol phase to skill-hint mapping survives in `nanobot/agent/loop.py`, `nanobot/agent/skills.py`, and `nanobot/agent/policy/dev_discipline.py`, so runtime enforcement still follows the declared control-plane state.
- Runtime metadata continues to flow explicitly into message building in `nanobot/agent/context.py` instead of relying on prose-only prompt conventions.
- Visible-output sanitization stays in the runtime loop so runtime context echoes and reasoning-only blocks do not leak back to users or channels.
- Workspace fastlane eligibility continues to come from workspace command metadata and router `--route-json` output, not from runtime hardcoded command tables.
- Workspace bridge metadata propagation survives through loop, spawn, subagent, and progress UX paths, including `workspace_runtime`, `workspace_work_mode`, and `workspace_agent_cmd`.
- Minimal workflow readiness for `/笔记` stays intact at the runtime boundary, including prepare-input and postprocess handling in `nanobot/command/workspace_bridge.py` and workflow progress hints in `nanobot/agent/loop.py`.
- Runtime-owned `/harness` behavior survives in `nanobot/command/*` and `nanobot/harness/*`, with `harnesses/store.json` as durable truth and markdown files treated as projections only.
- Harness projection sync and migration cleanup stay runtime-owned, including canonical-store reads, stable workflow ids, apply/update semantics, and service/store-driven auto-continue decisions.
- Heartbeat keeps the fork execution contract: maintenance hooks before file gating, tool-call `skip` or `run` decisioning, explicit `Current Time:` context, and notification delivery through the post-run evaluator gate.
- Channel CLI explicit `--config` resolution and `set_config_path(...)` behavior must survive in `nanobot/cli/commands.py`, even though upstream already ships the base feature, because the fork has overlapping heartbeat, harness, and workspace-bridge edits in the same file.
- Provider response normalization still needs fork behavior for reasoning payload preservation, cached-token accounting, tool-call sanitization, prompt-caching headers, and image-strip retry fallback in the OpenAI-compatible, Codex, Azure, Anthropic, and shared Responses API paths.
- Feishu and Telegram adapter behavior from the live fork remains required, especially reply context, streaming semantics, completion notices, mention and thread routing, media context, and channel-safe message splitting.

## Likely Obsolete After Upstream

- Earlier fork-only glue for `channels status --config` and `channels login --config` should not survive as a custom divergence where upstream already covers the base capability; only the integration with the fork's custom `nanobot/cli/commands.py` shape should remain.
- Earlier fork-only helper fixes for keeping assistant message content non-`None` look largely upstreamed already via current `nanobot/utils/helpers.py` history and should shrink to zero or near-zero delta if upstream still matches the required contract.
- Older retry-plumbing differences that upstream has since replaced with structured retry classification and better `Retry-After` handling should be deleted unless they are still required for the fork's empty-success contract.
- Any patch surface that existed only to bridge the pre-2026-04-08 channel CLI config-path gap should disappear on the new upstream base, because that upstream work is already present and was previously recorded as manually absorbed.

## Manual Conflict Hotspots

- `nanobot/agent/loop.py`: highest-risk file. It concentrates runtime protocol, empty-success fallback, workspace metadata propagation, visible-output sanitization, compact-state budgeting, workflow progress hints, and upstream runtime changes.
- `nanobot/cli/commands.py`: overlap point for upstream CLI work and fork-only channel `--config`, heartbeat execution framing, harness command ownership, and workspace bridge behavior.
- `nanobot/agent/context.py`: upstream prompt-building changes overlap with the fork's explicit runtime metadata injection and prompt-cache handling.
- `nanobot/agent/runner.py`: empty-success fallback and session serialization behavior overlap directly with upstream runner changes.
- `nanobot/providers/base.py`: shared retry classification and empty-success semantics affect every later provider integration.
- `nanobot/providers/openai_compat_provider.py`, `nanobot/providers/azure_openai_provider.py`, `nanobot/providers/openai_codex_provider.py`, and `nanobot/providers/anthropic_provider.py`: response parsing, reasoning payload preservation, cached-token accounting, and fallback handling can easily regress under upstream parser changes.
- `nanobot/command/workspace_bridge.py`, `nanobot/command/fastlane.py`, and `nanobot/command/builtin.py`: these files define the runtime versus workspace ownership boundary and must be reconciled carefully with the fork's router metadata contract, `/harness` ownership, and `/笔记` workflow prepare and postprocess behavior.
- `nanobot/harness/service.py`, `nanobot/harness/models.py`, `nanobot/harness/store.py`, and `nanobot/harness/projections.py`: canonical-store semantics, projection sync, workflow continuation, and migration cleanup all live here and represent a large fork-only surface.
- `nanobot/agent/subagent.py`, `nanobot/agent/subagent_resources.py`, and `nanobot/agent/tools/spawn.py`: subagent resource recovery, workspace metadata flow, and subagent-allowed enforcement overlap with upstream runtime-core edits.
- `nanobot/channels/feishu.py` and `nanobot/channels/telegram.py`: channel adapters still carry fork-specific reply, streaming, and routing behavior that can be silently broken by upstream changes.
- `nanobot/utils/helpers.py`: helper-layer content normalization and channel-safe formatting changes are small in file size but high in blast radius because they affect providers, channels, and CLI output together.

## Wave Mapping

- Wave 1: `nanobot/config/schema.py`, `nanobot/utils/helpers.py`, `nanobot/providers/base.py`, `nanobot/providers/registry.py`, `nanobot/cli/commands.py`
- Wave 2: `nanobot/agent/context.py`, `nanobot/agent/loop.py`, `nanobot/agent/runner.py`, `nanobot/agent/memory.py`, `nanobot/agent/compact_state.py`, `nanobot/agent/hook.py`, `nanobot/agent/skills.py`, `nanobot/agent/policy/dev_discipline.py`, `nanobot/agent/subagent.py`, `nanobot/agent/subagent_resources.py`, `nanobot/agent/tools/spawn.py`, `nanobot/api/server.py`
- Wave 3: `nanobot/command/builtin.py`, `nanobot/command/harness.py`, `nanobot/command/fastlane.py`, `nanobot/command/workspace_bridge.py`, `nanobot/harness/models.py`, `nanobot/harness/store.py`, `nanobot/harness/workflows.py`, `nanobot/harness/projections.py`, `nanobot/harness/service.py`, `nanobot/harness/cli.py`, `nanobot/heartbeat/service.py`
- Wave 4: `nanobot/providers/openai_compat_provider.py`, `nanobot/providers/anthropic_provider.py`, `nanobot/providers/azure_openai_provider.py`, `nanobot/providers/openai_codex_provider.py`, `nanobot/providers/github_copilot_provider.py`, `nanobot/providers/openai_responses/converters.py`, `nanobot/providers/openai_responses/parsing.py`, `nanobot/channels/base.py`, `nanobot/channels/registry.py`, `nanobot/channels/manager.py`, `nanobot/channels/feishu.py`, `nanobot/channels/telegram.py`, `nanobot/cli/commands.py`, `nanobot/utils/helpers.py`

## Wave 1 Remaining Divergence

- `nanobot/providers/base.py`: fork-local divergence still survives here. Blank no-tool provider responses are now classified as `empty model response` and routed through the shared retry path instead of being treated as successful completions.
- `nanobot/config/schema.py`: no surviving fork-local delta after Wave 1. Upstream alias handling and persisted provider config output remain the active shape.
- `nanobot/utils/helpers.py`: no surviving fork-local delta after Wave 1. Upstream already preserves the required non-`None` assistant message content behavior in shared helper construction.
- `nanobot/providers/registry.py`: no surviving fork-local delta after Wave 1. Upstream registry aliases remain aligned with config parsing for the provider names exercised in this sync.
- `nanobot/cli/commands.py`: no surviving Wave 1 fork-local delta yet. Upstream already carries the base channel `--config` support, while the fork's deeper heartbeat, harness, and workspace-bridge overlap remains deferred to later waves.

## Wave 2 Remaining Divergence

- `nanobot/agent/context.py`, `nanobot/agent/loop.py`, `nanobot/agent/runner.py`, and `nanobot/api/server.py`: explicit runtime metadata injection, runtime-context echo stripping, hidden think-tag stripping, and unified empty-success fallback still survive as fork-local behavior on top of upstream.
- `nanobot/agent/compact_state.py`, `nanobot/agent/memory.py`, `nanobot/agent/skills.py`, `nanobot/agent/policy/dev_discipline.py`, and `nanobot/agent/loop.py`: dual-track compact state, explicit runtime protocol fields, and phase-driven discipline/skill hints still remain fork-local.
- `nanobot/agent/subagent.py`, `nanobot/agent/subagent_resources.py`, `nanobot/agent/tools/spawn.py`, and `nanobot/agent/loop.py`: workspace runtime metadata propagation, subagent gating, and resource recovery behavior still remain fork-local.

## Wave 3 Remaining Divergence

- `nanobot/command/builtin.py`, `nanobot/command/harness.py`, `nanobot/command/fastlane.py`, and `nanobot/command/workspace_bridge.py`: runtime-owned `/harness`, workspace-router fastlane routing, workflow prepare/postprocess hooks, and canonical-store workflow continuation still remain fork-local.
- `nanobot/harness/models.py`, `nanobot/harness/store.py`, `nanobot/harness/workflows.py`, `nanobot/harness/projections.py`, `nanobot/harness/service.py`, and `nanobot/harness/cli.py`: canonical harness-store durability, projection-only markdown sync, stable workflow ids, and service/store-driven continuation still remain fork-local.
- `nanobot/heartbeat/service.py`: maintenance-before-gating, tool-call run/skip decisioning, explicit `Current Time:` context, and post-run notification gating still remain fork-local.

## Wave 4 Remaining Divergence

- `nanobot/providers/openai_compat_provider.py`, `nanobot/providers/anthropic_provider.py`, `nanobot/providers/azure_openai_provider.py`, `nanobot/providers/openai_codex_provider.py`, `nanobot/providers/github_copilot_provider.py`, `nanobot/providers/openai_responses/converters.py`, and `nanobot/providers/openai_responses/parsing.py`: no surviving fork-local delta after Wave 4. Upstream already preserves the required reasoning, cached-token, prompt-caching, sanitization, and shared Responses API behavior on this base.
- `nanobot/channels/base.py`, `nanobot/channels/registry.py`, `nanobot/channels/manager.py`, `nanobot/channels/feishu.py`, `nanobot/channels/telegram.py`, `nanobot/cli/commands.py`, and `nanobot/utils/helpers.py`: no surviving fork-local delta after Wave 4. Upstream already preserves the required plugin, delta-coalescing, Feishu, Telegram, and channel CLI behavior on this base.

## Final Integration Note

- See `docs/patches/2026-04-09-upstream-sync-integration.md` for the final verification record, smoke-command results, and cutover risks.
