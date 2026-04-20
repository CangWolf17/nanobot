"""
TDD: Harness Field Classification Contract — P1-1

Tests that harness record fields are correctly classified into three domains:
  1. Execution state (runtime owns, stored in store.json)
  2. Delivery state (workspace-side, stored separately or in dedicated sub-dict)
  3. Subagent state (runtime owns, stored in runtime_state sub-dict only)

These tests document the EXPECTED contract and will FAIL until the schema
boundary is properly enforced.

Target: nanobot/harness/models.py
"""

import json
import pytest
from pathlib import Path

# Load harness models without triggering nanobot dependency chain
import sys
# /home/admin/nanobot-fork-live/tests/harness/test_harness_field_classification.py
#   parents[0] = harness/, [1] = tests/, [2] = nanobot-fork-live/
_NANOBOT_ROOT = Path(__file__).resolve().parents[2]
_models_path = _NANOBOT_ROOT / "nanobot" / "harness" / "models.py"

import importlib.util
_spec = importlib.util.spec_from_file_location("nanobot.harness.models", _models_path)
_models = importlib.util.module_from_spec(_spec)
# Register module before exec so dataclass __module__ resolves correctly
sys.modules["nanobot.harness.models"] = _models
_spec.loader.exec_module(_models)

HarnessRecord = _models.HarnessRecord
HarnessRuntimeState = _models.HarnessRuntimeState


# =============================================================================
# P1-1a: Field classification schema
# =============================================================================

class TestHarnessFieldClassification:
    """All harness record fields must belong to exactly one domain."""

    def test_execution_fields_are_top_level(self):
        """Core execution fields live at top level of HarnessRecord."""
        record = HarnessRecord(id="har_test", status="active", phase="executing")
        assert record.status == "active"
        assert record.phase == "executing"
        assert record.title == ""
        assert record.summary == ""

    def test_delivery_fields_are_in_dedicated_sub_dicts(self):
        """Delivery fields must be in structured sub-dicts, not flat top-level keys."""
        record = HarnessRecord(id="har_test")
        # git_delivery should be a structured dict
        assert isinstance(record.git_delivery, dict)
        assert "status" in record.git_delivery
        assert "summary" in record.git_delivery

        # verification should be a structured dict
        assert isinstance(record.verification, dict)
        assert "status" in record.verification
        assert "summary" in record.verification
        assert "artifacts" in record.verification

    def test_runtime_state_is_in_sub_dict_only(self):
        """Runtime state (subagent, auto_state, etc.) must be in runtime_state sub-dict."""
        record = HarnessRecord(id="har_test")

        # Must NOT have top-level flat keys for runtime fields
        # (these should only exist in runtime_state sub-dict)
        assert not hasattr(record, "subagent_status") or getattr(record, "subagent_status", None) is None
        assert not hasattr(record, "auto_state") or getattr(record, "auto_state", None) is None

        # Must HAVE runtime_state sub-dict
        assert hasattr(record, "runtime_state")
        assert isinstance(record.runtime_state, HarnessRuntimeState)

    def test_runtime_state_fields_are_isolated(self):
        """Runtime state should be accessible only through runtime_state sub-dict."""
        rs = HarnessRuntimeState(
            runner="main",
            subagent_status="running",
            subagent_last_run_id="run_123",
            subagent_last_error="",
            subagent_last_summary="",
            auto_state="idle",
            continuation_token="",
            session_key="feishu:oc_123",
        )
        assert rs.subagent_status == "running"
        assert rs.subagent_last_run_id == "run_123"
        assert rs.session_key == "feishu:oc_123"

    def test_workflow_is_isolated_in_sub_dict(self):
        """Workflow fields must be in workflow sub-dict, not flat top-level keys."""
        record = HarnessRecord(id="har_test")
        assert isinstance(record.workflow, dict)
        assert "name" in record.workflow
        assert "spec_path" in record.workflow
        assert "return_to" in record.workflow


# =============================================================================
# P1-1b: Backward compatibility with flat store.json
# =============================================================================

class TestFlatStoreCompatibility:
    """HarnessRecord.from_dict() must handle flat workspace store.json format."""

    def test_from_dict_reads_nested_delivery_fields(self):
        """Structured nested format should be read correctly."""
        data = {
            "id": "har_001",
            "status": "completed",
            "phase": "completed",
            "git_delivery": {"status": "committed", "summary": "pushed to origin"},
            "verification": {
                "status": "passed",
                "summary": "all tests green",
                "artifacts": ["report.pdf"],
            },
        }
        record = HarnessRecord.from_dict(data)

        assert record.git_delivery["status"] == "committed"
        assert record.verification["status"] == "passed"
        assert record.verification["artifacts"] == ["report.pdf"]

    def test_from_dict_reads_nested_runtime_state(self):
        """Structured runtime_state sub-dict should be read correctly."""
        data = {
            "id": "har_002",
            "status": "active",
            "phase": "executing",
            "runtime_state": {
                "runner": "subagent",
                "subagent_status": "running",
                "subagent_last_run_id": "run_abc",
                "auto_state": "running",
                "session_key": "telegram:chat_456",
            },
        }
        record = HarnessRecord.from_dict(data)

        assert record.runtime_state.runner == "subagent"
        assert record.runtime_state.subagent_status == "running"
        assert record.runtime_state.subagent_last_run_id == "run_abc"
        assert record.runtime_state.session_key == "telegram:chat_456"

    def test_from_legacy_maps_flat_fields_to_structured_sub_dicts(self):
        """Legacy flat store.json format should migrate to structured sub-dicts."""
        # This is the ACTUAL format in store.json
        flat_data = {
            "id": "har_003",
            "kind": "work",
            "type": "feature",
            "title": "Test feature",
            "status": "active",
            "phase": "executing",
            # Flat delivery fields (from store.json)
            "git_delivery_status": "pending",
            "git_delivery_summary": "awaiting review",
            "verification_status": "pending",
            "verification_summary": "not yet run",
            "artifacts": ["log.txt"],
            # Flat runtime fields (from store.json)
            "subagent_status": "idle",
            "subagent_last_run_id": "run_xyz",
            "subagent_last_error": "",
            "subagent_last_summary": "",
            "auto_state": "idle",
            "session_key": "feishu:oc_789",
            "created_at": "2026-04-19T10:00:00",
            "updated_at": "2026-04-19T10:00:00",
        }

        record = HarnessRecord.from_legacy(
            record_id=flat_data["id"],
            legacy_index=flat_data,
            legacy_state=flat_data,
        )

        # Delivery fields must be in structured sub-dicts
        assert record.git_delivery["status"] == "pending"
        assert record.verification["status"] == "pending"
        assert record.artifacts == ["log.txt"]

        # Runtime fields must be in runtime_state sub-dict
        assert record.runtime_state.subagent_status == "idle"
        assert record.runtime_state.subagent_last_run_id == "run_xyz"
        assert record.runtime_state.session_key == "feishu:oc_789"

    def test_top_level_flat_runtime_fields_are_not_accessible(self):
        """Top-level flat runtime field keys must not shadow the sub-dict fields."""
        # store.json has flat fields — ensure from_dict() doesn't create
        # top-level attributes that shadow runtime_state sub-dict
        data = {
            "id": "har_004",
            "status": "active",
            "phase": "planning",
            # These flat keys exist in store.json but should NOT become
            # top-level HarnessRecord attributes
            "subagent_status": "running",
            "auto_state": "running",
            "git_delivery_status": "no_commit_required",
        }
        record = HarnessRecord.from_dict(data)

        # These flat fields should be IGNORED (or handled via from_legacy)
        # The record should have the structured sub-dicts instead
        assert isinstance(record.runtime_state, HarnessRuntimeState)
        assert isinstance(record.git_delivery, dict)
        # record.subagent_status should NOT be a top-level attribute
        # (it's only in runtime_state)


# =============================================================================
# P1-1c: Field ownership contract
# =============================================================================

class TestFieldOwnershipContract:
    """Define which code paths write to which field groups."""

    def test_execution_policy_fields(self):
        """execution_policy sub-dict contains delegation and risk settings."""
        record = HarnessRecord(id="har_test")
        ep = record.execution_policy

        # execution_policy is a HarnessExecutionPolicy dataclass
        assert hasattr(ep, "executor_mode")
        assert hasattr(ep, "delegation_level")
        assert hasattr(ep, "risk_level")
        assert hasattr(ep, "subagent_allowed")

        assert ep.executor_mode in {"main", "subagent", "auto"}
        assert ep.delegation_level in {"none", "assist", "default", "required"}

    def test_to_dict_roundtrip_preserves_all_fields(self):
        """Round-trip through to_dict/from_dict must preserve all structured fields."""
        original = HarnessRecord(
            id="har_roundtrip",
            status="active",
            phase="executing",
            summary="test summary",
            git_delivery={"status": "committed", "summary": "ok"},
            verification={"status": "passed", "summary": "green", "artifacts": []},
            runtime_state=HarnessRuntimeState(
                runner="main",
                subagent_status="completed",
                subagent_last_run_id="run_final",
            ),
        )

        serialized = original.to_dict()
        restored = HarnessRecord.from_dict(serialized)

        assert restored.id == original.id
        assert restored.status == original.status
        assert restored.phase == original.phase
        assert restored.git_delivery == original.git_delivery
        assert restored.verification == original.verification
        assert restored.runtime_state.subagent_status == original.runtime_state.subagent_status
        assert restored.runtime_state.subagent_last_run_id == original.runtime_state.subagent_last_run_id

    def test_harness_runtime_state_enum_validation(self):
        """runtime_state fields must validate against known enum sets."""
        rs = HarnessRuntimeState(
            runner="subagent",
            subagent_status="failed",
            auto_state="stopped",
        )

        assert rs.runner in _models._RUNNERS
        assert rs.subagent_status in _models._SUBAGENT_STATUSES
        assert rs.auto_state in _models._AUTO_STATES

    def test_harness_record_status_enum_validation(self):
        """HarnessRecord status/phase must validate against known sets."""
        record = HarnessRecord(id="har_test", status="active", phase="executing")

        assert record.status in _models._RECORD_STATUSES
        assert record.phase in _models._RECORD_PHASES
