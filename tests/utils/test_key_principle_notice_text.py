import pytest

from nanobot.utils.key_principle import normalize_key_principle_notice_text


def test_normalize_key_principle_notice_text_strips_key_principle_label_and_bold_wrapper() -> None:
    assert (
        normalize_key_principle_notice_text(
            "**Key Principle：先确认消息真的进 Codex，再修 Codex 自己为什么不产出。**"
        )
        == "先确认消息真的进 Codex，再修 Codex 自己为什么不产出。"
    )


def test_normalize_key_principle_notice_text_strips_kp_label_variants() -> None:
    assert normalize_key_principle_notice_text("**KP：** 先收口，再扩展。") == "先收口，再扩展。"
    assert normalize_key_principle_notice_text("KP: keep the boundary clean.") == "keep the boundary clean."


def test_normalize_key_principle_notice_text_keeps_plain_notice_text() -> None:
    assert normalize_key_principle_notice_text("✅ 回复完成") == "✅ 回复完成"


def test_normalize_key_principle_notice_text_preserves_inner_bold_markdown() -> None:
    assert (
        normalize_key_principle_notice_text(
            "**Key Principle：** 先确认主链路，再修 **渲染**。"
        )
        == "先确认主链路，再修 **渲染**。"
    )
