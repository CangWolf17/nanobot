from nanobot.harness.models import HarnessExecutionPolicy, HarnessRecord, HarnessRuntimeState, HarnessSnapshot


def test_snapshot_to_dict_includes_policy_and_runtime_defaults() -> None:
    snapshot = HarnessSnapshot(
        active_harness_id="har_0001",
        records={
            "har_0001": HarnessRecord(
                id="har_0001",
                title="Legacy harness",
                summary="keep canonical state here",
            )
        },
    )

    payload = snapshot.to_dict()

    assert payload["active_harness_id"] == "har_0001"
    assert payload["records"]["har_0001"]["execution_policy"] == HarnessExecutionPolicy().__dict__
    assert payload["records"]["har_0001"]["runtime_state"] == HarnessRuntimeState().__dict__
