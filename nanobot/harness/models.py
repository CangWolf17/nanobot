from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


def _dict_value(
    data: dict[str, Any] | None, key: str, default: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = (data or {}).get(key)
    if isinstance(value, dict):
        return dict(value)
    return dict(default or {})


def _list_value(data: dict[str, Any] | None, key: str) -> list[Any]:
    value = (data or {}).get(key)
    return list(value) if isinstance(value, list) else []


def _string_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


def _enum_value(value: Any, allowed: set[str], default: str) -> str:
    normalized = _string_value(value, default).strip().lower()
    return normalized if normalized in allowed else default


def _normalize_workflow(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data or {}
    return {
        "name": _string_value(payload.get("name")),
        "spec_path": _string_value(payload.get("spec_path")),
        "spec_hash": _string_value(payload.get("spec_hash")),
        "memory": _dict_value(payload, "memory"),
        "return_to": _string_value(payload.get("return_to")),
        "awaiting_confirmation": _bool_value(payload.get("awaiting_confirmation"), False),
    }


def _normalize_verification(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data or {}
    return {
        "status": _string_value(payload.get("status")),
        "summary": _string_value(payload.get("summary")),
        "artifacts": _list_value(payload, "artifacts"),
    }


def _normalize_git_delivery(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data or {}
    return {
        "status": _string_value(payload.get("status")),
        "summary": _string_value(payload.get("summary")),
    }


_EXECUTOR_MODES = {"main", "subagent", "auto"}
_DELEGATION_LEVELS = {"none", "assist", "default", "required"}
_RISK_LEVELS = {"safe", "normal", "sensitive"}
_RUNNERS = {"main", "subagent"}
_SUBAGENT_STATUSES = {"idle", "running", "paused", "failed", "completed"}
_AUTO_STATES = {"idle", "queued", "running", "stopped"}
_KINDS = {"work", "workflow"}
_TYPES = {"feature", "project", "workflow"}
_RECORD_STATUSES = {
    "planning",
    "active",
    "awaiting_decision",
    "blocked",
    "failed",
    "interrupted",
    "completed",
}
_RECORD_PHASES = {
    "planning",
    "executing",
    "verify",
    "awaiting_decision",
    "blocked",
    "failed",
    "interrupted",
    "completed",
}


@dataclass
class HarnessExecutionPolicy:
    executor_mode: Literal["main", "subagent", "auto"] = "main"
    delegation_level: Literal["none", "assist", "default", "required"] = "assist"
    risk_level: Literal["safe", "normal", "sensitive"] = "normal"
    auto_continue: bool = False
    subagent_allowed: bool = False
    subagent_profile: str = "default"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HarnessExecutionPolicy":
        payload = data or {}
        return cls(
            executor_mode=_enum_value(payload.get("executor_mode"), _EXECUTOR_MODES, "main"),
            delegation_level=_enum_value(
                payload.get("delegation_level"), _DELEGATION_LEVELS, "assist"
            ),
            risk_level=_enum_value(payload.get("risk_level"), _RISK_LEVELS, "normal"),
            auto_continue=_bool_value(payload.get("auto_continue"), False),
            subagent_allowed=_bool_value(payload.get("subagent_allowed"), False),
            subagent_profile=_string_value(payload.get("subagent_profile"), "default") or "default",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor_mode": self.executor_mode,
            "delegation_level": self.delegation_level,
            "risk_level": self.risk_level,
            "auto_continue": self.auto_continue,
            "subagent_allowed": self.subagent_allowed,
            "subagent_profile": self.subagent_profile,
        }


@dataclass
class HarnessRuntimeState:
    runner: Literal["main", "subagent"] = "main"
    subagent_status: Literal["idle", "running", "paused", "failed", "completed"] = "idle"
    subagent_last_run_id: str = ""
    subagent_last_error: str = ""
    subagent_last_summary: str = ""
    auto_state: Literal["idle", "queued", "running", "stopped"] = "idle"
    continuation_token: str = ""
    session_key: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HarnessRuntimeState":
        payload = data or {}
        return cls(
            runner=_enum_value(payload.get("runner"), _RUNNERS, "main"),
            subagent_status=_enum_value(payload.get("subagent_status"), _SUBAGENT_STATUSES, "idle"),
            subagent_last_run_id=_string_value(payload.get("subagent_last_run_id")),
            subagent_last_error=_string_value(payload.get("subagent_last_error")),
            subagent_last_summary=_string_value(payload.get("subagent_last_summary")),
            auto_state=_enum_value(payload.get("auto_state"), _AUTO_STATES, "idle"),
            continuation_token=_string_value(payload.get("continuation_token")),
            session_key=_string_value(payload.get("session_key")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "subagent_status": self.subagent_status,
            "subagent_last_run_id": self.subagent_last_run_id,
            "subagent_last_error": self.subagent_last_error,
            "subagent_last_summary": self.subagent_last_summary,
            "auto_state": self.auto_state,
            "continuation_token": self.continuation_token,
            "session_key": self.session_key,
        }


@dataclass
class HarnessRecord:
    id: str
    kind: str = "work"
    type: str = "feature"
    title: str = ""
    parent_id: str = ""
    queue_order: int = 0
    status: str = "active"
    phase: str = "planning"
    summary: str = ""
    awaiting_user: bool = False
    blocked: bool = False
    next_step: str = ""
    resume_hint: str = ""
    verification: dict[str, Any] = field(
        default_factory=lambda: {"status": "", "summary": "", "artifacts": []}
    )
    git_delivery: dict[str, Any] = field(default_factory=lambda: {"status": "", "summary": ""})
    pending_decisions: list[Any] = field(default_factory=list)
    artifacts: list[Any] = field(default_factory=list)
    workflow: dict[str, Any] = field(
        default_factory=lambda: {
            "name": "",
            "spec_path": "",
            "spec_hash": "",
            "memory": {},
            "return_to": "",
            "awaiting_confirmation": False,
        }
    )
    created_at: str = ""
    updated_at: str = ""
    execution_policy: HarnessExecutionPolicy = field(default_factory=HarnessExecutionPolicy)
    runtime_state: HarnessRuntimeState = field(default_factory=HarnessRuntimeState)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HarnessRecord":
        return cls(
            id=_string_value(data.get("id")),
            kind=_enum_value(data.get("kind"), _KINDS, "work"),
            type=_enum_value(data.get("type"), _TYPES, "feature"),
            title=_string_value(data.get("title")),
            parent_id=_string_value(data.get("parent_id")),
            queue_order=_int_value(data.get("queue_order"), 0),
            status=_enum_value(data.get("status"), _RECORD_STATUSES, "active"),
            phase=_enum_value(data.get("phase"), _RECORD_PHASES, "planning"),
            summary=_string_value(data.get("summary")),
            awaiting_user=_bool_value(data.get("awaiting_user"), False),
            blocked=_bool_value(data.get("blocked"), False),
            next_step=_string_value(data.get("next_step")),
            resume_hint=_string_value(data.get("resume_hint")),
            verification=_normalize_verification(_dict_value(data, "verification")),
            git_delivery=_normalize_git_delivery(_dict_value(data, "git_delivery")),
            pending_decisions=_list_value(data, "pending_decisions"),
            artifacts=_list_value(data, "artifacts"),
            workflow=_normalize_workflow(_dict_value(data, "workflow")),
            created_at=_string_value(data.get("created_at")),
            updated_at=_string_value(data.get("updated_at")),
            execution_policy=HarnessExecutionPolicy.from_dict(data.get("execution_policy")),
            runtime_state=HarnessRuntimeState.from_dict(data.get("runtime_state")),
        )

    @classmethod
    def from_legacy(
        cls,
        *,
        record_id: str,
        legacy_index: dict[str, Any],
        legacy_state: dict[str, Any],
    ) -> "HarnessRecord":
        verification_artifacts = _list_value(legacy_state, "artifacts") or _list_value(
            legacy_index, "artifacts"
        )
        return cls.from_dict(
            {
                "id": record_id,
                "kind": legacy_index.get("kind"),
                "type": legacy_index.get("type"),
                "title": legacy_state.get("title") or legacy_index.get("title"),
                "parent_id": legacy_index.get("parent_id"),
                "queue_order": legacy_index.get("queue_order"),
                "status": legacy_state.get("status") or legacy_index.get("status"),
                "phase": legacy_state.get("phase") or legacy_index.get("phase"),
                "summary": legacy_state.get("summary") or legacy_index.get("summary"),
                "awaiting_user": legacy_state.get(
                    "awaiting_user", legacy_index.get("awaiting_user", False)
                ),
                "blocked": legacy_state.get("blocked", legacy_index.get("blocked", False)),
                "next_step": legacy_state.get("next_step") or legacy_index.get("next_step"),
                "resume_hint": legacy_state.get("resume_hint") or legacy_index.get("resume_hint"),
                "verification": {
                    "status": legacy_state.get("verification_status")
                    or legacy_index.get("verification_status"),
                    "summary": legacy_state.get("verification_summary")
                    or legacy_index.get("verification_summary"),
                    "artifacts": verification_artifacts,
                },
                "git_delivery": {
                    "status": legacy_state.get("git_delivery_status")
                    or legacy_index.get("git_delivery_status"),
                    "summary": legacy_state.get("git_delivery_summary")
                    or legacy_index.get("git_delivery_summary"),
                },
                "pending_decisions": _list_value(legacy_state, "pending_decisions")
                or _list_value(legacy_index, "pending_decisions"),
                "artifacts": verification_artifacts,
                "workflow": {
                    "name": legacy_state.get("workflow_name") or legacy_index.get("workflow_name"),
                    "spec_path": legacy_state.get("workflow_spec_path")
                    or legacy_index.get("workflow_spec_path"),
                    "spec_hash": legacy_state.get("workflow_spec_hash")
                    or legacy_index.get("workflow_spec_hash"),
                    "memory": _dict_value(legacy_state, "workflow_memory")
                    or _dict_value(legacy_index, "workflow_memory"),
                    "return_to": legacy_state.get("return_to") or legacy_index.get("return_to"),
                    "awaiting_confirmation": legacy_state.get(
                        "awaiting_confirmation",
                        legacy_index.get("awaiting_confirmation", False),
                    ),
                },
                "created_at": legacy_state.get("created_at") or legacy_index.get("created_at"),
                "updated_at": legacy_state.get("updated_at") or legacy_index.get("updated_at"),
                "execution_policy": {
                    "executor_mode": legacy_state.get("executor_mode")
                    or legacy_index.get("executor_mode"),
                    "delegation_level": legacy_state.get("delegation_level")
                    or legacy_index.get("delegation_level"),
                    "risk_level": legacy_state.get("risk_level") or legacy_index.get("risk_level"),
                    "auto_continue": legacy_state.get(
                        "auto_continue",
                        legacy_index.get("auto_continue", legacy_index.get("auto", False)),
                    ),
                    "subagent_allowed": legacy_state.get(
                        "subagent_allowed", legacy_index.get("subagent_allowed", False)
                    ),
                    "subagent_profile": legacy_state.get("subagent_profile")
                    or legacy_index.get("subagent_profile"),
                },
                "runtime_state": {
                    "runner": legacy_state.get("runner") or legacy_index.get("runner"),
                    "subagent_status": legacy_state.get("subagent_status")
                    or legacy_index.get("subagent_status"),
                    "subagent_last_run_id": legacy_state.get("subagent_last_run_id")
                    or legacy_index.get("subagent_last_run_id"),
                    "subagent_last_error": legacy_state.get("subagent_last_error")
                    or legacy_index.get("subagent_last_error"),
                    "subagent_last_summary": legacy_state.get("subagent_last_summary")
                    or legacy_index.get("subagent_last_summary"),
                    "auto_state": legacy_state.get("auto_state") or legacy_index.get("auto_state"),
                    "continuation_token": legacy_state.get("continuation_token")
                    or legacy_index.get("continuation_token"),
                },
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "type": self.type,
            "title": self.title,
            "parent_id": self.parent_id,
            "queue_order": self.queue_order,
            "status": self.status,
            "phase": self.phase,
            "summary": self.summary,
            "awaiting_user": self.awaiting_user,
            "blocked": self.blocked,
            "next_step": self.next_step,
            "resume_hint": self.resume_hint,
            "verification": dict(self.verification),
            "git_delivery": dict(self.git_delivery),
            "pending_decisions": list(self.pending_decisions),
            "artifacts": list(self.artifacts),
            "workflow": dict(self.workflow),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "execution_policy": self.execution_policy.to_dict(),
            "runtime_state": self.runtime_state.to_dict(),
        }


@dataclass
class HarnessSnapshot:
    version: int = 1
    updated_at: str = ""
    active_harness_id: str = ""
    records: dict[str, HarnessRecord] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HarnessSnapshot":
        payload = data or {}
        raw_records = payload.get("records")
        records = {
            str(harness_id): HarnessRecord.from_dict(record)
            for harness_id, record in (raw_records.items() if isinstance(raw_records, dict) else [])
            if isinstance(record, dict)
        }
        return cls(
            version=_int_value(payload.get("version"), 1),
            updated_at=_string_value(payload.get("updated_at")),
            active_harness_id=_string_value(payload.get("active_harness_id")),
            records=records,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "active_harness_id": self.active_harness_id,
            "records": {
                harness_id: record.to_dict() for harness_id, record in self.records.items()
            },
        }
