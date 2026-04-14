"""Workspace slash-command bridge.

Forward unknown slash commands to the workspace router script before they fall
through to the LLM.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.command.fastlane import build_workspace_env, try_workspace_fastlane
from nanobot.command.router import CommandContext
from nanobot.config.paths import get_workspace_path

WORKSPACE_ROUTER = Path.home() / ".nanobot" / "workspace" / "scripts" / "router.py"
BRIDGE_TIMEOUT_SECONDS = 25
PREPARED_INPUT_CMDS = {"小结", "simplify", "笔记", "merge"}
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
    "笔记",
}


def _workspace_root_from_router() -> Path:
    if WORKSPACE_ROUTER.parent.name == "scripts":
        return WORKSPACE_ROUTER.parent.parent
    return WORKSPACE_ROUTER.parent


def _workspace_paths(workspace_root: Path | None = None) -> tuple[Path, Path, Path]:
    if workspace_root is not None:
        root = workspace_root
        router = root / "scripts" / "router.py"
    else:
        router = WORKSPACE_ROUTER
        root = _workspace_root_from_router()
    python = root / "venv" / "bin" / "python"
    return root, router, python


def _extract_agent_marker(content: str) -> str | None:
    stripped = content.strip()
    if not stripped.startswith("[AGENT]"):
        return None
    return stripped[len("[AGENT]") :].strip()


def _prepare_agent_input(
    agent_cmd: str,
    raw: str,
    env: dict[str, str] | None,
    *,
    workspace_root: Path | None = None,
) -> str | None:
    if agent_cmd not in PREPARED_INPUT_CMDS:
        return None
    _, router, python = _workspace_paths(workspace_root)
    try:
        result = subprocess.run(
            [
                str(python),
                str(router),
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


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _canonical_active_workflow_record(root: Path) -> dict[str, Any] | None:
    store = _read_json_dict(root / "harnesses" / "store.json")
    active_id = str(store.get("active_harness_id") or "").strip()
    records = store.get("records")
    if not active_id or not isinstance(records, dict):
        return None
    record = records.get(active_id)
    return record if isinstance(record, dict) else None


def _legacy_active_workflow_record(root: Path) -> dict[str, Any] | None:
    control = _read_json_dict(root / "harnesses" / "control.json")
    index = _read_json_dict(root / "harnesses" / "index.json")
    active_id = str(control.get("active_harness_id") or "").strip()
    harnesses = index.get("harnesses") if isinstance(index, dict) else None
    if not active_id or not isinstance(harnesses, dict):
        return None
    record = harnesses.get(active_id)
    return record if isinstance(record, dict) else None


def _is_merge_workflow_awaiting_decision(record: dict[str, Any]) -> bool:
    kind = str(record.get("kind") or "").strip()
    status = str(record.get("status") or "").strip()
    if kind != "workflow" or status != "awaiting_decision":
        return False
    if not bool(record.get("awaiting_user")):
        return False
    if bool(record.get("blocked")):
        return False
    workflow = record.get("workflow")
    if isinstance(workflow, dict):
        name = str(workflow.get("name") or "").strip()
    else:
        name = str(record.get("workflow_name") or "").strip()
    return name == "merge"


def prepare_active_workflow_continuation(
    msg: InboundMessage,
    *,
    workspace_root: Path | None = None,
) -> bool:
    raw = (msg.content or "").strip()
    if not raw or raw.startswith("/"):
        return False
    meta = msg.metadata if isinstance(msg.metadata, dict) else {}
    if meta.get("workspace_agent_cmd"):
        return False

    root, _, _ = _workspace_paths(workspace_root)

    sessions_control_path = root / "sessions" / "control.json"
    sessions_index_path = root / "sessions" / "index.json"
    if sessions_control_path.exists() and sessions_index_path.exists():
        try:
            sessions_control = _read_json_dict(sessions_control_path)
            sessions_index = _read_json_dict(sessions_index_path)
            active_session_id = str(sessions_control.get("active_session_id") or "").strip()
            sessions = sessions_index.get("sessions") if isinstance(sessions_index, dict) else None
            if active_session_id and isinstance(sessions, dict):
                active_session = sessions.get(active_session_id)
                if isinstance(active_session, dict):
                    session_root_raw = str(active_session.get("session_root") or "").strip()
                    if session_root_raw:
                        notes_state_path = Path(session_root_raw) / "notes_state.json"
                        if notes_state_path.exists():
                            notes_state = _read_json_dict(notes_state_path)
                            if (
                                str(notes_state.get("mode") or "").strip() == "notes"
                                and str(notes_state.get("phase") or "").strip()
                                == "awaiting_confirmation"
                            ):
                                env = build_workspace_env(msg)
                                prepared = _prepare_agent_input(
                                    "笔记",
                                    raw,
                                    env,
                                    workspace_root=root,
                                )
                                if prepared:
                                    meta["workspace_agent_cmd"] = "笔记"
                                    meta["workspace_agent_input"] = prepared
                                    msg.metadata = meta
                                    return True
        except Exception:
            pass

    active = _canonical_active_workflow_record(root) or _legacy_active_workflow_record(root)
    if not isinstance(active, dict) or not _is_merge_workflow_awaiting_decision(active):
        return False

    env = build_workspace_env(msg)
    prepared = _prepare_agent_input("merge", raw, env, workspace_root=root)
    if not prepared:
        return False
    meta["workspace_agent_cmd"] = "merge"
    meta["workspace_agent_input"] = prepared
    msg.metadata = meta
    return True


async def cmd_workspace_bridge(ctx: CommandContext) -> OutboundMessage | None:
    raw = (ctx.raw or "").strip()
    workspace_root = None
    if ctx.loop is not None:
        workspace_root = get_workspace_path(getattr(ctx.loop, "workspace", None))
    if not raw.startswith("/"):
        prepare_active_workflow_continuation(ctx.msg, workspace_root=workspace_root)
        return None
    if raw.lower() == "/harness" or raw.lower().startswith("/harness "):
        return None

    root, router, python = _workspace_paths(workspace_root)
    if not router.exists():
        return None

    if fastlane := await try_workspace_fastlane(ctx.msg, raw, workspace_root=workspace_root):
        return fastlane

    env = build_workspace_env(ctx.msg) if ctx.msg is not None else None

    try:
        result = subprocess.run(
            [str(python), str(router)],
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
            prepared = _prepare_agent_input(agent_cmd, raw, env, workspace_root=root)
            if prepared:
                ctx.msg.metadata["workspace_agent_input"] = prepared
        return None

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=stdout,
        metadata={"render_as": "text"},
    )
