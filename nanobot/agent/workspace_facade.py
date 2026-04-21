"""WorkspaceFacade: unified, TTL-cached workspace context access layer.

Replaces 11 independent build_*_agent_input() functions in router.py with a
single cached snapshot layer. Each facade instance has its own isolated cache
(no cross-request leakage), and reads are deferred until first access.

Design goals:
  1. TTL-cached — no redundant vault/harness/state reads within TTL window
  2. Per-instance isolation — each facade has its own cache
  3. Lazy loading — only reads what's actually needed
  4. Graceful degradation — if workspace scripts unavailable, returns None
  5. Force-refresh bypass — explicit invalidation when needed

Usage:
    facade = WorkspaceFacade(
        workspace_root=Path("/home/admin/.nanobot/workspace"),
        vault_root=Path("/home/admin/obsidian-vault"),
        ttl_seconds=30.0,
    )

    # Within TTL: returns cached; outside TTL: re-reads from disk/scripts
    plan = facade.get_plan_state()
    notes = facade.get_notes_state()

    # Force refresh (e.g., after known state change)
    harness = facade.get_harness_summary(force_refresh=True)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


# =============================================================================
# Module-level imports (workspace scripts)
# These are loaded lazily to avoid hard dependency on workspace being present.
# The facade degrades gracefully if workspace is unavailable.
# =============================================================================

def _load_workspace_loaders() -> dict[str, Any]:
    """Load workspace state-loader functions. Returns empty dict if unavailable."""
    try:
        import sys as _sys

        _ws_root = Path("/home/admin/.nanobot/workspace")
        if str(_ws_root) not in _sys.path:
            _sys.path.insert(0, str(_ws_root))

        from scripts.plan_state import load_plan_state
        from scripts.dev_state import load_active_dev_state
        from scripts.notes_workflow import load_notes_state
        from scripts.harness_projection import get_active_harness
        from scripts.sync_workflow import collect_repo_status
        from scripts.om_brain import build_brain_snapshot

        return {
            "load_plan_state": load_plan_state,
            "load_active_dev_state": load_active_dev_state,
            "load_notes_state": load_notes_state,
            "get_active_harness": get_active_harness,
            "collect_repo_status": collect_repo_status,
            "build_brain_snapshot": build_brain_snapshot,
        }
    except Exception as _exc:
        logger.debug("Workspace loaders unavailable: {}", _exc)
        return {}


_LOADERS: dict[str, Any] | None = None


def _get_loaders() -> dict[str, Any]:
    global _LOADERS
    if _LOADERS is None:
        _LOADERS = _load_workspace_loaders()
    return _LOADERS


# =============================================================================
# WorkspaceFacade
# =============================================================================

@dataclass
class WorkspaceFacade:
    """Unified, TTL-cached access to workspace context.

    Each instance owns an independent cache. Multiple concurrent requests
    each get their own facade instance — no cross-request cache pollution.
    """

    workspace_root: Path
    vault_root: Path
    ttl_seconds: float = 30.0

    # Cached state — all initially None (unread)
    _plan_state: Any = field(default=None, repr=False)
    _notes_state: Any = field(default=None, repr=False)
    _dev_state: Any = field(default=None, repr=False)
    _harness_summary: Any = field(default=None, repr=False)
    _repo_signals: Any = field(default=None, repr=False)
    _brain_snapshot: Any = field(default=None, repr=False)

    # Cache metadata
    _cache_timestamp: float = field(default=0.0, repr=False)
    _cache_valid: bool = field(default=False, repr=False)
    _cache_hits: int = field(default=0, repr=False)
    _cache_misses: int = field(default=0, repr=False)

    # Loader references (lazily populated from _get_loaders())
    _loaders: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._loaders = _get_loaders()

    # -------------------------------------------------------------------------
    # Cache lifecycle
    # -------------------------------------------------------------------------

    def _is_cache_valid(self) -> bool:
        """True if cache is within TTL window."""
        if not self._cache_valid:
            return False
        return (time.monotonic() - self._cache_timestamp) < self.ttl_seconds

    def _refresh_timestamp(self) -> None:
        """Mark cache as freshly populated."""
        self._cache_timestamp = time.monotonic()
        self._cache_valid = True

    def invalidate(self) -> None:
        """Force all caches to expire. Next read will re-fetch."""
        self._cache_valid = False
        self._cache_timestamp = 0.0
        logger.debug("WorkspaceFacade cache invalidated for {}", self.workspace_root)

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Return cache hit/miss statistics for observability."""
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "total": total,
            "hit_rate": round(hit_rate, 3),
            "cache_valid": self._cache_valid,
            "cache_age_seconds": round(time.monotonic() - self._cache_timestamp, 2)
            if self._cache_timestamp > 0
            else -1,
        }

    # -------------------------------------------------------------------------
    # State accessors (all use shared TTL cache)
    # -------------------------------------------------------------------------

    def get_plan_state(self, *, force_refresh: bool = False) -> Any:
        """Get plan/goal state, cached within TTL.

        Returns:
            PlanDraftState or None if unavailable / no active plan.
            Note: None is a valid cached result — subsequent calls within TTL
            return the cached None without re-reading.
        """
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            return self._plan_state  # may be None — that's a valid cached value

        self._cache_misses += 1
        loader = self._loaders.get("load_plan_state")
        if loader is None:
            self._refresh_timestamp()
            self._plan_state = None
            return None

        try:
            from scripts.work_store import active_work_root
            work_root = active_work_root(self.workspace_root)
            if work_root is not None:
                self._plan_state = loader(work_root)
            else:
                self._plan_state = None
        except Exception as exc:
            logger.warning("Failed to load plan state: {}", exc)
            self._plan_state = None

        self._refresh_timestamp()
        return self._plan_state

    def get_notes_state(self, *, force_refresh: bool = False) -> Any:
        """Get notes vault state, cached within TTL.

        Returns:
            NotesState or None if unavailable / no active notes session.
        """
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            return self._notes_state

        self._cache_misses += 1
        loader = self._loaders.get("load_notes_state")
        if loader is None:
            self._refresh_timestamp()
            self._notes_state = None
            return None

        try:
            self._notes_state = loader(self.workspace_root)
        except Exception as exc:
            logger.warning("Failed to load notes state: {}", exc)
            self._notes_state = None

        self._refresh_timestamp()
        return self._notes_state

    def get_dev_state(self, *, force_refresh: bool = False) -> Any:
        """Get dev discipline / runtime protocol state, cached within TTL.

        Returns:
            dict or None if unavailable / no active dev state.
        """
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            return self._dev_state

        self._cache_misses += 1
        loader = self._loaders.get("load_active_dev_state")
        if loader is None:
            self._refresh_timestamp()
            self._dev_state = None
            return None

        try:
            self._dev_state = loader(self.workspace_root)
        except Exception as exc:
            logger.warning("Failed to load dev state: {}", exc)
            self._dev_state = None

        self._refresh_timestamp()
        return self._dev_state

    def get_harness_summary(self, *, force_refresh: bool = False) -> Any:
        """Get active harness summary, cached within TTL.

        Returns:
            HarnessSummary or None if no active harness.
        """
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            return self._harness_summary

        self._cache_misses += 1
        loader = self._loaders.get("get_active_harness")
        if loader is None:
            self._refresh_timestamp()
            self._harness_summary = None
            return None

        try:
            self._harness_summary = loader(self.workspace_root)
        except Exception as exc:
            logger.warning("Failed to load harness summary: {}", exc)
            self._harness_summary = None

        self._refresh_timestamp()
        return self._harness_summary

    def get_repo_signals(self, *, force_refresh: bool = False) -> Any:
        """Get git repo status signals (dirty/clean/uncommitted), cached within TTL.

        Returns:
            dict or None if unavailable.
        """
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            return self._repo_signals

        self._cache_misses += 1
        loader = self._loaders.get("collect_repo_status")
        if loader is None:
            self._refresh_timestamp()
            self._repo_signals = None
            return None

        try:
            self._repo_signals = loader()
        except Exception as exc:
            logger.warning("Failed to collect repo signals: {}", exc)
            self._repo_signals = None

        self._refresh_timestamp()
        return self._repo_signals

    def get_brain_snapshot(self, *, force_refresh: bool = False) -> Any:
        """Get agent brain / OM context snapshot, cached within TTL.

        Returns:
            str or None if unavailable.
        """
        if not force_refresh and self._is_cache_valid():
            self._cache_hits += 1
            return self._brain_snapshot

        self._cache_misses += 1
        loader = self._loaders.get("build_brain_snapshot")
        if loader is None:
            self._refresh_timestamp()
            self._brain_snapshot = None
            return None

        try:
            self._brain_snapshot = loader()
        except Exception as exc:
            logger.warning("Failed to build brain snapshot: {}", exc)
            self._brain_snapshot = None

        self._refresh_timestamp()
        return self._brain_snapshot

    # -------------------------------------------------------------------------
    # Bulk snapshot (replaces the 11 individual build_*_agent_input calls)
    # -------------------------------------------------------------------------

    def get_full_snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Load all workspace state in a single pass.

        All state fields share one TTL to avoid reading vault/harness multiple
        times per cycle. Returns a dict with all available state.

        This is the entry point that replaces the 11 independent
        build_*_agent_input() functions in router.py.
        """
        # Prime all fields (each accessor uses shared TTL cache)
        self.get_plan_state(force_refresh=force_refresh)
        self.get_notes_state(force_refresh=force_refresh)
        self.get_dev_state(force_refresh=force_refresh)
        self.get_harness_summary(force_refresh=force_refresh)
        self.get_repo_signals(force_refresh=force_refresh)
        self.get_brain_snapshot(force_refresh=force_refresh)

        return {
            "plan": self._plan_state,
            "notes": self._notes_state,
            "dev": self._dev_state,
            "harness": self._harness_summary,
            "repo_signals": self._repo_signals,
            "brain": self._brain_snapshot,
        }
