from __future__ import annotations

from pathlib import Path

from nanobot.harness.models import HarnessRecord, HarnessSnapshot


def sync_workspace_projections(workspace_root: Path, snapshot: HarnessSnapshot) -> None:
    harnesses_dir = workspace_root / "harnesses"
    harnesses_dir.mkdir(parents=True, exist_ok=True)
    record_ids = set(snapshot.records)

    active = snapshot.records.get(snapshot.active_harness_id)
    (workspace_root / "TASK.md").write_text(
        _render_root_task(workspace_root, active),
        encoding="utf-8",
    )

    for record in snapshot.records.values():
        harness_dir = harnesses_dir / record.id
        harness_dir.mkdir(parents=True, exist_ok=True)
        (harness_dir / "HARNESS.md").write_text(_render_harness(record), encoding="utf-8")
        (harness_dir / "TASK.md").write_text(
            _render_harness_task(workspace_root, record, active_id=snapshot.active_harness_id),
            encoding="utf-8",
        )
        (harness_dir / "HANDOFF.md").write_text(_render_handoff(record), encoding="utf-8")

    _cleanup_stale_projection_markdown(harnesses_dir, record_ids)
    _cleanup_legacy_runtime_json(harnesses_dir)


def _render_root_task(workspace_root: Path, active: HarnessRecord | None) -> str:
    lines = ["# Active Harness Task", ""]
    if active is None:
        lines.append("No active harness.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            f"- id: {active.id}",
            f"- title: {active.title}",
            f"- path: {workspace_root / 'harnesses' / active.id}",
            f"- type: {active.type}",
            f"- status: {active.status}",
            f"- phase: {active.phase}",
            f"- executor_mode: {active.execution_policy.executor_mode}",
            f"- subagent_allowed: {str(active.execution_policy.subagent_allowed).lower()}",
            f"- runner: {active.runtime_state.runner}",
            f"- auto: {str(active.execution_policy.auto_continue).lower()}",
            f"- awaiting_user: {str(active.awaiting_user).lower()}",
            f"- summary: {active.summary}",
            f"- next_step: {active.next_step}",
            f"- resume_hint: {active.resume_hint}",
            f"- verification_status: {active.verification.get('status', '')}",
            f"- verification_summary: {active.verification.get('summary', '')}",
            f"- git_delivery_status: {active.git_delivery.get('status', '')}",
            f"- git_delivery_summary: {active.git_delivery.get('summary', '')}",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_harness(record: HarnessRecord) -> str:
    lines = [
        f"# Harness {record.id}",
        "",
        "## Meta",
        f"- title: {record.title}",
        f"- kind: {record.kind}",
        f"- type: {record.type}",
        f"- status: {record.status}",
        f"- phase: {record.phase}",
        f"- parent_id: {record.parent_id}",
        f"- updated_at: {record.updated_at}",
        "",
        "## Current Outcome",
        record.summary or "-",
        "",
        "## Pending Decisions",
    ]
    if record.pending_decisions:
        lines.extend(f"- {item}" for item in record.pending_decisions)
    else:
        lines.append("- none")
    lines.extend(["", "## Resume Hint", record.resume_hint or "-"])
    return "\n".join(lines) + "\n"


def _render_harness_task(workspace_root: Path, record: HarnessRecord, *, active_id: str) -> str:
    lines = [
        f"# Harness Task {record.id}",
        "",
        f"- id: {record.id}",
        f"- path: {workspace_root / 'harnesses' / record.id}",
        f"- title: {record.title}",
        f"- status: {record.status}",
        f"- phase: {record.phase}",
        f"- active: {str(record.id == active_id).lower()}",
        f"- summary: {record.summary}",
        f"- next_step: {record.next_step}",
        f"- resume_hint: {record.resume_hint}",
        "",
        "## Verification",
        f"- status: {record.verification.get('status', '')}",
        f"- summary: {record.verification.get('summary', '')}",
        "",
        "## Git Delivery",
        f"- status: {record.git_delivery.get('status', '')}",
        f"- summary: {record.git_delivery.get('summary', '')}",
    ]
    return "\n".join(lines) + "\n"


def _render_handoff(record: HarnessRecord) -> str:
    lines = [
        f"# Handoff {record.id}",
        "",
        "## Goal",
        record.title or "-",
        "",
        "## Current Status",
        f"- status: {record.status}",
        f"- phase: {record.phase}",
        f"- summary: {record.summary}",
        "",
        "## Pending Decisions",
    ]
    if record.pending_decisions:
        lines.extend(f"- {item}" for item in record.pending_decisions)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Next Step",
            record.next_step or "-",
            "",
            "## Read These First",
            "- HARNESS.md",
            "- TASK.md",
        ]
    )
    return "\n".join(lines) + "\n"


def _cleanup_legacy_runtime_json(harnesses_dir: Path) -> None:
    for path in (harnesses_dir / "index.json", harnesses_dir / "control.json"):
        if path.exists():
            path.unlink()
    for state_path in harnesses_dir.glob("*/state.json"):
        if state_path.exists():
            state_path.unlink()


def _cleanup_stale_projection_markdown(harnesses_dir: Path, record_ids: set[str]) -> None:
    for harness_dir in harnesses_dir.iterdir():
        if not harness_dir.is_dir() or harness_dir.name in record_ids:
            continue
        for name in ("HARNESS.md", "TASK.md", "HANDOFF.md"):
            path = harness_dir / name
            if path.exists():
                path.unlink()
