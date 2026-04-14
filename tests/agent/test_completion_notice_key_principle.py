import pytest

from nanobot.agent.loop import AgentLoop


def test_extract_terminal_key_principle_plain_text() -> None:
    body, kp = AgentLoop._extract_terminal_key_principle(
        "方案如下。\n\nKey Principle：先收口，再扩展。"
    )

    assert body == "方案如下。"
    assert kp == "Key Principle：先收口，再扩展。"


def test_extract_terminal_key_principle_markdown_bold() -> None:
    body, kp = AgentLoop._extract_terminal_key_principle(
        "方案如下。\n\n**Key Principle:** keep the boundary clean."
    )

    assert body == "方案如下。"
    assert kp == "**Key Principle:** keep the boundary clean."


def test_extract_terminal_key_principle_bold_with_fullwidth_colon() -> None:
    body, kp = AgentLoop._extract_terminal_key_principle(
        "方案如下。\n\n**Key Principle：** 先保证主链路正确，再做体验优化。"
    )

    assert body == "方案如下。"
    assert kp == "**Key Principle：** 先保证主链路正确，再做体验优化。"


def test_extract_terminal_key_principle_single_newline_prefix() -> None:
    body, kp = AgentLoop._extract_terminal_key_principle(
        "方案如下。\nKey Principle：先保证主链路正确，再做体验优化。"
    )

    assert body == "方案如下。"
    assert kp == "Key Principle：先保证主链路正确，再做体验优化。"


def test_extract_terminal_key_principle_multiline_body_after_label() -> None:
    body, kp = AgentLoop._extract_terminal_key_principle(
        "方案如下。\n\nKey Principle：\n先保证主链路正确，再做体验优化。"
    )

    assert body == "方案如下。"
    assert kp == "Key Principle：\n先保证主链路正确，再做体验优化。"


def test_extract_terminal_key_principle_ignores_mid_body_occurrence() -> None:
    text = "Key Principle：这是正文里的例子，不是结尾。\n下一段还在继续。"
    body, kp = AgentLoop._extract_terminal_key_principle(text)

    assert body == text
    assert kp is None
