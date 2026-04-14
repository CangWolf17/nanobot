import json
from pathlib import Path
from unittest.mock import patch

from nanobot.bus.events import InboundMessage
from nanobot.command.workspace_bridge import prepare_active_workflow_continuation


def test_prepare_active_workflow_continuation_for_merge_awaiting_decision_sets_agent_metadata(
    tmp_path: Path,
) -> None:
    harness_root = tmp_path / "harnesses"
    harness_root.mkdir(parents=True, exist_ok=True)
    (harness_root / "control.json").write_text(
        json.dumps({"active_harness_id": "har_0002"}), encoding="utf-8"
    )
    (harness_root / "index.json").write_text(
        json.dumps(
            {
                "harnesses": {
                    "har_0001": {
                        "id": "har_0001",
                        "kind": "work",
                        "type": "feature",
                        "status": "active",
                        "phase": "executing",
                        "active": False,
                    },
                    "har_0002": {
                        "id": "har_0002",
                        "kind": "workflow",
                        "type": "workflow",
                        "status": "awaiting_decision",
                        "phase": "awaiting_decision",
                        "active": True,
                        "awaiting_user": True,
                        "blocked": False,
                        "workflow_name": "merge",
                        "return_to": "har_0001",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="可以，合并吧",
        metadata={},
    )

    with patch(
        "nanobot.command.workspace_bridge._prepare_agent_input",
        return_value="prepared merge continuation",
    ):
        applied = prepare_active_workflow_continuation(msg, workspace_root=tmp_path)

    assert applied is True
    assert msg.metadata["workspace_agent_cmd"] == "merge"
    assert msg.metadata["workspace_agent_input"] == "prepared merge continuation"


def test_prepare_active_workflow_continuation_for_notes_awaiting_confirmation_sets_agent_metadata(
    tmp_path: Path,
) -> None:
    sessions_root = tmp_path / "sessions"
    session_root = sessions_root / "ses_0001"
    session_root.mkdir(parents=True, exist_ok=True)
    (sessions_root / "control.json").write_text(
        json.dumps({"active_session_id": "ses_0001"}), encoding="utf-8"
    )
    (sessions_root / "index.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "ses_0001": {
                        "id": "ses_0001",
                        "session_root": str(session_root),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (session_root / "notes_state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mode": "notes",
                "phase": "awaiting_confirmation",
                "last_action": "draft",
            }
        ),
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="可以，写入吧",
        metadata={},
    )

    with patch(
        "nanobot.command.workspace_bridge._prepare_agent_input",
        return_value="prepared notes continuation",
    ):
        applied = prepare_active_workflow_continuation(msg, workspace_root=tmp_path)

    assert applied is True
    assert msg.metadata["workspace_agent_cmd"] == "笔记"
    assert msg.metadata["workspace_agent_input"] == "prepared notes continuation"


def test_prepare_active_workflow_continuation_prefers_canonical_store_json(
    tmp_path: Path,
) -> None:
    harness_root = tmp_path / "harnesses"
    harness_root.mkdir(parents=True, exist_ok=True)
    (harness_root / "store.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-04-09T12:00:00",
                "active_harness_id": "har_merge",
                "records": {
                    "har_parent": {
                        "id": "har_parent",
                        "kind": "work",
                        "type": "feature",
                        "title": "Parent work item",
                        "status": "active",
                        "phase": "executing",
                    },
                    "har_merge": {
                        "id": "har_merge",
                        "kind": "workflow",
                        "type": "workflow",
                        "title": "Merge workflow",
                        "parent_id": "har_parent",
                        "status": "awaiting_decision",
                        "phase": "awaiting_decision",
                        "awaiting_user": True,
                        "blocked": False,
                        "workflow": {"name": "merge"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="可以，合并吧",
        metadata={},
    )

    with patch(
        "nanobot.command.workspace_bridge._prepare_agent_input",
        return_value="prepared merge continuation",
    ):
        applied = prepare_active_workflow_continuation(msg, workspace_root=tmp_path)

    assert applied is True
    assert msg.metadata["workspace_agent_cmd"] == "merge"
    assert msg.metadata["workspace_agent_input"] == "prepared merge continuation"


def test_prepare_active_workflow_continuation_ignores_notes_state_when_phase_not_waiting(
    tmp_path: Path,
) -> None:
    sessions_root = tmp_path / "sessions"
    session_root = sessions_root / "ses_0001"
    session_root.mkdir(parents=True, exist_ok=True)
    (sessions_root / "control.json").write_text(
        json.dumps({"active_session_id": "ses_0001"}), encoding="utf-8"
    )
    (sessions_root / "index.json").write_text(
        json.dumps(
            {
                "sessions": {
                    "ses_0001": {
                        "id": "ses_0001",
                        "session_root": str(session_root),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (session_root / "notes_state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mode": "notes",
                "phase": "completed",
                "last_action": "confirm",
            }
        ),
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="再补一段",
        metadata={},
    )

    applied = prepare_active_workflow_continuation(msg, workspace_root=tmp_path)

    assert applied is False
    assert msg.metadata == {}
