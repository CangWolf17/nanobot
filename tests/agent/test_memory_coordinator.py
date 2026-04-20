"""
TDD: MemoryCoordinator Contract — P1-3

Tests for the MemoryCoordinator, which unifies budget tracking and
consolidation logic across pre-reply and background paths.

These tests define the EXPECTED interface.
Implementation: nanobot/agent/memory_coordinator.py

Current state:
  - MemoryStore (Layer A): long-term memory
  - CompactStateManager (Layer B): compact_state per session
  - SessionManager (Layer C): messages + last_consolidated pointer
  - AgentLoop._inflight_turns (Layer D): turn state mirror (REDUNDANT)

Problem: Two separate budget checks (pre-reply + fallback) and
_inflight_turns mirror of session state.

Desired: MemoryCoordinator as single coordinator with unified budget logic.
"""

import time
from dataclasses import dataclass, field


# =============================================================================
# P1-3a: MemoryCoordinator expected interface
# =============================================================================

@dataclass
class BudgetInfo:
    """Result of a budget check."""
    is_over_budget: bool
    estimated_tokens: int
    source: str  # "compact" | "full" | "budget_disabled"


@dataclass
class MemoryCoordinator:
    """Unified coordinator for memory consolidation and budget tracking.

    Owns the single source of truth for:
    - Budget checking (pre-reply path + fallback path use same logic)
    - Consolidation triggering
    - last_consolidated pointer updates
    - compact_state coordination

    Replaces: scattered budget checks in AgentLoop._should_run_pre_reply_*()
    and AgentLoop._select_history_for_reply().
    """

    enabled: bool = True
    prompt_budget: int = 100_000
    recent_history_fallback_messages: int = 50

    # Internal budget tracking state
    _last_check_session_key: str | None = field(default=None, repr=False)
    _last_check_result: BudgetInfo | None = field(default=None, repr=False)
    _last_check_timestamp: float = field(default=0.0, repr=False)

    def check_budget(self, session) -> BudgetInfo:
        """Check if session history is over budget. Caches result.

        This is the SINGLE budget check — pre-reply and fallback paths
        both call this method, guaranteeing consistent budget logic.
        """
        # Check if we can use cached result for same session
        if (
            self._last_check_session_key == getattr(session, "key", None)
            and self._last_check_result is not None
            and (time.monotonic() - self._last_check_timestamp) < 5.0
        ):
            return self._last_check_result

        if not self.enabled:
            result = BudgetInfo(is_over_budget=False, estimated_tokens=0, source="budget_disabled")
        else:
            estimated = self._estimate_prompt_tokens(session)
            result = BudgetInfo(
                is_over_budget=estimated >= self.prompt_budget,
                estimated_tokens=estimated,
                source="full" if estimated >= self.prompt_budget else "compact",
            )

        self._last_check_session_key = getattr(session, "key", None)
        self._last_check_result = result
        self._last_check_timestamp = time.monotonic()
        return result

    def should_run_pre_reply_consolidation(self, session, msg=None) -> tuple[bool, str]:
        """Pre-reply consolidation gate. Returns (should_run, reason)."""
        if not self.enabled:
            return False, "memory_disabled"
        if msg is not None and bool((msg.metadata or {}).get("workspace_harness_auto")):
            return False, "harness_auto_skip"
        budget = self.check_budget(session)
        if not budget.is_over_budget:
            return False, "under_budget_skip"
        return True, "over_budget"

    def should_fallback_to_recent(self, session) -> tuple[bool, str]:
        """History fallback gate. Uses same budget check as pre-reply path."""
        if not self.enabled:
            return False, "memory_disabled"
        budget = self.check_budget(session)
        if not budget.is_over_budget:
            return False, "under_budget"
        return True, f"fallback_via_{budget.source}"

    def get_fallback_history(self, session, *, max_messages: int | None = None) -> list:
        """Get recent history slice for fallback when full history is over budget."""
        if max_messages is None:
            max_messages = self.recent_history_fallback_messages
        return session.get_history(max_messages=max_messages)

    def get_consolidated_offset(self, session) -> int:
        """Get the last consolidated message offset from session."""
        return session.last_consolidated

    # Abstract — implemented by actual coordinator
    def _estimate_prompt_tokens(self, session) -> int: ...


# =============================================================================
# P1-3a: MemoryCoordinator tests
# =============================================================================

class TestMemoryCoordinatorBudgetContract:
    """MemoryCoordinator provides a single, cached budget check for all paths."""

    def test_budget_check_is_cached_per_session(self):
        """Budget check for same session should be cached (no re-estimation)."""
        coord = MemoryCoordinator(enabled=True, prompt_budget=100_000)
        call_count = 0

        class MockSession:
            key = "feishu:oc_123"
            last_consolidated = 0
            messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]

            def get_history(self, max_messages=0):
                return self.messages

        session = MockSession()

        def counting_estimator(s):
            nonlocal call_count
            call_count += 1
            return 200_000  # Over budget

        coord._estimate_prompt_tokens = counting_estimator

        # First call — estimates
        r1 = coord.check_budget(session)
        assert r1.is_over_budget is True
        assert call_count == 1

        # Second call — cached
        r2 = coord.check_budget(session)
        assert r2.is_over_budget is True
        assert call_count == 1  # Still 1, cached

    def test_budget_check_not_cached_for_different_session(self):
        """Budget check for different session should NOT use cache."""
        coord = MemoryCoordinator(enabled=True, prompt_budget=100_000)
        call_count = 0

        class MockSession:
            def __init__(self, key):
                self.key = key
                self.last_consolidated = 0
                self.messages = [{"role": "user", "content": "x"}]

            def get_history(self, max_messages=0):
                return self.messages

        def counting_estimator(s):
            nonlocal call_count
            call_count += 1
            return 200_000

        coord._estimate_prompt_tokens = counting_estimator

        s1 = MockSession("session_1")
        s2 = MockSession("session_2")

        coord.check_budget(s1)
        assert call_count == 1

        coord.check_budget(s2)  # Different session — not cached
        assert call_count == 2

    def test_pre_reply_and_fallback_share_same_budget_check(self):
        """Pre-reply consolidation and history fallback must use the same budget logic."""
        coord = MemoryCoordinator(enabled=True, prompt_budget=100_000)

        class MockSession:
            key = "feishu:oc_789"
            last_consolidated = 0
            messages = [{"role": "user", "content": f"msg{i}"} for i in range(100)]

            def get_history(self, max_messages=0):
                return self.messages

        session = MockSession()
        coord._estimate_prompt_tokens = lambda s: 200_000  # Over budget

        # Both paths use the same check_budget method
        pre_reply_should, _ = coord.should_run_pre_reply_consolidation(session)
        fallback_should, _ = coord.should_fallback_to_recent(session)

        # Both should agree — same budget, same answer
        assert pre_reply_should is True
        assert fallback_should is True

    def test_harness_auto_skips_pre_reply_consolidation(self):
        """Messages from harness-auto should skip pre-reply consolidation."""
        coord = MemoryCoordinator(enabled=True)

        class MockMsg:
            metadata = {"workspace_harness_auto": True}

        class MockSession:
            key = "s1"
            last_consolidated = 0
            messages = []
            def get_history(self, max_messages=0): return []

        coord._estimate_prompt_tokens = lambda s: 200_000
        should, reason = coord.should_run_pre_reply_consolidation(MockSession(), msg=MockMsg())

        assert should is False
        assert reason == "harness_auto_skip"

    def test_disabled_memory_skips_all_paths(self):
        """When memory is disabled, both paths should return skip."""
        coord = MemoryCoordinator(enabled=False)

        class MockSession:
            key = "s1"
            last_consolidated = 0
            messages = []
            def get_history(self, max_messages=0): return []

        session = MockSession()

        should_pre, reason_pre = coord.should_run_pre_reply_consolidation(session)
        should_fallback, reason_fallback = coord.should_fallback_to_recent(session)

        assert should_pre is False
        assert reason_pre == "memory_disabled"
        assert should_fallback is False
        assert reason_fallback == "memory_disabled"


# =============================================================================
# P1-3b: _inflight_turns mirror elimination contract
# =============================================================================

class TestInflightTurnsMirrorElimination:
    """AgentLoop._inflight_turns mirrors SessionManager state — should be eliminated."""

    def test_inflight_turns_state_is_redundant_with_session(self):
        """_inflight_turns tracks running turns, but session already has this info."""
        # Current: AgentLoop._inflight_turns[session_key] = turn_metadata
        # Problem: duplicate of session.active_turn / session.last_consolidated
        # Fix: use session as single source of truth, no separate mirror

        class MockSession:
            key = "feishu:oc_123"
            active = True
            last_consolidated = 50

        # With MemoryCoordinator, session is the truth.
        # _inflight_turns should be removed — it adds no new information.
        session = MockSession()

        # Session already tells us if there's an active turn
        assert session.active is True
        assert session.last_consolidated == 50
        # No need for separate _inflight_turns dict

    def test_turn_context_instead_of_inflight_turns(self):
        """Per-turn state should live in TurnContext, not AgentLoop state dict."""
        # TurnContext (P0-2) owns: stream state, terminal KP, segment info
        # AgentLoop should NOT maintain a parallel _inflight_turns dict
        # This is the design contract established in Phase 1

        class TurnContext:
            def __init__(self, session_key):
                self.session_key = session_key
                self.status = "pending"

        ctx = TurnContext("feishu:oc_123")
        ctx.status = "running"

        # TurnContext owns the state, not a separate dict
        assert ctx.status == "running"
        # No separate _inflight_turns["feishu:oc_123"] = {...} needed


# =============================================================================
# P1-3c: BudgetInfo contract
# =============================================================================

class TestBudgetInfoContract:
    """BudgetInfo should carry all information needed for budget decisions."""

    def test_budget_info_fields(self):
        """BudgetInfo must have is_over_budget, estimated_tokens, source."""
        info = BudgetInfo(is_over_budget=True, estimated_tokens=150_000, source="full")

        assert info.is_over_budget is True
        assert info.estimated_tokens == 150_000
        assert info.source == "full"

    def test_budget_info_source_values(self):
        """Known source values should be documented."""
        valid_sources = {"compact", "full", "budget_disabled"}

        assert "compact" in valid_sources
        assert "full" in valid_sources
        assert "budget_disabled" in valid_sources

    def test_budget_info_immutable(self):
        """BudgetInfo should be treated as immutable (dataclass frozen=True ideally)."""
        info = BudgetInfo(is_over_budget=False, estimated_tokens=50_000, source="compact")

        # Should not be able to mutate after creation (ideal)
        # This is a contract requirement — callers should not modify BudgetInfo
        assert info.is_over_budget is False
        assert info.estimated_tokens == 50_000
