import pytest
from unittest.mock import patch

from nanobot.channels.feishu import _FeishuStreamBuf
from tests.channels.test_feishu_streaming import _make_channel


@pytest.mark.asyncio
async def test_stream_end_trims_terminal_key_principle_from_final_card_text():
    ch = _make_channel()
    ch._stream_bufs['oc_chat1'] = _FeishuStreamBuf(
        text='方案如下。\n\nKey Principle：先收口，再扩展。',
        card_id='card_1',
        sequence=2,
        last_edit=0.0,
    )

    with (
        patch.object(ch, '_stream_update_text_sync', return_value=True) as mock_update,
        patch.object(ch, '_close_streaming_mode_sync', return_value=True),
    ):
        await ch.send_delta(
            'oc_chat1',
            '',
            metadata={'_stream_end': True, '_terminal_key_principle_text': 'Key Principle：先收口，再扩展。'},
        )

    mock_update.assert_called_once_with('card_1', '方案如下。', 3)


def test_trim_terminal_key_principle_removes_exact_suffix_only():
    ch = _make_channel()
    text = '方案如下。\n\nKey Principle：先收口，再扩展。'
    trimmed = ch._trim_terminal_key_principle(text, 'Key Principle：先收口，再扩展。')
    assert trimmed == '方案如下。'

    untouched = ch._trim_terminal_key_principle('正文里提到 Key Principle：例子', 'Key Principle：先收口，再扩展。')
    assert untouched == '正文里提到 Key Principle：例子'


def test_trim_terminal_key_principle_removes_tail_block_even_when_metadata_format_differs():
    ch = _make_channel()
    text = '方案如下。\n\n**Key Principle**：先收口，再扩展。\n\n'

    trimmed = ch._trim_terminal_key_principle(text, 'Key Principle：先收口，再扩展。')

    assert trimmed == '方案如下。'
