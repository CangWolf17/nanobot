from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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
    title: str = ""
    summary: str = ""
    execution_policy: HarnessExecutionPolicy = field(default_factory=HarnessExecutionPolicy)
    runtime_state: HarnessRuntimeState = field(default_factory=HarnessRuntimeState)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HarnessRecord":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            summary=str(data.get("summary") or ""),
            execution_policy=HarnessExecutionPolicy.from_dict(data.get("execution_policy")),
            runtime_state=HarnessRuntimeState.from_dict(data.get("runtime_state")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "execution_policy": self.execution_policy.to_dict(),
            "runtime_state": self.runtime_state.to_dict(),
        }


@dataclass
class HarnessSnapshot:
    active_harness_id: str = ""
    records: dict[str, HarnessRecord] = field(default_factory=dict)
    version: int = 1

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
            active_harness_id=str(payload.get("active_harness_id") or ""),
            records=records,
            version=int(payload.get("version") or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "active_harness_id": self.active_harness_id,
            "records": {
                harness_id: record.to_dict() for harness_id, record in self.records.items()
            },
        }
