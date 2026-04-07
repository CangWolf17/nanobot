import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import register_builtin_commands
from nanobot.command.router import CommandContext, CommandRouter


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
