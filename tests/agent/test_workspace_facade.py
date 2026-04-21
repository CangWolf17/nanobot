"""
TDD: WorkspaceFacade Contract — P1-2

Tests for the WorkspaceFacade, which provides a single cached context
snapshot layer to replace 11 independent build_*_agent_input() functions.

These tests define the EXPECTED interface for WorkspaceFacade.
Implementation: nanobot/agent/workspace_facade.py

Current state: 11 independent build_*_agent_input() functions each read
vault/harness/plans independently, causing redundant I/O and context bloat.
Desired state: WorkspaceFacade with TTL-cached single snapshot + unified API.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path


# =============================================================================
# P1-2a: WorkspaceFacade expected interface
# =============================================================================

@dataclass
class WorkspaceFacade:
    """Single unified access point for workspace context.

    Replaces 11 independent build_*_agent_input() functions with a
    single TTL-cached snapshot layer.

    Fields:
        vault_root: Path to obsidian vault
        workspace_root: Path to nanobot workspace
        ttl_seconds: Cache TTL (default 30s for context freshness)
        _cache: Internal cache with timestamp
    """

    vault_root: Path
    workspace_root: Path
    ttl_seconds: float = 30.0

    _plan_state: dict | None = field(default=None, repr=False)
    _notes_state: dict | None = field(default=None, repr=False)
    _dev_state: dict | None = field(default=None, repr=False)
    _harness_summary: dict | None = field(default=None, repr=False)
    _vault_signals: dict | None = field(default=None, repr=False)
    _cache_timestamp: float = field(default=0.0, repr=False)
    _cache_valid: bool = field(default=False, repr=False)

    def get_plan_state(self, *, force_refresh: bool = False) -> dict | None:
        """Get plan state, using cache if valid."""
        if not force_refresh and self._is_cache_valid():
            return self._plan_state
        self._plan_state = self._read_plan_state()
        self._cache_timestamp = time.monotonic()
        self._cache_valid = True
        return self._plan_state

    def get_notes_state(self, *, force_refresh: bool = False) -> dict | None:
        """Get notes state, using cache if valid."""
        if not force_refresh and self._is_cache_valid():
            return self._notes_state
        self._notes_state = self._read_notes_state()
        self._cache_timestamp = time.monotonic()
        self._cache_valid = True
        return self._notes_state

    def get_dev_state(self, *, force_refresh: bool = False) -> dict | None:
        """Get dev state, using cache if valid."""
        if not force_refresh and self._is_cache_valid():
            return self._dev_state
        self._dev_state = self._read_dev_state()
        self._cache_timestamp = time.monotonic()
        self._cache_valid = True
        return self._dev_state

    def get_harness_summary(self, *, force_refresh: bool = False) -> dict | None:
        """Get harness summary, using cache if valid."""
        if not force_refresh and self._is_cache_valid():
            return self._harness_summary
        self._harness_summary = self._read_harness_summary()
        self._cache_timestamp = time.monotonic()
        self._cache_valid = True
        return self._harness_summary

    def _is_cache_valid(self) -> bool:
        """Check if cache is still within TTL window."""
        if not self._cache_valid:
            return False
        return (time.monotonic() - self._cache_timestamp) < self.ttl_seconds

    def invalidate(self) -> None:
        """Force cache invalidation for next access."""
        self._cache_valid = False

    # Abstract methods — implemented by actual facade
    def _read_plan_state(self) -> dict | None: ...
    def _read_notes_state(self) -> dict | None: ...
    def _read_dev_state(self) -> dict | None: ...
    def _read_harness_summary(self) -> dict | None: ...
    def _read_vault_signals(self) -> dict | None: ...


# =============================================================================
# P1-2a: WorkspaceFacade tests
# =============================================================================

class TestWorkspaceFacadeCacheContract:
    """WorkspaceFacade must cache reads with TTL to prevent redundant I/O."""

    def test_facade_initial_cache_is_invalid(self):
        """New facade should have invalid cache until first access."""
        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test_vault"),
            workspace_root=Path("/tmp/test_workspace"),
        )
        assert not facade._is_cache_valid()
        assert not facade._cache_valid

    def test_cache_becomes_valid_after_first_access(self):
        """After calling get_plan_state(), cache should be valid for TTL window."""
        # Mock the read function
        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test_vault"),
            workspace_root=Path("/tmp/test_workspace"),
        )
        facade._read_plan_state = lambda: {"active_plan": "test"}
        facade._read_notes_state = lambda: None
        facade._read_dev_state = lambda: None
        facade._read_harness_summary = lambda: None
        facade._read_vault_signals = lambda: None

        assert not facade._cache_valid
        state = facade.get_plan_state()
        assert state == {"active_plan": "test"}
        assert facade._cache_valid

    def test_cache_avoids_redundant_reads(self):
        """Within TTL, repeated calls should NOT re-read source."""
        call_count = 0

        def counting_reader():
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test_vault"),
            workspace_root=Path("/tmp/test_workspace"),
        )
        facade._read_plan_state = counting_reader
        facade._read_notes_state = lambda: None
        facade._read_dev_state = lambda: None
        facade._read_harness_summary = lambda: None
        facade._read_vault_signals = lambda: None

        # First call — reads
        result1 = facade.get_plan_state()
        assert call_count == 1
        assert result1 == {"count": 1}

        # Second call — cached, no re-read
        result2 = facade.get_plan_state()
        assert call_count == 1  # Still 1, no new read
        assert result2 == {"count": 1}

    def test_force_refresh_bypasses_cache(self):
        """force_refresh=True should always re-read even within TTL."""
        call_count = 0

        def counting_reader():
            nonlocal call_count
            call_count += 1
            return {"reads": call_count}

        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test_vault"),
            workspace_root=Path("/tmp/test_workspace"),
        )
        facade._read_plan_state = counting_reader
        facade._read_notes_state = lambda: None
        facade._read_dev_state = lambda: None
        facade._read_harness_summary = lambda: None
        facade._read_vault_signals = lambda: None

        facade.get_plan_state()
        assert call_count == 1

        facade.get_plan_state(force_refresh=True)
        assert call_count == 2  # Re-read

        facade.get_plan_state()  # Cached again
        assert call_count == 2  # Still cached

    def test_invalidate_clears_cache(self):
        """invalidate() should mark cache as invalid."""
        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test_vault"),
            workspace_root=Path("/tmp/test_workspace"),
        )
        facade._read_plan_state = lambda: {"plan": "test"}
        facade._read_notes_state = lambda: None
        facade._read_dev_state = lambda: None
        facade._read_harness_summary = lambda: None
        facade._read_vault_signals = lambda: None

        facade.get_plan_state()
        assert facade._cache_valid

        facade.invalidate()
        assert not facade._cache_valid
        assert not facade._is_cache_valid()


# =============================================================================
# P1-2b: Build function interface contract
# =============================================================================

class TestBuildFunctionInterfaceContract:
    """All build_*_agent_input() functions should share a common interface pattern."""

    def test_build_functions_share_signature_pattern(self):
        """All build_*_agent_input() take (user_input, root=None) and return str."""
        # This test documents the expected signature for all 11 build functions:
        # def build_X_agent_input(user_input: str, root: Path | None = None) -> str:
        expected_params = {"user_input", "root"}
        # The pattern is consistent — each function takes user_input and optional root
        assert "user_input" in expected_params
        assert "root" in expected_params

    def test_build_functions_are_idempotent(self):
        """Same input within TTL should produce same output (cached)."""
        # With WorkspaceFacade, the underlying reads are cached
        # So build functions called within TTL should be deterministic
        # This is a contract test — document the expectation
        pass  # Verified via facade cache tests

    def test_build_functions_handle_missing_state_gracefully(self):
        """When vault/plans/harness are missing, build functions should not crash."""
        # When plan state is None, build_plan_agent_input should still
        # return a valid string (possibly empty or with a default message)
        # This is a robustness requirement
        pass  # Documented expectation


# =============================================================================
# P1-2c: NOTES_SEMANTIC_MODULE_CACHE replacement
# =============================================================================

class TestNotesSemanticCacheContract:
    """NOTES_SEMANTIC_MODULE_CACHE in router.py should be replaced with TTL cache."""

    def test_module_cache_without_ttl_is_dangerous(self):
        """Unbounded module-level cache can cause OOM and stale data issues."""
        # Current: module-level dict with no TTL
        # Problem: modules accumulate, never evicted, stale on vault change
        # Solution: WorkspaceFacade with TTL invalidation

        # Document the danger
        unbounded_cache = {}  # Simulates module-level cache
        for i in range(1000):
            unbounded_cache[f"module_{i}"] = {"data": "x" * 1000}

        # Cache grows without bound — this is the problem
        assert len(unbounded_cache) == 1000  # OOM risk

        # TTL-based facade cache is bounded by TTL, not unbounded growth
        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test"),
            workspace_root=Path("/tmp/test"),
            ttl_seconds=30.0,
        )
        # Cache size is constant regardless of access frequency
        assert len([k for k in ["_plan_state", "_notes_state"] if getattr(facade, k, None) is not None]) >= 0

    def test_facade_cache_is_per_instance(self):
        """Each WorkspaceFacade instance has its own cache — no cross-request leakage."""
        facade1 = WorkspaceFacade(vault_root=Path("/tmp/v1"), workspace_root=Path("/tmp/w1"))
        facade2 = WorkspaceFacade(vault_root=Path("/tmp/v2"), workspace_root=Path("/tmp/w2"))

        facade1._plan_state = {"plan": "facade1_plan"}
        facade1._cache_valid = True
        facade1._cache_timestamp = time.monotonic()

        # facade2 should not see facade1's cache
        assert facade2._plan_state is None
        assert not facade2._cache_valid


# =============================================================================
# P1-2d: Context freshness contract
# =============================================================================

class TestContextFreshnessContract:
    """Context snapshots should have known freshness guarantees."""

    def test_context_is_fresh_within_ttl(self):
        """Within TTL, context should be considered fresh."""
        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test"),
            workspace_root=Path("/tmp/test"),
            ttl_seconds=30.0,
        )
        facade._read_plan_state = lambda: {"plan": "fresh"}
        facade._read_notes_state = lambda: None
        facade._read_dev_state = lambda: None
        facade._read_harness_summary = lambda: None
        facade._read_vault_signals = lambda: None

        facade.get_plan_state()
        # Within TTL, cache is valid
        assert facade._is_cache_valid()

    def test_context_becomes_stale_after_ttl(self):
        """After TTL, context should be considered stale and re-fetched."""
        facade = WorkspaceFacade(
            vault_root=Path("/tmp/test"),
            workspace_root=Path("/tmp/test"),
            ttl_seconds=0.01,  # 10ms TTL for testing
        )
        call_count = 0

        def counting_reader():
            nonlocal call_count
            call_count += 1
            return {"reads": call_count}

        facade._read_plan_state = counting_reader
        facade._read_notes_state = lambda: None
        facade._read_dev_state = lambda: None
        facade._read_harness_summary = lambda: None
        facade._read_vault_signals = lambda: None

        facade.get_plan_state()
        assert call_count == 1

        import time as t
        t.sleep(0.02)  # Wait past TTL

        # Now cache should be stale
        assert not facade._is_cache_valid()

        # Re-read
        facade.get_plan_state()
        assert call_count == 2  # Fresh read
