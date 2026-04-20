from pathlib import Path
from types import SimpleNamespace

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage


class _FakeBus:
    async def publish_outbound(self, _msg):
        return None

    async def consume_inbound(self):
        raise RuntimeError('unused')


def test_stream_completion_notice_preserves_existing_completion_text(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=_FakeBus(),
        provider=type('P', (), {'get_default_model': lambda self: 'x', 'generation': type('G', (), {'max_tokens': 1024})()})(),
        workspace=tmp_path,
        channels_config=SimpleNamespace(
            feishu=SimpleNamespace(
                streaming_completion_notice_enabled=True,
                streaming_completion_notice_text='✅ 回复完成',
                streaming_completion_notice_mention_user=True,
            )
        ),
    )
    msg = InboundMessage(channel='feishu', sender_id='user1', chat_id='c1', content='x', metadata={})
    response = OutboundMessage(
        channel='feishu',
        chat_id='c1',
        content='正文',
        metadata={'_streamed': True, '_completion_notice_text': 'Key Principle：先收口，再扩展。'},
    )

    loop._maybe_mark_stream_completion_notice(
        msg,
        response,
        stream_started_at=0.0,
        stream_finished_at=3.0,
        stream_chunk_count=10,
        stream_char_count=200,
    )

    assert response.metadata['_completion_notice'] is True
    assert response.metadata['_completion_notice_text'] == 'Key Principle：先收口，再扩展。'
    assert response.metadata['_completion_notice_mention_user_id'] == 'user1'
