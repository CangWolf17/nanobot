import json
from pathlib import Path
from unittest.mock import patch

from nanobot.bus.events import InboundMessage
from nanobot.command.workspace_bridge import prepare_active_workflow_continuation


def test_prepare_active_workflow_continuation_for_merge_awaiting_decision_sets_agent_metadata(tmp_path: Path) -> None:
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


def test_prepare_active_workflow_continuation_ignores_slash_commands_and_non_workflow_messages(tmp_path: Path) -> None:
    harness_root = tmp_path / "harnesses"
    harness_root.mkdir(parents=True, exist_ok=True)
    (harness_root / "control.json").write_text(
        json.dumps({"active_harness_id": "har_0001"}), encoding="utf-8"
    )
    (harness_root / "index.json").write_text(
        json.dumps(
            {
                "harnesses": {
                    "har_0001": {
                        "id": "har_0001",
                        "kind": "workflow",
                        "type": "workflow",
                        "status": "awaiting_decision",
                        "phase": "awaiting_decision",
                        "active": True,
                        "awaiting_user": True,
                        "blocked": False,
                        "workflow_name": "merge",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="/status",
        metadata={},
    )

    applied = prepare_active_workflow_continuation(msg, workspace_root=tmp_path)

    assert applied is False
    assert msg.metadata == {}
