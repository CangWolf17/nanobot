"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from time import time

from nanobot import __version__
from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.command.workspace_bridge import cmd_workspace_bridge
from nanobot.utils.helpers import build_status_content


def build_help_text() -> str:
    """Shared fallback help text for runtime builtin surfaces."""
    lines = [
        "🐈 nanobot commands:",
        "/new — Start a new conversation",
        "/stop — Stop the current task",
        "/interrupt — Interrupt current execution and keep context",
        "/restart — Restart the bot",
        "/status — Show bot status",
        "/help — Show available commands",
    ]
    return "\n".join(lines)


async def _cancel_session_work(ctx: CommandContext) -> tuple[int, int, int]:
    """Cancel active loop tasks and subagents for the current session."""
    loop = ctx.loop
    msg = ctx.msg
    tasks = loop._active_tasks.pop(msg.session_key, [])
    cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    sub_cancelled = await loop.subagents.cancel_by_session(msg.session_key)
    total = cancelled + sub_cancelled
    return cancelled, sub_cancelled, total


async def _sync_workspace_interrupt_harness(summary: str) -> None:
    workspace_root = Path.home() / ".nanobot" / "workspace"
    router_path = workspace_root / "scripts" / "router.py"
    python_path = workspace_root / "venv" / "bin" / "python"
    if not router_path.exists() or not python_path.exists():
        return
    try:
        subprocess.run(
            [str(python_path), str(router_path), "--interrupt-harness", summary],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10,
        )
    except Exception:
        return


async def _read_workspace_harness_status_summary() -> str | None:
    workspace_root = Path.home() / ".nanobot" / "workspace"
    task_path = workspace_root / "TASK.md"
    if not task_path.exists():
        return None
    try:
        text = task_path.read_text(encoding="utf-8")
    except Exception:
        return None

    lines = [line.rstrip() for line in text.splitlines()]
    status = None
    phase = None
    summary = None
    for line in lines:
        if line.startswith("- status: "):
            status = line.split(": ", 1)[1].strip()
        elif line.startswith("- phase: "):
            phase = line.split(": ", 1)[1].strip()
        elif line.startswith("- summary: "):
            summary = line.split(": ", 1)[1].strip()
    if not status:
        return None
    status_l = status.lower()
    phase_l = (phase or "").lower()
    if status_l not in {"interrupted", "planning", "active", "awaiting_decision", "blocked"}:
        return None
    if status_l == "active" and phase_l != "executing":
        return None
    if status_l == "planning" and phase_l != "planning":
        return None
    if summary:
        return f"{status} / {phase or '-'} — {summary}" if phase else f"{status} — {summary}"
    return f"{status} / {phase}" if phase else status


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    _cancelled, _sub_cancelled, total = await _cancel_session_work(ctx)
    msg = ctx.msg
    loop = ctx.loop
    if hasattr(loop, "_discard_inflight_turn"):
        loop._discard_inflight_turn(msg.session_key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)


async def cmd_interrupt(ctx: CommandContext) -> OutboundMessage:
    """Interrupt current execution while preserving session context."""
    _cancelled, _sub_cancelled, total = await _cancel_session_work(ctx)
    msg = ctx.msg
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    if total:
        preserved = None
        if hasattr(loop, "persist_interrupted_turn"):
            preserved = loop.persist_interrupted_turn(session, msg.session_key)
        summary = "interrupted — waiting for redirect"
        await _sync_workspace_interrupt_harness(summary)
        interrupt_state = {
            "status": "interrupted",
            "reason": "user_interrupt",
            "session_key": msg.session_key,
            "interrupted_at": time(),
            "summary": summary,
        }
        if isinstance(preserved, dict):
            partial_preview = str(preserved.get("assistant_partial") or "").strip()
            user_content = str(preserved.get("content") or "").strip()
            if user_content:
                interrupt_state["preserved_user_content"] = user_content
            if partial_preview:
                interrupt_state["partial_assistant_preview"] = partial_preview[:1000]
        session.metadata["interrupt_state"] = interrupt_state
        loop.sessions.save(session)
    content = (
        f"Interrupted {total} task(s). Context preserved — tell me how to redirect."
        if total
        else "No active task to interrupt."
    )
    return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv."""
    msg = ctx.msg

    async def _do_restart():
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "nanobot"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
    return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="Restarting...")


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    try:
        ctx_est, _ = loop.memory_consolidator.estimate_session_prompt_tokens(session)
    except Exception:
        pass
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)
    interrupt_state = session.metadata.get("interrupt_state") or {}
    interrupt_summary = str(interrupt_state.get("summary") or "").strip() or None
    harness_summary = await _read_workspace_harness_status_summary()
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__,
            model=loop.model,
            start_time=loop._start_time,
            last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            interrupt_summary=interrupt_summary,
            harness_summary=harness_summary,
        ),
        metadata={"render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Start a fresh session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated :]
    session.clear()
    session.metadata.pop("interrupt_state", None)
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.memory_consolidator.archive_messages(snapshot))
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="New session started.",
    )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands, preferring workspace help when available."""
    router_path = Path.home() / ".nanobot" / "workspace" / "scripts" / "router.py"
    if router_path.exists():
        try:
            result = subprocess.run(
                [str(router_path)],
                input="/help",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5,
            )
            stdout = (result.stdout or "").strip()
            if stdout and not stdout.startswith("[AGENT]"):
                return OutboundMessage(
                    channel=ctx.msg.channel,
                    chat_id=ctx.msg.chat_id,
                    content=stdout,
                    metadata={"render_as": "text"},
                )
        except Exception:
            pass

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={"render_as": "text"},
    )


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/interrupt", cmd_interrupt)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/help", cmd_help)
    router.intercept(cmd_workspace_bridge)
