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
WORKSPACE_PYTHON = WORKSPACE_ROOT / "venv" / "bin" / "python"
FASTLANE_ROUTE_TIMEOUT_SECONDS = 5


def build_workspace_env(msg: InboundMessage) -> dict[str, str]:
    return {
        **os.environ.copy(),
        "NANOBOT_CHANNEL": msg.channel,
        "NANOBOT_CHAT_ID": msg.chat_id,
        "NANOBOT_MESSAGE_ID": msg.metadata.get("message_id", ""),
    }


def _load_fastlane_decision(raw: str, env: dict[str, str] | None) -> dict[str, Any] | None:
    if not WORKSPACE_ROUTER.exists():
        return None
    try:
        result = subprocess.run(
            [str(WORKSPACE_PYTHON), str(WORKSPACE_ROUTER), "--route-json"],
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


def _script_result_to_text(result: subprocess.CompletedProcess[str], timeout: int) -> str:
    if (result.stdout or "").strip():
        return result.stdout.strip()
    if result.returncode != 0 and (result.stderr or "").strip():
        return f"[脚本错误] {result.stderr.strip()}"
    return ""


def _run_fastlane_script(decision: dict[str, Any], env: dict[str, str] | None) -> str:
    script = str(decision.get("script") or "")
    args = decision.get("args") or []
    timeout = decision.get("timeout")
    timeout_seconds = timeout if isinstance(timeout, int) else 3
    argv = [str(WORKSPACE_PYTHON), str(WORKSPACE_ROOT / "scripts" / script)]
    argv.extend(str(arg) for arg in args if isinstance(arg, str))

    try:
        result = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout_seconds,
            cwd=str(WORKSPACE_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"[路由] 脚本执行超时（>{timeout_seconds}s）"
    except Exception as exc:
        return f"[路由] 脚本执行异常：{exc}"

    return _script_result_to_text(result, timeout_seconds)


async def try_workspace_fastlane(msg: InboundMessage, raw: str) -> OutboundMessage | None:
    if not raw.startswith("/"):
        return None
    env = build_workspace_env(msg)
    decision = _load_fastlane_decision(raw, env)
    if not decision:
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
        content = _run_fastlane_script(decision, env)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={"render_as": "text"},
        )

    return None
