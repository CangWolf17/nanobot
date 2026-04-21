"""Tool for managing workflow state (activate/deactivate/query active workflows)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.utils.helpers import safe_filename

WORKSPACE_ROOT = Path.home() / ".nanobot" / "workspace"


class WorkflowStateTool(Tool):
    """Manage active workflow rules injection state per session."""

    @property
    def name(self) -> str:
        return "manage_workflow_state"

    @property
    def description(self) -> str:
        return "Manage active workflow rules injection state per session. Use activate/deactivate/query to control workflow rule injection."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["activate", "deactivate", "query"],
                    "description": "activate=activate a workflow; deactivate=close it; query=list active workflows",
                },
                "workflow_name": {
                    "type": "string",
                    "description": "Workflow name, e.g. 'diary'",
                },
                "skill_ref": {
                    "type": "string",
                    "description": "Path to rules file relative to workspace, e.g. 'skills/diary/rules.md'. Required for activate.",
                },
            },
            "required": ["action"],
        }

    def set_context(
        self,
        channel: str | None,
        chat_id: str | None,
        message_id: Any = None,
        metadata: Any = None,
        sender_id: str = "",
    ) -> None:
        """Store session context. session_key = f'{channel}:{chat_id}'."""
        self._channel = channel or "unknown"
        self._chat_id = chat_id or "unknown"

    @property
    def _session_key(self) -> str:
        """Use safe_filename to match nanobot's session storage path convention."""
        return safe_filename(f"{self._channel}:{self._chat_id}")

    async def execute(
        self,
        action: str,
        workflow_name: str | None = None,
        skill_ref: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Import here to avoid circular/optional dependency at module load time
        sys.path.insert(0, str(WORKSPACE_ROOT / "scripts"))
        try:
            from workflow_hooks import (
                activate_workflow,
                build_workflow_contexts,
                deactivate_workflow,
                query_workflows,
            )
        except Exception as e:
            return f"Error importing workflow_hooks: {e}"

        sk = self._session_key

        if action == "query":
            active = query_workflows(sk)
            if not active:
                return "No active workflows."
            lines = [f"Active workflows (session={sk}):"]
            for w in active:
                lines.append(f"  - {w.get('skill_name')}: {w.get('skill_ref')} (activated: {w.get('activated_at')})")
            return "\n".join(lines)

        if action == "activate":
            if not workflow_name:
                return "Error: workflow_name is required for activate."
            if not skill_ref:
                return "Error: skill_ref is required for activate."
            # Verify skill_ref file exists
            ref_path = WORKSPACE_ROOT / skill_ref
            if not ref_path.exists():
                return f"Error: skill_ref file not found: {ref_path}"
            activate_workflow(sk, workflow_name, skill_ref)
            # Verify it worked
            active = query_workflows(sk)
            found = any(w.get("skill_name") == workflow_name and w.get("active") for w in active)
            if found:
                return f"Workflow '{workflow_name}' activated. Rules from: {skill_ref}"
            return f"Error: workflow '{workflow_name}' activation failed."

        if action == "deactivate":
            if not workflow_name:
                return "Error: workflow_name is required for deactivate."
            deactivate_workflow(sk, workflow_name)
            return f"Workflow '{workflow_name}' deactivated."

        return f"Unknown action: {action}. Use activate, deactivate, or query."
