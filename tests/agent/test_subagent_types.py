from __future__ import annotations

import pytest



def test_get_subagent_type_spec_returns_worker_defaults():
    from nanobot.agent.subagent_types import get_subagent_type_spec

    spec = get_subagent_type_spec("worker")

    assert spec.name == "worker"
    assert spec.tier == "standard"
    assert spec.family == "gpt-5.4-mini"
    assert spec.effort == "xhigh"



def test_get_subagent_type_spec_returns_explorer_defaults():
    from nanobot.agent.subagent_types import get_subagent_type_spec

    spec = get_subagent_type_spec("explorer")

    assert spec.name == "explorer"
    assert spec.tier == "standard"
    assert spec.family == "gpt-5.4-mini"
    assert spec.effort == "medium"



def test_get_subagent_type_spec_rejects_unknown_type():
    from nanobot.agent.subagent_types import get_subagent_type_spec

    with pytest.raises(ValueError):
        get_subagent_type_spec("reviewer")
