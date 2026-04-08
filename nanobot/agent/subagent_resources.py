from __future__ import annotations

import importlib.util
import json
import sys
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
        if selected:
            return selected

        effort_rank = {"default": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4}
        fallback: list[tuple[int, int, str]] = []
        for model_id, raw in models.items():
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)) or bool(raw.get("template", False)):
                continue
            if self._clean(raw.get("tier")) != tier:
                continue
            route_clean = self._clean(raw.get("route"))
            try:
                route_index = policy.route_preferences.index(route_clean)
            except ValueError:
                continue
            rank = effort_rank.get(self._clean(raw.get("effort")).lower(), 10_000)
            fallback.append((route_index, rank, model_id))
        fallback.sort()
        return [model_id for _, _, model_id in fallback]

    def _reserved_quota_exhausted(self, policy: RoutePolicy, state: RouteState) -> bool:
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
            return ProviderFailureStatus(
                availability="hard_unavailable",
                reason=reason,
            )

    for marker, reason in _TRANSIENT_MARKERS:
        if marker.lower() in lowered or marker in text:
            return ProviderFailureStatus(
                availability="transient_unavailable",
                reason=reason,
            )

    return ProviderFailureStatus(
        availability="transient_unavailable",
        reason="unknown_error",
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_model_state(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    return data if isinstance(data, dict) else {}


def _runtime_models_from_registry(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    models = registry.get("models") if isinstance(registry, dict) else None
    if not isinstance(models, dict):
        return {}
    if int(registry.get("version") or 0) < 2:
        return {
            model_id: raw for model_id, raw in models.items() if isinstance(raw, dict)
        }

    runtime_models: dict[str, dict[str, Any]] = {}
    for model_id, raw in models.items():
        if not isinstance(raw, dict):
            continue
        runtime_models[model_id] = {
            "tier": raw.get("tier"),
            "family": raw.get("family"),
            "effort": raw.get("effort"),
            "route": raw.get("route_ref"),
            "provider_model": raw.get("provider_model"),
            "enabled": raw.get("enabled", True),
            "template": raw.get("template", False),
            "aliases": list(raw.get("aliases") or []),
        }
    return runtime_models


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


def _manager_model_from_state(state: dict[str, Any]) -> str:
    for key in ("current_model", "last_known_good_model", "last_effective_model"):
        value = str(state.get(key) or "").strip()
        if value:
            return value
    return ""


def _route_preferences_for_tier(
    registry: dict[str, Any],
    runtime_models: dict[str, dict[str, Any]],
    *,
    tier: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    if int(registry.get("version") or 0) < 2:
        return fallback

    routes: list[str] = []

    def append_route(route: str | None) -> None:
        route_clean = str(route or "").strip()
        if route_clean and route_clean not in routes:
            routes.append(route_clean)

    profile_defaults = registry.get("profile_defaults") if isinstance(registry, dict) else None
    if isinstance(profile_defaults, dict):
        for profile in profile_defaults.values():
            if not isinstance(profile, dict):
                continue
            ref = str(profile.get("ref") or "").strip()
            raw = runtime_models.get(ref)
            if not isinstance(raw, dict):
                continue
            if str(raw.get("tier") or "").strip() != tier:
                continue
            append_route(raw.get("route"))

    for raw in runtime_models.values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("tier") or "").strip() != tier:
            continue
        append_route(raw.get("route"))

    return tuple(routes) or fallback


def _provider_status_policy(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    value = data.get("provider_status_policy")
    return value if isinstance(value, dict) else {}



def _provider_status_probe_interval_seconds(data: dict[str, Any] | None) -> int:
    policy = _provider_status_policy(data)
    value = policy.get("probe_interval_seconds")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 6 * 3600



def _probe_ref_for_route(registry: dict[str, Any], route: str) -> str:
    route_clean = str(route or "").strip()
    if route_clean == "minimax":
        profile_defaults = registry.get("profile_defaults") if isinstance(registry, dict) else None
        if isinstance(profile_defaults, dict):
            archive = profile_defaults.get("archive")
            if isinstance(archive, dict):
                ref = str(archive.get("ref") or "").strip()
                if ref:
                    return ref
    models = registry.get("models") if isinstance(registry, dict) else None
    if isinstance(models, dict):
        for model_id, raw in models.items():
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)) or bool(raw.get("template", False)):
                continue
            if str(raw.get("route") or "").strip() != route_clean:
                continue
            return model_id
    return ""



def _provider_status_last_updated_at(data: dict[str, Any] | None, route: str) -> str:
    provider_status = data.get("provider_status") if isinstance(data, dict) else None
    if not isinstance(provider_status, dict):
        return ""
    status = provider_status.get(str(route or "").strip())
    if not isinstance(status, dict):
        return ""
    return str(status.get("updated_at") or "").strip()



def _is_probe_due(last_updated_at: str, *, now: str | None, probe_interval_seconds: int) -> bool:
    if probe_interval_seconds <= 0:
        return True
    last_dt = _parse_iso_datetime(last_updated_at)
    now_dt = _parse_iso_datetime(now) if now is not None else datetime.now(timezone.utc)
    if last_dt is None or now_dt is None:
        return True
    return (now_dt - last_dt).total_seconds() >= int(probe_interval_seconds)



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
    availability = str(status.get("availability") or "").strip()
    if availability != "transient_unavailable":
        return False
    updated_at = _parse_iso_datetime(status.get("updated_at"))
    now_dt = _parse_iso_datetime(now) if now is not None else datetime.now(timezone.utc)
    if updated_at is None or now_dt is None:
        return False
    return (now_dt - updated_at).total_seconds() >= int(transient_ttl_seconds)



def _normalize_status(value: object) -> tuple[str, str]:
    if isinstance(value, dict):
        availability = str(value.get("availability") or "").strip() or "available"
        reason = str(value.get("reason") or "").strip()
        return availability, reason
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
    if not isinstance(data, dict):
        data = {}
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
    if not isinstance(data, dict):
        data = {}
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



def apply_provider_probe_result(
    *,
    workspace: Path,
    probe: dict[str, Any] | None,
    updated_at: str | None = None,
) -> str | None:
    if not isinstance(probe, dict):
        return None
    api_base = str(probe.get("api_base") or "").strip()
    provider = str(probe.get("provider") or "").strip() or "custom"
    route = _route_from_api_base(api_base, provider)
    known_routes = {"aizhiwen-top", "tokenx", "weclawai", "minimax"}
    if route not in known_routes:
        return None
    if bool(probe.get("ok")):
        refresh_provider_status(workspace=workspace, route=route, updated_at=updated_at)
        return route
    reason = str(probe.get("reason") or "").strip()
    record_provider_failure(
        workspace=workspace,
        route=route,
        error_text=reason,
        updated_at=updated_at,
    )
    return route



def run_workspace_quick_provider_probe(workspace: Path, *, ref: str) -> dict[str, Any] | None:
    script_path = workspace / "scripts" / "model_runtime.py"
    if not script_path.exists():
        return None
    workspace_path = str(workspace)
    scripts_path = str(script_path.parent)
    original_sys_path = list(sys.path)
    original_module_keys = set(sys.modules.keys())
    tracked_keys = {
        "scripts",
        "scripts.model_runtime",
        "scripts.model_registry",
        "model_runtime",
        "model_registry",
        "nanobot_workspace_model_runtime",
    }
    try:
        for candidate in (workspace_path, scripts_path):
            if candidate and candidate not in sys.path:
                sys.path.insert(0, candidate)
        spec = importlib.util.spec_from_file_location("nanobot_workspace_model_runtime", script_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules["nanobot_workspace_model_runtime"] = module
        spec.loader.exec_module(module)
        health_fn = getattr(module, "quick_health_check", None)
        if not callable(health_fn):
            return None
        result = health_fn(
            ref=ref,
            config_path=workspace / "config.json",
            registry_path=workspace / "model_registry.json",
            state_path=workspace / "model_state.json",
            profile="chat",
        )
        return result if isinstance(result, dict) else None
    except Exception:
        return None
    finally:
        sys.path[:] = original_sys_path
        for key in list(sys.modules.keys()):
            if key in tracked_keys and key not in original_module_keys:
                sys.modules.pop(key, None)



def probe_due_provider_routes(
    *,
    workspace: Path,
    routes: list[str] | tuple[str, ...] | None = None,
    now: str | None = None,
    probe_runner: Any | None = None,
) -> list[dict[str, Any]]:
    registry = _load_json(workspace / "model_registry.json")
    selected_routes: list[str] = []
    if isinstance(routes, (list, tuple)):
        for item in routes:
            route = str(item or "").strip()
            if route and route not in selected_routes:
                selected_routes.append(route)
    else:
        provider_status = registry.get("provider_status") if isinstance(registry, dict) else None
        if isinstance(provider_status, dict) and provider_status:
            for route in provider_status:
                route_clean = str(route or "").strip()
                if route_clean and route_clean not in selected_routes:
                    selected_routes.append(route_clean)
        else:
            models = registry.get("models") if isinstance(registry, dict) else None
            if isinstance(models, dict):
                for raw in models.values():
                    if not isinstance(raw, dict):
                        continue
                    route_clean = str(raw.get("route") or "").strip()
                    if route_clean and route_clean not in selected_routes:
                        selected_routes.append(route_clean)
    return [
        probe_provider_route_status(
            workspace=workspace,
            route=route,
            now=now,
            probe_runner=probe_runner,
        )
        for route in selected_routes
    ]



def probe_provider_route_status(
    *,
    workspace: Path,
    route: str,
    now: str | None = None,
    probe_runner: Any | None = None,
) -> dict[str, Any]:
    registry = _load_json(workspace / "model_registry.json")
    probe_interval_seconds = _provider_status_probe_interval_seconds(registry)
    route_clean = str(route or "").strip()
    last_updated_at = _provider_status_last_updated_at(registry, route_clean)
    if not _is_probe_due(last_updated_at, now=now, probe_interval_seconds=probe_interval_seconds):
        return {"status": "skipped", "reason": "not_due", "route": route_clean}
    ref = _probe_ref_for_route(registry, route_clean)
    if not ref:
        return {"status": "skipped", "reason": "no_probe_ref", "route": route_clean}
    runner = probe_runner or run_workspace_quick_provider_probe
    probe = runner(workspace, ref=ref)
    applied_route = apply_provider_probe_result(
        workspace=workspace,
        probe=probe,
        updated_at=now,
    )
    return {
        "status": "updated" if applied_route else "skipped",
        "reason": "applied" if applied_route else "unknown_route",
        "route": applied_route or route_clean,
        "ref": ref,
        "probe": probe if isinstance(probe, dict) else None,
    }



def refresh_provider_in_manager(
    manager: SubagentResourceManager,
    *,
    route: str,
) -> None:
    route_clean = str(route or "").strip()
    if not route_clean:
        return
    base = manager.route_policies.get(route_clean, RoutePolicy())
    manager.route_policies[route_clean] = RoutePolicy(
        max_concurrency=base.max_concurrency,
        availability="available",
        unavailable_reason="",
        window_request_limit=base.window_request_limit,
        reserved_requests=base.reserved_requests,
    )



def apply_provider_failure_to_manager(
    manager: SubagentResourceManager,
    *,
    route: str,
    error_text: str | None,
) -> ProviderFailureStatus | None:
    route_clean = str(route or "").strip()
    if not route_clean:
        return None
    status = classify_provider_failure(error_text)
    base = manager.route_policies.get(route_clean, RoutePolicy())
    manager.route_policies[route_clean] = RoutePolicy(
        max_concurrency=base.max_concurrency,
        availability=status.availability,
        unavailable_reason=status.reason,
        window_request_limit=base.window_request_limit,
        reserved_requests=base.reserved_requests,
    )
    return status



def _merge_route_policy(base: RoutePolicy, override: dict[str, Any] | None) -> RoutePolicy:
    if not isinstance(override, dict):
        return base
    return RoutePolicy(
        max_concurrency=int(override.get("max_concurrency", base.max_concurrency) or 0),
        availability=str(override.get("availability") or base.availability or "available").strip() or "available",
        unavailable_reason=str(override.get("unavailable_reason") or override.get("reason") or base.unavailable_reason or "").strip(),
        window_request_limit=int(override.get("window_request_limit", base.window_request_limit) or 0),
        reserved_requests=int(override.get("reserved_requests", base.reserved_requests) or 0),
    )



def _merge_tier_policy(base: TierPolicy, override: dict[str, Any] | None) -> TierPolicy:
    if not isinstance(override, dict):
        return base
    raw_routes = override.get("route_preferences")
    if isinstance(raw_routes, list):
        routes = tuple(str(item).strip() for item in raw_routes if str(item).strip())
    else:
        routes = base.route_preferences
    return TierPolicy(
        default_effort=str(override.get("default_effort") or base.default_effort or "high").strip() or "high",
        route_preferences=routes,
        allow_queue=bool(override.get("allow_queue", base.allow_queue)),
        queue_limit=int(override.get("queue_limit", base.queue_limit) or 0),
    )



def build_manager_from_workspace_snapshot(
    *,
    workspace: Path,
    provider_status: dict[str, dict[str, str]] | None = None,
    fallback_model: str | None = None,
    now: str | None = None,
) -> SubagentResourceManager:
    registry = _load_json(workspace / "model_registry.json")
    if not isinstance(registry, dict):
        registry = {}
    runtime_models = _runtime_models_from_registry(registry)
    registry["models"] = runtime_models
    _config = _load_json(workspace / "config.json")
    state = _load_model_state(workspace / "model_state.json")
    transient_ttl_seconds = _provider_status_transient_ttl_seconds(registry)

    route_policies = {
        "aizhiwen-top": RoutePolicy(max_concurrency=10),
        "tokenx": RoutePolicy(max_concurrency=3),
        "minimax": RoutePolicy(max_concurrency=1, window_request_limit=600, reserved_requests=50),
    }
    registry_route_policies = registry.get("provider_policies") if isinstance(registry, dict) else None
    if isinstance(registry_route_policies, dict):
        for route, override in registry_route_policies.items():
            route_clean = str(route).strip()
            if not route_clean:
                continue
            base = route_policies.get(route_clean, RoutePolicy())
            route_policies[route_clean] = _merge_route_policy(base, override if isinstance(override, dict) else None)

    runtime_route_names = {
        str(raw.get("route") or "").strip()
        for raw in runtime_models.values()
        if isinstance(raw, dict) and str(raw.get("route") or "").strip()
    }
    for route in runtime_route_names:
        route_policies.setdefault(route, RoutePolicy())

    persisted_status = registry.get("provider_status") if isinstance(registry, dict) else None
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
            route_preferences=_route_preferences_for_tier(
                registry,
                runtime_models,
                tier="standard",
                fallback=("aizhiwen-top", "tokenx", "weclawai"),
            ),
            allow_queue=True,
            queue_limit=10,
        ),
        "lite": TierPolicy(
            default_effort="high",
            route_preferences=_route_preferences_for_tier(
                registry,
                runtime_models,
                tier="lite",
                fallback=("minimax",),
            ),
            allow_queue=True,
            queue_limit=5,
        ),
    }
    registry_tier_policies = registry.get("tier_policies") if isinstance(registry, dict) else None
    if isinstance(registry_tier_policies, dict):
        for tier, override in registry_tier_policies.items():
            tier_clean = str(tier).strip()
            if not tier_clean:
                continue
            base = tier_policies.get(tier_clean, TierPolicy())
            tier_policies[tier_clean] = _merge_tier_policy(base, override if isinstance(override, dict) else None)

    subagent_defaults = registry.get("subagent_defaults", {}) if isinstance(registry, dict) else {}
    manager_model = _manager_model_from_state(state)
    if not manager_model:
        manager_model = str((subagent_defaults or {}).get("model") or "").strip()
    if not manager_model:
        manager_model = str(fallback_model or "").strip()
    manager_tier = _infer_manager_tier_from_ref(manager_model)

    models = registry.get("models") if isinstance(registry, dict) else None
    if isinstance(models, dict) and manager_model and manager_model not in models:
        route = _route_from_api_base(
            str((_config.get("providers") or {}).get("custom", {}).get("apiBase") or ""),
            "custom",
        )
        models[manager_model] = {
            "tier": manager_tier,
            "family": manager_model,
            "effort": "high",
            "route": route,
            "provider": "custom",
            "provider_model": manager_model,
            "connection": {
                "api_base": str((_config.get("providers") or {}).get("custom", {}).get("apiBase") or "").strip(),
                "api_key": str((_config.get("providers") or {}).get("custom", {}).get("apiKey") or "").strip(),
                "extra_headers": (_config.get("providers") or {}).get("custom", {}).get("extraHeaders") or {},
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
