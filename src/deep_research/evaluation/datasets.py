"""LangSmith dataset synchronization for the evaluation harness.

The local case registry (``cases``) is the source of truth. This module
mirrors it into secret-free LangSmith examples keyed by stable case
identity ``(case_id, case_version)``: a missing dataset is created with
the schema version in its name, an unchanged dataset is reused, a
semantic case change bumps its case version and updates only that
example, and synchronization never deletes anything remote.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deep_research.evaluation.cases import (
    CASE_REGISTRY_VERSION,
    CaseRegistryError,
    validate_registry,
)
from deep_research.evaluation.config import (
    SecretLeakError,
    contains_secret,
    dataset_name,
)
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    EvaluationTier,
)
from deep_research.utils.types import JsonValue


class DatasetSyncError(RuntimeError):
    """A dataset synchronization failure, categorized by ``reason``.

    Reasons: ``"invalid_registry"`` (the local registry or case set is
    corrupt), ``"secret_in_dataset"`` (a known secret value would have
    been uploaded), and ``"dataset_unavailable"`` (any client failure).
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or f"dataset synchronization failed: {reason}")
        self.reason = reason

@dataclass(frozen=True, slots=True)
class SyncReport:
    """What one synchronization run did, per case identity."""

    dataset_name: str
    dataset_id: str
    dataset_url: str
    created: bool
    added_case_ids: tuple[str, ...]
    updated_case_ids: tuple[str, ...]
    reused_case_ids: tuple[str, ...]


def example_payload(
    case: EvaluationCase, *, rubric_version: int
) -> dict[str, JsonValue]:
    """The secret-free LangSmith example for one case.

    Built from named fields only — never from ``model_dump()`` of the
    whole case — so nothing that is not explicitly listed here can leak
    into the dataset. Reference expectations live under ``outputs``
    because LangSmith shows that block as the reference and evaluators
    read it there. ``state_summary`` carries counts plus the subtopic
    titles, source URLs, and claim texts: enough to inspect the case in
    the UI without shipping the whole model.
    """
    state = case.state
    return {
        "inputs": {
            "case_id": case.case_id,
            "case_version": case.version,
            "agent": case.agent_name,
            "tier": case.tier,
            "title": case.title,
            "purpose": case.purpose,
            "dependency_scenario": case.dependency_scenario,
            "question": state.original_question,
            "state_summary": {
                "sub_topic_count": len(state.sub_topics),
                "sub_topic_titles": [
                    sub_topic.title for sub_topic in state.sub_topics
                ],
                "finding_count": len(state.raw_findings),
                "source_count": len(state.evaluated_sources),
                "source_urls": [source.url for source in state.evaluated_sources],
                "claim_count": len(state.verified_claims),
                "claim_texts": [claim.text for claim in state.verified_claims],
            },
        },
        "outputs": {
            "expectations": case.expectations.model_dump(mode="json"),
            "judge_rubric": case.judge_rubric.model_dump(mode="json"),
        },
        "metadata": {
            "agent": case.agent_name,
            "tier": case.tier,
            "case_id": case.case_id,
            "case_version": case.version,
            "rubric_version": rubric_version,
            "case_registry_version": CASE_REGISTRY_VERSION,
        },
    }


def _validate_sync_case_identities(cases: Sequence[EvaluationCase]) -> None:
    """Reject duplicate identities or one id at conflicting versions.

    The per-agent case counts are a property of the whole registry and are
    enforced by ``validate_registry()``; a synchronization may legitimately
    cover a subset of the cases of one agent, so only the two identity
    rules apply to the subset itself.
    """
    seen: dict[tuple[str, int], int] = {}
    versions: dict[str, set[int]] = {}
    for case in cases:
        seen[case.identity] = seen.get(case.identity, 0) + 1
        versions.setdefault(case.case_id, set()).add(case.version)
    duplicates = sorted(key[0] for key, count in seen.items() if count > 1)
    if duplicates:
        raise CaseRegistryError(
            f"duplicate case identities: {', '.join(duplicates)}"
        )
    conflicting = sorted(
        case_id for case_id, found in versions.items() if len(found) > 1
    )
    if conflicting:
        raise CaseRegistryError(
            f"conflicting versions for case ids: {', '.join(conflicting)}"
        )


def _validate_cases(cases: Sequence[EvaluationCase]) -> None:
    """The registry is trusted only when both checks pass, before any call."""
    try:
        validate_registry()
        _validate_sync_case_identities(cases)
    except CaseRegistryError as error:
        raise DatasetSyncError("invalid_registry") from error


def synchronize_dataset(
    client: Any,
    *,
    agent_name: AgentName,
    tier: EvaluationTier,
    dataset_version: int,
    rubric_version: int,
    cases: Sequence[EvaluationCase],
    secrets: Sequence[str],
) -> SyncReport:
    """Mirror the secret-free case payloads into one versioned dataset.

    The order is the contract: validate the registry and the case
    identities, build every payload, scan every outgoing payload for
    known secrets, and only then touch the client. Examples are matched
    by ``(metadata["case_id"], metadata["case_version"])``; an exact
    identity match writes nothing, a different version of a known case id
    updates that example only, and an identity absent remotely is added.
    Deletion is never called: a remote example the local registry no
    longer names is left in place.
    """
    _validate_cases(cases)

    payloads = [
        example_payload(case, rubric_version=rubric_version) for case in cases
    ]

    leaked_paths: list[str] = []
    for case, payload in zip(cases, payloads):
        for path in contains_secret(payload, secrets):
            leaked_paths.append(f"{case.case_id} v{case.version}: {path}")
    if leaked_paths:
        raise DatasetSyncError(
            "secret_in_dataset", str(SecretLeakError.for_paths(leaked_paths))
        )

    name = dataset_name(agent_name, tier, dataset_version)
    try:
        created = not client.has_dataset(dataset_name=name)
        if created:
            dataset = client.create_dataset(name)
        else:
            dataset = client.read_dataset(dataset_name=name)

        remote_by_id: dict[str, Any] = {}
        for example in client.list_examples(dataset_name=name):
            remote_case_id = example.metadata.get("case_id")
            if remote_case_id is not None:
                remote_by_id[remote_case_id] = example

        added: list[str] = []
        updated: list[str] = []
        reused: list[str] = []
        to_create: list[dict[str, JsonValue]] = []
        to_update: list[dict[str, JsonValue]] = []
        for case, payload in zip(cases, payloads):
            remote = remote_by_id.get(case.case_id)
            if remote is None:
                added.append(case.case_id)
                to_create.append(payload)
            elif remote.metadata["case_version"] == case.version:
                reused.append(case.case_id)
            else:
                updated.append(case.case_id)
                to_update.append(payload)

        if to_create:
            client.create_examples(dataset_id=dataset.id, examples=to_create)
        if to_update:
            client.update_examples(updates=to_update)
    except Exception as error:
        raise DatasetSyncError("dataset_unavailable") from error

    return SyncReport(
        dataset_name=name,
        dataset_id=dataset.id,
        dataset_url=dataset.url,
        created=created,
        added_case_ids=tuple(added),
        updated_case_ids=tuple(updated),
        reused_case_ids=tuple(reused),
    )
