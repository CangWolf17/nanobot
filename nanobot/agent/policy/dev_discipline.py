"""Session-scoped dev discipline policy for nanobot runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROTOCOL_SCHEMA_VERSION = 1


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_active_session_root(workspace: Path) -> Path | None:
    control = _read_json(workspace / "sessions" / "control.json")
    index = _read_json(workspace / "sessions" / "index.json")
    if not control or not index:
        return None
    active_id = str(control.get("active_session_id") or "").strip()
    if not active_id:
        return None
    session = (index.get("sessions") or {}).get(active_id)
    if not isinstance(session, dict):
        return None
    session_root = session.get("session_root")
    if not session_root:
        return None
    try:
        return Path(str(session_root)).resolve()
    except Exception:
        return None


def load_active_dev_state(workspace: Path) -> dict[str, Any] | None:
    session_root = get_active_session_root(workspace)
    if not session_root:
        return None
    data = _read_json(session_root / "dev_state.json")
    return data if isinstance(data, dict) else None


def _gate_summary(gate: dict[str, Any] | None) -> str:
    gate = gate or {}
    if not gate.get("required"):
        return "not-required"
    return "satisfied" if gate.get("satisfied") else "pending"


def build_runtime_protocol(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    runtime_protocol = state.get("runtime_protocol") or {}
    gates = state.get("gates") or {}
    return {
        "version": int(runtime_protocol.get("version") or PROTOCOL_SCHEMA_VERSION),
        "strict_dev_mode": str(state.get("strict_dev_mode") or "enforce"),
        "task_kind": str(state.get("task_kind") or "idle"),
        "phase": str(state.get("phase") or "idle"),
        "work_mode": str(state.get("work_mode") or "plan"),
        "current_step": str(state.get("current_step") or ""),
        "gates": {
            name: _gate_summary(gates.get(name))
            for name in ("plan", "debug_root_cause", "failing_test", "verification")
        },
    }


def load_runtime_protocol(workspace: Path) -> dict[str, Any] | None:
    return build_runtime_protocol(load_active_dev_state(workspace))


def format_runtime_protocol_block(
    protocol: dict[str, Any] | None,
    *,
    skill_hints: list[str] | None = None,
) -> str:
    if not protocol:
        return ""
    gates = protocol.get("gates") or {}
    lines = [
        "## Runtime Protocol",
        f"version: {protocol.get('version', PROTOCOL_SCHEMA_VERSION)}",
        f"strict_dev_mode: {protocol.get('strict_dev_mode', 'enforce')}",
        f"task_kind: {protocol.get('task_kind', 'idle')}",
        f"phase: {protocol.get('phase', 'idle')}",
        f"work_mode: {protocol.get('work_mode', 'plan')}",
    ]
    current_step = str(protocol.get("current_step") or "")
    if current_step:
        lines.append(f"current_step: {current_step}")
    lines.append(
        "gates: "
        + ", ".join(
            f"{name}={gates.get(name, 'not-required')}"
            for name in ("plan", "debug_root_cause", "failing_test", "verification")
        )
    )
    if skill_hints:
        lines.append(f"required_skills: {', '.join(skill_hints)}")
    return "\n".join(lines)


def is_strict_dev_mode_enforced(workspace: Path) -> bool:
    protocol = load_runtime_protocol(workspace)
    if not protocol:
        return False
    if str(protocol.get("strict_dev_mode") or "") != "enforce":
        return False
    return str(protocol.get("task_kind") or "idle") != "idle" and str(protocol.get("phase") or "idle") != "idle"


def should_disable_concurrent_tools(workspace: Path) -> bool:
    return is_strict_dev_mode_enforced(workspace)


def format_dev_discipline_block(workspace: Path) -> str:
    protocol = load_runtime_protocol(workspace)
    if not protocol or not is_strict_dev_mode_enforced(workspace):
        return ""
    gates = protocol.get("gates") or {}
    lines = [
        "## Dev Discipline",
        f"strict_dev_mode: {protocol.get('strict_dev_mode', 'enforce')}",
        f"task_kind: {protocol.get('task_kind', 'idle')}",
        f"phase: {protocol.get('phase', 'idle')}",
        "required gates:",
        f"- plan: {gates.get('plan', 'not-required')}",
        f"- debug_root_cause: {gates.get('debug_root_cause', 'not-required')}",
        f"- failing_test: {gates.get('failing_test', 'not-required')}",
        f"- verification: {gates.get('verification', 'not-required')}",
    ]
    return "\n".join(lines)
