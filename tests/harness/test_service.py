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
    assert record.workflow["return_to"] == ""


def test_service_start_workflow_reuse_refreshes_goal_memory_and_return_to(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    work_result = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    first = service.start_workflow(
        "cleanup",
        origin_command="/harness cleanup",
        requested_goal_after_cleanup="first goal",
    )

    snapshot = service.store.load()
    snapshot.active_harness_id = work_result.active_harness_id
    service.store.save(snapshot)

    second = service.start_workflow("cleanup", origin_command="/harness cleanup")

    reused_snapshot = service.store.load()
    record = reused_snapshot.records[second.workflow_id]

    assert first.workflow_id == "har_cleanup"
    assert second.workflow_id == "har_cleanup"
    assert second.created_copy is False
    assert record.workflow["memory"] == {}
    assert record.workflow["return_to"] == work_result.active_harness_id
    assert f"- return_to: {work_result.active_harness_id}" in second.prepared_input


def test_service_start_goal_while_cleanup_is_active_creates_work_harness(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.start_workflow("cleanup", origin_command="/harness cleanup")

    result = service.handle_command(
        "/harness 修复 cleanup 后的恢复接线",
        session_key="feishu:c1",
        sender_id="u1",
    )

    snapshot = service.store.load()
    record = snapshot.records[result.active_harness_id]

    assert result.active_harness_id != "har_cleanup"
    assert record.kind == "work"
    assert record.title == "修复 cleanup 后的恢复接线"
    assert snapshot.active_harness_id == result.active_harness_id


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


def test_harness_auto_continue_decision_comes_from_service_not_workspace_projection(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )

    decision = service.decide_auto_continue(session_key="feishu:c1", sender_id="u1")

    assert decision.reason in {"continue", "awaiting_user", "blocked", "completed"}


def test_auto_continue_decision_falls_back_to_session_bound_harness(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    first = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    snapshot = service.store.load()
    snapshot.active_harness_id = ""
    service.store.save(snapshot)
    second = service.handle_command(
        "/harness 修复 delayed apply 目标绑定",
        session_key="feishu:c2",
        sender_id="u2",
    )

    snapshot = service.store.load()
    snapshot.records[second.active_harness_id].blocked = True
    snapshot.active_harness_id = second.active_harness_id
    service.store.save(snapshot)

    decision = service.decide_auto_continue(session_key="feishu:c1", sender_id="u1")

    assert decision.should_fire is True
    assert decision.reason == "continue"


def test_interrupt_updates_harness_state_without_workspace_router_subprocess(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    service.interrupt_active("interrupted — waiting for redirect")

    status = service.render_status()

    assert "interrupted" in status.lower()


def test_redirect_after_interrupt_resumes_active_harness_from_service(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    service.interrupt_active("interrupted — waiting for redirect")

    redirected = service.redirect_after_interrupt("继续处理 redirect 接线")
    status = service.render_status().lower()

    assert redirected is True
    assert "active" in status
    assert "planning" in status


def test_redirect_after_interrupt_falls_back_to_session_bound_harness(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    first = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    snapshot = service.store.load()
    snapshot.active_harness_id = ""
    service.store.save(snapshot)
    second = service.handle_command(
        "/harness 修复 delayed apply 目标绑定",
        session_key="feishu:c2",
        sender_id="u2",
    )

    service.interrupt_active("interrupted — waiting for redirect", session_key="feishu:c1")

    snapshot = service.store.load()
    snapshot.records[second.active_harness_id].status = "interrupted"
    snapshot.records[second.active_harness_id].phase = "interrupted"
    snapshot.active_harness_id = second.active_harness_id
    service.store.save(snapshot)

    redirected = service.redirect_after_interrupt("继续处理 redirect 接线", session_key="feishu:c1")
    refreshed = service.store.load()

    assert redirected is True
    assert refreshed.records[first.active_harness_id].status == "active"
    assert refreshed.records[second.active_harness_id].status == "interrupted"


def test_structured_harness_apply_updates_store_and_generates_closeout(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    update = """```json
    {"harness": {"status": "completed", "phase": "completed", "summary": "done", "verification_status": "passed", "verification_summary": "focused tests passed", "git_delivery_status": "no_commit_required", "git_delivery_summary": "analysis-only"}}
    ```"""

    result = service.apply_agent_update(update, session_key="feishu:c1")

    assert result.closeout_required is True
    assert "focused tests passed" in result.closeout_summary


def test_structured_harness_apply_targets_bound_harness_not_current_active(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    first = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    snapshot = service.store.load()
    snapshot.active_harness_id = ""
    service.store.save(snapshot)
    second = service.handle_command(
        "/harness 修复 delayed apply 目标绑定",
        session_key="feishu:c2",
        sender_id="u2",
    )

    snapshot = service.store.load()
    snapshot.active_harness_id = second.active_harness_id
    service.store.save(snapshot)

    update = """```json
    {"harness": {"status": "completed", "phase": "completed", "summary": "done", "verification_status": "passed", "verification_summary": "focused tests passed", "git_delivery_status": "no_commit_required", "git_delivery_summary": "analysis-only"}}
    ```"""

    service.apply_agent_update(
        update,
        session_key="feishu:c1",
        harness_id=first.active_harness_id,
    )

    refreshed = service.store.load()

    assert refreshed.records[first.active_harness_id].status == "completed"
    assert refreshed.records[second.active_harness_id].status == "active"


def test_structured_harness_apply_falls_back_to_session_bound_harness(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    first = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    snapshot = service.store.load()
    snapshot.active_harness_id = ""
    service.store.save(snapshot)
    second = service.handle_command(
        "/harness 修复 delayed apply 目标绑定",
        session_key="feishu:c2",
        sender_id="u2",
    )

    snapshot = service.store.load()
    snapshot.active_harness_id = second.active_harness_id
    service.store.save(snapshot)

    update = """```json
    {"harness": {"status": "completed", "phase": "completed", "summary": "done", "verification_status": "passed", "verification_summary": "focused tests passed", "git_delivery_status": "no_commit_required", "git_delivery_summary": "analysis-only"}}
    ```"""

    service.apply_agent_update(update, session_key="feishu:c1")

    refreshed = service.store.load()

    assert refreshed.records[first.active_harness_id].status == "completed"
    assert refreshed.records[second.active_harness_id].status == "active"
