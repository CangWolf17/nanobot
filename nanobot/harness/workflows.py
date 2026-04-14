from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    stable_harness_id: str
    title: str
    type: str = "workflow"


WORKFLOW_DEFINITIONS: dict[str, WorkflowDefinition] = {
    "cleanup": WorkflowDefinition(
        name="cleanup",
        stable_harness_id="har_cleanup",
        title="Cleanup workflow",
    )
}


def get_workflow_definition(name: str) -> WorkflowDefinition:
    normalized = (name or "").strip().lower()
    try:
        return WORKFLOW_DEFINITIONS[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown harness workflow: {name}") from exc
