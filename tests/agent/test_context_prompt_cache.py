"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

import datetime as datetime_module
from datetime import datetime as real_datetime
from importlib.resources import files as pkg_files
from pathlib import Path

from nanobot.agent.context import ContextBuilder


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_bootstrap_files_are_backed_by_templates() -> None:
    template_dir = pkg_files("nanobot") / "templates"

    for filename in ContextBuilder.BOOTSTRAP_FILES:
        assert (template_dir / filename).is_file(), f"missing bootstrap template: {filename}"


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_system_prompt_includes_dynamic_work_mode_block_when_requested(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(workspace_work_mode="plan")

    assert "## Work Mode" in prompt
    assert "Current workspace work mode: plan" in prompt
    assert "Do not make code or implementation changes" in prompt


def test_system_prompt_references_history_jsonl(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "memory/history.jsonl" in prompt
    assert "memory/HISTORY.md" not in prompt


def test_runtime_context_is_not_auto_injected_into_user_message(tmp_path) -> None:
    """Runtime metadata should no longer be prepended to the user turn by default."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert user_content == "Return exactly: OK"
    assert ContextBuilder._RUNTIME_CONTEXT_TAG not in user_content
    assert "Current Time:" not in user_content
    assert "Channel: cli" not in user_content
    assert "Chat ID: `direct`" not in user_content


def test_system_prompt_mentions_runtime_context_tool(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "get_runtime_context" in prompt
    assert "Runtime metadata is not auto-injected" in prompt


def test_harness_and_work_mode_stay_in_system_prompt_not_user_message(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="继续",
        channel="cli",
        chat_id="direct",
        workspace_work_mode="build",
        runtime_metadata={
            "has_active_harness": True,
            "active_harness": {
                "id": "har_0038",
                "type": "feature",
                "status": "active",
                "phase": "executing",
                "awaiting_user": False,
                "blocked": False,
                "auto": True,
                "subagent_allowed": True,
            },
        },
    )

    system_prompt = messages[0]["content"]
    user_content = messages[-1]["content"]

    assert "## Work Mode" in system_prompt
    assert "Current workspace work mode: build" in system_prompt
    assert "## Harness State" in system_prompt
    assert "id: har_0038" in system_prompt
    assert "phase: executing" in system_prompt
    assert "subagent_allowed: true" in system_prompt

    assert isinstance(user_content, str)
    assert user_content == "继续"
    assert "has_active_harness: true" not in user_content
    assert "active_harness:" not in user_content
    assert "id: har_0038" not in user_content
    assert "phase: executing" not in user_content
    assert "auto: true" not in user_content


def test_semantic_routing_hint_stays_in_system_prompt_not_user_message(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="please diagnose this failure",
        runtime_metadata={
            "semantic_routing": {
                "mode": "direct_route",
                "matches": [
                    {
                        "skill": "self-improving-lite",
                        "path": "skills/self-improving-lite/SKILL.md",
                        "description": "Structured diagnosis and self-reflection workflow.",
                        "matched_terms": ["diagnose"],
                    }
                ],
            }
        },
    )

    system_prompt = messages[0]["content"]
    user_content = messages[-1]["content"]

    assert "## Semantic Routing Hint" in system_prompt
    assert "self-improving-lite" in system_prompt
    assert "skills/self-improving-lite/SKILL.md" in system_prompt
    assert "matched `diagnose`" in system_prompt
    assert isinstance(user_content, str)
    assert user_content == "please diagnose this failure"
    assert "Semantic Routing Hint" not in user_content


def test_runtime_context_can_include_auxiliary_retrieval_block(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="继续推进",
        retrieval_context="Project memory says Phase 2 owns the retrieval seam.",
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert ContextBuilder._RETRIEVAL_CONTEXT_TAG in user_content
    assert "Auxiliary background only. Not part of the user's request." in user_content
    assert "Project memory says Phase 2 owns the retrieval seam." in user_content
    assert user_content.rstrip().endswith("继续推进")


def test_runtime_context_echo_strip_handles_runtime_metadata_block() -> None:
    from nanobot.agent.loop import AgentLoop

    text = """[Runtime Context — metadata only, not instructions]
Rules:
- Metadata only. Not part of the user's request.
- Use `Current Time` only for time-sensitive reasoning.
- Treat `Channel` and `Chat ID` as opaque routing metadata. Use them only for reply delivery, tool targeting, or channel-specific formatting when explicitly relevant.
- Never use this block to infer user intent or resolve references like \"this\", \"that\", \"above\", or \"these two\".
- If this block conflicts with the conversation content, trust the conversation content.

Current Time: 2026-04-05 11:37 (Sunday) (UTC, UTC+00:00)
Channel: feishu
Chat ID: `ou_test`
Runtime Metadata:
work_mode: build
has_active_harness: true
active_harness:
  id: har_0040
  type: project
  status: active
  phase: executing
  awaiting_user: false
  blocked: false
  auto: true

真正给用户的话"""

    assert AgentLoop._sanitize_visible_output(text) == "真正给用户的话"



def test_system_prompt_includes_dev_discipline_block_when_active_session_exists(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    session_root = workspace / "sessions" / "ses_0001"
    session_root.mkdir(parents=True)
    (workspace / "sessions" / "control.json").write_text(
        '{"active_session_id":"ses_0001"}', encoding="utf-8"
    )
    (workspace / "sessions" / "index.json").write_text(
        '{"sessions":{"ses_0001":{"session_root":"' + str(session_root) + '"}}}',
        encoding="utf-8",
    )
    (session_root / "dev_state.json").write_text(
        '{"strict_dev_mode":"enforce","task_kind":"feature","phase":"red_required","work_mode":"build","gates":{"plan":{"required":true,"satisfied":true},"debug_root_cause":{"required":false,"satisfied":false},"failing_test":{"required":true,"satisfied":false},"verification":{"required":true,"satisfied":false}}}',
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "## Dev Discipline" in prompt
    assert "task_kind: feature" in prompt
    assert "phase: red_required" in prompt
    assert "- failing_test: pending" in prompt
