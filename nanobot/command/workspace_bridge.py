"""Workspace slash-command bridge.

Forward unknown slash commands to the workspace router script before they fall
through to the LLM.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext

WORKSPACE_ROUTER = Path.home() / ".nanobot" / "workspace" / "scripts" / "router.py"
BRIDGE_TIMEOUT_SECONDS = 25
PREPARED_INPUT_CMDS = {"小结", "simplify"}
POSTPROCESSABLE_AGENT_CMDS = {
    "plan",
    "plan-exec",
    "diagnose",
    "诊断",
    "simplify",
    "sync",
    "merge",
    "小结",
    "感悟",
}


def _extract_agent_marker(content: str) -> str | None:
    stripped = content.strip()
    if not stripped.startswith("[AGENT]"):
        return None
    return stripped[len("[AGENT]") :].strip()


def _prepare_agent_input(agent_cmd: str, raw: str, env: dict[str, str] | None) -> str | None:
    if agent_cmd not in PREPARED_INPUT_CMDS:
        return None
    try:
        result = subprocess.run(
            [
                str(Path.home() / ".nanobot" / "workspace" / "venv" / "bin" / "python"),
                str(WORKSPACE_ROUTER),
                "--prepare-agent-input",
                agent_cmd,
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=BRIDGE_TIMEOUT_SECONDS,
            env=env,
        )
    except Exception:
        return None

    prepared = (result.stdout or "").strip()
    return prepared or None


async def cmd_workspace_bridge(ctx: CommandContext) -> OutboundMessage | None:
    raw = (ctx.raw or "").strip()
    if not raw.startswith("/"):
        return None
    if not WORKSPACE_ROUTER.exists():
        return None

    env = None
    if ctx.msg is not None:
        env = {
            **os.environ.copy(),
            "NANOBOT_CHANNEL": ctx.msg.channel,
            "NANOBOT_CHAT_ID": ctx.msg.chat_id,
            "NANOBOT_MESSAGE_ID": ctx.msg.metadata.get("message_id", ""),
        }

    try:
        result = subprocess.run(
            [
                str(Path.home() / ".nanobot" / "workspace" / "venv" / "bin" / "python"),
                str(WORKSPACE_ROUTER),
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=BRIDGE_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"[workspace-router timeout] 命令执行超过 {int(exc.timeout)}s：{raw}",
            metadata={"render_as": "text"},
        )
    except Exception as exc:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"[workspace-router error] {exc}",
            metadata={"render_as": "text"},
        )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if stderr and not stdout:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"[workspace-router error] {stderr}",
            metadata={"render_as": "text"},
        )

    if not stdout or stdout == raw:
        return None

    agent_cmd = _extract_agent_marker(stdout)
    if agent_cmd:
        if agent_cmd == "plan" and raw == "/plan exec":
            agent_cmd = "plan-exec"
        if agent_cmd in POSTPROCESSABLE_AGENT_CMDS:
            ctx.msg.metadata["workspace_agent_cmd"] = agent_cmd
            if agent_cmd in {"plan", "plan-exec"}:
                ctx.msg.metadata["workspace_work_mode"] = (
                    "build" if agent_cmd == "plan-exec" else "plan"
                )
            prepared = _prepare_agent_input(agent_cmd, raw, env)
            if prepared:
                ctx.msg.metadata["workspace_agent_input"] = prepared
        return None

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=stdout,
        metadata={"render_as": "text"},
    )
