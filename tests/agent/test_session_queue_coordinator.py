"""Tests for SessionQueueCoordinator."""

import time

import pytest

from nanobot.agent.queue import (
    BatchMetrics,
    DispatchState,
    QueuedItem,
    QueuedItemKind,
    SessionQueueCoordinator,
    SessionQueueState,
)


class TestEnqueueNormal:
    def test_enqueue_normal_into_empty_session(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="hello",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        state = coord._ensure_state("sess1")
        assert len(state.normal_buffer) == 1
        item = state.normal_buffer[0]
        assert item.kind == QueuedItemKind.NORMAL
        assert item.content == "hello"
        assert item.channel == "cli"
        assert item.chat_id == "direct"
        assert item.sender_id == "user"
        assert item.provenance_id.startswith("norm-")

    def test_enqueue_normal_multiple_into_active_session(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="first",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.enqueue_normal(
            content="second",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        state = coord._ensure_state("sess1")
        assert len(state.normal_buffer) == 2
        assert state.normal_buffer[0].content == "first"
        assert state.normal_buffer[1].content == "second"

    def test_enqueue_normal_with_metadata(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="msg",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
            metadata={"key": "value"},
        )
        state = coord._ensure_state("sess1")
        assert state.normal_buffer[0].metadata == {"key": "value"}


class TestReserveTurnSlot:
    def test_reserve_turn_slot_success(self):
        coord = SessionQueueCoordinator()
        result = coord.reserve_turn_slot(
            content="turn item",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert result is True
        state = coord._ensure_state("sess1")
        assert state.turn_slot is not None
        assert state.turn_slot.kind == QueuedItemKind.TURN
        assert state.turn_slot.content == "turn item"
        assert state.turn_slot.provenance_id.startswith("turn-")

    def test_second_reserve_turn_slot_returns_false(self):
        coord = SessionQueueCoordinator()
        coord.reserve_turn_slot(
            content="first turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        result = coord.reserve_turn_slot(
            content="second turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert result is False
        state = coord._ensure_state("sess1")
        # original slot preserved
        assert state.turn_slot.content == "first turn"

    def test_reserve_turn_slot_idle_immediate(self):
        """Slot is free when session has no prior reservation."""
        coord = SessionQueueCoordinator()
        result = coord.reserve_turn_slot(
            content="immediate turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess2",
        )
        assert result is True

    def test_has_turn_slot_true_when_reserved(self):
        coord = SessionQueueCoordinator()
        coord.reserve_turn_slot(
            content="turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert coord.has_turn_slot("sess1") is True

    def test_has_turn_slot_false_when_empty(self):
        coord = SessionQueueCoordinator()
        assert coord.has_turn_slot("sess1") is False

    def test_consume_turn_slot_returns_item_and_clears(self):
        coord = SessionQueueCoordinator()
        coord.reserve_turn_slot(
            content="to consume",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        item = coord.consume_turn_slot("sess1")
        assert item is not None
        assert item.content == "to consume"
        assert coord.has_turn_slot("sess1") is False

    def test_consume_turn_slot_on_empty_returns_none(self):
        coord = SessionQueueCoordinator()
        item = coord.consume_turn_slot("sess1")
        assert item is None


class TestBatchFIFO:
    def test_batch_fifo_preserves_order(self):
        coord = SessionQueueCoordinator()
        for i in range(5):
            coord.enqueue_normal(
                content=f"msg{i}",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                session_key="sess1",
            )
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=10, max_chars=100000)
        assert len(consumed) == 5
        assert [item.content for item in consumed] == ["msg0", "msg1", "msg2", "msg3", "msg4"]

    def test_batch_guard_by_item_count(self):
        coord = SessionQueueCoordinator()
        for i in range(12):
            coord.enqueue_normal(
                content=f"msg{i}",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                session_key="sess1",
            )
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=10, max_chars=100000)
        assert len(consumed) == 10
        assert metrics.last_item_count == 10
        assert metrics.dropped_reason == "item_count"
        # 2 items remain
        remaining = coord.peek_normal_buffer("sess1")
        assert len(remaining) == 2

    def test_batch_guard_by_char_size(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="short",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.enqueue_normal(
            content="x" * 50,
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.enqueue_normal(
            content="y" * 50,
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        # max_chars=60 should consume "short" (~5) then stop before 50-char items exceed limit
        # short=5, next item 50 chars would make 55 which is < 60, so consume it too
        # third item 50 chars would make 105 > 60, stop
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=10, max_chars=60)
        assert len(consumed) == 2
        assert metrics.dropped_reason == "char_count"
        remaining = coord.peek_normal_buffer("sess1")
        assert len(remaining) == 1
        assert remaining[0].content == "y" * 50

    def test_overflow_remainder_stays_queued(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="a",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.enqueue_normal(
            content="b",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=1, max_chars=10000)
        assert len(consumed) == 1
        assert consumed[0].content == "a"
        remaining = coord.peek_normal_buffer("sess1")
        assert len(remaining) == 1
        assert remaining[0].content == "b"

    def test_single_item_exceeding_char_limit_still_consumed(self):
        """When a single item itself exceeds max_chars it is consumed and overflow is marked."""
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="x" * 100,
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=10, max_chars=50)
        assert len(consumed) == 1
        assert consumed[0].content == "x" * 100
        assert metrics.last_char_count == 100
        assert metrics.dropped_reason == "char_count"


class TestRaceWindow:
    def test_peek_and_assemble_race_window_returns_both_items(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="late normal",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.reserve_turn_slot(
            content="reserved turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        normals, turn = coord.peek_and_assemble_race_window("sess1")
        assert len(normals) == 1
        assert normals[0].content == "late normal"
        assert turn is not None
        assert turn.content == "reserved turn"

    def test_peek_and_assemble_race_window_no_turn(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="only normal",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        normals, turn = coord.peek_and_assemble_race_window("sess1")
        assert len(normals) == 1
        assert turn is None


class TestEffectiveKey:
    def test_unified_session_routes_to_unified_default(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="unified msg",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="any-key",
            unified=True,
        )
        # same effective key regardless of session_key
        assert coord.has_normal_items("any-key", unified=True)
        assert coord.has_normal_items("other-key", unified=True)

    def test_normal_session_uses_own_key(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="sess1 msg",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert coord.has_normal_items("sess1") is True
        assert coord.has_normal_items("sess2") is False

    def test_dispatch_state_per_session(self):
        coord = SessionQueueCoordinator()
        coord.set_dispatch_state("sess1", DispatchState.RUNNING)
        coord.set_dispatch_state("sess2", DispatchState.IDLE)
        assert coord.get_dispatch_state("sess1") == DispatchState.RUNNING
        assert coord.get_dispatch_state("sess2") == DispatchState.IDLE


class TestClearQueue:
    def test_clear_queue_on_new_session(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="old msg",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.reserve_turn_slot(
            content="old turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.set_dispatch_state("sess1", DispatchState.RUNNING)
        coord.clear_queue("sess1")
        state = coord._ensure_state("sess1")
        assert len(state.normal_buffer) == 0
        assert state.turn_slot is None
        assert state.dispatch_state == DispatchState.IDLE

    def test_clear_all_queues(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="msg1",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        coord.enqueue_normal(
            content="msg2",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess2",
        )
        coord.clear_all_queues()
        assert coord.has_normal_items("sess1") is False
        assert coord.has_normal_items("sess2") is False


class TestQueueExistence:
    def test_has_queued_work_normal_only(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="msg",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert coord.has_queued_work("sess1") is True

    def test_has_queued_work_turn_only(self):
        coord = SessionQueueCoordinator()
        coord.reserve_turn_slot(
            content="turn",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert coord.has_queued_work("sess1") is True

    def test_has_queued_work_empty(self):
        coord = SessionQueueCoordinator()
        assert coord.has_queued_work("sess1") is False

    def test_has_normal_queued_work_true(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="msg",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        assert coord.has_normal_queued_work("sess1") is True

    def test_has_normal_queued_work_false(self):
        coord = SessionQueueCoordinator()
        assert coord.has_normal_queued_work("sess1") is False


class TestInterruptConsume:
    def test_set_and_clear_pending_interrupt_consume(self):
        coord = SessionQueueCoordinator()
        assert coord.has_pending_interrupt_consume("sess1") is False
        coord.set_pending_interrupt_consume("sess1")
        assert coord.has_pending_interrupt_consume("sess1") is True
        coord.clear_pending_interrupt_consume("sess1")
        assert coord.has_pending_interrupt_consume("sess1") is False


class TestBatchMetrics:
    def test_batch_metrics_recorded(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="abc",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        consumed, metrics = coord.consume_normal_batch("sess1")
        assert metrics.last_item_count == 1
        assert metrics.last_char_count == 3
        assert metrics.dropped_reason is None

    def test_batch_metrics_dropped_reason_item_count(self):
        coord = SessionQueueCoordinator()
        for i in range(5):
            coord.enqueue_normal(
                content=f"msg{i}",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                session_key="sess1",
            )
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=3, max_chars=100000)
        assert metrics.dropped_reason == "item_count"

    def test_batch_metrics_dropped_reason_char_count(self):
        coord = SessionQueueCoordinator()
        coord.enqueue_normal(
            content="short",
            channel="cli",
            chat_id="direct",
            sender_id="user",
            session_key="sess1",
        )
        consumed, metrics = coord.consume_normal_batch("sess1", max_items=10, max_chars=3)
        assert metrics.dropped_reason == "char_count"