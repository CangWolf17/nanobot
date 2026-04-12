from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.agent.skills import SkillsLoader


def _write_active_dev_state(workspace: Path, state: str) -> None:
    session_root = workspace / "sessions" / "ses_0001"
    session_root.mkdir(parents=True)
    (workspace / "sessions" / "control.json").write_text(
        '{"active_session_id":"ses_0001"}', encoding="utf-8"
    )
    (workspace / "sessions" / "index.json").write_text(
        '{"sessions":{"ses_0001":{"session_root":"' + str(session_root) + '"}}}',
        encoding="utf-8",
    )
    (session_root / "dev_state.json").write_text(state, encoding="utf-8")


def test_context_prompt_includes_runtime_protocol_and_skill_hint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _write_active_dev_state(
        workspace,
        '{"strict_dev_mode":"enforce","task_kind":"feature","phase":"red_required","work_mode":"build","current_step":"Task 3","gates":{"plan":{"required":true,"satisfied":true},"debug_root_cause":{"required":false,"satisfied":false},"failing_test":{"required":true,"satisfied":false},"verification":{"required":true,"satisfied":false}}}',
    )

    prompt = ContextBuilder(workspace).build_system_prompt()

    assert "## Runtime Protocol" in prompt
    assert "version: 1" in prompt
    assert "phase: red_required" in prompt
    assert "current_step: Task 3" in prompt
    assert "required_skills: test-driven-development" in prompt


def test_skills_loader_maps_protocol_phase_to_skill_hints(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    loader = SkillsLoader(workspace)

    hints = loader.get_protocol_skill_hints({"phase": "verify_required"})

    assert "verification-before-completion" in hints
