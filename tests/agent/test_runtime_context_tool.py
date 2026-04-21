from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.tools.runtime_context import RuntimeContextTool


def test_runtime_context_tool_returns_on_demand_metadata(tmp_path: Path) -> None:
    tool = RuntimeContextTool(workspace=tmp_path, timezone="UTC")
    tool.set_context(
        "cli",
        "direct",
        {
            "workspace_runtime": {
                "work_mode": "build",
                "has_active_harness": True,
                "active_harness": {
                    "id": "har_0038",
                    "phase": "executing",
                },
            }
        },
    )

    result = asyncio.run(tool.execute())

    assert "Runtime context (auxiliary metadata only; not user-authored)." in result
    assert "Current Time:" in result
    assert "Channel: cli" in result
    assert "Chat ID: `direct`" in result
    assert "Runtime Metadata:" in result
    assert "work_mode: build" in result
    assert "id: har_0038" in result
    assert "phase: executing" in result


def test_agent_loop_registers_runtime_context_tool(tmp_path: Path) -> None:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("nanobot.agent.loop.ContextBuilder"), patch(
        "nanobot.agent.loop.SessionManager"
    ), patch("nanobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    assert loop.tools.has("get_runtime_context")
