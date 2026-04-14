import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanobot.bus.events import InboundMessage
from nanobot.command.router import CommandContext
from nanobot.command.workspace_bridge import cmd_workspace_bridge


def test_workspace_bridge_returns_fastlane_help_without_router_fallback(tmp_path: Path) -> None:
    route_decision = MagicMock(
        stdout='{"kind":"help_fastlane","target":"plan","content":"/plan help"}\n',
        stderr="",
        returncode=0,
    )
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/help plan",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/help plan",
        args="plan",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.fastlane.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.subprocess.run") as mock_bridge_run,
        patch("nanobot.command.fastlane.subprocess.run", return_value=route_decision),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is not None
    assert result.content == "/plan help"
    mock_bridge_run.assert_not_called()


def test_workspace_bridge_prepares_active_merge_workflow_continuation_for_non_slash_message(
    tmp_path: Path,
) -> None:
    harness_root = tmp_path / "harnesses"
    harness_root.mkdir(parents=True, exist_ok=True)
    (harness_root / "control.json").write_text('{"active_harness_id":"har_0002"}', encoding="utf-8")
    (harness_root / "index.json").write_text(
        '{"harnesses":{"har_0002":{"id":"har_0002","kind":"workflow","type":"workflow","status":"awaiting_decision","phase":"awaiting_decision","active":true,"awaiting_user":true,"blocked":false,"workflow_name":"merge","return_to":"har_0001"}}}',
        encoding="utf-8",
    )
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="可以，合并吧",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="可以，合并吧",
        args="",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch(
            "nanobot.command.workspace_bridge._prepare_agent_input",
            return_value="prepared merge continuation",
        ),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "merge"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared merge continuation"
    completed = MagicMock(stdout="Autopilot: idle\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/autopilot status",
            metadata={"message_id": "om_test"},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/autopilot status",
        args="status",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run", return_value=completed
        ) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is not None
    assert result.content == "Autopilot: idle"
    env = mock_run.call_args.kwargs["env"]
    assert env["NANOBOT_CHANNEL"] == "feishu"
    assert env["NANOBOT_CHAT_ID"] == "ou_test"
    assert env["NANOBOT_MESSAGE_ID"] == "om_test"


def test_workspace_bridge_marks_zh_diagnose_for_postprocess(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]诊断\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/诊断 API 调用失败",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/诊断 API 调用失败",
        args="API 调用失败",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.subprocess.run", return_value=completed),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "诊断"


def test_workspace_bridge_marks_plan_for_postprocess_and_plan_mode(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]plan\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/plan 规划新的任务流",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/plan 规划新的任务流",
        args="规划新的任务流",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.subprocess.run", return_value=completed),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "plan"
    assert ctx.msg.metadata["workspace_work_mode"] == "plan"


def test_workspace_bridge_marks_plan_exec_as_build_mode(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]plan\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/plan exec",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/plan exec",
        args="exec",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.subprocess.run", return_value=completed),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "plan-exec"
    assert ctx.msg.metadata["workspace_work_mode"] == "build"


def test_workspace_bridge_prepares_summary_agent_input(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]小结\n", stderr="", returncode=0)
    prepared = MagicMock(stdout="prepared summary input\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/小结 今天完成了 router 收口",
            metadata={"message_id": "om_test"},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/小结 今天完成了 router 收口",
        args="今天完成了 router 收口",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.try_workspace_fastlane", return_value=None),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=[completed, prepared],
        ) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "小结"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared summary input"
    assert mock_run.call_args_list[1].args[0][-2:] == ["--prepare-agent-input", "小结"]

    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/simplify router.py",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/simplify router.py",
        args="router.py",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.try_workspace_fastlane", return_value=None),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["router.py"], timeout=25),
        ),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is not None
    assert "workspace-router timeout" in result.content
    assert "25s" in result.content


def test_workspace_bridge_prepares_simplify_agent_input(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]simplify\n", stderr="", returncode=0)
    prepared = MagicMock(stdout="prepared simplify input\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/simplify scripts/router.py",
            metadata={"message_id": "om_test"},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/simplify scripts/router.py",
        args="scripts/router.py",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.try_workspace_fastlane", return_value=None),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=[completed, prepared],
        ) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "simplify"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared simplify input"
    assert mock_run.call_args_list[1].args[0][-2:] == ["--prepare-agent-input", "simplify"]

    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/simplify router.py",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/simplify router.py",
        args="router.py",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.try_workspace_fastlane", return_value=None),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["router.py"], timeout=25),
        ),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is not None
    assert "workspace-router timeout" in result.content
    assert "25s" in result.content


def test_workspace_bridge_prepares_notes_agent_input(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]笔记\n", stderr="", returncode=0)
    prepared = MagicMock(stdout="prepared notes input\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/笔记 记录一下 runtime follow-ups",
            metadata={"message_id": "om_test"},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/笔记 记录一下 runtime follow-ups",
        args="记录一下 runtime follow-ups",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.try_workspace_fastlane", return_value=None),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=[completed, prepared],
        ) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "笔记"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared notes input"
    assert mock_run.call_args_list[1].args[0][-2:] == ["--prepare-agent-input", "笔记"]


def test_workspace_bridge_prepares_merge_agent_input(tmp_path: Path) -> None:
    completed = MagicMock(stdout="[AGENT]merge\n", stderr="", returncode=0)
    prepared = MagicMock(stdout="prepared merge input\n", stderr="", returncode=0)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/merge",
            metadata={"message_id": "om_test"},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/merge",
        args="",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch("nanobot.command.workspace_bridge.try_workspace_fastlane", return_value=None),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=[completed, prepared],
        ) as mock_run,
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "merge"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared merge input"
    assert mock_run.call_args_list[1].args[0][-2:] == ["--prepare-agent-input", "merge"]


def test_workspace_bridge_returns_exception_message_instead_of_raising(tmp_path: Path) -> None:
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/体重 70.5",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/体重 70.5",
        args="70.5",
        loop=None,
    )

    with (
        patch("nanobot.command.workspace_bridge.WORKSPACE_ROUTER", tmp_path / "router.py"),
        patch(
            "nanobot.command.workspace_bridge.subprocess.run",
            side_effect=RuntimeError("router boom"),
        ),
    ):
        (tmp_path / "router.py").write_text("#!/bin/sh\n", encoding="utf-8")
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is not None
    assert "workspace-router error" in result.content
    assert "router boom" in result.content


def test_workspace_bridge_no_longer_marks_harness_agent_cmd_when_runtime_handler_exists() -> None:
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="/harness auto",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="/harness auto",
        args="auto",
        loop=None,
    )

    result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert "workspace_agent_cmd" not in ctx.msg.metadata


def test_workspace_bridge_non_slash_continuation_uses_loop_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "runtime-workspace"
    harness_root = workspace_root / "harnesses"
    harness_root.mkdir(parents=True, exist_ok=True)
    (harness_root / "control.json").write_text('{"active_harness_id":"har_0002"}', encoding="utf-8")
    (harness_root / "index.json").write_text(
        '{"harnesses":{"har_0002":{"id":"har_0002","kind":"workflow","type":"workflow","status":"awaiting_decision","phase":"awaiting_decision","active":true,"awaiting_user":true,"blocked":false,"workflow_name":"merge","return_to":"har_0001"}}}',
        encoding="utf-8",
    )
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="可以，合并吧",
            metadata={},
        ),
        session=None,
        key="feishu:ou_test",
        raw="可以，合并吧",
        args="",
        loop=MagicMock(workspace=workspace_root),
    )

    with patch(
        "nanobot.command.workspace_bridge._prepare_agent_input",
        return_value="prepared merge continuation",
    ):
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "merge"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared merge continuation"


def test_workspace_bridge_non_slash_continuation_prepares_input_with_loop_workspace_paths(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "runtime-workspace"
    harness_root = workspace_root / "harnesses"
    harness_root.mkdir(parents=True, exist_ok=True)
    (harness_root / "control.json").write_text('{"active_harness_id":"har_0002"}', encoding="utf-8")
    (harness_root / "index.json").write_text(
        '{"harnesses":{"har_0002":{"id":"har_0002","kind":"workflow","type":"workflow","status":"awaiting_decision","phase":"awaiting_decision","active":true,"awaiting_user":true,"blocked":false,"workflow_name":"merge","return_to":"har_0001"}}}',
        encoding="utf-8",
    )
    ctx = CommandContext(
        msg=InboundMessage(
            channel="feishu",
            sender_id="user1",
            chat_id="ou_test",
            content="可以，合并吧",
            metadata={"message_id": "om_test"},
        ),
        session=None,
        key="feishu:ou_test",
        raw="可以，合并吧",
        args="",
        loop=MagicMock(workspace=workspace_root),
    )
    prepared = MagicMock(stdout="prepared merge continuation\n", stderr="", returncode=0)

    with patch(
        "nanobot.command.workspace_bridge.subprocess.run",
        return_value=prepared,
    ) as mock_run:
        result = asyncio.run(cmd_workspace_bridge(ctx))

    assert result is None
    assert ctx.msg.metadata["workspace_agent_cmd"] == "merge"
    assert ctx.msg.metadata["workspace_agent_input"] == "prepared merge continuation"
    argv = mock_run.call_args.args[0]
    assert argv[0] == str(workspace_root / "venv" / "bin" / "python")
    assert argv[1] == str(workspace_root / "scripts" / "router.py")
    assert argv[-2:] == ["--prepare-agent-input", "merge"]
