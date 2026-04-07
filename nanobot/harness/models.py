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
            executor_mode=str(payload.get("executor_mode") or "main"),
            delegation_level=str(payload.get("delegation_level") or "assist"),
            risk_level=str(payload.get("risk_level") or "normal"),
            auto_continue=bool(payload.get("auto_continue", False)),
            subagent_allowed=bool(payload.get("subagent_allowed", False)),
            subagent_profile=str(payload.get("subagent_profile") or "default"),
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

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HarnessRuntimeState":
        payload = data or {}
        return cls(
            runner=str(payload.get("runner") or "main"),
            subagent_status=str(payload.get("subagent_status") or "idle"),
            subagent_last_run_id=str(payload.get("subagent_last_run_id") or ""),
            subagent_last_error=str(payload.get("subagent_last_error") or ""),
            subagent_last_summary=str(payload.get("subagent_last_summary") or ""),
            auto_state=str(payload.get("auto_state") or "idle"),
            continuation_token=str(payload.get("continuation_token") or ""),
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
        workflow_default = cls(id="").workflow
        verification_default = cls(id="").verification
        git_delivery_default = cls(id="").git_delivery
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "work"),
            type=str(data.get("type") or "feature"),
            title=str(data.get("title") or ""),
            parent_id=str(data.get("parent_id") or ""),
            queue_order=int(data.get("queue_order") or 0),
            status=str(data.get("status") or "active"),
            phase=str(data.get("phase") or "planning"),
            summary=str(data.get("summary") or ""),
            awaiting_user=bool(data.get("awaiting_user", False)),
            blocked=bool(data.get("blocked", False)),
            next_step=str(data.get("next_step") or ""),
            resume_hint=str(data.get("resume_hint") or ""),
            verification={**verification_default, **_dict_value(data, "verification")},
            git_delivery={**git_delivery_default, **_dict_value(data, "git_delivery")},
            pending_decisions=_list_value(data, "pending_decisions"),
            artifacts=_list_value(data, "artifacts"),
            workflow={**workflow_default, **_dict_value(data, "workflow")},
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            execution_policy=HarnessExecutionPolicy.from_dict(data.get("execution_policy")),
            runtime_state=HarnessRuntimeState.from_dict(data.get("runtime_state")),
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
            "workflow": {
                "name": str(self.workflow.get("name") or ""),
                "spec_path": str(self.workflow.get("spec_path") or ""),
                "spec_hash": str(self.workflow.get("spec_hash") or ""),
                "memory": dict(self.workflow.get("memory") or {}),
                "return_to": str(self.workflow.get("return_to") or ""),
                "awaiting_confirmation": bool(self.workflow.get("awaiting_confirmation", False)),
            },
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
            version=int(payload.get("version") or 1),
            updated_at=str(payload.get("updated_at") or ""),
            active_harness_id=str(payload.get("active_harness_id") or ""),
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
