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
    ContractModel,
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
    """One scripted graph pass and the evidence it emits."""

    iteration: int = Field(ge=0)
    findings: list[Finding] = Field(default_factory=list)
    sources: list[ScoredSource] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    critic_targets: list[str] = Field(default_factory=list)
    new_evidence_topics: list[str] = Field(default_factory=list)
    force_refinement: bool = False


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
    """A bounded judge verdict; no judge prose is needed by the gates."""

    score: float = Field(ge=0.0, le=1.0)
    dimensions: dict[str, float] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=1_000)
    rubric: WholeReportRubric = Field(default_factory=WholeReportRubric)


class DeterministicEvaluation(ContractModel):
    """Every deterministic metric and the hard failures it produced."""

    planned_topics: int = Field(ge=0)
    attempted_topics: int = Field(ge=0)
    covered_topics: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
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
    publication_events: int = Field(ge=0)
    report_writes: int = Field(ge=0)
    evidence_writes: int = Field(ge=0)
    memory_writes: int = Field(ge=0)
    cli_summary_matches: bool
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
    cli_summary: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: CampaignMetadata
    langsmith_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.deterministic.integrity_passed and self.judge.score >= 0.80


class CaseCampaignResult(ContractModel):
    case_id: str
    repetitions: list[CampaignRepetition] = Field(min_length=1)
    mean_coverage: float = Field(ge=0.0, le=1.0)
    mean_judge_score: float = Field(ge=0.0, le=1.0)
    accepted: bool
    hard_failures: list[str] = Field(default_factory=list)
    artifact_path: str | None = None


class CampaignResult(ContractModel):
    """Round-trippable whole-report campaign artifact."""

    campaign_id: str = Field(min_length=1)
    tier: Literal["controlled", "live"]
    repetitions: int = Field(ge=1)
    cases: list[CaseCampaignResult] = Field(min_length=1)
    accepted: bool
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_path: str | None = None
    langsmith_metadata: dict[str, JsonValue] = Field(default_factory=dict)


# Descriptive aliases keep the public vocabulary focused on the whole-report
# campaign while the shorter internal names remain convenient in the runner.
WholeReportCase = ControlledCase
WholeReportRepetition = CampaignRepetition
WholeReportCaseResult = CaseCampaignResult
WholeReportResult = CampaignResult
