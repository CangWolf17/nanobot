"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import os
import subprocess
import time
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.compact_state import CompactStateManager
from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from nanobot.agent.memory import MemoryConsolidator
from nanobot.agent.policy.dev_discipline import should_disable_concurrent_tools
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.command import CommandContext, CommandRouter, register_builtin_commands
from nanobot.command.fastlane import try_workspace_fastlane
from nanobot.bus.queue import MessageBus
from nanobot.harness.service import HarnessService
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import (
        ChannelsConfig,
        ExecToolConfig,
        MemoryConsolidationConfig,
        WebSearchConfig,
    )
    from nanobot.cron.service import CronService


class _LoopHookChain(AgentHook):
    """Run the core loop hook first, then best-effort extra hooks."""

    __slots__ = ("_primary", "_extras")

    def __init__(self, primary: AgentHook, extra_hooks: list[AgentHook]) -> None:
        self._primary = primary
        self._extras = CompositeHook(extra_hooks)

    def wants_streaming(self) -> bool:
        return self._primary.wants_streaming() or self._extras.wants_streaming()

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._primary.before_iteration(context)
        await self._extras.before_iteration(context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._primary.on_stream(context, delta)
        await self._extras.on_stream(context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._primary.on_stream_end(context, resuming=resuming)
        await self._extras.on_stream_end(context, resuming=resuming)

    async def on_retry(
        self,
        context: AgentHookContext,
        *,
        attempt: int,
        max_retries: int,
        delay: float,
        error: str | None,
    ) -> None:
        await self._primary.on_retry(
            context,
            attempt=attempt,
            max_retries=max_retries,
            delay=delay,
            error=error,
        )
        await self._extras.on_retry(
            context,
            attempt=attempt,
            max_retries=max_retries,
            delay=delay,
            error=error,
        )

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._primary.before_execute_tools(context)
        await self._extras.before_execute_tools(context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._primary.after_iteration(context)
        await self._extras.after_iteration(context)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        content = self._primary.finalize_content(context, content)
        return self._extras.finalize_content(context, content)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 16_000
    _LEGACY_RUNTIME_CONTEXT_PREFIX = "Current Time:"
    _PRE_REPLY_CONSOLIDATION_SKIP_REASON_HARNESS_AUTO = "harness_auto_skip"
    _PRE_REPLY_CONSOLIDATION_SKIP_REASON_UNDER_BUDGET = "under_budget_skip"
    _RUNTIME_CONTEXT_ECHO_RE = re.compile(
        rf"^{re.escape(ContextBuilder._RUNTIME_CONTEXT_TAG)}\n"
        r"(?:(?:Rules:\n(?:- [^\n]*\n)+\n))?"
        r"Current Time: [^\n]*\n"
        r"(?:Channel: [^\n]*\n)?"
        r"(?:Chat ID: `?[^\n]*`?\n)?"
        r"\n*"
    )
    _LEGACY_RUNTIME_CONTEXT_ECHO_RE = re.compile(
        r"^Current Time: [^\n]*\n"
        r"(?:Channel: [^\n]*\n)?"
        r"(?:Chat ID: `?[^\n]*`?\n)?"
        r"\n*"
    )
    _STREAM_COMPLETION_NOTICE_MIN_SECONDS = 8.0
    _STREAM_COMPLETION_NOTICE_MIN_CHARS = 600
    _STREAM_COMPLETION_NOTICE_MIN_CHUNKS = 12

    def _redirect_workspace_harness_if_interrupted(
        self,
        user_input: str,
        *,
        interrupt_state: dict[str, Any] | None = None,
    ) -> None:
        state = interrupt_state or {}
        try:
            HarnessService.for_workspace(self.workspace).redirect_after_interrupt(
                user_input,
                session_key=str(state.get("session_key") or "").strip(),
                harness_id=str(state.get("workspace_harness_id") or "").strip(),
            )
        except Exception:
            return

    def _extract_runtime_metadata(self, msg: InboundMessage) -> dict[str, Any]:
        meta = msg.metadata or {}
        explicit_runtime = meta.get("workspace_runtime")
        runtime_meta: dict[str, Any] = {}

        work_mode = str(meta.get("workspace_work_mode") or "").strip()
        if not work_mode:
            try:
                from nanobot.agent.policy.dev_discipline import load_runtime_protocol

                protocol = load_runtime_protocol(self.workspace)
                work_mode = str((protocol or {}).get("work_mode") or "").strip()
            except Exception:
                work_mode = ""
        if work_mode in {"plan", "build"}:
            runtime_meta["work_mode"] = work_mode

        try:
            service = HarnessService.for_workspace(self.workspace)
            harness_id = str(meta.get("workspace_harness_id") or "").strip()
            if harness_id or meta.get("workspace_agent_cmd") == "harness":
                service_meta = service.runtime_metadata(
                    requested_auto=bool(meta.get("workspace_harness_auto")),
                    session_key=msg.session_key,
                    harness_id=harness_id,
                )
            else:
                service_meta = service.runtime_metadata_for_session(
                    requested_auto=bool(meta.get("workspace_harness_auto")),
                    session_key=msg.session_key,
                )
        except Exception:
            service_meta = {"has_active_harness": False}
        if bool(service_meta.get("has_active_harness")):
            runtime_meta.update(service_meta)
            return runtime_meta
        if isinstance(explicit_runtime, dict) and explicit_runtime:
            runtime_meta.update(explicit_runtime)
            return runtime_meta
        runtime_meta.update(service_meta)
        return runtime_meta

    def _decide_harness_auto_reentry(self, msg: InboundMessage) -> dict[str, Any]:
        meta = msg.metadata or {}
        origin_sender_id = str(
            meta.get("_origin_sender_id")
            or meta.get("_completion_notice_mention_user_id")
            or msg.sender_id
            or ""
        ).strip()
        decision: dict[str, Any] = {
            "should_fire": False,
            "reason": "not_harness_auto",
            "origin_sender_id": origin_sender_id,
        }
        if meta.get("workspace_agent_cmd") != "harness":
            return decision
        if not bool(meta.get("workspace_harness_auto")):
            decision["reason"] = "auto_disabled"
            return decision

        service = HarnessService.for_workspace(self.workspace)
        try:
            service_decision = service.decide_auto_continue(
                session_key=msg.session_key,
                sender_id=msg.sender_id,
                origin_sender_id=origin_sender_id,
                harness_id=str(meta.get("workspace_harness_id") or "").strip(),
            )
        except Exception:
            decision["reason"] = "no_active_harness"
            return decision
        decision["should_fire"] = service_decision.should_fire
        decision["reason"] = service_decision.reason
        decision["origin_sender_id"] = service_decision.origin_sender_id
        return decision

    def _should_schedule_harness_auto_continue(self, msg: InboundMessage) -> bool:
        return bool(self._decide_harness_auto_reentry(msg).get("should_fire"))

    async def _schedule_harness_auto_continue(self, msg: InboundMessage) -> None:
        service = HarnessService.for_workspace(self.workspace)
        decision = self._decide_harness_auto_reentry(msg)
        if not bool(decision.get("should_fire")):
            return
        meta = dict(msg.metadata or {})
        follow_up = InboundMessage(
            channel=msg.channel,
            sender_id="system",
            chat_id=msg.chat_id,
            content=msg.content,
            metadata=service.build_auto_continue_metadata(
                meta,
                origin_sender_id=str(
                    decision.get("origin_sender_id") or msg.sender_id or ""
                ).strip(),
                session_key=msg.session_key,
                harness_id=str(meta.get("workspace_harness_id") or "").strip(),
            ),
            session_key_override=msg.session_key,
        )
        await self.bus.publish_inbound(follow_up)

    def _stream_completion_notice_settings(self, channel_name: str) -> tuple[bool, str, bool]:
        if not self.channels_config or not channel_name:
            return False, "", False
        section = getattr(self.channels_config, channel_name, None)
        if section is None:
            return False, "", False
        if isinstance(section, dict):
            enabled = bool(
                section.get("streaming_completion_notice_enabled")
                or section.get("streamingCompletionNoticeEnabled")
            )
            text = str(
                section.get("streaming_completion_notice_text")
                or section.get("streamingCompletionNoticeText")
                or ""
            ).strip()
            mention_user = bool(
                section.get("streaming_completion_notice_mention_user")
                or section.get("streamingCompletionNoticeMentionUser")
            )
            return enabled, text, mention_user
        enabled = bool(
            getattr(section, "streaming_completion_notice_enabled", False)
            or getattr(section, "streamingCompletionNoticeEnabled", False)
        )
        text = str(
            getattr(section, "streaming_completion_notice_text", "")
            or getattr(section, "streamingCompletionNoticeText", "")
            or ""
        ).strip()
        mention_user = bool(
            getattr(section, "streaming_completion_notice_mention_user", False)
            or getattr(section, "streamingCompletionNoticeMentionUser", False)
        )
        return enabled, text, mention_user

    def _maybe_mark_stream_completion_notice(
        self,
        msg: InboundMessage,
        response: OutboundMessage,
        *,
        stream_started_at: float | None,
        stream_finished_at: float | None,
        stream_chunk_count: int,
        stream_char_count: int,
    ) -> None:
        meta = dict(response.metadata or {})
        if not meta.get("_streamed"):
            return

        enabled, notice_text, mention_user = self._stream_completion_notice_settings(msg.channel)
        if not enabled or not notice_text:
            return

        if stream_chunk_count <= 0 or stream_char_count <= 0:
            if not mention_user:
                return

        duration = 0.0
        if stream_started_at is not None and stream_finished_at is not None:
            duration = max(0.0, stream_finished_at - stream_started_at)

        is_harness_auto = bool((msg.metadata or {}).get("workspace_harness_auto"))
        auto_decision = self._decide_harness_auto_reentry(msg) if is_harness_auto else None
        if is_harness_auto and self._should_schedule_harness_auto_continue(msg):
            return

        content_len = len(str(response.content or ""))
        should_notify = mention_user or any(
            [
                is_harness_auto,
                duration >= self._STREAM_COMPLETION_NOTICE_MIN_SECONDS,
                stream_char_count >= self._STREAM_COMPLETION_NOTICE_MIN_CHARS,
                content_len >= self._STREAM_COMPLETION_NOTICE_MIN_CHARS,
                stream_chunk_count >= self._STREAM_COMPLETION_NOTICE_MIN_CHUNKS,
            ]
        )
        if not should_notify:
            return

        meta["_completion_notice"] = True
        meta["_completion_notice_text"] = notice_text
        mention_user_id = ""
        if mention_user:
            mention_user_id = str(
                (auto_decision or {}).get("origin_sender_id")
                or (msg.metadata or {}).get("_origin_sender_id")
                or msg.sender_id
                or ""
            ).strip()
        if mention_user_id:
            meta["_completion_notice_mention_user_id"] = mention_user_id
        response.metadata = meta

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        memory_config: MemoryConsolidationConfig | None = None,
        archive_provider: LLMProvider | None = None,
        archive_model: str | None = None,
        hooks: list[AgentHook] | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig, MemoryConsolidationConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.memory_config = memory_config or MemoryConsolidationConfig()
        self.archive_provider = archive_provider or provider
        self.archive_model = (
            archive_model or self.memory_config.model or (model or provider.get_default_model())
        )
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._inflight_turns: dict[str, dict[str, Any]] = {}
        self._extra_hooks: list[AgentHook] = hooks or []

        self.context = ContextBuilder(workspace, timezone=timezone)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # NANOBOT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.memory_consolidator = MemoryConsolidator(
            workspace=workspace,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            get_compact_state=self._get_session_compact_state,
            max_completion_tokens=provider.generation.max_tokens,
            archive_provider=self.archive_provider,
            archive_model=self.archive_model,
        )
        # Compatibility shim for tests/runtime paths that still expect
        # `loop.consolidator.archive(...)` from the upstream Consolidator surface.
        self.consolidator = SimpleNamespace(
            archive=self.memory_consolidator.archive_messages,
        )
        compact_model = self.memory_config.compact_state_model or self.archive_model
        self.compact_state = CompactStateManager(
            provider=self.archive_provider,
            model=compact_model,
            max_chars=max(0, int(self.memory_config.compact_state_max_chars)),
        )
        self._register_default_tools()
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)

    async def _maybe_consolidate_and_sync_compact_state(self, session: Session) -> bool:
        completed = await self.memory_consolidator.maybe_consolidate_by_tokens(session)
        if not completed or not self.memory_config.compact_state_enabled:
            return completed
        synced = await self.compact_state.sync_session(session)
        if synced:
            self.sessions.save(session)
        return synced

    def _get_session_compact_state(self, session: Session) -> str | None:
        if not self.memory_config.compact_state_enabled:
            return None
        compact_state = self.compact_state.get_state(session)
        return compact_state.strip() or None

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    path_append=self.exec_config.path_append,
                )
            )
        self.tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name == "message":
                        tool.set_context(channel, chat_id, message_id)
                    elif name == "spawn":
                        tool.set_context(channel, chat_id, metadata)
                    else:
                        tool.set_context(channel, chat_id)

    @staticmethod
    def _read_complete_line(text: str, start: int) -> tuple[str | None, int]:
        end = text.find("\n", start)
        if end == -1:
            return None, start
        return text[start : end + 1], end + 1

    @classmethod
    def _consume_exact_line(
        cls,
        text: str,
        start: int,
        expected: str,
    ) -> tuple[bool, bool, int]:
        line, next_idx = cls._read_complete_line(text, start)
        if line is None:
            fragment = text[start:]
            if not fragment or expected.startswith(fragment):
                return False, True, start
            return False, False, start
        if line == expected:
            return True, False, next_idx
        return False, False, start

    @classmethod
    def _consume_prefixed_line(
        cls,
        text: str,
        start: int,
        prefix: str,
    ) -> tuple[bool, bool, int]:
        line, next_idx = cls._read_complete_line(text, start)
        if line is None:
            fragment = text[start:]
            if not fragment or prefix.startswith(fragment) or fragment.startswith(prefix):
                return False, True, start
            return False, False, start
        if line.startswith(prefix):
            return True, False, next_idx
        return False, False, start

    @classmethod
    def _consume_optional_prefixed_line(
        cls,
        text: str,
        start: int,
        prefix: str,
    ) -> tuple[bool, bool, int]:
        consumed, pending, next_idx = cls._consume_prefixed_line(text, start, prefix)
        if consumed or pending:
            return consumed, pending, next_idx
        return False, False, start

    @classmethod
    def _maybe_consume_runtime_context_prefix(
        cls,
        text: str,
    ) -> tuple[str | None, bool] | None:
        """If *text* starts with a runtime metadata echo, strip it.

        Returns:
        - ``(remainder, False)`` when a full runtime prefix was consumed
        - ``(None, True)`` when the prefix is still incomplete and should be held
        - ``None`` when the text does not look like a runtime prefix
        """
        cleaned = text.lstrip()
        tag = ContextBuilder._RUNTIME_CONTEXT_TAG

        def _consume_after_current_time(start: int) -> tuple[str | None, bool] | None:
            idx = start
            consumed_runtime_metadata = False

            consumed, pending, next_idx = cls._consume_optional_prefixed_line(
                cleaned, idx, "Channel: "
            )
            if pending:
                return None, True
            if consumed:
                idx = next_idx

            consumed, pending, next_idx = cls._consume_optional_prefixed_line(
                cleaned, idx, "Chat ID: "
            )
            if pending:
                return None, True
            if consumed:
                idx = next_idx

            consumed, pending, next_idx = cls._consume_optional_prefixed_line(
                cleaned, idx, "Runtime Metadata:\n"
            )
            if pending:
                return None, True
            if consumed:
                consumed_runtime_metadata = True
                idx = next_idx
                while True:
                    line, next_idx = cls._read_complete_line(cleaned, idx)
                    if line is None:
                        fragment = cleaned[idx:]
                        if not fragment or not fragment.startswith("\n"):
                            return None, True
                        return None
                    if line == "\n":
                        idx = next_idx
                        break
                    if line.startswith(" ") or (":" in line and not line.startswith("- ")):
                        idx = next_idx
                        continue
                    return None

            if consumed_runtime_metadata:
                return cleaned[idx:], False

            if idx >= len(cleaned):
                return None, True
            if cleaned[idx] != "\n":
                return None
            while idx < len(cleaned) and cleaned[idx] == "\n":
                idx += 1
            return cleaned[idx:], False

        if tag.startswith(cleaned) and not cleaned.startswith(tag):
            return None, True
        if cleaned.startswith(tag):
            idx = len(tag)
            if idx == len(cleaned):
                return None, True
            if cleaned[idx] != "\n":
                return None
            idx += 1

            consumed, pending, next_idx = cls._consume_exact_line(cleaned, idx, "Rules:\n")
            if pending:
                return None, True
            if consumed:
                idx = next_idx
                saw_bullet = False
                while True:
                    line, next_idx = cls._read_complete_line(cleaned, idx)
                    if line is None:
                        fragment = cleaned[idx:]
                        if not fragment or fragment.startswith("-") or "- ".startswith(fragment):
                            return None, True
                        return None
                    if line == "\n":
                        if not saw_bullet:
                            return None
                        idx = next_idx
                        break
                    if not line.startswith("- "):
                        return None
                    saw_bullet = True
                    idx = next_idx

            consumed, pending, next_idx = cls._consume_prefixed_line(cleaned, idx, "Current Time: ")
            if pending:
                return None, True
            if not consumed:
                return None
            return _consume_after_current_time(next_idx)

        if cls._LEGACY_RUNTIME_CONTEXT_PREFIX.startswith(cleaned) and not cleaned.startswith(
            cls._LEGACY_RUNTIME_CONTEXT_PREFIX
        ):
            return None, True
        consumed, pending, next_idx = cls._consume_prefixed_line(cleaned, 0, "Current Time: ")
        if pending:
            return None, True
        if consumed:
            return _consume_after_current_time(next_idx)
        return None

    @classmethod
    def _strip_runtime_context_echo(cls, text: str | None) -> str | None:
        """Remove leaked runtime metadata blocks if the model echoes them back."""
        if not text:
            return None
        cleaned = text.lstrip()
        parsed = cls._maybe_consume_runtime_context_prefix(cleaned)
        if parsed is not None:
            remainder, pending = parsed
            if not pending:
                return remainder or None
        for pattern in (cls._RUNTIME_CONTEXT_ECHO_RE, cls._LEGACY_RUNTIME_CONTEXT_ECHO_RE):
            updated = pattern.sub("", cleaned, count=1)
            if updated != cleaned:
                cleaned = updated.lstrip()
        return cleaned or None

    @classmethod
    def _sanitize_visible_output(cls, text: str | None, *, streaming: bool = False) -> str | None:
        """Remove internal-only visible noise like think blocks and runtime metadata echoes."""
        cleaned = cls._strip_think(text)
        if not cleaned:
            return None
        parsed = cls._maybe_consume_runtime_context_prefix(cleaned)
        if parsed is not None:
            remainder, pending = parsed
            if pending:
                return None if streaming else cleaned
            return remainder or None
        return cleaned or None

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from nanobot.utils.helpers import strip_think

        return strip_think(text) or None

    @staticmethod
    def _format_retry_progress(error: str | None, attempt: int, max_retries: int) -> str:
        lowered = (error or "").lower()
        if "timeout" in lowered or "timed out" in lowered:
            return f"模型响应超时，正在自动重试（{attempt}/{max_retries}）…"
        return f"模型服务临时异常，正在自动重试（{attempt}/{max_retries}）…"

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""

        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'

        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.
        """
        loop_self = self

        class _LoopHook(AgentHook):
            def __init__(self) -> None:
                self._stream_buf = ""

            def wants_streaming(self) -> bool:
                return on_stream is not None

            async def on_stream(self, context: AgentHookContext, delta: str) -> None:
                prev_clean = (
                    loop_self._sanitize_visible_output(self._stream_buf, streaming=True) or ""
                )
                self._stream_buf += delta
                new_clean = (
                    loop_self._sanitize_visible_output(self._stream_buf, streaming=True) or ""
                )
                incremental = new_clean[len(prev_clean) :]
                if incremental and on_stream:
                    await on_stream(incremental)

            async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
                if on_stream_end:
                    await on_stream_end(resuming=resuming)
                self._stream_buf = ""

            async def on_retry(
                self,
                context: AgentHookContext,
                *,
                attempt: int,
                max_retries: int,
                delay: float,
                error: str | None,
            ) -> None:
                if on_progress:
                    await on_progress(loop_self._format_retry_progress(error, attempt, max_retries))

            async def before_execute_tools(self, context: AgentHookContext) -> None:
                if on_progress:
                    if not on_stream:
                        thought = loop_self._sanitize_visible_output(
                            context.response.content if context.response else None
                        )
                        if thought:
                            await on_progress(thought)
                    tool_hint = loop_self._strip_think(loop_self._tool_hint(context.tool_calls))
                    await on_progress(tool_hint, tool_hint=True)
                for tc in context.tool_calls:
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tc.name, args_str[:200])
                loop_self._set_tool_context(channel, chat_id, message_id)

            def finalize_content(
                self, context: AgentHookContext, content: str | None
            ) -> str | None:
                return loop_self._sanitize_visible_output(content)

        loop_hook: AgentHook = _LoopHook()
        if self._extra_hooks:
            loop_hook = _LoopHookChain(loop_hook, self._extra_hooks)

        result = await self.runner.run(
            AgentRunSpec(
                initial_messages=initial_messages,
                tools=self.tools,
                model=self.model,
                max_iterations=self.max_iterations,
                hook=loop_hook,
                error_message="Sorry, I encountered an error calling the AI model.",
                concurrent_tools=not should_disable_concurrent_tools(self.workspace),
            )
        )
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
        elif result.stop_reason == "error":
            logger.error(
                "LLM returned error: {}", ((result.error or result.final_content) or "")[:200]
            )
        return result.final_content, result.tools_used, result.messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=self)
                result = await self.commands.dispatch_priority(ctx)
                if result:
                    await self.bus.publish_outbound(result)
                continue
            if fastlane := await try_workspace_fastlane(msg, raw):
                await self.bus.publish_outbound(fastlane)
                continue
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(
                lambda t, k=msg.session_key: (
                    self._active_tasks.get(k, []) and self._active_tasks[k].remove(t)
                    if t in self._active_tasks.get(k, [])
                    else None
                )
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()
        async with lock, gate:
            try:
                on_stream = on_stream_end = None
                stream_base_id: str | None = None
                stream_segment = 0
                stream_end_sent = False
                stream_started_at: float | None = None
                stream_finished_at: float | None = None
                stream_chunk_count = 0
                stream_char_count = 0
                segment_stream_chunk_count = 0
                segment_stream_char_count = 0

                async def _emit_pending_stream_end() -> None:
                    nonlocal stream_end_sent, stream_finished_at
                    if stream_base_id is None or stream_end_sent:
                        return
                    if stream_started_at is not None:
                        stream_finished_at = time.monotonic()
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            metadata={
                                "_stream_end": True,
                                "_resuming": False,
                                "_stream_id": f"{stream_base_id}:{stream_segment}",
                            },
                        )
                    )
                    stream_end_sent = True

                if msg.metadata.get("_wants_stream"):
                    # Split one answer into distinct stream segments.
                    stream_base_id = f"{msg.session_key}:{time.time_ns()}"

                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            metadata={
                                "_stream_start": True,
                                "_stream_id": f"{stream_base_id}:0",
                            },
                        )
                    )

                    def _current_stream_id() -> str:
                        return f"{stream_base_id}:{stream_segment}"

                    async def on_stream(delta: str) -> None:
                        nonlocal \
                            stream_started_at, \
                            stream_finished_at, \
                            stream_chunk_count, \
                            stream_char_count, \
                            segment_stream_chunk_count, \
                            segment_stream_char_count
                        if delta and delta.strip():
                            now = time.monotonic()
                            if stream_started_at is None:
                                stream_started_at = now
                            stream_finished_at = now
                            stream_chunk_count += 1
                            stream_char_count += len(delta)
                            segment_stream_chunk_count += 1
                            segment_stream_char_count += len(delta)
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=delta,
                                metadata={
                                    "_stream_delta": True,
                                    "_stream_id": _current_stream_id(),
                                },
                            )
                        )

                    async def on_stream_end(*, resuming: bool = False) -> None:
                        nonlocal \
                            stream_segment, \
                            stream_end_sent, \
                            stream_finished_at, \
                            segment_stream_chunk_count, \
                            segment_stream_char_count
                        if stream_started_at is not None:
                            stream_finished_at = time.monotonic()
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata={
                                    "_stream_end": True,
                                    "_resuming": resuming,
                                    "_stream_id": _current_stream_id(),
                                },
                            )
                        )
                        stream_end_sent = True
                        if resuming:
                            segment_stream_chunk_count = 0
                            segment_stream_char_count = 0
                            stream_end_sent = False
                        stream_segment += 1

                response = await self._process_message(
                    msg,
                    on_stream=on_stream,
                    on_stream_end=on_stream_end,
                )
                if response is not None:
                    await _emit_pending_stream_end()
                    separate_completion_notice: OutboundMessage | None = None
                    final_segment_silent = on_stream is not None and (
                        segment_stream_chunk_count <= 0 or segment_stream_char_count <= 0
                    )
                    if final_segment_silent:
                        meta = dict(response.metadata or {})
                        meta.pop("_streamed", None)
                        response.metadata = meta
                        separate_completion_notice = OutboundMessage(
                            channel=response.channel,
                            chat_id=response.chat_id,
                            content="",
                            metadata={**dict(response.metadata or {}), "_streamed": True},
                        )
                        self._maybe_mark_stream_completion_notice(
                            msg,
                            separate_completion_notice,
                            stream_started_at=stream_started_at,
                            stream_finished_at=stream_finished_at,
                            stream_chunk_count=stream_chunk_count,
                            stream_char_count=stream_char_count,
                        )
                        if not (separate_completion_notice.metadata or {}).get("_completion_notice"):
                            separate_completion_notice = None
                    else:
                        self._maybe_mark_stream_completion_notice(
                            msg,
                            response,
                            stream_started_at=stream_started_at,
                            stream_finished_at=stream_finished_at,
                            stream_chunk_count=stream_chunk_count,
                            stream_char_count=stream_char_count,
                        )
                    await self.bus.publish_outbound(response)
                    if separate_completion_notice is not None:
                        await self.bus.publish_outbound(separate_completion_notice)
                elif msg.channel == "cli":
                    await _emit_pending_stream_end()
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            metadata=msg.metadata or {},
                        )
                    )
            except asyncio.CancelledError:
                await _emit_pending_stream_end()
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                self._discard_inflight_turn(msg.session_key)
                logger.exception("Error processing message for session {}", msg.session_key)
                await _emit_pending_stream_end()
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    )
                )

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    async def _run_pre_reply_consolidation(self, session: Session) -> bool:
        """Best-effort pre-reply consolidation. Returns True if it completed cleanly."""
        if not self.memory_config.enabled:
            return True
        timeout = max(0.1, float(self.memory_config.pre_reply_timeout_seconds))
        try:
            logger.debug(
                "Pre-reply consolidation start {} timeout={} archive_model={}",
                session.key,
                timeout,
                self.archive_model,
            )
            completed = await asyncio.wait_for(
                self._maybe_consolidate_and_sync_compact_state(session),
                timeout=timeout,
            )
            return completed
        except asyncio.TimeoutError:
            logger.warning(
                "Pre-reply consolidation timed out for {} after {}s", session.key, timeout
            )
            try:
                await self.memory_consolidator.handle_timeout(session, phase="pre-reply")
            except Exception:
                logger.exception(
                    "Failed to record pre-reply consolidation timeout for {}", session.key
                )
            return False
        except Exception:
            logger.exception("Pre-reply consolidation failed for {}", session.key)
            return False

    def _should_run_pre_reply_consolidation(
        self,
        session: Session,
        *,
        msg: InboundMessage | None = None,
    ) -> tuple[bool, str]:
        if not self.memory_config.enabled:
            return False, "memory_disabled"
        if msg is not None and bool((msg.metadata or {}).get("workspace_harness_auto")):
            return False, self._PRE_REPLY_CONSOLIDATION_SKIP_REASON_HARNESS_AUTO
        over_budget, _estimated, _source = self.memory_consolidator.is_over_budget(session)
        if not over_budget:
            return False, self._PRE_REPLY_CONSOLIDATION_SKIP_REASON_UNDER_BUDGET
        return True, "over_budget"

    async def _maybe_run_pre_reply_consolidation(
        self,
        session: Session,
        *,
        msg: InboundMessage | None = None,
    ) -> bool:
        should_run, reason = self._should_run_pre_reply_consolidation(session, msg=msg)
        if not should_run:
            logger.debug("Pre-reply consolidation skipped for {}: {}", session.key, reason)
            return True
        return await self._run_pre_reply_consolidation(session)

    async def _run_background_consolidation(self, session: Session) -> None:
        """Best-effort background consolidation with a looser timeout."""
        if not self.memory_config.enabled:
            return
        timeout = max(0.1, float(self.memory_config.background_timeout_seconds))
        try:
            completed = await asyncio.wait_for(
                self._maybe_consolidate_and_sync_compact_state(session),
                timeout=timeout,
            )
            if not completed:
                logger.warning(
                    "Background consolidation did not complete cleanly for {}", session.key
                )
        except asyncio.TimeoutError:
            logger.warning(
                "Background consolidation timed out for {} after {}s", session.key, timeout
            )
            try:
                await self.memory_consolidator.handle_timeout(session, phase="background")
            except Exception:
                logger.exception(
                    "Failed to record background consolidation timeout for {}", session.key
                )
        except Exception:
            logger.exception("Background consolidation failed for {}", session.key)

    def _select_history_for_reply(
        self,
        session: Session,
        *,
        preflight_ok: bool,
    ) -> list[dict[str, Any]]:
        """Choose full vs recent-window history for the main reply."""
        history = session.get_history(max_messages=0)
        if not self.memory_config.enabled:
            return history

        if preflight_ok:
            return history

        fallback_max = max(1, int(self.memory_config.recent_history_fallback_messages))
        over_budget, estimated, source = self.memory_consolidator.is_over_budget(session)
        if not over_budget:
            return history

        fallback_history = session.get_history(max_messages=fallback_max)
        logger.warning(
            "Recent-history fallback active for {}: estimated={} via {}, max_messages={}",
            session.key,
            estimated,
            source,
            fallback_max,
        )
        return fallback_history

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    def _register_inflight_turn(self, session_key: str, *, role: str, content: str) -> None:
        self._inflight_turns[session_key] = {
            "role": role,
            "content": content,
            "assistant_partial": "",
        }

    def _append_inflight_assistant_delta(self, session_key: str, delta: str) -> None:
        if not delta:
            return
        turn = self._inflight_turns.get(session_key)
        if not isinstance(turn, dict):
            return
        turn["assistant_partial"] = str(turn.get("assistant_partial") or "") + delta

    def _discard_inflight_turn(self, session_key: str) -> None:
        self._inflight_turns.pop(session_key, None)

    def _pop_inflight_turn_snapshot(self, session_key: str) -> dict[str, str] | None:
        turn = self._inflight_turns.pop(session_key, None)
        if not isinstance(turn, dict):
            return None
        return {
            "role": str(turn.get("role") or "user"),
            "content": str(turn.get("content") or "").strip(),
            "assistant_partial": str(turn.get("assistant_partial") or "").strip(),
        }

    def persist_interrupted_turn(self, session: Session, session_key: str) -> dict[str, str] | None:
        snapshot = self._pop_inflight_turn_snapshot(session_key)
        if snapshot is None:
            return None

        entries: list[dict[str, Any]] = []
        if snapshot["content"]:
            entries.append({"role": snapshot["role"], "content": snapshot["content"]})
        if snapshot["assistant_partial"]:
            entries.append({"role": "assistant", "content": snapshot["assistant_partial"]})
        if entries:
            self._save_turn(session, entries, skip=0)
        return snapshot

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            await self._maybe_run_pre_reply_consolidation(session, msg=msg)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"), msg.metadata)
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                current_role=current_role,
                workspace_work_mode=(msg.metadata or {}).get("workspace_work_mode"),
                compact_state=self._get_session_compact_state(session),
                runtime_metadata=self._extract_runtime_metadata(msg),
            )
            final_content, _, all_msgs = await self._run_agent_loop(
                messages,
                channel=channel,
                chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
            )
            final_content = self._postprocess_workspace_agent_output(msg, final_content or "")
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            self._schedule_background(self._run_background_consolidation(session))
            if self._should_schedule_harness_auto_continue(msg):
                await self._schedule_harness_auto_continue(msg)
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
                metadata=dict(msg.metadata or {}),
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        if (interrupt_state := session.metadata.pop("interrupt_state", None)) is not None:
            self._redirect_workspace_harness_if_interrupted(
                msg.content,
                interrupt_state=interrupt_state if isinstance(interrupt_state, dict) else None,
            )
            self.sessions.save(session)

        preflight_ok = await self._maybe_run_pre_reply_consolidation(session, msg=msg)

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        workspace_cmd = (msg.metadata or {}).get("workspace_agent_cmd")
        if workspace_cmd and on_progress is None:
            progress_map = {
                "plan": "正在进入规划讨论…",
                "plan-exec": "正在执行当前任务步骤…",
                "diagnose": "正在诊断问题…",
                "诊断": "正在诊断问题…",
                "simplify": "正在生成简化方案…",
                "小结": "正在生成小结…",
                "感悟": "正在整理感悟…",
                "笔记": "正在整理笔记草稿…",
                "sync": "正在同步与消化内容…",
                "merge": "正在准备合并流程…",
                "harness": "正在初始化 harness 并推进目标…",
                "weather_brief": "正在生成天气早报…",
            }
            hint = progress_map.get(workspace_cmd, f"正在处理 /{workspace_cmd} …")
            await _bus_progress(hint)

        tool_metadata = dict(msg.metadata or {})
        computed_runtime = self._extract_runtime_metadata(msg)
        if computed_runtime:
            tool_metadata["workspace_runtime"] = computed_runtime
        self._set_tool_context(
            msg.channel, msg.chat_id, msg.metadata.get("message_id"), tool_metadata
        )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = self._select_history_for_reply(session, preflight_ok=preflight_ok)
        prepared_agent_input = (msg.metadata or {}).get("workspace_agent_input")
        current_message = (
            prepared_agent_input
            if isinstance(prepared_agent_input, str) and prepared_agent_input.strip()
            else msg.content
        )
        persisted_role = "assistant" if msg.sender_id == "subagent" else "user"
        persisted_turn_content = msg.content
        self._register_inflight_turn(
            msg.session_key, role=persisted_role, content=persisted_turn_content
        )
        effective_on_stream = on_stream
        if on_stream is not None:

            async def _capture_stream(delta: str) -> None:
                self._append_inflight_assistant_delta(msg.session_key, delta)
                await on_stream(delta)

            effective_on_stream = _capture_stream
        initial_messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            workspace_work_mode=(msg.metadata or {}).get("workspace_work_mode"),
            compact_state=self._get_session_compact_state(session),
            runtime_metadata=self._extract_runtime_metadata(msg),
        )
        persisted_current_content = None
        if current_message != msg.content:
            persisted_messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=msg.chat_id,
                workspace_work_mode=(msg.metadata or {}).get("workspace_work_mode"),
                compact_state=self._get_session_compact_state(session),
                runtime_metadata=self._extract_runtime_metadata(msg),
            )
            if persisted_messages:
                persisted_current_content = persisted_messages[-1].get("content")

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=effective_on_stream,
            on_stream_end=on_stream_end,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
        )

        final_content = self._postprocess_workspace_agent_output(msg, final_content or "")

        if final_content is None:
            final_content = AgentRunner._user_facing_error_message(
                "empty model response",
                retry_count=0,
                fallback="模型返回了空响应。请稍后重试，或切换模型。",
            )

        if persisted_current_content is not None:
            current_turn_user_idx = 1 + len(history)
            if (
                current_turn_user_idx < len(all_msgs)
                and isinstance(all_msgs[current_turn_user_idx], dict)
                and all_msgs[current_turn_user_idx].get("role") == "user"
            ):
                all_msgs[current_turn_user_idx] = {
                    **all_msgs[current_turn_user_idx],
                    "content": persisted_current_content,
                }

        self._save_turn(session, all_msgs, 1 + len(history))
        self._discard_inflight_turn(msg.session_key)
        self.sessions.save(session)
        self._schedule_background(self._run_background_consolidation(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        if self._should_schedule_harness_auto_continue(msg):
            await self._schedule_harness_auto_continue(msg)

        meta = dict(msg.metadata or {})
        if on_stream is not None:
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )

    def _postprocess_workspace_agent_output(self, msg: InboundMessage, final_content: str) -> str:
        """Run workspace router postprocess hook for agent-routed slash commands."""
        cmd = (msg.metadata or {}).get("workspace_agent_cmd")
        if not cmd:
            return final_content

        if cmd == "harness":
            result = HarnessService.for_workspace(self.workspace).apply_agent_update(
                final_content,
                session_key=msg.session_key,
                harness_id=str((msg.metadata or {}).get("workspace_harness_id") or "").strip(),
            )
            return result.final_content

        router_path = Path.home() / ".nanobot" / "workspace" / "scripts" / "router.py"
        if not router_path.exists():
            return final_content

        try:
            result = subprocess.run(
                [str(router_path), "--postprocess-agent", cmd],
                input=final_content,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10,
            )
        except Exception:
            return final_content

        processed = (result.stdout or "").strip()
        return processed or final_content

    @staticmethod
    def _image_placeholder(block: dict[str, Any]) -> dict[str, str]:
        """Convert an inline image block into a compact text placeholder."""
        path = (block.get("_meta") or {}).get("path", "")
        return {"type": "text", "text": f"[image: {path}]" if path else "[image]"}

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                filtered.append(self._image_placeholder(block))
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if truncate_text and len(text) > self._TOOL_RESULT_MAX_CHARS:
                    text = text[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(
                    ContextBuilder._RUNTIME_CONTEXT_TAG
                ):
                    # Strip the full runtime-context block and keep only the raw user text.
                    stripped = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG) :].lstrip("\n")
                    parts = stripped.split("\n\n", 2)
                    if len(parts) > 2 and parts[2].strip():
                        entry["content"] = parts[2]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=dict(metadata or {}),
        )
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
