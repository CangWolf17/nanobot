"""Native runtime handler for /harness commands."""

from __future__ import annotations

from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext
from nanobot.config.paths import get_workspace_path
from nanobot.harness.service import HarnessService


async def cmd_harness(ctx: CommandContext) -> OutboundMessage | None:
    workspace = get_workspace_path(getattr(ctx.loop, "workspace", None))
    service = HarnessService.for_workspace(workspace)

    try:
        result = service.handle_command(
            ctx.raw,
            session_key=ctx.key,
            sender_id=ctx.msg.sender_id,
        )
    except ValueError as exc:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=str(exc),
            metadata={"render_as": "text"},
        )

    if result.response_mode == "text":
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=result.text,
            metadata={"render_as": "text"},
        )

    ctx.msg.metadata["workspace_agent_cmd"] = getattr(result, "agent_cmd", "harness")
    ctx.msg.metadata["workspace_agent_input"] = result.prepared_input
    if getattr(result, "active_harness_id", ""):
        ctx.msg.metadata["workspace_harness_id"] = result.active_harness_id
    if ctx.raw.strip().lower() == "/harness auto":
        ctx.msg.metadata["workspace_harness_auto"] = True
    return None
