from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderFailureStatus:
    availability: str
    reason: str


@dataclass(frozen=True)
class RoutePolicy:
    max_concurrency: int = 0
    availability: str = "available"
    unavailable_reason: str = ""
    window_request_limit: int = 0
    reserved_requests: int = 0


@dataclass
class RouteState:
    inflight: int = 0
    waiting: int = 0
    window_used_requests: int = 0


@dataclass(frozen=True)
class TierPolicy:
    default_effort: str = "high"
    route_preferences: tuple[str, ...] = ()
    allow_queue: bool = False
    queue_limit: int = 0


@dataclass(frozen=True)
class SubagentRequest:
    model: str | None = None
    tier: str | None = None
    harness_tier: str | None = None
    harness_model: str | None = None
    manager_tier: str | None = None
    manager_model: str | None = None
    task_kind: str | None = None
    session_key: str | None = None
    harness_id: str | None = None


@dataclass(frozen=True)
class SubagentLease:
    model_id: str
    tier: str
    route: str
    effort: str


@dataclass(frozen=True)
class AcquireDecision:
    status: str
    reason: str = ""
    lease: SubagentLease | None = None


class SubagentResourceManager:
    def __init__(
        self,
        *,
        registry: dict[str, Any],
        tier_policies: dict[str, TierPolicy],
        route_policies: dict[str, RoutePolicy],
        route_states: dict[str, RouteState] | None = None,
        defaults: dict[str, str] | None = None,
    ) -> None:
        self.registry = registry if isinstance(registry, dict) else {"models": {}}
        self.tier_policies = dict(tier_policies or {})
        self.route_policies = dict(route_policies or {})
        self.route_states = dict(route_states or {})
        self.defaults = dict(defaults or {})

    def default_request(self, *, tier: str | None = None, model: str | None = None) -> SubagentRequest:
        return SubagentRequest(
            model=self._clean(model),
            tier=self._clean(tier),
            manager_tier=self.defaults.get("manager_tier") or "",
            manager_model=self.defaults.get("manager_model") or "",
        )

    def acquire(self, request: SubagentRequest) -> AcquireDecision:
        candidates = self._resolve_candidates(request)
        if not candidates:
            return AcquireDecision(status="rejected", reason="no_candidate")

        last_unavailable_reason = ""
        for model_id in candidates:
            raw = self._model_record(model_id)
            if raw is None:
                continue
            route = str(raw.get("route") or "").strip()
            tier = str(raw.get("tier") or "").strip()
            effort = str(raw.get("effort") or "").strip().lower()
            policy = self.route_policies.get(route, RoutePolicy())
            state = self.route_states.setdefault(route, RouteState())

            if policy.availability == "hard_unavailable":
                last_unavailable_reason = policy.unavailable_reason or "hard_unavailable"
                continue

            if self._reserved_quota_exhausted(policy, state):
                return AcquireDecision(status="rejected", reason="reserved_quota_exhausted")

            max_concurrency = int(policy.max_concurrency or 0)
            if max_concurrency <= 0 or state.inflight < max_concurrency:
                state.inflight += 1
                if int(policy.window_request_limit or 0) > 0:
                    state.window_used_requests += 1
                return AcquireDecision(
                    status="granted",
                    lease=SubagentLease(
                        model_id=model_id,
                        tier=tier,
                        route=route,
                        effort=effort,
                    ),
                )

            tier_policy = self.tier_policies.get(tier, TierPolicy())
            if tier_policy.allow_queue and state.waiting < int(tier_policy.queue_limit or 0):
                state.waiting += 1
                return AcquireDecision(status="queued", reason="queue_wait")
            if tier_policy.allow_queue and state.waiting >= int(tier_policy.queue_limit or 0):
                return AcquireDecision(status="rejected", reason="queue_limit")
            return AcquireDecision(status="rejected", reason="max_concurrency")

        if last_unavailable_reason:
            return AcquireDecision(status="rejected", reason=last_unavailable_reason)
        return AcquireDecision(status="rejected", reason="no_candidate")

    def release(self, lease: SubagentLease | None) -> None:
        if lease is None:
            return
        state = self.route_states.setdefault(lease.route, RouteState())
        if state.inflight > 0:
            state.inflight -= 1

    def _resolve_candidates(self, request: SubagentRequest) -> list[str]:
        explicit_model = self._clean(request.model)
        if explicit_model:
            resolved = self.resolve_model_ref(explicit_model)
            return [resolved] if resolved else [explicit_model]

        requested_tier = (
            self._clean(request.tier)
            or self._clean(request.harness_tier)
            or self._clean(request.manager_tier)
        )
        if requested_tier:
            tier_candidates = self._tier_candidates(requested_tier)
            if tier_candidates:
                return tier_candidates

        default_model = self._clean(request.harness_model) or self._clean(request.manager_model)
        if not default_model:
            return []
        resolved = self.resolve_model_ref(default_model)
        return [resolved] if resolved else [default_model]

    def _tier_candidates(self, tier: str) -> list[str]:
        policy = self.tier_policies.get(tier, TierPolicy())
        models = self.registry.get("models", {}) if isinstance(self.registry, dict) else {}
        if not isinstance(models, dict):
            return []

        selected: list[str] = []
        desired_effort = self._clean(policy.default_effort).lower()
        for route in policy.route_preferences:
            route_clean = self._clean(route)
            for model_id, raw in models.items():
                if not isinstance(raw, dict):
                    continue
                if not bool(raw.get("enabled", True)) or bool(raw.get("template", False)):
                    continue
                if self._clean(raw.get("tier")) != tier:
                    continue
                if self._clean(raw.get("route")) != route_clean:
                    continue
                if desired_effort and self._clean(raw.get("effort")).lower() != desired_effort:
                    continue
                selected.append(model_id)
        return selected

    @staticmethod
    def _reserved_quota_exhausted(policy: RoutePolicy, state: RouteState) -> bool:
        limit = int(policy.window_request_limit or 0)
        reserve = int(policy.reserved_requests or 0)
        if limit <= 0:
            return False
        remaining = limit - int(state.window_used_requests or 0)
        return remaining <= reserve

    def _model_record(self, model_id: str) -> dict[str, Any] | None:
        models = self.registry.get("models", {}) if isinstance(self.registry, dict) else {}
        if not isinstance(models, dict):
            return None
        raw = models.get(model_id)
        return raw if isinstance(raw, dict) else None

    def resolve_model_ref(self, ref: str) -> str | None:
        candidate = self._clean(ref)
        if not candidate:
            return None
        models = self.registry.get("models", {}) if isinstance(self.registry, dict) else {}
        if not isinstance(models, dict):
            return None
        if candidate in models:
            return candidate
        folded = candidate.casefold()
        for model_id, raw in models.items():
            if model_id.casefold() == folded:
                return model_id
            aliases = raw.get("aliases") if isinstance(raw, dict) else None
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str) and alias.casefold() == folded:
                        return model_id
        return None

    @staticmethod
    def _clean(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""


_TRANSIENT_MARKERS: tuple[tuple[str, str], ...] = (
    ("429", "http_429"),
    ("502", "http_502"),
    ("503", "http_503"),
    ("504", "http_504"),
    ("timed out", "timeout"),
    ("timeout", "timeout"),
)

_HARD_MARKERS: tuple[tuple[str, str], ...] = (
    ("余额不足", "quota_exhausted"),
    ("额度已用完", "quota_exhausted"),
    ("今日用量已用完", "quota_exhausted"),
    ("quota exceeded", "quota_exhausted"),
    ("quota exhausted", "quota_exhausted"),
    ("insufficient balance", "quota_exhausted"),
    ("insufficient_quota", "quota_exhausted"),
    ("credit balance is too low", "quota_exhausted"),
    ("billing", "billing_unavailable"),
)


def classify_provider_failure(error_text: str | None) -> ProviderFailureStatus:
    text = str(error_text or "").strip()
    lowered = text.lower()
    for marker, reason in _HARD_MARKERS:
        if marker.lower() in lowered or marker in text:
            return ProviderFailureStatus("hard_unavailable", reason)
    for marker, reason in _TRANSIENT_MARKERS:
        if marker.lower() in lowered or marker in text:
            return ProviderFailureStatus("transient_unavailable", reason)
    return ProviderFailureStatus("transient_unavailable", "unknown_error")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _route_from_api_base(api_base: str, fallback: str) -> str:
    lower = str(api_base or "").strip().rstrip("/").lower()
    if not lower:
        return fallback
    if "aizhiwen" in lower:
        return "aizhiwen-top"
    if "tokenx" in lower:
        return "tokenx"
    if "weclawai" in lower:
        return "weclawai"
    if "minimax" in lower or "minimaxi" in lower:
        return "minimax"
    return fallback


def _infer_manager_tier_from_ref(ref: str) -> str:
    text = str(ref or "").strip().lower()
    if text.startswith("lite-"):
        return "lite"
    return "standard"


def _provider_status_policy(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    value = data.get("provider_status_policy")
    return value if isinstance(value, dict) else {}


def _provider_status_transient_ttl_seconds(data: dict[str, Any] | None) -> int:
    policy = _provider_status_policy(data)
    value = policy.get("transient_ttl_seconds")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 6 * 3600


def _provider_status_runtime_error_source(data: dict[str, Any] | None) -> str:
    policy = _provider_status_policy(data)
    value = str(policy.get("runtime_error_source") or "").strip()
    return value or "runtime_error"


def _provider_status_refresh_source(data: dict[str, Any] | None) -> str:
    policy = _provider_status_policy(data)
    value = str(policy.get("refresh_source") or "").strip()
    return value or "monitor_refresh"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _status_is_stale(status: object, *, now: str | None, transient_ttl_seconds: int) -> bool:
    if not isinstance(status, dict):
        return False
    if str(status.get("availability") or "").strip() != "transient_unavailable":
        return False
    updated_at = _parse_iso_datetime(status.get("updated_at"))
    now_dt = _parse_iso_datetime(now) if now is not None else datetime.now(timezone.utc)
    if updated_at is None or now_dt is None:
        return False
    return (now_dt - updated_at).total_seconds() >= int(transient_ttl_seconds)


def _normalize_status(value: object) -> tuple[str, str]:
    if isinstance(value, dict):
        return (
            str(value.get("availability") or "").strip() or "available",
            str(value.get("reason") or "").strip(),
        )
    return "available", ""


def record_provider_failure(
    *,
    workspace: Path,
    route: str,
    error_text: str | None,
    updated_at: str | None = None,
    source: str | None = None,
) -> ProviderFailureStatus | None:
    route_clean = str(route or "").strip()
    if not route_clean:
        return None
    status = classify_provider_failure(error_text)
    registry_path = workspace / "model_registry.json"
    data = _load_json(registry_path)
    provider_status = data.setdefault("provider_status", {})
    if not isinstance(provider_status, dict):
        provider_status = {}
        data["provider_status"] = provider_status
    provider_status[route_clean] = {
        "availability": status.availability,
        "reason": status.reason,
        "source": str(source or _provider_status_runtime_error_source(data)).strip() or _provider_status_runtime_error_source(data),
        "updated_at": str(updated_at or _utc_now_iso()).strip() or _utc_now_iso(),
    }
    registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status


def refresh_provider_status(
    *,
    workspace: Path,
    route: str,
    updated_at: str | None = None,
    source: str | None = None,
) -> None:
    route_clean = str(route or "").strip()
    if not route_clean:
        return
    registry_path = workspace / "model_registry.json"
    data = _load_json(registry_path)
    provider_status = data.setdefault("provider_status", {})
    if not isinstance(provider_status, dict):
        provider_status = {}
        data["provider_status"] = provider_status
    provider_status[route_clean] = {
        "availability": "available",
        "reason": "",
        "source": str(source or _provider_status_refresh_source(data)).strip() or _provider_status_refresh_source(data),
        "updated_at": str(updated_at or _utc_now_iso()).strip() or _utc_now_iso(),
    }
    registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_manager_from_workspace_snapshot(
    *,
    workspace: Path,
    provider_status: dict[str, dict[str, str]] | None = None,
    fallback_model: str | None = None,
    now: str | None = None,
) -> SubagentResourceManager:
    registry = _load_json(workspace / "model_registry.json")
    registry.setdefault("models", {})
    config = _load_json(workspace / "config.json")
    transient_ttl_seconds = _provider_status_transient_ttl_seconds(registry)

    route_policies = {
        "aizhiwen-top": RoutePolicy(max_concurrency=10),
        "tokenx": RoutePolicy(max_concurrency=3),
        "minimax": RoutePolicy(max_concurrency=1, window_request_limit=600, reserved_requests=50),
    }
    registry_route_policies = registry.get("provider_policies")
    if isinstance(registry_route_policies, dict):
        for route, override in registry_route_policies.items():
            if not isinstance(override, dict):
                continue
            base = route_policies.get(str(route).strip(), RoutePolicy())
            route_policies[str(route).strip()] = RoutePolicy(
                max_concurrency=int(override.get("max_concurrency", base.max_concurrency) or 0),
                availability=str(override.get("availability") or base.availability or "available").strip() or "available",
                unavailable_reason=str(override.get("unavailable_reason") or override.get("reason") or base.unavailable_reason or "").strip(),
                window_request_limit=int(override.get("window_request_limit", base.window_request_limit) or 0),
                reserved_requests=int(override.get("reserved_requests", base.reserved_requests) or 0),
            )

    persisted_status = registry.get("provider_status")
    if isinstance(persisted_status, dict):
        for route, status in persisted_status.items():
            route_clean = str(route).strip()
            if route_clean not in route_policies:
                continue
            if _status_is_stale(status, now=now, transient_ttl_seconds=transient_ttl_seconds):
                continue
            availability, reason = _normalize_status(status)
            base = route_policies[route_clean]
            route_policies[route_clean] = RoutePolicy(
                max_concurrency=base.max_concurrency,
                availability=availability,
                unavailable_reason=reason,
                window_request_limit=base.window_request_limit,
                reserved_requests=base.reserved_requests,
            )

    for route, status in (provider_status or {}).items():
        route_clean = str(route).strip()
        if route_clean not in route_policies:
            continue
        availability, reason = _normalize_status(status)
        base = route_policies[route_clean]
        route_policies[route_clean] = RoutePolicy(
            max_concurrency=base.max_concurrency,
            availability=availability,
            unavailable_reason=reason,
            window_request_limit=base.window_request_limit,
            reserved_requests=base.reserved_requests,
        )

    tier_policies = {
        "standard": TierPolicy(
            default_effort="high",
            route_preferences=("aizhiwen-top", "tokenx", "weclawai"),
            allow_queue=True,
            queue_limit=10,
        ),
        "lite": TierPolicy(
            default_effort="high",
            route_preferences=("minimax",),
            allow_queue=True,
            queue_limit=5,
        ),
    }
    registry_tier_policies = registry.get("tier_policies")
    if isinstance(registry_tier_policies, dict):
        for tier, override in registry_tier_policies.items():
            if not isinstance(override, dict):
                continue
            base = tier_policies.get(str(tier).strip(), TierPolicy())
            tier_policies[str(tier).strip()] = TierPolicy(
                default_effort=str(override.get("default_effort") or base.default_effort or "high").strip() or "high",
                route_preferences=tuple(
                    str(item).strip()
                    for item in override.get("route_preferences", list(base.route_preferences))
                    if str(item).strip()
                ),
                allow_queue=bool(override.get("allow_queue", base.allow_queue)),
                queue_limit=int(override.get("queue_limit", base.queue_limit) or 0),
            )

    subagent_defaults = registry.get("subagent_defaults", {}) if isinstance(registry, dict) else {}
    agents_defaults = config.get("agents", {}).get("defaults", {}) if isinstance(config, dict) else {}
    manager_model = str(subagent_defaults.get("model") or agents_defaults.get("model") or fallback_model or "").strip()
    manager_tier = _infer_manager_tier_from_ref(manager_model)

    models = registry.get("models")
    if isinstance(models, dict) and manager_model and manager_model not in models:
        providers = config.get("providers", {}) if isinstance(config, dict) else {}
        custom = providers.get("custom") if isinstance(providers, dict) else {}
        api_base = str((custom or {}).get("apiBase") or "").strip()
        route = _route_from_api_base(api_base, "custom")
        models[manager_model] = {
            "tier": manager_tier,
            "family": manager_model,
            "effort": "high",
            "route": route,
            "provider": "custom",
            "provider_model": manager_model,
            "connection": {
                "api_base": api_base,
                "api_key": str((custom or {}).get("apiKey") or "").strip(),
                "extra_headers": (custom or {}).get("extraHeaders") or {},
            },
            "agent": {},
            "enabled": True,
            "template": False,
            "aliases": [],
        }

    return SubagentResourceManager(
        registry=registry,
        tier_policies=tier_policies,
        route_policies=route_policies,
        route_states={route: RouteState() for route in route_policies},
        defaults={
            "manager_model": manager_model,
            "manager_tier": manager_tier,
        },
    )
