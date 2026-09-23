"""The terminal semantic report review: a source-bound judgement of the report.

Task 10 replaces a structural proxy — a keyword-and-length formula over counts
— with a review that reads the report and the evidence behind it. The proxy is
kept for historical comparability (``e2e_evaluation.evaluators`` labels it
structural-only) and is never an acceptance gate again.

Three properties make this review different from the proxy in kind rather than
in degree:

* **It sees the whole report.** ``reader_content`` is the candidate verbatim,
  and no section, statement, or excerpt is prefix-clipped. Oversized evidence
  is split into bounded batches with a complete manifest, so "reviewed" means
  every batch came back — a missing batch is ``incomplete``, never a pass.
* **It judges against the evidence, not against itself.** The packet carries
  the exact excerpts, their badges, the target obligations with their support
  policies, and the deterministic hard checks. The request carries no score
  from the Critic, no prior run's judgement, and no threshold to reach: a
  reviewer told what the acceptance bar is would be answering a different
  question. A separate request is independent *process* review — a second
  reading of the same material — and not proof of an independent model error;
  Task 13's external source review addresses that limit, and nothing here
  claims it.
* **No judgement can read as an acceptance.** A review that could not be made
  is ``incomplete`` or ``provider_failed`` with no dimensions at all, and a
  review whose dispositions leave a statement unsupported derives the material
  defect that blocks acceptance, so one narrow finding cannot be recorded as
  an observation while the report still passes. Acceptance itself is
  ``semantic_review_passes``: the seven dimensions, a mean at or above the
  threshold, and no unresolved critical or major defect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, ClassVar

from pydantic import Field, ValidationError

from deep_research.agents.base import (
    OUTPUT_LIMIT_ATTEMPT_EFFORTS,
    OUTPUT_LIMIT_RETRY_EFFORT,
    OUTPUT_LIMIT_RETRY_OUTCOMES,
    StructuredCompleter,
    call_configuration_fingerprint,
)
from deep_research.agents.critic import (
    CritiqueContractViolation,
    CritiqueGapDraft,
    normalize_gaps,
)
from deep_research.agents.errors import (
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.quality import compute_substantive_coverage
from deep_research.agents.report import (
    ReportComposition,
    evidence_badge_label,
    evidence_status_bucket,
)
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
)
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    EVIDENCE_BADGE_LABELS,
    QUESTION_TARGET_ID,
    REVIEW_DIMENSIONS,
    REVIEW_RUBRIC_VERSION,
    SEMANTIC_REVIEW_MEAN,
    UNSETTLED_STATEMENT_DISPOSITIONS,
    AnswerContract,
    ContractModel,
    CritiqueGap,
    ReportReview,
    ReportStatement,
    ResearchError,
    ResearchState,
    ScoredSource,
    SourceTemporal,
    StatementReviewDisposition,
    SubstantiveCoverage,
    UnitScore,
    counted_evidence_targets,
    statement_claims,
    target_is_answered,
)

REPORT_JUDGE_ROLE = "report_judge"
"""The service role this review resolves its configuration under.

An extra, independently configured call role rather than a seventh agent: it
has no tool path, no ReAct loop, and no slot in ``ResearchAgents``' six agents.
Preflight validates it exactly like an agent, so a misconfigured judge fails
the run before any collaborator exists.
"""

REPORT_REVIEW_PROMPT_VERSION = "report-review-1"
"""The prompt and reply contract this review's requests are versioned under."""

REPORT_REVIEW_MAX_TOKENS = 32768
"""The review request's own output budget.

One reply carries seven dimension scores, a disposition for every reader
statement, and the defects it found, so it is not a small reply; it is the same
reasoning the Critic's review budget exists for.
"""

REPORT_REVIEW_EVIDENCE_BATCH_CHARS = 4000
"""How much rendered evidence one batch carries before the next one starts."""

REPORT_REVIEW_OPERATION = "report_review"
"""The operation label this reviewer's records carry.

One role makes one kind of request — the cross-section judgement and the
follow-ups that complete it — so one label names them all, and a reader
grouping the run's warnings by operation sees this reviewer's records together.
"""


def report_review_output_limit_retry(
    error: Exception,
    *,
    schema: str,
    reasoning_effort: str,
    max_tokens: int,
    outcome: str,
) -> ResearchError:
    """Record that a truncated review request was re-asked at another effort.

    The Critic's rule, on this reviewer's requests: one retry, the same output
    budget, and no run ending. The record exists because the retry is a second
    paid call — without it, a review that took two requests is indistinguishable
    in the artifacts from one that took a single request.

    The request, the effort, the budget it kept, and what came back are in the
    *message*, not only in ``details``: this project publishes details only for
    the error types whose projection it has vetted, and an effort a reader
    cannot see is a retry a reader cannot find.
    """
    if outcome not in OUTPUT_LIMIT_RETRY_OUTCOMES:
        raise ValueError(f"unknown retry outcome: {outcome!r}")
    return agent_error(
        agent_name=REPORT_JUDGE_ROLE,
        error_type="report_review_output_limit_retry",
        message=(
            f"The {schema} review request was truncated by the output limit; "
            f"it was re-asked once at reasoning_effort {reasoning_effort} with "
            f"the same {max_tokens}-token output budget, and "
            + (
                "the retry returned a reply."
                if outcome == "answered"
                else "the retry was truncated as well, so no judgement was "
                "made from it."
            )
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            REPORT_REVIEW_OPERATION,
            error,
            attempt=2,
            schema=schema,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            outcome=outcome,
        ),
    )

MAX_REVIEW_DEFECTS = 12
"""How many distinct defects one review may carry before it is refused."""

# The dimensions, in a stable order, with the semantic definition of each. The
# names are the whole-report campaign's own seven; the definitions are what
# Task 10 changed from a formula to a judgement.
DIMENSION_GUIDANCE: tuple[tuple[str, str], ...] = (
    (
        "completeness",
        "Does the report answer the original question, as the answer contract "
        "frames it, including every part the question actually asked for?",
    ),
    (
        "prioritization",
        "Are the report's ordering and emphasis justified by the evidence — by "
        "an evidenced comparison basis — rather than by how much was written "
        "about something, or by a model's stated confidence?",
    ),
    (
        "evidence_quality",
        "Is each substantive assertion carried by the passages cited for it: "
        "the right kind of source for the claim, and evidence that entails "
        "what the sentence says, at the scope and period it says it?",
    ),
    (
        "attribution",
        "Does every claim's provenance read as what it is — independently "
        "corroborated, primary-source attribution, a recorded derivation, or "
        "contested — with no single work presented as a settled consensus?",
    ),
    (
        "uncertainty",
        "Is the report's uncertainty calibrated and useful: real limits and "
        "contradictions disclosed, and no limitation invented that the "
        "evidence does not record?",
    ),
    (
        "readability",
        "Is the report coherent and economical, without repetition that hides "
        "a contradiction or padding that displaces the answer?",
    ),
    (
        "actionability",
        "Is the report useful for the task that was actually asked? A factual "
        "question is answered by the fact; obligations to recommend or to "
        "instruct belong here only when the question asked for them.",
    ),
)

REPORT_REVIEW_SYSTEM_PROMPT = (
    "You are the terminal reviewer of a finished research report. You judge "
    "the report a reader will receive against the evidence the run actually "
    "holds. You have no tools: everything you may rely on is in this request, "
    "and a fact that is not in the evidence you were shown is not established "
    "by anything you know.\n"
    "\n"
    "Judge substance, not presentation. A fluent report that asserts things "
    "its evidence does not carry is a failed report however well it reads, and "
    "a plain report that answers the question on adequate evidence is a good "
    "one whether or not it tells the reader what to do.\n"
    "\n"
    "For every reader statement, record one disposition: supported, "
    "attributed, inference, unsupported, or returned_to_fact_checker. Use "
    "returned_to_fact_checker when a statement introduces something the cited "
    "evidence does not carry and you cannot settle it from what you were "
    "shown — a mechanism, a quantity, a date, a name, or a place that appears "
    "in the sentence and in no cited passage. Check names and places wherever "
    "they appear in a statement, including a name or place that opens a "
    "sentence: sentence position is not evidence, and an unattested place name "
    "is exactly as unsupported at the start of a sentence as in the middle of "
    "one.\n"
    "\n"
    "Apply the strict definitions. Two passages corroborate a claim only when "
    "they are different works by different publishers and each supports the "
    "whole claim; a single primary source stated as its own attribution is "
    "supported at that level and must not be failed for lacking a second work, "
    "and it must not be passed as independently corroborated. A ranking needs "
    "an evidenced comparison basis: importance is not evidence abundance, and "
    "it is not a model's confidence. Where no comparison basis exists, say so "
    "rather than accepting the order.\n"
    "\n"
    "Report every defect you find as a typed defect against the ids in this "
    "request, and only against ids in this request. When nothing is wrong, "
    "return no defects — but a report you could not verify is not a clean "
    "report, so record what you could not verify as a disposition rather than "
    "as silence."
)

REPORT_REVIEW_INSTRUCTION = (
    "Return one JSON object and nothing else, with these fields:\n"
    "- dimensions: seven scores in [0,1], one per named dimension.\n"
    "- statement_dispositions: one entry per reader statement id you were "
    "shown, each with the statement id and its disposition.\n"
    "- defects: the typed defects you found, each naming the target, "
    "statement, or claim cluster it affects.\n"
    "- reviewed_statement_ids: every reader statement id you actually read.\n"
    "- reviewed_evidence_ids: every evidence id you actually read.\n"
    "- rationale: why the report scores as it does.\n"
    "Every id you cite must be one this request showed you. List every "
    "statement id and every evidence id you read in the two reviewed lists: a "
    "review that does not cover what it was shown is recorded as incomplete "
    "rather than as a result."
)


def semantic_review_passes(review: ReportReview | None) -> bool:
    """Whether a semantic review accepts the report it judged.

    Local and pure. A missing review never passes: the object is optional so a
    caller can ask the question of a run that has not been reviewed yet, and
    the answer must not depend on which caller asked.

    The mean is taken over ``REVIEW_DIMENSIONS`` rather than over a literal, so
    the divisor cannot drift from the dimension set. A perfect mean cannot
    rescue a material defect, and no defect can lower a score that was never
    given: the two conditions are independent, which is what stops one strong
    dimension from averaging away one false claim.
    """
    if review is None:
        return False
    scores = review.dimensions
    return (
        review.status == "scored"
        and set(scores) == REVIEW_DIMENSIONS
        and all(_usable_score(value) for value in scores.values())
        and sum(scores.values()) / len(REVIEW_DIMENSIONS)
        >= SEMANTIC_REVIEW_MEAN
        and not any(gap.material for gap in review.defects)
        and review.coverage_complete
        and _dispositions_complete(review)
    )


def _dispositions_complete(review: ReportReview) -> bool:
    """Whether every statement this review read carries a recorded judgement.

    ``reviewed_statement_ids`` is the review's account of what it read; a
    disposition is what it concluded about it. Reading a statement and
    recording nothing is the per-statement support review being skipped while
    the review still reports itself complete, and one entry per statement is
    what this review's own response contract asks for — so a review missing a
    disposition has not performed the judgement acceptance is resting on.
    """
    return set(review.reviewed_statement_ids).issubset(
        review.per_statement_dispositions
    )


def _usable_score(value: object) -> bool:
    """A dimension that can be averaged: finite, and inside [0, 1]."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = float(value)
    return number == number and number not in (float("inf"), float("-inf")) and (
        0.0 <= number <= 1.0
    )


# --- the packet one review reads --------------------------------------------


class ReviewEvidenceItem(ContractModel):
    """One exact read excerpt the reviewer may treat as evidence.

    Only a registered ``EvidenceUnit`` becomes one of these, exactly as the
    Critic's own evidence item does: a search result, a snippet, or a memory
    recall is not evidence and cannot construct one. The badge is the
    claim-specific corroboration status recorded for the clusters the citing
    statements rest on, and an ambiguous badge resolves to none rather than to
    the stronger of two readings.
    """

    evidence_id: str = Field(min_length=1)
    read_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    badge: str = ""
    badge_label: str = Field(min_length=1)
    cited_by_statement_ids: list[str] = Field(default_factory=list)


class ReviewEvidenceBatch(ContractModel):
    """A bounded group of evidence items, rendered under one heading.

    ``chars`` is the rendered length, recorded on the batch so "no batch
    exceeds the budget" is checkable without re-rendering. A single item larger
    than the budget is its own batch and is allowed to exceed it: re-cutting an
    exact excerpt would change the evidence, which is worse than one long
    batch.
    """

    batch_id: str = Field(min_length=1)
    items: list[ReviewEvidenceItem] = Field(min_length=1)
    chars: int = Field(ge=1)

    @property
    def evidence_ids(self) -> list[str]:
        return [item.evidence_id for item in self.items]


class ReviewTargetView(ContractModel):
    """One planned obligation, with what the report actually answered.

    ``answered`` is the deterministic Section 2.3 reading, and ``accounted``
    says whether an unanswered obligation has a recorded reason. Both travel
    with the target so the reviewer can see the difference between an omission
    the run explained and one it did not — and neither is a verdict: the review
    may still judge that an "answered" target is answered badly.
    """

    target_id: str = Field(min_length=1)
    coverage_id: str = Field(min_length=1)
    coverage_title: str = ""
    question: str = Field(min_length=1)
    required: bool = True
    critical: bool = False
    support_policy: str = "independent_pair"
    required_dimensions: list[str] = Field(default_factory=list)
    answered_dimension_ids: list[str] = Field(default_factory=list)
    answered_by_statement_ids: list[str] = Field(default_factory=list)
    answered: bool = False
    accounted: bool = False
    account_reason: str = ""


class ReviewClaimView(ContractModel):
    """One checked claim, as the reviewer may see it.

    Deliberately without the recorded confidence: a number the adjudicator
    produced is a model judgement about the claim, not evidence for it, and a
    reviewer shown it would be invited to weigh a claim by how sure something
    else was. The verdict and the corroboration badge stay, because those are
    the strict taxonomy Section 2.1 defines.
    """

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    verdict: str = Field(min_length=1)
    evidence_status: str = ""
    badge_label: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)


class ReviewSourceView(ContractModel):
    """One assessed source, identified without its scores.

    Publisher and work identity are what an independence judgement turns on,
    so they travel — with the aliases, status, and evidenced lineage that
    resolved them. So do the fitness facts that constrain what a citation is
    worth: its role, its self-interest, how it was transported, what its dates
    are, and which assessment revision said so. The numeric scores do not:
    they are a model's rating of a source, and a reviewer that ranked evidence
    by them would be repeating the proxy this review replaces.

    This is also exactly the per-source projection of the composition's
    semantic fingerprint, so a change a reviewer could see always invalidates
    the stored judgement.
    """

    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher_id: str = ""
    work_id: str = ""
    evaluation_status: str = ""
    source_role: str = ""
    self_interest: str = ""
    transport_relation: str = ""
    temporal: SourceTemporal = Field(default_factory=SourceTemporal)
    assessment_revision: str = ""
    identity_status: str = ""
    """``known``/``unknown``/``conflicting``; empty when none was resolved."""
    work_aliases: list[str] = Field(default_factory=list)
    derives_from_work_ids: list[str] = Field(default_factory=list)


def review_source_view(source: ScoredSource) -> ReviewSourceView:
    """The one view of an assessed source a review reads and is keyed by."""
    identity = source.work_identity
    return ReviewSourceView(
        url=source.url,
        title=source.title,
        publisher_id=source.publisher_id or "",
        work_id=source.work_id or "",
        evaluation_status=source.evaluation_status,
        source_role=source.source_role,
        self_interest=source.self_interest,
        transport_relation=source.transport_relation,
        temporal=source.temporal,
        assessment_revision=source.assessment_revision,
        identity_status=identity.identity_status if identity is not None else "",
        work_aliases=list(identity.aliases) if identity is not None else [],
        derives_from_work_ids=(
            list(identity.derives_from_work_ids) if identity is not None else []
        ),
    )


class ReviewRankedRow(ContractModel):
    """One row of the report's ranked or compared table, in the printed order.

    The reader table is where a report states an order, and an order is a claim
    that needs a comparison basis. The row travels with the cells it prints
    beside the ranking, the statement id a defect can cite, and the evidence
    ids its statement rests on — so the reviewer can ask whether a passage
    actually compares the ranked things rather than whether there is a lot of
    it. ``rank`` is the position the reader sees.
    """

    rank: int = Field(ge=1)
    statement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    cell_texts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ReviewDeterministic(ContractModel):
    """The deterministic results the reviewer is entitled to see.

    Hard checks, and the coverage metrics the run computed for itself. These
    are facts about the candidate — a failed integrity gate, a required target
    with no answer — and not acceptance coaching: there is no threshold here,
    no score from another reviewer, and no suggested conclusion.
    """

    hard_checks: list[str] = Field(default_factory=list)
    substantive_topic_ratio: UnitScore = 0.0
    planned_topics: int = Field(default=0, ge=0)
    covered_topics: int = Field(default=0, ge=0)
    planned_targets: int = Field(default=0, ge=0)
    required_targets: int = Field(default=0, ge=0)
    answered_targets: int = Field(default=0, ge=0)
    critical_targets: int = Field(default=0, ge=0)
    unanswered_critical_target_ids: list[str] = Field(default_factory=list)
    unaccounted_target_ids: list[str] = Field(default_factory=list)
    initial_target_ids: list[str] = Field(default_factory=list)
    expanded_target_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_coverage(
        cls,
        coverage: SubstantiveCoverage,
        *,
        hard_checks: Sequence[str],
    ) -> "ReviewDeterministic":
        return cls(
            hard_checks=list(hard_checks),
            substantive_topic_ratio=coverage.topic_ratio,
            planned_topics=coverage.planned_topics,
            covered_topics=coverage.covered_topics,
            planned_targets=coverage.planned_targets,
            required_targets=coverage.required_targets,
            answered_targets=coverage.answered_targets,
            critical_targets=coverage.critical_targets,
            unanswered_critical_target_ids=list(
                coverage.unanswered_critical_target_ids
            ),
            unaccounted_target_ids=list(coverage.unaccounted_target_ids),
            initial_target_ids=list(coverage.initial_target_ids),
            expanded_target_ids=list(coverage.expanded_target_ids),
        )


class ReportReviewInput(ContractModel):
    """Everything one semantic review is allowed to judge, and nothing else.

    The whole reader report, every statement record, every registered excerpt,
    the obligations with their policies, the checked claims, the assessed
    sources' identities, and the deterministic checks over the same coverage
    denominator. It carries no Critic score, no prior run's judgement, no
    threshold, and no suggested verdict: a reviewer told what the bar is would
    be answering a different question.

    ``fingerprint`` covers the exact material above. A review is reused only
    for an identical fingerprint, and a returned review is recorded against the
    fingerprint it was opened on, so a judgement can never be attached to text
    it did not read.
    """

    question: str = Field(min_length=1)
    answer_contract: AnswerContract | None = None
    reader_content: str = ""
    reader_sections: dict[str, str] = Field(default_factory=dict)
    statements: list[ReportStatement] = Field(default_factory=list)
    targets: list[ReviewTargetView] = Field(default_factory=list)
    evidence_batches: list[ReviewEvidenceBatch] = Field(default_factory=list)
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    claims: list[ReviewClaimView] = Field(default_factory=list)
    sources: list[ReviewSourceView] = Field(default_factory=list)
    ranked_rows: list[ReviewRankedRow] = Field(default_factory=list)
    """The report's ranked or compared table, row by row, in printed order.

    Taken from the composition's own row structure rather than from an
    ``answered_dimensions`` label: that field records which atom dimensions the
    recorded evidence carries, and a row a constraints plan legitimately
    attested often carries none of them, so selecting by it would give the
    reviewer the comparison-basis rule and no row to apply it to.
    """
    deterministic: ReviewDeterministic = Field(default_factory=ReviewDeterministic)
    rubric_version: int = Field(default=REVIEW_RUBRIC_VERSION, ge=1)
    composition_fingerprint: str = ""
    """The semantic fingerprint of the composition this packet was built from.

    Carried so the review that comes back can record the same value: the state
    merge compares it against an incoming composition to decide whether a
    stored judgement still describes this report. It is derived from the
    composition alone, so it is stable across the terminal re-render that
    changes only the presentation badge.
    """
    fingerprint: str = ""

    @property
    def expected_statement_ids(self) -> list[str]:
        return [statement.statement_id for statement in self.statements]

    @property
    def expected_batch_ids(self) -> list[str]:
        return [batch.batch_id for batch in self.evidence_batches]

    @property
    def total_batches(self) -> int:
        return len(self.evidence_batches)

    @property
    def evidence_ids(self) -> list[str]:
        return [
            item.evidence_id
            for batch in self.evidence_batches
            for item in batch.items
        ]

    @property
    def coverage_ids(self) -> set[str]:
        return {target.coverage_id for target in self.targets}

    def batch(self, batch_id: str) -> ReviewEvidenceBatch | None:
        return next(
            (
                batch
                for batch in self.evidence_batches
                if batch.batch_id == batch_id
            ),
            None,
        )

    def statement(self, statement_id: str) -> ReportStatement | None:
        return next(
            (
                statement
                for statement in self.statements
                if statement.statement_id == statement_id
            ),
            None,
        )


def _batch_evidence(
    items: Sequence[ReviewEvidenceItem],
) -> list[ReviewEvidenceBatch]:
    """Fill bounded batches in order, never dropping or re-cutting an item."""
    batches: list[ReviewEvidenceBatch] = []
    current: list[ReviewEvidenceItem] = []
    current_chars = 0

    def rendered_chars(item: ReviewEvidenceItem) -> int:
        return len(item.excerpt) + len(item.source_title) + len(item.locator) + 64

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        batches.append(
            ReviewEvidenceBatch(
                batch_id=f"batch-{len(batches) + 1:02d}",
                items=list(current),
                chars=max(1, current_chars),
            )
        )
        current = []
        current_chars = 0

    for item in items:
        size = rendered_chars(item)
        if current and current_chars + size > REPORT_REVIEW_EVIDENCE_BATCH_CHARS:
            flush()
        current.append(item)
        current_chars += size
    flush()
    return batches


def _ranked_rows(
    composition: ReportComposition | None,
) -> list[ReviewRankedRow]:
    """The report's own ranked material, in the order the reader receives it.

    A constraints report prints one row per constraint in
    ``composition.constraints`` order and a tabular answer prints
    ``composition.answer_rows``; either order is the report's own claim about
    importance, so the rows travel with the cells beside them and the evidence
    ids their statements cite. A report prints one answer table, so the
    constraint rows are the ranking when they exist and the answer rows are it
    otherwise — numbering the two tables as one ranking would invent a single
    order the reader never sees.
    """
    if composition is None:
        return []
    rows: list[ReviewRankedRow] = []
    for constraint in composition.constraints:
        statement = constraint.statement
        if statement is None:
            continue
        rows.append(
            ReviewRankedRow(
                rank=len(rows) + 1,
                statement_id=statement.statement_id,
                text=constraint.text,
                cell_texts=[
                    text
                    for text in (
                        constraint.deployment_mechanism,
                        constraint.geography,
                    )
                    if text.strip()
                ],
                evidence_ids=list(statement.evidence_ids),
            )
        )
    if rows:
        return rows
    for answer_row in composition.answer_rows:
        statement = answer_row.statement
        if statement is None:
            continue
        rows.append(
            ReviewRankedRow(
                rank=len(rows) + 1,
                statement_id=statement.statement_id,
                text=statement.text,
                cell_texts=[cell.text for cell in answer_row.labels],
                evidence_ids=list(statement.evidence_ids),
            )
        )
    return rows


def _badge_by_evidence(
    composition: ReportComposition,
    *,
    cited_by: Mapping[str, Sequence[str]],
    reading_by_cluster: Mapping[str, str],
) -> dict[str, str]:
    """The one corroboration badge the statements citing a passage agree on.

    Derived from the claims behind the citing statements rather than from the
    cluster registry alone, so a fixture — or a snapshot whose clusters were
    persisted without the registry — still reports the badge its claims carry.
    Each claim contributes its *reading* — ``evidence_status_bucket``, which
    consults the verdict the way the reader and the quality counts do, so a
    contradicted claim carrying a stale ``verified_pair`` badge is contested
    here too. Any disagreement reports no badge: an ambiguous passage must not
    read as the stronger of two verdicts.
    """
    by_id = {statement.statement_id: statement for statement in composition.statements}
    badges: dict[str, set[str]] = {}
    for evidence_id, statement_ids in cited_by.items():
        for statement_id in statement_ids:
            statement = by_id.get(statement_id)
            if statement is None:
                continue
            for claim in statement_claims(composition, statement):
                bucket = evidence_status_bucket(
                    claim.evidence_status, verdict=claim.verdict
                )
                if bucket != "not_established":
                    badges.setdefault(evidence_id, set()).add(bucket)
            for cluster_id in statement.claim_cluster_ids:
                reading = reading_by_cluster.get(cluster_id, "")
                if reading and reading != "not_established":
                    badges.setdefault(evidence_id, set()).add(reading)
    return {
        evidence_id: (next(iter(values)) if len(values) == 1 else "")
        for evidence_id, values in badges.items()
    }


def _badge_label(badge: str, *, verdict: str | None = None) -> str:
    """The reader's label for one recorded badge, read through its verdict."""
    return evidence_badge_label(badge, verdict=verdict)


def build_report_review_input(
    state: ResearchState,
    composition: ReportComposition | None = None,
) -> ReportReviewInput:
    """Build the one packet a semantic review reads, from the exact candidate.

    ``composition`` defaults to ``state.composition``. The report text comes
    from ``state.report`` verbatim — never a prefix — and the statements,
    targets, evidence, and claims come from the composition and the state's
    canonical snapshots, which is why a defect can only ever cite a record this
    packet carries.
    """
    if composition is None:
        composition = state.composition
    report = state.report or ""
    statements = list(composition.statements) if composition is not None else []
    units = dict(composition.evidence_units) if composition is not None else {}
    clusters = dict(composition.claim_clusters) if composition is not None else {}
    sub_topics = (
        list(composition.sub_topics)
        if composition is not None
        else list(state.sub_topics)
    )

    cited_by: dict[str, list[str]] = {}
    for statement in statements:
        for evidence_id in statement.evidence_ids:
            cited_by.setdefault(evidence_id, []).append(statement.statement_id)

    reading_by_cluster = {
        cluster_id: _cluster_reading(cluster)
        for cluster_id, cluster in clusters.items()
    }
    badges = _badge_by_evidence(
        composition, cited_by=cited_by, reading_by_cluster=reading_by_cluster
    ) if composition is not None else {}

    cited_ids: list[str] = []
    for statement in statements:
        for evidence_id in statement.evidence_ids:
            if evidence_id not in cited_ids:
                cited_ids.append(evidence_id)
    ordered_ids = [
        *cited_ids,
        *(evidence_id for evidence_id in units if evidence_id not in cited_ids),
    ]
    items = [
        ReviewEvidenceItem(
            evidence_id=units[evidence_id].evidence_id,
            read_id=units[evidence_id].read_id,
            source_url=units[evidence_id].source_url,
            source_title=units[evidence_id].source_title,
            locator=units[evidence_id].locator,
            excerpt=units[evidence_id].excerpt,
            target_ids=list(units[evidence_id].target_ids),
            badge=badges.get(evidence_id, ""),
            badge_label=_badge_label(badges.get(evidence_id, "")),
            cited_by_statement_ids=cited_by.get(evidence_id, []),
        )
        for evidence_id in ordered_ids
        if evidence_id in units
    ]

    coverage = compute_substantive_coverage(state, composition) if composition else None
    coverage = coverage or compute_substantive_coverage(
        state,
        ReportComposition(
            question=state.original_question,
            session_id=state.session_id,
            sub_topics=list(state.sub_topics),
        ),
    )
    accounted = set(coverage.accounted_target_ids)
    targets: list[ReviewTargetView] = []
    for topic in sub_topics:
        for target in counted_evidence_targets(topic.evidence_targets):
            answering = [
                statement
                for statement in statements
                if target.target_id in statement.target_ids
            ]
            answered_dimensions: list[str] = []
            for statement in answering:
                for dimension in statement.answered_dimensions:
                    if dimension not in answered_dimensions:
                        answered_dimensions.append(dimension)
            targets.append(
                ReviewTargetView(
                    target_id=target.target_id,
                    coverage_id=target.coverage_id,
                    coverage_title=topic.title,
                    question=target.question,
                    required=target.required,
                    critical=target.critical,
                    support_policy=target.support_policy,
                    required_dimensions=list(target.required_dimensions),
                    answered_dimension_ids=answered_dimensions,
                    answered_by_statement_ids=[
                        statement.statement_id for statement in answering
                    ],
                    answered=target_is_answered(state, target),
                    accounted=(
                        target.target_id in accounted
                        or target_is_answered(state, target)
                    ),
                    account_reason=(
                        "a recorded disposition or spent acquisition explains "
                        "this obligation"
                        if target.target_id in accounted
                        else ""
                    ),
                )
            )

    canonical_claims = _canonical_claims(state, composition)
    canonical_sources = _canonical_sources(state, composition)
    hard_checks = list(state.quality.hard_failures) if state.quality else []
    if composition is None:
        hard_checks.append(
            "the report carries no typed composition, so no reader statement "
            "record exists to tie a finding to its evidence"
        )
    missing_evidence = sorted(
        {
            evidence_id
            for statement in statements
            for evidence_id in statement.evidence_ids
            if evidence_id not in units
        }
    )
    if missing_evidence:
        hard_checks.append(
            f"{len(missing_evidence)} statement evidence id(s) are not in the "
            "read registry"
        )

    packet = ReportReviewInput(
        question=state.original_question,
        answer_contract=state.answer_contract,
        reader_content=report,
        reader_sections=_split_reader_report(report),
        statements=statements,
        targets=targets,
        evidence_batches=_batch_evidence(items),
        omitted_evidence_ids=[],
        claims=[
            ReviewClaimView(
                claim_id=claim.claim_id,
                text=claim.text,
                verdict=claim.verdict,
                evidence_status=claim.evidence_status or "",
                badge_label=_badge_label(
                    claim.evidence_status or "", verdict=claim.verdict
                ),
                source_urls=list(claim.source_urls),
                target_ids=list(claim.target_ids),
            )
            for claim in canonical_claims
        ],
        sources=[review_source_view(source) for source in canonical_sources],
        ranked_rows=_ranked_rows(composition),
        deterministic=ReviewDeterministic.from_coverage(
            coverage, hard_checks=hard_checks
        ),
        composition_fingerprint=composition_semantic_fingerprint(composition),
    )
    return packet.model_copy(
        update={"fingerprint": report_review_input_fingerprint(packet)}
    )


def _canonical_claims(
    state: ResearchState,
    composition: ReportComposition | None,
) -> list[Any]:
    from deep_research.agents.identity import merge_claim_snapshot  # noqa: PLC0415

    rows = list(state.verified_claims or (composition.claims if composition else ()))
    return merge_claim_snapshot([], rows)


def _canonical_sources(
    state: ResearchState,
    composition: ReportComposition | None,
) -> list[ScoredSource]:
    from deep_research.agents.identity import merge_source_snapshot  # noqa: PLC0415

    rows = list(
        state.evaluated_sources or (composition.sources if composition else ())
    )
    return merge_source_snapshot([], rows)


def _cluster_reading(cluster: Any) -> str:
    """The one corroboration reading a cluster's verdicts agree on, or blank.

    Normalized to the same four buckets the counts and the reader use, so a
    cluster's recorded badge and a claim's badge cannot enter one evidence
    item's reading set as two different words for the same judgement.
    """
    readings = {
        evidence_status_bucket(badge)
        for badge in cluster.verdict_evidence_status.values()
        if badge in EVIDENCE_BADGE_LABELS
    }
    return readings.pop() if len(readings) == 1 else ""


def composition_semantic_fingerprint(
    composition: ReportComposition | None,
) -> str:
    """The digest of everything in one composition a review judges.

    Task 10's state rule is "replacing the composition invalidates the stored
    review unless its semantic fingerprint matches", and this is that
    fingerprint: the reader-visible content (every rendered point's text and
    its statement record), the evidence registry, the plan's sub-topics, and
    the canonical claims and sources.

    Deliberately *not* a whole-composition dump, and deliberately not
    ``quality_status``: that field is a generated presentation badge the
    terminal finalizer rewrites on the way out, and hashing it would make
    stamping "accepted" onto a report invalidate the judgement that accepted
    it. A content, reference, or target change always invalidates; the badge
    never does.
    """
    if composition is None:
        return ""
    projection = {
        "question": composition.question,
        "scope": composition.scope,
        "as_of": composition.as_of,
        "sub_topics": [
            topic.model_dump(mode="json") for topic in composition.sub_topics
        ],
        "points": [
            _point_projection(point)
            for point in _composition_points(composition)
        ],
        "cell_statements": [
            statement.model_dump(mode="json")
            for statement in _composition_cell_statements(composition)
        ],
        "answer_rows": [
            {
                "cells": [cell.model_dump(mode="json") for cell in row.cells],
            }
            for row in composition.answer_rows
        ],
        "uncertainty_statements": [
            statement.model_dump(mode="json")
            for statement in composition.uncertainty_statements
        ],
        "uncertainty_notes": list(composition.uncertainty_notes),
        "limitations": list(composition.limitations),
        "evidence_units": {
            evidence_id: unit.model_dump(mode="json")
            for evidence_id, unit in sorted(composition.evidence_units.items())
        },
        "claims": [
            {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "verdict": claim.verdict,
                "evidence_status": claim.evidence_status or "",
                "source_urls": sorted(claim.source_urls),
                "cluster_id": claim.cluster_id or "",
            }
            for claim in composition.claims
        ],
        "sources": [
            review_source_view(source).model_dump(mode="json")
            for source in composition.sources
        ],
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _point_projection(point: Any) -> dict[str, Any]:
    """One rendered point's reader-visible content and its record."""
    return {
        "text": point.text,
        "claim_ids": list(point.claim_ids),
        "source_urls": list(point.source_urls),
        "statement": (
            None
            if point.statement is None
            else point.statement.model_dump(mode="json")
        ),
    }


def _composition_points(composition: ReportComposition) -> list[Any]:
    """Every rendered *point*, in render order.

    Points — summary bullets, constraint rows, section bullets — carry claim
    ids and source urls as well as their statement record. A constraint's
    mechanism and geography **cells**, and an answer row's cells, are not
    points: they are bare ``ReportStatement`` records, and
    ``_composition_cell_statements`` projects them separately rather than
    asking a statement for a ``claim_ids`` field it does not have. Both halves
    are reader-visible, so both are in the identity.
    """
    points: list[Any] = [*composition.summary, *composition.constraints]
    for section in composition.sections:
        points.extend(section.points)
    return points


def _composition_cell_statements(composition: ReportComposition) -> list[Any]:
    """Every statement a composition renders as a table *cell*, in order."""
    statements: list[Any] = []
    for row in composition.constraints:
        for cell in (row.mechanism_statement, row.geography_statement):
            if cell is not None:
                statements.append(cell)
    for row in composition.answer_rows:
        statements.extend(row.cells)
    return statements


def report_review_input_fingerprint(packet: ReportReviewInput) -> str:
    """The stable digest of the exact material one review judges.

    Twelve hex characters over the packet's canonical JSON, with its own
    ``fingerprint`` field excluded. Every field is part of it on purpose — a
    statement's text, an evidence excerpt, a target's policy, and the reader
    content are all things whose change makes the stored judgement about a
    different report — and the packet deliberately carries no presentation
    field: ``quality_status`` is a generated badge this packet never reads, so
    stamping "accepted" onto a composition cannot invalidate a judgement of its
    content, while a content, reference, or target change always does.
    """
    payload = packet.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _split_reader_report(report: str) -> dict[str, str]:
    """Split the candidate into its own ``##`` sections, whole.

    The same split the Critic's packet performs, for the same reason: the
    request renders one fenced block per section so a heading inside the report
    cannot be confused with a heading of the request, and no section is cut.
    """
    from deep_research.agents.critic import (
        _split_reader_report as split,  # noqa: PLC0415
    )

    return split(report)


# --- the provider-facing contract -------------------------------------------


class ReviewDimensionScores(ContractModel):
    """The seven dimension scores, as the reply carries them.

    Seven named fields rather than a mapping: the dimension set is then
    structural in the response schema, so a reply that omits one is a schema
    failure with a field path rather than a dict that quietly averages over six.
    """

    completeness: UnitScore
    prioritization: UnitScore
    evidence_quality: UnitScore
    attribution: UnitScore
    uncertainty: UnitScore
    readability: UnitScore
    actionability: UnitScore

    def as_dimensions(self) -> dict[str, float]:
        return {
            "completeness": float(self.completeness),
            "prioritization": float(self.prioritization),
            "evidence_quality": float(self.evidence_quality),
            "attribution": float(self.attribution),
            "uncertainty": float(self.uncertainty),
            "readability": float(self.readability),
            "actionability": float(self.actionability),
        }


class StatementDispositionDraft(ContractModel):
    """One provider-reported per-statement disposition."""

    statement_id: str = Field(min_length=1)
    disposition: StatementReviewDisposition
    problem: str = ""


class ReviewBatchDraft(ContractModel):
    """One provider-reported batch review.

    A batch reply carries no dimensions: the seven scores are a judgement of
    the whole report, and a per-batch score would be an average over sections
    pretending to be one.
    """

    batch_id: str = Field(min_length=1)
    statement_dispositions: list[StatementDispositionDraft] = Field(
        default_factory=list
    )
    defects: list[CritiqueGapDraft] = Field(default_factory=list)
    reviewed_statement_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_ids: list[str] = Field(default_factory=list)
    problem: str = ""


class ReportReviewDraft(ContractModel):
    """One provider-reported whole-report review, before local merge."""

    dimensions: ReviewDimensionScores
    statement_dispositions: list[StatementDispositionDraft] = Field(
        default_factory=list
    )
    defects: list[CritiqueGapDraft] = Field(
        default_factory=list, max_length=MAX_REVIEW_DEFECTS
    )
    reviewed_statement_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ReportReviewContractViolation(RuntimeError):
    """A reply that passed its schema and broke the review's own contract."""


# --- the request ------------------------------------------------------------


def _render_answer_contract(contract: AnswerContract | None) -> str:
    if contract is None:
        return "(no answer contract was frozen for this run)"
    return "\n".join(
        (
            f"- question: {contract.question}",
            f"- scope: {contract.scope_statement}",
            f"- geography: {contract.geographic_scope}",
            f"- as of: {contract.as_of_date}",
            f"- answer form: {contract.answer_kind}",
            f"- evidence period: {contract.evidence_period_requirement}",
            f"- requested word limit: {contract.requested_word_limit}",
            "- assumptions: "
            + ("; ".join(contract.assumptions) or "(none recorded)"),
        )
    )


def _render_statements(packet: ReportReviewInput) -> str:
    lines: list[str] = []
    for statement in packet.statements:
        lines.append(
            f"- {statement.statement_id} [{statement.mode}] "
            f"targets={','.join(statement.target_ids) or '-'} "
            f"dimensions={','.join(statement.answered_dimensions) or '-'} "
            f"clusters={','.join(statement.claim_cluster_ids) or '-'} "
            f"evidence={','.join(statement.evidence_ids) or '-'}\n"
            f"  {statement.text}"
            + (f"\n  basis: {statement.basis}" if statement.basis else "")
        )
    return "\n".join(lines) or "(no reader statement records were supplied)"


def _render_targets(packet: ReportReviewInput) -> str:
    lines: list[str] = []
    for target in sorted(packet.targets, key=lambda item: item.answered):
        lines.append(
            f"- {target.target_id} ({target.coverage_id}"
            f"{' critical' if target.critical else ''}"
            f"{'' if target.required else ' optional'}) "
            f"policy={target.support_policy} "
            f"answered={'yes' if target.answered else 'no'} "
            f"accounted={'yes' if target.accounted else 'no'} "
            f"dimensions={','.join(target.required_dimensions) or '-'} "
            f"statements={','.join(target.answered_by_statement_ids) or '-'}\n"
            f"  {target.question}"
        )
    return "\n".join(lines) or "(no evidence targets were planned)"


def _render_evidence_item(item: ReviewEvidenceItem) -> str:
    return (
        f"### {item.evidence_id}\n"
        f"source: {item.source_title} — {item.source_url}\n"
        f"read: {item.read_id} locator: {item.locator}\n"
        f"corroboration: {item.badge_label}\n"
        f"cited by: {', '.join(item.cited_by_statement_ids) or '-'}\n"
        f"excerpt:\n{item.excerpt}"
    )


def _render_evidence_batches(packet: ReportReviewInput) -> str:
    blocks: list[str] = []
    for batch in packet.evidence_batches:
        blocks.append(
            f"## Evidence {batch.batch_id} "
            f"({len(batch.items)} passage(s), {batch.chars} rendered chars)"
        )
        blocks.extend(_render_evidence_item(item) for item in batch.items)
    return "\n\n".join(blocks) or "(no read evidence was registered)"


def _render_ranking_section(packet: ReportReviewInput) -> str:
    """Every ranked row with the evidence behind it, and the basis rule.

    Ruling 6: ranking requires an evidenced comparison basis. Importance is not
    evidence abundance and it is not model confidence, so the request shows the
    rows the reader sees, their printed order, and the exact excerpts each row
    rests on — the material a comparison has to be made from — and states the
    rule rather than hoping the model applies one. The rows come from the
    composition's own ranked table (``ranked_rows``), which is the only place
    the printed order exists; a row's ``answered_dimensions`` records what the
    evidence carries, not that it was ranked, and is empty for a legitimately
    attested constraint.
    """
    lines = [
        "A ranking states an order. Every order in this report must be "
        "justified by an evidenced comparison basis: a passage that actually "
        "compares the ranked things, on a stated scale, for the stated period "
        "and scope. Evidence abundance is not importance — a row with five "
        "weak passages is not more important than a row with one decisive "
        "one — and a model's stated confidence is not evidence at all. When no "
        "comparison basis exists, say so, and say that no defensible universal "
        "order was established, rather than accepting the order as written.",
        "Ranked rows and the evidence behind them:",
    ]
    if not packet.ranked_rows:
        lines.append("- (this report records no ranked or compared rows)")
        return "\n".join(lines)
    by_id = {
        item.evidence_id: item
        for batch in packet.evidence_batches
        for item in batch.items
    }
    for row in packet.ranked_rows:
        cells = "; ".join(row.cell_texts)
        lines.append(
            f"- {row.rank}. {row.statement_id}: {row.text}"
            + (f" ({cells})" if cells else "")
            + "\n  evidence: "
            + (
                " | ".join(
                    f"[{evidence_id}] {by_id[evidence_id].excerpt[:400]}"
                    for evidence_id in row.evidence_ids
                    if evidence_id in by_id
                )
                or "(no registered passage is cited for this row)"
            )
        )
    return "\n".join(lines)


def _render_claims(packet: ReportReviewInput) -> str:
    lines = [
        f"- [{claim.verdict}; {claim.badge_label}] {claim.text} "
        f"({', '.join(claim.source_urls) or 'no url'})"
        for claim in packet.claims
    ]
    return "\n".join(lines) or "(no claims were checked)"


def _render_sources(packet: ReportReviewInput) -> str:
    lines = [_render_source(source) for source in packet.sources]
    return "\n".join(lines) or "(no sources were assessed)"


def _render_source(source: ReviewSourceView) -> str:
    """One source line: identity, fitness, and dates, never a score."""
    temporal = source.temporal
    dates = ", ".join(
        f"{name}={value}"
        for name, value in (
            ("published", temporal.publication_date),
            ("data", temporal.data_period),
            ("forecast", temporal.forecast_horizon),
            ("effective", temporal.effective_date),
        )
        if value
    )
    return (
        f"- {source.title} — {source.url} "
        f"(publisher={source.publisher_id or 'unknown'}, "
        f"work={source.work_id or 'unknown'}, "
        f"identity={source.identity_status or 'unknown'}, "
        f"aliases={', '.join(source.work_aliases) or 'none'}, "
        f"derives_from={', '.join(source.derives_from_work_ids) or 'none'}, "
        f"role={source.source_role or 'unknown'}, "
        f"self_interest={source.self_interest or 'unknown'}, "
        f"transport={source.transport_relation or 'unknown'}, "
        f"assessment={source.evaluation_status or 'unknown'}, "
        f"revision={source.assessment_revision or 'none'}, "
        f"temporal={temporal.status}"
        + (f" [{dates}]" if dates else "")
        + ")"
    )


def _render_dimension_guidance() -> str:
    return "\n".join(
        f"- {name}: {guidance}" for name, guidance in DIMENSION_GUIDANCE
    )


def _render_manifest(packet: ReportReviewInput) -> str:
    return "\n".join(
        (
            f"Statement ids in this packet: "
            f"{', '.join(packet.expected_statement_ids) or '(none)'}",
            f"Evidence ids in this packet: "
            f"{', '.join(packet.evidence_ids) or '(none)'}",
            f"Evidence batches: {packet.total_batches} "
            f"({', '.join(packet.expected_batch_ids) or 'none'})",
            f"Omitted evidence ids: "
            f"{', '.join(packet.omitted_evidence_ids) or '(none)'}",
        )
    )


def review_messages(packet: ReportReviewInput) -> list[ChatMessage]:
    """The one whole-report request: report, statements, evidence, checks.

    Nothing here is truncated. The reader content is rendered section by
    section, each in full, and the evidence is rendered batch by batch in
    full — a report whose end is cut off is a report whose closing
    contradiction, invented limitation, or fabricated citation cannot be
    judged, and the reviewed baseline lost exactly those.
    """
    sections = [
        f"# Research question\n{packet.question}",
        (
            "# Packet fingerprint\n"
            f"Packet fingerprint: {packet.fingerprint}\n"
            "This review is of exactly this material: the report, the statement "
            "records, the targets, the evidence, and the checks below are the "
            "whole of what is being judged."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        (
            "# Reader content — the complete candidate\n"
            "The report is split into fenced sections below, one per section, "
            "each carried in full and with nothing removed from its end. Its "
            "headings belong to the report rather than to this request. This is "
            "the complete report a reader would receive, not a prefix: judge it "
            "whole, including its closing sections."
        ),
        *_render_reader_sections(packet),
        (
            "# Reader statements\n"
            "Every substantive sentence the report prints, with the mode it "
            "reads in, the targets and dimensions it answers, and the evidence "
            "behind it. A defect may cite a statement id from this list and no "
            "other.\n" + _render_statements(packet)
        ),
        (
            "# Evidence targets\n"
            "The obligations this pass owed, with the support policy each one "
            "declared and whether the run's own deterministic reading counts it "
            "as answered.\n" + _render_targets(packet)
        ),
        (
            "# Ranking and comparison basis\n" + _render_ranking_section(packet)
        ),
        (
            "# Evidence — read excerpts, batched\n"
            "Exact passages of successful reads this run registered, grouped "
            "into batches. An excerpt is evidence; a search result, a snippet, "
            "or a memory recall is not, and none of them appears here.\n"
            + _render_evidence_batches(packet)
        ),
        (
            "# Deterministic checks\n"
            "Integrity results and coverage the run computed for itself. A "
            "failed check is a fact about the candidate, not a verdict — and "
            "none of these numbers is a target to reach.\n"
            + _render_hard_checks(packet)
        ),
        f"# Checked claims\n{_render_claims(packet)}",
        f"# Assessed sources\n{_render_sources(packet)}",
        (
            "# What each dimension means\n"
            "Score each dimension in [0,1] against its own definition:\n"
            + _render_dimension_guidance()
        ),
        f"# Response contract\n{REPORT_REVIEW_INSTRUCTION}",
        f"# Manifest of what you were shown\n{_render_manifest(packet)}",
    ]
    return [
        ChatMessage(role="developer", content=REPORT_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def _render_reader_sections(packet: ReportReviewInput) -> list[str]:
    from deep_research.agents.critic import (  # noqa: PLC0415
        _render_balanced_report_sections,
    )

    return _render_balanced_report_sections(
        packet.reader_content, report_sections=packet.reader_sections
    )


def batch_review_messages(
    packet: ReportReviewInput,
    batch: ReviewEvidenceBatch,
) -> list[ChatMessage]:
    """The follow-up request for one evidence batch the reply did not cover.

    Same packet, same fingerprint, one batch in full: a reviewer that did not
    read a batch is asked to read that batch rather than to redo the whole
    review, and the batch's own id travels so the merge can record which
    batches came back.
    """
    sections = [
        f"# Research question\n{packet.question}",
        (
            "# Packet fingerprint\n"
            f"Packet fingerprint: {packet.fingerprint}\n"
            f"Evidence batch under review: {batch.batch_id}"
        ),
        (
            "# Reader statements\n"
            "The statements whose evidence this batch carries, with their "
            "text.\n" + _render_statements(packet)
        ),
        (
            f"# Evidence — {batch.batch_id}\n"
            "Read every passage in this batch and record what it supports, "
            "contradicts, or leaves unestablished.\n"
            + "\n\n".join(_render_evidence_item(item) for item in batch.items)
        ),
        (
            "# Response contract\n"
            "Return one JSON object with these fields and no others: "
            "batch_id (this batch's id), statement_dispositions (one entry per "
            "statement you can now judge), defects (typed, naming the ids "
            "above), reviewed_statement_ids, reviewed_evidence_ids (every "
            "evidence id in this batch that you read), and problem (anything "
            "that stopped you reading it, or an empty string)."
        ),
    ]
    return [
        ChatMessage(role="developer", content=REPORT_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def _render_hard_checks(packet: ReportReviewInput) -> str:
    deterministic = packet.deterministic
    lines = [
        f"- {check}" for check in deterministic.hard_checks
    ] or ["- no deterministic hard failure was recorded"]
    lines.extend(
        (
            f"- substantive topic coverage: "
            f"{deterministic.covered_topics}/{deterministic.planned_topics} "
            f"topic(s) fully answered",
            f"- required targets answered: "
            f"{deterministic.answered_targets}/{deterministic.required_targets}",
            f"- unanswered critical targets: "
            f"{', '.join(deterministic.unanswered_critical_target_ids) or 'none'}",
            f"- obligations with no recorded reason: "
            f"{', '.join(deterministic.unaccounted_target_ids) or 'none'}",
        )
    )
    return "\n".join(lines)


# --- merging a reply into one review ----------------------------------------


def _dispositions(
    drafts: Sequence[StatementDispositionDraft],
    *,
    packet: ReportReviewInput,
) -> dict[str, StatementReviewDisposition]:
    """One disposition per statement, with disagreement resolved safely.

    A statement two replies judge differently resolves to the *less* settled
    reading: "this sentence is not carried by its evidence" may not be
    overwritten by another reply's "supported", or a disagreement between two
    readings of the same text would be recorded as agreement. An id the packet
    does not carry is refused, exactly as an unresolvable defect scope is.
    """
    resolved: dict[str, StatementReviewDisposition] = {}
    for draft in drafts:
        statement_id = draft.statement_id.strip()
        if not statement_id:
            continue
        if packet.statement(statement_id) is None:
            raise ReportReviewContractViolation(
                f"the reply judged statement {statement_id!r}, which this "
                "packet does not carry"
            )
        previous = resolved.get(statement_id)
        if previous is None or (
            draft.disposition in UNSETTLED_STATEMENT_DISPOSITIONS
            and previous not in UNSETTLED_STATEMENT_DISPOSITIONS
        ):
            resolved[statement_id] = draft.disposition
    return resolved


def _resolved_defects(
    drafts: Sequence[CritiqueGapDraft],
    *,
    packet: ReportReviewInput,
) -> list[CritiqueGap]:
    """The typed defects a reply named, refused where their scope cannot resolve.

    The reply is parsed by the Critic's own ``normalize_gaps`` — one contract,
    one parser, one dedup rule for both reviewers — and the ids it names are
    then checked against this packet's registries. ``normalize_gaps`` is called
    without a ``CriticPacket`` because this packet is a different shape; the
    resolution check that packet would have performed is performed here
    instead, and a defect whose declared scope resolves to nothing is refused
    rather than widened to the whole answer.
    """
    if not drafts:
        return []
    try:
        gaps = normalize_gaps(
            list(drafts),
            known_coverage_ids=packet.coverage_ids,
            packet=None,
            limit=MAX_REVIEW_DEFECTS,
        )
    except CritiqueContractViolation as violation:
        raise ReportReviewContractViolation(str(violation)) from violation
    known_targets = {target.target_id for target in packet.targets}
    known_statements = set(packet.expected_statement_ids)
    known_clusters: set[str] = set()
    for statement in packet.statements:
        known_clusters.update(statement.claim_cluster_ids)
    for gap in gaps:
        unknown_targets = [
            target_id
            for target_id in gap.target_ids
            if target_id not in known_targets
            and target_id != QUESTION_TARGET_ID
        ]
        unknown_statements = [
            statement_id
            for statement_id in gap.statement_ids
            if statement_id not in known_statements
        ]
        unknown_clusters = [
            cluster_id
            for cluster_id in gap.claim_cluster_ids
            if cluster_id not in known_clusters
        ]
        if unknown_targets or unknown_statements or unknown_clusters:
            raise ReportReviewContractViolation(
                "the defect declared scope ids that do not resolve in this "
                "packet: "
                + ", ".join(
                    sorted(unknown_targets + unknown_statements + unknown_clusters)
                )
            )
        if not (gap.target_ids or gap.statement_ids or gap.claim_cluster_ids):
            raise ReportReviewContractViolation(
                "a defect must name the target, statement, or claim cluster "
                "it affects"
            )
    return gaps


def _derived_defects(
    packet: ReportReviewInput,
    dispositions: Mapping[str, StatementReviewDisposition],
    defects: Sequence[CritiqueGap],
) -> tuple[list[CritiqueGap], list[str]]:
    """Material defects for the statements a disposition left unestablished.

    The reviewer's own disposition is a judgement — "this sentence is not in
    the source" — and it must not be recordable while the review still passes,
    which is exactly what would happen if an unsettled statement were an
    observation and the pass rule only read ``defects``. So an unsettled
    disposition that no returned defect names gets a project-derived material
    defect, and the statements that produced one are recorded: the report says
    which defects the reviewer returned and which this project derived from its
    dispositions.

    The skip test asks about a **material** defect, because that is what the
    record contract requires an unsettled statement to be named by: asking
    whether *any* defect named it let a `minor` observation about a sentence
    stand in for the finding that the sentence is not carried by its evidence.
    The derived defect was then skipped, the record contract refused the
    assembled review, and that refusal propagated out of ``review_report`` —
    so a contract-valid reply crashed the run instead of recording the
    judgement the reviewer had actually made.
    """
    derived: list[CritiqueGap] = []
    derived_statements: list[str] = []
    for statement_id in sorted(dispositions):
        disposition = dispositions[statement_id]
        if disposition not in UNSETTLED_STATEMENT_DISPOSITIONS:
            continue
        if any(
            statement_id in gap.statement_ids for gap in defects if gap.material
        ):
            continue
        statement = packet.statement(statement_id)
        if statement is None:
            continue
        targets = [
            target.target_id
            for target in packet.targets
            if target.target_id in statement.target_ids
        ]
        derived.append(
            CritiqueGap(
                gap_id=f"review-{len(derived) + 1:02d}",
                target_ids=targets or [QUESTION_TARGET_ID],
                claim_cluster_ids=list(statement.claim_cluster_ids),
                statement_ids=[statement_id],
                kind="missing_support",
                severity="major",
                repair_action="adjudicate",
                problem=(
                    "The semantic review recorded this statement as "
                    f"{disposition!r}: the evidence behind it does not "
                    "establish it as written."
                ),
            )
        )
        derived_statements.append(statement_id)
    return derived, derived_statements


def _merge_review(
    packet: ReportReviewInput,
    *,
    dimension_scores: Mapping[str, float] | None,
    dispositions: Mapping[str, StatementReviewDisposition],
    defects: Sequence[CritiqueGap],
    derived_statements: Sequence[str],
    reviewed_statement_ids: Sequence[str],
    reviewed_evidence_ids: Sequence[str],
    reviewed_batch_ids: Sequence[str],
    rationale: str,
    status: str,
) -> ReportReview:
    """Assemble the recorded review from the replies that came back."""
    known_statements = set(packet.expected_statement_ids)
    reviewed = [
        statement_id
        for statement_id in dict.fromkeys(reviewed_statement_ids)
        if statement_id in known_statements
    ]
    unreviewed = [
        statement_id
        for statement_id in packet.expected_statement_ids
        if statement_id not in reviewed
    ]
    known_evidence = set(packet.evidence_ids)
    reviewed_evidence = [
        evidence_id
        for evidence_id in dict.fromkeys(reviewed_evidence_ids)
        if evidence_id in known_evidence
    ]
    omitted = [
        evidence_id
        for evidence_id in packet.evidence_ids
        if evidence_id not in reviewed_evidence
    ]
    return ReportReview(
        status=status,  # type: ignore[arg-type]
        dimensions=dict(dimension_scores or {}),
        defects=list(defects),
        per_statement_dispositions=dict(dispositions),
        reviewed_statement_ids=reviewed,
        unreviewed_statement_ids=unreviewed,
        reviewed_evidence_ids=reviewed_evidence,
        omitted_evidence_ids=omitted,
        reviewed_batch_ids=list(dict.fromkeys(reviewed_batch_ids)),
        expected_batch_ids=list(packet.expected_batch_ids),
        reviewed_target_ids=[target.target_id for target in packet.targets],
        derived_defect_statement_ids=list(derived_statements),
        input_fingerprint=packet.fingerprint,
        composition_fingerprint=packet.composition_fingerprint,
        rubric_version=packet.rubric_version,
        rationale=rationale,
    )


def _status_for(
    packet: ReportReviewInput,
    *,
    dimension_scores: Mapping[str, float] | None,
    reviewed_statement_ids: Sequence[str],
    reviewed_batch_ids: Sequence[str],
    dispositions: Mapping[str, StatementReviewDisposition],
) -> str:
    """``scored`` only when this review actually covered what it was given.

    Coverage is a precondition of a score, not a note beside one: a review that
    skipped a statement, a batch, or a dimension has not judged the report, and
    recording it as ``scored`` would let missing coverage read as a pass. The
    same rule is stated on ``ReportReview`` itself, so an incomplete score
    cannot be constructed even by a later caller.

    Reading is not judging, so a disposition is required for every statement
    too: ``reviewed_statement_ids`` is the reply's own account of what it read,
    and a statement listed there with no disposition recorded is the
    per-statement support review claimed and not performed. With a perfect mean
    and no defects that shape would otherwise be recorded ``scored``, which is
    the one reading this whole contract exists to prevent.
    """
    if dimension_scores is None:
        return "incomplete"
    if set(dimension_scores) != REVIEW_DIMENSIONS:
        return "incomplete"
    if set(packet.expected_statement_ids).difference(reviewed_statement_ids):
        return "incomplete"
    if set(packet.expected_statement_ids).difference(dispositions):
        return "incomplete"
    if set(packet.expected_batch_ids).difference(reviewed_batch_ids):
        return "incomplete"
    return "scored"


def _incomplete_reason(
    packet: ReportReviewInput,
    *,
    reviewed_statement_ids: Sequence[str],
    reviewed_batch_ids: Sequence[str],
    dispositions: Mapping[str, StatementReviewDisposition],
) -> str:
    missing_statements = [
        statement_id
        for statement_id in packet.expected_statement_ids
        if statement_id not in reviewed_statement_ids
    ]
    undispositioned_statements = [
        statement_id
        for statement_id in packet.expected_statement_ids
        if statement_id not in dispositions
    ]
    missing_batches = [
        batch_id
        for batch_id in packet.expected_batch_ids
        if batch_id not in reviewed_batch_ids
    ]
    parts: list[str] = []
    if missing_statements:
        parts.append(
            "no review reached statement(s) " + ", ".join(missing_statements)
        )
    if undispositioned_statements:
        parts.append(
            "no disposition was recorded for statement(s) "
            + ", ".join(undispositioned_statements)
        )
    if missing_batches:
        parts.append(
            "no review reached evidence batch(es) " + ", ".join(missing_batches)
        )
    return (
        "This review is incomplete: " + "; ".join(parts) + "."
        if parts
        else "This review returned no complete judgement."
    )


# --- the reviewer -----------------------------------------------------------


class ReportReviewer:
    """The tool-free terminal reviewer, configured as its own service role.

    Not a ``BaseAgent``: there is no ReAct loop, no toolset, no scratchpad, and
    no slot in the six-agent registry — the reviewer makes exactly one kind of
    request and holds no conversation. It still fingerprints that request the
    way every agent fingerprints its own, so an artifact records the model,
    effort, prompt version, and output budget the judgement was made under.
    """

    name: ClassVar[str] = REPORT_JUDGE_ROLE
    description: ClassVar[str] = (
        "Judge the finished report against the evidence it rests on."
    )
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    prompt_version: ClassVar[str] = REPORT_REVIEW_PROMPT_VERSION

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker | None = None,
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
    ) -> None:
        self._provider = provider
        self._tracker = tracker
        self._config = config or AgentRuntimeConfig()
        self._model_profile = model_profile
        self._call_fingerprints: dict[str, str] = {}
        self._review_records: list[ResearchError] = []

    @property
    def provider(self) -> StructuredCompleter:
        return self._provider

    @property
    def config(self) -> AgentRuntimeConfig:
        return self._config

    @property
    def model_profile(self) -> EffectiveModelConfig | None:
        return self._model_profile

    @property
    def call_fingerprints(self) -> dict[str, str]:
        """One configuration fingerprint per kind of request this reviewer made."""
        return dict(self._call_fingerprints)

    def fingerprint_call(
        self,
        label: str,
        *,
        output_limit: int | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Fingerprint one review request and record it, as an agent does.

        A per-call ``reasoning_effort`` override stands in for the profile's
        value when one is passed, exactly as it does for an agent's call.
        """
        profile = self._model_profile
        effort = (
            reasoning_effort
            if reasoning_effort is not None
            else ("unresolved" if profile is None else profile.reasoning_effort)
        )
        value = call_configuration_fingerprint(
            agent_name=self.name,
            model="unresolved" if profile is None else profile.model,
            thinking_mode=(
                "unresolved" if profile is None else profile.thinking_mode
            ),
            reasoning_effort=effort,
            output_limit=output_limit,
            context_limit=self._config.prompt_context_entries,
            schema_name=label,
            prompt_version=self.prompt_version,
        )
        self._call_fingerprints[label] = value
        return value

    async def review(
        self,
        packet: ReportReviewInput,
        *,
        previous: ReportReview | None = None,
    ) -> ReportReview:
        """Judge one packet, or reuse a scored review of the same material.

        Reuse is by fingerprint and nothing else: a stored review of different
        content is not a review of this report, and a stored review that is not
        ``scored`` is not a review at all. There is no second attempt loop
        here — one request per batch, and the outcome is recorded — but a
        request the provider truncated is re-asked once, and
        ``review_records`` reports what that cost.
        """
        self._review_records = []
        if (
            previous is not None
            and previous.status == "scored"
            and previous.input_fingerprint == packet.fingerprint
        ):
            return previous
        return await review_report(
            self._provider,
            packet,
            tracker=self._tracker,
            reviewer=self,
        )

    @property
    def review_records(self) -> tuple[ResearchError, ...]:
        """The records the most recent review produced, provider-free.

        Empty for a review that needed no retry — including one this reviewer
        reused instead of making — so a consumer publishing these beside the
        review cannot report an operational fact about a call that never
        happened. A whole review produces at most one, the output-limit retry.
        """
        return tuple(self._review_records)

    async def _request(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
    ) -> Any:
        self.fingerprint_call(
            schema.__name__,
            output_limit=self._config.report_review_max_tokens,
        )
        try:
            reply = await self._structured_request(messages, schema)
        except ProviderOutputLimitError as error:
            reply = await self._re_ask_truncated(messages, schema, error)
        payload = (
            reply.model_dump(mode="python")
            if isinstance(reply, schema)
            else reply
        )
        # Re-validated whatever its Python type: a provider that returns a
        # payload returns a dict, and an adapter or test double can return an
        # object whose fields were never checked. One trustworthy validation
        # boundary means the *values* are checked, not just the transport.
        return schema.model_validate(payload)

    async def _structured_request(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        *,
        reasoning_effort: str | None = None,
    ) -> Any:
        """One structured request for this review, at this request's effort.

        ``reasoning_effort`` is the retry's own setting: ``None`` on every
        ordinary request, which keeps the request this reviewer has always sent
        byte-identical, and the shared retry effort on the second attempt. The
        output budget is the operation's configured cap on both.
        """
        return await self._provider.complete_structured(
            messages,
            schema,
            agent_name=self.name,
            max_tokens=self._config.report_review_max_tokens,
            reasoning_effort=reasoning_effort,
        )

    async def _re_ask_truncated(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        error: ProviderOutputLimitError,
    ) -> Any:
        """Re-ask a truncated review request once, or report it stayed truncated.

        The same allowance the Critic's review gets, for the same reason: a
        truncated reply is the one failure a different request can fix, so it
        is re-asked once under the same output budget at the effort that leaves
        more of that budget for the answer. A second truncation is re-raised to
        the caller, which is where this reviewer's non-fatal "no judgement
        exists" path lives — an unjudged report never ends the run.

        The raised copy is fresh telemetry rather than the caught object, so
        the traceback this reaches the ledger through does not carry the
        provider response the truncation was detected on.
        """
        try:
            reply = await self._structured_request(
                messages, schema, reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT
            )
        except ProviderOutputLimitError as retry_error:
            self._review_records.append(
                report_review_output_limit_retry(
                    error,
                    schema=schema.__name__,
                    reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
                    max_tokens=self._config.report_review_max_tokens,
                    outcome="truncated",
                )
            )
            raise retry_error.redacted_copy(
                ProviderOutputLimitError.SAFE_MESSAGE
            ) from None
        self._review_records.append(
            report_review_output_limit_retry(
                error,
                schema=schema.__name__,
                reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
                max_tokens=self._config.report_review_max_tokens,
                outcome="answered",
            )
        )
        return reply


async def review_report(
    provider: StructuredCompleter,
    packet: ReportReviewInput,
    *,
    tracker: Tracker | None = None,
    config: AgentRuntimeConfig | None = None,
    model_profile: EffectiveModelConfig | None = None,
    reviewer: ReportReviewer | None = None,
    reviewed_fingerprint: str | None = None,
) -> ReportReview:
    """Run one complete semantic review of ``packet`` and record its outcome.

    The review is whole-report: one cross-section request carrying the complete
    report, every statement, every target, and every evidence batch; then one
    follow-up request for each batch the cross-section reply did not claim to
    have read. The report is never re-requested and never clipped, and a batch
    that never came back leaves the review ``incomplete`` with the omitted ids
    on the record.

    A provider failure is ``provider_failed``; a reply that is malformed, that
    breaks the defect contract, or that cites a record this packet does not
    carry is ``incomplete``. Every one of those carries no dimension scores,
    so none of them can be averaged into an acceptance, and ``tracker`` is
    optional only so a unit test can call this without a session span.
    """
    owner = reviewer or ReportReviewer(
        provider=provider, tracker=tracker, config=config, model_profile=model_profile
    )

    async def _run() -> ReportReview:
        return await _review_packet(
            owner, packet, reviewed_fingerprint=reviewed_fingerprint
        )

    if owner._tracker is None:  # noqa: SLF001  (the reviewer's own tracker)
        return await _run()
    async with owner._tracker.agent_span(owner.name):  # noqa: SLF001
        return await _run()


async def _review_packet(
    reviewer: ReportReviewer,
    packet: ReportReviewInput,
    *,
    reviewed_fingerprint: str | None,
) -> ReportReview:
    """The one review flow, separate so the span wraps all of it."""
    if not packet.reader_content.strip():
        return _merge_review(
            packet,
            dimension_scores=None,
            dispositions={},
            defects=[],
            derived_statements=[],
            reviewed_statement_ids=[],
            reviewed_evidence_ids=[],
            reviewed_batch_ids=[],
            rationale=(
                "There is no reader content to review, so no judgement of the "
                "report exists."
            ),
            status="incomplete",
        )
    if reviewed_fingerprint is not None and reviewed_fingerprint != packet.fingerprint:
        return _merge_review(
            packet,
            dimension_scores=None,
            dispositions={},
            defects=[],
            derived_statements=[],
            reviewed_statement_ids=[],
            reviewed_evidence_ids=[],
            reviewed_batch_ids=[],
            rationale=(
                "The packet changed under this review: the material judged is "
                f"not the material the review was opened on "
                f"({reviewed_fingerprint} != {packet.fingerprint})."
            ),
            status="incomplete",
        )

    dimensions: dict[str, float] | None = None
    disposition_drafts: list[StatementDispositionDraft] = []
    defect_drafts: list[CritiqueGapDraft] = []
    reviewed_statements: list[str] = []
    reviewed_evidence: list[str] = []
    reviewed_batches: list[str] = []
    rationales: list[str] = []

    try:
        cross = await reviewer._request(  # noqa: SLF001
            review_messages(packet), ReportReviewDraft
        )
    except ProviderError as error:
        return _failed_review(packet, _provider_reason(error))
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(packet, _schema_reason(error), status="incomplete")
    except ReportReviewContractViolation as violation:
        return _failed_review(packet, str(violation), status="incomplete")

    dimensions = cross.dimensions.as_dimensions()
    disposition_drafts.extend(cross.statement_dispositions)
    defect_drafts.extend(cross.defects)
    reviewed_statements.extend(cross.reviewed_statement_ids)
    reviewed_evidence.extend(cross.reviewed_evidence_ids)
    rationales.append(cross.rationale)
    covered_evidence = set(reviewed_evidence)
    for batch in packet.evidence_batches:
        if set(batch.evidence_ids).issubset(covered_evidence):
            reviewed_batches.append(batch.batch_id)

    for batch in packet.evidence_batches:
        if batch.batch_id in reviewed_batches:
            continue
        try:
            reply = await reviewer._request(  # noqa: SLF001
                batch_review_messages(packet, batch), ReviewBatchDraft
            )
        except ProviderError as error:
            return _failed_review(packet, _provider_reason(error))
        except (StructuredOutputError, ValidationError) as error:
            return _failed_review(packet, _schema_reason(error), status="incomplete")
        except ReportReviewContractViolation as violation:
            return _failed_review(packet, str(violation), status="incomplete")
        disposition_drafts.extend(reply.statement_dispositions)
        defect_drafts.extend(reply.defects)
        reviewed_statements.extend(reply.reviewed_statement_ids)
        reviewed_evidence.extend(reply.reviewed_evidence_ids)
        if reply.problem.strip():
            rationales.append(reply.problem)
        if set(batch.evidence_ids).issubset(set(reply.reviewed_evidence_ids)):
            reviewed_batches.append(batch.batch_id)

    try:
        dispositions = _dispositions(disposition_drafts, packet=packet)
        defects = _resolved_defects(defect_drafts, packet=packet)
    except ReportReviewContractViolation as violation:
        return _failed_review(packet, str(violation), status="incomplete")

    derived, derived_statements = _derived_defects(packet, dispositions, defects)
    status = _status_for(
        packet,
        dimension_scores=dimensions,
        reviewed_statement_ids=reviewed_statements,
        reviewed_batch_ids=reviewed_batches,
        dispositions=dispositions,
    )
    rationale = " ".join(rationales).strip() or (
        "The review returned no rationale."
    )
    if status != "scored":
        rationale = (
            " ".join(
                (
                    rationale,
                    _incomplete_reason(
                        packet,
                        reviewed_statement_ids=reviewed_statements,
                        reviewed_batch_ids=reviewed_batches,
                        dispositions=dispositions,
                    ),
                )
            )
        ).strip()
        dimensions = None
    try:
        return _merge_review(
            packet,
            dimension_scores=dimensions,
            dispositions=dispositions,
            defects=[*defects, *derived],
            derived_statements=derived_statements,
            reviewed_statement_ids=reviewed_statements,
            reviewed_evidence_ids=reviewed_evidence,
            reviewed_batch_ids=reviewed_batches,
            rationale=rationale,
            status=status,
        )
    except ValidationError as error:
        # The last boundary: assembling the record is the one remaining step
        # that can fail on the record contract, and this function is called
        # from a graph node with no exit code for it — a ``ValidationError``
        # raised here reached the CLI as an uncaught traceback, with the
        # finished report never published and neither exit 4 nor exit 3. A
        # judgement that cannot be recorded is recorded as *absent* instead:
        # ``incomplete``, no dimensions, the reason on the rationale, which is
        # the mandated quality-assessment failure and still routes nowhere.
        # The reply's own drafts are not the record's problem, so the review is
        # rebuilt from the packet rather than from the failed assembly.
        return _failed_review(
            packet,
            (
                "The review could not be recorded: the record contract refused "
                f"the assembled review ({type(error).__name__}). No judgement "
                "of this report is stored."
            ),
            status="incomplete",
        )


def _failed_review(
    packet: ReportReviewInput,
    reason: str,
    *,
    status: str = "provider_failed",
) -> ReportReview:
    """A review that could not be made: an explicit absence, never a score."""
    return _merge_review(
        packet,
        dimension_scores=None,
        dispositions={},
        defects=[],
        derived_statements=[],
        reviewed_statement_ids=[],
        reviewed_evidence_ids=[],
        reviewed_batch_ids=[],
        rationale=reason,
        status=status,
    )


def _provider_reason(error: Exception) -> str:
    return (
        "The review could not be made: the model provider failed "
        f"({type(error).__name__}). No judgement of this report exists."
    )


def _schema_reason(error: Exception) -> str:
    return (
        "The review could not be recorded: the reply did not satisfy the "
        f"review contract ({type(error).__name__}). No judgement of this "
        "report exists."
    )


def review_defects_as_refinement_jobs(
    review: ReportReview | None,
) -> Iterator[CritiqueGap]:
    """The material defects of a scored review, in the order it returned them.

    Only a ``scored`` review routes: an incomplete or provider-failed review
    judged nothing, so acting on its (empty) defect list as "no defects" is the
    one reading that must never happen — and it cannot, because this yields
    nothing only for a review that found nothing.
    """
    if review is None or review.status != "scored":
        return iter(())
    return iter(review.material_defects)


__all__ = [
    "DIMENSION_GUIDANCE",
    "MAX_REVIEW_DEFECTS",
    "REPORT_JUDGE_ROLE",
    "REPORT_REVIEW_EVIDENCE_BATCH_CHARS",
    "REPORT_REVIEW_INSTRUCTION",
    "REPORT_REVIEW_MAX_TOKENS",
    "REPORT_REVIEW_PROMPT_VERSION",
    "REPORT_REVIEW_SYSTEM_PROMPT",
    "REVIEW_DIMENSIONS",
    "REVIEW_RUBRIC_VERSION",
    "SEMANTIC_REVIEW_MEAN",
    "ReportReviewContractViolation",
    "ReportReviewDraft",
    "ReportReviewInput",
    "ReportReviewer",
    "ReviewBatchDraft",
    "ReviewClaimView",
    "ReviewDeterministic",
    "ReviewDimensionScores",
    "ReviewEvidenceBatch",
    "ReviewEvidenceItem",
    "ReviewRankedRow",
    "ReviewSourceView",
    "ReviewTargetView",
    "StatementDispositionDraft",
    "batch_review_messages",
    "build_report_review_input",
    "composition_semantic_fingerprint",
    "report_review_input_fingerprint",
    "review_defects_as_refinement_jobs",
    "review_messages",
    "review_report",
    "review_source_view",
    "semantic_review_passes",
]
