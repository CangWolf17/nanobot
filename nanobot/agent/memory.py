"""Memory system for persistent agent memory."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from nanobot.config.paths import get_workspace_memory_tracked_files
from nanobot.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain

if TYPE_CHECKING:
    from nanobot.agent.runner import AgentRunSpec
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session, SessionManager


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


def _ensure_text(value: Any) -> str:
    """Normalize tool-call payload values to text for file storage."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _normalize_save_memory_args(args: Any) -> dict[str, Any] | None:
    """Normalize provider tool-call arguments to the expected dict shape."""
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None


_TOOL_CHOICE_ERROR_MARKERS = (
    "tool_choice",
    "toolchoice",
    "does not support",
    'should be ["none", "auto"]',
)


def _is_tool_choice_unsupported(content: str | None) -> bool:
    """Detect provider errors caused by forced tool_choice being unsupported."""
    text = (content or "").lower()
    return any(m in text for m in _TOOL_CHOICE_ERROR_MARKERS)


class MemoryStore:
    """Two-layer memory with a JSONL archive plus Dream-managed durable files."""

    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3
    _LEGACY_ENTRY_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*(.*)$")
    _RAW_CONTINUATION_PREFIXES = ("USER:", "ASSISTANT:", "TOOL:", "SYSTEM:")

    def __init__(self, workspace: Path, max_history_entries: int = 200):
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.soul_file = workspace / "SOUL.md"
        self._tracked_files = tuple(get_workspace_memory_tracked_files(workspace))
        self._user_enabled = "USER.md" in self._tracked_files
        self.user_file = workspace / "USER.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.legacy_history_file = self.memory_dir / "HISTORY.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self.max_history_entries = max_history_entries
        self._consecutive_failures = 0
        self._maybe_migrate_legacy_history()

    @staticmethod
    def read_file(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def write_file(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        self.write_file(self.memory_file, content)

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.write_file(self.soul_file, content)

    def read_user(self) -> str:
        if not self._user_enabled:
            return ""
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        if not self._user_enabled:
            return
        self.write_file(self.user_file, content)

    def read_long_term(self) -> str:
        return self.read_memory()

    def write_long_term(self, content: str) -> None:
        self.write_memory(content)

    def append_history(self, entry: Any) -> int:
        cursor = self._next_cursor()
        record = self._normalize_history_record(entry, cursor=cursor)
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.write_file(self._cursor_file, str(cursor))
        return cursor

    def read_unprocessed_history(self, since_cursor: int = 0) -> list[dict[str, Any]]:
        entries = self._load_history_entries()
        return [entry for entry in entries if int(entry.get("cursor", 0) or 0) > since_cursor]

    def compact_history(self) -> None:
        if self.max_history_entries <= 0:
            return
        entries = self._load_history_entries()
        if len(entries) <= self.max_history_entries:
            return
        kept = entries[-self.max_history_entries :]
        with open(self.history_file, "w", encoding="utf-8") as f:
            for entry in kept:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if kept:
            self.write_file(self._cursor_file, str(int(kept[-1].get("cursor", 0) or 0)))

    def get_last_dream_cursor(self) -> int:
        raw = self.read_file(self._dream_cursor_file).strip()
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.write_file(self._dream_cursor_file, str(max(0, int(cursor))))

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = (
                f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            )
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[dict],
        provider: LLMProvider,
        model: str,
    ) -> bool:
        """Consolidate the provided message chunk into MEMORY.md + history.jsonl."""
        if not messages:
            return True

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

If the conversation contains runtime context or metadata blocks, treat them as auxiliary information and ignore them unless they materially change the user's real task or durable facts.
Do not quote or reproduce such blocks in `history_entry` or `memory_update`.
At the end of both `history_entry` and `memory_update`, include the note: `Runtime context is auxiliary metadata and may be unrelated to the actual problem.`

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{self._format_messages(messages)}"""

        chat_messages = [
            {
                "role": "system",
                "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation. Do not copy or surface runtime context / metadata blocks into the saved memory or history entry.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            forced = {"type": "function", "function": {"name": "save_memory"}}
            response = await provider.chat_with_retry(
                messages=chat_messages,
                tools=_SAVE_MEMORY_TOOL,
                model=model,
                tool_choice=forced,
            )

            if response.finish_reason == "error" and _is_tool_choice_unsupported(response.content):
                logger.warning("Forced tool_choice unsupported, retrying with auto")
                response = await provider.chat_with_retry(
                    messages=chat_messages,
                    tools=_SAVE_MEMORY_TOOL,
                    model=model,
                    tool_choice="auto",
                )

            if not response.has_tool_calls:
                logger.warning(
                    "Memory consolidation: LLM did not call save_memory "
                    "(finish_reason={}, content_len={}, content_preview={})",
                    response.finish_reason,
                    len(response.content or ""),
                    (response.content or "")[:200],
                )
                return self._fail_or_raw_archive(messages)

            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None:
                logger.warning("Memory consolidation: unexpected save_memory arguments")
                return self._fail_or_raw_archive(messages)

            if "history_entry" not in args or "memory_update" not in args:
                logger.warning("Memory consolidation: save_memory payload missing required fields")
                return self._fail_or_raw_archive(messages)

            entry = args["history_entry"]
            update = args["memory_update"]

            if entry is None or update is None:
                logger.warning(
                    "Memory consolidation: save_memory payload contains null required fields"
                )
                return self._fail_or_raw_archive(messages)

            normalized_entry = (
                _ensure_text(entry).strip()
                if not isinstance(entry, dict)
                else entry
            )
            if (isinstance(normalized_entry, str) and not normalized_entry) or normalized_entry == {}:
                logger.warning("Memory consolidation: history_entry is empty after normalization")
                return self._fail_or_raw_archive(messages)

            self.append_history(normalized_entry)
            update = _ensure_text(update)
            if update != current_memory:
                self.write_long_term(update)

            self._consecutive_failures = 0
            logger.info("Memory consolidation done for {} messages", len(messages))
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return self._fail_or_raw_archive(messages)

    def _fail_or_raw_archive(self, messages: list[dict]) -> bool:
        """Increment failure count; after threshold, raw-archive messages and return True."""
        self._consecutive_failures += 1
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list[dict]) -> None:
        """Fallback: dump raw messages to the canonical archive without summarization."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.append_history(
            f"[{ts}] [RAW] {len(messages)} messages\n{self._format_messages(messages)}"
        )
        logger.warning("Memory consolidation degraded: raw-archived {} messages", len(messages))

    def _load_history_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not self.history_file.exists():
            return entries

        for idx, line in enumerate(self.read_file(self.history_file).splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed history entry in {}", self.history_file)
                continue
            if not isinstance(parsed, dict):
                continue
            cursor = parsed.get("cursor")
            try:
                parsed["cursor"] = int(cursor) if cursor is not None else idx
            except (TypeError, ValueError):
                parsed["cursor"] = idx
            entries.append(parsed)
        return entries

    def _next_cursor(self) -> int:
        raw = self.read_file(self._cursor_file).strip()
        if raw:
            try:
                return int(raw) + 1
            except ValueError:
                pass
        entries = self._load_history_entries()
        if not entries:
            return 1
        return max(int(entry.get("cursor", 0) or 0) for entry in entries) + 1

    def _history_exists_and_nonempty(self) -> bool:
        return self.history_file.exists() and bool(self.read_file(self.history_file).strip())

    def _maybe_migrate_legacy_history(self) -> None:
        if not self.legacy_history_file.exists():
            return
        if self._history_exists_and_nonempty():
            return

        legacy_bytes = self.legacy_history_file.read_bytes()
        legacy_text = legacy_bytes.decode("utf-8", errors="replace")
        backup = self.memory_dir / "HISTORY.md.bak"
        backup.write_text(legacy_text, encoding="utf-8")
        fallback_timestamp = datetime.fromtimestamp(backup.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M"
        )

        entries = self._parse_legacy_history(legacy_text, fallback_timestamp)
        if entries:
            with open(self.history_file, "w", encoding="utf-8") as f:
                for cursor, entry in enumerate(entries, start=1):
                    record = self._normalize_history_record(entry, cursor=cursor)
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.write_file(self._cursor_file, str(len(entries)))
            self.write_file(self._dream_cursor_file, str(len(entries)))

        self.legacy_history_file.unlink()

    @classmethod
    def _parse_legacy_history(
        cls,
        text: str,
        fallback_timestamp: str,
    ) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        current: dict[str, Any] | None = None

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            content = "\n".join(current["lines"]).strip()
            if content:
                entries.append(
                    {
                        "timestamp": current["timestamp"],
                        "content": content,
                    }
                )
            current = None

        def start(timestamp: str, content: str, *, raw: bool) -> None:
            nonlocal current
            current = {"timestamp": timestamp, "lines": [content] if content else [], "raw": raw}

        for raw_line in text.splitlines():
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                flush()
                continue

            match = cls._LEGACY_ENTRY_RE.match(stripped)
            if match:
                timestamp, remainder = match.groups()
                remainder = remainder.strip()
                if current is not None and current["raw"] and remainder.startswith(
                    cls._RAW_CONTINUATION_PREFIXES
                ):
                    current["lines"].append(stripped)
                    continue
                flush()
                start(timestamp, remainder, raw=remainder.startswith("[RAW]"))
                continue

            if stripped.startswith("[") and current is not None and not current["raw"]:
                flush()
                start(fallback_timestamp, stripped, raw=False)
                continue

            if current is None:
                start(fallback_timestamp, stripped, raw=False)
                continue

            current["lines"].append(stripped)

        flush()
        return entries

    @staticmethod
    def _normalize_history_record(entry: Any, *, cursor: int) -> dict[str, Any]:
        record: dict[str, Any] = {"cursor": cursor}
        if isinstance(entry, dict):
            record.update(entry)
            record.setdefault("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
            if "content" not in record and isinstance(record.get("summary"), str):
                record["content"] = record["summary"]
            return record

        record["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        record["content"] = _ensure_text(entry).strip()
        return record


class Dream:
    """Second-stage durable memory governor."""

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        *,
        max_batch_size: int = 20,
        max_iterations: int = 10,
    ) -> None:
        from nanobot.agent.runner import AgentRunner
        from nanobot.agent.skills import BUILTIN_SKILLS_DIR
        from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.utils.gitstore import GitStore

        self.store = store
        self.provider = provider
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_iterations = max_iterations
        self._tracked_files = tuple(get_workspace_memory_tracked_files(self.store.workspace))
        self._skill_creator_path = BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"
        self._runner = AgentRunner(provider)
        self._tools = ToolRegistry()
        self._tools.register(
            ReadFileTool(
                workspace=self.store.workspace,
                allowed_dir=self.store.workspace,
                extra_allowed_dirs=[BUILTIN_SKILLS_DIR],
            )
        )
        self._tools.register(
            EditFileTool(
                workspace=self.store.workspace,
                allowed_dir=self.store.workspace,
            )
        )
        self._tools.register(
            WriteFileTool(
                workspace=self.store.workspace,
                allowed_dir=self.store.workspace,
            )
        )
        self.git = getattr(store, "git", None) or GitStore(self.store.workspace, self._tracked_files)
        self.store.git = self.git

    async def run(self) -> bool:
        """Process unread archive entries into governed durable memory updates."""
        pending = self.store.read_unprocessed_history(
            since_cursor=self.store.get_last_dream_cursor()
        )[: self.max_batch_size]
        if not pending:
            return False

        analysis = await self._analyze_pending_entries(pending)
        spec = self._build_run_spec(pending, analysis)
        result = await self._runner.run(spec)
        last_cursor = max(int(entry.get("cursor", 0) or 0) for entry in pending)
        self.store.set_last_dream_cursor(last_cursor)
        self.store.compact_history()
        self._maybe_commit_memory_changes(result.tool_events)
        return True

    async def _analyze_pending_entries(self, pending: list[dict[str, Any]]) -> str:
        prompt = "\n".join(
            f"- [{entry.get('cursor', '?')}] {entry.get('timestamp', '?')}: {entry.get('content', '')}"
            for entry in pending
        )
        response = await self.provider.chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You triage archive entries for durable memory updates. "
                        "Return concise analysis only."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Review these archive entries and identify durable facts, preference changes, "
                        "conflicts, stale items to remove, and repeated workflows worth turning into skills.\n\n"
                        f"{prompt}"
                    ),
                },
            ],
            model=self.model,
        )
        return str(response.content or "").strip()

    def _build_run_spec(
        self,
        pending: list[dict[str, Any]],
        analysis: str,
    ) -> AgentRunSpec:
        from nanobot.agent.runner import AgentRunSpec

        template_path = Path(__file__).resolve().parent.parent / "templates" / "agent" / "dream_phase2.md"
        system_prompt = template_path.read_text(encoding="utf-8").replace(
            "{{ skill_creator_path }}",
            str(self._skill_creator_path),
        )
        archive_lines = "\n".join(
            f"- [{entry.get('cursor', '?')}] {entry.get('timestamp', '?')}: {entry.get('content', '')}"
            for entry in pending
        )
        current_files_sections = [
            "### SOUL.md",
            self.store.read_soul() or "(empty)",
        ]
        user_text = self.store.read_user()
        if user_text:
            current_files_sections.extend(["", "### USER.md", user_text])
        current_files_sections.extend(["", "### memory/MEMORY.md", self.store.read_memory() or "(empty)"])
        user_prompt = f"""## Analysis
{analysis or "(no additional analysis)"}

## Pending Archive Entries
{archive_lines}

## Current Files
{chr(10).join(current_files_sections)}
"""
        return AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=self._tools,
            model=self.model,
            max_iterations=self.max_iterations,
            fail_on_tool_error=False,
        )

    def _maybe_commit_memory_changes(self, tool_events: list[dict[str, str]]) -> None:
        details = [
            str(event.get("detail") or "").strip()
            for event in tool_events
            if str(event.get("status") or "") == "ok"
        ]
        if not any(path in detail for detail in details for path in self._tracked_files):
            return
        if not self.git.is_initialized():
            self.git.init()
        change_count = sum(
            1 for path in self._tracked_files if any(path in detail for detail in details)
        )
        message = f"dream: {datetime.now().strftime('%Y-%m-%d')}, {change_count} change(s)"
        self.git.auto_commit(message)


class MemoryConsolidator:
    """Owns consolidation policy, locking, and session offset updates."""

    _MAX_CONSOLIDATION_ROUNDS = 5

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        get_compact_state: Callable[[Session], str | None] | None = None,
        max_completion_tokens: int = 4096,
        archive_provider: LLMProvider | None = None,
        archive_model: str | None = None,
    ):
        self.store = MemoryStore(workspace)
        self.provider = provider
        self.model = model
        self.archive_provider = archive_provider or provider
        self.archive_model = archive_model or model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._get_compact_state = get_compact_state
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive a selected message chunk into persistent memory."""
        return await self.store.consolidate(messages, self.archive_provider, self.archive_model)

    async def handle_timeout(self, session: Session, *, phase: str) -> None:
        """Record an outer timeout and degrade to raw archive after repeated hangs."""
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0 or estimated < self.prompt_budget():
                return

            boundary = self.pick_consolidation_boundary(
                session,
                max(1, estimated - self.target_prompt_tokens()),
            )
            if boundary is None:
                return

            end_idx = boundary[0]
            chunk = session.messages[session.last_consolidated : end_idx]
            if not chunk:
                return

            archived = self.store._fail_or_raw_archive(chunk)
            if archived:
                session.last_consolidated = end_idx
                self.sessions.save(session)
                logger.warning(
                    "Memory consolidation timeout degraded {}: phase={}, estimated={}/{} via {}, chunk={} msgs",
                    session.key,
                    phase,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                return

            logger.warning(
                "Memory consolidation timeout recorded {}: phase={}, estimated={}/{} via {}, chunk={} msgs, consecutive_failures={}",
                session.key,
                phase,
                estimated,
                self.context_window_tokens,
                source,
                len(chunk),
                self.store._consecutive_failures,
            )

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(
        self,
        session: Session,
        *,
        max_history_messages: int = 0,
    ) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        history = session.get_history(max_messages=max_history_messages)
        channel, chat_id = session.key.split(":", 1) if ":" in session.key else (None, None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
            compact_state=(
                self._get_compact_state(session)
                if self._get_compact_state is not None
                else session.metadata.get("compact_state")
            ),
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    def prompt_budget(self) -> int:
        """Prompt token budget for the main conversation path."""
        context_window = self.context_window_tokens
        if not isinstance(context_window, int) or isinstance(context_window, bool):
            context_window = 65_536
        max_completion = self.max_completion_tokens
        if not isinstance(max_completion, int) or isinstance(max_completion, bool):
            max_completion = 8_192
        return context_window - max_completion - self._SAFETY_BUFFER

    def target_prompt_tokens(self) -> int:
        """Target prompt size after consolidation."""
        return self.prompt_budget() // 2

    def is_over_budget(
        self, session: Session, *, max_history_messages: int = 0
    ) -> tuple[bool, int, str]:
        """Return whether the main conversation prompt is over the safe budget."""
        estimated, source = self.estimate_session_prompt_tokens(
            session, max_history_messages=max_history_messages
        )
        return estimated >= self.prompt_budget(), estimated, source

    async def archive_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive messages with guaranteed persistence (retries until raw-dump fallback)."""
        if not messages:
            return True
        for _ in range(self.store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def maybe_consolidate_by_tokens(self, session: Session) -> bool:
        """Loop: archive old messages until prompt fits within safe budget.

        Returns True when consolidation completed cleanly (or was unnecessary),
        and False when the pass aborted/failed and caller should treat it as a
        best-effort miss.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if not session.messages or self.context_window_tokens <= 0:
            return True

        lock = self.get_lock(session.key)
        async with lock:
            budget = self.prompt_budget()
            target = self.target_prompt_tokens()
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return True
            if estimated < budget:
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return True

            active_chunk: list[dict[str, object]] | None = None
            active_end_idx: int | None = None
            active_round: int | None = None
            active_estimated = estimated
            active_source = source

            try:
                for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                    if estimated <= target:
                        return True

                    boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                    if boundary is None:
                        logger.debug(
                            "Token consolidation: no safe boundary for {} (round {})",
                            session.key,
                            round_num,
                        )
                        return True

                    end_idx = boundary[0]
                    chunk = session.messages[session.last_consolidated : end_idx]
                    if not chunk:
                        return True

                    active_chunk = chunk
                    active_end_idx = end_idx
                    active_round = round_num
                    active_estimated = estimated
                    active_source = source

                    logger.info(
                        "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs, archive_model={}",
                        round_num,
                        session.key,
                        estimated,
                        self.context_window_tokens,
                        source,
                        len(chunk),
                        self.archive_model,
                    )
                    if not await self.consolidate_messages(chunk):
                        return False
                    session.last_consolidated = end_idx
                    self.sessions.save(session)

                    active_chunk = None
                    active_end_idx = None
                    active_round = None

                    estimated, source = self.estimate_session_prompt_tokens(session)
                    if estimated <= 0:
                        return True
                return True
            except asyncio.CancelledError:
                if active_chunk and active_end_idx is not None:
                    archived = self.store._fail_or_raw_archive(active_chunk)
                    if archived:
                        session.last_consolidated = active_end_idx
                        self.sessions.save(session)
                        logger.warning(
                            "Memory consolidation cancelled/degraded {}: round={}, estimated={}/{} via {}, chunk={} msgs",
                            session.key,
                            active_round,
                            active_estimated,
                            self.context_window_tokens,
                            active_source,
                            len(active_chunk),
                        )
                    else:
                        logger.warning(
                            "Memory consolidation cancelled/recorded {}: round={}, estimated={}/{} via {}, chunk={} msgs, consecutive_failures={}",
                            session.key,
                            active_round,
                            active_estimated,
                            self.context_window_tokens,
                            active_source,
                            len(active_chunk),
                            self.store._consecutive_failures,
                        )
                    return False
                raise
