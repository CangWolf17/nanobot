import time

from nanobot.channels.feishu import FeishuChannel


def test_feishu_stream_edit_interval_lowered_for_better_responsiveness():
    assert FeishuChannel._STREAM_EDIT_INTERVAL == 0.2
