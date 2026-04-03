from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanobot.bus.events import InboundMessage
from nanobot.command.fastlane import try_workspace_fastlane


def _msg(content: str) -> InboundMessage:
    return InboundMessage(
        channel="feishu",
        sender_id="user1",
        chat_id="ou_test",
        content=content,
        metadata={"message_id": "om_test"},
    )


def test_fastlane_returns_help_without_falling_back(tmp_path: Path) -> None:
    decision = MagicMock(
        stdout='{"kind":"help_fastlane","target":"model","content":"/model help text"}\n',
        stderr="",
        returncode=0,
    )

    with (
        patch("nanobot.command.fastlane.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.fastlane.subprocess.run", return_value=decision) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(try_workspace_fastlane(_msg("/help model"), "/help model"))

    assert result is not None
    assert result.content == "/model help text"
    assert result.metadata == {"render_as": "text"}
    assert mock_run.call_count == 1


def test_fastlane_executes_whitelisted_l4_script(tmp_path: Path) -> None:
    route_decision = MagicMock(
        stdout='{"kind":"exec_fastlane","command":"model","script":"models_cmd.py","args":["list"],"timeout":20}\n',
        stderr="",
        returncode=0,
    )
    script_result = MagicMock(stdout="model-a\nmodel-b\n", stderr="", returncode=0)

    with (
        patch("nanobot.command.fastlane.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch(
            "nanobot.command.fastlane.subprocess.run",
            side_effect=[route_decision, script_result],
        ) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(try_workspace_fastlane(_msg("/model list"), "/model list"))

    assert result is not None
    assert result.content == "model-a\nmodel-b"
    assert result.metadata == {"render_as": "text"}
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[1].args[0][-1] == "list"
    assert mock_run.call_args_list[1].args[0][-2].endswith("/models_cmd.py")


def test_fastlane_ignores_non_whitelisted_command(tmp_path: Path) -> None:
    decision = MagicMock(stdout='{"kind":"none"}\n', stderr="", returncode=0)

    with (
        patch("nanobot.command.fastlane.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.fastlane.subprocess.run", return_value=decision),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(try_workspace_fastlane(_msg("/merge"), "/merge"))

    assert result is None
