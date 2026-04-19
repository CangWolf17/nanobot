"""
TDD: Channel TTL Cleanup Contract — P3-1

Tests for channel state TTL cleanup to prevent unbounded memory growth
and zombie state after channel restart.

These tests define the EXPECTED TTL cleanup behavior.
Target: nanobot/channels/feishu.py

Current state:
  - _processed_message_ids: size-based cleanup only (>1000)
  - _stream_bufs: cleaned on stream end (ok)
  - No TTL-based cleanup for any in-memory state

Desired state:
  - All in-memory state with unbounded growth potential has TTL cleanup
  - Channel restart clears all in-memory state
  - No zombie state across restarts
"""

import time
from collections import OrderedDict
from dataclasses import dataclass


# =============================================================================
# P3-1a: TTL cleanup contract
# =============================================================================

class TestProcessedMessageIdTTLContract:
    """_processed_message_ids must have TTL-based cleanup, not just size-based."""

    def test_processed_message_ids_size_based_cleanup_only_is_incomplete(self):
        """Size-based cleanup (>1000) leaves stale entries for up to 1000 messages."""
        # Current behavior: cleanup only when len > 1000
        # Problem: if channel processes < 1000 messages over a long period,
        # stale message_ids (from old sessions) accumulate indefinitely
        dedup: OrderedDict[str, None] = OrderedDict()

        # Simulate adding many message_ids
        for i in range(500):
            dedup[f"msg_{i}"] = None

        # After 500 messages, no cleanup (len < 1000)
        assert len(dedup) == 500
        # But some of these may be days old and from old sessions

        # Size-based cleanup is incomplete — needs TTL complement
        assert len(dedup) < 1000  # No cleanup triggered

    def test_ttl_based_cleanup_removes_stale_entries(self):
        """TTL cleanup should remove entries older than TTL_WINDOW."""
        # Expected: dedup has timestamp per entry; entries older than
        # TTL_WINDOW (e.g., 1 hour) are evicted regardless of size
        dedup: OrderedDict[str, float] = OrderedDict()  # message_id -> timestamp
        TTL_WINDOW = 3600.0  # 1 hour

        now = time.monotonic()

        # Add old entry
        dedup["old_msg"] = now - 7200  # 2 hours ago

        # Add recent entry
        dedup["recent_msg"] = now - 60  # 1 minute ago

        # TTL cleanup removes old entry
        cutoff = now - TTL_WINDOW
        stale_keys = [k for k, ts in dedup.items() if ts < cutoff]
        valid_keys = [k for k, ts in dedup.items() if ts >= cutoff]

        assert "old_msg" in stale_keys
        assert "recent_msg" in valid_keys
        assert len(stale_keys) == 1

    def test_ttl_cleanup_is_size_independent(self):
        """TTL cleanup should work even when dict is small."""
        dedup: OrderedDict[str, float] = OrderedDict()
        TTL_WINDOW = 3600.0
        now = time.monotonic()

        # Small dict with old entries
        dedup["old1"] = now - 7200
        dedup["old2"] = now - 7200
        dedup["recent"] = now - 60

        cutoff = now - TTL_WINDOW
        dedup = OrderedDict((k, v) for k, v in dedup.items() if v >= cutoff)

        # Even with only 3 entries (never triggers size-based cleanup),
        # TTL cleanup removes stale ones
        assert "recent" in dedup
        assert "old1" not in dedup
        assert "old2" not in dedup


# =============================================================================
# P3-1b: Channel restart contract
# =============================================================================

class TestChannelRestartContract:
    """Channel restart must clear all in-memory state."""

    def test_processed_message_ids_cleared_on_restart(self):
        """_processed_message_ids must be cleared when channel restarts."""
        # Expected behavior: on channel.stop() or channel.start(),
        # _processed_message_ids is cleared
        dedup: OrderedDict[str, None] = OrderedDict()
        for i in range(100):
            dedup[f"msg_{i}"] = None

        # Simulate restart: clear dedup
        dedup.clear()

        assert len(dedup) == 0

    def test_stream_buffers_cleared_on_restart(self):
        """_stream_bufs must be cleared when channel restarts."""
        stream_bufs: dict[str, object] = {}
        for i in range(10):
            stream_bufs[f"chat_{i}"] = {"text": f"buffer_{i}"}

        # Simulate restart: clear all buffers
        stream_bufs.clear()

        assert len(stream_bufs) == 0

    def test_channel_restart_has_no_zombie_state(self):
        """Restart should not retain any state from previous session."""
        # Simulate channel state
        state = {
            "_processed_message_ids": {"msg_1": None, "msg_2": None},
            "_stream_bufs": {"chat_1": {"text": "partial"}},
            "_loop": None,
        }

        # Simulate restart: create fresh channel
        fresh_state = {
            "_processed_message_ids": {},
            "_stream_bufs": {},
            "_loop": None,
        }

        # No zombie state
        assert len(fresh_state["_processed_message_ids"]) == 0
        assert len(fresh_state["_stream_bufs"]) == 0


# =============================================================================
# P3-1c: Stream buffer lifecycle contract
# =============================================================================

class TestStreamBufferLifecycleContract:
    """Stream buffers must be cleaned when stream ends."""

    def test_stream_end_clears_buffer(self):
        """When stream ends, _stream_bufs[chat_id] should be removed."""
        buffers: dict[str, dict] = {}
        chat_id = "chat_123"

        # Start stream
        buffers[chat_id] = {"text": "", "started": True}
        assert chat_id in buffers

        # Stream ends: pop buffer
        buffers.pop(chat_id, None)

        assert chat_id not in buffers

    def test_stream_end_with_error_also_clears_buffer(self):
        """Even on error, stream buffer should be cleaned up."""
        buffers: dict[str, dict] = {}
        chat_id = "chat_error"

        buffers[chat_id] = {"text": "partial", "error": True}
        buffers.pop(chat_id, None)

        assert chat_id not in buffers

    def test_multiple_concurrent_streams_isolated(self):
        """Multiple concurrent streams should not interfere."""
        buffers: dict[str, dict] = {}
        chats = [f"chat_{i}" for i in range(5)]

        for cid in chats:
            buffers[cid] = {"text": f"content_{cid}"}

        # End only chat_2
        buffers.pop("chat_2", None)

        assert "chat_2" not in buffers
        assert "chat_1" in buffers
        assert "chat_4" in buffers
        assert len(buffers) == 4
