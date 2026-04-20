from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.guarded import GuardedTool


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def execute(self, **kwargs):
        return kwargs["value"]


@pytest.mark.asyncio
async def test_guarded_tool_blocks_when_checker_returns_reason() -> None:
    tool = GuardedTool(_EchoTool(), lambda params: "blocked for test")

    result = await tool.execute(value="x")

    assert result == "Error: blocked for test"


@pytest.mark.asyncio
async def test_guarded_tool_delegates_when_checker_allows() -> None:
    inner = _EchoTool()
    inner.execute = AsyncMock(return_value="ok")  # type: ignore[method-assign]
    tool = GuardedTool(inner, lambda params: None)

    result = await tool.execute(value="x")

    assert result == "ok"
    inner.execute.assert_awaited_once_with(value="x")
