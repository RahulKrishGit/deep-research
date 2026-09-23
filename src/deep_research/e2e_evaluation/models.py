"""Typed contracts for the controlled whole-report quality campaign.

The campaign deliberately has its own contracts.  The individual-agent
evaluation package remains responsible for agent trajectories, while this
module describes the cross-agent report, its evidence ledger, and the bounded
judge hand-off.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from deep_research.utils.types import (
    Claim,
    ClaimCluster,
    ContractModel,
    EvidenceUnit,
    Finding,
    ReportComposition,
    ResearchState,
    ScoredSource,
    SubTopic,
)

CAMPAIGN_SCHEMA_VERSION = 1
CASE_SCHEMA_VERSION = 1
CASE_REGISTRY_VERSION = 1
REPORT_SCHEMA_VERSION = 1
QUALITY_GATE_VERSION = 1

AGENT_NAMES: tuple[str, ...] = (
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
)


class SnapshotPass(ContractModel):
    """One scripted graph pass and the evidence it emits.

    ``claim_clusters`` and ``evidence_units`` are the registries the
    production agents persist beside these snapshots — the Researcher writes
    the units of the reads it admitted, the Fact Checker the clusters its
    claims joined — and the Synthesizer copies both into the composition it
    composes. A scripted pass emits them for the same reason it emits claims:
    without them a reader statement can name no cluster, so it can answer no
    obligation, and a fixture that declares one could never show it answered.
    """

    iteration: int = Field(ge=0)
    findings: list[Finding] = Field(default_factory=list)
    sources: list[ScoredSource] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    critic_targets: list[str] = Field(default_factory=list)
    new_evidence_topics: list[str] = Field(default_factory=list)
    force_refinement: bool = False
    claim_clusters: dict[str, ClaimCluster] = Field(default_factory=dict)
    evidence_units: dict[str, EvidenceUnit] = Field(default_factory=dict)


class EvidenceLedgerSummary(ContractModel):
    """Bounded, content-light evidence context allowed to reach a judge."""

    source_count: int = Field(ge=0)
    scored_source_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    verified_claim_count: int = Field(ge=0)
    contradicted_claim_count: int = Field(ge=0)
    verification_passage_count: int = Field(ge=0)
    duplicate_source_rows: int = Field(ge=0)
    duplicate_claims: int = Field(ge=0)
    source_titles: list[str] = Field(default_factory=list, max_length=16)
    claim_summaries: list[str] = Field(default_factory=list, max_length=16)


class WholeReportJudgeInput(ContractModel):
    """The only input shape a whole-report judge may receive.

    There is intentionally no provider transcript, tool payload, secret,
    hidden reasoning, or raw trajectory field in this contract.
    """

    question: str = Field(min_length=1, max_length=2_000)
    scoped_plan: list[SubTopic] = Field(min_length=1, max_length=7)
    reader_report: str = Field(min_length=1, max_length=16_000)
    deterministic_metrics: dict[str, float | int | bool] = Field(
        min_length=1, max_length=64
    )
    evidence_ledger_summary: EvidenceLedgerSummary

    @model_validator(mode="after")
    def forbid_unbounded_or_unsafe_metrics(self) -> "WholeReportJudgeInput":
        for key in self.deterministic_metrics:
            if key.casefold() in {
                "secret",
                "api_key",
                "raw_provider_output",
                "tool_payload",
                "hidden_reasoning",
                "chain_of_thought",
            }:
                raise ValueError("judge metrics contain a prohibited field")
        return self


class WholeReportRubric(ContractModel):
    """Bounded reader-facing dimensions for a whole-report judge."""

    rubric_id: str = "whole-report-quality"
    version: int = Field(default=1, ge=1)
    dimensions: dict[str, str] = Field(
        default_factory=lambda: {
            "completeness": "Answers every planned topic and names gaps.",
            "prioritization": "Ranks mechanisms and decisions clearly.",
            "evidence_quality": "Uses read, scored, provenance-bearing evidence.",
            "attribution": "Links settled points to the sources supporting them.",
            "uncertainty": "Discloses contradiction and evidence limits.",
            "readability": "Keeps the reader report concise and navigable.",
            "actionability": "Makes implications and decisions clear.",
        },
        min_length=7,
        max_length=7,
    )


class WholeReportJudgeScore(ContractModel):
    """A bounded judge verdict; no judge prose is needed by the gates.

    ``structural_only`` is not decoration. The whole-report judge this contract
    was written for computes its seven dimensions from counts, ratios, and
    keywords — a length band for "readability", the presence of decision
    language for "actionability", and ratios a hard integrity gate already
    requires to be 1.0 for two more. It stays available because historical
    campaign artifacts carry its scores and re-scoring them would make old and
    new results incomparable, and it is labelled here so no consumer can read
    it as an independent judgement of the report's substance. Task 10's
    semantic review (``SemanticReviewSummary``) is that judgement, and
    production imports neither of these — they are evaluation diagnostics.
    """

    score: float = Field(ge=0.0, le=1.0)
    dimensions: dict[str, float] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=1_000)
    rubric: WholeReportRubric = Field(default_factory=WholeReportRubric)
    structural_only: bool = True


class SemanticReviewSummary(ContractModel):
    """The harness's reading of one terminal semantic report review.

    The evaluation mirror of ``utils.types.ReportReview``, and deliberately
    lossless where the review's own counts matter: every dimension, every
    defect by severity, the statements and evidence the review covered, and the
    fingerprint it judged. A summary that dropped the defect list would report
    a review of a report with a critical false claim as a high-scoring pass —
    which is the defect this record exists to make visible in the harness, and
    the reason ``accepted`` is computed by the review's own rule rather than
    from the mean alone.
    """

    rubric_version: int = Field(default=1, ge=1)
    status: str = Field(default="", min_length=0)
    """``scored`` / ``incomplete`` / ``provider_failed``, or ``""`` for none."""
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    accepted: bool = False
    dimensions: dict[str, float] = Field(default_factory=dict)
    defect_count: int = Field(default=0, ge=0)
    material_defect_count: int = Field(default=0, ge=0)
    derived_defect_count: int = Field(default=0, ge=0)
    reviewed_statement_ids: list[str] = Field(default_factory=list)
    unreviewed_statement_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_ids: list[str] = Field(default_factory=list)
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    expected_batch_ids: list[str] = Field(default_factory=list)
    reviewed_batch_ids: list[str] = Field(default_factory=list)
    input_fingerprint: str = ""
    coverage_complete: bool = False

    @property
    def scored(self) -> bool:
        return self.status == "scored"

    @property
    def missing(self) -> bool:
        """True when no judgement exists at all.

        The distinct case a metric must never round to a pass: an absent
        review is not a review that found nothing.
        """
        return self.status != "scored"


class DeterministicEvaluation(ContractModel):
    """Every deterministic metric and the hard failures it produced."""

    planned_topics: int = Field(ge=0)
    attempted_topics: int = Field(ge=0)
    covered_topics: int = Field(ge=0)
    """Topics every counted obligation of which was answered (Section 2.3).

    The substantive reading, taken from the quality snapshot's own
    ``measured_covered_topics``, because that is the measurement the product's
    broad-plan gate and this campaign both judge. On a plan declaring no
    counted obligation at all it is the claimed count, which is the reading
    ``compute_report_quality`` falls back to on the same shape.
    """
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    """``covered_topics`` over ``planned_topics`` — the ratio the gates read."""
    claimed_covered_topics: int = Field(default=0, ge=0)
    """Topics some claim recorded consuming, whether or not they were answered.

    Published beside the graded count rather than instead of it: a run whose
    claim consumed a topic it never answered is exactly the case the two
    readings disagree about, and the record has to show both.
    """
    claimed_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    """``claimed_covered_topics`` over ``planned_topics``.

    The formatter prints this one on its Quality line, so the CLI comparison
    is made against it; it is a diagnostic, never the graded ratio.
    """
    read_sources: int = Field(ge=0)
    cited_sources: int = Field(ge=0)
    scored_cited_sources: int = Field(ge=0)
    source_read_provenance_ratio: float = Field(ge=0.0, le=1.0)
    checked_claims: int = Field(ge=0)
    claims_with_provenance: int = Field(ge=0)
    checked_claim_provenance_ratio: float = Field(ge=0.0, le=1.0)
    resolved_citations: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    citation_linkage_ratio: float = Field(ge=0.0, le=1.0)
    duplicate_claims: int = Field(ge=0)
    duplicate_source_rows: int = Field(ge=0)
    duplicate_finding_rows: int = Field(default=0, ge=0)
    contradicted_claims: int = Field(ge=0)
    disclosed_contradictions: int = Field(ge=0)
    uncited_settled_points: int = Field(ge=0)
    reader_report_words: int = Field(ge=0)
    evidence_ledger_words: int = Field(ge=0)
    reader_report_chars: int = Field(ge=0)
    evidence_ledger_chars: int = Field(ge=0)
    refinement_passes: int = Field(ge=1)
    critic_targets: int = Field(ge=0)
    closed_critic_targets: int = Field(ge=0)
    new_evidence_in_refinement: int = Field(ge=0)
    repeated_source_snapshot_passes: int = Field(ge=0)
    repeated_claim_snapshot_passes: int = Field(ge=0)
    gate_forced_refinement_passes: int = Field(ge=0)
    # A gate-forced refinement pass that the Critic gave no target for. Not a
    # failure — the gate is the graph's own verdict and the broad case exists
    # to exercise it — but a pass that spends budget and cannot be aimed at
    # anything, so the artifact records it rather than accepting it silently.
    targetless_gate_forced_refinements: int = Field(default=0, ge=0)
    publication_events: int = Field(ge=0)
    report_writes: int = Field(ge=0)
    evidence_writes: int = Field(ge=0)
    memory_writes: int = Field(ge=0)
    cli_summary_matches: bool
    rendered_citation_resolution: bool = True
    # Whether the state this verdict came from carried the production graph's
    # own node events. When it did not, every *observed* metric leg was
    # substituted from the case fixture and is true by construction, so the
    # verdict has to say which branch ran.
    graph_observed: bool = True
    integrity_failures: list[str] = Field(default_factory=list)
    hard_failures: list[str] = Field(default_factory=list)

    @property
    def integrity_passed(self) -> bool:
        return not self.integrity_failures and not self.hard_failures


class CampaignMetadata(ContractModel):
    """Reproducibility fields written to local and trace-shaped metadata."""

    campaign_schema_version: int = CAMPAIGN_SCHEMA_VERSION
    case_schema_version: int = CASE_SCHEMA_VERSION
    case_registry_version: int = CASE_REGISTRY_VERSION
    report_schema_version: int = REPORT_SCHEMA_VERSION
    quality_gate_version: int = QUALITY_GATE_VERSION
    graph_revision: str = Field(min_length=1)
    target_prompt_fingerprints: dict[str, str] = Field(min_length=6, max_length=6)
    target_model: str = Field(min_length=1)
    target_reasoning_effort: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    judge_reasoning_effort: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    tier: Literal["controlled", "live"]
    repetition: int = Field(ge=1)
    request_counts: dict[str, int] = Field(default_factory=dict)


class ExpectedResult(ContractModel):
    """The product result one controlled case declares it should produce.

    A declared result and a passing run are two different facts, and the two
    this contract keeps apart are "the campaign accepted this report" and
    "this case produced the result it declares". A case whose subject is a
    refusal — a plan that declared an obligation nothing answered — declares a
    partial result, and a suite that demanded every row be accepted could not
    hold such a case at all: it would have to read a correct run as a failure.

    ``required_failures`` are the legs the run must record, which is the case's
    own assertion: a case exists to show a named way the run fell short, so a
    run that stopped recording it fails here rather than passing quietly.
    ``allowed_failures`` are the legs this case's own fixture makes correct;
    nothing outside the two lists is tolerated, so a declared-partial row can
    never become a blanket exemption from the gates.
    """

    accepted: bool = True
    required_failures: list[str] = Field(default_factory=list)
    allowed_failures: list[str] = Field(default_factory=list)


class ControlledCase(ContractModel):
    """One graph-shaped scripted case, with no external dependencies."""

    case_id: str = Field(min_length=1)
    version: int = Field(default=CASE_SCHEMA_VERSION, ge=1)
    tier: Literal["controlled", "live"] = "controlled"
    title: str = Field(min_length=1)
    question: str = Field(min_length=1)
    sub_topics: list[SubTopic] = Field(min_length=1, max_length=7)
    passes: list[SnapshotPass] = Field(min_length=1)
    network_zero: bool = True
    scripted_dependencies: bool = True
    authorization_required: bool = False
    expected_contradictions: int = Field(default=0, ge=0)
    expected_refinement_topics: list[str] = Field(default_factory=list)
    expected_result: ExpectedResult = Field(default_factory=ExpectedResult)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    def first_pass(self) -> SnapshotPass:
        return self.passes[0]

    def final_pass(self) -> SnapshotPass:
        return self.passes[-1]

    @property
    def repeated_snapshots(self) -> bool:
        if len(self.passes) < 2:
            return False
        first = self.passes[0]
        return any(
            pass_item.sources == first.sources and pass_item.claims == first.claims
            for pass_item in self.passes[1:]
        )


class CampaignRepetition(ContractModel):
    """One deterministic repetition of one case."""

    case_id: str
    repetition: int = Field(ge=1)
    state: ResearchState
    composition: ReportComposition
    report: str = Field(min_length=1)
    evidence_ledger: str = Field(min_length=1)
    deterministic: DeterministicEvaluation
    judge: WholeReportJudgeScore
    semantic_review: SemanticReviewSummary | None = None
    """The terminal semantic review this repetition's report was judged by.

    ``None`` for a historical repetition, or one whose run produced no review —
    which is exactly the case a reader has to be able to tell apart from a
    review that passed, so it is recorded as absent rather than defaulted to an
    empty summary.
    """
    cli_summary: dict[str, JsonValue] = Field(default_factory=dict)
    cli_output: list[str] = Field(default_factory=list)
    judge_input: WholeReportJudgeInput
    publication_operations: list[str] = Field(default_factory=list)
    metadata: CampaignMetadata
    langsmith_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.deterministic.integrity_passed and self.judge.score + 1e-9 >= 0.70


class CaseCampaignResult(ContractModel):
    case_id: str
    repetitions: list[CampaignRepetition] = Field(min_length=1)
    mean_coverage: float = Field(ge=0.0, le=1.0)
    mean_judge_score: float = Field(ge=0.0, le=1.0)
    accepted: bool
    """Every repetition of this case cleared the campaign's own gates.

    The product-level fact: this is what the gates said about the reports, and
    it is not the case's verdict. A case that declares a partial result has
    ``accepted`` False and ``met_expectation`` True.
    """
    expected_result: ExpectedResult = Field(default_factory=ExpectedResult)
    """The result this case declared, echoed into the artifact.

    Recorded rather than left in the registry: a result file whose verdict
    cannot be read beside the declaration it was judged against invites the
    reader to supply their own.
    """
    met_expectation: bool
    """Every repetition produced the result this case declares.

    Per repetition, not on average: a suite pass whose mean hides one
    repetition that produced something else is the reading this field exists
    to refuse.
    """
    hard_failures: list[str] = Field(default_factory=list)
    artifact_path: str | None = None


class CampaignResult(ContractModel):
    """Round-trippable whole-report campaign artifact."""

    campaign_id: str = Field(min_length=1)
    tier: Literal["controlled", "live"]
    repetitions: int = Field(ge=1)
    cases: list[CaseCampaignResult] = Field(min_length=1)
    accepted: bool
    """The suite's verdict: every row produced the result it declares.

    Not "every row was accepted": a suite holding a case whose subject is a
    refusal is a passing suite when that row produces the refusal. The
    stricter product-level fact is kept beside it as ``rows_accepted`` so the
    two never have to be read as one.
    """
    rows_accepted: bool = True
    """Every row was itself accepted by the campaign's gates."""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_path: str | None = None
    langsmith_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ReplayRepetitionResult(ContractModel):
    """One repetition of one real-agent replay row.

    This is the real-agent harness's repetition record, and it is deliberately
    not ``CampaignRepetition``: a replay runs the six production agents through
    the real graph and judges nothing with the whole-report judge, so a
    repetition forced into that contract would have to carry a judge score that
    was never computed. What it carries instead is what the run actually
    produced — the terminal quality, the exit code, the ways it fell short of
    its case's declared result — plus the two pieces of harness evidence the
    campaign's contracts have no place for: the socket connections the run
    attempted, and the fingerprint of the report it published.
    """

    case_id: str
    repetition: int = Field(ge=1)
    session_id: str
    terminal_quality: str
    exit_code: int
    expectation_failures: list[str] = Field(default_factory=list)
    answered_target_ids: list[str] = Field(default_factory=list)
    network_attempts: list[str] = Field(default_factory=list)
    """Every socket connect this repetition attempted. Empty is the evidence.

    A suite that cannot show this empty on every repetition is not accepted:
    the harness exists to be network-zero, so an attempt is a failure of the
    proof rather than a warning about it.
    """
    report_fingerprint: str
    """The published report's hash, over its canonical form.

    Canonicalized because two things about a report are facts about the
    *session* that made it: the ``As of`` clock read, and which ordinal each
    reference was assigned. The fingerprint is what makes "the same result
    every time" a measurable claim rather than a hope.
    """


class ReplayCaseResult(ContractModel):
    """One declared replay row, and the repetitions that ran it."""

    case_id: str
    version: int = Field(ge=1)
    expected_product_result: str
    decisive_assertion: str
    repetitions: list[ReplayRepetitionResult] = Field(min_length=1)
    deterministic: bool
    """Whether every repetition produced one identical outcome."""
    passed: bool
    """Whether every repetition met its case's declared result."""
    artifact_path: str | None = None


class ReplaySuiteResult(ContractModel):
    """Round-trippable real-agent suite artifact.

    ``mode`` names the harness that ran. Both values are declared because the
    axis is the harness, not the result shape: a suite run says which one
    produced it, so evidence from scripted doubles can never be read as
    evidence from the production agents.
    """

    campaign_id: str = Field(min_length=1)
    tier: Literal["controlled"]
    mode: Literal["real-agent", "graph-historical"]
    manifest_version: int = Field(ge=1)
    case_version: int = Field(ge=1)
    repetitions: int = Field(ge=1)
    cases: list[ReplayCaseResult] = Field(min_length=1)
    accepted: bool
    """The suite's verdict: every row produced the result it declares.

    A real-agent row can declare a partial result — ``same-work-mirror`` does —
    and a suite that read that as a failure could not hold the negative half of
    its own matrix. The stricter, product-level fact is ``rows_accepted``.
    """
    rows_accepted: bool = True
    """Every repetition's own product result was an accepted one."""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_path: str | None = None


# Descriptive aliases keep the public vocabulary focused on the whole-report
# campaign while the shorter internal names remain convenient in the runner.
WholeReportCase = ControlledCase
WholeReportRepetition = CampaignRepetition
WholeReportCaseResult = CaseCampaignResult
WholeReportResult = CampaignResult
