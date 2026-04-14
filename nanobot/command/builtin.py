"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from time import time
import re

from nanobot import __version__
from nanobot.bus.events import OutboundMessage
from nanobot.command.harness import cmd_harness
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.command.workspace_bridge import cmd_workspace_bridge
from nanobot.config.paths import get_workspace_path
from nanobot.harness.service import HarnessService
from nanobot.utils.helpers import build_status_content
from nanobot.utils.restart import set_restart_notice_to_env


def _resolve_workspace_root(loop: object | None = None) -> Path:
    """Resolve the runtime workspace root, preferring loop configuration."""
    workspace = getattr(loop, "workspace", None) if loop is not None else None
    if workspace:
        return get_workspace_path(workspace)
    return Path.home() / ".nanobot" / "workspace"


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
) -> str | None:
    workspace_root = workspace_root or _resolve_workspace_root()
    summary = HarnessService.for_workspace(workspace_root).render_status_summary()
    if summary is not None:
        return summary

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
    _, _, total = await _cancel_session_work(ctx)
    msg = ctx.msg
    loop = ctx.loop
    if hasattr(loop, "_discard_inflight_turn"):
        loop._discard_inflight_turn(msg.session_key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv."""
    msg = ctx.msg
    set_restart_notice_to_env(channel=msg.channel, chat_id=msg.chat_id)

    async def _do_restart():
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "nanobot"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_interrupt(ctx: CommandContext) -> OutboundMessage:
    """Interrupt current execution while preserving session context."""
    _, _, total = await _cancel_session_work(ctx)
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


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    try:
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    except Exception:
        pass
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)
    
    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    try:
        from nanobot.utils.searchusage import fetch_search_usage
        web_cfg = getattr(loop, "web_config", None)
        search_cfg = getattr(web_cfg, "search", None) if web_cfg else None
        if search_cfg is not None:
            provider = getattr(search_cfg, "provider", "duckduckgo")
            api_key = getattr(search_cfg, "api_key", "") or None
            usage = await fetch_search_usage(provider=provider, api_key=api_key)
            search_usage_text = usage.format()
    except Exception:
        pass  # Never let usage fetch break /status
    interrupt_state = session.metadata.get("interrupt_state") or {}
    interrupt_summary = str(interrupt_state.get("summary") or "").strip() or None
    harness_summary = await _read_workspace_harness_status_summary(
        workspace_root=_resolve_workspace_root(loop)
    )
    content = build_status_content(
        version=__version__,
        model=loop.model,
        start_time=loop._start_time,
        last_usage=loop._last_usage,
        context_window_tokens=loop.context_window_tokens,
        session_msg_count=len(session.get_history(max_messages=0)),
        context_tokens_estimate=ctx_est,
        search_usage_text=search_usage_text,
    )
    effective_interrupt_summary = harness_summary or interrupt_summary
    if effective_interrupt_summary:
        content = f"{content}\n⚡ Interrupt: {effective_interrupt_summary}"
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Start a fresh session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    session.metadata.pop("interrupt_state", None)
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.consolidator.archive(snapshot))
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        metadata=dict(ctx.msg.metadata or {})
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    async def _run_dream():
        t0 = time.monotonic()
        try:
            did_work = await loop.dream.run()
            elapsed = time.monotonic() - t0
            if did_work:
                content = f"Dream completed in {elapsed:.1f}s."
            else:
                content = "Dream: nothing to process."
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


def _format_dream_log_content(commit, diff: str, *, requested_sha: str | None = None) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.message.splitlines()[0]}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest commit (HEAD~1 vs HEAD).
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle."
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest commit's diff
        commits = git.log(max_entries=1)
        result = git.show_commit_diff(commits[0].sha) if commits else None
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = "Dream memory has no saved versions yet."

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent commits for the user to pick
        commits = git.log(max_entries=10)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        changed_files = _format_changed_files(result[1]) if result else "the tracked memory files"
        new_sha = git.revert(sha)
        if new_sha:
            content = (
                f"Restored Dream memory to the state before `{sha}`.\n\n"
                f"- New safety commit: `{new_sha}`\n"
                f"- Restored files: {changed_files}\n\n"
                f"Use `/dream-log {new_sha}` to inspect the restore diff."
            )
        else:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "It may not exist, or it may be the first saved version with no earlier state to restore."
            )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
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
                    metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
                )
        except Exception:
            pass

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _runtime_help_lines() -> list[str]:
    return [
        "🐈 nanobot commands:",
        "/new — Start a new conversation",
        "/stop — Stop the current task",
        "/interrupt — Interrupt current execution and keep context",
        "/restart — Restart the bot",
        "/status — Show bot status",
        "/harness — Manage the runtime harness",
        "/dream — Manually trigger Dream consolidation",
        "/dream-log — Show what the last Dream changed",
        "/dream-restore — Revert memory to a previous state",
        "/help — Show available commands",
    ]


def _merge_runtime_help_text(workspace_help: str) -> str:
    """Append runtime-owned commands when workspace help omits them."""
    text = workspace_help.strip()
    if not text:
        return build_help_text()
    existing_commands = {
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"^(/\S+)", line.strip()))
    }
    merged = [text]
    for line in _runtime_help_lines()[1:]:
        command = line.split(" ", 1)[0]
        if command not in existing_commands:
            merged.append(line)
    return "\n".join(merged)


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    return "\n".join(_runtime_help_lines())


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/interrupt", cmd_interrupt)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/harness", cmd_harness)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/help", cmd_help)
    router.prefix("/harness ", cmd_harness)
    router.intercept(cmd_workspace_bridge)
