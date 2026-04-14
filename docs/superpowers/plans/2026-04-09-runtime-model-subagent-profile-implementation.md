# Runtime Model Subagent Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make runtime subagent selection and leased-provider rebuilds honor the v2 `subagent` profile end-to-end instead of falling back to chat/default legacy behavior.

**Architecture:** Keep legacy registry fallback working, but make the runtime treat `profile_defaults.subagent` as the authoritative v2 default for subagent work. Thread explicit subagent-profile intent through `SubagentRequest`, resource-manager candidate selection, and `_build_provider_for_lease()` so v2 leases resolve/build through `ResolvedModelSpec` with `profile_hint="subagent"` before any compatibility fallback.

**Tech Stack:** Python, pytest, `nanobot.agent.subagent_resources`, `nanobot.agent.subagent`, `nanobot.model_registry`

---

### Task 1: Make Manager Snapshots Profile-Aware For Subagent Work

**Files:**
- Modify: `nanobot/agent/subagent_resources.py`
- Test: `tests/agent/test_subagent_resources.py`

- [ ] **Step 1: Write the failing tests**

```python
def _v2_subagent_registry() -> dict:
    return {
        "version": 2,
        "profile_defaults": {
            "chat": {"ref": "standard-gpt-5.4-high-tokenx"},
            "subagent": {"ref": "subagent-gpt-5.4-high-tokenx"},
        },
        "routes": {
            "tokenx": {"config_provider_ref": "custom", "adapter": "openai_compat"},
        },
        "models": {
            "standard-gpt-5.4-high-tokenx": {
                "family": "gpt-5.4",
                "tier": "standard",
                "effort": "high",
                "route_ref": "tokenx",
                "provider_model": "gpt-5.4",
                "enabled": True,
                "template": False,
                "capabilities": {"chat": True, "tool_calls": True},
            },
            "subagent-gpt-5.4-high-tokenx": {
                "family": "gpt-5.4",
                "tier": "standard",
                "effort": "high",
                "route_ref": "tokenx",
                "provider_model": "gpt-5.4",
                "enabled": True,
                "template": False,
                "capabilities": {"subagent": True, "tool_calls": True},
            },
        },
    }


def test_build_manager_snapshot_prefers_v2_subagent_profile_over_current_chat_model(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_v2_subagent_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_state.json").write_text(
        json.dumps({"current_model": "standard-gpt-5.4-high-tokenx"}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    request = manager.default_request()
    decision = manager.acquire(request)

    assert request.profile == "subagent"
    assert request.manager_model == "subagent-gpt-5.4-high-tokenx"
    assert request.manager_tier == "standard"
    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "subagent-gpt-5.4-high-tokenx"


def test_subagent_profile_filters_standard_tier_candidates_to_subagent_capable_models(tmp_path):
    from nanobot.agent.subagent_resources import SubagentRequest, build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_v2_subagent_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    decision = manager.acquire(SubagentRequest(tier="standard", profile="subagent"))

    assert decision.status == "granted"
    assert decision.lease is not None
    assert decision.lease.model_id == "subagent-gpt-5.4-high-tokenx"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_subagent_resources.py -q -k 'prefers_v2_subagent_profile_over_current_chat_model or subagent_profile_filters_standard_tier_candidates'`
Expected: FAIL because `build_manager_from_workspace_snapshot()` still seeds manager defaults from `model_state` / legacy defaults, `SubagentRequest` has no `profile`, `_runtime_models_from_registry()` drops `capabilities`, and tier selection does not filter for subagent-capable models.

- [ ] **Step 3: Write the minimal implementation**

```python
@dataclass(frozen=True)
class SubagentRequest:
    model: str | None = None
    tier: str | None = None
    profile: str | None = None
    harness_tier: str | None = None
    harness_model: str | None = None
    manager_tier: str | None = None
    manager_model: str | None = None
    task_kind: str | None = None
    session_key: str | None = None
    harness_id: str | None = None


def _runtime_models_from_registry(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    models = registry.get("models") if isinstance(registry, dict) else None
    if not isinstance(models, dict):
        return {}
    if int(registry.get("version") or 0) < 2:
        return {model_id: raw for model_id, raw in models.items() if isinstance(raw, dict)}

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
            "capabilities": dict(raw.get("capabilities") or {}),
        }
    return runtime_models


def _resolve_v2_profile_default_ref(
    workspace_registry: dict[str, Any],
    runtime_models: dict[str, dict[str, Any]],
    *,
    profile: str,
) -> str:
    if int(workspace_registry.get("version") or 0) < 2:
        return ""
    try:
        from nanobot.model_registry.resolver import RegistryResolver
        from nanobot.model_registry.schema import ModelRegistry

        spec = RegistryResolver(ModelRegistry.from_dict(workspace_registry)).resolve_profile(profile)
    except Exception:
        return ""
    return _resolve_selectable_runtime_model_ref(runtime_models, spec.model_id)


def _request_requires_capability(request: SubagentRequest) -> str:
    profile = str(request.profile or "").strip().lower()
    if profile == "subagent":
        return "subagent"
    return ""


def _tier_candidates(self, tier: str, *, request: SubagentRequest | None = None) -> list[str]:
    policy = self.tier_policies.get(tier, TierPolicy())
    models = self.registry.get("models", {}) if isinstance(self.registry, dict) else {}
    required_capability = _request_requires_capability(request or SubagentRequest())
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
            if required_capability and not bool((raw.get("capabilities") or {}).get(required_capability)):
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
        if required_capability and not bool((raw.get("capabilities") or {}).get(required_capability)):
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
```

```python
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
        tier_candidates = self._tier_candidates(requested_tier, request=request)
        if tier_candidates:
            return tier_candidates

    default_model = self._clean(request.harness_model) or self._clean(request.manager_model)
    if not default_model:
        return []
    resolved = self.resolve_model_ref(default_model)
    return [resolved] if resolved else [default_model]


def default_request(self, *, tier: str | None = None, model: str | None = None) -> SubagentRequest:
    return SubagentRequest(
        model=self._clean(model),
        tier=self._clean(tier),
        profile=self.defaults.get("manager_profile") or "subagent",
        manager_tier=self.defaults.get("manager_tier") or "",
        manager_model=self.defaults.get("manager_model") or "",
    )
```

```python
manager_model = _resolve_v2_profile_default_ref(
    workspace_registry,
    runtime_models,
    profile="subagent",
)
if not manager_model:
    state_model = _manager_model_from_state(state)
    if state_model:
        manager_model = _resolve_selectable_runtime_model_ref(runtime_models, state_model)
if not manager_model:
    manager_model = str((subagent_defaults or {}).get("model") or "").strip()
if not manager_model:
    manager_model = str(fallback_model or "").strip()
manager_tier = _infer_manager_tier_from_ref(manager_model)
manager_profile = "subagent" if manager_model and int(workspace_registry.get("version") or 0) >= 2 else ""

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
        "manager_profile": manager_profile,
    },
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_subagent_resources.py -q -k 'prefers_v2_subagent_profile_over_current_chat_model or subagent_profile_filters_standard_tier_candidates'`
Expected: PASS

- [ ] **Step 5: Checkpoint the diff without committing**

Run: `git diff -- nanobot/agent/subagent_resources.py tests/agent/test_subagent_resources.py`
Expected: only the new profile-aware request/default/candidate-selection changes appear. Do not create a commit unless the user explicitly asks.

### Task 2: Resolve And Rebuild Leases As `subagent` Profile

**Files:**
- Modify: `nanobot/agent/subagent.py`
- Test: `tests/agent/test_task_cancel.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_subagent_request_marks_profile_as_subagent(self, tmp_path):
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "gpt-5.4"
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

    request = mgr._resolve_subagent_request(
        task="do task",
        label="bg",
        origin={"channel": "feishu", "chat_id": "c1", "metadata": {}},
    )

    assert request.profile == "subagent"


def test_build_provider_for_lease_uses_subagent_profile_hint_for_v2_registry(self, tmp_path):
    import json

    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.subagent_resources import SubagentLease
    from nanobot.bus.queue import MessageBus

    registry = {
        "version": 2,
        "profile_defaults": {"subagent": {"ref": "subagent-gpt-5.4-high-tokenx"}},
        "routes": {
            "tokenx": {
                "config_provider_ref": "custom",
                "adapter": "openai_compat",
                "api_base_override": "https://tokenx24.com/v1",
            }
        },
        "models": {
            "subagent-gpt-5.4-high-tokenx": {
                "family": "gpt-5.4",
                "tier": "standard",
                "effort": "high",
                "route_ref": "tokenx",
                "provider_model": "gpt-5.4",
                "enabled": True,
                "template": False,
                "capabilities": {"subagent": True, "tool_calls": True},
            }
        },
    }
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": "gpt-5.4"}},
                "providers": {"custom": {"apiKey": "k-tokenx"}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    bus = MessageBus()
    parent_provider = MagicMock()
    parent_provider.get_default_model.return_value = "parent-model"
    mgr = SubagentManager(provider=parent_provider, workspace=tmp_path, bus=bus)

    provider, model = mgr._build_provider_for_lease(
        SubagentLease(
            model_id="subagent-gpt-5.4-high-tokenx",
            tier="standard",
            route="tokenx",
            effort="high",
        )
    )

    assert provider.get_default_model() == "gpt-5.4"
    assert model == "gpt-5.4"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_task_cancel.py -q -k 'marks_profile_as_subagent or uses_subagent_profile_hint_for_v2_registry'`
Expected: FAIL because `_resolve_subagent_request()` does not set `profile`, and `_build_provider_for_lease()` still resolves v2 refs with `profile_hint="chat"`.

- [ ] **Step 3: Write the minimal implementation**

```python
def _resolve_subagent_request(
    self,
    *,
    task: str,
    label: str | None,
    tier: str | None = None,
    model: str | None = None,
    session_key: str | None = None,
    origin: dict[str, Any],
) -> SubagentRequest:
    metadata = origin.get("metadata") if isinstance(origin, dict) else {}
    runtime_meta = metadata.get("workspace_runtime") if isinstance(metadata, dict) else None
    active_harness = runtime_meta.get("active_harness") if isinstance(runtime_meta, dict) else None
    harness_model = ""
    harness_tier = ""
    harness_id = ""
    if isinstance(active_harness, dict):
        harness_model = str(active_harness.get("subagent_model") or "").strip()
        harness_tier = str(active_harness.get("subagent_tier") or "").strip()
        harness_id = str(active_harness.get("id") or "").strip()
    manager_request = (
        self.resource_manager.default_request()
        if self.resource_manager is not None
        else SubagentRequest(manager_model=self.model, profile="subagent")
    )
    return SubagentRequest(
        model=(model or "").strip() or None,
        tier=(tier or "").strip() or None,
        profile="subagent",
        harness_tier=harness_tier or None,
        harness_model=harness_model or None,
        manager_tier=manager_request.manager_tier,
        manager_model=manager_request.manager_model or self.model,
        session_key=(session_key or "").strip() or None,
        harness_id=harness_id or None,
    )
```

```python
def _build_provider_for_lease(self, lease: SubagentLease) -> tuple[LLMProvider, str]:
    from nanobot.config.loader import load_config
    from nanobot.model_registry.provider_factory import build_provider_from_spec
    from nanobot.model_registry.resolver import (
        ModelRegistryError,
        ModelRegistrySemanticError,
        RegistryResolver,
    )
    from nanobot.model_registry.store import ModelRegistryStore
    from nanobot.nanobot import _make_provider

    try:
        registry = ModelRegistryStore(self.workspace / "model_registry.json").load()
        spec = RegistryResolver(registry).resolve_ref(lease.model_id, profile_hint="subagent")
        config = load_config(self.workspace / "config.json")
        return build_provider_from_spec(spec, config)
    except (FileNotFoundError, ModelRegistryError, ModelRegistrySemanticError, ValueError):
        pass

    if lease.model_id == self.model:
        return self.provider, self.model

    # Keep the existing workspace-snapshot and legacy-registry fallback ladder below this block unchanged.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_task_cancel.py -q -k 'marks_profile_as_subagent or uses_subagent_profile_hint_for_v2_registry'`
Expected: PASS

- [ ] **Step 5: Checkpoint the diff without committing**

Run: `git diff -- nanobot/agent/subagent.py tests/agent/test_task_cancel.py`
Expected: only the subagent-profile request threading and v2 lease-resolution changes appear. Do not create a commit unless the user explicitly asks.

### Task 3: Verify The Subagent-Profile Slice

**Files:**
- Verify: `tests/agent/test_subagent_resources.py`
- Verify: `tests/agent/test_task_cancel.py`
- Verify: `tests/model_registry/test_resolver.py`

- [ ] **Step 1: Run the focused runtime-model/subagent tests**

Run: `uv run pytest tests/agent/test_subagent_resources.py tests/agent/test_task_cancel.py tests/model_registry/test_resolver.py -q`
Expected: PASS

- [ ] **Step 2: Run the repo baseline checks**

Run: `uv run pytest -q && uv run ruff check`
Expected: the full suite passes and `ruff` reports `All checks passed!`

- [ ] **Step 3: Update the handoff note if verification is green**

Add the new executed state to:

```text
/home/admin/.nanobot/workspace/docs/handoffs/2026-04-09-runtime-model-refactor-review-closure-handoff.md
```

Record:
- the subagent-profile slice that was implemented
- the fresh focused/full verification output
- whether any legacy fallback remained intentional

- [ ] **Step 4: Leave git history untouched unless the user explicitly asks for a commit**

Run: `git status --short`
Expected: the working tree reflects only the intended subagent-profile slice plus any unrelated pre-existing changes.
