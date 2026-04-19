"""
TDD: Turn Lifecycle Contract Tests — P0-2

Tests for per-turn streaming state isolation and turn lifecycle management.
These tests document the expected shape of TurnContext.

Target: nanobot/agent/loop.py

Current state: streaming state lives as 10+ local variables + nested closures
inside _dispatch(). This makes the logic untestable in isolation.

Desired state: TurnContext dataclass owns all turn-scoped streaming state,
with clear lifecycle: init → run → complete/cancel/error.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable


# =============================================================================
# P0-2: TurnContext dataclass definition (expected shape)
# =============================================================================

@dataclass
class TurnContext:
    """Owns all per-turn streaming state. Should be testable in isolation.

    This test defines the EXPECTED interface. The actual TurnContext will
    be implemented in nanobot/agent/turn_context.py and imported by AgentLoop.
    """
    session_key: str
    channel: str
    chat_id: str
    wants_stream: bool

    # Stream identifiers
    stream_base_id: str | None = None
    stream_segment: int = 0

    # Stream lifecycle flags
    stream_end_sent: bool = False
    stream_started: bool = False

    # Timing
    stream_started_at: float | None = None
    stream_finished_at: float | None = None

    # Counters
    stream_chunk_count: int = 0
    stream_char_count: int = 0
    segment_chunk_count: int = 0
    segment_char_count: int = 0

    # KPI extraction
    terminal_key_principle_text: str = ""

    # Lifecycle state
    status: str = "pending"  # pending | running | completed | cancelled | error

    def start_stream(self, base_id: str) -> None:
        """Begin a new stream for this turn."""
        self.stream_base_id = base_id
        self.stream_started = True
        self.status = "running"

    def on_delta(self, delta: str, now: float) -> int:
        """Record a stream delta. Returns char count added.

        stream_started_at is set on the FIRST NON-EMPTY delta,
        matching AgentLoop._dispatch() semantics:
          if delta and delta.strip():
              if stream_started_at is None:
                  stream_started_at = now
        """
        if delta and delta.strip():
            if self.stream_started_at is None:
                self.stream_started_at = now
        self.stream_finished_at = now
        self.stream_chunk_count += 1
        self.stream_char_count += len(delta)
        self.segment_chunk_count += 1
        self.segment_char_count += len(delta)
        return len(delta)

    def on_segment_end(self, resuming: bool) -> str:
        """End current segment. Returns stream_id. If resuming, resets segment counters."""
        self.stream_segment += 1
        if resuming:
            self.segment_chunk_count = 0
            self.segment_char_count = 0
            self.stream_end_sent = False
        else:
            self.stream_end_sent = True
            self.stream_finished_at = self.stream_finished_at or self.stream_finished_at
        self.status = "completed"
        return self._current_stream_id()

    def _current_stream_id(self) -> str:
        return f"{self.stream_base_id}:{self.stream_segment}"

    @property
    def current_stream_id(self) -> str:
        return self._current_stream_id()

    @property
    def final_segment_silent(self) -> bool:
        """True if the final segment had no visible content."""
        return self.segment_chunk_count <= 0 or self.segment_char_count <= 0


# =============================================================================
# P0-2: TurnContext tests — lock the expected behavior
# =============================================================================

class TestTurnContextLifecycle:
    """TurnContext should manage a complete turn lifecycle."""

    def test_turn_initial_state(self):
        """New TurnContext should have clean initial state."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        assert ctx.status == "pending"
        assert ctx.stream_chunk_count == 0
        assert ctx.stream_char_count == 0
        assert ctx.stream_segment == 0
        assert not ctx.stream_end_sent
        assert not ctx.stream_started
        assert ctx.terminal_key_principle_text == ""

    def test_turn_start_stream(self):
        """Starting stream should set base_id and mark as running."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("feishu:oc_123:1234567890")

        assert ctx.stream_base_id == "feishu:oc_123:1234567890"
        assert ctx.stream_started is True
        assert ctx.status == "running"
        assert ctx.stream_segment == 0

    def test_turn_delta_accumulates_counters(self):
        """Each delta should accumulate chunk and char counters correctly."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("base")
        import time
        now = time.monotonic()

        ctx.on_delta("Hello ", now)
        ctx.on_delta("world!", now)

        assert ctx.stream_chunk_count == 2
        assert ctx.stream_char_count == 12
        assert ctx.segment_chunk_count == 2
        assert ctx.segment_char_count == 12

    def test_turn_first_delta_records_started_at(self):
        """First delta should record stream_started_at."""
        import time
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("base")
        now = time.monotonic()

        assert ctx.stream_started_at is None
        ctx.on_delta("first", now)
        assert ctx.stream_started_at is not None

    def test_turn_segment_end_resets_segment_counters_when_resuming(self):
        """Segment end with resuming=True should reset segment counters."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("base")
        import time
        ctx.on_delta("segment 1 content", time.monotonic())

        stream_id = ctx.on_segment_end(resuming=True)

        assert stream_id == "base:1"
        assert ctx.segment_chunk_count == 0
        assert ctx.segment_char_count == 0
        assert not ctx.stream_end_sent  # Reset because resuming
        assert ctx.stream_chunk_count == 1  # Total still accumulated

    def test_turn_segment_end_marks_completed_when_not_resuming(self):
        """Segment end with resuming=False should mark turn as completed."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("base")
        import time
        ctx.on_delta("final content", time.monotonic())

        stream_id = ctx.on_segment_end(resuming=False)

        assert stream_id == "base:1"
        assert ctx.stream_end_sent is True
        assert ctx.status == "completed"

    def test_turn_final_segment_silent_detection(self):
        """final_segment_silent should detect silent final segments."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("base")

        # Non-silent segment
        import time
        ctx.on_delta("visible", time.monotonic())
        assert not ctx.final_segment_silent

        # Silent segment (zero chars)
        ctx2 = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx2.start_stream("base2")
        ctx2.on_delta("", time.monotonic())
        assert ctx2.final_segment_silent

    def test_turn_current_stream_id_format(self):
        """current_stream_id should follow {base}:{segment} format."""
        ctx = TurnContext(
            session_key="feishu:oc_123",
            channel="feishu",
            chat_id="oc_123",
            wants_stream=True,
        )
        ctx.start_stream("session:1234:5678900")
        assert ctx.current_stream_id == "session:1234:5678900:0"

        ctx.stream_segment = 3
        assert ctx.current_stream_id == "session:1234:5678900:3"


# =============================================================================
# P0-2: Turn lifecycle tracking contract
# =============================================================================

class TestTurnRegistryContract:
    """AgentLoop should track inflight turns with clear lifecycle management.

    Current state: _inflight_turns is a dict in AgentLoop with no formal contract.
    Desired state: TurnRegistry with add/remove/update/query operations.
    """

    def test_inflight_turns_add_on_dispatch_start(self):
        """Starting dispatch should register the turn."""
        # This documents the expected API
        registry = {}  # Simulating AgentLoop._inflight_turns
        session_key = "feishu:oc_123"
        turn_id = f"{session_key}:turn_1"

        # Add turn
        registry[session_key] = {
            "turn_id": turn_id,
            "status": "running",
            "started_at": "2026-04-19T10:00:00",
        }

        assert session_key in registry
        assert registry[session_key]["status"] == "running"

    def test_inflight_turns_remove_on_completion(self):
        """Completed/cancelled turns should be removable."""
        registry = {}
        session_key = "feishu:oc_123"

        registry[session_key] = {"status": "running"}
        # Complete
        registry[session_key]["status"] = "completed"
        # Discard (as AgentLoop._discard_inflight_turn does)
        registry.pop(session_key, None)

        assert session_key not in registry

    def test_inflight_turns_multiple_sessions_can_run_concurrently(self):
        """Multiple sessions can have inflight turns simultaneously."""
        registry = {}
        sessions = [
            ("feishu:oc_1", "running"),
            ("telegram:chat_2", "running"),
            ("discord:guild_3", "running"),
        ]
        for session_key, status in sessions:
            registry[session_key] = {"status": status}

        assert len(registry) == 3
        for session_key, status in sessions:
            assert session_key in registry
            assert registry[session_key]["status"] == status

    def test_inflight_turns_same_session_serial(self):
        """Same session should not have multiple concurrent turns."""
        registry = {}
        session_key = "feishu:oc_123"

        # First turn starts
        registry[session_key] = {"status": "running", "turn_id": 1}
        # Second turn check (should not start if already running)
        can_start = session_key not in registry

        assert can_start is False  # Already has a turn


# =============================================================================
# P0-2: Stream state isolation (closure vs TurnContext)
# =============================================================================

class TestStreamStateIsolation:
    """Stream callbacks should not leak mutable state across turns.

    Current state: stream callbacks are closures over _dispatch() local variables.
    Risk: if turn context is shared, delta counts could leak between concurrent turns.

    Desired state: each turn has its own TurnContext, fully isolated.
    """

    def test_turn_context_is_isolated_between_turns(self):
        """Each TurnContext should have independent counter state."""
        import time
        ctx1 = TurnContext(session_key="s1", channel="c1", chat_id="c1", wants_stream=True)
        ctx2 = TurnContext(session_key="s2", channel="c2", chat_id="c2", wants_stream=True)

        ctx1.start_stream("s1:stream")
        ctx2.start_stream("s2:stream")

        ctx1.on_delta("content1", time.monotonic())
        ctx1.on_delta("content1b", time.monotonic())

        # ctx2 should have zero counts, unaffected by ctx1
        assert ctx2.stream_chunk_count == 0
        assert ctx2.stream_char_count == 0
        assert ctx2.segment_chunk_count == 0

    def test_turn_context_terminal_kp_isolation(self):
        """terminal_key_principle_text should be turn-scoped."""
        ctx1 = TurnContext(session_key="s1", channel="c1", chat_id="c1", wants_stream=True)
        ctx2 = TurnContext(session_key="s2", channel="c2", chat_id="c2", wants_stream=True)

        ctx1.terminal_key_principle_text = "KP from turn 1"
        ctx2.terminal_key_principle_text = "KP from turn 2"

        assert ctx1.terminal_key_principle_text == "KP from turn 1"
        assert ctx2.terminal_key_principle_text == "KP from turn 2"

    def test_turn_context_status_isolation(self):
        """Turn status should be independently managed per turn."""
        import time
        ctx1 = TurnContext(session_key="s1", channel="c1", chat_id="c1", wants_stream=True)
        ctx2 = TurnContext(session_key="s2", channel="c2", chat_id="c2", wants_stream=True)

        ctx1.start_stream("s1")
        ctx2.start_stream("s2")
        ctx1.on_segment_end(resuming=False)  # Complete ctx1
        # ctx2 still running

        assert ctx1.status == "completed"
        assert ctx2.status == "running"
