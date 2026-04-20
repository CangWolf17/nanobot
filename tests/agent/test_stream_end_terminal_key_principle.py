import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from tests.agent.test_loop_workspace_progress import _make_loop


@pytest.mark.asyncio
async def test_stream_end_metadata_propagates_terminal_key_principle(tmp_path):
    loop, bus = _make_loop(tmp_path)

    async def fake_process_message(msg, **_kwargs):
        return OutboundMessage(
            channel='feishu',
            chat_id='oc_chat1',
            content='正文',
            metadata={
                '_streamed': True,
                '_completion_notice': True,
                '_completion_notice_text': 'Key Principle：先收口，再扩展。',
                '_terminal_key_principle_text': 'Key Principle：先收口，再扩展。',
            },
        )

    loop._process_message = fake_process_message

    msg = InboundMessage(
        channel='feishu',
        sender_id='user1',
        chat_id='oc_chat1',
        content='吐个kp测一下',
        metadata={'_wants_stream': True},
    )

    await loop._dispatch(msg)

    outbound = []
    while not bus.outbound.empty():
        outbound.append(bus.outbound.get_nowait())

    stream_end = next(m for m in outbound if (m.metadata or {}).get('_stream_end'))
    assert stream_end.metadata['_terminal_key_principle_text'] == 'Key Principle：先收口，再扩展。'

    final_msg = [m for m in outbound if not (m.metadata or {}).get('_stream_end') and not (m.metadata or {}).get('_stream_start')][-1]
    assert final_msg.metadata['_completion_notice_text'] == 'Key Principle：先收口，再扩展。'
