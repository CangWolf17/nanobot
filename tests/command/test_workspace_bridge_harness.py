from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio

from nanobot.bus.events import InboundMessage
from nanobot.command.router import CommandContext
from nanobot.command.builtin import build_help_text
from nanobot.command.workspace_bridge import cmd_workspace_bridge


def _make_ctx(raw: str):
    loop = MagicMock()
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content=raw)
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=loop)


def test_workspace_bridge_ignores_harness_commands_for_runtime_handler(tmp_path: Path) -> None:
    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=AssertionError("workspace subprocess should not run for /harness"),
        ),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        ctx = _make_ctx("/harness auto")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata == {}


def test_build_help_text_includes_harness_command() -> None:
    assert "/harness" in build_help_text()
