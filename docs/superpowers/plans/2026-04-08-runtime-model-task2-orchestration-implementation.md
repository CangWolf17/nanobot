# Runtime Model Task 2 Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Task 1's runtime-native registry into runtime-side `model_state` / `route_state` orchestration so subagent resource decisions and leased-provider rebuilds stop depending only on the legacy raw workspace registry shape.

**Architecture:** Keep the workspace `model_runtime.py` / `models_cmd.py` scripts as the current `/model` command surface, but make the runtime repo prefer v2 registry + `model_state.json` when building manager defaults, route policies, and lease providers. Preserve a narrow legacy fallback path so the runtime does not break while the workspace-side registry/scripts are still in transition.

**Tech Stack:** Python, pytest, runtime `nanobot.model_registry` package, existing subagent resource manager, workspace script bridge

---

### Task 1: Read `model_state.json` Into Manager Defaults

**Files:**
- Modify: `nanobot/agent/subagent_resources.py`
- Test: `tests/agent/test_subagent_resources.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_manager_snapshot_prefers_current_model_from_model_state(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_state.json").write_text(
        json.dumps(
            {
                "current_model": "standard-gpt-5.4-xhigh-tokenx",
                "last_known_good_model": "standard-gpt-5.4-high-aizhiwen-top",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)
    request = manager.default_request()

    assert request.manager_model == "standard-gpt-5.4-xhigh-tokenx"
    assert request.manager_tier == "standard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_subagent_resources.py -q -k 'prefers_current_model_from_model_state'`
Expected: FAIL because `build_manager_from_workspace_snapshot()` currently only seeds `manager_model` from `subagent_defaults.model` / `fallback_model`.

- [ ] **Step 3: Write minimal implementation**

```python
def _load_model_state(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    return data if isinstance(data, dict) else {}


def _manager_model_from_state(state: dict[str, Any], *, fallback_model: str | None) -> str:
    for key in ("current_model", "last_known_good_model", "last_effective_model"):
        value = str(state.get(key) or "").strip()
        if value:
            return value
    return str(fallback_model or "").strip()
```

```python
state = _load_model_state(workspace / "model_state.json")
manager_model = _manager_model_from_state(state, fallback_model=fallback_model)
if not manager_model:
    manager_model = str((subagent_defaults or {}).get("model") or "").strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agent/test_subagent_resources.py -q -k 'prefers_current_model_from_model_state'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/agent/subagent_resources.py tests/agent/test_subagent_resources.py
git commit -m "Prefer model state when seeding manager defaults"
```

### Task 2: Translate V2 Registry Routes And Models Into Runtime Route State

**Files:**
- Modify: `nanobot/agent/subagent_resources.py`
- Test: `tests/agent/test_subagent_resources.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_manager_snapshot_reads_v2_route_names_and_profile_defaults(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    registry = {
        "version": 2,
        "profile_defaults": {
            "chat": {"ref": "standard-gpt-5.4-high-tokenx"},
            "archive": {"ref": "archive-gpt-4.1-mini"},
        },
        "routes": {
            "tokenx": {"config_provider_ref": "custom", "adapter": "openai_compat"},
            "responses": {"config_provider_ref": "openai", "adapter": "openai_responses"},
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
                "capabilities": {"tool_calls": True},
            },
            "archive-gpt-4.1-mini": {
                "family": "gpt-4.1",
                "tier": "lite",
                "effort": "high",
                "route_ref": "responses",
                "provider_model": "gpt-4.1-mini",
                "enabled": True,
                "template": False,
                "capabilities": {"archive": True, "tool_calls": True},
            },
        },
    }

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)

    assert "tokenx" in manager.route_policies
    assert "responses" in manager.route_policies
    assert manager.acquire(manager.default_request(tier="lite")).lease.route == "responses"
```

```python
def test_build_manager_snapshot_keeps_legacy_registry_path_working(tmp_path):
    from nanobot.agent.subagent_resources import build_manager_from_workspace_snapshot

    (tmp_path / "config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "model_registry.json").write_text(
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manager = build_manager_from_workspace_snapshot(workspace=tmp_path)

    assert manager.acquire(manager.default_request()).status == "granted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_subagent_resources.py -q -k 'reads_v2_route_names_and_profile_defaults or keeps_legacy_registry_path_working'`
Expected: FAIL on the v2 test because the current snapshot builder only understands legacy `models[*].route`.

- [ ] **Step 3: Write minimal implementation**

```python
def _runtime_models_from_registry(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if int(registry.get("version") or 0) >= 2:
        return {
            model_id: {
                "tier": raw.get("tier"),
                "family": raw.get("family"),
                "effort": raw.get("effort"),
                "route": raw.get("route_ref"),
                "provider_model": raw.get("provider_model"),
                "enabled": raw.get("enabled", True),
                "template": raw.get("template", False),
                "aliases": raw.get("aliases") or [],
            }
            for model_id, raw in (registry.get("models") or {}).items()
            if isinstance(raw, dict)
        }
    return registry.get("models") if isinstance(registry.get("models"), dict) else {}
```

```python
runtime_models = _runtime_models_from_registry(registry)
route_names = {
    str(raw.get("route") or "").strip()
    for raw in runtime_models.values()
    if isinstance(raw, dict) and str(raw.get("route") or "").strip()
}
route_policies = {route: route_policies.get(route, RoutePolicy()) for route in route_names}
registry["models"] = runtime_models
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_subagent_resources.py -q -k 'reads_v2_route_names_and_profile_defaults or keeps_legacy_registry_path_working'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/agent/subagent_resources.py tests/agent/test_subagent_resources.py
git commit -m "Teach subagent snapshot builder the v2 model registry"
```

### Task 3: Rebuild Lease Providers Through `ResolvedModelSpec`

**Files:**
- Modify: `nanobot/agent/subagent.py`
- Test: `tests/agent/test_task_cancel.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_run_subagent_rebuilds_provider_from_v2_registry_lease(self, monkeypatch, tmp_path):
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.subagent_resources import SubagentLease
    from nanobot.bus.queue import MessageBus

    registry = {
        "version": 2,
        "profile_defaults": {"chat": {"ref": "standard-gpt-5.4-high-tokenx"}},
        "routes": {
            "tokenx": {
                "config_provider_ref": "custom",
                "adapter": "openai_compat",
                "api_base_override": "https://tokenx24.com/v1",
            }
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
                "capabilities": {"tool_calls": True},
            }
        },
    }

    (tmp_path / "model_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {"agents": {"defaults": {"model": "gpt-5.4"}}, "providers": {"custom": {"apiKey": "k-tokenx"}}},
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    bus = MessageBus()
    parent_provider = MagicMock()
    parent_provider.get_default_model.return_value = "parent-model"
    mgr = SubagentManager(provider=parent_provider, workspace=tmp_path, bus=bus)

    provider, model = mgr._build_provider_for_lease(
        SubagentLease(model_id="standard-gpt-5.4-high-tokenx", tier="standard", route="tokenx", effort="high")
    )

    assert provider.get_default_model() == "gpt-5.4"
    assert model == "gpt-5.4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_task_cancel.py -q -k 'rebuilds_provider_from_v2_registry_lease'`
Expected: FAIL because `_build_provider_for_lease()` currently only understands legacy `connection` / `provider` / `agent` fields.

- [ ] **Step 3: Write minimal implementation**

```python
def _build_provider_for_lease(self, lease: SubagentLease) -> tuple[LLMProvider, str]:
    config = load_config(self.workspace / "config.json")
    store = ModelRegistryStore(self.workspace / "model_registry.json")
    registry = store.load()
    spec = RegistryResolver(registry).resolve_ref(lease.model_id, profile_hint="chat")
    provider, provider_model = build_provider_from_spec(spec, config)
    return provider, provider_model
```

```python
try:
    return _build_provider_from_v2_registry(...)
except (FileNotFoundError, ModelRegistryError, ModelRegistrySemanticError, ValueError):
    return _build_provider_from_legacy_record(...)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agent/test_task_cancel.py -q -k 'rebuilds_provider_from_v2_registry_lease'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/agent/subagent.py tests/agent/test_task_cancel.py
git commit -m "Build leased subagent providers from resolved model specs"
```

### Task 4: Final Verification For Task 2 Slice

**Files:**
- Verify only

- [ ] **Step 1: Run focused runtime orchestration tests**

Run: `uv run pytest tests/agent/test_subagent_resources.py tests/agent/test_task_cancel.py tests/command/test_fastlane.py -q`
Expected: PASS

- [ ] **Step 2: Run lint on touched runtime files**

Run: `uv run ruff check nanobot/agent/subagent_resources.py nanobot/agent/subagent.py tests/agent/test_subagent_resources.py tests/agent/test_task_cancel.py tests/command/test_fastlane.py`
Expected: PASS

- [ ] **Step 3: Commit checkpoint**

```bash
git add nanobot/agent/subagent_resources.py nanobot/agent/subagent.py tests/agent/test_subagent_resources.py tests/agent/test_task_cancel.py tests/command/test_fastlane.py
git commit -m "Wire runtime model state into subagent orchestration"
```
