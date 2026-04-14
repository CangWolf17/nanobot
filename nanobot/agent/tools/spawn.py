"""Spawn tool for creating background subagents."""

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    _PASSTHROUGH_META_KEYS = {
        "workspace_agent_cmd",
        "workspace_harness_id",
        "workspace_harness_auto",
        "workspace_work_mode",
        "_origin_sender_id",
        "_completion_notice_mention_user_id",
    }

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"
        self._metadata: dict[str, Any] = {}

    def set_context(
        self,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = session_key or f"{channel}:{chat_id}"
        filtered: dict[str, Any] = {}
        if isinstance(metadata, dict):
            for key in self._PASSTHROUGH_META_KEYS:
                if key in metadata:
                    filtered[key] = metadata[key]
            if isinstance(metadata.get("workspace_runtime"), dict):
                filtered["workspace_runtime"] = metadata["workspace_runtime"]
        self._metadata = filtered

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
                "tier": {
                    "type": "string",
                    "enum": ["lite", "standard"],
                    "description": "Optional subagent tier. lite=read/summarize style tasks; standard=full independent subtask.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional explicit model ref. Overrides tier/default routing when provided.",
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str,
        label: str | None = None,
        tier: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        runtime_meta = self._metadata.get("workspace_runtime")
        if self._metadata.get("workspace_agent_cmd") == "harness" and isinstance(
            runtime_meta, dict
        ):
            active_harness = runtime_meta.get("active_harness")
            if isinstance(active_harness, dict) and not bool(
                active_harness.get("subagent_allowed", False)
            ):
                return (
                    "Error: spawn blocked by harness policy "
                    "(subagent_allowed=false for active harness)."
                )
        return await self._manager.spawn(
            task=task,
            label=label,
            tier=tier,
            model=model,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
            origin_metadata=dict(self._metadata),
        )
