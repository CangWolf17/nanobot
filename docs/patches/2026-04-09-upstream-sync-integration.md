# Upstream Sync Integration

## Base

- upstream head: `ba8bce0`
- integration branch: `cangwolf/upstream-sync-2026-04-09`

## What Stayed Fork-Only

- Unified empty-success retry/fallback handling still lives in `nanobot/providers/base.py`, `nanobot/agent/runner.py`, `nanobot/agent/loop.py`, and `nanobot/api/server.py`.
- Explicit runtime metadata injection, prompt-cache-aware context building, runtime echo stripping, and hidden think-tag stripping still live in `nanobot/agent/context.py`, `nanobot/agent/loop.py`, and `nanobot/agent/runner.py`.
- Dual-track compact state and explicit runtime protocol/discipline behavior still live in `nanobot/agent/compact_state.py`, `nanobot/agent/memory.py`, `nanobot/agent/skills.py`, `nanobot/agent/policy/dev_discipline.py`, and `nanobot/agent/loop.py`.
- Workspace runtime metadata propagation, subagent gating, and resource recovery still live in `nanobot/agent/subagent.py`, `nanobot/agent/subagent_resources.py`, `nanobot/agent/tools/spawn.py`, and `nanobot/agent/loop.py`.
- Runtime-owned `/harness`, workspace-router fastlane routing, workflow prepare/postprocess hooks, canonical harness-store state, and workflow continuation still live in `nanobot/command/builtin.py`, `nanobot/command/harness.py`, `nanobot/command/fastlane.py`, `nanobot/command/workspace_bridge.py`, `nanobot/harness/models.py`, `nanobot/harness/store.py`, `nanobot/harness/workflows.py`, `nanobot/harness/projections.py`, `nanobot/harness/service.py`, and `nanobot/harness/cli.py`.
- Heartbeat maintenance/decision/notification behavior still lives in `nanobot/heartbeat/service.py`.

## What Upstream Replaced

- Shared helper/config/registry compatibility no longer needs fork-local code in `nanobot/config/schema.py`, `nanobot/utils/helpers.py`, `nanobot/providers/registry.py`, or `nanobot/cli/commands.py` for the original Wave 1 goals.
- Provider-layer reasoning, cached-token, prompt-caching, sanitization, image-strip fallback, and shared Responses API behavior no longer need fork-local code in `nanobot/providers/openai_compat_provider.py`, `nanobot/providers/anthropic_provider.py`, `nanobot/providers/azure_openai_provider.py`, `nanobot/providers/openai_codex_provider.py`, `nanobot/providers/github_copilot_provider.py`, `nanobot/providers/openai_responses/converters.py`, and `nanobot/providers/openai_responses/parsing.py`.
- Channel plugin, delta-coalescing, Feishu, Telegram, and CLI config-path behavior no longer need fork-local code in `nanobot/channels/base.py`, `nanobot/channels/registry.py`, `nanobot/channels/manager.py`, `nanobot/channels/feishu.py`, `nanobot/channels/telegram.py`, `nanobot/cli/commands.py`, and `nanobot/utils/helpers.py`.

## Verification

- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest -q` on fresh upstream base: `1336 passed, 2 skipped, 48 warnings in 42.01s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/providers/test_provider_retry.py tests/cli/test_commands.py tests/test_openai_api.py tests/tools/test_filesystem_tools.py tests/tools/test_tool_validation.py -q`: `169 passed, 47 warnings in 5.40s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/agent/test_empty_success.py tests/agent/test_context_prompt_cache.py tests/agent/test_runner.py tests/test_openai_api.py -q`: `68 passed, 44 warnings in 1.07s`
- Reviewer-approved Task 5 bucket `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/agent/test_loop_consolidation_tokens.py tests/agent/test_consolidate_offset.py tests/agent/test_consolidator.py tests/agent/test_task_cancel.py tests/agent/test_runner.py tests/agent/test_context_prompt_cache.py tests/agent/test_compact_state.py tests/agent/test_protocol_state.py tests/agent/test_subagent_resources.py -q`: `132 passed in 1.12s`
- Cross-wave runtime/api regression `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/agent/test_empty_success.py tests/test_openai_api.py -q`: `18 passed, 44 warnings in 1.35s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/command/test_fastlane.py tests/command/test_workspace_bridge.py tests/command/test_workspace_bridge_harness.py tests/command/test_workspace_workflow_continuation.py tests/command/test_harness_command.py tests/harness/test_models.py tests/harness/test_store.py tests/harness/test_projections.py tests/harness/test_service.py tests/agent/test_heartbeat_service.py -q`: `67 passed in 0.85s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/command/test_fastlane.py -q`: `4 passed in 0.24s`
- Provider exploratory regression `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/providers/test_reasoning_content.py tests/providers/test_stepfun_reasoning.py tests/providers/test_prompt_cache_markers.py tests/providers/test_anthropic_thinking.py -q`: `26 passed in 3.14s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/providers/test_openai_responses.py tests/providers/test_cached_tokens.py tests/providers/test_azure_openai_provider.py tests/providers/test_mistral_provider.py tests/providers/test_providers_init.py -q`: `93 passed in 2.32s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest tests/channels/test_channel_plugins.py tests/channels/test_channel_manager_delta_coalescing.py tests/channels/test_feishu_streaming.py tests/channels/test_feishu_reply.py tests/channels/test_feishu_tool_hint_code_block.py tests/channels/test_feishu_table_split.py tests/channels/test_telegram_channel.py tests/cli/test_commands.py -q`: `200 passed, 4 warnings in 8.35s`
- Final full regression `/home/admin/.nanobot-upstream-sync/venv/bin/python -m pytest -q`: `1423 passed, 2 skipped, 48 warnings in 40.20s`
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m nanobot.cli.commands agent -m "/help" --no-markdown`: exited `0` and returned the runtime help surface with `/interrupt`, `/harness`, `/dream`, `/dream-log`, and `/dream-restore`.
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m nanobot.cli.commands agent -m "/model help" --no-markdown`: exited `0` and returned the `/model` help surface through the runtime fastlane fallback.
- `/home/admin/.nanobot-upstream-sync/venv/bin/python -m nanobot.cli.commands channels status --config /home/admin/.nanobot/config.json`: exited `0`, skipped Matrix because optional deps are not installed, and reported Feishu enabled with all other built-in channels disabled.

## Remaining Risks

- `nanobot/api/server.py` still emits `aiohttp` `NotAppKeyWarning` in API tests. It is non-fatal, but it is still visible in both targeted and full-suite runs.
- The smoke commands now depend on a local `/home/admin/.nanobot/config.json` migration outside the repo: the rejected root `memory` block was removed, its model preference was moved to `agents.defaults.dream.modelOverride`, and the OpenAI provider credentials were aligned with `~/.codex/config.toml` and `auth.json`.
- `nanobot/cli/commands.py` did not regain the older fork's gateway-side heartbeat wiring because the Task 8 bucket and full regression stayed green without it; reviewer should decide whether that behavior is intentionally obsolete or should be restored in a follow-up.
- `nanobot/command/workspace_bridge.py` now prefers canonical `harnesses/store.json` continuation state; reviewer should confirm that this is the intended long-term source of truth for workflow continuation.
- The underlying external workspace router at `/home/admin/.nanobot/workspace/scripts/router.py` still has a `scripts` import-path problem in this environment; `/model help` is green because `nanobot/command/fastlane.py` now provides a runtime-owned fallback for that help surface instead of exposing the traceback.
