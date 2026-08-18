"""Dataset synchronization: identity matching, reuse, and never deleting."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.datasets import (
    DatasetSyncError,
    example_payload,
    synchronize_dataset,
)
from tests.evaluation_fakes import FakeDataset, FakeLangSmithClient


def sync(client, *, agent_name="planner", tier="controlled", cases=None,
         secrets=()):
    return synchronize_dataset(
        client,
        agent_name=agent_name,
        tier=tier,
        dataset_version=1,
        rubric_version=1,
        cases=cases if cases is not None else cases_for(agent_name, tier),
        secrets=secrets,
    )


def test_a_missing_dataset_is_created_with_the_versioned_name() -> None:
    client = FakeLangSmithClient()

    report = sync(client)

    assert report.created is True
    assert report.dataset_name == "deep-research-planner-controlled-v1"
    assert client.created_datasets == [
        "deep-research-planner-controlled-v1"
    ]
    assert len(client.created_examples) == 3


def test_an_unchanged_dataset_is_reused_and_nothing_is_written() -> None:
    client = FakeLangSmithClient()
    sync(client)
    client.created_datasets.clear()
    client.created_examples.clear()

    report = sync(client)

    assert report.created is False
    assert client.created_datasets == []
    assert client.created_examples == []
    assert set(report.reused_case_ids) == {
        case.case_id for case in cases_for("planner", "controlled")
    }


def test_a_new_case_version_updates_that_example_only() -> None:
    client = FakeLangSmithClient()
    sync(client)
    client.created_examples.clear()

    cases = list(cases_for("planner", "controlled"))
    cases[0] = cases[0].model_copy(
        update={"version": 2, "purpose": "A revised purpose."}
    )
    report = sync(client, cases=cases)

    assert report.updated_case_ids == (cases[0].case_id,)
    assert len(client.updated_examples) == 1
    assert len(report.reused_case_ids) == 2


def test_synchronization_never_deletes_a_remote_example() -> None:
    """A remote example the local registry no longer knows is left alone."""
    client = FakeLangSmithClient()
    sync(client)

    report = sync(client, cases=list(cases_for("planner", "controlled"))[:2])

    assert report.added_case_ids == ()
    assert len(client.list_examples(
        dataset_name="deep-research-planner-controlled-v1"
    )) == 3


def test_deleting_anything_would_raise_in_the_fake() -> None:
    """Guards the guard: the fake fails loudly if deletion is ever added."""
    client = FakeLangSmithClient()

    with pytest.raises(AssertionError):
        client.delete_example(example_id="x")


def test_a_duplicate_case_id_fails_before_any_remote_call() -> None:
    client = FakeLangSmithClient()
    cases = list(cases_for("planner", "controlled"))
    cases.append(cases[0])

    with pytest.raises(DatasetSyncError) as caught:
        sync(client, cases=cases)

    assert caught.value.reason == "invalid_registry"
    assert client.created_datasets == []


def test_conflicting_versions_of_one_case_id_fail_before_any_remote_call(
) -> None:
    client = FakeLangSmithClient()
    cases = list(cases_for("planner", "controlled"))
    cases.append(cases[0].model_copy(update={"version": 2}))

    with pytest.raises(DatasetSyncError) as caught:
        sync(client, cases=cases)

    assert caught.value.reason == "invalid_registry"
    assert client.created_datasets == []


def test_an_example_carries_the_identity_metadata_evaluators_need() -> None:
    case = cases_for("planner", "controlled")[0]

    payload = example_payload(case, rubric_version=1)

    assert payload["inputs"]["case_id"] == case.case_id
    assert payload["inputs"]["case_version"] == case.version
    assert payload["inputs"]["agent"] == "planner"
    assert payload["inputs"]["tier"] == "controlled"
    assert payload["inputs"]["question"] == case.state.original_question
    assert payload["outputs"]["expectations"]
    assert payload["metadata"]["rubric_version"] == 1
    assert payload["metadata"]["case_registry_version"] >= 1


def test_an_example_carries_no_client_key_or_absolute_local_path() -> None:
    payload = example_payload(cases_for("planner", "controlled")[0],
                              rubric_version=1)
    rendered = repr(payload)

    assert "api_key" not in rendered.lower()
    assert "C:\\\\" not in rendered
    assert "/home/" not in rendered


def test_a_secret_in_an_outgoing_example_aborts_the_sync() -> None:
    client = FakeLangSmithClient()
    case = cases_for("planner", "controlled")[0]
    leaking = case.model_copy(
        update={"purpose": "check sk-abcdefghijklmnop handling"}
    )

    with pytest.raises(DatasetSyncError) as caught:
        sync(client, cases=[leaking], secrets=("sk-abcdefghijklmnop",))

    assert caught.value.reason == "secret_in_dataset"
    assert "sk-abcdefghijklmnop" not in str(caught.value)
    assert client.created_examples == []


def test_a_live_dataset_holds_exactly_one_example() -> None:
    client = FakeLangSmithClient()

    report = sync(client, tier="live")

    assert report.dataset_name == "deep-research-planner-live-v1"
    assert len(client.created_examples) == 1


def test_an_existing_dataset_is_read_not_recreated() -> None:
    client = FakeLangSmithClient(
        datasets=[FakeDataset("deep-research-planner-controlled-v1", "ds1")]
    )

    report = sync(client)

    assert report.created is False
    assert report.dataset_id == "ds1"
    assert client.created_datasets == []


def test_a_client_failure_is_wrapped_as_dataset_unavailable() -> None:
    """Any client exception becomes ``dataset_unavailable``, never leaks."""
    class BrokenClient(FakeLangSmithClient):
        def create_dataset(self, dataset_name, **kwargs):
            raise RuntimeError("network down")

    with pytest.raises(DatasetSyncError) as caught:
        sync(BrokenClient())

    assert caught.value.reason == "dataset_unavailable"


def test_the_secret_error_message_names_the_path_not_the_value() -> None:
    client = FakeLangSmithClient()
    case = cases_for("planner", "controlled")[0]
    leaking = case.model_copy(
        update={"purpose": "check sk-abcdefghijklmnop handling"}
    )

    with pytest.raises(DatasetSyncError) as caught:
        sync(client, cases=[leaking], secrets=("sk-abcdefghijklmnop",))

    assert "inputs.purpose" in str(caught.value)
    assert "sk-abcdefghijklmnop" not in str(caught.value)
