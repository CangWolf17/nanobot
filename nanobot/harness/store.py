from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.harness.models import (
    HarnessExecutionPolicy,
    HarnessRecord,
    HarnessRuntimeState,
    HarnessSnapshot,
)


@dataclass
class HarnessStore:
    workspace_root: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> "HarnessStore":
        return cls(workspace_root=workspace_root)

    @property
    def harnesses_dir(self) -> Path:
        return self.workspace_root / "harnesses"

    @property
    def store_path(self) -> Path:
        return self.harnesses_dir / "store.json"

    def load(self) -> HarnessSnapshot:
        if self.store_path.exists():
            return self._load_store_json()
        snapshot = self._migrate_legacy_workspace_files()
        self.save(snapshot)
        return snapshot

    def save(self, snapshot: HarnessSnapshot) -> None:
        self.harnesses_dir.mkdir(parents=True, exist_ok=True)
        payload = snapshot.to_dict()
        self.store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_store_json(self) -> HarnessSnapshot:
        return HarnessSnapshot.from_dict(json.loads(self.store_path.read_text(encoding="utf-8")))

    def _migrate_legacy_workspace_files(self) -> HarnessSnapshot:
        index_payload = self._read_json_file(self.harnesses_dir / "index.json")
        control_payload = self._read_json_file(self.harnesses_dir / "control.json")
        raw_records = index_payload.get("harnesses") if isinstance(index_payload, dict) else {}

        records: dict[str, HarnessRecord] = {}
        if isinstance(raw_records, dict):
            for harness_id, raw_record in raw_records.items():
                if not isinstance(raw_record, dict):
                    continue
                record_id = str(raw_record.get("id") or harness_id)
                state_payload = self._read_json_file(self.harnesses_dir / record_id / "state.json")
                records[record_id] = self._migrate_legacy_record(
                    record_id, raw_record, state_payload
                )

        return HarnessSnapshot(
            updated_at=str(control_payload.get("updated_at") or ""),
            active_harness_id=str(control_payload.get("active_harness_id") or ""),
            records=records,
        )

    def _migrate_legacy_record(
        self,
        record_id: str,
        legacy_index: dict[str, Any],
        legacy_state: dict[str, Any],
    ) -> HarnessRecord:
        verification_artifacts = self._list_value(legacy_state, "artifacts") or self._list_value(
            legacy_index, "artifacts"
        )
        return HarnessRecord(
            id=record_id,
            kind=str(legacy_index.get("kind") or "work"),
            type=str(legacy_index.get("type") or "feature"),
            title=str(legacy_state.get("title") or legacy_index.get("title") or ""),
            parent_id=str(legacy_index.get("parent_id") or ""),
            queue_order=int(legacy_index.get("queue_order") or 0),
            status=str(legacy_state.get("status") or legacy_index.get("status") or "active"),
            phase=str(legacy_state.get("phase") or legacy_index.get("phase") or "planning"),
            summary=str(legacy_state.get("summary") or legacy_index.get("summary") or ""),
            awaiting_user=bool(
                legacy_state.get("awaiting_user", legacy_index.get("awaiting_user", False))
            ),
            blocked=bool(legacy_state.get("blocked", legacy_index.get("blocked", False))),
            next_step=str(legacy_state.get("next_step") or legacy_index.get("next_step") or ""),
            resume_hint=str(
                legacy_state.get("resume_hint") or legacy_index.get("resume_hint") or ""
            ),
            verification={
                "status": str(
                    legacy_state.get("verification_status")
                    or legacy_index.get("verification_status")
                    or ""
                ),
                "summary": str(
                    legacy_state.get("verification_summary")
                    or legacy_index.get("verification_summary")
                    or ""
                ),
                "artifacts": verification_artifacts,
            },
            git_delivery={
                "status": str(
                    legacy_state.get("git_delivery_status")
                    or legacy_index.get("git_delivery_status")
                    or ""
                ),
                "summary": str(
                    legacy_state.get("git_delivery_summary")
                    or legacy_index.get("git_delivery_summary")
                    or ""
                ),
            },
            pending_decisions=self._list_value(legacy_state, "pending_decisions")
            or self._list_value(legacy_index, "pending_decisions"),
            artifacts=verification_artifacts,
            workflow={
                "name": str(
                    legacy_state.get("workflow_name") or legacy_index.get("workflow_name") or ""
                ),
                "spec_path": str(
                    legacy_state.get("workflow_spec_path")
                    or legacy_index.get("workflow_spec_path")
                    or ""
                ),
                "spec_hash": str(
                    legacy_state.get("workflow_spec_hash")
                    or legacy_index.get("workflow_spec_hash")
                    or ""
                ),
                "memory": self._dict_value(legacy_state, "workflow_memory")
                or self._dict_value(legacy_index, "workflow_memory"),
                "return_to": str(
                    legacy_state.get("return_to") or legacy_index.get("return_to") or ""
                ),
                "awaiting_confirmation": bool(
                    legacy_state.get(
                        "awaiting_confirmation",
                        legacy_index.get("awaiting_confirmation", False),
                    )
                ),
            },
            created_at=str(legacy_state.get("created_at") or legacy_index.get("created_at") or ""),
            updated_at=str(legacy_state.get("updated_at") or legacy_index.get("updated_at") or ""),
            execution_policy=HarnessExecutionPolicy(
                executor_mode=str(
                    legacy_state.get("executor_mode") or legacy_index.get("executor_mode") or "main"
                ),
                delegation_level=str(
                    legacy_state.get("delegation_level")
                    or legacy_index.get("delegation_level")
                    or "assist"
                ),
                risk_level=str(
                    legacy_state.get("risk_level") or legacy_index.get("risk_level") or "normal"
                ),
                auto_continue=bool(
                    legacy_state.get("auto_continue", legacy_index.get("auto_continue", False))
                ),
                subagent_allowed=bool(
                    legacy_state.get(
                        "subagent_allowed", legacy_index.get("subagent_allowed", False)
                    )
                ),
                subagent_profile=str(
                    legacy_state.get("subagent_profile")
                    or legacy_index.get("subagent_profile")
                    or "default"
                ),
            ),
            runtime_state=HarnessRuntimeState(
                runner=str(legacy_state.get("runner") or legacy_index.get("runner") or "main"),
                subagent_status=str(
                    legacy_state.get("subagent_status")
                    or legacy_index.get("subagent_status")
                    or "idle"
                ),
                subagent_last_run_id=str(
                    legacy_state.get("subagent_last_run_id")
                    or legacy_index.get("subagent_last_run_id")
                    or ""
                ),
                subagent_last_error=str(
                    legacy_state.get("subagent_last_error")
                    or legacy_index.get("subagent_last_error")
                    or ""
                ),
                subagent_last_summary=str(
                    legacy_state.get("subagent_last_summary")
                    or legacy_index.get("subagent_last_summary")
                    or ""
                ),
                auto_state=str(
                    legacy_state.get("auto_state") or legacy_index.get("auto_state") or "idle"
                ),
                continuation_token=str(
                    legacy_state.get("continuation_token")
                    or legacy_index.get("continuation_token")
                    or ""
                ),
            ),
        )

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
        value = data.get(key)
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _list_value(data: dict[str, Any], key: str) -> list[Any]:
        value = data.get(key)
        return list(value) if isinstance(value, list) else []
