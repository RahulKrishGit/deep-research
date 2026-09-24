"""Hard gates and deterministic quality scoring for every agent.

The general half applies to every agent: the deterministic hard gates
plus the weighted deterministic quality score. The agent-specific half
declares the per-agent gates (``AGENT_GATE_IDS``), every case metric
(``METRIC_FUNCTIONS``), and the LangSmith code-evaluator adapter
(``code_evaluator``). A rule shared between a gate and a metric is
implemented once as a private predicate and referenced from both, so the
two cannot drift apart.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from typing import TypeAlias

from langsmith.schemas import Example, Run
from pydantic import ValidationError

from deep_research.agents.critic import route_decision
from deep_research.agents.fact_checker import (
    claimed_domains_for,
    independent_domains,
)
from deep_research.agents.planner import earned_support_policy
from deep_research.agents.report import build_citation_index, collapse_mirror_urls
from deep_research.agents.sources import (
    normalize_source_url,
    publisher_identity,
    source_domain,
)
from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.config import contains_secret
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    GateReport,
    GateResult,
    TargetOutput,
)
from deep_research.utils.types import (
    MAX_TARGETS_PER_TOPIC,
    AtomicProposition,
    Claim,
    Critique,
    CritiqueGap,
    EvidenceTarget,
    ScoredSource,
    SubTopic,
    UnitScore,
    answered_required_dimensions,
    counted_evidence_targets,
)

GENERAL_GATE_IDS: tuple[str, ...] = (
    "agent_constructed",
    "run_completed",
    "contracts_valid",
    "required_fields_present",
    "budgets_respected",
    "errors_typed",
    "citations_known",
    "no_secret_in_output",
    "no_prohibited_calls",
    "trace_available",
    "no_tracker_transport_failure",
)

MetricFunction: TypeAlias = Callable[[TargetOutput, EvaluationCase], bool]

_RESEARCH_ERROR_KEYS = frozenset(
    {"error_type", "source", "message", "timestamp"}
)
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
_CITATION_MARKER_PATTERN = re.compile(r"\[(\d+)\]")
# Greedy URL matching keeps trailing punctuation that belongs to prose
# (markdown parens, commas, periods, ...). Strip it so the extracted string
# compares equal to the canonical ``known_source_urls`` entry.
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}'\"'"
_TRANSPORT_FAILURE_TYPE = "langsmith_tracing_failure"
_PROVIDER_FAILURE_KINDS = frozenset(
    {
        "output_limit",
        "schema_output",
        "provider_timeout",
        "provider_rate_limit",
        "provider_transport",
        "provider_http",
        "provider_response",
        "provider_failure",
    }
)


class MissingMetricError(RuntimeError):
    """A case declares a deterministic metric with no implementation."""


def _field(value: object, name: str) -> object:
    """Read one field from a contract model or a plain dict.

    ``model_copy(update=...)`` does not re-validate, so a fixture can hold
    a raw dict where ``models.py`` declares a model; both shapes must be
    readable without assuming which one arrived.
    """
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _url_like_strings(payload: object) -> set[str]:
    """Every URL-looking substring, recursively, in ``payload``.

    Trailing punctuation is trimmed off each match so a URL embedded in
    prose (``...report).``) compares equal to the bare canonical entry;
    ``rstrip`` removes stacked punctuation in one pass.
    """
    found: set[str] = set()
    if isinstance(payload, str):
        found.update(
            url.rstrip(_URL_TRAILING_PUNCTUATION)
            for url in _URL_PATTERN.findall(payload)
        )
    elif isinstance(payload, Mapping):
        for item in payload.values():
            found.update(_url_like_strings(item))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.update(_url_like_strings(item))
    return found


def _gate_agent_constructed(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    stage = _field(output.failure, "stage")
    passed = stage is None or stage != "construction"
    return GateResult(
        gate_id="agent_constructed",
        passed=passed,
        detail="" if passed else "agent construction failed",
    )


def _gate_run_completed(output: TargetOutput, case: EvaluationCase) -> GateResult:
    if output.completed is not True:
        return GateResult(
            gate_id="run_completed",
            passed=False,
            detail="run did not complete",
        )
    if output.failure is not None:
        return GateResult(
            gate_id="run_completed",
            passed=False,
            detail="run ended in a recorded failure",
        )
    return GateResult(gate_id="run_completed", passed=True, detail="")


def _gate_contracts_valid(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    problems: list[str] = []
    if not isinstance(output.result, Mapping):
        problems.append("result is not a mapping")
    if not isinstance(output.state_update, Mapping):
        problems.append("state_update is not a mapping")
    return GateResult(
        gate_id="contracts_valid",
        passed=not problems,
        detail="; ".join(problems),
    )


def _gate_required_fields_present(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    result = output.result if isinstance(output.result, Mapping) else {}
    state_update = (
        output.state_update if isinstance(output.state_update, Mapping) else {}
    )
    missing = [
        name
        for name in case.expectations.required_output_fields
        if name not in result and name not in state_update
    ]
    return GateResult(
        gate_id="required_fields_present",
        passed=not missing,
        detail=(
            "missing required fields: " + ", ".join(missing) if missing else ""
        ),
    )


def _gate_budgets_respected(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    """Check the budgets the runtime actually enforces, per loop.

    ``tool_budget`` and ``max_iterations`` bound **one** ReAct loop, and an
    agent that runs one bounded loop per unit of work merges them: the
    fact-checker runs a loop per claim, so its ``tool_calls`` is a whole-case
    sum that can legitimately exceed a per-loop ceiling (two in-budget loops of
    6 and 5 sum to 11). This gate therefore compares the largest single loop's
    own totals, which ``ReActSummary.max_loop_*`` carries, and falls back to
    the summed totals for artifacts written before those fields existed. The
    detail reports the value that decided the outcome, plus the whole-case
    total when it differs, so a failure still names the number to diagnose.
    """
    react = output.react
    if react is None:
        return GateResult(
            gate_id="budgets_respected",
            passed=False,
            detail="react summary missing; budget cannot be assessed",
        )
    iterations = _field(react, "iterations")
    tool_calls = _field(react, "tool_calls")
    # ``None`` means the artifact predates the per-loop fields, not zero.
    per_loop_iterations = _field(react, "max_loop_iterations")
    per_loop_tool_calls = _field(react, "max_loop_tool_calls")
    measured_iterations = (
        per_loop_iterations if isinstance(per_loop_iterations, int) else iterations
    )
    measured_tool_calls = (
        per_loop_tool_calls if isinstance(per_loop_tool_calls, int) else tool_calls
    )
    violations: list[str] = []
    if (
        not isinstance(measured_iterations, int)
        or measured_iterations > case.expectations.max_iterations
    ):
        violations.append(
            f"iterations {measured_iterations} exceed "
            f"{case.expectations.max_iterations}"
            f"{_budget_total_note(iterations, measured_iterations)}"
        )
    if (
        not isinstance(measured_tool_calls, int)
        or measured_tool_calls > case.expectations.max_tool_calls
    ):
        violations.append(
            f"tool_calls {measured_tool_calls} exceed "
            f"{case.expectations.max_tool_calls}"
            f"{_budget_total_note(tool_calls, measured_tool_calls)}"
        )
    return GateResult(
        gate_id="budgets_respected",
        passed=not violations,
        detail="; ".join(violations),
    )


def _budget_total_note(total: object, measured: object) -> str:
    """Name the whole-case total too, when it differs from the per-loop value."""
    if not isinstance(total, int) or total == measured:
        return ""
    return f" (largest single loop; whole case total {total})"


def _gate_errors_typed(output: TargetOutput, case: EvaluationCase) -> GateResult:
    untyped = [
        index
        for index, entry in enumerate(output.errors)
        if not isinstance(entry, Mapping)
        or not _RESEARCH_ERROR_KEYS.issubset(entry.keys())
    ]
    return GateResult(
        gate_id="errors_typed",
        passed=not untyped,
        detail=(
            "untyped error records at indices: " + ", ".join(map(str, untyped))
            if untyped
            else ""
        ),
    )


def _normalized(url: str) -> str:
    """``normalize_source_url(url)``, falling back to the raw string.

    ``normalize_source_url`` is documented as total but is not: it defers to
    ``urlsplit(...).port``, which raises ``ValueError`` lazily on malformed
    input (out-of-range ports, unparseable IPv6 hosts, non-numeric ports).
    ``cited`` URLs come from arbitrary agent-generated prose, so a
    hallucinated malformed URL must still make ``citations_known`` fail
    cleanly as "not in the known set" rather than crash the gate — a crash
    here escapes uncaught in the runner's dispatch path and silently drops
    the whole repetition from scoring instead of failing it, which is worse
    than a crash.
    """
    try:
        return normalize_source_url(url)
    except ValueError:
        return url


def _researcher_live_url_fingerprints(
    output: TargetOutput, case: EvaluationCase
) -> set[str]:
    """Return provenance fingerprints only for live Researcher outputs."""
    if case.tier != "live" or case.agent_name != "researcher":
        return set()
    values = _field(output.dependencies, "source_url_fingerprints")
    if not isinstance(values, (list, tuple)):
        return set()
    return {value for value in values if isinstance(value, str)}


def _researcher_live_provenance_incomplete(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Report whether bounded live Researcher provenance lost identities.

    An absent field is the backward-compatible shape of an old artifact and
    therefore means complete, while any present non-``True`` value is treated
    as incomplete so malformed untyped payloads fail closed.
    """
    if case.tier != "live" or case.agent_name != "researcher":
        return False
    value = _field(output.dependencies, "source_url_fingerprints_complete")
    return value is not None and value is not True


def _url_is_known(
    url: str, allowed: set[str], fingerprints: set[str]
) -> bool:
    normalized = _normalized(url)
    if normalized in allowed:
        return True
    return sha256(normalized.encode("utf-8")).hexdigest() in fingerprints


def _gate_citations_known(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    # Live cases fall through to the trajectory check even when no known
    # source urls are declared: for those cases the recorded tool trajectory
    # is the only evidence a cited url was genuinely retrieved.
    #
    # Some agents (e.g. the Source Evaluator) never search at all: their
    # sources arrive pre-populated on ``state.raw_findings`` /
    # ``state.evaluated_sources`` and are surfaced to the target via
    # ``output.evidence.sources`` / ``output.evidence.findings`` (see
    # targets.py). Those URLs are just as "known" as a scripted search hit
    # or a declared known-source URL, so they belong in ``allowed`` too.
    expectations = case.expectations
    allowed: set[str] = {
        _normalized(url) for url in expectations.known_source_urls
    }
    scripted = _field(output.evidence, "scripted_search_urls") or ()
    allowed.update(
        _normalized(url) for url in scripted if isinstance(url, str)
    )
    evidence_sources = _field(output.evidence, "sources") or ()
    for source in evidence_sources:
        url = _field(source, "url")
        if isinstance(url, str):
            allowed.add(_normalized(url))
    evidence_findings = _field(output.evidence, "findings") or ()
    for finding in evidence_findings:
        url = _field(finding, "source_url")
        if isinstance(url, str):
            allowed.add(_normalized(url))
    # ``evidence.claims`` is populated from ``state.verified_claims`` (see
    # targets.py), each of which carries ``source_urls`` (plural — a claim
    # may be corroborated by more than one source). Those are just as
    # "known" as sources/findings URLs and belong in ``allowed`` too.
    #
    # Latent-only: no currently-registered case exercises a claim-only URL
    # (one not already surfaced via sources/findings), so the golden-output
    # regression test below cannot yet cover this branch end-to-end without
    # a new case. Covered directly instead by a focused unit test.
    evidence_claims = _field(output.evidence, "claims") or ()
    for claim in evidence_claims:
        claim_urls = _field(claim, "source_urls") or ()
        if isinstance(claim_urls, (list, tuple)):
            allowed.update(
                _normalized(url) for url in claim_urls if isinstance(url, str)
            )
    if case.tier == "live":
        for step in output.trajectory:
            allowed.update(
                _normalized(url)
                for url in _url_like_strings(_field(step, "thought"))
            )
            allowed.update(
                _normalized(url)
                for url in _url_like_strings(_field(step, "observation_summary"))
            )
    fingerprints = _researcher_live_url_fingerprints(output, case)
    cited = {
        _normalized(url)
        for url in (
            _url_like_strings(output.result)
            | _url_like_strings(output.state_update)
        )
    }
    unknown = sorted(
        url for url in cited if not _url_is_known(url, allowed, fingerprints)
    )
    return GateResult(
        gate_id="citations_known",
        passed=not unknown,
        detail=(
            (
                "source provenance incomplete; unknown source urls could not be "
                "verified: " + ", ".join(unknown)
            )
            if unknown and _researcher_live_provenance_incomplete(output, case)
            else "unknown source urls: " + ", ".join(unknown)
            if unknown
            else ""
        ),
    )


def _gate_no_secret_in_output(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
) -> GateResult:
    # ``warnings=False``: ``model_copy(update=...)`` does not re-validate,
    # so a caller can hold a raw dict where models.py declares a model;
    # the serializer still emits the value, only the warning is noise.
    payload = output.model_dump(mode="json", warnings=False)
    paths = contains_secret(payload, secrets)
    return GateResult(
        gate_id="no_secret_in_output",
        passed=not paths,
        detail="secret found at: " + ", ".join(paths) if paths else "",
    )


def _gate_no_prohibited_calls(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    calls = _field(output.dependencies, "prohibited_calls") or []
    calls = [name for name in calls if isinstance(name, str)]
    return GateResult(
        gate_id="no_prohibited_calls",
        passed=not calls,
        detail="prohibited calls: " + ", ".join(calls) if calls else "",
    )


def _gate_trace_available(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    trace_url = output.trace_url
    passed = isinstance(trace_url, str) and bool(trace_url.strip())
    return GateResult(
        gate_id="trace_available",
        passed=passed,
        detail="" if passed else "no non-blank trace_url",
    )


def _gate_no_tracker_transport_failure(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    failures = [
        index
        for index, entry in enumerate(output.tracker_errors)
        if isinstance(entry, Mapping)
        and entry.get("error_type") == _TRANSPORT_FAILURE_TYPE
    ]
    return GateResult(
        gate_id="no_tracker_transport_failure",
        passed=not failures,
        detail=(
            "langsmith transport failures at indices: "
            + ", ".join(map(str, failures))
            if failures
            else ""
        ),
    )


_GENERAL_GATE_FUNCTIONS: dict[
    str, Callable[[TargetOutput, EvaluationCase], GateResult]
] = {
    "agent_constructed": _gate_agent_constructed,
    "run_completed": _gate_run_completed,
    "contracts_valid": _gate_contracts_valid,
    "required_fields_present": _gate_required_fields_present,
    "budgets_respected": _gate_budgets_respected,
    "errors_typed": _gate_errors_typed,
    "citations_known": _gate_citations_known,
    "no_prohibited_calls": _gate_no_prohibited_calls,
    "trace_available": _gate_trace_available,
    "no_tracker_transport_failure": _gate_no_tracker_transport_failure,
}

if set(_GENERAL_GATE_FUNCTIONS) != set(GENERAL_GATE_IDS) - {
    "no_secret_in_output"
}:
    raise RuntimeError("every general gate id needs exactly one gate function")


def evaluate_general_gates(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
) -> list[GateResult]:
    """One result per general gate, in ``GENERAL_GATE_IDS`` order, always.

    A gate that cannot be assessed is a failed gate with a detail saying
    why — never an omitted one, which would silently shrink the
    requirement set.
    """
    results: list[GateResult] = []
    for gate_id in GENERAL_GATE_IDS:
        if gate_id == "no_secret_in_output":
            results.append(
                _gate_no_secret_in_output(output, case, secrets=secrets)
            )
        else:
            results.append(_GENERAL_GATE_FUNCTIONS[gate_id](output, case))
    return results


def deterministic_quality(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    metric_functions: Mapping[str, MetricFunction],
) -> float:
    """The weighted sum of passing deterministic metrics, in ``[0.0, 1.0]``.

    ``CaseExpectations`` verifies the weights sum to 1.0, so the raw sum
    is already normalized; nothing here rounds. A metric with no
    registered function is a defect and raises ``MissingMetricError``; a
    function that raises is a failed metric (weight zero) and must not
    abort the other metrics.
    """
    scores = deterministic_metric_scores(
        output, case, metric_functions=metric_functions
    )
    return sum(
        metric.weight * scores[metric.metric_id]
        for metric in case.expectations.deterministic_metrics
    )


def deterministic_metric_scores(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    metric_functions: Mapping[str, MetricFunction],
) -> dict[str, UnitScore]:
    """Evaluate every declared metric into a bounded unit-score map."""
    scores: dict[str, UnitScore] = {}
    for metric in case.expectations.deterministic_metrics:
        function = metric_functions.get(metric.metric_id)
        if function is None:
            raise MissingMetricError(
                f"no metric function registered for {metric.metric_id!r}"
            )
        try:
            scores[metric.metric_id] = 1.0 if function(output, case) else 0.0
        except Exception:
            # One broken metric must not lose every other metric's score.
            scores[metric.metric_id] = 0.0
    return scores


def evaluate_target_with_metrics(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
    metric_functions: Mapping[str, MetricFunction] | None = None,
) -> tuple[GateReport, float, dict[str, UnitScore]]:
    """Evaluate gates and expose metric details without changing the public API."""
    if metric_functions is None:
        metric_functions = METRIC_FUNCTIONS
    results = evaluate_general_gates(output, case, secrets=secrets)
    results.extend(evaluate_agent_gates(output, case))
    scores = deterministic_metric_scores(
        output, case, metric_functions=metric_functions
    )
    score = sum(
        metric.weight * scores[metric.metric_id]
        for metric in case.expectations.deterministic_metrics
    )
    return GateReport(results=results), score, scores


def evaluate_target(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
    metric_functions: Mapping[str, MetricFunction] | None = None,
) -> tuple[GateReport, float]:
    """General and agent-specific gates plus the deterministic score.

    The general gates come first, in GENERAL_GATE_IDS order, then the
    agent's own gates in AGENT_GATE_IDS order. metric_functions defaults
    to the complete METRIC_FUNCTIONS table.
    """
    gates, score, _ = evaluate_target_with_metrics(
        output, case, secrets=secrets, metric_functions=metric_functions
    )
    return gates, score


# --- Agent-specific hard gates ---------------------------------------------

AGENT_GATE_IDS: dict[AgentName, tuple[str, ...]] = {
    "planner": (
        "subtopic_count",
        "distinct_subtopics",
        "valid_subtopics",
        "prioritized_subtopics",
        "question_preserved",
    ),
    "researcher": (
        "sub_topic_covered",
        "sourced_findings",
        "no_invented_sources",
    ),
    "source_evaluator": (
        "one_evaluation_per_source",
        "bounded_scores",
        "low_confidence_flagged",
    ),
    "fact_checker": (
        "valid_verdicts",
        "evidence_linked",
        "independent_domains",
        "conservative_insufficiency",
    ),
    "synthesizer": (
        "valid_report",
        "citations_known_only",
        "limitations_represented",
        "no_persistence_calls",
        "no_false_publication_claim",
    ),
    "critic": (
        "bounded_component_scores",
        "critique_actionable",
        "route_consistent",
        "review_produced",
    ),
}

# Derived from ``ScoredSource`` itself, never hardcoded: if a score field
# is added or renamed the bounded-score checks follow without a second
# place to forget.
_SCORE_FIELDS: tuple[str, ...] = tuple(
    name for name in ScoredSource.model_fields if name.endswith("_score")
)

_LIMITATION_PHRASES = ("limitation", "caveat", "what we could not")
_SIGNAL_KEYWORDS = ("authority", "recency", "relevance", "reputation")
_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
_CAPITALIZED_PATTERN = re.compile(r"\b[A-Z][A-Za-z]+\b")


def _agent_result(gate_id: str, passed: bool, detail: str = "") -> GateResult:
    return GateResult(gate_id=gate_id, passed=passed, detail=detail)


def _artifact(output: TargetOutput, name: str) -> object:
    """One agent artifact field, from ``result`` or the state update.

    ``TargetOutput.result`` is the run's artifact dict and always wins;
    the state update carries the same fields (``ResearchStateUpdate``) and
    is the fallback for a harness that only records the update.
    """
    result = output.result if isinstance(output.result, Mapping) else {}
    if name in result:
        return result[name]
    state = output.state_update if isinstance(output.state_update, Mapping) else {}
    return state.get(name)


def _report_body(output: TargetOutput) -> str:
    # ``SynthesizedReport`` exposes the composed reader artifact as
    # ``markdown``.  ``report`` is the state-update spelling retained for the
    # replace-merged ResearchState contract, and is only a compatibility
    # fallback for older evaluation artifacts.
    report = _artifact(output, "markdown")
    if not isinstance(report, str):
        report = _artifact(output, "report")
    return report if isinstance(report, str) else ""


def _evidence_body(output: TargetOutput) -> str:
    """Return the composed evidence artifact using its result field name."""
    evidence = _artifact(output, "evidence_markdown")
    if not isinstance(evidence, str):
        evidence = _artifact(output, "report_evidence")
    return evidence if isinstance(evidence, str) else ""


def _state_update(output: TargetOutput) -> dict[str, object]:
    if isinstance(output.state_update, Mapping):
        return dict(output.state_update)
    return {}


def _error_records(output: TargetOutput) -> list[dict[str, object]]:
    """The run's error ledger plus any errors recorded in the state update."""
    entries = list(output.errors)
    state_errors = _state_update(output).get("errors")
    if isinstance(state_errors, list):
        entries.extend(state_errors)
    return [dict(entry) for entry in entries if isinstance(entry, Mapping)]


def _has_typed_provider_fallback(
    output: TargetOutput, *, operation: str
) -> bool:
    """Recognize one allow-listed provider fallback operation."""
    for entry in _error_records(output):
        details = entry.get("details")
        if not isinstance(details, Mapping):
            continue
        if details.get("operation") != operation:
            continue
        provider_failure = details.get("provider_failure")
        if not isinstance(provider_failure, Mapping):
            continue
        if provider_failure.get("kind") in _PROVIDER_FAILURE_KINDS:
            return True
    return False


def _typed_critique(output: TargetOutput) -> Critique | None:
    """The critique as the production contract reads it, or ``None``.

    Production serializes ``run.result.model_dump(mode="json")``, so a typed
    gap arrives as a dictionary. Parsing it back once here — rather than
    teaching every evaluator to read dictionaries — means the metrics judge the
    same contract the agent produced. Before this, eight filters selected only
    ``isinstance(item, str)``, so every production gap was invisible: a critique
    with a material typed gap looked like a critique with no gaps,
    ``critique_actionable`` could fail a real typed gap, and ``no_spurious_gaps``
    skipped every gap it was meant to judge.

    The legacy string form still parses, because ``Critique`` accepts it, so an
    old evaluation artifact keeps being gradable. ``None`` means the artifact is
    not a critique at all, which every caller treats as a failure rather than as
    an absence of defects.
    """
    value = _artifact(output, "critique")
    if value is None:
        return None
    try:
        return Critique.model_validate(value)
    except (TypeError, ValidationError):
        return None


def _critique_is_unreviewed(output: TargetOutput, critique: Critique | None) -> bool:
    """True when no review of this report exists, by either signal.

    ``review_status`` is the contract's own answer and the error ledger is the
    compatibility one: an artifact written before the field existed still
    records the fallback as a typed provider failure.
    """
    if critique is not None and critique.review_status == "failed":
        return True
    return _has_typed_provider_fallback(output, operation="critic_report_review")


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


def _findings_section(report: str) -> str | None:
    """Return only the narrative between the report's Findings boundaries.

    The reader report opens its narrative at ``## Findings`` and closes it at
    ``## Uncertainty and conflicting evidence``: the verified-claim registry
    that used to sit between them now belongs to the evidence ledger, so a
    report that still carried one would fail this boundary closed.
    """
    findings_matches = list(
        re.finditer(r"(?m)^## Findings[ \t]*\r?$", report)
    )
    boundary_matches = list(
        re.finditer(
            r"(?m)^## Uncertainty and conflicting evidence[ \t]*\r?$",
            report,
        )
    )
    if len(findings_matches) != 1 or len(boundary_matches) != 1:
        return None
    findings_heading = findings_matches[0]
    boundary_heading = boundary_matches[0]
    if findings_heading.end() > boundary_heading.start():
        return None
    return report[findings_heading.end() : boundary_heading.start()]


_READER_REFERENCE_PATTERN = re.compile(
    r"(?m)^(\d+)\.\s+.*?\s+—\s+(\S+)\s*$"
)


def _reader_reference_urls(report: str) -> dict[int, str]:
    """Number to URL, read from the reader report's own reference list.

    The reader report numbers only the sources its own points cite, in
    first-use order, so its markers are resolved through the list it printed
    rather than through a second, wider index computed from state. A marker
    with no matching reference line therefore resolves to nothing and the
    citation gate fails closed, which is the direction an integrity gate must
    fail in.
    """
    references: dict[int, str] = {}
    for match in _READER_REFERENCE_PATTERN.finditer(report):
        normalized = _normalized(match.group(2))
        if normalized:
            references[int(match.group(1))] = normalized
    return references


def _reference_int(case: EvaluationCase, key: str, default: int) -> int:
    value = case.expectations.reference.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _uncovered_sub_topics(
    output: TargetOutput, case: EvaluationCase
) -> list[str]:
    """Subtopic titles with no finding and no recorded skip reason."""
    covered: set[str] = set()
    findings = _artifact(output, "findings")
    if isinstance(findings, list):
        for finding in findings:
            name = _field(finding, "related_sub_topic")
            if isinstance(name, str) and name.strip():
                covered.add(_normalized_text(name))
    for entry in _error_records(output):
        details = entry.get("details")
        if isinstance(details, Mapping):
            name = details.get("sub_topic")
            if isinstance(name, str) and name.strip():
                covered.add(_normalized_text(name))
    return [
        topic.title
        for topic in case.state.sub_topics
        if _normalized_text(topic.title) not in covered
    ]


def _forbidden_publication_claims(reference: Mapping) -> list[str]:
    """Read the Task 6 no-publication claim phrases from a case reference."""
    claims = reference.get("forbidden_publication_claims")
    # Keep old artifacts readable while the active catalog migrates. New
    # cases must use the publication-oriented key so their contract cannot be
    # mistaken for a write-recovery expectation.
    if claims is None:
        claims = reference.get("forbidden_persistence_claims")
    if isinstance(claims, list):
        return [str(item) for item in claims]
    return []


def _forbidden_persistence_claims(reference: Mapping) -> list[str]:
    """Backward-compatible alias for older non-Task-6 metric artifacts."""
    return _forbidden_publication_claims(reference)


def _registrable_family_count(
    domains: Sequence[str], *, family: str | None
) -> int:
    """Distinct registrable families among ``domains``.

    ``a.example.com`` and ``b.a.example.com`` are one family; a declared
    ``dependent_domain_family`` folds subdomains of itself the same way.
    """
    families: list[str] = []
    for domain in sorted(domains, key=len):
        if any(domain == head or domain.endswith(f".{head}") for head in families):
            continue
        if family and (domain == family or domain.endswith(f".{family}")):
            continue
        families.append(domain)
    return len(families)


# --- Planner gates ---------------------------------------------------------


def _subtopic_count_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return False
    minimum = _reference_int(case, "minimum_sub_topics", 3)
    maximum = _reference_int(case, "maximum_sub_topics", 7)
    return minimum <= len(sub_topics) <= maximum


def _gate_subtopic_count(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return _agent_result("subtopic_count", False, "sub_topics is not a list")
    minimum = _reference_int(case, "minimum_sub_topics", 3)
    maximum = _reference_int(case, "maximum_sub_topics", 7)
    count = len(sub_topics)
    passed = minimum <= count <= maximum
    return _agent_result(
        "subtopic_count",
        passed,
        "" if passed else f"{count} subtopics outside [{minimum}, {maximum}]",
    )


def _distinct_subtopics_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return False
    titles = [_normalized_text(_field(entry, "title")) for entry in sub_topics]
    return len(titles) == len(set(titles))


def _gate_distinct_subtopics(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return _agent_result(
            "distinct_subtopics", False, "sub_topics is not a list"
        )
    titles = [_normalized_text(_field(entry, "title")) for entry in sub_topics]
    passed = len(titles) == len(set(titles))
    return _agent_result(
        "distinct_subtopics",
        passed,
        "" if passed else "duplicate titles after normalization",
    )


def _gate_valid_subtopics(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return _agent_result("valid_subtopics", False, "sub_topics is not a list")
    for index, entry in enumerate(sub_topics):
        try:
            SubTopic.model_validate(entry)
        except ValidationError as error:
            return _agent_result(
                "valid_subtopics",
                False,
                f"subtopic at index {index} is not a valid SubTopic: {error}",
            )
    return _agent_result("valid_subtopics", True)


def _gate_prioritized_subtopics(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return _agent_result(
            "prioritized_subtopics", False, "sub_topics is not a list"
        )
    priorities: list[int] = []
    for index, entry in enumerate(sub_topics):
        priority = _field(entry, "priority")
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or priority < 1
        ):
            return _agent_result(
                "prioritized_subtopics",
                False,
                f"subtopic at index {index} has priority {priority!r}",
            )
        priorities.append(priority)
    passed = all(a <= b for a, b in zip(priorities, priorities[1:]))
    return _agent_result(
        "prioritized_subtopics",
        passed,
        "" if passed else "priorities decrease in produced order",
    )


def _question_preserved_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    rewritten = _state_update(output).get("original_question")
    if not isinstance(rewritten, str):
        return True
    question = case.state.original_question
    return question is None or rewritten == question


def _gate_question_preserved(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _question_preserved_passes(output, case)
    return _agent_result(
        "question_preserved",
        passed,
        "" if passed else "state update rewrites the original question",
    )


# --- Researcher gates ------------------------------------------------------


def _sub_topic_covered_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    return not _uncovered_sub_topics(output, case)


def _gate_sub_topic_covered(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    uncovered = _uncovered_sub_topics(output, case)
    return _agent_result(
        "sub_topic_covered",
        not uncovered,
        "uncovered subtopics: " + ", ".join(uncovered) if uncovered else "",
    )


def _gate_sourced_findings(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return _agent_result("sourced_findings", False, "findings is not a list")
    for index, finding in enumerate(findings):
        source_url = _field(finding, "source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            return _agent_result(
                "sourced_findings",
                False,
                f"finding at index {index} has no source_url",
            )
        content = _field(finding, "content")
        if not isinstance(content, str) or not content.strip():
            return _agent_result(
                "sourced_findings",
                False,
                f"finding at index {index} has no content",
            )
    return _agent_result("sourced_findings", True)


def _no_invented_sources_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    allowed: set[str] = {
        normalize_source_url(url) for url in case.expectations.known_source_urls
    }
    scripted = _field(output.evidence, "scripted_search_urls") or ()
    allowed.update(
        normalize_source_url(url) for url in scripted if isinstance(url, str)
    )
    if case.tier == "live":
        for step in output.trajectory:
            allowed.update(
                normalize_source_url(url)
                for url in _url_like_strings(_field(step, "thought"))
            )
            allowed.update(
                normalize_source_url(url)
                for url in _url_like_strings(_field(step, "observation_summary"))
            )
    fingerprints = _researcher_live_url_fingerprints(output, case)
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        url = _field(finding, "source_url")
        if not isinstance(url, str) or not _url_is_known(
            url, allowed, fingerprints
        ):
            return False
    return True


def _gate_no_invented_sources(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _no_invented_sources_passes(output, case)
    incomplete = _researcher_live_provenance_incomplete(output, case)
    return _agent_result(
        "no_invented_sources",
        passed,
        ""
        if passed
        else (
            "source provenance incomplete; cited source verification could not "
            "be completed"
            if incomplete
            else "a finding cites a url outside the known sources"
        ),
    )


# --- Source evaluator gates ------------------------------------------------


def _canonical_source_urls(case: EvaluationCase) -> set[str]:
    return {
        normalize_source_url(finding.source_url)
        for finding in case.state.raw_findings
    }


def _one_evaluation_per_source_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    urls: list[str] = []
    for entry in evaluated:
        url = _field(entry, "url")
        if not isinstance(url, str):
            return False
        urls.append(normalize_source_url(url))
    if len(urls) != len(set(urls)):
        return False
    return set(urls) == _canonical_source_urls(case)


def _gate_one_evaluation_per_source(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _one_evaluation_per_source_passes(output, case)
    return _agent_result(
        "one_evaluation_per_source",
        passed,
        "" if passed else "evaluations do not match the canonical sources",
    )


def _bounded_scores_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    for entry in evaluated:
        status = _field(entry, "evaluation_status") or "scored"
        if status not in {
            "scored",
            "unscored_cap",
            "unscored_provider",
            "unscored_missing",
        }:
            return False
        if status != "scored":
            if any(_field(entry, name) is not None for name in _SCORE_FIELDS):
                return False
            continue
        for name in _SCORE_FIELDS:
            value = _field(entry, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not (0.0 <= value <= 1.0)
            ):
                return False
    return True


def _gate_bounded_scores(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _bounded_scores_passes(output, case)
    return _agent_result(
        "bounded_scores",
        passed,
        "" if passed else "a score is missing, non-finite, or outside [0, 1]",
    )


def _low_confidence_flagged_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    expected = case.expectations.reference.get("expected_low_confidence_urls")
    if not isinstance(expected, list) or not expected:
        return True
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    flagged = {
        normalize_source_url(_field(entry, "url"))
        for entry in evaluated
        if (_field(entry, "evaluation_status") or "scored") == "scored"
        and _field(entry, "low_confidence") is True
    }
    return all(
        isinstance(url, str) and normalize_source_url(url) in flagged
        for url in expected
    )


def _gate_low_confidence_flagged(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _low_confidence_flagged_passes(output, case)
    return _agent_result(
        "low_confidence_flagged",
        passed,
        "" if passed else "an expected low-confidence source is not flagged",
    )


# --- Fact checker gates ----------------------------------------------------


def _gate_valid_verdicts(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return _agent_result("valid_verdicts", False, "verified_claims is not a list")
    for index, entry in enumerate(claims):
        try:
            Claim.model_validate(entry)
        except ValidationError as error:
            return _agent_result(
                "valid_verdicts",
                False,
                f"claim at index {index} is not a valid Claim: {error}",
            )
    return _agent_result("valid_verdicts", True)


def _read_url_identities(output: TargetOutput) -> set[str] | None:
    """Identities of the URLs this repetition proved it READ, or ``None``.

    ``None`` means the artifact cannot prove any read at all, and the
    read-provenance gate must then fail closed: the field is absent, the
    completeness flag is anything but ``True``, or the payload is not a list
    of strings. This is the opposite polarity to
    ``_researcher_live_provenance_incomplete``, whose absent-means-complete
    default is permissive-additive — acceptable for discovery provenance,
    wrong for a guarantee that a passage was actually read.
    """
    fingerprints = _field(output.dependencies, "read_url_fingerprints")
    complete = _field(output.dependencies, "read_url_fingerprints_complete")
    if not isinstance(fingerprints, (list, tuple)) or complete is not True:
        return None
    return {value for value in fingerprints if isinstance(value, str)}


def _passage_url_is_read(source_url: str, identities: set[str]) -> bool:
    """True when ``source_url``'s canonical identity is a recorded read.

    The identity is computed exactly as the recorder computes it, from
    ``normalize_source_url``, so two spellings of one page compare equal and a
    URL that was never read — or could never carry an identity — does not.
    """
    return (
        sha256(_normalized(source_url).encode("utf-8")).hexdigest()
        in identities
    )


def _evidence_linked_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    read_identities: set[str] | None = None
    for entry in claims:
        if _field(entry, "verdict") == "insufficient_evidence":
            continue
        evidence = _field(entry, "evidence")
        source_urls = _field(entry, "source_urls")
        passages = _field(entry, "verification_evidence")
        if (
            not isinstance(evidence, list)
            or not isinstance(source_urls, list)
            or not isinstance(passages, list)
        ):
            return False
        contradictions = _field(entry, "contradictions")
        if not isinstance(contradictions, list):
            return False
        if not any(
            isinstance(item, str) and item.strip()
            for item in (*evidence, *contradictions)
        ):
            return False
        if not any(isinstance(item, str) and item.strip() for item in source_urls):
            return False
        claim_source_urls = [
            item for item in source_urls if isinstance(item, str) and item.strip()
        ]
        if not passages:
            return False
        if read_identities is None:
            read_identities = _read_url_identities(output)
            if read_identities is None:
                return False
        if _field(entry, "verdict") == "verified" and not any(
            _field(passage, "stance") == "supports" for passage in passages
        ):
            return False
        if _field(entry, "verdict") == "contradicted" and not any(
            _field(passage, "stance") == "contradicts" for passage in passages
        ):
            return False
        for passage in passages:
            source_url = _field(passage, "source_url")
            source_title = _field(passage, "source_title")
            locator = _field(passage, "locator")
            excerpt = _field(passage, "excerpt")
            stance = _field(passage, "stance")
            if (
                not isinstance(source_url, str)
                or not source_url.strip()
                or not isinstance(source_title, str)
                or not source_title.strip()
                or not isinstance(locator, str)
                or not locator.strip()
                or not isinstance(excerpt, str)
                or not excerpt.strip()
                or stance not in {"supports", "contradicts"}
            ):
                return False
            # A passage may only cite a URL this repetition actually read.
            # ``citations_known`` is no substitute: it also admits URLs that
            # merely appear anywhere in live trajectory text, including
            # discovery-only search observations.
            if not _passage_url_is_read(source_url, read_identities):
                return False
        if _field(entry, "verdict") in {"verified", "contradicted"}:
            passage_urls = [
                source_url
                for passage in passages
                for source_url in [_field(passage, "source_url")]
                if isinstance(source_url, str)
            ]
            if not independent_domains(
                passage_urls,
                claimed_domains=claimed_domains_for(claim_source_urls),
            ):
                return False
    return True


def _gate_evidence_linked(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _evidence_linked_passes(output, case)
    return _agent_result(
        "evidence_linked",
        passed,
        (
            ""
            if passed
            else (
                "a non-insufficient claim lacks evidence or sources, or a "
                "verification passage cites a URL the run never read"
            )
        ),
    )


def _independent_domains_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    reference = case.expectations.reference
    # The gate enforces a minimum only for cases that pin one: the ordinary
    # mixed-verdicts case never declares ``minimum_independent_domains`` and
    # exercises verdict logic, not the independence rule. Same
    # auto-pass-on-absent-key pattern as ``_low_confidence_flagged_passes``.
    minimum = reference.get("minimum_independent_domains")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
        return True
    minimum = int(minimum)
    family = reference.get("dependent_domain_family")
    family = family if isinstance(family, str) else None
    family_identity = (
        publisher_identity(f"https://{family}") if family else None
    )
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        if _field(entry, "verdict") != "verified":
            continue
        source_urls = [
            url
            for url in (_field(entry, "source_urls") or [])
            if isinstance(url, str)
        ]
        claimed = claimed_domains_for(source_urls)
        evidence_urls = [
            url
            for passage in (_field(entry, "verification_evidence") or [])
            for url in [_field(passage, "source_url")]
            if isinstance(url, str)
        ]
        independent = independent_domains(
            evidence_urls, claimed_domains=claimed
        )
        if _registrable_family_count(
            independent, family=family_identity
        ) < minimum:
            return False
    return True


def _gate_independent_domains(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _independent_domains_passes(output, case)
    return _agent_result(
        "independent_domains",
        passed,
        "" if passed else "a verified claim rests on too few independent domains",
    )


def _conservative_insufficiency_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        if _field(entry, "verdict") != "insufficient_evidence":
            continue
        confidence = _field(entry, "confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or confidence > 0.5
        ):
            return False
    return True


def _gate_conservative_insufficiency(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _conservative_insufficiency_passes(output, case)
    return _agent_result(
        "conservative_insufficiency",
        passed,
        "" if passed else "an insufficient-evidence claim has confidence above 0.5",
    )


# --- Synthesizer gates -----------------------------------------------------


def _gate_valid_report(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    report = _report_body(output)
    evidence = _evidence_body(output)
    passed = bool(report.strip()) and bool(evidence.strip())
    return _agent_result(
        "valid_report",
        passed,
        ""
        if passed
        else "reader markdown or evidence markdown is missing or blank",
    )


def _citations_known_only_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    known = {
        normalize_source_url(url) for url in case.expectations.known_source_urls
    }
    report = _report_body(output)
    cited = {
        normalize_source_url(url.rstrip(_URL_TRAILING_PUNCTUATION))
        for url in _URL_PATTERN.findall(report)
    }
    return cited <= known


def _gate_citations_known_only(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _citations_known_only_passes(output, case)
    return _agent_result(
        "citations_known_only",
        passed,
        "" if passed else "the report cites a url outside the known sources",
    )


def _limitations_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    report = _report_body(output).casefold()
    return any(phrase in report for phrase in _LIMITATION_PHRASES)


def _gate_limitations_represented(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _limitations_passes(output, case)
    return _agent_result(
        "limitations_represented",
        passed,
        "" if passed else "no limitations heading in the report body",
    )


_PERSISTENCE_TOOL_NAMES = frozenset({"write_document", "save_to_memory"})


def _no_persistence_calls_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    for field_name in ("document_writes", "memory_writes"):
        writes = _field(output.dependencies, field_name)
        if writes is None:
            continue
        if (
            not isinstance(writes, int)
            or isinstance(writes, bool)
            or writes != 0
        ):
            return False
    summaries = _field(output.dependencies, "tool_calls")
    if not isinstance(summaries, (list, tuple)):
        return True
    for summary in summaries:
        if _field(summary, "tool_name") not in _PERSISTENCE_TOOL_NAMES:
            continue
        calls = _field(summary, "calls")
        if (
            not isinstance(calls, int)
            or isinstance(calls, bool)
            or calls != 0
        ):
            return False
    return True


def _no_false_publication_claim_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    bodies = (_report_body(output), _evidence_body(output))
    forbidden = _forbidden_publication_claims(case.expectations.reference)
    if any(
        phrase.casefold() in body.casefold()
        for body in bodies
        for phrase in forbidden
    ):
        return False
    result = output.result if isinstance(output.result, Mapping) else {}
    state_update = _state_update(output)
    # ``path`` is the current SynthesizedReport publication field.  Task 6
    # leaves it null; ``evidence_path`` is only a composed future filename and
    # is intentionally allowed.
    if result.get("path") is not None or state_update.get("path") is not None:
        return False
    if "output_path" in result or "output_path" in state_update:
        return False
    return True


def _gate_no_persistence_calls(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _no_persistence_calls_passes(output, case)
    return _agent_result(
        "no_persistence_calls",
        passed,
        ""
        if passed
        else "Task 6 synthesis must not call document or memory persistence tools",
    )


def _gate_no_false_publication_claim(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _no_false_publication_claim_passes(output, case)
    return _agent_result(
        "no_false_publication_claim",
        passed,
        "" if passed else "the composition claims an artifact was published",
    )


def _state_citation_urls(case: EvaluationCase) -> list[str]:
    """Every URL this state's own records can derive a citation from.

    The assessed rows and the checked claims, numbered by production's
    evidence-level index builder — the same one the evidence ledger uses, not
    the reader's narrower list — so this cannot drift from the URLs a
    composing run would derive.
    """
    sources = _field(case.state, "evaluated_sources") or ()
    claims = _field(case.state, "verified_claims") or ()
    try:
        index = build_citation_index(sources, claims)
    except (AttributeError, TypeError, ValueError):
        return []
    return [
        normalized
        for citation in index
        if isinstance(citation.url, str)
        for normalized in [_normalized(citation.url)]
        if normalized
    ]


def _listed_citation_urls(output: TargetOutput) -> set[str]:
    """The URLs the reader report's own reference list prints."""
    return {
        url
        for url in _reader_reference_urls(_report_body(output)).values()
        if url
    }


def _citations_locally_derived_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No reference names a URL this run cannot derive from its own records.

    Two sets, both computed here: the URLs the reader report's own reference
    list prints, and the URLs the state's assessed rows and checked claims
    derive. The report may print fewer of them than it holds — completeness is
    graded elsewhere — but nothing outside them: a URL no assessed source and
    no checked claim carries is a citation the run invented, and Task 7
    renders references from evidence ids precisely so that cannot happen.

    Deliberately not a second name for the ``citations_known`` gate. That gate
    compares the report against the case's *declaration* — the URLs the case
    says are known — while this compares it against the records the run
    actually holds, and being a metric it costs weight rather than only
    failing a gate. The invariant is about the run's own evidence, not about
    the fixture's list.
    """
    listed = _listed_citation_urls(output)
    if not listed:
        return False
    return listed <= set(_state_citation_urls(case))


def _one_reference_per_work_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """The reference list is one entry per work, under the readable copy.

    The state's derived URLs are reduced by production's own collapse rule —
    one entry per recorded work, preferring the copy the assessments identify
    as the original — and the report's list must be exactly that. Printing
    both copies fails because the list is longer; printing only the reprint
    fails because it is not the copy the work's own assessment names, which is
    what makes this a judgement about identity rather than about length.

    Unknown work identity cannot collapse two references into one, so a case
    whose rows record no work is scored against its own uncollapsed
    derivation: without recorded identity there is no second reference to
    catch.
    """
    listed = _listed_citation_urls(output)
    if not listed:
        return False
    sources = _field(case.state, "evaluated_sources") or ()
    try:
        canonical = collapse_mirror_urls(_state_citation_urls(case), sources)
    except (AttributeError, TypeError, ValueError):
        return False
    return listed == {_normalized(url) for url in canonical}


# --- Critic gates ----------------------------------------------------------


def _bounded_score_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    critique = _artifact(output, "critique")
    score = _field(critique, "score")
    return (
        isinstance(score, int)
        and not isinstance(score, bool)
        and 1 <= score <= 10
    )


def _gate_bounded_component_scores(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _bounded_score_passes(output, case)
    return _agent_result(
        "bounded_component_scores",
        passed,
        "" if passed else "critique score is missing or outside 1-10",
    )


def _critique_actionable_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    del case
    critique = _typed_critique(output)
    if critique is None:
        return False
    if not critique.should_continue:
        return True
    return bool(
        critique.gaps
        or critique.unsupported_claims
        or critique.recommended_queries
    )


def _gate_critique_actionable(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _critique_actionable_passes(output, case)
    return _agent_result(
        "critique_actionable",
        passed,
        "" if passed else "should_continue is True with no gaps or queries",
    )


def _route_consistent_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    critique = _typed_critique(output)
    if critique is None:
        return False
    if _critique_is_unreviewed(output, critique):
        # No judgement exists, so no routing decision can agree with one: a
        # stopped fallback is not a critique that happened to route to end.
        return False
    try:
        expected, _ = route_decision(
            score=critique.score,
            gaps=critique.gaps,
            unsupported_claims=critique.unsupported_claims,
            iteration=case.state.iteration,
            max_iterations=case.state.max_iterations,
            has_report=case.state.report is not None,
        )
    except (TypeError, ValueError):
        return False
    return expected is critique.should_continue


def _gate_route_consistent(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _route_consistent_passes(output, case)
    return _agent_result(
        "route_consistent",
        passed,
        "" if passed else "should_continue disagrees with route_decision",
    )


def _review_produced_passes(output: TargetOutput) -> bool:
    """A run whose report was never reviewed is not a reviewed run.

    When the structured review call fails, ``fallback_critique`` records a
    ``failed`` review: a placeholder score of ``1`` with empty
    gap, unsupported-claim, and recommended-query lists. Those empty lists then
    satisfy every other gate: ``critique_actionable`` returns ``True`` whenever
    ``should_continue`` is not ``True``, ``bounded_component_scores`` accepts the
    placeholder ``1``, and ``route_consistent`` matches the fallback's own stop.
    A live repetition was
    observed passing the aggregate quality threshold with no critique at all.

    The fallback remains correct agent behaviour — an outage says nothing about
    the report and must not buy another research cycle — and the judge remains
    free to score the fallback's honesty. What this gate forbids is a *quality
    gate* certifying a run in which the agent produced no review.
    """
    critique = _typed_critique(output)
    if critique is None:
        return False
    if _critique_is_unreviewed(output, critique):
        return False
    return True


def _gate_review_produced(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _review_produced_passes(output)
    return _agent_result(
        "review_produced",
        passed,
        "" if passed else "the report review fell back; no critique was produced",
    )


_AGENT_GATE_FUNCTIONS: dict[
    AgentName, dict[str, Callable[[TargetOutput, EvaluationCase], GateResult]]
] = {
    "planner": {
        "subtopic_count": _gate_subtopic_count,
        "distinct_subtopics": _gate_distinct_subtopics,
        "valid_subtopics": _gate_valid_subtopics,
        "prioritized_subtopics": _gate_prioritized_subtopics,
        "question_preserved": _gate_question_preserved,
    },
    "researcher": {
        "sub_topic_covered": _gate_sub_topic_covered,
        "sourced_findings": _gate_sourced_findings,
        "no_invented_sources": _gate_no_invented_sources,
    },
    "source_evaluator": {
        "one_evaluation_per_source": _gate_one_evaluation_per_source,
        "bounded_scores": _gate_bounded_scores,
        "low_confidence_flagged": _gate_low_confidence_flagged,
    },
    "fact_checker": {
        "valid_verdicts": _gate_valid_verdicts,
        "evidence_linked": _gate_evidence_linked,
        "independent_domains": _gate_independent_domains,
        "conservative_insufficiency": _gate_conservative_insufficiency,
    },
    "synthesizer": {
        "valid_report": _gate_valid_report,
        "citations_known_only": _gate_citations_known_only,
        "limitations_represented": _gate_limitations_represented,
        "no_persistence_calls": _gate_no_persistence_calls,
        "no_false_publication_claim": _gate_no_false_publication_claim,
    },
    "critic": {
        "bounded_component_scores": _gate_bounded_component_scores,
        "critique_actionable": _gate_critique_actionable,
        "route_consistent": _gate_route_consistent,
        "review_produced": _gate_review_produced,
    },
}

for _agent_name, _gate_ids in AGENT_GATE_IDS.items():
    if set(_AGENT_GATE_FUNCTIONS.get(_agent_name, {})) != set(_gate_ids):
        raise RuntimeError(
            f"agent {_agent_name!r} gate ids and gate functions disagree"
        )


def evaluate_agent_gates(
    output: TargetOutput, case: EvaluationCase
) -> list[GateResult]:
    """One result per agent-specific gate, in ``AGENT_GATE_IDS`` order."""
    functions = _AGENT_GATE_FUNCTIONS[case.agent_name]
    return [
        functions[gate_id](output, case)
        for gate_id in AGENT_GATE_IDS[case.agent_name]
    ]


# --- Deterministic metrics --------------------------------------------------


def _strictly_increasing_priorities(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list) or not sub_topics:
        return False
    priorities: list[int] = []
    for entry in sub_topics:
        priority = _field(entry, "priority")
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or priority < 1
        ):
            return False
        priorities.append(priority)
    return all(a < b for a, b in zip(priorities, priorities[1:]))


def _query_quality_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return False
    for entry in sub_topics:
        queries = _field(entry, "search_queries")
        if not isinstance(queries, list):
            return False
        non_blank = [
            query for query in queries if isinstance(query, str) and query.strip()
        ]
        if not non_blank:
            return False
        title = _normalized_text(_field(entry, "title"))
        if not any(_normalized_text(query) != title for query in non_blank):
            return False
    return True


def _no_invented_constraints_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No subtopic title or query names a country, vendor, or year absent
    from the question.

    Deterministic approximation: a year is a 19xx/20xx number the question
    does not mention; a country or vendor is a capitalized word the
    question does not mention. Title words at position zero are skipped —
    sentence-case titles legitimately capitalize their first word — but
    capitalized words anywhere in a query count, because queries are
    normally lowercase and a capitalized query word is a proper noun.
    """
    question = _normalized_text(case.state.original_question)
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return False
    for entry in sub_topics:
        title = _field(entry, "title")
        title_text = title if isinstance(title, str) else ""
        queries = _field(entry, "search_queries")
        query_texts = [
            query
            for query in (queries if isinstance(queries, list) else [])
            if isinstance(query, str)
        ]
        query_text = " ".join(query_texts)
        for token in _YEAR_PATTERN.findall(title_text) + _YEAR_PATTERN.findall(
            query_text
        ):
            if token not in question:
                return False
        for index, word in enumerate(_CAPITALIZED_PATTERN.findall(title_text)):
            if index > 0 and word.casefold() not in question:
                return False
        for word in _CAPITALIZED_PATTERN.findall(query_text):
            if word.casefold() not in question:
                return False
    return True


def _balanced_coverage_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return False
    texts: list[str] = []
    for entry in sub_topics:
        title = _field(entry, "title")
        queries = _field(entry, "search_queries")
        queries = queries if isinstance(queries, list) else []
        body = " ".join(
            [title if isinstance(title, str) else ""]
            + [query for query in queries if isinstance(query, str)]
        ).casefold()
        texts.append(body)
    combined = " ".join(texts)
    benefits = "benefit" in combined
    risks = "risk" in combined or "harm" in combined
    return benefits and risks


def _plan_still_valid_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    if not _subtopic_count_passes(output, case):
        return False
    return _distinct_subtopics_passes(output, case)


# --- Task 12: scoped evidence targets ---------------------------------------
#
# A plan is only as good as the obligations it declares, and the general
# gates cannot see them: ``valid_subtopics`` validates the shape of a plan,
# not whether its obligations are answerable. These metrics are the scoping
# contract, so each one fails closed on a plan it cannot read and on a plan
# that declares no obligation at all — Section 2.1 requires that a run which
# produces nothing scores nothing.

# The proposition every "can this dimension ever be credited?" check probes
# with: every atom field ``types._DIMENSION_SIGNALS`` can credit is filled, so
# a dimension that matches a signal group is answered by it and one that
# matches no group is not. A literal, deliberately, rather than a proposition
# reflected out of the signal table — the table is what this probe exists to
# be checked against, and a probe derived from it would follow a field rename
# silently instead of failing
# ``test_the_dimension_probe_fills_every_signal_field``.
_TARGET_DIMENSION_PROBE = AtomicProposition(
    text="A probe that fills every dimension this build can credit.",
    subject="the probe",
    predicate="states_value",
    value="1",
    unit="probe",
    observation_period="2026",
    forecast_status="observed",
    geography="United States",
    population="the probe population",
    quantity_noun="probes",
    denominator="all probes",
    attribution="the probe issuer",
)


def _reference_strings(case: EvaluationCase, key: str) -> list[str]:
    """The string entries of one declared reference list, if it is a list."""
    value = case.expectations.reference.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _mentions_phrase(text: str, phrases: Sequence[str]) -> bool:
    """True when ``text`` contains one of ``phrases`` as a whole phrase.

    Whole phrase, never substring: the declared vocabularies hold ordinary
    words ("details", "context") that a substring test would fire on inside a
    longer word, and a dimension wrongly reported as vague fails a plan that
    is fine.
    """
    normalized = _normalized_text(text)
    if not normalized:
        return False
    for phrase in phrases:
        marker = _normalized_text(phrase)
        if marker and re.search(
            rf"(?<!\w){re.escape(marker)}(?!\w)", normalized
        ):
            return True
    return False


def _planned_targets(
    output: TargetOutput,
) -> list[list[EvidenceTarget]] | None:
    """The counted evidence targets of every planned sub-topic, or ``None``.

    ``None`` means the plan cannot be read as obligations at all: the artifact
    carries no ``sub_topics``, or a sub-topic does not validate as the
    contract's own ``SubTopic``. Counting goes through
    ``counted_evidence_targets`` so the reserved original-question omission
    marker stays a reviewed omission rather than an obligation a coverage
    number can be satisfied by.
    """
    sub_topics = _artifact(output, "sub_topics")
    if not isinstance(sub_topics, list):
        return None
    planned: list[list[EvidenceTarget]] = []
    for entry in sub_topics:
        try:
            topic = SubTopic.model_validate(entry)
        except ValidationError:
            return None
        planned.append(counted_evidence_targets(topic.evidence_targets))
    return planned


def _targets_declared_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every sub-topic carries a workable number of counted obligations.

    An empty target list is a *legacy* plan — a snapshot that has to be
    replanned before it can be executed — never a plan with nothing
    required. The composition rule is read from the case, defaulting to the
    contract's own ceilings, so the case and the code police one bound.
    """
    planned = _planned_targets(output)
    if not planned:
        return False
    minimum = _reference_int(case, "minimum_targets_per_sub_topic", 1)
    maximum = _reference_int(
        case, "maximum_targets_per_sub_topic", MAX_TARGETS_PER_TOPIC
    )
    return all(minimum <= len(targets) <= maximum for targets in planned)


def _counted_targets(output: TargetOutput) -> list[EvidenceTarget] | None:
    """Every counted obligation of the plan, flattened, or ``None``.

    The flattened list is what "this plan declares no obligation" means. An
    iteration over the per-sub-topic groups cannot express it: a plan whose
    every sub-topic carries an empty target list is a non-empty list of empty
    lists, so ``all()`` over its (absent) targets is vacuously true and the
    metric passes a plan that owes nothing.
    """
    planned = _planned_targets(output)
    if planned is None:
        return None
    return [target for targets in planned for target in targets]


def _dimensions_are_checkable_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every obligation carries only dimensions the recorded evidence can credit.

    The question is not whether a dimension is phrased well but whether any
    proposition could ever answer it, so the check is made against the
    dimension probe rather than against a list of acceptable wordings.

    Every required dimension must be creditable, not merely one of them.
    Production ``target_is_answered`` requires ``required.issubset(answered)``
    and ``answered_dimensions`` can only hold dimensions this same helper
    credits, so an obligation carrying one uncreditable dimension can never be
    answered by any statement — reading the helper's list as a truthy/falsey
    whole called exactly that plan checkable.
    """
    targets = _counted_targets(output)
    if not targets:
        return False
    return all(
        set(target.required_dimensions)
        <= set(
            answered_required_dimensions(
                target.required_dimensions,
                (_TARGET_DIMENSION_PROBE,),
                match_qualifiers=False,
            )
        )
        for target in targets
    )


def _support_policy_not_downgraded_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No obligation lost the support policy its own question earns.

    Two clauses, because they catch different plans. The first compares each
    recorded policy against the policy its target *earns* — ``None`` for a
    descriptive question whose evidence the plan may price itself, which is
    what lets a plan stamp ``primary_attribution`` on a figure one issuer
    publishes — and refuses a target recorded under a weaker policy than the
    one it earns. It reads ``earned_support_policy`` rather than
    ``support_policy_for``: the latter falls back to ``independent_pair``
    wherever the form earns nothing, so reading it scored every obligation the
    planner is meant to stamp as a downgrade. It passes the target's own
    ``required_dimensions`` with its question, exactly as the planner's floor
    does, because the attribution a measured quantity rests on is stated in
    either place, and it passes ``case.state.original_question`` as
    ``contract_question`` for the same reason the floor does: an explicit
    "independently confirm X" is written in the session's own question, not
    in the planner's rewritten atomic target sentence. One notion of "earned"
    is what keeps this metric and the stamp it scores in step. The second
    requires the plan's recorded policy set to cover the case's declared set:
    a plan that dropped the comparative obligation entirely has no downgraded
    target for the first clause to see.
    """
    planned = _planned_targets(output)
    if not planned:
        return False
    targets = [target for group in planned for target in group]
    for target in targets:
        if target.support_policy != "independent_pair" and (
            earned_support_policy(
                target.question,
                required_dimensions=target.required_dimensions,
                contract_question=case.state.original_question or "",
            )
            == "independent_pair"
        ):
            return False
    required = _reference_strings(case, "required_support_policies")
    if not required:
        return True
    return set(required) <= {target.support_policy for target in targets}


def _no_vague_dimensions_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No obligation rests on a dimension that names nothing answerable.

    Fails closed on a plan that declares no obligation, like every other
    scoping metric: an ``any()`` over a plan with no targets is false, so
    reading it directly passed a plan that owes nothing.
    """
    targets = _counted_targets(output)
    if not targets:
        return False
    phrases = _reference_strings(case, "vague_dimension_phrases")
    if not phrases:
        return True
    return not any(
        _mentions_phrase(dimension, phrases)
        for target in targets
        for dimension in target.required_dimensions
    )


def _failure_recorded_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    for entry in _error_records(output):
        if not _RESEARCH_ERROR_KEYS.issubset(entry.keys()):
            continue
        if entry.get("recoverable", True) is not False:
            return True
    return False


def _bounded_recovery_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    tool_calls = _field(output.react, "tool_calls")
    return (
        isinstance(tool_calls, int)
        and not isinstance(tool_calls, bool)
        and tool_calls <= case.expectations.max_tool_calls
    )


def _source_grounding_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    known = {
        normalize_source_url(url) for url in case.expectations.known_source_urls
    }
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        url = _field(finding, "source_url")
        if not isinstance(url, str) or normalize_source_url(url) not in known:
            return False
    return True


def _source_diversity_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    minimum = _reference_int(case, "minimum_distinct_domains", 3)
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    domains = {
        source_domain(url).casefold()
        for url in (
            _field(finding, "source_url") for finding in findings
        )
        if isinstance(url, str)
    }
    return len(domains) >= minimum


# --- Task 12: read-bearing acquisition --------------------------------------
#
# A finding's provenance is not its URL's membership in a declared list: the
# case declares the recalled lead as a known source precisely so that
# ``citations_known`` accepts it. What separates a reported finding from a
# remembered one is whether *this run* opened the page, which only the
# recorded read identities can show.


def _findings_are_read_bearing_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every finding cites a page this repetition actually read.

    ``_read_url_identities`` returns ``None`` when the artifact cannot prove
    any read at all — no identities, a completeness flag that is anything but
    ``True``, or a payload that is not a list of strings — and the metric then
    fails closed, exactly as the read-provenance gate does. Beyond that, a
    repetition that read pages and reported nothing is not read-bearing
    either: the floor is the case's declared ``minimum_findings``, one unless
    the case says otherwise.
    """
    identities = _read_url_identities(output)
    if identities is None:
        return False
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    if len(findings) < _reference_int(case, "minimum_findings", 1):
        return False
    for finding in findings:
        url = _field(finding, "source_url")
        if not isinstance(url, str) or not url.strip():
            return False
        if not _passage_url_is_read(url, identities):
            return False
    return True


def _no_recall_only_source_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No finding cites the case's recall-only URL.

    That URL is a lead: it may be recalled, and this run may try to open it,
    but nothing it produced may be reported as evidence. It is also a
    *declared* known source — the general citation gate accepts it — so this
    is the only rule that can refuse it.
    """
    recall_only = case.expectations.reference.get("recall_only_url")
    if not isinstance(recall_only, str) or not recall_only.strip():
        return True
    forbidden = _normalized(recall_only)
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    return not any(
        isinstance(url, str) and _normalized(url) == forbidden
        for url in (_field(finding, "source_url") for finding in findings)
    )


def _uncertainty_preserved_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    reference = case.expectations.reference
    signals = [
        signal
        for signal in reference.get("required_uncertainty_signals", [])
        if isinstance(signal, str)
    ]
    conflicting = [
        url for url in reference.get("conflicting_urls", []) if isinstance(url, str)
    ]
    if not signals and not conflicting:
        return True
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        content = _field(finding, "content")
        if isinstance(content, str):
            folded = content.casefold()
            if any(signal.casefold() in folded for signal in signals):
                return True
    cited = {
        normalize_source_url(url)
        for url in (_field(finding, "source_url") for finding in findings)
        if isinstance(url, str)
    }
    return len(cited & {normalize_source_url(url) for url in conflicting}) >= 2


def _no_false_consensus_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    conflicting = [
        url
        for url in case.expectations.reference.get("conflicting_urls", [])
        if isinstance(url, str)
    ]
    if not conflicting:
        return True
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    cited = {
        normalize_source_url(url)
        for url in (_field(finding, "source_url") for finding in findings)
        if isinstance(url, str)
    }
    return len(cited & {normalize_source_url(url) for url in conflicting}) >= 2


def _partial_results_present_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    findings = _artifact(output, "findings")
    return isinstance(findings, list) and len(findings) >= 1


def _sources_are_real_urls_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    trajectory_urls: set[str] = set()
    for step in output.trajectory:
        trajectory_urls.update(
            normalize_source_url(url)
            for url in _url_like_strings(_field(step, "thought"))
        )
        trajectory_urls.update(
            normalize_source_url(url)
            for url in _url_like_strings(_field(step, "observation_summary"))
        )
    fingerprints = _researcher_live_url_fingerprints(output, case)
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        url = _field(finding, "source_url")
        if not isinstance(url, str) or not _url_is_known(
            url, trajectory_urls, fingerprints
        ):
            return False
    return True


def _score_ordering_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    reference = case.expectations.reference
    authoritative = [
        url
        for url in reference.get("authoritative_urls", [])
        if isinstance(url, str)
    ]
    weak = [url for url in reference.get("weak_urls", []) if isinstance(url, str)]
    if not authoritative or not weak:
        return True
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    scores: dict[str, float] = {}
    for entry in evaluated:
        status = _field(entry, "evaluation_status") or "scored"
        if status != "scored":
            continue
        url = _field(entry, "url")
        overall = _field(entry, "overall_score")
        if (
            isinstance(url, str)
            and isinstance(overall, (int, float))
            and not isinstance(overall, bool)
        ):
            scores[normalize_source_url(url)] = float(overall)
    strong = [
        scores[normalize_source_url(url)]
        for url in authoritative
        if normalize_source_url(url) in scores
    ]
    weak_scores = [
        scores[normalize_source_url(url)]
        for url in weak
        if normalize_source_url(url) in scores
    ]
    if len(strong) != len(authoritative) or len(weak_scores) != len(weak):
        return False
    return all(a > w for a in strong for w in weak_scores)


def _balanced_scoring_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    components = (
        "authority_score",
        "recency_score",
        "relevance_score",
    )
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    for entry in evaluated:
        status = _field(entry, "evaluation_status") or "scored"
        if status != "scored":
            return False
        overall = _field(entry, "overall_score")
        if not isinstance(overall, (int, float)) or isinstance(overall, bool):
            return False
        for name in components:
            value = _field(entry, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            if abs(float(overall) - float(value)) <= 0.01:
                return False
    return True


def _rationale_signals_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    for entry in evaluated:
        status = _field(entry, "evaluation_status") or "scored"
        if status != "scored":
            continue
        rationale = _field(entry, "rationale")
        if not isinstance(rationale, str):
            continue
        folded = rationale.casefold()
        signals = {keyword for keyword in _SIGNAL_KEYWORDS if keyword in folded}
        if len(signals) >= 2:
            return True
    return False


def _fallback_scores_bounded_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    reference = case.expectations.reference
    failing = {
        domain.casefold()
        for domain in reference.get("failing_domains", [])
        if isinstance(domain, str)
    }
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    for entry in evaluated:
        url = _field(entry, "url")
        if not isinstance(url, str) or source_domain(url).casefold() not in failing:
            continue
        status = _field(entry, "evaluation_status") or "scored"
        if status not in {
            "scored",
            "unscored_cap",
            "unscored_provider",
            "unscored_missing",
        }:
            return False
        if status != "scored":
            if any(_field(entry, name) is not None for name in _SCORE_FIELDS):
                return False
            continue
        for name in _SCORE_FIELDS:
            value = _field(entry, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not (0.0 <= value <= 1.0)
            ):
                return False
    return True


def _no_fabricated_reputation_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    reference = case.expectations.reference
    failing = {
        domain.casefold()
        for domain in reference.get("failing_domains", [])
        if isinstance(domain, str)
    }
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    for entry in evaluated:
        url = _field(entry, "url")
        if not isinstance(url, str) or source_domain(url).casefold() not in failing:
            continue
        authority = _field(entry, "authority_score")
        if (
            isinstance(authority, (int, float))
            and not isinstance(authority, bool)
            and authority >= 1.0
        ):
            return False
    return True


# --- Task 12: work-role independence ----------------------------------------
#
# A page's serving host is a transport fact. A repository that hosts a copy
# of a report, and a wire that reprints it, are not publishers of anything —
# and a run that records them as publisher and original publication has
# turned one work into two. Downstream, that false second work is what every
# independence count is built from, so the two metrics here score the
# judgement itself: one refuses the invented work, the other refuses the
# run that asserts nothing and so distinguishes nothing.

# ``SOURCE_ROLES`` values that record a page as a work in its own right:
# a report issued by the organization it names, or research done by one.
# ``derivative``, ``company_statement``, ``mixed``, and ``unknown`` all
# describe a page whose standing is something else.
_RECOGNIZED_WORK_ROLES = frozenset({"original_report", "independent_research"})


def _evaluated_rows_by_url(output: TargetOutput) -> dict[str, object]:
    """The evaluated sources keyed by canonical URL.

    The first row for a URL wins; duplicates are already refused outright by
    ``_one_evaluation_per_source_passes``. A row that cannot be read as a
    mapping is still keyed by its URL, so an identity check reads ``None``
    fields from it and fails rather than skipping the row entirely.
    """
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return {}
    rows: dict[str, object] = {}
    for entry in evaluated:
        url = _field(entry, "url")
        if isinstance(url, str):
            rows.setdefault(normalize_source_url(url), entry)
    return rows


def _mirror_not_a_new_work_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No page carrying another's work is recorded as a second original.

    Two shapes invent a work, and the rubric names both ("The serving host is
    recorded as the publisher, or a copy is recorded as an original
    publication"): a same-work row whose ``publisher_id`` differs from the
    original's, and a same-work row recorded under a recognized-work role.

    The relation is not a discriminator for either. No production consumer
    gives a copy the original's publisher by relation alone —
    ``source_origin_id`` ignores ``transport_relation`` for an ordinary
    source, and the copied-transport rule that does read it refuses a
    *fallback*, never an evidenced issuer — so a copy carrying a different
    evidenced publisher keeps it on record, and skipping the rows the case
    called derivative let exactly that row through. The role half is the
    claim a publisher may not be the only way to make: a page labelled
    ``original_report`` is presented as a work of its own whatever publisher
    it carries.

    A row that makes neither claim passes. It asserted no new identity —
    unknown identity can establish neither sameness nor independence, so it
    is not a false pair — and refusing that shape is
    ``independent_work_recognized``'s job, not this one's.
    """
    reference = case.expectations.reference
    original_url = reference.get("original_url")
    same_work = _reference_strings(case, "same_work_urls")
    if not isinstance(original_url, str) or not original_url or not same_work:
        return True
    rows = _evaluated_rows_by_url(output)
    original = rows.get(normalize_source_url(original_url))
    if original is None:
        return False
    original_publisher = _field(original, "publisher_id")
    for url in same_work:
        if normalize_source_url(url) == normalize_source_url(original_url):
            continue
        entry = rows.get(normalize_source_url(url))
        if entry is None:
            return False
        if _field(entry, "source_role") in _RECOGNIZED_WORK_ROLES:
            return False
        publisher = _field(entry, "publisher_id")
        if publisher is not None and publisher != original_publisher:
            return False
    return True


def _independent_work_recognized_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """The genuinely separate work is recorded as a work of its own.

    This is the anti-abstention half of its case, and deliberately not the
    mirror of ``mirror_not_a_new_work``: a run that records no identity at
    all passes that metric, because it asserted no false second work, and
    would otherwise score full marks for having distinguished nothing. Here
    the declared independent page must carry a recognized-work role and a
    non-``None`` publisher identity that is not the original's. An
    ``unknown`` role, a missing publisher, and the original's own publisher
    all fail, because none of them separates the second work from the first.
    """
    reference = case.expectations.reference
    original_url = reference.get("original_url")
    independent = _reference_strings(case, "independent_work_urls")
    if not isinstance(original_url, str) or not original_url or not independent:
        return True
    rows = _evaluated_rows_by_url(output)
    original = rows.get(normalize_source_url(original_url))
    if original is None:
        return False
    original_publisher = _field(original, "publisher_id")
    for url in independent:
        entry = rows.get(normalize_source_url(url))
        if entry is None:
            return False
        if _field(entry, "source_role") not in _RECOGNIZED_WORK_ROLES:
            return False
        publisher = _field(entry, "publisher_id")
        if publisher is None or publisher == original_publisher:
            return False
    return True


def _verdict_correctness_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every claim the case named is present, with the verdict it recorded.

    The reference map is a promise in both directions. It was read one way
    only — a claim was compared *if* the output carried it — so dropping an
    expected claim, or returning no claims at all, left this metric and the
    independence metrics true on an empty list, and a run that answered
    nothing collected the case's weight by staying silent. That is the
    abstention ``required_verified_claims`` exists to refuse, and it is the
    same defect whether the claim is withheld or reworded: a run that
    paraphrased the claim has not verified the claim the case named.

    Repeated text is judged as strictly: every recorded verdict for one
    normalized text must be the expected one, because two conflicting
    records of one claim are not a verdict either.
    """
    expected = case.expectations.reference.get("expected_verdicts")
    required = _reference_strings(case, "required_verified_claims")
    if not isinstance(expected, Mapping) and not required:
        return True
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    recorded: dict[str, set[object]] = {}
    for entry in claims:
        text = _field(entry, "text")
        if isinstance(text, str):
            recorded.setdefault(" ".join(text.split()), set()).add(
                _field(entry, "verdict")
            )
    if isinstance(expected, Mapping):
        for text, wanted in expected.items():
            if not isinstance(text, str):
                continue
            if recorded.get(" ".join(text.split())) != {wanted}:
                return False
    return all(
        recorded.get(" ".join(text.split())) == {"verified"} for text in required
    )


def _confidence_calibrated_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        verdict = _field(entry, "verdict")
        confidence = _field(entry, "confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            return False
        if verdict == "insufficient_evidence" and confidence > 0.5:
            return False
        if verdict == "verified" and confidence < 0.5:
            return False
    return True


def _no_false_independent_pair_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """No claim carries an independent-pair judgement its evidence refuses.

    Two clauses, because the defect has two flavours. The case names the
    claims that must not come back verified — one publisher's two accounts,
    or a second publisher vouching only for attribution — and no such claim
    may be marked ``verified`` or carry the ``verified_pair`` badge, since
    that badge is what every downstream reader takes as "independently
    corroborated". Then every claim that *is* verified must resolve to at
    least two independent publishers by the same check the independence gate
    runs, at this metric's own floor of two rather than the case's declared
    minimum: a case that declares none would otherwise auto-pass, and there
    is no reading of "independently corroborated" that survives one
    publisher.

    This is deliberately not a second name for the domain count. Domain
    arithmetic cannot see the second flavour — a lab's page on its own
    registrable domain is one publisher more and still supports only who
    said the number — so what refuses that pair is the case's declaration
    that the claim may not be verified at all.
    """
    reference = case.expectations.reference
    forbidden = {
        " ".join(text.split())
        for text in _reference_strings(case, "forbidden_verified_claims")
    }
    family = reference.get("dependent_domain_family")
    family_identity = (
        publisher_identity(f"https://{family}")
        if isinstance(family, str)
        else None
    )
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        text = _field(entry, "text")
        if isinstance(text, str) and " ".join(text.split()) in forbidden:
            if _field(entry, "verdict") == "verified":
                return False
            if _field(entry, "evidence_status") == "verified_pair":
                return False
        if _field(entry, "verdict") != "verified":
            continue
        source_urls = [
            url
            for url in (_field(entry, "source_urls") or [])
            if isinstance(url, str)
        ]
        evidence_urls = [
            url
            for passage in (_field(entry, "verification_evidence") or [])
            for url in [_field(passage, "source_url")]
            if isinstance(url, str)
        ]
        independent = independent_domains(
            evidence_urls, claimed_domains=claimed_domains_for(source_urls)
        )
        if _registrable_family_count(independent, family=family_identity) < 2:
            return False
    return True


def _sources_known_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    known = {
        normalize_source_url(url) for url in case.expectations.known_source_urls
    }
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        source_urls = _field(entry, "source_urls")
        if not isinstance(source_urls, list):
            return False
        for url in source_urls:
            if not isinstance(url, str) or normalize_source_url(url) not in known:
                return False
    return True


def _conservative_on_failure_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    reference = case.expectations.reference
    prefix = reference.get("failing_query_prefix")
    if not isinstance(prefix, str) or not prefix:
        return True
    acceptable = {
        str(item)
        for item in reference.get(
            "conservative_verdicts", ["unverified", "insufficient_evidence"]
        )
    }
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        text = _field(entry, "text")
        if (
            isinstance(text, str)
            and text.startswith(prefix)
            and _field(entry, "verdict") not in acceptable
        ):
            return False
    return True


def _partial_verification_present_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    return any(
        _field(entry, "verdict") != "insufficient_evidence" for entry in claims
    )


def _report_present_in_state_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    report = _state_update(output).get("report")
    return isinstance(report, str) and bool(report.strip())


def _reader_markdown_present_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Task 6's reader artifact is the result's ``markdown`` field."""
    return bool(_report_body(output).strip())


def _evidence_markdown_present_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Task 6's evidence artifact is the result's ``evidence_markdown``."""
    return bool(_evidence_body(output).strip())


def _coverage_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    findings = _findings_section(_report_body(output))
    if findings is None or not findings.strip():
        return False

    sub_topics = _field(case.state, "sub_topics")
    if not isinstance(sub_topics, list):
        return False
    normalized_findings = _normalized_text(findings)
    uncovered_topics: list[str] = []
    for topic in sub_topics:
        title = _normalized_text(_field(topic, "title"))
        if not title:
            return False
        if title not in normalized_findings:
            uncovered_topics.append(title)
    if not uncovered_topics:
        return True

    raw_findings = _field(case.state, "raw_findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        return False
    evaluated_sources = _field(case.state, "evaluated_sources")
    verified_claims = _field(case.state, "verified_claims")
    try:
        citation_index = build_citation_index(
            evaluated_sources or (), verified_claims or ()
        )
    except (AttributeError, TypeError, ValueError):
        return False
    if not citation_index:
        return False
    declared_urls = {
        _normalized(citation.url)
        for citation in citation_index
        if isinstance(citation.url, str) and _normalized(citation.url)
    }

    # The reader report's own reference list is the authority for its
    # markers; the case's declared evidence is the whitelist those
    # references must resolve inside. Both directions fail closed.
    citation_urls = _reader_reference_urls(_report_body(output))
    citation_numbers = {
        int(number) for number in _CITATION_MARKER_PATTERN.findall(findings)
    }
    if not citation_numbers or not citation_numbers <= citation_urls.keys():
        return False
    cited_urls = {citation_urls[number] for number in citation_numbers}
    if not cited_urls <= declared_urls:
        return False

    source_topics: dict[str, set[str]] = {}
    for finding in raw_findings:
        source_url = _field(finding, "source_url")
        related_sub_topic = _normalized_text(
            _field(finding, "related_sub_topic")
        )
        if not isinstance(source_url, str) or not related_sub_topic:
            return False
        normalized_url = _normalized(source_url)
        if not normalized_url:
            return False
        source_topics.setdefault(normalized_url, set()).add(related_sub_topic)

    return all(
        any(
            source_topics.get(url) == {topic_title}
            for url in cited_urls
        )
        for topic_title in uncovered_topics
    )


def _conflict_represented_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    reference = case.expectations.reference
    signals = [
        signal
        for signal in reference.get("required_caveat_signals", [])
        if isinstance(signal, str)
    ]
    texts = [
        text
        for text in reference.get("conflicting_claim_texts", [])
        if isinstance(text, str)
    ]
    if not signals and not texts:
        return True
    report = _report_body(output).casefold()
    return any(signal.casefold() in report for signal in signals) or any(
        text.casefold() in report for text in texts
    )


def _no_overstatement_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    forbidden = [
        word
        for word in case.expectations.reference.get("forbidden_overstatement", [])
        if isinstance(word, str)
    ]
    if not forbidden:
        return True
    pattern = re.compile(
        r"\b(" + "|".join(map(re.escape, forbidden)) + r")\b",
        re.IGNORECASE,
    )
    return pattern.search(_report_body(output)) is None


def _rationale_present_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    critique = _artifact(output, "critique")
    rationale = _field(critique, "rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return False
    themes = [
        theme
        for theme in case.expectations.reference.get("reference_themes", [])
        if isinstance(theme, str)
    ]
    if not themes:
        return True
    folded = rationale.casefold()
    if any(theme.casefold() in folded for theme in themes):
        return True
    theme_words = {
        word.casefold()
        for theme in themes
        for word in theme.split()
        if len(word) >= 5
    }
    return bool(theme_words & set(folded.split()))


def _no_spurious_gaps_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    themes = [
        theme
        for theme in case.expectations.reference.get("reference_themes", [])
        if isinstance(theme, str)
    ]
    if not themes:
        return True
    stop_words = {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }

    def meaningful_tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", value.casefold())) - stop_words

    theme_tokens = [
        meaningful_tokens(theme)
        for theme in themes
    ]
    report = " ".join((case.state.report or "").split()).casefold()
    report_sentences = re.split(r"(?<=[.!?])\s+", report)
    report_clauses = [
        clause
        for sentence in report_sentences
        for clause in re.split(r"[,;:]", sentence)
    ]
    unresolved_markers = (
        "absence of",
        "do not yet exist",
        "insufficient",
        "main uncertainty",
        "not yet",
        "outstanding question",
        "still accumulating",
        "uncertain",
        "uncertainty",
        "unresolved",
    )

    def report_acknowledges_unresolved(tokens: set[str]) -> bool:
        return any(
            tokens <= meaningful_tokens(clause)
            and any(marker in clause for marker in unresolved_markers)
            for clause in report_clauses
        )

    critique = _typed_critique(output)
    if critique is None:
        return True
    for gap in critique.gaps:
        gap_tokens = meaningful_tokens(gap.problem)
        if any(
            tokens
            and tokens <= gap_tokens
            and not report_acknowledges_unresolved(tokens)
            for tokens in theme_tokens
        ):
            return False
    return True


def _gaps_actionable_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    question = " ".join((case.state.original_question or "").split()).casefold()
    critique = _artifact(output, "critique")
    queries = _field(critique, "recommended_queries")
    if not isinstance(queries, list):
        return False
    for query in queries:
        if not isinstance(query, str) or not query.strip():
            continue
        folded = " ".join(query.split()).casefold()
        if folded and folded not in question and question not in folded:
            return True
    return False


def _gaps_identified_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    known = [
        gap
        for gap in case.expectations.reference.get("known_gaps", [])
        if isinstance(gap, str)
    ]
    if not known:
        return True
    critique = _typed_critique(output)
    candidate_texts = [gap.problem for gap in critique.gaps] if critique else []
    candidate_texts += [
        item
        for item in (_field(_artifact(output, "critique"), "recommended_queries") or [])
        if isinstance(item, str)
    ]
    folded = " ".join(candidate_texts).casefold()
    return any(gap.casefold() in folded for gap in known)


def _route_discipline_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    critique = _artifact(output, "critique")
    if _field(critique, "should_continue") is True:
        return False
    return case.state.iteration >= case.state.max_iterations


def _declared_bound(case: EvaluationCase, key: str) -> float | None:
    """One numeric band bound the case declared, or ``None``."""
    value = case.expectations.reference.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _conservative_score_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """The score stays inside the band the case declares, both ways.

    The ceiling is what this metric was written for: a review that must reject
    the report cannot score it like one it accepted. The floor is the other
    half of the same claim — the defect is real and bounded, and a review that
    answers it by collapsing to the bottom of the scale has stopped grading
    the report at all, which a ceiling alone cannot see. A case that declares
    neither bound is not scored on its score, which is the honest reading of a
    band nobody stated.
    """
    minimum = _declared_bound(case, "minimum_score")
    maximum = _declared_bound(case, "maximum_score")
    if minimum is None and maximum is None:
        return True
    score = _field(_artifact(output, "critique"), "score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return False
    if maximum is not None and score > maximum:
        return False
    if minimum is not None and score < minimum:
        return False
    return True


def _material_typed_gaps(critique: Critique | None) -> list[CritiqueGap]:
    """The gaps this case scores: the ones that must be closed."""
    if critique is None:
        return []
    return [gap for gap in critique.gaps if gap.material]


def _typed_gaps_answer_with(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    expected_key: str,
    forbidden_key: str,
    selected: Callable[[CritiqueGap], str],
) -> bool:
    """Whether every material gap is answered with an allowed value.

    The shape the two typed metrics share, and the shape is the point. A
    case declares the values its defect may be answered with — the kinds it
    may be typed as, the repair actions it may be routed to — and the run has
    to name at least one material gap and stay inside them. A review that
    names no such gap scores zero rather than passing vacuously: the defect
    the case exists to carry would otherwise be free to go unnoticed, and
    saying nothing would be cheaper than saying the wrong thing. That is what
    makes both metrics fail a confident approval, which is the anti-abstention
    direction the case needs.

    A case that also declares *where* its defect lives — the claim clusters
    and targets the seeded defect is in — requires at least one material gap
    to name one of them. Without that, a correctly phrased gap against an
    unrelated claim was indistinguishable from the diagnosis the case is
    calibrating: the words are an answer about an obligation, and the
    obligation is part of the answer.
    """
    expected = {
        value
        for value in case.expectations.reference.get(expected_key, [])
        if isinstance(value, str)
    }
    forbidden = {
        value
        for value in case.expectations.reference.get(forbidden_key, [])
        if isinstance(value, str)
    }
    if not expected and not forbidden:
        return False
    gaps = _material_typed_gaps(_typed_critique(output))
    if not gaps:
        return False
    if not _names_the_declared_defect(gaps, case):
        return False
    for gap in gaps:
        value = selected(gap)
        if value in forbidden:
            return False
        if expected and value not in expected:
            return False
    return True


def _names_the_declared_defect(
    gaps: Sequence[CritiqueGap], case: EvaluationCase
) -> bool:
    """Whether one material gap names the obligation the case seeded.

    True when the case declares no defective ids at all: a case that does not
    say where its defect lives is not scored on naming it.
    """
    clusters = {
        value
        for value in case.expectations.reference.get("defective_cluster_ids", [])
        if isinstance(value, str)
    }
    targets = {
        value
        for value in case.expectations.reference.get("defective_target_ids", [])
        if isinstance(value, str)
    }
    if not clusters and not targets:
        return True
    return any(
        clusters.intersection(gap.claim_cluster_ids)
        or targets.intersection(gap.target_ids)
        for gap in gaps
    )


def _gap_kind_correct_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every material gap is diagnosed as a kind this defect may be.

    A defect that no number of pages can close — an identity defect, where
    the evidence the run already holds is not the evidence it claims — is a
    different failure from a hole in the plan, and the repair it needs is
    different too. Typing it ``coverage`` sends the run to fetch pages for a
    question that was never short of them.
    """
    return _typed_gaps_answer_with(
        output,
        case,
        expected_key="expected_gap_kinds",
        forbidden_key="forbidden_gap_kinds",
        selected=lambda gap: gap.kind,
    )


def _repair_action_routed_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every material gap routes to an action this defect can be closed by.

    Read beside ``gap_kind_correct``: the two are graded separately because
    a review can diagnose correctly and still route to a search, which is the
    misroute this case is about, and a case that scored the pair as one could
    not say which happened.
    """
    return _typed_gaps_answer_with(
        output,
        case,
        expected_key="expected_repair_actions",
        forbidden_key="forbidden_repair_actions",
        selected=lambda gap: gap.repair_action,
    )


METRIC_FUNCTIONS: dict[str, MetricFunction] = {
    # planner
    "subtopic_count": _subtopic_count_passes,
    "distinct_titles": _distinct_subtopics_passes,
    "priority_ordering": _strictly_increasing_priorities,
    "query_quality": _query_quality_passes,
    "question_preserved": _question_preserved_passes,
    "balanced_coverage": _balanced_coverage_passes,
    "no_invented_constraints": _no_invented_constraints_passes,
    "plan_still_valid": _plan_still_valid_passes,
    "failure_recorded": _failure_recorded_passes,
    "bounded_recovery": _bounded_recovery_passes,
    "targets_declared": _targets_declared_passes,
    "dimensions_are_checkable": _dimensions_are_checkable_passes,
    "support_policy_not_downgraded": _support_policy_not_downgraded_passes,
    "no_vague_dimensions": _no_vague_dimensions_passes,
    # researcher
    "sub_topic_coverage": _sub_topic_covered_passes,
    "source_grounding": _source_grounding_passes,
    "source_diversity": _source_diversity_passes,
    "budget_respected": _bounded_recovery_passes,
    "uncertainty_preserved": _uncertainty_preserved_passes,
    "no_false_consensus": _no_false_consensus_passes,
    "partial_results_present": _partial_results_present_passes,
    "no_invented_sources": _no_invented_sources_passes,
    "sources_are_real_urls": _sources_are_real_urls_passes,
    "findings_are_read_bearing": _findings_are_read_bearing_passes,
    "no_recall_only_source": _no_recall_only_source_passes,
    # source evaluator
    "one_evaluation_per_source": _one_evaluation_per_source_passes,
    "score_ordering": _score_ordering_passes,
    "bounded_scores": _bounded_scores_passes,
    "low_confidence_flagged": _low_confidence_flagged_passes,
    "balanced_scoring": _balanced_scoring_passes,
    "rationale_mentions_multiple_signals": _rationale_signals_passes,
    "all_sources_still_scored": _one_evaluation_per_source_passes,
    "fallback_scores_bounded": _fallback_scores_bounded_passes,
    "no_fabricated_reputation": _no_fabricated_reputation_passes,
    "mirror_not_a_new_work": _mirror_not_a_new_work_passes,
    "independent_work_recognized": _independent_work_recognized_passes,
    # fact checker
    "verdict_correctness": _verdict_correctness_passes,
    "evidence_linked": _evidence_linked_passes,
    "confidence_calibrated": _confidence_calibrated_passes,
    "sources_known": _sources_known_passes,
    "independence_enforced": _independent_domains_passes,
    "no_false_independent_pair": _no_false_independent_pair_passes,
    "conservative_on_failure": _conservative_on_failure_passes,
    "partial_verification_present": _partial_verification_present_passes,
    # synthesizer
    "reader_markdown_present": _reader_markdown_present_passes,
    "evidence_markdown_present": _evidence_markdown_present_passes,
    "no_persistence_calls": _no_persistence_calls_passes,
    "no_false_publication_claim": _no_false_publication_claim_passes,
    "citations_locally_derived": _citations_locally_derived_passes,
    "one_reference_per_work": _one_reference_per_work_passes,
    # Legacy aliases remain readable for pre-Task-6 artifacts; active Task 6
    # cases use the explicit composition names above.
    "report_present": _report_present_in_state_passes,
    "citations_known": _citations_known_only_passes,
    "coverage": _coverage_passes,
    "limitations_present": _limitations_passes,
    "conflict_represented": _conflict_represented_passes,
    "no_overstatement": _no_overstatement_passes,
    "report_present_in_state": _report_present_in_state_passes,
    # critic
    "score_bounded": _bounded_score_passes,
    "route_consistent": _route_consistent_passes,
    "rationale_present": _rationale_present_passes,
    "no_spurious_gaps": _no_spurious_gaps_passes,
    "gaps_actionable": _gaps_actionable_passes,
    "gaps_identified": _gaps_identified_passes,
    "route_discipline": _route_discipline_passes,
    "conservative_score": _conservative_score_passes,
    "gap_kind_correct": _gap_kind_correct_passes,
    "repair_action_routed": _repair_action_routed_passes,
}

_CASE_METRIC_IDS = {
    metric.metric_id
    for case in all_cases()
    for metric in case.expectations.deterministic_metrics
}
if not _CASE_METRIC_IDS.issubset(METRIC_FUNCTIONS):
    raise RuntimeError(
        "every case metric needs an implementation; missing: "
        + ", ".join(sorted(_CASE_METRIC_IDS - set(METRIC_FUNCTIONS)))
    )


# --- The LangSmith code-evaluator adapter -----------------------------------


def format_code_evaluator_feedback(
    report: GateReport, quality: float
) -> dict[str, list[dict[str, object]]]:
    """Format an already-computed gate report and quality score."""
    results: list[dict[str, object]] = [
        {
            "key": f"gate:{result.gate_id}",
            "score": 1 if result.passed else 0,
            "comment": result.detail,
        }
        for result in report.results
    ]
    results.append(
        {
            "key": "hard_gates_passed",
            "score": 1 if report.passed else 0,
            "comment": "",
        }
    )
    results.append({"key": "deterministic_quality", "score": quality, "comment": ""})
    return {"results": results}


def code_evaluator(
    case: EvaluationCase, *, secrets: Sequence[str]
) -> Callable[[Run, Example], dict]:
    """A synchronous LangSmith evaluator for one case's repetitions.

    Every gate becomes ``{"key": f"gate:{gate_id}", "score": 0/1,
    "comment": detail}``; ``hard_gates_passed`` and
    ``deterministic_quality`` carry the aggregates. A run whose outputs do
    not parse as a ``TargetOutput`` scores zero on both aggregates — one
    broken row must not abort the rest of the experiment.
    """

    def evaluate(run: Run, example: Example) -> dict:
        try:
            output = TargetOutput.model_validate(run.outputs)
        except ValidationError as error:
            return {
                "results": [
                    {
                        "key": "hard_gates_passed",
                        "score": 0,
                        "comment": f"target output failed validation: {error}",
                    },
                    {
                        "key": "deterministic_quality",
                        "score": 0.0,
                        "comment": "target output failed validation",
                    },
                ]
            }
        report, quality = evaluate_target(output, case, secrets=secrets)
        return format_code_evaluator_feedback(report, quality)

    return evaluate
