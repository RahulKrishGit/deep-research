"""The source-of-truth case registry for individual agent evaluation.

Cases are code-backed typed fixtures rather than YAML because
``ResearchState`` and dependency behavior are richer than a safe
serializable representation. The LangSmith datasets hold a secret-free
mirror; this module is the truth they are synchronized from.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from deep_research.agents.evidence import build_read_record
from deep_research.agents.planner import coverage_id_for
from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    CaseExpectations,
    DeterministicMetric,
    EvaluationCase,
    EvaluationTier,
    JudgeRubric,
    RubricDimension,
    UnknownCaseError,
)
from deep_research.utils.types import (
    EvidenceTarget,
    FigureAttribution,
    FigureContext,
    FigureKind,
    FigureResult,
    Finding,
    FindingFigure,
    FindingStatus,
    FindingVerification,
    MemorySnapshot,
    ReadRecord,
    ResearchState,
    ScoredSource,
    SubTopic,
    TransportRelation,
)

CASE_REGISTRY_VERSION = 2
"""The version of the registry's *semantics*, not of this file.

Task 12 requires that existing case ids are preserved where they are
meaningful and that their semantics are versioned when they change. This
is the version at which one high-risk case per agent joins the registry
and the inventory stops being a literal "exactly three controlled and one
live" and becomes a declared id contract.

The bump lands ahead of the six cases it names, deliberately. A version
that moves early over-approximates — two artifacts stamped 2 may hold
different case sets — while one that moves late lies: if the number had
stayed at 1 while Rounds 4-6 added cases, artifacts would record
``case_registry_version: 1`` beside a case set no v1 run ever scored, and
provenance would fail silently rather than loudly. Where the two
directions of error are not symmetric, take the one that fails safe.

v1: three controlled and one live case per agent, the original fixtures.
v2: the declared-inventory registry and Task 12's high-risk cases.
"""

# The declared inventory. Not a count in an assertion: the registry's
# shape is a contract, and a contract belongs where a reader can read it.
#
# Comparing ids rather than lengths is what makes a case that is renamed,
# duplicated across agents, or filed under the wrong agent fail validation
# instead of quietly satisfying a count. Adding a case is a one-line edit
# to the relevant agent's tuple, in the same commit as the case itself —
# and it must be appended, never prepended (see ``CONTROLLED_CASES``).
EXPECTED_CONTROLLED_CASE_IDS: dict[AgentName, tuple[str, ...]] = {
    "planner": (
        "focused-decomposition",
        "ambiguous-scope",
        "planning-tool-failure",
        "scoped-evidence-targets",
    ),
    "researcher": (
        "multi-source-coverage",
        "conflicting-evidence",
        "partial-search-failure",
        "read-bearing-acquisition",
    ),
    "source_evaluator": (
        "strong-and-weak-sources",
        "corroboration-recency-reputation",
        "reputation-provider-failure",
        "work-role-independence",
    ),
    "evidence_verifier": (
        "scope-corrected-to-all-segments",
        "relay-labelled-as-relay",
        "invented-evidence-words-rejected",
    ),
    "report_writer": (
        "complete-cited-report",
        "conflict-and-limitations",
        "composition-no-publication",
        "canonical-evidence-report",
    ),
}

EXPECTED_LIVE_CASE_IDS: dict[AgentName, tuple[str, ...]] = {
    "planner": ("planner-live-scope",),
    "researcher": ("researcher-live-evidence",),
    "source_evaluator": ("source-evaluator-live-ranking",),
    "evidence_verifier": ("evidence-verifier-live-benchmark",),
    "report_writer": ("report-writer-live-report",),
}

# A fixed timestamp so a case fixture is byte-identical between runs and a
# dataset example never changes just because the clock moved.
FIXED_TIMESTAMP = "2026-08-01T00:00:00+00:00"

# ``SubTopic.coverage_id`` belongs to the Planner, which stamps ``topic-NN``
# in priority order after validation — a case author must not invent one.
# ``sub_topic`` therefore builds a curated sub-topic unstamped, and
# ``evaluation_state`` stamps the real, position-based id for every state it
# assembles, so this value never reaches a case.
UNSTAMPED_COVERAGE_ID = "topic-unstamped"


class CaseRegistryError(ValueError):
    """The local case registry is invalid; nothing may be executed."""


def sub_topic(
    title: str,
    *,
    rationale: str,
    queries: Sequence[str],
    criteria: Sequence[str],
    priority: int,
    coverage_id: str = UNSTAMPED_COVERAGE_ID,
    targets: Sequence[EvidenceTarget] = (),
) -> SubTopic:
    """One curated sub-topic, with the evidence targets a plan stamped on it.

    ``targets`` are the obligations a later pass answers; a case that grades
    target accounting (the Report Writer's Not found section, the Evidence
    Verifier's target binding) seeds them here, because ``evaluation_state``
    stamps only the coverage ids and never invents a target.
    """
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale=rationale,
        search_queries=list(queries),
        success_criteria=list(criteria),
        priority=priority,
        evidence_targets=list(targets),
    )


def target(
    target_id: str,
    *,
    question: str,
    measure: str,
    unit_dimension: str | None = None,
    period: str | None = None,
    kind: str | None = None,
    geography: str | None = None,
    organisation: str | None = None,
    required: bool = True,
) -> EvidenceTarget:
    """One curated evidence target, under the id its own plan stamps.

    ``target_id`` is ``<coverage_id>-target-NN`` exactly as ``PlannerAgent``
    derives it, and the coverage id is read back out of it rather than
    supplied twice: a target whose two ids disagreed would bind to a topic
    that does not own it. ``measure`` is required, because it is the field the
    answer is checked against: a target that states no measure is one no
    finding can be shown to answer.
    """
    coverage_id, _, suffix = target_id.rpartition("-target-")
    if not coverage_id or not suffix:
        raise CaseRegistryError(
            f"target id {target_id!r} must be <coverage-id>-target-NN"
        )
    return EvidenceTarget(
        target_id=target_id,
        coverage_id=coverage_id,
        question=question,
        required=required,
        measure=measure,
        unit_dimension=unit_dimension,
        period=period,
        kind=kind,  # type: ignore[arg-type]
        geography=geography,
        organisation=organisation,
    )


def finding(
    content: str,
    *,
    url: str,
    title: str,
    sub_topic_title: str,
    confidence: float = 0.8,
    snippet: str | None = None,
    read_id: str | None = None,
    locator: str | None = None,
    figures: Sequence[FindingFigure] = (),
    target_ids: Sequence[str] = (),
    vintage: str | None = None,
    statement_date: str | None = None,
    data_period: str | None = None,
    attributed_issuer: str | None = None,
    measure_scope: str | None = None,
    release_date: str | None = None,
) -> Finding:
    """One curated, not-yet-verified finding fixture.

    ``snippet``, ``read_id`` and ``locator`` are the evidence binding the
    Evidence Verifier checks: a finding whose ``read_id`` names no seeded read
    is dropped as ``read_not_found``, and one whose ``snippet`` is not on that
    read's page as ``snippet_not_on_page``, so a fixture that means to be
    verified states both. ``figures``, ``measure_scope``, ``data_period`` and
    the attribution fields are what the Context Check judges.
    """
    return Finding(
        content=content,
        source_url=url,
        source_title=title,
        extracted_at=FIXED_TIMESTAMP,
        confidence=confidence,
        related_sub_topic=sub_topic_title,
        snippet=snippet,
        read_id=read_id,
        locator=locator,
        figures=list(figures),
        target_ids=list(target_ids),
        vintage=vintage,
        statement_date=statement_date,
        data_period=data_period,
        attributed_issuer=attributed_issuer,
        measure_scope=measure_scope,
        release_date=release_date,
    )


def figure(
    value: str,
    unit: str,
    *,
    period: str | None = None,
    kind: FigureKind | None = None,
) -> FindingFigure:
    """One figure a finding's snippet states, exactly as the page writes it."""
    return FindingFigure(value=value, unit=unit, period=period, kind=kind)


def context(
    *,
    organisation: str,
    kind: FigureKind,
    attribution: FigureAttribution = "own",
    period: str | None = None,
    scope: str | None = None,
) -> FigureContext:
    """The context a Context Check confirmed for one kept figure."""
    return FigureContext(
        period=period,
        scope=scope,
        attribution=attribution,
        organisation=organisation,
        kind=kind,
    )


def kept(
    item: FindingFigure,
    confirmed: FigureContext,
    *,
    evidence_words: str | None = None,
    corrected: bool = False,
    reason: str | None = None,
) -> FigureResult:
    """One figure the Context Check kept, with the context it confirmed."""
    return FigureResult(
        figure=item,
        matched=True,
        context=confirmed,
        evidence_words=evidence_words,
        corrected=corrected,
        reason=reason,
    )


def dropped(
    item: FindingFigure,
    reason: str,
    *,
    evidence_words: str | None = None,
    text: str | None = None,
) -> FigureResult:
    """One figure the Context Check dropped, naming why.

    ``reason`` is the enumerated drop reason the verifier can emit
    (``FigureDropReason``); ``text`` is the checker's own sentence, carried
    only where a case wants to pin it.
    """
    return FigureResult(
        figure=item,
        matched=True,
        evidence_words=evidence_words,
        dropped_reason=reason,  # type: ignore[arg-type]
        reason=text,
    )


def verified(
    item: Finding,
    figure_results: Sequence[FigureResult],
    *,
    status: FindingStatus,
    dropped_reason: str | None = None,
    context_unchecked: bool = False,
) -> Finding:
    """The same finding carrying the Evidence Verifier's judgement of it.

    ``FindingVerification`` refuses an inconsistent pair — a ``verified``
    finding with a corrected or dropped figure, a ``verified`` finding whose
    every figure was dropped, a dropped finding with no reason — so a case
    that declares one here is corrected by the type rather than by review.
    """
    return item.model_copy(
        update={
            "verification": FindingVerification(
                status=status,
                figure_results=list(figure_results),
                dropped_reason=dropped_reason,  # type: ignore[arg-type]
                context_unchecked=context_unchecked,
            )
        }
    )


def scored_source(
    url: str,
    *,
    title: str,
    authority: float,
    recency: float,
    relevance: float,
    overall: float,
    rationale: str,
    low_confidence: bool = False,
    serving_host: str | None = None,
    publisher_id: str | None = None,
    work_id: str | None = None,
    transport_relation: TransportRelation = "unknown",
) -> ScoredSource:
    """One assessment row, with the identity its page carries declared.

    A case whose rows are meant to be *readable as identity* declares the
    identity here, because nothing else can supply it. Production derives
    ``serving_host``, ``publisher_id``, ``work_id``, and
    ``transport_relation`` from the read behind the row — see
    ``read_record`` — so a case with no reads (a Synthesizer case assesses
    nothing itself) has to state what its rows are: which host served the
    bytes, which publisher and work the document belongs to, and whether it
    is that work's own publication or a copy. Leaving the defaults is the
    honest "not established", and consumers read ``None`` as exactly that.
    """
    return ScoredSource(
        url=url,
        title=title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        overall_score=overall,
        rationale=rationale,
        low_confidence=low_confidence,
        serving_host=serving_host,
        publisher_id=publisher_id,
        work_id=work_id,
        transport_relation=transport_relation,
    )


def read_record(
    url: str,
    *,
    case_id: str,
    title: str,
    text: str,
    reader: str = "web_scraper",
    target_ids: Sequence[str] = (),
) -> ReadRecord:
    """One seeded, complete read of one document.

    A tool-free agent derives a source's publisher, work, and transport
    relation from the read behind it and from nothing else, so a case that
    scores identity has to seed the reads it scores. The record is built by
    ``build_read_record`` — the same strict producer a live read goes
    through — so a seeded read cannot carry a passage that is not verbatim
    its own body, and its content hash and read id are derived from that
    body exactly as a run's would be.

    ``case_id`` rather than a session id: a seeded read can only belong to
    the case's own evaluation session, which is derived here by the same rule
    ``evaluation_state`` uses, and ``evaluation_state`` refuses a read whose
    session is not the one it is assembling.
    """
    return build_read_record(
        session_id=f"evaluation-{case_id}",
        reader=reader,
        requested_url=url,
        resolved_url=url,
        title=title,
        retrieved_at=FIXED_TIMESTAMP,
        text=text,
        # One document, one whole-body passage: a case authors a document,
        # not a reader's pagination, and a single locator that is exactly the
        # body satisfies the verbatim rule without inventing chunk boundaries
        # the case author never saw.
        passages={"body": text},
        target_ids=target_ids,
    )


def evaluation_state(
    *,
    case_id: str,
    question: str,
    sub_topics: Sequence[SubTopic] = (),
    findings: Sequence[Finding] = (),
    sources: Sequence[ScoredSource] = (),
    verified_findings: Sequence[Finding] = (),
    reads: Sequence[ReadRecord] = (),
    report: str | None = None,
    iteration: int = 0,
    max_extra_passes: int = 1,
    memory_context: MemorySnapshot | None = None,
) -> ResearchState:
    """One curated starting state.

    ``session_id`` is derived from the case id and always prefixed with
    ``evaluation-`` so no case can look like a production session in a
    trace or a memory namespace. Seeded ``reads`` belong to that same
    session and are checked against it rather than relabelled: a read's id
    is a fingerprint of the session that made it, so rewriting the session
    here would leave every seeded read identifying itself as something it
    was not.

    ``max_extra_passes`` is the step-4 field (it replaces the removed
    ``max_iterations``): the number of extra researcher passes the state
    allows on top of the first, never a lower bound on the first pass, and
    ``ResearchState`` refuses a state whose ``iteration`` exceeds it.

    Task 7 review: the controlled memory double drops a scripted
    ``"timestamp"`` field from a seeded entry and falls back to the real
    clock, so a memory seed can never be pinned to a fixed time. Keep any
    assertion on a scripted memory entry's timestamp out of the
    deterministic metrics.
    """
    session_id = f"evaluation-{case_id}"
    for read in reads:
        if read.origin_session_id != session_id:
            raise CaseRegistryError(
                "a seeded read must belong to its own case's session: "
                f"{read.read_id} was read by {read.origin_session_id!r}"
            )
    return ResearchState(
        session_id=session_id,
        original_question=question,
        # A curated plain tuple of sub-topics with duplicate titles would
        # collide in a coverage report, so the planner ids are stamped here,
        # exactly as ``PlannerAgent`` stamps the ids it plans: ordered by
        # priority first, ``topic-01`` on the most important sub-topic, so an
        # id always carries a priority position rather than the position the
        # case author happened to write. ``sorted`` is stable, so equal
        # priorities keep the order the author supplied.
        sub_topics=[
            item.model_copy(update={"coverage_id": coverage_id_for(position)})
            for position, item in enumerate(
                sorted(sub_topics, key=lambda sub_topic: sub_topic.priority),
                start=1,
            )
        ],
        raw_findings=list(findings),
        verified_findings=list(verified_findings),
        evaluated_sources=list(sources),
        read_records={read.read_id: read for read in reads},
        report=report,
        iteration=iteration,
        max_extra_passes=max_extra_passes,
        memory_context=memory_context or MemorySnapshot(),
    )


def metrics(*pairs: tuple[str, float, str]) -> list[DeterministicMetric]:
    """Build the weighted metric list, keeping the weights visible inline."""
    return [
        DeterministicMetric(
            metric_id=metric_id, weight=weight, description=description
        )
        for metric_id, weight, description in pairs
    ]


def rubric(
    rubric_id: str,
    *dimensions: tuple[str, str, str, str],
) -> JudgeRubric:
    """``(dimension_id, description, anchor_1_0, anchor_0_0)`` per dimension."""
    return JudgeRubric(
        rubric_id=rubric_id,
        version=1,
        agent_dimensions=[
            RubricDimension(
                dimension_id=dimension_id,
                description=description,
                anchors={"1.0": high, "0.0": low},
            )
            for dimension_id, description, high, low in dimensions
        ],
    )


# Failure-injection scenarios are scripted in dependencies.py (Tasks 10-15).
# Task 7 review: scripted failures typed as ``httpx.TimeoutException`` /
# ``httpx.HTTPStatusError`` are retried by the real tools (3 attempts each),
# so a scripted failure shows up triple-counted in call counts. Prefer a
# plain ``RuntimeError`` for failure-recovery scenarios, or assert the
# tripled count explicitly.
def build_case(
    *,
    case_id: str,
    agent_name: AgentName,
    tier: EvaluationTier,
    title: str,
    purpose: str,
    state: ResearchState,
    dependency_scenario: str,
    expectations: CaseExpectations,
    judge_rubric: JudgeRubric,
    metadata: dict[str, JsonValue] | None = None,
    version: int = 1,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        version=version,
        agent_name=agent_name,
        tier=tier,
        title=title,
        purpose=purpose,
        state=state,
        dependency_scenario=dependency_scenario,
        expectations=expectations,
        judge_rubric=judge_rubric,
        metadata=metadata or {},
    )


from deep_research.evaluation.cases import (  # noqa: E402
    evidence_verifier,
    planner,
    report_writer,
    researcher,
    source_evaluator,
)

_MODULES = {
    "planner": planner,
    "researcher": researcher,
    "source_evaluator": source_evaluator,
    "evidence_verifier": evidence_verifier,
    "report_writer": report_writer,
}


def all_cases() -> tuple[EvaluationCase, ...]:
    """Every case, in agent order then controlled-before-live order."""
    collected: list[EvaluationCase] = []
    for agent_name in AGENT_NAMES:
        module = _MODULES[agent_name]
        collected.extend(module.CONTROLLED_CASES)
        collected.extend(module.LIVE_CASES)
    return tuple(collected)


def cases_for(
    agent_name: AgentName, tier: EvaluationTier
) -> tuple[EvaluationCase, ...]:
    return tuple(
        case
        for case in all_cases()
        if case.agent_name == agent_name and case.tier == tier
    )


def case_by_id(
    agent_name: AgentName, tier: EvaluationTier, case_id: str
) -> EvaluationCase:
    available = cases_for(agent_name, tier)
    for case in available:
        if case.case_id == case_id:
            return case
    valid = ", ".join(item.case_id for item in available)
    raise UnknownCaseError(
        f"unknown {tier} case {case_id!r} for {agent_name}; "
        f"expected one of: {valid}"
    )


def case_by_identity(case_id: str, version: int) -> EvaluationCase:
    """Look a case up the way a dataset example identifies it."""
    for case in all_cases():
        if case.identity == (case_id, version):
            return case
    raise UnknownCaseError(
        f"no case {case_id!r} at version {version} is in the local registry"
    )


def validate_registry(
    cases: Sequence[EvaluationCase] | None = None,
) -> None:
    """Fail before any model call when the registry cannot be trusted.

    Checks the three things that would corrupt a dataset or an experiment:
    duplicate identities, one id at conflicting versions, and an inventory
    that does not match the declared ids for an agent and tier.
    """
    catalog = list(all_cases() if cases is None else cases)

    seen: dict[tuple[str, int], int] = {}
    versions: dict[str, set[int]] = {}
    for case in catalog:
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

    # The inventory is compared by id and in order, not by length. A count
    # cannot see the failure that matters: a case that is renamed, or
    # reordered under the wrong agent, still satisfies any number.
    for agent_name in AGENT_NAMES:
        for tier, declared in (
            ("controlled", EXPECTED_CONTROLLED_CASE_IDS),
            ("live", EXPECTED_LIVE_CASE_IDS),
        ):
            expected = declared.get(agent_name, ())
            found = tuple(
                case.case_id
                for case in catalog
                if case.agent_name == agent_name and case.tier == tier
            )
            if found != expected:
                raise CaseRegistryError(
                    f"{agent_name} must define the declared {tier} cases "
                    f"{list(expected)}; found {list(found)}"
                )
