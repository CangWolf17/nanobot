"""Async message queue for decoupled channel-agent communication."""

import asyncio
import dataclasses

from nanobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message queue that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    Contract: publish() makes a shallow defensive copy of the message's
    metadata dict so that subsequent mutations by the producer cannot
    affect the consumer. This isolates the producer/consumer sides and
    prevents accidental state leakage through shared dict references.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent.

        Makes a shallow copy of metadata to isolate channel (producer) from
        agent (consumer). The original message object is untouched.
        """
        published = self._copy_with_isolated_metadata(msg)
        await self.inbound.put(published)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels.

        Makes a shallow copy of metadata to isolate agent (producer) from
        channel dispatcher (consumer). The original message object is untouched.
        """
        published = self._copy_with_isolated_metadata(msg)
        await self.outbound.put(published)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @staticmethod
    def _copy_with_isolated_metadata(
        msg: InboundMessage | OutboundMessage,
    ) -> InboundMessage | OutboundMessage:
        """Return a copy of msg with metadata replaced by a shallow dict copy.

        This ensures the producer's reference to the original metadata dict
        is never shared with the consumer side, preventing mutation leakage.
        """
        kwargs = dataclasses.asdict(msg)
        # Replace metadata with a shallow copy; this severs the reference chain
        kwargs["metadata"] = dict(kwargs["metadata"])
        return msg.__class__(**kwargs)

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()

