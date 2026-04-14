"""Workspace fastlane helpers for read-only slash commands."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from nanobot.bus.events import InboundMessage, OutboundMessage

WORKSPACE_ROOT = Path.home() / ".nanobot" / "workspace"
WORKSPACE_ROUTER = WORKSPACE_ROOT / "scripts" / "router.py"
FASTLANE_ROUTE_TIMEOUT_SECONDS = 5
_MODEL_HELP_TEXT = """/model [子命令]
作用：运行态模型管理入口（当前保持纯脚本）。
子命令：
- current：查看当前模型。
- list：查看可用模型。
- use <模型>：切换当前模型；无参数时回退到 list。
- rollback：回滚到 last_known_good 的第一版运行态入口。
- health：执行当前模型 quick probe。

说明：
- `/work model` 已迁到 `/model`。
- `/model registry ...` 属于后续 agent workflow 前台，不走当前纯脚本链。
- 当前已接入 runtime apply / state file / rollback / quick health / selector / candidate chain。
- alias collision / rollback target / quick-deep health / registry CRUD 仍在 deferred。"""


def _workspace_root_from_router(router: Path) -> Path:
    if router.parent.name == "scripts":
        return router.parent.parent
    return router.parent


def _workspace_paths(workspace_root: Path | None = None) -> tuple[Path, Path, Path]:
    if workspace_root is not None:
        root = workspace_root
        router = root / "scripts" / "router.py"
    else:
        router = WORKSPACE_ROUTER
        root = _workspace_root_from_router(router)
    python = root / "venv" / "bin" / "python"
    return root, router, python


def _builtin_fastlane_help(raw: str) -> str | None:
    normalized = " ".join(raw.strip().lower().split())
    if normalized in {"/model help", "/help model"}:
        return _MODEL_HELP_TEXT
    return None


def build_workspace_env(msg: InboundMessage) -> dict[str, str]:
    return {
        **os.environ.copy(),
        "NANOBOT_CHANNEL": msg.channel,
        "NANOBOT_CHAT_ID": msg.chat_id,
        "NANOBOT_MESSAGE_ID": str(msg.metadata.get("message_id", "")),
    }


def _load_fastlane_decision(
    raw: str,
    env: dict[str, str] | None,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any] | None:
    _, router, python = _workspace_paths(workspace_root)
    if not router.exists():
        return None
    try:
        result = subprocess.run(
            [str(python), str(router), "--route-json"],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=FASTLANE_ROUTE_TIMEOUT_SECONDS,
            env=env,
        )
    except Exception:
        return None

    stdout = (result.stdout or "").strip()
    if not stdout:
        return None

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _script_result_to_text(result: subprocess.CompletedProcess[str]) -> str:
    if (result.stdout or "").strip():
        return result.stdout.strip()
    if result.returncode != 0 and (result.stderr or "").strip():
        return f"[脚本错误] {result.stderr.strip()}"
    return ""


def _run_fastlane_script(
    decision: dict[str, Any],
    env: dict[str, str] | None,
    *,
    workspace_root: Path | None = None,
) -> str:
    root, _, python = _workspace_paths(workspace_root)
    script = str(decision.get("script") or "")
    args = decision.get("args") or []
    timeout = decision.get("timeout")
    timeout_seconds = timeout if isinstance(timeout, int) else 3
    argv = [str(python), str(root / "scripts" / script)]
    argv.extend(str(arg) for arg in args if isinstance(arg, str))

    try:
        result = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout_seconds,
            cwd=str(root),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"[路由] 脚本执行超时（>{timeout_seconds}s）"
    except Exception as exc:
        return f"[路由] 脚本执行异常：{exc}"

    return _script_result_to_text(result)


async def try_workspace_fastlane(
    msg: InboundMessage,
    raw: str,
    workspace_root: Path | None = None,
) -> OutboundMessage | None:
    if not raw.startswith("/"):
        return None
    env = build_workspace_env(msg)
    decision = _load_fastlane_decision(raw, env, workspace_root=workspace_root)
    if not decision:
        if fallback := _builtin_fastlane_help(raw):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=fallback,
                metadata={"render_as": "text"},
            )
        return None

    kind = decision.get("kind")
    if kind == "help_fastlane":
        content = str(decision.get("content") or "").strip()
        if not content:
            return None
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={"render_as": "text"},
        )

    if kind == "exec_fastlane":
        content = _run_fastlane_script(decision, env, workspace_root=workspace_root)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={"render_as": "text"},
        )

    return None
