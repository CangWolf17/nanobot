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
from nanobot.command.harness import cmd_harness
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.command.workspace_bridge import cmd_workspace_bridge
from nanobot.config.paths import get_workspace_path
from nanobot.harness.service import HarnessService
from nanobot.utils.helpers import build_status_content
from nanobot.utils.restart import set_restart_notice_to_env


def build_help_text() -> str:
    """Shared fallback help text for runtime builtin surfaces."""
    lines = [
        "🐈 nanobot commands:",
        "/new — Start a new conversation",
        "/stop — Stop the current task",
        "/interrupt — Interrupt current execution and keep context",
        "/restart — Restart the bot",
        "/status — Show bot status",
        "/dream — Run Dream memory consolidation now",
        "/dream-log — Inspect the latest Dream memory change",
        "/dream-restore — List or restore Dream memory versions",
        "/harness — Manage the runtime harness",
        "/help — Show available commands",
    ]
    return "\n".join(lines)


def _merge_runtime_help_text(workspace_help: str) -> str:
    """Append runtime-only commands when workspace help omits them."""
    text = workspace_help.strip()
    if not text:
        return build_help_text()
    if "/harness" in text:
        return text
    return f"{text}\n/harness — Manage the runtime harness"


def _resolve_workspace_root(loop: object | None = None) -> Path:
    """Resolve the runtime workspace root, preferring loop configuration."""
    return get_workspace_path(getattr(loop, "workspace", None) if loop is not None else None)


def _clear_workflow_state(loop: object, session_key: str) -> None:
    """Clear session-scoped workflow state on /new."""
    try:
        from nanobot.utils.helpers import safe_filename
        safe_key = safe_filename(session_key)
        workspace = _resolve_workspace_root(loop)
        state_path = workspace / "sessions" / safe_key / "workflow_state.json"
        if state_path.exists():
            state_path.unlink()
    except Exception:
        pass


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


async def _read_workspace_harness_status_summary(
    *,
    workspace_root: Path | None = None,
    session_key: str = "",
) -> str | None:
    workspace_root = workspace_root or _resolve_workspace_root()
    service = HarnessService.for_workspace(workspace_root)
    bound_session_key = session_key.strip()
    if bound_session_key:
        summary = service.render_status_summary_for_session(bound_session_key)
    else:
        summary = service.render_status_summary()
    if summary is not None:
        return summary
    if bound_session_key:
        return None

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
    task_summary = None
    for line in lines:
        if line.startswith("- status: "):
            status = line.split(": ", 1)[1].strip()
        elif line.startswith("- phase: "):
            phase = line.split(": ", 1)[1].strip()
        elif line.startswith("- summary: "):
            task_summary = line.split(": ", 1)[1].strip()
    if not status:
        return None
    if task_summary:
        return (
            f"{status} / {phase or '-'} — {task_summary}" if phase else f"{status} — {task_summary}"
        )
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
        service = HarnessService.for_workspace(_resolve_workspace_root(loop))
        runtime_meta = service.runtime_metadata(session_key=msg.session_key)
        harness_id = ""
        active_harness = (
            runtime_meta.get("active_harness") if isinstance(runtime_meta, dict) else None
        )
        if isinstance(active_harness, dict):
            harness_id = str(active_harness.get("id") or "").strip()
        service.interrupt_active(summary, session_key=msg.session_key, harness_id=harness_id)
        interrupt_state = {
            "status": "interrupted",
            "reason": "user_interrupt",
            "session_key": msg.session_key,
            "interrupted_at": time(),
            "summary": summary,
        }
        if harness_id:
            interrupt_state["workspace_harness_id"] = harness_id
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
    set_restart_notice_to_env(channel=msg.channel, chat_id=msg.chat_id)

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
    for consolidator in (
        getattr(loop, "consolidator", None),
        getattr(loop, "memory_consolidator", None),
    ):
        if consolidator is None:
            continue
        try:
            ctx_est, _ = consolidator.estimate_session_prompt_tokens(session)
        except Exception:
            continue
        if isinstance(ctx_est, int) and ctx_est > 0:
            break
        ctx_est = 0
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)
    metadata = session.metadata if isinstance(getattr(session, "metadata", None), dict) else {}
    interrupt_state = metadata.get("interrupt_state") or {}
    interrupt_summary = str(interrupt_state.get("summary") or "").strip() or None
    harness_summary = await _read_workspace_harness_status_summary(
        workspace_root=_resolve_workspace_root(loop),
        session_key=ctx.msg.session_key,
    )
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
    msg = ctx.msg
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated :]
    session.clear()
    session.metadata.pop("interrupt_state", None)
    HarnessService.for_workspace(_resolve_workspace_root(loop)).clear_session_binding(
        msg.session_key
    )
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)

    # Clear session-scoped workflow states on /new
    _clear_workflow_state(loop, msg.session_key)
    if snapshot:
        archive = getattr(getattr(loop, "consolidator", None), "archive", None)
        if archive is None:
            archive = loop.memory_consolidator.archive_messages
        loop._schedule_background(archive(snapshot))
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content="New session started.",
    )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands, preferring workspace help when available."""
    router_path = _resolve_workspace_root(ctx.loop) / "scripts" / "router.py"
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
                    content=_merge_runtime_help_text(stdout),
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


def _get_dream_store(ctx: CommandContext):
    loop = ctx.loop
    consolidator = getattr(loop, "consolidator", None) or getattr(loop, "memory_consolidator", None)
    return getattr(consolidator, "store", None)


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Run Dream immediately for the current workspace."""
    loop = ctx.loop
    if loop is None:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Dream is unavailable in this runtime.",
        )

    store = _get_dream_store(ctx)
    if store is None:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Dream memory store is unavailable in this runtime.",
        )

    from nanobot.agent.memory import Dream

    provider = getattr(loop, "archive_provider", None) or getattr(loop, "provider", None)
    model = getattr(loop, "archive_model", None) or getattr(loop, "model", None)
    max_batch_size = getattr(getattr(loop, "memory_config", None), "max_batch_size", None)
    max_iterations = getattr(getattr(loop, "memory_config", None), "max_iterations", None)

    dream = Dream(
        store=store,
        provider=provider,
        model=model,
        max_batch_size=int(max_batch_size or 20),
        max_iterations=int(max_iterations or 10),
    )
    changed = await dream.run()
    content = (
        "Dream ran successfully and updated durable memory."
        if changed
        else "Dream had nothing new to consolidate."
    )
    return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=content)


def _extract_changed_files(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            path = parts[2][2:]
            if path not in files:
                files.append(path)
    return files


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show the latest Dream commit or a specific Dream diff."""
    store = _get_dream_store(ctx)
    git = getattr(store, "git", None)
    sha = str(ctx.args or "").strip()

    if git is None or not git.is_initialized():
        if store is not None and int(getattr(store, "get_last_dream_cursor", lambda: 0)() or 0) <= 0:
            content = "Dream has not run yet. Run `/dream` after new archive entries exist."
        else:
            content = "Dream change history is not available yet."
        return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=content)

    latest = git.log(max_entries=1)
    target = sha or (latest[0].sha if latest else "")
    if not target:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Dream has not recorded any memory changes yet.",
        )

    shown = git.show_commit_diff(target)
    if shown is None:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"Couldn't find Dream change `{target}`. Use `/dream-restore` to list recent versions.",
        )

    commit, diff = shown
    changed_files = _extract_changed_files(diff)
    files_line = ", ".join(f"`{path}`" for path in changed_files) if changed_files else "(no file changes)"
    content = "\n".join(
        [
            "## Dream Update",
            "Here is the latest Dream memory change." if not sha else f"Here is Dream change `{commit.sha}`.",
            f"- Commit: `{commit.sha}`",
            f"- Timestamp: {commit.timestamp}",
            f"- Changed files: {files_line}",
            f"- Message: {commit.message}",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff or "(no diff available)",
            "```",
        ]
    )
    return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=content)


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """List Dream versions or restore to the state before a selected commit."""
    store = _get_dream_store(ctx)
    git = getattr(store, "git", None)
    sha = str(ctx.args or "").strip()

    if git is None or not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Dream restore history is not available yet.",
        )

    if not sha:
        commits = git.log(max_entries=20)
        if not commits:
            content = "## Dream Restore\nNo Dream memory versions are available yet."
        else:
            lines = [
                "## Dream Restore",
                "Choose a Dream memory version to restore.",
                "",
            ]
            lines.extend(
                f"`{commit.sha}` {commit.timestamp} - {commit.message}" for commit in commits
            )
            lines.extend(
                [
                    "",
                    "Preview a version with `/dream-log <sha>`.",
                    "Restore a version with `/dream-restore <sha>`.",
                ]
            )
            content = "\n".join(lines)
        return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=content)

    shown = git.show_commit_diff(sha)
    if shown is None:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"Couldn't find Dream change `{sha}`. Use `/dream-restore` to list recent versions.",
        )
    commit, diff = shown
    new_sha = git.revert(commit.sha)
    if not new_sha:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"Dream restore for `{commit.sha}` failed. Please inspect the memory git store manually.",
        )

    changed_files = _extract_changed_files(diff)
    files_line = ", ".join(f"`{path}`" for path in changed_files) if changed_files else "(no file changes)"
    content = "\n".join(
        [
            f"Restored Dream memory to the state before `{commit.sha}`.",
            f"- New safety commit: `{new_sha}`",
            f"- Restored files: {files_line}",
            f"Use `/dream-log {new_sha}` to inspect the restore diff.",
        ]
    )
    return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=content)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/interrupt", cmd_interrupt)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/harness", cmd_harness)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/help", cmd_help)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/harness ", cmd_harness)
    router.prefix("/dream ", cmd_dream)
    router.prefix("/dream-log ", cmd_dream_log)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.intercept(cmd_workspace_bridge)
