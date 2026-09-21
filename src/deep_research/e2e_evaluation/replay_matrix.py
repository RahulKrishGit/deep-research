"""The versioned offline matrix: eighteen real-agent scenarios.

The manifest below is the *declared inventory* the release proof is measured
against. Each row names a case id, the version of its semantics, the product
result the plan expects, the decisive assertion that makes it that result, and
the scenario builder that drives the real stack.

Every case runs the six production agents through the real graph. The
``ScriptedGraphAgent`` cases in :mod:`deep_research.e2e_evaluation.cases`
remain as graph-only historical regression tests; nothing here instantiates
that double.
"""

from __future__ import annotations

from collections.abc import Callable

from deep_research.e2e_evaluation.replay import (
    CaseExpectation,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
)

REPLAY_CASE_MANIFEST_VERSION = 1

# The case-schema version each case's semantics are pinned at. Bumping a case
# means changing its declaration here and in its own builder together, so a
# recorded result names the semantics it was produced under.
REPLAY_CASE_VERSION = 1


def _source(
    url: str,
    title: str,
    text: str,
    excerpt: str,
    claim: str,
    *,
    issuer: str = "Acme Institute",
    verdict: str = "insufficient_evidence",
) -> ReplaySource:
    return ReplaySource(
        url=url,
        title=title,
        text=text,
        excerpt=excerpt,
        claim=claim,
        issuer=issuer,
        verdict=verdict,
    )


# --- primary-attribution -----------------------------------------------------


def _primary_attribution() -> ReplayScenario:
    """An official measurement question answered by primary attribution.

    The row's decisive assertion is the *badge*: the report may state the
    figure the issuing body published, and must not dress it up as an
    independently corroborated pair, because nothing in the run read a second
    measurement of it.

    Two shapes have to be right for the target to be answered at all, and both
    are properties of the prose rather than of the harness: the clause must
    name its issuing body the way the attribution contract reads one, and it
    must state the value, the year and the geography the frozen contract
    requires. A page whose sentence omits any of them leaves the target
    unanswered no matter how authoritative it is.

    The four pages state the same figure in four different sentences on
    purpose. One official body publishing one number is exactly the shape that
    must never earn the independent-pair badge, and four pages carrying one
    identical sentence would be one claim recorded four times rather than four
    accounts of it.
    """
    authored = (
        (
            "measurement report",
            "Acme Institute widget measurement report",
            "measurement",
            "the measured efficiency of the Acme widget",
            "measured value",
            "Acme widget",
            "measured efficiency",
            (
                "According to Acme Institute the measured efficiency of the "
                "Acme widget in the United States is 42 percent in 2025"
            ),
        ),
        (
            "measurement method",
            "Acme Institute widget measurement method note",
            "method",
            "the method the Acme Institute measured with",
            "observation period",
            "Acme Institute",
            "official method",
            (
                "According to Acme Institute the Acme widget efficiency "
                "measured under the official method in the United States is "
                "42 percent in 2025"
            ),
        ),
        (
            "label figure",
            "Acme Institute widget label figure note",
            "label",
            "the figure printed on the Acme widget label",
            "geography",
            "Acme widget",
            "United States",
            (
                "According to Acme Institute the efficiency printed on the "
                "Acme widget label in the United States is 42 percent in 2025"
            ),
        ),
        (
            "registry entry",
            "Acme Institute widget registry entry",
            "registry",
            "the Acme Institute's own registry entry",
            "attribution",
            "Acme Institute",
            "official registry",
            (
                "According to Acme Institute the Acme widget efficiency "
                "recorded in the official registry in the United States is 42 "
                "percent in 2025"
            ),
        ),
    )
    sources = {
        label: _source(
            url=f"https://official.example.test/{url_slug}",
            title=title,
            text=f"{title}. {claim}.",
            excerpt=claim,
            claim=claim,
        )
        for (
            label,
            title,
            url_slug,
            _subject,
            _dimension,
            _row_subject,
            _row_dimension,
            claim,
        ) in authored
    }
    topics = tuple(
        ReplayTopic(
            title=f"Acme widget {label}",
            question=(
                "What measured efficiency does the official Acme Institute "
                f"report state for {subject}?"
            ),
            dimensions=(dimension,),
            critical=index == 0,
            query=f"Acme Institute Acme widget {label} official measurement",
            sources=(sources[label],),
            answer_labels=(row_subject, row_dimension),
        )
        for index, (
            label,
            _title,
            _url_slug,
            subject,
            dimension,
            row_subject,
            row_dimension,
            _claim,
        ) in enumerate(authored)
    )
    return ReplayScenario(
        case_id="primary-attribution",
        version=REPLAY_CASE_VERSION,
        question=(
            "What measured efficiency does the official Acme Institute "
            "report state for the Acme widget?"
        ),
        topics=topics,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=tuple(
                f"topic-{index:02d}-target-01" for index in range(1, 5)
            ),
            forbidden_assertions=(
                "verified pair",
                "independently corroborated",
            ),
            minimum_answerable_claims=4,
            required_invariants=("primary_attribution_not_verified",),
        ),
    )


# --- the manifest ------------------------------------------------------------


class ReplayCaseEntry:
    """One declared matrix row: identity, expectation, and its builder."""

    def __init__(
        self,
        *,
        case_id: str,
        version: int,
        title: str,
        expected_product_result: str,
        decisive_assertion: str,
        build: Callable[[], ReplayScenario],
        graph_only_historical: bool = False,
    ) -> None:
        self.case_id = case_id
        self.version = version
        self.title = title
        self.expected_product_result = expected_product_result
        self.decisive_assertion = decisive_assertion
        self.build = build
        self.graph_only_historical = graph_only_historical


REPLAY_CASE_MANIFEST: tuple[ReplayCaseEntry, ...] = (
    ReplayCaseEntry(
        case_id="primary-attribution",
        version=REPLAY_CASE_VERSION,
        title="Official measurement answered as primary attribution",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The exact official measurement question is answered as "
            "primary-attributed, and not falsely verified."
        ),
        build=_primary_attribution,
    ),
)

REPLAY_CASE_IDS: tuple[str, ...] = tuple(
    entry.case_id for entry in REPLAY_CASE_MANIFEST
)


def replay_scenarios() -> tuple[ReplayScenario, ...]:
    """Every declared scenario, freshly built."""
    return tuple(entry.build() for entry in REPLAY_CASE_MANIFEST)


def manifest_entry(case_id: str) -> ReplayCaseEntry:
    for entry in REPLAY_CASE_MANIFEST:
        if entry.case_id == case_id:
            return entry
    raise KeyError(f"unknown replay case {case_id!r}")


def scenario_by_id(case_id: str) -> ReplayScenario:
    return manifest_entry(case_id).build()


__all__ = [
    "REPLAY_CASE_IDS",
    "REPLAY_CASE_MANIFEST",
    "REPLAY_CASE_MANIFEST_VERSION",
    "REPLAY_CASE_VERSION",
    "ReplayCaseEntry",
    "manifest_entry",
    "replay_scenarios",
    "scenario_by_id",
]
