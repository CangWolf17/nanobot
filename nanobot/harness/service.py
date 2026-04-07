from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.harness.models import HarnessRecord, HarnessSnapshot
from nanobot.harness.store import HarnessStore
from nanobot.harness.workflows import WorkflowDefinition, get_workflow_definition
from nanobot.utils.helpers import timestamp


@dataclass(frozen=True)
class HarnessCommandResult:
    agent_cmd: str
    active_harness_id: str
    prepared_input: str


@dataclass(frozen=True)
class WorkflowStartResult:
    workflow_id: str
    created_copy: bool
    prepared_input: str


@dataclass
class HarnessService:
    workspace_root: Path
    store: HarnessStore

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
        if not command.startswith("/harness"):
            raise ValueError("expected a /harness command")

        goal = command[len("/harness") :].strip()
        if goal.lower() == "cleanup":
            workflow = self.start_workflow("cleanup", origin_command=command)
            return HarnessCommandResult(
                agent_cmd="harness",
                active_harness_id=workflow.workflow_id,
                prepared_input=workflow.prepared_input,
            )

        snapshot = self.store.load()
        active_record = self._get_active_record(snapshot)
        if active_record is None:
            if not goal:
                raise ValueError("/harness requires a goal when no active harness exists")
            active_record = self._create_work_harness(snapshot, goal)
            self._save_snapshot(snapshot)
        prepared_input = self._build_goal_prepared_input(
            snapshot=snapshot,
            active_record=active_record,
            requested_goal=goal,
            session_key=session_key,
            sender_id=sender_id,
        )
        return HarnessCommandResult(
            agent_cmd="harness",
            active_harness_id=active_record.id,
            prepared_input=prepared_input,
        )

    def start_workflow(
        self,
        workflow_name: str,
        *,
        origin_command: str,
        requested_goal_after_cleanup: str = "",
    ) -> WorkflowStartResult:
        snapshot = self.store.load()
        definition = get_workflow_definition(workflow_name)
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
        memory = dict(record.workflow.get("memory") or {})
        if requested_goal_after_cleanup:
            memory["requested_goal_after_cleanup"] = requested_goal_after_cleanup
        record.workflow["memory"] = memory
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
            f"- memory: {record.workflow.get('memory', {})}",
        ]

    def _get_active_record(self, snapshot: HarnessSnapshot) -> HarnessRecord | None:
        active_id = snapshot.active_harness_id
        if not active_id:
            return None
        return snapshot.records.get(active_id)

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
