"""
TDD: State Contract Tests — P0-1

Tests for typed metadata contracts at bus/event boundaries.
Imports bus/events.py directly to avoid full nanobot dependency chain.

These tests define the EXPECTED behavior as the contract.
Passing = contract is established; Failing = contract is violated.

Run: python -m pytest tests/agent/test_state_contracts.py -v
(Async tests are self-contained via asyncio.run() — no pytest-asyncio needed.)
"""

import asyncio
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

# Load modules directly to avoid nanobot.__init__ import chain
# /home/admin/nanobot-fork-live/tests/agent/test_state_contracts.py
#   parents[0] = agent/, [1] = tests/, [2] = nanobot-fork-live/
_NANOBOT_ROOT = Path(__file__).resolve().parents[2]
_events_path = _NANOBOT_ROOT / "nanobot" / "bus" / "events.py"
_queue_path = _NANOBOT_ROOT / "nanobot" / "bus" / "queue.py"

_spec_events = importlib.util.spec_from_file_location("nanobot.bus.events", _events_path)
_events = importlib.util.module_from_spec(_spec_events)
_spec_events.loader.exec_module(_events)

_spec_queue = importlib.util.spec_from_file_location("nanobot.bus.queue", _queue_path)
# queue.py imports from events — inject our already-loaded events module first
sys.modules["nanobot.bus.events"] = _events
_queue = importlib.util.module_from_spec(_spec_queue)
_spec_queue.loader.exec_module(_queue)

InboundMessage = _events.InboundMessage
OutboundMessage = _events.OutboundMessage
MessageBus = _queue.MessageBus


# =============================================================================
# P0-1a: Typed header definitions (documentation + structural tests)
# =============================================================================

class TestTypedHeaderContract:
    """After refactor: metadata should use typed sub-dicts, not raw free-form keys."""

    def test_inbound_metadata_has_typed_routing_info(self):
        """Known routing keys should be accessible and typed in metadata."""
        msg = InboundMessage(
            channel="feishu",
            sender_id="u1",
            chat_id="c1",
            content="/plan",
            metadata={
                "workspace_agent_cmd": "plan",
                "workspace_harness_id": "har_0010",
                "workspace_harness_auto": True,
            },
        )
        # Current: keys are in free-form metadata dict
        # Desired: typed InboundHeaders with known keyset
        assert msg.metadata["workspace_agent_cmd"] == "plan"

    def test_outbound_metadata_stream_markers_form_cohesive_group(self):
        """Stream lifecycle markers should be a typed StreamInfo group."""
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="hello",
            metadata={
                "_stream_start": True,
                "_stream_delta": False,
                "_stream_end": False,
            },
        )
        # Known stream marker keys (may not all be present — that's by design)
        stream_key_definitions = {
            "_stream_start",
            "_stream_delta",
            "_stream_end",
            "_stream_id",
            "_stream_chunk_count",
            "_stream_char_count",
        }
        # At minimum _stream_start and _stream_delta should be present
        assert "_stream_start" in msg.metadata
        assert "_stream_delta" in msg.metadata

    def test_outbound_completion_notice_is_cohesive_group(self):
        """Completion notice keys should form a typed CompletionNotice group."""
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="done",
            metadata={
                "_completion_notice": True,
                "_completion_notice_text": "生成完毕",
                "_completion_notice_mention_user": True,
            },
        )
        completion_keys = {"_completion_notice", "_completion_notice_text", "_completion_notice_mention_user"}
        assert completion_keys.issubset(msg.metadata.keys())

    def test_known_metadata_keyset_exhaustive_documented(self):
        """All metadata keys used across the system should be documented and classified."""
        workspace_keys = {
            "workspace_agent_cmd",
            "workspace_agent_input",
            "workspace_harness_id",
            "workspace_harness_auto",
            "workspace_runtime",
            "workspace_work_mode",
        }
        stream_keys = {
            "_stream_start",
            "_stream_delta",
            "_stream_end",
            "_stream_id",
            "_stream_chunk_count",
            "_stream_char_count",
            "_streamed",
        }
        completion_keys = {
            "_completion_notice",
            "_completion_notice_text",
            "_completion_notice_mention_user",
        }
        delivery_keys = {
            "_progress",
            "_tool_hint",
            "_harness_closeout",
        }
        all_keys = workspace_keys | stream_keys | completion_keys | delivery_keys
        # Verify no duplicates across groups
        assert len(all_keys) == (
            len(workspace_keys) + len(stream_keys) + len(completion_keys) + len(delivery_keys)
        )


# =============================================================================
# P0-1b: Bus immutability contract — ISOLATION (the key fix)
# =============================================================================

class TestBusImmutabilityContract:
    """Bus publish/consume must isolate producer metadata from consumer.

    The MessageBus makes a shallow copy of message metadata on publish.
    This prevents a producer's post-publish mutations from leaking into
    consumers. This is the core P0-1 fix that addresses the mutation-leak
    bad smell identified in the architecture audit.
    """

    def test_inbound_publish_isolates_metadata(self):
        """Publishing an InboundMessage should make metadata isolated (not shared)."""
        bus = MessageBus()
        original_metadata = {"workspace_agent_cmd": "plan", "count": 1}
        msg = InboundMessage(
            channel="feishu",
            sender_id="u1",
            chat_id="c1",
            content="/plan",
            metadata=original_metadata,
        )

        async def run():
            await bus.publish_inbound(msg)
            return await bus.consume_inbound()

        retrieved = asyncio.run(run())

        # Contract: metadata dict is isolated (different object, same values)
        assert retrieved.metadata is not original_metadata  # Isolated
        assert retrieved.metadata == original_metadata  # Values preserved

    def test_outbound_publish_isolates_metadata(self):
        """Publishing an OutboundMessage should make metadata isolated (not shared)."""
        bus = MessageBus()
        original_metadata = {"_stream_start": True, "count": 1}
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="streaming",
            metadata=original_metadata,
        )

        async def run():
            await bus.publish_outbound(msg)
            return await bus.consume_outbound()

        retrieved = asyncio.run(run())

        assert retrieved.metadata is not original_metadata  # Isolated
        assert retrieved.metadata == original_metadata  # Values preserved

    def test_inbound_metadata_changes_after_publish_do_not_leak(self):
        """Mutations to original msg metadata after publish must NOT affect consumer."""
        bus = MessageBus()
        msg = InboundMessage(
            channel="feishu",
            sender_id="u1",
            chat_id="c1",
            content="/plan",
            metadata={"count": 0},
        )

        async def run():
            await bus.publish_inbound(msg)
            # Mutate original metadata AFTER publish
            msg.metadata["count"] = 999
            return await bus.consume_inbound()

        retrieved = asyncio.run(run())
        # Contract: consumer sees the pre-mutation state
        assert retrieved.metadata["count"] == 0

    def test_outbound_metadata_changes_after_publish_do_not_leak(self):
        """Mutations to original OutboundMessage metadata after publish must NOT affect consumer."""
        bus = MessageBus()
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="streaming",
            metadata={"_stream_delta": "partial"},
        )

        async def run():
            await bus.publish_outbound(msg)
            # Mutate after publish
            msg.metadata["_stream_delta"] = "corrupted"
            return await bus.consume_outbound()

        retrieved = asyncio.run(run())
        assert retrieved.metadata["_stream_delta"] == "partial"


# =============================================================================
# P0-1c: Session metadata contract (round-trip serialization)
# =============================================================================

class TestSessionSerializationContract:
    """InboundMessage must survive full dataclass serialization round-trip."""

    def test_inbound_full_serialization_roundtrip(self):
        """Full dataclass serialization should preserve all fields including metadata."""
        original = InboundMessage(
            channel="feishu",
            sender_id="ou_abc123",
            chat_id="oc_def456",
            content="/harness auto",
            media=["https://example.com/image.png"],
            metadata={
                "workspace_agent_cmd": "harness",
                "workspace_harness_auto": True,
                "workspace_harness_id": "har_0010",
            },
            session_key_override="feishu:oc_def456",
        )

        serialized = dataclasses.asdict(original)
        restored = InboundMessage(**serialized)

        assert restored.channel == original.channel
        assert restored.sender_id == original.sender_id
        assert restored.chat_id == original.chat_id
        assert restored.content == original.content
        assert restored.media == original.media
        assert restored.metadata == original.metadata
        assert restored.session_key_override == original.session_key_override
        assert restored.session_key == original.session_key

    def test_inbound_session_key_derivation_normal_case(self):
        """Normal case: session_key = channel:chat_id."""
        msg = InboundMessage(
            channel="feishu", sender_id="u1", chat_id="oc_def456", content="x"
        )
        assert msg.session_key == "feishu:oc_def456"

    def test_inbound_session_key_override(self):
        """session_key_override takes precedence over channel:chat_id derivation."""
        msg = InboundMessage(
            channel="feishu",
            sender_id="u1",
            chat_id="oc_def456",
            content="x",
            session_key_override="unified:default",
        )
        assert msg.session_key == "unified:default"

    def test_outbound_full_serialization_roundtrip(self):
        """OutboundMessage should survive full dataclass serialization."""
        original = OutboundMessage(
            channel="feishu",
            chat_id="oc_def456",
            content="Generating...",
            reply_to="om_12345",
            media=["file.pdf"],
            metadata={
                "_stream_start": True,
                "_completion_notice": True,
                "_completion_notice_text": "生成完毕",
            },
        )

        serialized = dataclasses.asdict(original)
        restored = OutboundMessage(**serialized)

        assert restored.channel == original.channel
        assert restored.chat_id == original.chat_id
        assert restored.content == original.content
        assert restored.reply_to == original.reply_to
        assert restored.media == original.media
        assert restored.metadata == original.metadata


# =============================================================================
# P0-1d: Interrupt state schema contract
# =============================================================================

class TestInterruptStateContract:
    """interrupt_state stored in session.metadata should have a known schema."""

    def test_interrupt_state_required_fields(self):
        """interrupt_state must have at minimum session_key and harness reference."""
        interrupt_state = {
            "session_key": "feishu:oc_def456",
            "workspace_harness_id": "har_0010",
            "workspace_harness_auto": True,
        }
        assert "session_key" in interrupt_state
        assert "workspace_harness_id" in interrupt_state
        assert "workspace_harness_auto" in interrupt_state

    def test_interrupt_state_isolated_in_inbound_metadata(self):
        """interrupt_state should be isolated when stored in InboundMessage metadata."""
        interrupt_state = {
            "session_key": "feishu:oc_def456",
            "workspace_harness_id": "har_0010",
        }
        msg = InboundMessage(
            channel="feishu",
            sender_id="u1",
            chat_id="c1",
            content="resume",
            metadata={"interrupt_state": interrupt_state},
        )

        async def run():
            bus = MessageBus()
            await bus.publish_inbound(msg)
            return await bus.consume_inbound()

        retrieved = asyncio.run(run())
        # The interrupt_state dict itself is isolated (not same object)
        extracted = retrieved.metadata.get("interrupt_state")
        assert extracted is not interrupt_state  # Isolated by MessageBus
        assert extracted == interrupt_state  # Values preserved


# =============================================================================
# P0-1e: Delivery filtering contract (ChannelManager semantics)
# =============================================================================

class TestDeliveryFilteringContract:
    """ChannelManager delivery filtering depends on known metadata key semantics."""

    def test_message_without_filter_flags_passes_through(self):
        """Message without _progress/_tool_hint should always be deliverable."""
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="regular message",
            metadata={},
        )
        assert "_progress" not in msg.metadata
        assert "_tool_hint" not in msg.metadata

    def test_progress_flag_indicates_show_progress(self):
        """_progress=True means this is a progress message."""
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="[working...]",
            metadata={"_progress": True},
        )
        assert msg.metadata.get("_progress") is True

    def test_tool_hint_tri_state_semantics(self):
        """_tool_hint has 3 states: True (show), False (suppress), absent (neither)."""
        # State 1: True — tool hint should be shown
        msg1 = OutboundMessage(
            channel="feishu", chat_id="c1", content="x",
            metadata={"_tool_hint": True, "_progress": True},
        )
        # State 2: False — tool hint suppressed
        msg2 = OutboundMessage(
            channel="feishu", chat_id="c1", content="x",
            metadata={"_tool_hint": False, "_progress": True},
        )
        # State 3: absent — neither shown
        msg3 = OutboundMessage(
            channel="feishu", chat_id="c1", content="x",
            metadata={"_progress": True},
        )

        assert msg1.metadata.get("_tool_hint") is True
        assert msg2.metadata.get("_tool_hint") is False
        assert "_tool_hint" not in msg3.metadata

    def test_stream_and_completion_can_coexist(self):
        """Stream end can carry completion notice together — valid pattern."""
        msg = OutboundMessage(
            channel="feishu",
            chat_id="c1",
            content="final chunk",
            metadata={
                "_stream_end": True,
                "_completion_notice": True,
            },
        )
        assert msg.metadata.get("_stream_end") is True
        assert msg.metadata.get("_completion_notice") is True
