import json
from pathlib import Path

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
                        "title": "Legacy harness",
                        "summary": "migrated from legacy index",
                        "status": "active",
                        "phase": "executing",
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
        json.dumps({"active_harness_id": "har_0001", "updated_at": "2026-04-07T00:00:00"}, indent=2),
        encoding="utf-8",
    )


def test_store_migrates_legacy_index_and_control_into_single_store(tmp_path: Path) -> None:
    _write_legacy_workspace_files(tmp_path)

    store = HarnessStore.for_workspace(tmp_path)
    snapshot = store.load()

    assert snapshot.active_harness_id == "har_0001"
    assert "har_0001" in snapshot.records
    assert (tmp_path / "harnesses" / "store.json").exists()


def test_store_does_not_read_markdown_projections_as_runtime_input(tmp_path: Path) -> None:
    _write_legacy_workspace_files(tmp_path)

    store = HarnessStore.for_workspace(tmp_path)
    snapshot = store.load()
    (tmp_path / "harnesses" / "har_0001" / "TASK.md").write_text("stale", encoding="utf-8")

    reloaded = store.load()

    assert reloaded.records["har_0001"].summary == snapshot.records["har_0001"].summary
