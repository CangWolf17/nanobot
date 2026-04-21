"""Tests for /tq and /turnqueue command surface."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from nanobot.bus.events import InboundMessage
from nanobot.command.runtime_builtin import cmd_tq
from nanobot.command.router import CommandContext


# ---------------------------------------------------------------------------
# Fake loop — uses real dict for _active_tasks so iteration works correctly
# ---------------------------------------------------------------------------

class _FakeLoop:
    def __init__(
        self,
        *,
        unified_session: bool = False,
        active_tasks: list = None,
        coordinator: MagicMock | None = None,
    ):
        self._unified_session = unified_session
        # Use a real dict (not MagicMock) so iteration over _active_tasks works
        self._active_tasks: dict[str, list] = {}
        if active_tasks is not None:
            self._active_tasks["cli:direct"] = active_tasks
        self._dispatch = AsyncMock()
        self.coordinator = coordinator or MagicMock()

    def __getattr__(self, name: str):
        # Fall through for any unset attributes
        return MagicMock()


# ---------------------------------------------------------------------------
# Message / context helpers
# ---------------------------------------------------------------------------

def _make_msg(
    raw: str,
    *,
    session_key_override: str | None = None,
) -> InboundMessage:
    """Create a real InboundMessage.

    Uses channel='cli', chat_id='direct' so msg.session_key == 'cli:direct'.
    _FakeLoop stores active_tasks under 'cli:direct' to match.
    """
    return InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="direct",
        content=raw,
        metadata={},
        session_key_override=session_key_override,
    )


def _make_ctx(
    raw: str,
    loop: _FakeLoop,
    *,
    session_key_override: str | None = None,
) -> CommandContext:
    msg = _make_msg(raw, session_key_override=session_key_override)
    return CommandContext(msg=msg, session=None, key="cli:direct", raw=raw, loop=loop)


# ---------------------------------------------------------------------------
# Idle session → immediate dispatch
# ---------------------------------------------------------------------------

def test_idle_tq_immediate_dispatch():
    """Idle session: /tq immediately dispatches the content as a new turn."""
    loop = _FakeLoop(active_tasks=[])  # no active tasks = idle
    ctx = _make_ctx("/tq hello world", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert result.content == "Queued for immediate turn."
    loop._dispatch.assert_awaited_once()
    call_msg = loop._dispatch.call_args[0][0]
    assert call_msg.content == "hello world"
    assert call_msg.metadata.get("_tq_turn") is True


def test_idle_tq_no_args_returns_usage():
    """Idle session with no args: returns usage help."""
    loop = _FakeLoop(active_tasks=[])
    ctx = _make_ctx("/tq", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert "Usage:" in result.content
    assert "/turnqueue is an alias" in result.content
    loop._dispatch.assert_not_called()


def test_idle_turnqueue_alias():
    """Idle session: /turnqueue also triggers immediate dispatch."""
    loop = _FakeLoop(active_tasks=[])
    ctx = _make_ctx("/turnqueue hello from alias", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert result.content == "Queued for immediate turn."
    loop._dispatch.assert_awaited_once()
    call_msg = loop._dispatch.call_args[0][0]
    assert call_msg.content == "hello from alias"


# ---------------------------------------------------------------------------
# Active session → reserve slot
# ---------------------------------------------------------------------------

def test_active_tq_reserves_slot():
    """Active session: /tq reserves the turn slot."""
    coordinator = MagicMock()
    coordinator.reserve_turn_slot.return_value = True
    running_task = MagicMock(done=MagicMock(return_value=False))
    loop = _FakeLoop(active_tasks=[running_task], coordinator=coordinator)
    ctx = _make_ctx("/tq queued message", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert result.content == "Queued for the next turn."
    coordinator.reserve_turn_slot.assert_called_once()
    call_kwargs = coordinator.reserve_turn_slot.call_args[1]
    assert call_kwargs["content"] == "queued message"
    assert call_kwargs["session_key"] == "cli:direct"
    assert call_kwargs["unified"] is False


def test_active_tq_slot_already_occupied():
    """Second /tq while slot occupied → reject."""
    coordinator = MagicMock()
    coordinator.reserve_turn_slot.return_value = False  # slot taken
    running_task = MagicMock(done=MagicMock(return_value=False))
    loop = _FakeLoop(active_tasks=[running_task], coordinator=coordinator)
    ctx = _make_ctx("/tq second message", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert "already queued" in result.content.lower()


def test_active_turnqueue_alias_reserves_slot():
    """Active session: /turnqueue also tries to reserve slot."""
    coordinator = MagicMock()
    coordinator.reserve_turn_slot.return_value = True
    running_task = MagicMock(done=MagicMock(return_value=False))
    loop = _FakeLoop(active_tasks=[running_task], coordinator=coordinator)
    ctx = _make_ctx("/turnqueue alias message", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert result.content == "Queued for the next turn."
    coordinator.reserve_turn_slot.assert_called_once()


def test_active_tq_with_no_args_returns_usage():
    """Active session with no args: returns usage help (does not reserve)."""
    coordinator = MagicMock()
    running_task = MagicMock(done=MagicMock(return_value=False))
    loop = _FakeLoop(active_tasks=[running_task], coordinator=coordinator)
    ctx = _make_ctx("/tq", loop)

    result = asyncio.run(cmd_tq(ctx))

    assert "Usage:" in result.content
    coordinator.reserve_turn_slot.assert_not_called()


# ---------------------------------------------------------------------------
# Unified session
# ---------------------------------------------------------------------------

def test_unified_idle_tq_uses_unified_key():
    """Unified idle session: uses unified:default as effective key."""
    coordinator = MagicMock()
    loop = _FakeLoop(unified_session=True, active_tasks=[], coordinator=coordinator)
    loop._active_tasks["unified:default"] = []
    ctx = _make_ctx("/tq unified msg", loop, session_key_override=None)

    result = asyncio.run(cmd_tq(ctx))

    assert result.content == "Queued for immediate turn."
    assert "unified:default" in loop._active_tasks


def test_unified_session_key_override_skips_unified():
    """session_key_override set → use regular session key even in unified mode."""
    coordinator = MagicMock()
    loop = _FakeLoop(unified_session=True, active_tasks=[], coordinator=coordinator)
    # session_key_override is set → unified is bypassed, use "cli:direct"
    ctx = _make_ctx("/tq regular msg", loop, session_key_override="cli:override")

    result = asyncio.run(cmd_tq(ctx))

    assert result.content == "Queued for immediate turn."
    assert "cli:direct" in loop._active_tasks
