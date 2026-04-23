"""Runtime-specific helpers for built-in slash commands."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from time import time

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.command.harness import cmd_harness
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.command.workspace_bridge import cmd_workspace_bridge
from nanobot.config.paths import get_workspace_path
from nanobot.harness.service import HarnessService

UNIFIED_SESSION_KEY = "unified:default"


def build_help_text() -> str:
    """Shared fallback help text for runtime builtin surfaces."""
    lines = [
        "🐈 nanobot commands:",
        "/new — Start a new conversation",
        "/stop — Stop the current task",
        "/interrupt — Interrupt current execution and keep context",
        "/restart — Restart the bot",
        "/status — Show bot status",
        "/dream — Manually trigger Dream consolidation",
        "/dream-log — Show what the last Dream changed",
        "/dream-restore — Revert memory to a previous state",
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


def _workspace_router_is_trusted(workspace_root: Path, router_path: Path) -> bool:
    """Allow workspace router execution only for a trusted local workspace file."""
    try:
        resolved_workspace = workspace_root.resolve()
        resolved_router = router_path.resolve()
        if not resolved_router.is_relative_to(resolved_workspace):
            return False
        if not router_path.is_file() or router_path.is_symlink():
            return False
        return True
    except Exception:
        return False


async def cancel_session_work(ctx: CommandContext) -> tuple[int, int, int]:
    """Cancel active loop tasks and subagents for the current session."""
    loop = ctx.loop
    session_key = ctx.key
    tasks = loop._active_tasks.pop(session_key, [])
    cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    sub_cancelled = await loop.subagents.cancel_by_session(session_key)
    return cancelled, sub_cancelled, cancelled + sub_cancelled


async def stop_session_work(ctx: CommandContext) -> int:
    """Cancel current work and clear any inflight turn snapshot."""
    _cancelled, _sub_cancelled, total = await cancel_session_work(ctx)
    loop = ctx.loop
    if hasattr(loop, "_discard_inflight_turn"):
        loop._discard_inflight_turn(ctx.key)
    return total


async def read_workspace_harness_status_summary(
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
        return f"{status} / {phase or '-'} — {task_summary}" if phase else f"{status} — {task_summary}"
    return f"{status} / {phase}" if phase else status


async def build_runtime_status_kwargs(loop: object, session: object, session_key: str) -> dict[str, str | None]:
    """Collect runtime-only fields appended to /status output."""
    metadata = session.metadata if isinstance(getattr(session, "metadata", None), dict) else {}
    interrupt_state = metadata.get("interrupt_state") or {}
    interrupt_summary = str(interrupt_state.get("summary") or "").strip() or None
    harness_summary = await read_workspace_harness_status_summary(
        workspace_root=_resolve_workspace_root(loop),
        session_key=session_key,
    )
    return {
        "interrupt_summary": interrupt_summary,
        "harness_summary": harness_summary,
    }


def clear_runtime_session_state(*, loop: object, session: object, session_key: str) -> None:
    """Clear runtime-only session bindings during /new."""
    metadata = getattr(session, "metadata", None)
    if isinstance(metadata, dict):
        metadata.pop("interrupt_state", None)
    HarnessService.for_workspace(_resolve_workspace_root(loop)).clear_session_binding(session_key)


def schedule_session_archive(loop: object, snapshot: list[dict]) -> None:
    """Archive a cleared session snapshot across consolidator implementations."""
    if not snapshot:
        return
    archive = getattr(getattr(loop, "consolidator", None), "archive", None)
    if archive is None:
        archive = loop.memory_consolidator.archive_messages
    loop._schedule_background(archive(snapshot))


async def cmd_interrupt(ctx: CommandContext) -> OutboundMessage:
    """Interrupt current execution. If queue has work, auto-dispatch it after cancel."""
    msg = ctx.msg
    loop = ctx.loop
    effective_key = loop._unified_session and UNIFIED_SESSION_KEY or ctx.key

    # Cancel active work
    cancelled, sub_cancelled, total = await cancel_session_work(ctx)

    # Check queue state
    has_normal = loop.coordinator.has_normal_queued_work(effective_key, loop._unified_session)
    has_turn = loop.coordinator.has_turn_slot(effective_key, loop._unified_session)

    if not has_normal and not has_turn:
        # No queue — legacy behavior
        session = ctx.session or loop.sessions.get_or_create(ctx.key)
        if total:
            preserved = None
            if hasattr(loop, "persist_interrupted_turn"):
                preserved = loop.persist_interrupted_turn(session, ctx.key)
            summary = "interrupted — waiting for redirect"
            service = HarnessService.for_workspace(_resolve_workspace_root(loop))
            runtime_meta = service.runtime_metadata(session_key=ctx.key)
            harness_id = ""
            active_harness = runtime_meta.get("active_harness") if isinstance(runtime_meta, dict) else None
            if isinstance(active_harness, dict):
                harness_id = str(active_harness.get("id") or "").strip()
            service.interrupt_active(summary, session_key=ctx.key, harness_id=harness_id)
            interrupt_state = {
                "status": "interrupted",
                "reason": "user_interrupt",
                "session_key": ctx.key,
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
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={**dict(msg.metadata or {}), "render_as": "interactive"},
        )

    # Queue has work — assemble and auto-dispatch in spec order:
    # batched normal queue first, reserved /tq last.
    synthesized_msg = None
    consumed = []
    metrics = None
    if has_normal:
        consumed, metrics = loop.coordinator.consume_normal_batch(
            effective_key, loop._unified_session, max_items=10, max_chars=8000
        )
    turn_item = loop.coordinator.consume_turn_slot(effective_key, loop._unified_session) if has_turn else None

    if consumed or turn_item:
        parts = [item.content for item in consumed]
        if turn_item:
            parts.append(turn_item.content)

        first = consumed[0] if consumed else turn_item
        assert first is not None
        queued_batch = {
            "kind": "interrupt_queue" if turn_item and consumed else ("queued_turn" if turn_item else "queued_batch"),
            "item_count": len(consumed) + (1 if turn_item else 0),
            "provenance_ids": [item.provenance_id for item in consumed],
        }
        if metrics is not None:
            queued_batch["char_count"] = metrics.last_char_count
            if metrics.dropped_reason:
                queued_batch["dropped_reason"] = metrics.dropped_reason
        if turn_item:
            queued_batch["turn_id"] = turn_item.provenance_id
            queued_batch["turn_metadata"] = dict(turn_item.metadata or {})

        synthesized_msg = InboundMessage(
            channel=first.channel,
            sender_id=first.sender_id,
            chat_id=first.chat_id,
            content="\n---\n".join(parts),
            metadata={
                "_queued_batch": queued_batch,
                "_from_interrupt": True,
                **({"_queued_turn": True} if turn_item and not consumed else {}),
            },
        )

    if synthesized_msg:
        # Auto-dispatch the synthesized message as tracked session work.
        loop._spawn_dispatch_task(synthesized_msg, effective_key=effective_key)
        content = f"Interrupted {total} task(s) and queued next message."
    else:
        content = f"Interrupted {total} task(s). Context preserved — tell me how to redirect."

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=content,
        metadata={**dict(msg.metadata or {}), "render_as": "interactive"},
    )


async def cmd_tq(ctx: CommandContext) -> OutboundMessage:
    """
    /tq <message> — queue a message for the next turn slot.
    /turnqueue <message> — alias.

    One slot only. Second /tq while slot occupied → reject.
    Idle session → immediately starts next turn.
    """
    msg = ctx.msg
    raw = ctx.raw

    # Extract content after /tq or /turnqueue
    if raw.startswith("/tq "):
        content = raw[4:]
    elif raw.startswith("/turnqueue "):
        content = raw[11:]
    elif raw in ("/tq", "/turnqueue"):
        content = ""
    else:
        content = ""

    if not content.strip():
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Usage: /tq <message> — queues your message for the next turn\n/turnqueue is an alias for /tq.",
            metadata={**dict(msg.metadata or {}), "render_as": "text"},
        )

    loop = ctx.loop
    effective_key = (
        UNIFIED_SESSION_KEY if loop._unified_session and not msg.session_key_override else msg.session_key
    )

    # Check if session is idle
    active_tasks = loop._active_tasks.get(effective_key, [])
    has_active = any(not t.done() for t in active_tasks) if active_tasks else False

    if not has_active:
        # Idle: immediately dispatch — rewrite message to just content and let _dispatch handle it
        new_msg = InboundMessage(
            channel=msg.channel,
            sender_id=msg.sender_id,
            chat_id=msg.chat_id,
            content=content,
            metadata={**dict(msg.metadata or {}), "_tq_turn": True},
        )
        loop._spawn_dispatch_task(new_msg, effective_key=effective_key)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Queued for immediate turn.",
            metadata={**dict(msg.metadata or {}), "render_as": "interactive"},
        )

    # Active: reserve turn slot
    reserved = loop.coordinator.reserve_turn_slot(
        content=content,
        channel=msg.channel,
        chat_id=msg.chat_id,
        sender_id=msg.sender_id,
        session_key=effective_key,
        unified=loop._unified_session,
        metadata={"raw": raw, "_tq_turn": True},
    )
    if reserved:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Queued for the next turn.",
            metadata={**dict(msg.metadata or {}), "render_as": "interactive"},
        )
    else:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="A turn is already queued. Wait for it to complete.",
            metadata={**dict(msg.metadata or {}), "render_as": "interactive"},
        )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands, preferring workspace help when available."""
    workspace_root = _resolve_workspace_root(ctx.loop)
    router_path = workspace_root / "scripts" / "router.py"
    if router_path.exists() and _workspace_router_is_trusted(workspace_root, router_path):
        try:
            completed = subprocess.run(
                [str(router_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
            workspace_help = (completed.stdout or "").strip()
            if workspace_help:
                return OutboundMessage(
                    channel=ctx.msg.channel,
                    chat_id=ctx.msg.chat_id,
                    content=_merge_runtime_help_text(workspace_help),
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


def register_runtime_commands(router: CommandRouter) -> None:
    """Register runtime-only builtins layered on top of the upstream command set."""
    router.priority("/interrupt", cmd_interrupt)
    router.exact("/tq", cmd_tq)
    router.exact("/turnqueue", cmd_tq)
    router.exact("/harness", cmd_harness)
    router.prefix("/harness ", cmd_harness)
    router.exact("/help", cmd_help)
    router.intercept(cmd_workspace_bridge)
