import json
from pathlib import Path

from nanobot.harness.models import (
    HarnessExecutionPolicy,
    HarnessRecord,
    HarnessRuntimeState,
    HarnessSnapshot,
)
from nanobot.harness.store import HarnessStore


def _write_legacy_workspace_files(root: Path) -> None:
    harness_dir = root / "harnesses" / "har_0001"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (root / "harnesses" / "index.json").write_text(
        json.dumps(
            {
                "harnesses": {
                    "har_0001": {
                        "id": "har_0001",
                        "kind": "workflow",
                        "type": "project",
                        "title": "Legacy harness",
                        "parent_id": "har_0000",
                        "queue_order": 7,
                        "status": "active",
                        "phase": "executing",
                        "summary": "migrated from legacy index",
                        "awaiting_user": True,
                        "blocked": True,
                        "next_step": "continue next child",
                        "resume_hint": "check child queue",
                        "verification_status": "passed",
                        "verification_summary": "legacy verification summary",
                        "artifacts": ["index-artifact"],
                        "pending_decisions": ["pick a child"],
                        "workflow_name": "merge",
                        "workflow_spec_path": "/specs/merge.yaml",
                        "workflow_spec_hash": "sha256:index",
                        "workflow_memory": {"phase": "index"},
                        "return_to": "har_0000",
                        "awaiting_confirmation": True,
                        "git_delivery_status": "committed",
                        "git_delivery_summary": "legacy git delivery",
                        "created_at": "2026-04-07T09:00:00",
                        "updated_at": "2026-04-07T10:00:00",
                        "path": str(harness_dir),
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "harnesses" / "control.json").write_text(
        json.dumps(
            {"active_harness_id": "har_0001", "updated_at": "2026-04-07T10:30:00"}, indent=2
        ),
        encoding="utf-8",
    )
    (harness_dir / "state.json").write_text(
        json.dumps(
            {
                "summary": "state summary wins",
                "executor_mode": "auto",
                "delegation_level": "required",
                "risk_level": "sensitive",
                "auto_continue": True,
                "subagent_allowed": True,
                "subagent_profile": "reviewer",
                "runner": "subagent",
                "subagent_status": "running",
                "subagent_last_run_id": "run-123",
                "subagent_last_error": "",
                "subagent_last_summary": "state runtime summary",
                "auto_state": "queued",
                "continuation_token": "token-1",
                "artifacts": ["state-artifact"],
                "pending_decisions": ["approve workflow"],
                "workflow_memory": {"phase": "state"},
                "verification_status": "failed",
                "verification_summary": "state verification summary",
                "git_delivery_status": "not_set",
                "git_delivery_summary": "state git delivery",
                "updated_at": "2026-04-07T10:15:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_store_migrates_legacy_index_control_and_state_into_single_store(tmp_path: Path) -> None:
    _write_legacy_workspace_files(tmp_path)

    store = HarnessStore.for_workspace(tmp_path)
    snapshot = store.load()

    record = snapshot.records["har_0001"]
    assert snapshot.active_harness_id == "har_0001"
    assert snapshot.updated_at == "2026-04-07T10:30:00"
    assert record.kind == "workflow"
    assert record.type == "project"
    assert record.parent_id == "har_0000"
    assert record.queue_order == 7
    assert record.status == "active"
    assert record.phase == "executing"
    assert record.summary == "state summary wins"
    assert record.awaiting_user is True
    assert record.blocked is True
    assert record.next_step == "continue next child"
    assert record.resume_hint == "check child queue"
    assert record.verification == {
        "status": "failed",
        "summary": "state verification summary",
        "artifacts": ["state-artifact"],
    }
    assert record.git_delivery == {
        "status": "not_set",
        "summary": "state git delivery",
    }
    assert record.pending_decisions == ["approve workflow"]
    assert record.artifacts == ["state-artifact"]
    assert record.workflow == {
        "name": "merge",
        "spec_path": "/specs/merge.yaml",
        "spec_hash": "sha256:index",
        "memory": {"phase": "state"},
        "return_to": "har_0000",
        "awaiting_confirmation": True,
    }
    assert record.created_at == "2026-04-07T09:00:00"
    assert record.updated_at == "2026-04-07T10:15:00"
    assert record.execution_policy == HarnessExecutionPolicy(
        executor_mode="auto",
        delegation_level="required",
        risk_level="sensitive",
        auto_continue=True,
        subagent_allowed=True,
        subagent_profile="reviewer",
    )
    assert record.runtime_state == HarnessRuntimeState(
        runner="subagent",
        subagent_status="running",
        subagent_last_run_id="run-123",
        subagent_last_error="",
        subagent_last_summary="state runtime summary",
        auto_state="queued",
        continuation_token="token-1",
    )
    assert (tmp_path / "harnesses" / "store.json").exists()


def test_store_save_writes_exact_canonical_json_shape(tmp_path: Path) -> None:
    snapshot = HarnessSnapshot(
        version=1,
        updated_at="2026-04-07T11:00:00",
        active_harness_id="har_0002",
        records={
            "har_0002": HarnessRecord(
                id="har_0002",
                kind="work",
                type="feature",
                title="Canonical harness",
                parent_id="",
                queue_order=2,
                status="active",
                phase="planning",
                summary="saved to canonical store",
                awaiting_user=False,
                blocked=False,
                next_step="implement feature",
                resume_hint="open store",
                verification={"status": "not_run", "summary": "", "artifacts": []},
                git_delivery={"status": "", "summary": ""},
                pending_decisions=[],
                artifacts=[],
                workflow={
                    "name": "",
                    "spec_path": "",
                    "spec_hash": "",
                    "memory": {},
                    "return_to": "",
                    "awaiting_confirmation": False,
                },
                created_at="2026-04-07T10:50:00",
                updated_at="2026-04-07T11:00:00",
                execution_policy=HarnessExecutionPolicy(),
                runtime_state=HarnessRuntimeState(),
            )
        },
    )

    store = HarnessStore.for_workspace(tmp_path)
    store.save(snapshot)

    payload = json.loads((tmp_path / "harnesses" / "store.json").read_text(encoding="utf-8"))
    assert payload == snapshot.to_dict()


def test_store_does_not_read_markdown_projections_as_runtime_input(tmp_path: Path) -> None:
    _write_legacy_workspace_files(tmp_path)
    harness_dir = tmp_path / "harnesses" / "har_0001"
    (harness_dir / "TASK.md").write_text("stale summary", encoding="utf-8")
    (harness_dir / "HARNESS.md").write_text("stale phase", encoding="utf-8")

    store = HarnessStore.for_workspace(tmp_path)
    snapshot = store.load()

    (harness_dir / "TASK.md").write_text("rewritten markdown should stay ignored", encoding="utf-8")
    reloaded = store.load()

    assert snapshot.records["har_0001"].summary == "state summary wins"
    assert reloaded.records["har_0001"].summary == "state summary wins"
    assert reloaded.records["har_0001"].phase == "executing"
