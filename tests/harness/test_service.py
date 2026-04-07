from pathlib import Path

from nanobot.harness.service import HarnessService


def test_service_start_goal_creates_or_resumes_active_harness(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    created = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )

    assert created.agent_cmd == "harness"
    assert created.active_harness_id.startswith("har_")
    assert "Current Harness Snapshot" in created.prepared_input

    resumed = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )

    assert resumed.active_harness_id == created.active_harness_id


def test_service_start_workflow_reuses_stable_alias_without_copying_caller_task(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)

    result = service.start_workflow("cleanup", origin_command="/harness cleanup")

    assert result.workflow_id == "har_cleanup"
    assert result.created_copy is False


def test_service_start_workflow_stores_requested_goal_and_sets_active_harness(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)

    result = service.start_workflow(
        "cleanup",
        origin_command="/harness cleanup",
        requested_goal_after_cleanup="resume original goal",
    )

    snapshot = service.store.load()
    record = snapshot.records[result.workflow_id]

    assert snapshot.active_harness_id == "har_cleanup"
    assert record.kind == "workflow"
    assert record.workflow["name"] == "cleanup"
    assert record.workflow["memory"]["requested_goal_after_cleanup"] == "resume original goal"


def test_service_renderers_use_canonical_store_views(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    assert "no active harness" in service.render_status().lower()

    work_result = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    service.start_workflow("cleanup", origin_command="/harness cleanup")

    status_text = service.render_status()
    list_text = service.render_list()
    workflows_text = service.render_workflows()

    assert "har_cleanup" in status_text
    assert work_result.active_harness_id in list_text
    assert "cleanup" not in list_text.lower()
    assert "har_cleanup" in workflows_text
    assert "cleanup" in workflows_text.lower()
