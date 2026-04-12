from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from nanobot.agent.tools.base import Tool


class GuardedTool(Tool):
    def __init__(
        self,
        inner: Tool,
        checker: Callable[[dict[str, Any]], str | None | Awaitable[str | None]],
    ) -> None:
        self._inner = inner
        self._checker = checker

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def description(self) -> str:
        return self._inner.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._inner.parameters

    async def execute(self, **kwargs: Any) -> Any:
        blocked = self._checker(dict(kwargs))
        if inspect.isawaitable(blocked):
            blocked = await blocked
        if blocked:
            return f"Error: {blocked}"
        return await self._inner.execute(**kwargs)
