from nanobot.harness.models import (
    HarnessExecutionPolicy,
    HarnessRecord,
    HarnessRuntimeState,
    HarnessSnapshot,
)


def test_snapshot_to_dict_includes_canonical_store_fields() -> None:
    snapshot = HarnessSnapshot(
        version=2,
        updated_at="2026-04-07T10:00:00",
        active_harness_id="har_0001",
        records={
            "har_0001": HarnessRecord(
                id="har_0001",
                kind="workflow",
                type="project",
                title="Legacy harness",
                parent_id="har_0000",
                queue_order=3,
                status="active",
                phase="executing",
                summary="keep canonical state here",
                awaiting_user=True,
                blocked=False,
                next_step="continue",
                resume_hint="resume from child",
                verification={
                    "status": "passed",
                    "summary": "focused pytest passed",
                    "artifacts": ["pytest -q"],
                },
                git_delivery={
                    "status": "committed",
                    "summary": "commit abc123",
                },
                pending_decisions=["approve merge"],
                artifacts=["artifact.txt"],
                workflow={
                    "name": "merge",
                    "spec_path": "/specs/merge.yaml",
                    "spec_hash": "sha256:abc",
                    "memory": {"phase": "review"},
                    "return_to": "har_0000",
                    "awaiting_confirmation": True,
                },
                created_at="2026-04-07T09:00:00",
                updated_at="2026-04-07T10:00:00",
                execution_policy=HarnessExecutionPolicy(
                    executor_mode="auto", subagent_allowed=True
                ),
                runtime_state=HarnessRuntimeState(runner="subagent", subagent_status="running"),
            )
        },
    )

    payload = snapshot.to_dict()

    assert payload == {
        "version": 2,
        "updated_at": "2026-04-07T10:00:00",
        "active_harness_id": "har_0001",
        "records": {
            "har_0001": {
                "id": "har_0001",
                "kind": "workflow",
                "type": "project",
                "title": "Legacy harness",
                "parent_id": "har_0000",
                "queue_order": 3,
                "status": "active",
                "phase": "executing",
                "summary": "keep canonical state here",
                "awaiting_user": True,
                "blocked": False,
                "next_step": "continue",
                "resume_hint": "resume from child",
                "verification": {
                    "status": "passed",
                    "summary": "focused pytest passed",
                    "artifacts": ["pytest -q"],
                },
                "git_delivery": {
                    "status": "committed",
                    "summary": "commit abc123",
                },
                "pending_decisions": ["approve merge"],
                "artifacts": ["artifact.txt"],
                "workflow": {
                    "name": "merge",
                    "spec_path": "/specs/merge.yaml",
                    "spec_hash": "sha256:abc",
                    "memory": {"phase": "review"},
                    "return_to": "har_0000",
                    "awaiting_confirmation": True,
                },
                "created_at": "2026-04-07T09:00:00",
                "updated_at": "2026-04-07T10:00:00",
                "execution_policy": HarnessExecutionPolicy(
                    executor_mode="auto", subagent_allowed=True
                ).to_dict(),
                "runtime_state": HarnessRuntimeState(
                    runner="subagent", subagent_status="running"
                ).to_dict(),
            }
        },
    }


def test_snapshot_from_dict_normalizes_invalid_enum_and_boolean_values() -> None:
    snapshot = HarnessSnapshot.from_dict(
        {
            "version": "2",
            "updated_at": "2026-04-07T10:00:00",
            "active_harness_id": "har_0001",
            "records": {
                "har_0001": {
                    "id": "har_0001",
                    "kind": "unknown-kind",
                    "type": "unknown-type",
                    "status": "unknown-status",
                    "phase": "unknown-phase",
                    "awaiting_user": "false",
                    "blocked": "TRUE",
                    "workflow": {"awaiting_confirmation": "false"},
                    "execution_policy": {
                        "executor_mode": "bad-mode",
                        "delegation_level": "bad-level",
                        "risk_level": "bad-risk",
                        "auto_continue": "false",
                        "subagent_allowed": "TRUE",
                    },
                    "runtime_state": {
                        "runner": "bad-runner",
                        "subagent_status": "bad-status",
                        "auto_state": "bad-auto",
                    },
                }
            },
        }
    )

    record = snapshot.records["har_0001"]

    assert snapshot.version == 2
    assert record.kind == "work"
    assert record.type == "feature"
    assert record.status == "active"
    assert record.phase == "planning"
    assert record.awaiting_user is False
    assert record.blocked is True
    assert record.workflow["awaiting_confirmation"] is False
    assert record.execution_policy == HarnessExecutionPolicy(
        executor_mode="main",
        delegation_level="assist",
        risk_level="normal",
        auto_continue=False,
        subagent_allowed=True,
    )
    assert record.runtime_state == HarnessRuntimeState(
        runner="main",
        subagent_status="idle",
        auto_state="idle",
    )
