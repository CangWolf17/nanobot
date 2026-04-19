"""TurnContext: per-turn streaming state container.

Refactored from AgentLoop._dispatch() local variables into a first-class dataclass.
This makes turn-scoped streaming state testable in isolation and prevents the
"10 local variables + nested closures" god-method smell.

Usage:
    ctx = TurnContext(session_key=msg.session_key, channel=msg.channel,
                      chat_id=msg.chat_id, wants_stream=bool(msg.metadata.get("_wants_stream")))
    ctx.start_stream(f"{ctx.session_key}:{time.time_ns()}")

    async def on_stream(delta: str) -> None:
        ctx.on_delta(delta, time.monotonic())
        await bus.publish_outbound(OutboundMessage(...))

    response = await agent.process_message(msg, on_stream=on_stream, on_stream_end=ctx.on_stream_end)
    terminal_kp = str((response.metadata or {}).get("_terminal_key_principle_text") or "").strip()
    ctx.set_terminal_kp(terminal_kp)
    await ctx.emit_pending_stream_end(bus, msg)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TurnContext:
    """Owns all per-turn streaming state. One instance per active turn."""

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

    # Timing (set by on_delta / on_stream_end)
    stream_started_at: float | None = None
    stream_finished_at: float | None = None

    # Counters
    stream_chunk_count: int = 0
    stream_char_count: int = 0
    segment_chunk_count: int = 0
    segment_char_count: int = 0

    # KPI extraction (set after response)
    terminal_key_principle_text: str = ""

    # Lifecycle state
    status: str = "pending"  # pending | running | completed | cancelled | error

    # Pending terminal KP for stream end metadata
    _pending_kp: str = field(default="", repr=False)

    def start_stream(self, base_id: str) -> None:
        """Begin a new stream for this turn. Idempotent."""
        self.stream_base_id = base_id
        self.stream_started = True
        self.status = "running"

    def on_delta(self, delta: str, now: float) -> int:
        """Record a stream delta.

        Only sets stream_started_at on the FIRST NON-EMPTY delta,
        matching AgentLoop._dispatch() original semantics:
            if delta and delta.strip():
                if stream_started_at is None:
                    stream_started_at = now

        Returns the char count of the delta.
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

    def on_stream_end(self, *, resuming: bool = False) -> str:
        """Handle end of a stream segment.

        Args:
            resuming: if True, this is an intermediate end (turn continues);
                      resets segment counters and clears stream_end_sent.

        Returns:
            The stream_id for this segment (e.g. "session:ts:0").
        """
        self.stream_segment += 1
        if resuming:
            self.segment_chunk_count = 0
            self.segment_char_count = 0
            self.stream_end_sent = False
            self.status = "running"
        else:
            self.stream_end_sent = True
            self.status = "completed"
        return self.current_stream_id

    def set_terminal_kp(self, kp_text: str) -> None:
        """Set terminal Key Principle text after response is processed."""
        self.terminal_key_principle_text = kp_text
        self._pending_kp = kp_text

    @property
    def current_stream_id(self) -> str:
        """Current stream_id in format {stream_base_id}:{stream_segment}."""
        return f"{self.stream_base_id}:{self.stream_segment}"

    @property
    def final_segment_silent(self) -> bool:
        """True if the final segment had no visible content.

        Mirrors AgentLoop._dispatch() logic:
            final_segment_silent = (
                segment_stream_chunk_count <= 0
                or segment_stream_char_count <= 0
            )
        """
        return self.segment_chunk_count <= 0 or self.segment_char_count <= 0

    @property
    def pending_stream_end(self) -> bool:
        """True if a stream end marker is pending (not yet sent)."""
        return self.stream_base_id is not None and not self.stream_end_sent

    def build_stream_end_metadata(self) -> dict:
        """Build the metadata dict for a stream end marker."""
        metadata = {
            "_stream_end": True,
            "_resuming": False,
            "_stream_id": self.current_stream_id,
        }
        if self.terminal_key_principle_text:
            metadata["_terminal_key_principle_text"] = self.terminal_key_principle_text
        return metadata

    def build_stream_start_metadata(self) -> dict:
        """Build the metadata dict for a stream start marker."""
        return {
            "_stream_start": True,
            "_stream_id": self.current_stream_id,
        }

    def build_stream_delta_metadata(self) -> dict:
        """Build the metadata dict for a stream delta."""
        return {
            "_stream_delta": True,
            "_stream_id": self.current_stream_id,
        }
