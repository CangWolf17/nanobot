"""Session-scoped compact resume state for archived history."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from nanobot.agent.memory import _is_tool_choice_unsupported


_SAVE_COMPACT_STATE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_compact_state",
            "description": "Save the updated compact resume state for the current session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "compact_state": {
                        "type": "string",
                        "description": (
                            "Concise markdown resume state for the current session. "
                            "Focus on active goals, recent decisions, open loops, and next steps."
                        ),
                    }
                },
                "required": ["compact_state"],
            },
        },
    }
]


def _ensure_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _normalize_args(args: Any) -> dict[str, Any] | None:
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None


class CompactStateManager:
    """Maintains a compact session resume state alongside archived history."""

    STATE_KEY = "compact_state"
    OFFSET_KEY = "compact_state_offset"
    UPDATED_AT_KEY = "compact_state_updated_at"

    def __init__(self, provider, model: str, max_chars: int = 4000):
        self.provider = provider
        self.model = model
        self.max_chars = max_chars

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        lines = []
        for message in messages:
            content = message.get("content")
            if not content:
                continue
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message.get('role', '?').upper()}: {content}"
            )
        return "\n".join(lines)

    def get_state(self, session) -> str:
        value = session.metadata.get(self.STATE_KEY, "")
        return value if isinstance(value, str) else _ensure_text(value)

    def get_offset(self, session) -> int:
        raw = session.metadata.get(self.OFFSET_KEY, 0)
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    async def sync_session(self, session) -> bool:
        start = self.get_offset(session)
        end = session.last_consolidated
        if end <= start:
            return True

        chunk = session.messages[start:end]
        if not chunk:
            session.metadata[self.OFFSET_KEY] = end
            return True

        current_state = self.get_state(session)
        prompt = f"""Update the compact resume state for this session.

Keep it concise and operational. Capture active goals, recent decisions, open loops, relevant verification results, and the next useful step. Do not rewrite long-term biography or durable facts that belong in persistent memory.

## Current Compact State
{current_state or "(empty)"}

## Newly Archived Messages
{self._format_messages(chunk)}"""

        chat_messages = [
            {
                "role": "system",
                "content": (
                    "You maintain a compact session resume state for a coding assistant. "
                    "Always call the save_compact_state tool with the full updated state."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        forced = {"type": "function", "function": {"name": "save_compact_state"}}
        response = await self.provider.chat_with_retry(
            messages=chat_messages,
            tools=_SAVE_COMPACT_STATE_TOOL,
            model=self.model,
            tool_choice=forced,
        )

        if response.finish_reason == "error" and _is_tool_choice_unsupported(response.content):
            logger.warning("Forced compact-state tool_choice unsupported, retrying with auto")
            response = await self.provider.chat_with_retry(
                messages=chat_messages,
                tools=_SAVE_COMPACT_STATE_TOOL,
                model=self.model,
                tool_choice="auto",
            )

        if not response.has_tool_calls:
            logger.warning(
                "Compact state sync: LLM did not call save_compact_state "
                "(finish_reason={}, content_len={}, content_preview={})",
                response.finish_reason,
                len(response.content or ""),
                (response.content or "")[:200],
            )
            return False

        args = _normalize_args(response.tool_calls[0].arguments)
        if args is None or "compact_state" not in args or args["compact_state"] is None:
            logger.warning("Compact state sync: save_compact_state payload missing compact_state")
            return False

        compact_state = _ensure_text(args["compact_state"]).strip()
        if not compact_state:
            logger.warning("Compact state sync: compact_state is empty after normalization")
            return False

        if self.max_chars > 0 and len(compact_state) > self.max_chars:
            compact_state = compact_state[: self.max_chars].rstrip()

        session.metadata[self.STATE_KEY] = compact_state
        session.metadata[self.OFFSET_KEY] = end
        session.metadata[self.UPDATED_AT_KEY] = datetime.now().isoformat()
        return True
