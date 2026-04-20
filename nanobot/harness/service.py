from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.harness.models import HarnessRecord, HarnessSnapshot
from nanobot.harness.projections import sync_workspace_projections
from nanobot.harness.store import HarnessStore
from nanobot.harness.workflows import WorkflowDefinition, get_workflow_definition
from nanobot.utils.helpers import timestamp


@dataclass(frozen=True)
class HarnessCommandResult:
    response_mode: str
    agent_cmd: str = "harness"
    active_harness_id: str = ""
    prepared_input: str = ""
    text: str = ""


@dataclass(frozen=True)
class WorkflowStartResult:
    workflow_id: str
    created_copy: bool
    prepared_input: str


@dataclass(frozen=True)
class HarnessAutoContinueDecision:
    should_fire: bool
    reason: str
    origin_sender_id: str


@dataclass(frozen=True)
class HarnessApplyResult:
    final_content: str
    closeout_required: bool
    closeout_summary: str


@dataclass
class HarnessService:
    workspace_root: Path
    store: HarnessStore

    _JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> "HarnessService":
        return cls(
            workspace_root=workspace_root,
            store=HarnessStore.for_workspace(workspace_root),
        )

    def handle_command(
        self,
        raw: str,
        *,
        session_key: str,
        sender_id: str,
    ) -> HarnessCommandResult:
        command = (raw or "").strip()
        command_lower = command.lower()
        if not command_lower.startswith("/harness"):
            raise ValueError("expected a /harness command")

        goal = command[len("/harness") :].strip()
        if goal.lower() == "status":
            return HarnessCommandResult(
                response_mode="text",
                text=self.render_status_for_session(session_key=session_key),
            )
        if goal.lower() == "list":
            return HarnessCommandResult(response_mode="text", text=self.render_list())
        if goal.lower() == "workflows":
            return HarnessCommandResult(response_mode="text", text=self.render_workflows())
        if goal.lower() == "cleanup":
            workflow = self.start_workflow(
                "cleanup",
                origin_command=command,
                session_key=session_key,
            )
            return HarnessCommandResult(
                response_mode="agent",
                agent_cmd="harness",
                active_harness_id=workflow.workflow_id,
                prepared_input=workflow.prepared_input,
            )

        snapshot = self.store.load()
        active_record = self._get_active_record(snapshot)
        if active_record is None or (goal and active_record.kind == "workflow"):
            if not goal:
                raise ValueError("/harness requires a goal when no active harness exists")
            active_record = self._create_work_harness(snapshot, goal)
        self._bind_session(snapshot, active_record=active_record, session_key=session_key)
        self._save_snapshot(snapshot)
        prepared_input = self._build_goal_prepared_input(
            snapshot=snapshot,
            active_record=active_record,
            requested_goal=goal,
            session_key=session_key,
            sender_id=sender_id,
        )
        return HarnessCommandResult(
            response_mode="agent",
            agent_cmd="harness",
            active_harness_id=active_record.id,
            prepared_input=prepared_input,
        )

    def start_workflow(
        self,
        workflow_name: str,
        *,
        origin_command: str,
        session_key: str = "",
        requested_goal_after_cleanup: str = "",
    ) -> WorkflowStartResult:
        snapshot = self.store.load()
        definition = get_workflow_definition(workflow_name)
        prior_active_id = snapshot.active_harness_id
        record = snapshot.records.get(definition.stable_harness_id)
        if record is None:
            record = self._create_workflow_harness(snapshot, definition)

        record.title = definition.title
        record.kind = "workflow"
        record.type = "workflow"
        record.status = "active"
        record.phase = "planning"
        record.updated_at = timestamp()
        record.workflow["name"] = definition.name
        memory: dict[str, str] = {}
        if requested_goal_after_cleanup:
            memory["requested_goal_after_cleanup"] = requested_goal_after_cleanup
        record.workflow["memory"] = memory
        record.workflow["return_to"] = self._workflow_return_target(
            prior_active_id=prior_active_id,
            workflow_id=record.id,
        )
        self._bind_session(snapshot, active_record=record, session_key=session_key)
        snapshot.active_harness_id = record.id
        self._save_snapshot(snapshot)

        return WorkflowStartResult(
            workflow_id=record.id,
            created_copy=False,
            prepared_input=self._build_workflow_prepared_input(
                snapshot=snapshot,
                workflow=record,
                origin_command=origin_command,
            ),
        )

    def render_status(self) -> str:
        snapshot = self.store.load()
        active = self._get_active_record(snapshot)
        if active is None:
            return "No active harness."
        return "\n".join(
            [
                f"Active harness: {active.id}",
                f"Title: {active.title or '[untitled]'}",
                f"Kind: {active.kind}",
                f"Status: {active.status}",
                f"Phase: {active.phase}",
            ]
        )

    def render_status_for_session(self, *, session_key: str) -> str:
        snapshot = self.store.load()
        active = self._get_session_bound_record(snapshot, session_key=session_key)
        if active is None:
            return "No active harness."
        return "\n".join(
            [
                f"Active harness: {active.id}",
                f"Title: {active.title or '[untitled]'}",
                f"Kind: {active.kind}",
                f"Status: {active.status}",
                f"Phase: {active.phase}",
            ]
        )

    def render_status_summary(self) -> str | None:
        snapshot = self.store.load()
        active = self._get_active_record(snapshot)
        if active is None:
            return None
        if active.summary:
            return f"{active.status} / {active.phase} — {active.summary}"
        return f"{active.status} / {active.phase}"

    def render_status_summary_for_session(self, session_key: str) -> str | None:
        snapshot = self.store.load()
        active = self._get_session_bound_record(snapshot, session_key=session_key)
        if active is None:
            return None
        if active.summary:
            return f"{active.status} / {active.phase} — {active.summary}"
        return f"{active.status} / {active.phase}"

    def render_list(self) -> str:
        snapshot = self.store.load()
        records = [record for record in snapshot.records.values() if record.kind != "workflow"]
        if not records:
            return "No work harnesses."
        return "\n".join(
            f"- {record.id}: {record.title or '[untitled]'} ({record.status}/{record.phase})"
            for record in sorted(records, key=lambda item: (item.queue_order, item.id))
        )

    def render_workflows(self) -> str:
        snapshot = self.store.load()
        records = [record for record in snapshot.records.values() if record.kind == "workflow"]
        if not records:
            return "No workflow harnesses."
        return "\n".join(
            f"- {record.id}: {record.workflow.get('name') or record.title or '[unnamed workflow]'} ({record.status}/{record.phase})"
            for record in sorted(records, key=lambda item: item.id)
        )

    def runtime_metadata(
        self,
        *,
        requested_auto: bool = False,
        session_key: str = "",
        harness_id: str = "",
    ) -> dict[str, Any]:
        snapshot = self.store.load()
        active = self._resolve_harness_target(
            snapshot,
            harness_id=harness_id,
            session_key=session_key,
        )
        if active is None:
            return {"has_active_harness": False}

        active_payload = self._runtime_payload(active, requested_auto=requested_auto)
        main = active
        if active.parent_id:
            parent = snapshot.records.get(active.parent_id)
            if parent is not None:
                main = parent

        main_payload = self._runtime_payload(main, requested_auto=active_payload["auto"])
        payload: dict[str, Any] = {
            "has_active_harness": True,
            "active_harness": active_payload,
            "main_harness": main_payload,
            "next_runnable_child": None,
            "stop_gate_child": None,
        }

        if main.type != "project":
            return payload

        children = [record for record in snapshot.records.values() if record.parent_id == main.id]
        children.sort(
            key=lambda item: (
                self._record_queue_order(item),
                item.created_at or item.updated_at,
                item.id,
            )
        )
        main_payload["has_open_children"] = any(
            not self._is_completed(record) for record in children
        )
        for child in children:
            if payload["stop_gate_child"] is None and self._is_stop_gate(child):
                payload["stop_gate_child"] = self._runtime_child_payload(child)
                break
            if payload["next_runnable_child"] is None and not self._is_completed(child):
                payload["next_runnable_child"] = self._runtime_child_payload(child)
        return payload

    def runtime_metadata_for_session(
        self,
        *,
        requested_auto: bool = False,
        session_key: str = "",
    ) -> dict[str, Any]:
        bound_session_key = session_key.strip()
        if not bound_session_key:
            return {"has_active_harness": False}
        snapshot = self.store.load()
        active = self._get_session_bound_record(snapshot, session_key=bound_session_key)
        if active is None:
            return {"has_active_harness": False}

        active_payload = self._runtime_payload(active, requested_auto=requested_auto)
        main = active
        if active.parent_id:
            parent = snapshot.records.get(active.parent_id)
            if parent is not None:
                main = parent

        main_payload = self._runtime_payload(main, requested_auto=active_payload["auto"])
        payload: dict[str, Any] = {
            "has_active_harness": True,
            "active_harness": active_payload,
            "main_harness": main_payload,
            "next_runnable_child": None,
            "stop_gate_child": None,
        }

        if main.type != "project":
            return payload

        children = [record for record in snapshot.records.values() if record.parent_id == main.id]
        children.sort(
            key=lambda item: (
                self._record_queue_order(item),
                item.created_at or item.updated_at,
                item.id,
            )
        )
        main_payload["has_open_children"] = any(
            not self._is_completed(record) for record in children
        )
        for child in children:
            if payload["stop_gate_child"] is None and self._is_stop_gate(child):
                payload["stop_gate_child"] = self._runtime_payload(
                    child, requested_auto=requested_auto
                )
            if payload["next_runnable_child"] is None and self._is_runnable(child):
                payload["next_runnable_child"] = self._runtime_payload(
                    child, requested_auto=requested_auto
                )
        return payload

    def decide_auto_continue(
        self,
        *,
        session_key: str,
        sender_id: str,
        origin_sender_id: str = "",
        harness_id: str = "",
    ) -> HarnessAutoContinueDecision:
        runtime_meta = self.runtime_metadata(
            requested_auto=True,
            session_key=session_key,
            harness_id=harness_id,
        )
        origin = origin_sender_id or sender_id
        if not bool(runtime_meta.get("has_active_harness")):
            return HarnessAutoContinueDecision(
                should_fire=False,
                reason="no_active_harness",
                origin_sender_id=origin,
            )

        active_harness = runtime_meta.get("active_harness")
        main_harness = runtime_meta.get("main_harness")
        stop_gate_child = runtime_meta.get("stop_gate_child")
        next_runnable_child = runtime_meta.get("next_runnable_child")
        active = active_harness if isinstance(active_harness, dict) else {}
        main = main_harness if isinstance(main_harness, dict) else active

        if isinstance(stop_gate_child, dict):
            if bool(stop_gate_child.get("awaiting_user")):
                return HarnessAutoContinueDecision(False, "awaiting_user", origin)
            if bool(stop_gate_child.get("blocked")):
                return HarnessAutoContinueDecision(False, "blocked", origin)
            stop_status = str(stop_gate_child.get("status") or "").strip().lower()
            if stop_status in {"awaiting_decision", "failed", "interrupted"}:
                return HarnessAutoContinueDecision(False, stop_status, origin)

        if bool(main.get("awaiting_user")):
            return HarnessAutoContinueDecision(False, "awaiting_user", origin)
        if bool(main.get("blocked")):
            return HarnessAutoContinueDecision(False, "blocked", origin)

        main_status = str(main.get("status") or "").strip().lower()
        has_open_children = bool(main.get("has_open_children"))
        if main_status == "completed" and not (
            has_open_children and isinstance(next_runnable_child, dict)
        ):
            return HarnessAutoContinueDecision(False, "completed", origin)

        if isinstance(next_runnable_child, dict):
            child_status = str(next_runnable_child.get("status") or "").strip().lower()
            if child_status in {"planning", "active"}:
                return HarnessAutoContinueDecision(True, "continue", origin)

        status = str(active.get("status") or "").strip().lower()
        if status == "completed":
            return HarnessAutoContinueDecision(False, "completed", origin)
        if bool(active.get("awaiting_user")):
            return HarnessAutoContinueDecision(False, "awaiting_user", origin)
        if bool(active.get("blocked")):
            return HarnessAutoContinueDecision(False, "blocked", origin)
        return HarnessAutoContinueDecision(True, "continue", origin)

    def build_auto_continue_metadata(
        self,
        metadata: dict[str, Any] | None,
        *,
        origin_sender_id: str,
        session_key: str = "",
        harness_id: str = "",
    ) -> dict[str, Any]:
        base = dict(metadata or {})
        base["_auto_continue"] = True
        base["_origin_sender_id"] = origin_sender_id
        if harness_id:
            base["workspace_harness_id"] = harness_id
        runtime_meta = self.runtime_metadata(
            requested_auto=bool(base.get("workspace_harness_auto")),
            session_key=session_key,
            harness_id=harness_id,
        )
        if runtime_meta:
            base["workspace_runtime"] = runtime_meta
        return base

    def interrupt_active(
        self, summary: str, *, session_key: str = "", harness_id: str = ""
    ) -> bool:
        snapshot = self.store.load()
        active = self._resolve_harness_target(
            snapshot,
            harness_id=harness_id,
            session_key=session_key,
        )
        if active is None:
            return False
        active.status = "interrupted"
        active.phase = "interrupted"
        active.summary = summary.strip() or active.summary
        active.resume_hint = summary.strip() or active.resume_hint
        active.updated_at = timestamp()
        self._save_snapshot(snapshot)
        return True

    def redirect_after_interrupt(
        self,
        user_input: str,
        *,
        session_key: str = "",
        harness_id: str = "",
    ) -> bool:
        snapshot = self.store.load()
        active = self._resolve_harness_target(
            snapshot,
            harness_id=harness_id,
            session_key=session_key,
        )
        if active is None or active.status != "interrupted":
            return False
        active.status = "active"
        active.phase = "planning"
        active.resume_hint = user_input.strip() or active.resume_hint
        active.next_step = user_input.strip() or active.next_step
        active.updated_at = timestamp()
        self._save_snapshot(snapshot)
        return True

    def redirect_active(self, user_input: str) -> bool:
        return self.redirect_after_interrupt(user_input)

    def clear_session_binding(self, session_key: str) -> bool:
        bound_session_key = session_key.strip()
        if not bound_session_key:
            return False
        snapshot = self.store.load()
        changed = False
        for record in snapshot.records.values():
            if record.runtime_state.session_key == bound_session_key:
                record.runtime_state.session_key = ""
                changed = True
        if changed:
            self._save_snapshot(snapshot)
        return changed

    def apply_agent_update(
        self,
        content: str,
        *,
        session_key: str,
        harness_id: str = "",
    ) -> HarnessApplyResult:
        payload = self._extract_harness_update_payload(content)
        if not isinstance(payload, dict):
            return HarnessApplyResult(
                final_content=content,
                closeout_required=False,
                closeout_summary="",
            )

        snapshot = self.store.load()
        target = self._resolve_apply_target(
            snapshot, harness_id=harness_id, session_key=session_key
        )
        if target is None:
            return HarnessApplyResult(
                final_content=content,
                closeout_required=False,
                closeout_summary="",
            )

        self._apply_record_update(target, payload)
        self._save_snapshot(snapshot)

        closeout_required = self._is_completed(target)
        closeout_summary = self._build_closeout_summary(target) if closeout_required else ""
        return HarnessApplyResult(
            final_content=closeout_summary or content,
            closeout_required=closeout_required,
            closeout_summary=closeout_summary,
        )

    def sync_projections(self) -> None:
        sync_workspace_projections(self.workspace_root, self.store.load())

    def get_projection_status(self) -> dict[str, str]:
        store_path = self.store.store_path
        root_task_path = self.workspace_root / "TASK.md"
        source_path = root_task_path if root_task_path.exists() else store_path
        last_sync = ""
        if source_path.exists():
            last_sync = datetime.fromtimestamp(
                source_path.stat().st_mtime, tz=timezone.utc
            ).replace(microsecond=0).isoformat()
        return {
            "store_path": str(store_path),
            "root_task_path": str(root_task_path),
            "last_sync": last_sync,
        }

    def _resolve_apply_target(
        self,
        snapshot: HarnessSnapshot,
        *,
        harness_id: str,
        session_key: str,
    ) -> HarnessRecord | None:
        return self._resolve_harness_target(
            snapshot,
            harness_id=harness_id,
            session_key=session_key,
        )

    def _resolve_harness_target(
        self,
        snapshot: HarnessSnapshot,
        *,
        harness_id: str,
        session_key: str,
    ) -> HarnessRecord | None:
        target_id = harness_id.strip()
        if target_id:
            return snapshot.records.get(target_id)
        bound_session_key = session_key.strip()
        if bound_session_key:
            record = self._get_session_bound_record(snapshot, session_key=bound_session_key)
            if record is not None:
                return record
        return self._get_active_record(snapshot)

    @staticmethod
    def _get_session_bound_record(
        snapshot: HarnessSnapshot,
        *,
        session_key: str,
    ) -> HarnessRecord | None:
        bound_session_key = session_key.strip()
        if not bound_session_key:
            return None
        for record in snapshot.records.values():
            if record.runtime_state.session_key == bound_session_key:
                return record
        return None

    @staticmethod
    def _bind_session(
        snapshot: HarnessSnapshot,
        *,
        active_record: HarnessRecord,
        session_key: str,
    ) -> None:
        bound_session_key = session_key.strip()
        if not bound_session_key:
            return
        for record in snapshot.records.values():
            if (
                record.id != active_record.id
                and record.runtime_state.session_key == bound_session_key
            ):
                record.runtime_state.session_key = ""
        active_record.runtime_state.session_key = bound_session_key

    def _create_work_harness(self, snapshot: HarnessSnapshot, goal: str) -> HarnessRecord:
        record = HarnessRecord(
            id=self._next_work_harness_id(snapshot),
            kind="work",
            type="feature",
            title=goal,
            status="active",
            phase="planning",
            summary="",
            created_at=timestamp(),
            updated_at=timestamp(),
        )
        record.queue_order = self._next_queue_order(snapshot)
        snapshot.records[record.id] = record
        snapshot.active_harness_id = record.id
        return record

    def _create_workflow_harness(
        self,
        snapshot: HarnessSnapshot,
        definition: WorkflowDefinition,
    ) -> HarnessRecord:
        record = HarnessRecord(
            id=definition.stable_harness_id,
            kind="workflow",
            type="workflow",
            title=definition.title,
            status="active",
            phase="planning",
            workflow={
                "name": definition.name,
                "spec_path": "",
                "spec_hash": "",
                "memory": {},
                "return_to": "",
                "awaiting_confirmation": False,
            },
            created_at=timestamp(),
            updated_at=timestamp(),
        )
        record.queue_order = self._next_queue_order(snapshot)
        snapshot.records[record.id] = record
        return record

    def _build_goal_prepared_input(
        self,
        *,
        snapshot: HarnessSnapshot,
        active_record: HarnessRecord,
        requested_goal: str,
        session_key: str,
        sender_id: str,
    ) -> str:
        lines = [
            "You are executing the runtime harness service.",
            f"session_key: {session_key}",
            f"sender_id: {sender_id}",
            f"requested_goal: {requested_goal or active_record.title}",
            "",
            "## Current Harness Snapshot",
            *self._snapshot_lines(active_record),
        ]
        if active_record.kind == "workflow":
            lines.extend(["", "## Workflow Context", *self._workflow_lines(active_record)])
        return "\n".join(lines)

    def _build_workflow_prepared_input(
        self,
        *,
        snapshot: HarnessSnapshot,
        workflow: HarnessRecord,
        origin_command: str,
    ) -> str:
        lines = [
            "You are executing the runtime harness workflow service.",
            f"origin_command: {origin_command}",
            "",
            "## Current Harness Snapshot",
            *self._snapshot_lines(workflow),
            "",
            "## Workflow Context",
            *self._workflow_lines(workflow),
        ]
        return "\n".join(lines)

    def _snapshot_lines(self, record: HarnessRecord) -> list[str]:
        return [
            f"- id: {record.id}",
            f"- kind: {record.kind}",
            f"- type: {record.type}",
            f"- title: {record.title}",
            f"- status: {record.status}",
            f"- phase: {record.phase}",
            f"- summary: {record.summary}",
            f"- next_step: {record.next_step}",
            f"- resume_hint: {record.resume_hint}",
            f"- active_harness_id: {record.id}",
        ]

    def _workflow_lines(self, record: HarnessRecord) -> list[str]:
        return [
            f"- workflow_name: {record.workflow.get('name', '')}",
            f"- return_to: {record.workflow.get('return_to', '')}",
            f"- memory: {record.workflow.get('memory', {})}",
        ]

    def _get_active_record(self, snapshot: HarnessSnapshot) -> HarnessRecord | None:
        active_id = snapshot.active_harness_id
        if not active_id:
            return None
        return snapshot.records.get(active_id)

    @staticmethod
    def _is_completed(record: HarnessRecord) -> bool:
        return record.status == "completed" or record.phase == "completed"

    @staticmethod
    def _is_stop_gate(record: HarnessRecord) -> bool:
        if record.awaiting_user or record.blocked:
            return True
        return record.status in {
            "awaiting_decision",
            "blocked",
            "failed",
            "interrupted",
        } or record.phase in {
            "awaiting_decision",
            "blocked",
            "failed",
            "interrupted",
        }

    @staticmethod
    def _record_queue_order(record: HarnessRecord) -> int:
        if record.queue_order > 0:
            return record.queue_order
        if record.id.startswith("har_") and record.id[4:].isdigit():
            return int(record.id[4:])
        return 10**9

    def _runtime_payload(self, record: HarnessRecord, *, requested_auto: bool) -> dict[str, Any]:
        return {
            "id": record.id,
            "type": record.type,
            "status": record.status,
            "phase": record.phase,
            "awaiting_user": record.awaiting_user,
            "blocked": record.blocked,
            "auto": requested_auto or record.execution_policy.auto_continue,
            "executor_mode": record.execution_policy.executor_mode,
            "delegation_level": record.execution_policy.delegation_level,
            "risk_level": record.execution_policy.risk_level,
            "subagent_allowed": record.execution_policy.subagent_allowed,
            "subagent_profile": record.execution_policy.subagent_profile,
            "runner": record.runtime_state.runner,
        }

    def _runtime_child_payload(self, record: HarnessRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "type": record.type,
            "status": record.status,
            "phase": record.phase,
            "awaiting_user": record.awaiting_user,
            "blocked": record.blocked,
            "auto": record.execution_policy.auto_continue,
            "executor_mode": record.execution_policy.executor_mode,
            "delegation_level": record.execution_policy.delegation_level,
            "risk_level": record.execution_policy.risk_level,
            "subagent_allowed": record.execution_policy.subagent_allowed,
            "subagent_profile": record.execution_policy.subagent_profile,
            "runner": record.runtime_state.runner,
        }

    def _extract_harness_update_payload(self, content: str) -> dict[str, Any] | None:
        match = self._JSON_BLOCK_RE.search(content or "")
        if match is None:
            return None
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        harness = payload.get("harness")
        return harness if isinstance(harness, dict) else None

    def _apply_record_update(self, record: HarnessRecord, payload: dict[str, Any]) -> None:
        direct_fields = {
            "status": "status",
            "phase": "phase",
            "summary": "summary",
            "next_step": "next_step",
            "resume_hint": "resume_hint",
        }
        for payload_key, attr_name in direct_fields.items():
            value = payload.get(payload_key)
            if value is not None:
                setattr(record, attr_name, str(value).strip())
        for payload_key, attr_name in {
            "awaiting_user": "awaiting_user",
            "blocked": "blocked",
        }.items():
            value = payload.get(payload_key)
            if isinstance(value, bool):
                setattr(record, attr_name, value)
        if payload.get("verification_status") is not None:
            record.verification["status"] = str(payload.get("verification_status") or "").strip()
        if payload.get("verification_summary") is not None:
            record.verification["summary"] = str(payload.get("verification_summary") or "").strip()
        if payload.get("git_delivery_status") is not None:
            record.git_delivery["status"] = str(payload.get("git_delivery_status") or "").strip()
        if payload.get("git_delivery_summary") is not None:
            record.git_delivery["summary"] = str(payload.get("git_delivery_summary") or "").strip()
        record.updated_at = timestamp()

    def _build_closeout_summary(self, record: HarnessRecord) -> str:
        lines = [record.summary.strip()] if record.summary.strip() else []
        verification_summary = str(record.verification.get("summary") or "").strip()
        git_summary = str(record.git_delivery.get("summary") or "").strip()
        if verification_summary:
            lines.append(f"Verification: {verification_summary}")
        if git_summary:
            lines.append(f"Git: {git_summary}")
        return "\n".join(lines).strip() or "Harness completed."

    def _next_queue_order(self, snapshot: HarnessSnapshot) -> int:
        if not snapshot.records:
            return 1
        return max(record.queue_order for record in snapshot.records.values()) + 1

    def _next_work_harness_id(self, snapshot: HarnessSnapshot) -> str:
        index = 1
        while True:
            candidate = f"har_{index:04d}"
            if candidate not in snapshot.records:
                return candidate
            index += 1

    def _save_snapshot(self, snapshot: HarnessSnapshot) -> None:
        snapshot.updated_at = timestamp()
        self.store.save(snapshot)
        sync_workspace_projections(self.workspace_root, snapshot)

    def _workflow_return_target(self, *, prior_active_id: str, workflow_id: str) -> str:
        if not prior_active_id or prior_active_id == workflow_id:
            return ""
        return prior_active_id
