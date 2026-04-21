"""Session queue coordinator — per-session normal-buffer and turn-slot state management."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DispatchState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    TURN_CLOSING = "turn_closing"
    DISPATCHING_RESERVED_TURN = "dispatching_reserved_turn"


class QueuedItemKind(Enum):
    NORMAL = "normal"
    TURN = "turn"


@dataclass
class QueuedItem:
    kind: QueuedItemKind
    content: str
    channel: str
    chat_id: str
    sender_id: str
    enqueued_at: float
    provenance_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchMetrics:
    last_item_count: int = 0
    last_char_count: int = 0
    dropped_reason: str | None = None


@dataclass
class SessionQueueState:
    normal_buffer: list[QueuedItem] = field(default_factory=list)
    turn_slot: QueuedItem | None = None
    dispatch_state: DispatchState = DispatchState.IDLE
    pending_interrupt_consume: bool = False
    pending_reserved_dispatch: dict | None = None  # assembled payload
    batch_metrics: BatchMetrics = field(default_factory=BatchMetrics)


class SessionQueueCoordinator:
    def __init__(self) -> None:
        self._state: dict[str, SessionQueueState] = {}

    # --- effective key helpers ---
    def _effective_key(self, session_key: str, unified: bool = False) -> str:
        if unified:
            return "unified:default"
        return session_key

    def _ensure_state(self, session_key: str, unified: bool = False) -> SessionQueueState:
        key = self._effective_key(session_key, unified)
        if key not in self._state:
            self._state[key] = SessionQueueState()
        return self._state[key]

    # --- normal queue ---
    def enqueue_normal(
        self,
        content: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        session_key: str,
        unified: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a plain user message to the normal buffer."""
        state = self._ensure_state(session_key, unified)
        item = QueuedItem(
            kind=QueuedItemKind.NORMAL,
            content=content,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            enqueued_at=time.time(),
            provenance_id=f"norm-{time.time_ns()}",
            metadata=metadata or {},
        )
        state.normal_buffer.append(item)

    # --- turn queue (/tq) ---
    def reserve_turn_slot(
        self,
        content: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        session_key: str,
        unified: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Reserve the next-turn slot. Returns True if reserved, False if slot already occupied.
        """
        state = self._ensure_state(session_key, unified)
        if state.turn_slot is not None:
            return False
        item = QueuedItem(
            kind=QueuedItemKind.TURN,
            content=content,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            enqueued_at=time.time(),
            provenance_id=f"turn-{time.time_ns()}",
            metadata=metadata or {},
        )
        state.turn_slot = item
        return True

    def has_turn_slot(self, session_key: str, unified: bool = False) -> bool:
        state = self._ensure_state(session_key, unified)
        return state.turn_slot is not None

    def consume_turn_slot(self, session_key: str, unified: bool = False) -> QueuedItem | None:
        state = self._ensure_state(session_key, unified)
        item = state.turn_slot
        state.turn_slot = None
        return item

    # --- normal queue consumption ---
    def has_normal_items(self, session_key: str, unified: bool = False) -> bool:
        state = self._ensure_state(session_key, unified)
        return len(state.normal_buffer) > 0

    def peek_normal_buffer(self, session_key: str, unified: bool = False) -> list[QueuedItem]:
        state = self._ensure_state(session_key, unified)
        return list(state.normal_buffer)

    def consume_normal_batch(
        self,
        session_key: str,
        unified: bool = False,
        max_items: int = 10,
        max_chars: int = 8000,
    ) -> tuple[list[QueuedItem], BatchMetrics]:
        """
        Consume a batch of normal items (FIFO) up to guards.
        Returns (consumed_items, metrics). Remaining items stay in buffer.
        """
        state = self._ensure_state(session_key, unified)
        consumed: list[QueuedItem] = []
        total_chars = 0
        metrics = BatchMetrics()

        for i, item in enumerate(state.normal_buffer):
            item_chars = len(item.content)
            if len(consumed) >= max_items:
                metrics.dropped_reason = "item_count"
                break
            if total_chars + item_chars > max_chars:
                if not consumed:
                    # single item exceeds limit — consume it anyway, mark overflow
                    consumed.append(item)
                    total_chars += item_chars
                metrics.dropped_reason = "char_count"
                break
            consumed.append(item)
            total_chars += item_chars

        # remove consumed from buffer
        if consumed:
            state.normal_buffer = state.normal_buffer[len(consumed) :]

        metrics.last_item_count = len(consumed)
        metrics.last_char_count = total_chars
        state.batch_metrics = metrics
        return consumed, metrics

    def peek_and_assemble_race_window(
        self, session_key: str, unified: bool = False
    ) -> tuple[list[QueuedItem], QueuedItem | None]:
        """
        For race window: return (late_normal_items, turn_slot_item) if turn_slot is reserved.
        """
        state = self._ensure_state(session_key, unified)
        normals = list(state.normal_buffer)
        turn = state.turn_slot
        return normals, turn

    # --- dispatch state ---
    def set_dispatch_state(
        self, session_key: str, dispatch_state: DispatchState, unified: bool = False
    ) -> None:
        state = self._ensure_state(session_key, unified)
        state.dispatch_state = dispatch_state

    def get_dispatch_state(self, session_key: str, unified: bool = False) -> DispatchState:
        state = self._ensure_state(session_key, unified)
        return state.dispatch_state

    # --- queue-aware interrupt ---
    def set_pending_interrupt_consume(self, session_key: str, unified: bool = False) -> None:
        state = self._ensure_state(session_key, unified)
        state.pending_interrupt_consume = True

    def clear_pending_interrupt_consume(self, session_key: str, unified: bool = False) -> None:
        state = self._ensure_state(session_key, unified)
        state.pending_interrupt_consume = False

    def has_pending_interrupt_consume(self, session_key: str, unified: bool = False) -> bool:
        state = self._ensure_state(session_key, unified)
        return state.pending_interrupt_consume

    def set_pending_dispatch(
        self,
        session_key: str,
        payload: Any,
        unified: bool = False,
    ) -> None:
        state = self._ensure_state(session_key, unified)
        state.pending_reserved_dispatch = payload

    def pop_pending_dispatch(self, session_key: str, unified: bool = False) -> Any | None:
        state = self._ensure_state(session_key, unified)
        payload = state.pending_reserved_dispatch
        state.pending_reserved_dispatch = None
        return payload

    # --- queue existence checks ---
    def has_queued_work(self, session_key: str, unified: bool = False) -> bool:
        """True if any queued work exists (normal buffer or turn slot)."""
        state = self._ensure_state(session_key, unified)
        return len(state.normal_buffer) > 0 or state.turn_slot is not None

    def has_normal_queued_work(self, session_key: str, unified: bool = False) -> bool:
        state = self._ensure_state(session_key, unified)
        return len(state.normal_buffer) > 0

    # --- cleanup ---
    def clear_queue(self, session_key: str, unified: bool = False) -> None:
        key = self._effective_key(session_key, unified)
        if key in self._state:
            self._state[key] = SessionQueueState()

    def clear_all_queues(self) -> None:
        self._state.clear()
