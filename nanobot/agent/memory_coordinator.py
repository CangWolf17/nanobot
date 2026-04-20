"""MemoryCoordinator: unified budget tracking and consolidation gating.

Wraps MemoryConsolidator to provide a single, cached budget check used by both
the pre-reply consolidation path and the history-fallback path. This eliminates
the double-estimation problem where is_over_budget() was called twice per turn
with no caching.

Design goals:
  1. Single budget check — pre-reply and fallback both call check_budget()
  2. Per-session caching — budget result cached for 5s to avoid double-estimation
  3. Consistent budget logic — both paths use identical budget threshold
  4. Drop-in alongside MemoryConsolidator — does not replace it

Usage:
    coordinator = MemoryCoordinator(consolidator=memory_consolidator, enabled=True)

    # Both paths use the same cached result
    should_consolidate, reason = coordinator.should_run_pre_reply(session, msg=msg)
    should_fallback, reason = coordinator.should_fallback_to_recent(session)

    # Direct budget check with cache
    budget = coordinator.check_budget(session)
    if budget.is_over_budget:
        ...
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.agent.memory import MemoryConsolidator


@dataclass
class BudgetInfo:
    """Result of a budget check. Immutable — callers must not mutate."""

    is_over_budget: bool
    estimated_tokens: int
    source: str  # "compact" | "full" | "budget_disabled"

    def __post_init__(self) -> None:
        if self.source not in {"compact", "full", "budget_disabled"}:
            raise ValueError(f"Unknown budget source: {self.source!r}")


@dataclass
class MemoryCoordinator:
    """Unified coordinator for memory consolidation budget decisions.

    Wraps an existing MemoryConsolidator. The consolidator handles actual
    consolidation I/O; this coordinator handles budget gate decisions with
    per-session caching to prevent double-estimation.
    """

    consolidator: "MemoryConsolidator"
    enabled: bool = True
    recent_history_fallback_messages: int = 50
    _cache_ttl_seconds: float = 5.0  # short TTL to avoid stale across turns

    # Per-session cache: session_key -> (BudgetInfo, timestamp)
    _budget_cache: dict[str, tuple[BudgetInfo, float]] = field(default_factory=dict, repr=False)

    # Harness-auto skip reason (must match AgentLoop constant)
    _HARNESS_AUTO_SKIP = "harness_auto_skip"
    _DISABLED_SKIP = "memory_disabled"
    _UNDER_BUDGET_SKIP = "under_budget_skip"

    def check_budget(self, session: "Session") -> BudgetInfo:
        """Check if session history is over budget. Caches result per session.

        This is the SINGLE budget check — pre-reply consolidation and history
        fallback both call this method, guaranteeing consistent logic and
        avoiding double-estimation within the same turn.

        Returns:
            BudgetInfo with is_over_budget, estimated_tokens, source.
        """
        session_key = getattr(session, "key", None) or str(id(session))

        # Check cache
        cached = self._budget_cache.get(session_key)
        if cached is not None:
            info, cached_at = cached
            if (time.monotonic() - cached_at) < self._cache_ttl_seconds:
                return info  # Cache hit

        # Cache miss — delegate to MemoryConsolidator
        if not self.enabled:
            return BudgetInfo(
                is_over_budget=False,
                estimated_tokens=0,
                source="budget_disabled",
            )

        over, estimated, source = self.consolidator.is_over_budget(session)
        info = BudgetInfo(
            is_over_budget=over,
            estimated_tokens=estimated,
            source=source,
        )
        self._budget_cache[session_key] = (info, time.monotonic())
        return info

    def should_run_pre_reply(
        self, session: "Session", *, msg: "InboundMessage | None" = None
    ) -> tuple[bool, str]:
        """Pre-reply consolidation gate.

        Returns (should_run, reason):
            (True, "over_budget") — run consolidation
            (False, "memory_disabled") — consolidation disabled
            (False, "harness_auto_skip") — harness-auto message, skip
            (False, "under_budget_skip") — within budget, no consolidation needed
        """
        if not self.enabled:
            return False, self._DISABLED_SKIP

        if msg is not None and bool((msg.metadata or {}).get("workspace_harness_auto")):
            return False, self._HARNESS_AUTO_SKIP

        budget = self.check_budget(session)
        if not budget.is_over_budget:
            return False, self._UNDER_BUDGET_SKIP
        return True, "over_budget"

    def should_fallback_to_recent(self, session: "Session") -> tuple[bool, str]:
        """History fallback gate — uses same budget check as pre-reply path.

        Returns (should_fallback, reason):
            (True, f"fallback_via_{source}") — use recent window
            (False, "memory_disabled") — consolidation disabled
            (False, "under_budget") — full history acceptable
        """
        if not self.enabled:
            return False, self._DISABLED_SKIP

        budget = self.check_budget(session)
        if not budget.is_over_budget:
            return False, "under_budget"
        return True, f"fallback_via_{budget.source}"

    def get_fallback_history(self, session: "Session") -> list[dict[str, Any]]:
        """Return recent-history slice for fallback when full history is over budget."""
        return session.get_history(max_messages=self.recent_history_fallback_messages)

    def invalidate_session(self, session_key: str) -> None:
        """Evict cached budget for a specific session (e.g., after consolidation)."""
        self._budget_cache.pop(session_key, None)

    def clear_cache(self) -> None:
        """Clear all cached budget results."""
        self._budget_cache.clear()
