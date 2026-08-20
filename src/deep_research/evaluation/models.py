"""Strict contracts for the individual-agent evaluation harness."""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import Field, JsonValue, model_validator

from deep_research.utils.config import ReasoningEffort
from deep_research.utils.types import ContractModel, ResearchState, UnitScore

ARTIFACT_SCHEMA_VERSION = 1

AgentName: TypeAlias = Literal[
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
]
EvaluationTier: TypeAlias = Literal["controlled", "live"]

AGENT_NAMES: tuple[AgentName, ...] = (
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
)
CLI_AGENT_NAMES: tuple[str, ...] = tuple(
    name.replace("_", "-") for name in AGENT_NAMES
)
TIERS: tuple[EvaluationTier, ...] = ("controlled", "live")

_CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class UnknownAgentError(ValueError):
    """A caller named an agent that does not exist."""


class UnknownTierError(ValueError):
    """A caller named a tier that does not exist."""


class UnknownCaseError(ValueError):
    """A caller named a case that is not in the registry."""


def parse_agent_name(value: str) -> AgentName:
    """Canonicalize a user-facing agent name to its internal key."""
    candidate = value.strip().casefold().replace("-", "_")
    if candidate not in AGENT_NAMES:
        valid = ", ".join(CLI_AGENT_NAMES)
        raise UnknownAgentError(
            f"unknown agent {value!r}; expected one of: {valid}"
        )
    return candidate  # type: ignore[return-value]


def cli_agent_name(name: AgentName) -> str:
    """The kebab-case name the CLI, datasets, and output paths use."""
    return name.replace("_", "-")


def parse_tier(value: str) -> EvaluationTier:
    candidate = value.strip().casefold()
    if candidate not in TIERS:
        raise UnknownTierError(
            f"unknown tier {value!r}; expected one of: controlled, live"
        )
    return candidate  # type: ignore[return-value]


class DeterministicMetric(ContractModel):
    """One weighted, case-specific deterministic quality metric."""

    metric_id: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0)
    description: str = Field(min_length=1)


class CaseExpectations(ContractModel):
    """Everything evaluators — never the agent — are allowed to know."""

    required_output_fields: list[str] = Field(min_length=1)
    reference: dict[str, JsonValue] = Field(default_factory=dict)
    known_source_urls: list[str] = Field(default_factory=list)
    max_iterations: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    deterministic_metrics: list[DeterministicMetric] = Field(min_length=1)
    must_record_recoverable_error: bool = False
    prohibited_tool_names: list[str] = Field(default_factory=list)
    required_live_dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_metrics(self) -> "CaseExpectations":
        ids = [metric.metric_id for metric in self.deterministic_metrics]
        if len(set(ids)) != len(ids):
            raise ValueError("deterministic metric ids must be unique")
        total = sum(metric.weight for metric in self.deterministic_metrics)
        # Tolerance, not equality: 0.6 + 0.4 is not exactly 1.0 in binary.
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"deterministic metric weights must sum to 1.0, got {total}"
            )
        return self


class RubricDimension(ContractModel):
    dimension_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    anchors: dict[str, str] = Field(min_length=1)


class JudgeRubric(ContractModel):
    """The agent-specific half of the judge rubric.

    The six common dimensions and their fixed weights live in
    ``judging.COMMON_DIMENSION_WEIGHTS``. A rubric adds observable anchors;
    it never changes a weight.
    """

    rubric_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    agent_dimensions: list[RubricDimension] = Field(default_factory=list)


class EvaluationCase(ContractModel):
    case_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    agent_name: AgentName
    tier: EvaluationTier
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    state: ResearchState
    dependency_scenario: str = Field(min_length=1)
    expectations: CaseExpectations
    judge_rubric: JudgeRubric
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_case_id(self) -> "EvaluationCase":
        if not _CASE_ID_PATTERN.match(self.case_id):
            raise ValueError(
                f"case_id {self.case_id!r} must be kebab-case "
                "(lowercase letters and digits joined by single hyphens)"
            )
        return self

    def fresh_state(self) -> ResearchState:
        """A deep copy, so no repetition observes another's mutations."""
        return self.state.model_copy(deep=True)

    @property
    def identity(self) -> tuple[str, int]:
        return (self.case_id, self.version)


class ToolCallSummary(ContractModel):
    tool_name: str = Field(min_length=1)
    calls: int = Field(ge=0)
    failures: int = Field(ge=0)


class DependencyLedger(ContractModel):
    """What this repetition actually touched.

    Controlled gates read ``prohibited_calls``; live gates read
    ``real_services_used``.
    """

    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    prohibited_calls: list[str] = Field(default_factory=list)
    real_services_used: list[str] = Field(default_factory=list)
    memory_writes: int = Field(default=0, ge=0)
    memory_reads: int = Field(default=0, ge=0)
    document_writes: int = Field(default=0, ge=0)


class TrajectoryStep(ContractModel):
    """One safe, redacted ReAct step summary the judge is shown."""

    iteration: int = Field(ge=0)
    thought: str = ""
    tool_name: str | None = None
    succeeded: bool | None = None
    observation_summary: str = ""


class EvidenceContext(ContractModel):
    """The source and evidence context made available to the agent."""

    sources: list[dict[str, JsonValue]] = Field(default_factory=list)
    findings: list[dict[str, JsonValue]] = Field(default_factory=list)
    claims: list[dict[str, JsonValue]] = Field(default_factory=list)
    scripted_search_urls: list[str] = Field(default_factory=list)


class ReActSummary(ContractModel):
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    stop_reason: str = Field(min_length=1)
    max_iterations: int = Field(ge=1)
    tool_budget: int = Field(ge=0)


FailureStage: TypeAlias = Literal[
    "setup",
    "construction",
    "provider",
    "tool",
    "validation",
    "judging",
    "trace",
    "artifact",
    "unhandled",
]


class EvaluationFailure(ContractModel):
    """A redacted, typed failure record. Never a raw provider exception."""

    stage: FailureStage
    reason: str = Field(min_length=1)
    message: str = Field(min_length=1)
    exception_type: str | None = None


class TargetOutput(ContractModel):
    """Everything one repetition produced, typed and already redacted."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    agent_name: AgentName
    tier: EvaluationTier
    repetition: int = Field(ge=1)
    session_id: str = Field(min_length=1)
    experiment_name: str = Field(min_length=1)
    trace_url: str | None = None
    completed: bool
    failure: EvaluationFailure | None = None
    result: dict[str, JsonValue] | None = None
    state_update: dict[str, JsonValue] = Field(default_factory=dict)
    errors: list[dict[str, JsonValue]] = Field(default_factory=list)
    tracker_errors: list[dict[str, JsonValue]] = Field(default_factory=list)
    react: ReActSummary | None = None
    dependencies: DependencyLedger = Field(default_factory=DependencyLedger)
    evidence: EvidenceContext = Field(default_factory=EvidenceContext)
    trajectory: list[TrajectoryStep] = Field(default_factory=list)
    target_model_requested: str = Field(min_length=1)
    target_model_returned: str | None = None
    target_reasoning_effort: ReasoningEffort
    thinking_mode: Literal["enabled"] = "enabled"

    @property
    def has_evaluable_output(self) -> bool:
        """True when the judge must run.

        A run that produced a typed result is evaluable even when a hard
        gate failed: the spec runs the judge on every repetition that
        produced evaluable output.
        """
        return self.result is not None


class GateResult(ContractModel):
    gate_id: str = Field(min_length=1)
    passed: bool
    detail: str = ""


class GateReport(ContractModel):
    results: list[GateResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def failed_ids(self) -> list[str]:
        return [item.gate_id for item in self.results if not item.passed]


class JudgeScores(ContractModel):
    """The six fixed common dimensions, all in ``[0.0, 1.0]``."""

    role_adherence: UnitScore
    completeness: UnitScore
    groundedness: UnitScore
    reasoning_quality: UnitScore
    usefulness: UnitScore
    uncertainty_calibration: UnitScore


class JudgeVerdict(ContractModel):
    scores: JudgeScores
    agent_specific: dict[str, UnitScore] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=2000)


JudgeStatus: TypeAlias = Literal["scored", "judge_not_run"]
JudgeNotRunReason: TypeAlias = Literal[
    "no_evaluable_output",
    "setup_failure",
    "unhandled_exception",
    "judge_provider_failure",
    "judge_schema_failure",
]


class JudgeFeedback(ContractModel):
    status: JudgeStatus
    not_run_reason: JudgeNotRunReason | None = None
    verdict: JudgeVerdict | None = None
    judge_quality: UnitScore | None = None
    prompt_id: str = Field(min_length=1)
    rubric_version: int = Field(ge=1)
    prompt_fingerprint: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    judge_configuration_fingerprint: str = Field(min_length=1)
    evaluator_trace_url: str | None = None
    evaluator_source_url: str | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> "JudgeFeedback":
        if self.status == "scored":
            if self.verdict is None or self.judge_quality is None:
                raise ValueError(
                    "a scored judge feedback requires a verdict and a score"
                )
            return self
        if self.not_run_reason is None:
            raise ValueError("judge_not_run requires a typed reason")
        if self.judge_quality is not None:
            raise ValueError(
                "judge_not_run must never carry a fabricated quality score"
            )
        return self


class RepetitionResult(ContractModel):
    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    repetition: int = Field(ge=1)
    completed: bool
    gates: GateReport
    deterministic_quality: UnitScore | None = None
    judge: JudgeFeedback | None = None
    aggregate_quality: UnitScore | None = None
    trace_url: str | None = None
    errors: list[EvaluationFailure] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.completed
            and self.gates.passed
            and self.aggregate_quality is not None
        )


class CaseResult(ContractModel):
    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    repetitions: list[RepetitionResult] = Field(min_length=1)
    average_quality: float | None = None
    passed: bool
    lowest_scoring_trace_url: str | None = None


EvaluationStatus: TypeAlias = Literal[
    "REVIEW REQUIRED", "FAILED", "INFRASTRUCTURE FAILURE"
]


class ExperimentResult(ContractModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    agent_name: AgentName
    tier: EvaluationTier
    experiment_name: str = Field(min_length=1)
    experiment_url: str | None = None
    dataset_name: str = Field(min_length=1)
    dataset_url: str | None = None
    cases: list[CaseResult] = Field(default_factory=list)
    status: EvaluationStatus
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    errors: list[EvaluationFailure] = Field(default_factory=list)

    @property
    def mean_quality(self) -> float | None:
        scores = [
            repetition.aggregate_quality
            for case in self.cases
            for repetition in case.repetitions
            if repetition.aggregate_quality is not None
        ]
        return sum(scores) / len(scores) if scores else None


class SuiteResult(ContractModel):
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    suite_id: str = Field(min_length=1)
    experiments: list[ExperimentResult] = Field(default_factory=list)
    status: EvaluationStatus
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
