# Runtime Subagent Probe Boundary Note

## Decision

Runtime-native provider probing is the primary path.

- model resolution truth comes from `model_registry.json`
- route/provider metadata used by probing should come from registry-backed runtime records
- workspace script probing is compatibility-only

## Primary Path

Use `run_runtime_quick_provider_probe()` when the runtime can resolve a real model record.

Supported runtime-native probe backends currently include:

- `openai_compat`
- `azure_openai`
- `anthropic`
- `github_copilot`

## Compatibility Path

`run_legacy_workspace_provider_probe()` is a legacy compatibility shim around
workspace `scripts/model_runtime.py`.

It exists for two cases only:

1. explicitly allowed legacy workspace fallbacks such as `openai_codex`
2. truly unresolved refs where runtime-native model resolution cannot produce a registry-backed target

New probe logic should not be added to this compatibility layer.

## Why

Allowing workspace config/scripts to silently define runtime probe truth blurs the
boundary between:

- registry truth
- active config projection
- old workspace-local compatibility behavior

That makes provider debugging and future runtime cleanup much harder.

## Rule

If runtime can name the model from registry, probe in runtime.
If it cannot, fall back only through the explicit legacy compatibility path.
