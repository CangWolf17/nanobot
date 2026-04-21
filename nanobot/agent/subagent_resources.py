from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from nanobot.agent.subagent_types import get_subagent_type_spec

_ANTHROPIC_DEFAULT_API_BASE = "https://api.anthropic.com"
_OPENAI_DEFAULT_API_BASE = "https://api.openai.com/v1"
_GITHUB_COPILOT_DEFAULT_API_BASE = "https://api.githubcopilot.com"
_ANTHROPIC_VERSION = "2023-06-01"
_AZURE_OPENAI_API_VERSION = "2024-10-21"
_WORKSPACE_FALLBACK_BACKENDS = {"openai_codex"}
_DEFAULT_NANOBOT_ROOT = Path.home() / ".nanobot"
_DEFAULT_WORKSPACE_ROOT = _DEFAULT_NANOBOT_ROOT / "workspace"


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
    candidate_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSubagentSpawnRequest:
    name: str | None = None
    subagent_type: str | None = None
    model: str | None = None
    preferred_route: str | None = None
    compatibility_tier: str | None = None


@dataclass(frozen=True)
class SubagentResolution:
    requested_name: str | None
    requested_type: str | None
    requested_model: str | None
    preferred_route: str | None
    candidate_chain: tuple[str, ...]
    resolved_model_id: str
    reason: str


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
    queue_route: str = ""
    queue_tier: str = ""


@dataclass(frozen=True)
class RuntimeProbeRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]
    provider: str
    api_base: str
    route: str


class SubagentResourceManager:
    def __init__(
        self,
        *,
        registry: dict[str, Any],
        tier_policies: dict[str, TierPolicy],
        route_policies: dict[str, RoutePolicy],
        route_states: dict[str, RouteState] | None = None,
        defaults: dict[str, Any] | None = None,
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
        return self.acquire_candidates(candidates)

    def acquire_candidates(self, candidates: list[str] | tuple[str, ...]) -> AcquireDecision:
        if not candidates:
            return AcquireDecision(status="rejected", reason="no_candidate")

        last_unavailable_reason = ""
        for model_id in candidates:
            raw = self._model_record(model_id)
            if raw is None:
                if isinstance(model_id, str) and model_id.strip():
                    return AcquireDecision(
                        status="granted",
                        lease=SubagentLease(
                            model_id=model_id.strip(),
                            tier="direct",
                            route="",
                            effort="",
                        ),
                    )
                continue
            route = str(raw.get("route") or "").strip()
            tier = str(raw.get("tier") or "").strip()
            effort = str(raw.get("effort") or "").strip().lower()
            policy = self.route_policies.get(route, RoutePolicy())
            state = self.route_states.setdefault(route, RouteState())

            if policy.availability in {"hard_unavailable", "manual_outage"}:
                last_unavailable_reason = policy.unavailable_reason or policy.availability
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
                return AcquireDecision(
                    status="queued",
                    reason="queue_wait",
                    queue_route=route,
                    queue_tier=tier,
                )

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

    def release_waiting_route(self, route: str | None) -> None:
        route_clean = self._clean(route)
        if not route_clean:
            return
        state = self.route_states.setdefault(route_clean, RouteState())
        if state.waiting > 0:
            state.waiting -= 1

    def _resolve_candidates(self, request: SubagentRequest) -> list[str]:
        if request.candidate_chain:
            return [str(item).strip() for item in request.candidate_chain if str(item).strip()]

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

        fallback_candidates: list[str] = []
        for candidate in (request.harness_model, request.manager_model):
            default_model = self._clean(candidate)
            if not default_model:
                continue
            resolved = self.resolve_model_refs(default_model)
            if resolved:
                fallback_candidates.extend(resolved)
            else:
                fallback_candidates.append(default_model)
        deduped: list[str] = []
        for candidate in fallback_candidates:
            if candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def resolve_model_refs(self, ref: str) -> list[str]:
        candidate = self._clean(ref)
        if not candidate:
            return []
        models = self.registry.get("models", {}) if isinstance(self.registry, dict) else {}
        if not isinstance(models, dict):
            return []
        folded = candidate.casefold()
        matches: list[str] = []
        for model_id, raw in models.items():
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)) or bool(raw.get("template", False)):
                continue
            if model_id.casefold() == folded:
                matches.append(model_id)
                continue
            if str(raw.get("family") or "").strip().casefold() == folded:
                matches.append(model_id)
                continue
            aliases = raw.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str) and alias.casefold() == folded:
                        matches.append(model_id)
                        break
        return matches

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

    def list_type_candidates(
        self,
        *,
        family: str,
        effort: str,
        preferred_route: str | None = None,
    ) -> list[str]:
        models = self.registry.get("models", {}) if isinstance(self.registry, dict) else {}
        if not isinstance(models, dict):
            return []

        preferred = self._clean(preferred_route)
        ordered_routes: list[str] = []
        for policy in self.tier_policies.values():
            for item in policy.route_preferences:
                route = self._clean(item)
                if route and route not in ordered_routes:
                    ordered_routes.append(route)

        ranked: list[tuple[int, int, str]] = []
        for model_id, raw in models.items():
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)) or bool(raw.get("template", False)):
                continue
            if self._clean(raw.get("family")) != self._clean(family):
                continue
            if self._clean(raw.get("effort")).lower() != self._clean(effort).lower():
                continue
            route = self._clean(raw.get("route"))
            policy = self.route_policies.get(route, RoutePolicy())
            availability = str(policy.availability or "available").strip()
            if availability in {"hard_unavailable", "manual_outage"}:
                availability_rank = 2
            elif availability == "transient_unavailable":
                availability_rank = 1
            else:
                availability_rank = 0
            if preferred and route == preferred:
                route_rank = -1
            else:
                try:
                    route_rank = ordered_routes.index(route)
                except ValueError:
                    route_rank = len(ordered_routes) + 100
            ranked.append((availability_rank, route_rank, model_id))

        ranked.sort()
        return [model_id for _, _, model_id in ranked]

    def resolve_spawn_request(
        self,
        request: RuntimeSubagentSpawnRequest,
        *,
        fallback_model: str | None = None,
    ) -> SubagentResolution:
        requested_name = self._clean(request.name) or None
        requested_type = self._clean(request.subagent_type) or None
        requested_model = self._clean(request.model) or None
        preferred_route = self._clean(request.preferred_route) or None
        compatibility_tier = self._clean(request.compatibility_tier) or None

        if requested_model:
            resolved_model = self.resolve_model_ref(requested_model) or requested_model
            return SubagentResolution(
                requested_name=requested_name,
                requested_type=requested_type,
                requested_model=requested_model,
                preferred_route=preferred_route,
                candidate_chain=(resolved_model,),
                resolved_model_id=resolved_model,
                reason="explicit_model",
            )

        if requested_type:
            spec = get_subagent_type_spec(requested_type)
            candidates = self.list_type_candidates(
                family=spec.family,
                effort=spec.effort,
                preferred_route=preferred_route,
            )
            if candidates:
                return SubagentResolution(
                    requested_name=requested_name,
                    requested_type=requested_type,
                    requested_model=requested_model,
                    preferred_route=preferred_route,
                    candidate_chain=tuple(candidates),
                    resolved_model_id=candidates[0],
                    reason=f"builtin_type:{spec.name}",
                )
            if not compatibility_tier:
                raise ValueError(
                    f"no candidates for subagent type {requested_type} ({spec.family}/{spec.effort})"
                )

        if compatibility_tier:
            manager_request = self.default_request(tier=compatibility_tier)
            candidates = self._resolve_candidates(manager_request)
            if not candidates:
                raise ValueError(f"no candidates for compatibility tier: {compatibility_tier}")
            return SubagentResolution(
                requested_name=requested_name,
                requested_type=None,
                requested_model=requested_model,
                preferred_route=preferred_route,
                candidate_chain=tuple(candidates),
                resolved_model_id=candidates[0],
                reason=f"compatibility_tier:{compatibility_tier}",
            )

        if fallback_model:
            resolved_model = self.resolve_model_ref(fallback_model) or str(fallback_model).strip()
            if resolved_model:
                return SubagentResolution(
                    requested_name=requested_name,
                    requested_type=requested_type,
                    requested_model=requested_model,
                    preferred_route=preferred_route,
                    candidate_chain=(resolved_model,),
                    resolved_model_id=resolved_model,
                    reason="manager_fallback_model",
                )

        raise ValueError("spawn requires `type` or `model`")

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
    ("daily_limit_exceeded", "quota_exhausted"),
    ("daily usage limit exceeded", "quota_exhausted"),
    ("usage_limit_exceeded", "quota_exhausted"),
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
    if text.startswith("pro-"):
        return "pro"
    if text.startswith("standard-"):
        return "standard"
    if "gpt-5.4-mini" in text:
        return "standard"
    if "gpt-5.4" in text:
        return "pro"
    return "standard"


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


def _route_policy_with_status(
    base: RoutePolicy,
    *,
    availability: str,
    reason: str,
) -> RoutePolicy:
    return RoutePolicy(
        max_concurrency=base.max_concurrency,
        availability=str(availability or base.availability or "available").strip() or "available",
        unavailable_reason=str(reason or "").strip(),
        window_request_limit=base.window_request_limit,
        reserved_requests=base.reserved_requests,
    )


def _apply_route_status(
    route_policies: dict[str, RoutePolicy],
    *,
    route: str,
    availability: str,
    reason: str,
) -> bool:
    route_clean = str(route or "").strip()
    if not route_clean:
        return False
    base = route_policies.get(route_clean, RoutePolicy())
    route_policies[route_clean] = _route_policy_with_status(
        base,
        availability=availability,
        reason=reason,
    )
    return True


def _registry_path_for_workspace(workspace: Path) -> Path:
    env_path = str(os.environ.get("NANOBOT_MODEL_REGISTRY_PATH") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    workspace_path = Path(workspace).expanduser()
    if workspace_path == _DEFAULT_WORKSPACE_ROOT:
        return _DEFAULT_NANOBOT_ROOT / "model_registry.json"
    return workspace_path / "model_registry.json"


def _config_path_for_workspace(workspace: Path) -> Path:
    env_path = str(os.environ.get("NANOBOT_CONFIG_PATH") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    workspace_path = Path(workspace).expanduser()
    if workspace_path == _DEFAULT_WORKSPACE_ROOT:
        return _DEFAULT_NANOBOT_ROOT / "config.json"
    return workspace_path / "config.json"


def _load_registry_for_status_update(workspace: Path) -> tuple[Path, dict[str, Any]]:
    registry_path = _registry_path_for_workspace(workspace)
    data = _load_json(registry_path)
    if not isinstance(data, dict):
        data = {}
    return registry_path, data


def _provider_status_bucket(data: dict[str, Any]) -> dict[str, Any]:
    provider_status = data.setdefault("provider_status", {})
    if not isinstance(provider_status, dict):
        provider_status = {}
        data["provider_status"] = provider_status
    return provider_status


def _persist_provider_status_entry(
    *,
    registry_path: Path,
    data: dict[str, Any],
    route: str,
    availability: str,
    reason: str,
    source: str,
    updated_at: str,
) -> None:
    route_clean = str(route or "").strip()
    if not route_clean:
        return
    provider_status = _provider_status_bucket(data)
    provider_status[route_clean] = {
        "availability": str(availability or "available").strip() or "available",
        "reason": str(reason or "").strip(),
        "source": str(source or "").strip(),
        "updated_at": str(updated_at or "").strip(),
    }
    registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    registry_path, data = _load_registry_for_status_update(workspace)
    _persist_provider_status_entry(
        registry_path=registry_path,
        data=data,
        route=route_clean,
        availability=status.availability,
        reason=status.reason,
        source=str(source or _provider_status_runtime_error_source(data)).strip()
        or _provider_status_runtime_error_source(data),
        updated_at=str(updated_at or _utc_now_iso()).strip() or _utc_now_iso(),
    )
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
    registry_path, data = _load_registry_for_status_update(workspace)
    _persist_provider_status_entry(
        registry_path=registry_path,
        data=data,
        route=route_clean,
        availability="available",
        reason="",
        source=str(source or _provider_status_refresh_source(data)).strip()
        or _provider_status_refresh_source(data),
        updated_at=str(updated_at or _utc_now_iso()).strip() or _utc_now_iso(),
    )



def _known_provider_routes(workspace: Path) -> set[str]:
    registry = _load_json(_registry_path_for_workspace(workspace))
    routes = {"aizhiwen-top", "tokenx", "weclawai", "minimax"}

    models = registry.get("models") if isinstance(registry, dict) else None
    if isinstance(models, dict):
        for raw in models.values():
            if not isinstance(raw, dict):
                continue
            route = str(raw.get("route") or "").strip()
            if route:
                routes.add(route)

    provider_status = registry.get("provider_status") if isinstance(registry, dict) else None
    if isinstance(provider_status, dict):
        for route in provider_status:
            route_clean = str(route or "").strip()
            if route_clean:
                routes.add(route_clean)

    provider_policies = registry.get("provider_policies") if isinstance(registry, dict) else None
    if isinstance(provider_policies, dict):
        for route in provider_policies:
            route_clean = str(route or "").strip()
            if route_clean:
                routes.add(route_clean)

    return routes



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
    route = str(probe.get("route") or "").strip() or _route_from_api_base(api_base, provider)
    if route not in _known_provider_routes(workspace):
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



def _resolve_registry_model_ref(registry: dict[str, Any], ref: str) -> str | None:
    candidate = str(ref or "").strip()
    if not candidate:
        return None
    models = registry.get("models", {}) if isinstance(registry, dict) else {}
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



def _connection_api_key(connection: dict[str, Any]) -> str:
    if not isinstance(connection, dict):
        return ""
    api_key = str(connection.get("api_key") or "").strip()
    if api_key:
        return api_key
    env_name = str(connection.get("api_key_env") or "").strip()
    if not env_name:
        return ""
    return str(os.environ.get(env_name) or "").strip()



def _probe_reasoning_effort(raw: dict[str, Any]) -> str | None:
    effort = str(raw.get("effort") or "").strip().lower()
    family = str(raw.get("family") or "").strip().lower()
    if effort not in {"low", "medium", "high", "xhigh"}:
        return None
    if family == "gpt-5.4" and effort == "xhigh":
        return "high"
    return effort


def _merge_extra_headers(
    headers: dict[str, str],
    extra_headers: dict[str, Any],
) -> dict[str, str]:
    merged = dict(headers)
    for key, value in extra_headers.items():
        key_clean = str(key or "").strip()
        value_clean = str(value or "").strip()
        if key_clean:
            merged[key_clean] = value_clean
    return merged



def _probe_user_messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "reply with OK only"}]



def _apply_probe_reasoning_effort(
    payload: dict[str, Any],
    *,
    raw: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(payload)
    reasoning_effort = _probe_reasoning_effort(raw)
    if reasoning_effort:
        enriched["reasoning_effort"] = reasoning_effort
    return enriched



def _response_error_reason(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "").strip()
            if message:
                return message
        message = str(payload.get("message") or "").strip()
        if message:
            return message
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text[:500]
    status_code = int(getattr(response, "status_code", 0) or 0)
    return f"HTTP {status_code}" if status_code else "probe_failed"



def _runtime_probe_backend(raw: dict[str, Any]) -> str:
    from nanobot.providers.registry import find_by_name

    explicit = str(raw.get("probe_backend") or raw.get("adapter") or raw.get("backend") or "").strip()
    if explicit:
        return explicit
    provider_name = str(raw.get("provider") or "custom").strip() or "custom"
    spec = find_by_name(provider_name)
    return spec.backend if spec is not None else "openai_compat"



def _build_openai_compat_probe_request(
    *,
    provider_name: str,
    provider_model: str,
    api_base: str,
    api_key: str,
    extra_headers: dict[str, Any],
    raw: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    payload = _apply_probe_reasoning_effort(
        {
            "model": provider_model,
            "messages": _probe_user_messages(),
            "max_tokens": 8,
            "temperature": 0,
        },
        raw=raw,
    )
    return api_base.rstrip("/") + "/chat/completions", _merge_extra_headers(headers, extra_headers), payload



def _build_azure_openai_probe_request(
    *,
    provider_model: str,
    api_base: str,
    api_key: str,
    raw: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    base_url = api_base if api_base.endswith("/") else api_base + "/"
    url = urljoin(base_url, f"openai/deployments/{provider_model}/chat/completions")
    url = f"{url}?api-version={_AZURE_OPENAI_API_VERSION}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    payload = _apply_probe_reasoning_effort(
        {
            "messages": _probe_user_messages(),
            "max_completion_tokens": 8,
        },
        raw=raw,
    )
    return url, headers, payload



def _build_anthropic_probe_request(
    *,
    provider_model: str,
    api_base: str,
    api_key: str,
    extra_headers: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    model_name = provider_model.split("/", 1)[1] if provider_model.startswith("anthropic/") else provider_model
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        url = base + "/messages"
    elif base.endswith("/v1/messages"):
        url = base
    else:
        url = base + "/v1/messages"

    headers = {
        "content-type": "application/json",
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    if api_key:
        headers["x-api-key"] = api_key

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": _probe_user_messages(),
        "max_tokens": 8,
    }
    return url, _merge_extra_headers(headers, extra_headers), payload



def _build_github_copilot_probe_request(
    *,
    provider_model: str,
    api_base: str,
    extra_headers: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    from nanobot.providers.github_copilot_provider import (
        EDITOR_PLUGIN_VERSION,
        EDITOR_VERSION,
        USER_AGENT,
    )

    access_token = _fetch_github_copilot_access_token()
    headers: dict[str, str] = {
        "content-type": "application/json",
        "authorization": f"Bearer {access_token}",
        "Editor-Version": EDITOR_VERSION,
        "Editor-Plugin-Version": EDITOR_PLUGIN_VERSION,
        "User-Agent": USER_AGENT,
    }

    payload: dict[str, Any] = {
        "model": provider_model,
        "messages": _probe_user_messages(),
        "max_tokens": 8,
        "temperature": 0,
    }
    return api_base.rstrip("/") + "/chat/completions", _merge_extra_headers(headers, extra_headers), payload



def _fetch_github_copilot_access_token() -> str:
    from nanobot.providers.github_copilot_provider import (
        DEFAULT_COPILOT_TOKEN_URL,
        _copilot_headers,
        _load_github_token,
    )

    github_token = _load_github_token()
    if github_token is None or not str(github_token.access or "").strip():
        raise RuntimeError(
            "GitHub Copilot is not logged in. Run: nanobot provider login github-copilot"
        )

    response = httpx.get(
        DEFAULT_COPILOT_TOKEN_URL,
        headers=_copilot_headers(str(github_token.access)),
        timeout=20.0,
        follow_redirects=True,
    )
    status_code = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status_code < 300:
        raise RuntimeError(_response_error_reason(response))
    payload = response.json()
    token = str(payload.get("token") or "").strip() if isinstance(payload, dict) else ""
    if not token:
        raise RuntimeError("GitHub Copilot token exchange returned no token.")
    return token



def _build_runtime_probe_request(
    *,
    resolved: str,
    raw: dict[str, Any],
) -> RuntimeProbeRequest | None:
    backend = _runtime_probe_backend(raw)
    connection = raw.get("connection") if isinstance(raw.get("connection"), dict) else {}
    api_base = str(connection.get("api_base") or "").strip()
    api_key = _connection_api_key(connection)
    provider_name = str(raw.get("provider") or "custom").strip() or "custom"
    provider_model = str(raw.get("provider_model") or resolved).strip() or resolved
    extra_headers = connection.get("extra_headers") if isinstance(connection.get("extra_headers"), dict) else {}
    route = str(raw.get("route") or "").strip() or _route_from_api_base(api_base, provider_name)

    if backend == "openai_compat":
        if not api_base:
            return None
        url, headers, payload = _build_openai_compat_probe_request(
            provider_name=provider_name,
            provider_model=provider_model,
            api_base=api_base,
            api_key=api_key,
            extra_headers=extra_headers,
            raw=raw,
        )
        return RuntimeProbeRequest(
            url=url,
            headers=headers,
            payload=payload,
            provider=provider_name,
            api_base=api_base,
            route=route,
        )

    if backend == "azure_openai":
        if not api_base:
            return None
        url, headers, payload = _build_azure_openai_probe_request(
            provider_model=provider_model,
            api_base=api_base,
            api_key=api_key,
            raw=raw,
        )
        return RuntimeProbeRequest(
            url=url,
            headers=headers,
            payload=payload,
            provider=provider_name,
            api_base=api_base,
            route=route,
        )

    if backend == "anthropic":
        api_base = api_base or _ANTHROPIC_DEFAULT_API_BASE
        url, headers, payload = _build_anthropic_probe_request(
            provider_model=provider_model,
            api_base=api_base,
            api_key=api_key,
            extra_headers=extra_headers,
        )
        return RuntimeProbeRequest(
            url=url,
            headers=headers,
            payload=payload,
            provider=provider_name,
            api_base=api_base,
            route=route,
        )

    if backend == "github_copilot":
        api_base = api_base or _GITHUB_COPILOT_DEFAULT_API_BASE
        url, headers, payload = _build_github_copilot_probe_request(
            provider_model=provider_model,
            api_base=api_base,
            extra_headers=extra_headers,
        )
        return RuntimeProbeRequest(
            url=url,
            headers=headers,
            payload=payload,
            provider=provider_name,
            api_base=api_base,
            route=route,
        )

    return None



def _resolve_runtime_probe_target(
    workspace: Path,
    *,
    ref: str,
) -> tuple[str, dict[str, Any]] | None:
    registry = _load_json(_registry_path_for_workspace(workspace))
    resolved = _resolve_registry_model_ref(registry, ref)
    if resolved:
        models = registry.get("models") if isinstance(registry, dict) else None
        raw = models.get(resolved, {}) if isinstance(models, dict) else {}
        if isinstance(raw, dict) and raw:
            return resolved, raw
    return None



def _default_probe_request_runner(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
):
    return httpx.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
    )



def _runtime_probe_result(
    request: RuntimeProbeRequest,
    *,
    ok: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "provider": request.provider,
        "api_base": request.api_base,
        "route": request.route,
        "reason": reason,
    }



def run_runtime_quick_provider_probe(
    workspace: Path,
    *,
    ref: str,
    request_runner: Any | None = None,
) -> dict[str, Any] | None:
    target = _resolve_runtime_probe_target(workspace, ref=ref)
    if target is None:
        return None
    resolved, raw = target

    request = _build_runtime_probe_request(resolved=resolved, raw=raw)
    if request is None:
        return None

    runner = request_runner or _default_probe_request_runner
    try:
        response = runner(
            url=request.url,
            headers=request.headers,
            payload=request.payload,
            timeout=60.0,
        )
    except Exception as exc:
        return _runtime_probe_result(request, ok=False, reason=f"{type(exc).__name__}: {exc}")

    status_code = int(getattr(response, "status_code", 0) or 0)
    if 200 <= status_code < 300:
        return _runtime_probe_result(request, ok=True, reason="OK")

    return _runtime_probe_result(request, ok=False, reason=_response_error_reason(response))



def _runtime_probe_strategy(
    workspace: Path,
    *,
    ref: str,
) -> tuple[str, str]:
    target = _resolve_runtime_probe_target(workspace, ref=ref)
    if target is None:
        return "workspace_fallback", "no_runtime_target"
    _resolved, raw = target
    backend = _runtime_probe_backend(raw)
    if backend in {"openai_compat", "azure_openai", "anthropic", "github_copilot"}:
        return "runtime", f"runtime_backend:{backend}"
    if backend in _WORKSPACE_FALLBACK_BACKENDS:
        return "workspace_fallback", f"legacy_workspace_fallback:{backend}"
    return "unsupported", f"unsupported_runtime_backend:{backend}"



def _probe_runner_for_strategy(strategy: str):
    if strategy == "runtime":
        return run_runtime_quick_provider_probe
    if strategy == "workspace_fallback":
        return run_legacy_workspace_provider_probe
    return None



def _unsupported_probe_result(*, strategy: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "provider": "",
        "api_base": "",
        "route": "",
        "reason": reason,
        "strategy": strategy,
    }



def _annotate_probe_result(
    probe: dict[str, Any] | None,
    *,
    strategy: str,
    reason: str,
) -> dict[str, Any] | None:
    if not isinstance(probe, dict):
        return None
    result = dict(probe)
    result.setdefault("strategy", strategy)
    result.setdefault("reason", reason if not result.get("reason") else result.get("reason"))
    return result



def run_default_provider_probe(workspace: Path, *, ref: str) -> dict[str, Any] | None:
    strategy, reason = _runtime_probe_strategy(workspace, ref=ref)
    runner = _probe_runner_for_strategy(strategy)
    if runner is None:
        return _unsupported_probe_result(strategy=strategy, reason=reason)
    return _annotate_probe_result(
        runner(workspace, ref=ref),
        strategy=strategy,
        reason=reason,
    )



def run_legacy_workspace_provider_probe(workspace: Path, *, ref: str) -> dict[str, Any] | None:
    """Legacy compatibility probe via workspace scripts/model_runtime.py.

    This is intentionally not the primary runtime path.
    Prefer registry-backed runtime-native probing whenever the runtime can resolve
    a real model record. This fallback only exists to preserve old workspace-local
    compatibility paths such as `openai_codex` and truly unresolved legacy refs.
    """
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
            config_path=_config_path_for_workspace(workspace),
            registry_path=_registry_path_for_workspace(workspace),
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



def run_workspace_quick_provider_probe(workspace: Path, *, ref: str) -> dict[str, Any] | None:
    """Compatibility alias for legacy callers.

    New code should call `run_legacy_workspace_provider_probe()` directly so the
    compatibility-only status stays obvious.
    """
    return run_legacy_workspace_provider_probe(workspace, ref=ref)



def probe_due_provider_routes(
    *,
    workspace: Path,
    routes: list[str] | tuple[str, ...] | None = None,
    now: str | None = None,
    probe_runner: Any | None = None,
) -> list[dict[str, Any]]:
    registry = _load_json(_registry_path_for_workspace(workspace))
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



def _probe_route_status_result(
    *,
    status: str,
    reason: str,
    route: str,
    ref: str | None = None,
    probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "route": route,
    }
    if ref:
        result["ref"] = ref
    if isinstance(probe, dict):
        result["probe"] = probe
    return result



def probe_provider_route_status(
    *,
    workspace: Path,
    route: str,
    now: str | None = None,
    probe_runner: Any | None = None,
) -> dict[str, Any]:
    registry = _load_json(_registry_path_for_workspace(workspace))
    probe_interval_seconds = _provider_status_probe_interval_seconds(registry)
    route_clean = str(route or "").strip()
    last_updated_at = _provider_status_last_updated_at(registry, route_clean)
    if not _is_probe_due(last_updated_at, now=now, probe_interval_seconds=probe_interval_seconds):
        return _probe_route_status_result(status="skipped", reason="not_due", route=route_clean)
    ref = _probe_ref_for_route(registry, route_clean)
    if not ref:
        return _probe_route_status_result(status="skipped", reason="no_probe_ref", route=route_clean)
    runner = probe_runner or run_default_provider_probe
    probe = runner(workspace, ref=ref)
    applied_route = apply_provider_probe_result(
        workspace=workspace,
        probe=probe,
        updated_at=now,
    )
    return _probe_route_status_result(
        status="updated" if applied_route else "skipped",
        reason="applied" if applied_route else "unknown_route",
        route=applied_route or route_clean,
        ref=ref,
        probe=probe if isinstance(probe, dict) else None,
    )



def refresh_provider_in_manager(
    manager: SubagentResourceManager,
    *,
    route: str,
) -> None:
    _apply_route_status(
        manager.route_policies,
        route=route,
        availability="available",
        reason="",
    )



def apply_provider_failure_to_manager(
    manager: SubagentResourceManager,
    *,
    route: str,
    error_text: str | None,
) -> ProviderFailureStatus | None:
    if not str(route or "").strip():
        return None
    status = classify_provider_failure(error_text)
    _apply_route_status(
        manager.route_policies,
        route=route,
        availability=status.availability,
        reason=status.reason,
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
    registry = _load_json(_registry_path_for_workspace(workspace))
    if not isinstance(registry, dict):
        registry = {}
    registry.setdefault("models", {})
    _config = _load_json(_config_path_for_workspace(workspace))
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

    persisted_status = registry.get("provider_status") if isinstance(registry, dict) else None
    if isinstance(persisted_status, dict):
        for route, status in persisted_status.items():
            route_clean = str(route).strip()
            if route_clean not in route_policies:
                continue
            if _status_is_stale(status, now=now, transient_ttl_seconds=transient_ttl_seconds):
                continue
            availability, reason = _normalize_status(status)
            _apply_route_status(
                route_policies,
                route=route_clean,
                availability=availability,
                reason=reason,
            )

    for route, status in (provider_status or {}).items():
        route_clean = str(route).strip()
        if route_clean not in route_policies:
            continue
        availability, reason = _normalize_status(status)
        _apply_route_status(
            route_policies,
            route=route_clean,
            availability=availability,
            reason=reason,
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
    registry_tier_policies = registry.get("tier_policies") if isinstance(registry, dict) else None
    if isinstance(registry_tier_policies, dict):
        for tier, override in registry_tier_policies.items():
            tier_clean = str(tier).strip()
            if not tier_clean:
                continue
            base = tier_policies.get(tier_clean, TierPolicy())
            tier_policies[tier_clean] = _merge_tier_policy(base, override if isinstance(override, dict) else None)

    subagent_defaults = registry.get("subagent_defaults", {}) if isinstance(registry, dict) else {}
    manager_model = str((subagent_defaults or {}).get("model") or "").strip()
    if not manager_model:
        manager_model = str(fallback_model or "").strip()
    manager_tier = _infer_manager_tier_from_ref(manager_model)
    task_budget = int((subagent_defaults or {}).get("task_budget") or 0)
    level_limit = int((subagent_defaults or {}).get("level_limit") or 0)

    return SubagentResourceManager(
        registry=registry,
        tier_policies=tier_policies,
        route_policies=route_policies,
        route_states={route: RouteState() for route in route_policies},
        defaults={
            "manager_model": manager_model,
            "manager_tier": manager_tier,
            "task_budget": task_budget,
            "level_limit": level_limit,
        },
    )
