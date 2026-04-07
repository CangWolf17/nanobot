import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import (
    _read_workspace_harness_status_summary,
    cmd_help,
    cmd_interrupt,
    register_builtin_commands,
)
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.harness.service import HarnessService


def _make_ctx(raw: str) -> CommandContext:
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content=raw)
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=None)


def test_runtime_harness_command_handles_harness_auto_without_workspace_subprocess() -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    ctx = _make_ctx("/harness auto")

    fake_service = SimpleNamespace(
        handle_command=lambda *args, **kwargs: SimpleNamespace(
            response_mode="agent",
            active_harness_id="har_0001",
            prepared_input="prepared harness auto input",
        )
    )

    with (
        patch("nanobot.command.harness.HarnessService.for_workspace", return_value=fake_service),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=AssertionError("workspace bridge subprocess should not run"),
        ),
    ):
        result = asyncio.run(router.dispatch(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "harness"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared harness auto input"
    assert ctx.msg.metadata["workspace_harness_id"] == "har_0001"
    assert ctx.msg.metadata["workspace_harness_auto"] is True


def test_runtime_harness_command_returns_text_response_for_status() -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    ctx = _make_ctx("/harness status")

    fake_service = SimpleNamespace(
        handle_command=lambda *args, **kwargs: SimpleNamespace(
            response_mode="text",
            text="Active harness: har_0001",
            prepared_input="",
        )
    )

    with patch("nanobot.command.harness.HarnessService.for_workspace", return_value=fake_service):
        result = asyncio.run(router.dispatch(ctx))

    assert result is not None
    assert result.content == "Active harness: har_0001"
    assert result.metadata == {"render_as": "text"}
    assert ctx.msg.metadata == {}


def test_runtime_harness_command_accepts_uppercase_prefix_for_status(tmp_path: Path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    ctx = _make_ctx("/HARNESS status")

    with patch("nanobot.command.harness.get_workspace_path", return_value=tmp_path):
        result = asyncio.run(router.dispatch(ctx))

    assert result is not None
    assert result.content == "No active harness."
    assert result.metadata == {"render_as": "text"}


def test_runtime_help_includes_harness_even_when_workspace_help_exists(tmp_path: Path) -> None:
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="/help")
    ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/help", loop=None)
    home = tmp_path / "home"
    router_path = home / ".nanobot" / "workspace" / "scripts" / "router.py"
    router_path.parent.mkdir(parents=True, exist_ok=True)
    router_path.write_text("#!/bin/sh\n", encoding="utf-8")

    with (
        patch("nanobot.command.builtin.Path.home", return_value=home),
        patch(
            "nanobot.command.builtin.subprocess.run",
            return_value=SimpleNamespace(
                stdout="/plan -- Workspace planner help\n/help -- Workspace help\n",
                stderr="",
            ),
        ),
    ):
        result = asyncio.run(cmd_help(ctx))

    assert result is not None
    assert "/plan -- Workspace planner help" in result.content
    assert "/harness" in result.content
    assert result.metadata == {"render_as": "text"}


def test_runtime_help_uses_loop_workspace_root_and_still_includes_harness(tmp_path: Path) -> None:
    workspace_root = tmp_path / "runtime-workspace"
    router_path = workspace_root / "scripts" / "router.py"
    router_path.parent.mkdir(parents=True, exist_ok=True)
    router_path.write_text("#!/bin/sh\n", encoding="utf-8")
    loop = MagicMock(workspace=workspace_root)
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="/help")
    ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/help", loop=loop)

    with patch(
        "nanobot.command.builtin.subprocess.run",
        return_value=SimpleNamespace(
            stdout="/plan -- Workspace planner help\n/help -- Workspace help\n",
            stderr="",
        ),
    ) as mock_run:
        result = asyncio.run(cmd_help(ctx))

    assert result is not None
    assert "/harness" in result.content
    assert mock_run.call_args.args[0] == [str(router_path)]


def test_status_summary_helper_reads_from_explicit_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "runtime-workspace"
    task_path = workspace_root / "TASK.md"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        "- status: interrupted\n- phase: planning\n- summary: waiting for redirect\n",
        encoding="utf-8",
    )

    result = asyncio.run(_read_workspace_harness_status_summary(workspace_root=workspace_root))

    assert result == "interrupted / planning — waiting for redirect"


def test_interrupt_command_updates_harness_using_explicit_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "runtime-workspace"
    service = HarnessService.for_workspace(workspace_root)
    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    loop = MagicMock(workspace=workspace_root)
    loop._active_tasks = {
        "feishu:c1": [
            MagicMock(done=MagicMock(return_value=False), cancel=MagicMock(return_value=True))
        ]
    }
    loop.subagents.cancel_by_session = AsyncMock(return_value=0)
    loop.sessions.get_or_create.return_value = MagicMock(metadata={})
    loop.sessions.save = MagicMock()
    loop.persist_interrupted_turn = MagicMock(return_value=None)
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="/interrupt")
    ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/interrupt", loop=loop)

    asyncio.run(cmd_interrupt(ctx))

    assert "interrupted" in service.render_status().lower()
