"""Spawn tool for creating background subagents."""

from typing import TYPE_CHECKING, Any

from nanobot.agent.subagent_types import list_builtin_subagent_types
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
    _NESTED_RUNTIME_META_KEYS = {
        "subagent_runtime",
    }

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"
        self._metadata: dict[str, Any] = {}

    def set_context(
        self, channel: str, chat_id: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"
        filtered: dict[str, Any] = {}
        if isinstance(metadata, dict):
            for key in self._PASSTHROUGH_META_KEYS:
                if key in metadata:
                    filtered[key] = metadata[key]
            if isinstance(metadata.get("workspace_runtime"), dict):
                filtered["workspace_runtime"] = metadata["workspace_runtime"]
            for key in self._NESTED_RUNTIME_META_KEYS:
                if isinstance(metadata.get(key), dict):
                    filtered[key] = metadata[key]
        self._metadata = filtered

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a background subagent for a bounded task. "
            "Preferred call shapes: (task + type) or (task + model). "
            "Use type=worker for implementation/execution and type=explorer for reconnaissance/search. "
            "Do not omit both type and model. "
            "label/tier are deprecated compatibility inputs only. "
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
                "name": {
                    "type": "string",
                    "description": "Optional short identifier for the subagent. Prefer `name` over deprecated `label`.",
                },
                "type": {
                    "type": "string",
                    "enum": list(list_builtin_subagent_types()),
                    "description": "Built-in runtime subagent type. Prefer this for default behavior: worker=execution/implementation, explorer=exploration/recon.",
                },
                "label": {
                    "type": "string",
                    "description": "Deprecated compatibility alias of `name`. Avoid in new calls.",
                },
                "tier": {
                    "type": "string",
                    "enum": ["lite", "standard"],
                    "description": "Deprecated compatibility hint only. Use `type` or `model` for new calls.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional explicit registry model ref. Overrides built-in type/default routing when provided.",
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str,
        name: str | None = None,
        type: str | None = None,
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
            name=name,
            subagent_type=type,
            label=label,
            tier=tier,
            model=model,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
            origin_metadata=dict(self._metadata),
        )
