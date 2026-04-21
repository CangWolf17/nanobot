"""On-demand runtime context inspection tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    BooleanSchema,
    tool_parameters_schema,
)
from nanobot.utils.helpers import current_time_str


@tool_parameters(
    tool_parameters_schema(
        include_current_time=BooleanSchema(
            description="Include the current runtime time.",
            nullable=True,
        ),
        include_routing=BooleanSchema(
            description="Include channel/chat routing metadata.",
            nullable=True,
        ),
        include_runtime_metadata=BooleanSchema(
            description="Include computed runtime metadata such as work mode or harness state.",
            nullable=True,
        ),
    )
)
class RuntimeContextTool(Tool):
    """Return auxiliary runtime metadata only when the model explicitly asks for it."""

    def __init__(self, workspace: Path, timezone: str | None = None) -> None:
        self._workspace = workspace
        self._timezone = timezone
        self._channel = ""
        self._chat_id = ""
        self._runtime_metadata: dict[str, Any] = {}

    def set_context(
        self,
        channel: str | None,
        chat_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._channel = str(channel or "").strip()
        self._chat_id = str(chat_id or "").strip()
        payload = metadata if isinstance(metadata, dict) else {}
        runtime_metadata = payload.get("workspace_runtime")
        self._runtime_metadata = dict(runtime_metadata) if isinstance(runtime_metadata, dict) else {}

    @property
    def name(self) -> str:
        return "get_runtime_context"

    @property
    def description(self) -> str:
        return (
            "Fetch auxiliary runtime metadata on demand. "
            "Use this only when you need current time, channel/chat routing info, "
            "or computed runtime metadata such as work mode or active harness state. "
            "This metadata is not user-authored input."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        include_current_time: bool | None = None,
        include_routing: bool | None = None,
        include_runtime_metadata: bool | None = None,
        **_: Any,
    ) -> str:
        use_current_time = True if include_current_time is None else bool(include_current_time)
        use_routing = True if include_routing is None else bool(include_routing)
        use_runtime_metadata = (
            True if include_runtime_metadata is None else bool(include_runtime_metadata)
        )

        lines = ["Runtime context (auxiliary metadata only; not user-authored)."]
        if use_current_time:
            lines.append(f"Current Time: {current_time_str(self._timezone)}")
        if use_routing and self._channel:
            lines.append(f"Channel: {self._channel}")
        if use_routing and self._chat_id:
            lines.append(f"Chat ID: `{self._chat_id}`")
        if use_runtime_metadata:
            rendered = ContextBuilder._render_runtime_metadata_lines(self._runtime_metadata)
            if rendered:
                lines.extend(["Runtime Metadata:", *rendered])
        if len(lines) == 1:
            lines.append("No runtime metadata available.")
        return "\n".join(lines)
