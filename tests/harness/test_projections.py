import json
from pathlib import Path

import pytest

from nanobot.harness.cli import migrate_and_sync
from nanobot.harness.service import HarnessService


def _write_legacy_workspace_files(root: Path) -> None:
    harness_dir = root / "harnesses" / "har_0001"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (root / "harnesses" / "index.json").write_text(
        json.dumps(
            {
                "harnesses": {
                    "har_0001": {
                        "id": "har_0001",
                        "kind": "work",
                        "type": "feature",
                        "title": "Legacy harness",
                        "status": "active",
                        "phase": "planning",
                        "summary": "legacy summary",
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
                "next_step": "continue runtime migration",
                "resume_hint": "run projection sync",
                "verification_status": "not_run",
                "verification_summary": "",
                "git_delivery_status": "not_set",
                "git_delivery_summary": "",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_projection_sync_regenerates_root_task_and_per_harness_markdown_from_store(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )

    service.sync_projections()

    root_task = tmp_path / "TASK.md"

    assert root_task.exists()
    assert "Active Harness Task" in root_task.read_text(encoding="utf-8")
    assert any(path.name == "HARNESS.md" for path in (tmp_path / "harnesses").rglob("HARNESS.md"))
    assert any(path.name == "TASK.md" for path in (tmp_path / "harnesses").rglob("TASK.md"))
    assert any(path.name == "HANDOFF.md" for path in (tmp_path / "harnesses").rglob("HANDOFF.md"))


def test_projection_sync_ignores_manual_markdown_drift_and_rewrites_from_store(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)

    service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    service.sync_projections()

    task_path = tmp_path / "TASK.md"
    task_path.write_text("stale", encoding="utf-8")

    service.sync_projections()

    assert "Active Harness Task" in task_path.read_text(encoding="utf-8")


def test_projection_sync_removes_legacy_runtime_json_after_migration(tmp_path: Path) -> None:
    _write_legacy_workspace_files(tmp_path)
    service = HarnessService.for_workspace(tmp_path)

    service.sync_projections()

    assert (tmp_path / "harnesses" / "store.json").exists()
    assert not (tmp_path / "harnesses" / "index.json").exists()
    assert not (tmp_path / "harnesses" / "control.json").exists()
    assert not (tmp_path / "harnesses" / "har_0001" / "state.json").exists()


def test_projection_sync_removes_stale_projection_markdown_for_deleted_harness_dirs(
    tmp_path: Path,
) -> None:
    service = HarnessService.for_workspace(tmp_path)
    result = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )
    service.sync_projections()

    stale_dir = tmp_path / "harnesses" / "har_stale"
    stale_dir.mkdir(parents=True, exist_ok=True)
    for name in ("HARNESS.md", "TASK.md", "HANDOFF.md"):
        (stale_dir / name).write_text("stale", encoding="utf-8")
    (stale_dir / "notes.txt").write_text("keep me", encoding="utf-8")

    snapshot = service.store.load()
    del snapshot.records[result.active_harness_id]
    snapshot.active_harness_id = ""
    service.store.save(snapshot)

    service.sync_projections()

    assert not (stale_dir / "HARNESS.md").exists()
    assert not (stale_dir / "TASK.md").exists()
    assert not (stale_dir / "HANDOFF.md").exists()
    assert (stale_dir / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_migrate_and_sync_rejects_missing_workspace_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises(NotADirectoryError):
        migrate_and_sync(missing)


def test_migrate_and_sync_rejects_existing_directory_without_workspace_markers(
    tmp_path: Path,
) -> None:
    not_a_workspace = tmp_path / "random-dir"
    not_a_workspace.mkdir()

    with pytest.raises(NotADirectoryError):
        migrate_and_sync(not_a_workspace)


def test_projection_sync_marks_projection_files_as_projection_only(tmp_path: Path) -> None:
    service = HarnessService.for_workspace(tmp_path)

    result = service.handle_command(
        "/harness 修复 interrupt 的真实接线",
        session_key="feishu:c1",
        sender_id="u1",
    )

    service.sync_projections()

    root_task = (tmp_path / "TASK.md").read_text(encoding="utf-8").lower()
    harness_task = (
        tmp_path / "harnesses" / result.active_harness_id / "TASK.md"
    ).read_text(encoding="utf-8").lower()

    assert "projection-only" in root_task
    assert "projection-only" in harness_task
