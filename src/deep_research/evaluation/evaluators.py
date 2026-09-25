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

from deep_research.agents.evidence import excerpt_matches
from deep_research.agents.evidence_verifier import read_text
from deep_research.agents.report import collapse_mirror_urls
from deep_research.agents.sources import (
    normalize_source_url,
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
    EvidenceTarget,
    Finding,
    FindingVerification,
    ReadRecord,
    ScoredSource,
    SubTopic,
    UnitScore,
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
    "evidence_verifier": (
        "verification_recorded",
        "no_invented_evidence",
        "drop_reasons_named",
    ),
    "report_writer": (
        "valid_report",
        "citations_known_only",
        "refusals_logged",
        "no_persistence_calls",
        "no_false_publication_claim",
    ),
}

# Derived from ``ScoredSource`` itself, never hardcoded: if a score field
# is added or renamed the bounded-score checks follow without a second
# place to forget.
_SCORE_FIELDS: tuple[str, ...] = tuple(
    name for name in ScoredSource.model_fields if name.endswith("_score")
)

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


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


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


# --- Evidence verifier gates -----------------------------------------------


def _verified_findings(output: TargetOutput) -> list[Finding] | None:
    """Every finding this run judged, typed, or ``None`` when unreadable.

    Read from the result's ``findings`` — the verifier's own
    ``VerifiedFindings``, which is what the run returned and is therefore the
    set it judged. The state update's ``verified_findings`` is deliberately
    **not** a substitute: ``EvidenceVerifierAgent.run`` merges its judgement
    onto the snapshot the state already carried (``[*state.verified_findings,
    *judged]``), so that key holds every earlier pass's findings beside this
    run's, and grading from it would score a set this repetition did not
    judge. A payload that does not validate is refused rather than guessed at:
    these gates exist to prove a judgement happened, and an unreadable
    snapshot proves nothing.
    """
    payload = dict(output.result or {}).get("findings") if isinstance(
        output.result, Mapping
    ) else None
    if not isinstance(payload, (list, tuple)):
        return None
    try:
        return [Finding.model_validate(item) for item in payload]
    except ValidationError:
        return None


def _finding_reads(case: EvaluationCase) -> dict[str, ReadRecord]:
    """The pages this case seeded, keyed by read id."""
    return dict(_field(case.state, "read_records") or {})


def _verification_recorded_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every finding this run produced carries the verifier's judgement.

    ``None`` for a finding means the run never judged it, which is exactly
    what no later pass may treat as evidence, so this fails closed — on an
    unreadable payload as much as on an unjudged finding.
    """
    del case
    findings = _verified_findings(output)
    if not findings:
        return False
    return all(finding.verification is not None for finding in findings)


def _no_invented_evidence_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every kept figure's evidence words are words its own page carries.

    §5.2's rule, re-derived from the artifact rather than trusted: the run
    records the words the Context Check returned, and this predicate proves
    the page the finding cites really contains them. A kept figure with no
    evidence words is an unchecked context (PD-26) and states nothing to
    invent; a figure whose words are not on its page is an invention however
    the checker worded its verdict, and a finding whose read is not among the
    seeded pages cannot be proven at all.
    """
    findings = _verified_findings(output)
    if not findings:
        return False
    reads = _finding_reads(case)
    for finding in findings:
        verification = finding.verification
        if verification is None:
            continue
        for result in verification.figure_results:
            if not result.kept or not result.evidence_words:
                continue
            read = reads.get(finding.read_id or "")
            if read is None or not excerpt_matches(
                read_text(read), result.evidence_words
            ):
                return False
    return True


def _drop_reasons_named_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every figure a run did not keep names why it did not keep it.

    The typed contract already refuses the shape — ``FindingVerification``
    insists a dropped finding carries its reason, and ``FigureResult`` refuses
    a figure that is neither kept (with its confirmed context) nor dropped
    (with its reason) — so this predicate is the fail-closed re-check on the
    serialized artifact: a drop a reader cannot explain is a silent loss, and
    a snapshot that cannot be read back is not evidence that it happened.
    """
    del case
    findings = _verified_findings(output)
    if not findings:
        return False
    for finding in findings:
        verification = finding.verification
        if verification is None:
            return False
        for result in verification.figure_results:
            if not result.kept and not result.dropped_reason:
                return False
    return True


def _expected_outcome_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """The verifier's judgement is the one this case was built to produce.

    Each declared outcome names the page and what a run must record for it:
    the finding's status, the finding's drop reason where it was dropped, the
    figure's drop reason where a figure was dropped, and the confirmed
    period, scope, kind, attribution and organisation of the figure it kept.
    This is what makes a controlled case a test rather than a fixture — the
    scope correction, the relay's credit and the invented-words refusal are
    all pinned here — and it is a metric rather than a gate because a
    near-miss (the right status, the wrong scope) should cost weight rather
    than fail the run outright.
    """
    outcomes = [
        item
        for item in case.expectations.reference.get("expected_outcomes") or []
        if isinstance(item, Mapping)
    ]
    if not outcomes:
        return False
    findings = _verified_findings(output)
    if not findings:
        return False
    by_url: dict[str, Finding] = {}
    for finding in findings:
        by_url.setdefault(_normalized(finding.source_url), finding)
    for outcome in outcomes:
        url = _normalized(str(outcome.get("source_url") or ""))
        finding = by_url.get(url)
        if finding is None or finding.verification is None:
            return False
        verification = finding.verification
        status = outcome.get("status")
        if isinstance(status, str) and verification.status != status:
            return False
        finding_reason = outcome.get("finding_drop_reason")
        if (
            isinstance(finding_reason, str)
            and verification.dropped_reason != finding_reason
        ):
            return False
        if not _figure_outcome_matches(verification, outcome):
            return False
    return True


def _figure_outcome_matches(
    verification: FindingVerification, outcome: Mapping
) -> bool:
    """One declared outcome's figure clauses, against a real verification."""
    figure_reason = outcome.get("figure_drop_reason")
    if isinstance(figure_reason, str) and not any(
        result.dropped_reason == figure_reason
        for result in verification.figure_results
    ):
        return False
    fields = ("period", "scope", "kind", "attribution", "organisation")
    declared = {name: outcome[name] for name in fields if name in outcome}
    if not declared:
        return True
    kept = [
        result
        for result in verification.figure_results
        if result.kept and result.context is not None
    ]
    if not kept:
        return False
    context = kept[0].context
    assert context is not None
    return all(getattr(context, name) == value for name, value in declared.items())


def _gate_verification_recorded(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _verification_recorded_passes(output, case)
    return _agent_result(
        "verification_recorded",
        passed,
        "" if passed else "a finding this run produced carries no verification",
    )


def _gate_no_invented_evidence(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _no_invented_evidence_passes(output, case)
    return _agent_result(
        "no_invented_evidence",
        passed,
        ""
        if passed
        else "a kept figure's evidence words are not on the page it cites",
    )


def _gate_drop_reasons_named(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _drop_reasons_named_passes(output, case)
    return _agent_result(
        "drop_reasons_named",
        passed,
        "" if passed else "a dropped figure or finding names no reason",
    )


# --- Report writer gates -----------------------------------------------------


def _required_target_ids(case: EvaluationCase) -> list[str]:
    """Every required obligation the plan stamped, in plan order."""
    return [
        target.target_id
        for topic in case.state.sub_topics
        for target in counted_evidence_targets(topic.evidence_targets)
        if target.required
    ]


def _accounted_target_ids(
    output: TargetOutput,
) -> tuple[set[str], set[str]] | None:
    """The composition's answered and Not-found target ids, or ``None``.

    Read from the composition the writer returned rather than from rendered
    prose: the Key Facts rows record the targets their figures answer, and the
    Not found list is the composition's own record of the ones nothing
    answered.
    """
    composition = _artifact(output, "composition")
    if not isinstance(composition, Mapping):
        return None
    answered = {
        str(target_id)
        for row in composition.get("fact_rows") or []
        if isinstance(row, Mapping)
        for target_id in row.get("target_ids") or []
    }
    not_found = {
        str(target.get("target_id"))
        for target in composition.get("not_found") or []
        if isinstance(target, Mapping)
    }
    return answered, not_found


def _valid_report_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    """Both artifacts are present, and every required target is accounted for.

    Presence alone would pass a report that silently dropped an obligation,
    which is the failure §6.1 item 5 exists to prevent: a required target the
    run could not answer has to be listed under Not found, so it is either
    answered by a figure or named as missing, never absent from both.
    """
    report = _report_body(output)
    evidence = _evidence_body(output)
    if not report.strip() or not evidence.strip():
        return False
    accounted = _accounted_target_ids(output)
    if accounted is None:
        return False
    answered, not_found = accounted
    return set(_required_target_ids(case)) <= answered | not_found


def _gate_valid_report(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _valid_report_passes(output, case)
    return _agent_result(
        "valid_report",
        passed,
        ""
        if passed
        else "reader markdown or evidence markdown is missing or blank, or a "
        "required target is neither answered nor listed under Not found",
    )


def _reported_citation_urls(output: TargetOutput) -> set[str]:
    """Every URL the reader report prints, normalized."""
    return {
        normalized
        for url in _URL_PATTERN.findall(_report_body(output))
        for normalized in [_normalized(url.rstrip(_URL_TRAILING_PUNCTUATION))]
        if normalized
    }


def _finding_urls(output: TargetOutput) -> set[str] | None:
    """Every page the composition's own findings carry, or ``None``.

    This is the writer's whole citation vocabulary: §6.1 renders the
    reference list from the findings a pass composed, so a URL no finding
    carries is a citation the report cannot have derived — whatever the case
    declares as known.
    """
    composition = _artifact(output, "composition")
    if not isinstance(composition, Mapping):
        return None
    urls: set[str] = set()
    for item in composition.get("findings") or []:
        if not isinstance(item, Mapping):
            continue
        url = item.get("source_url")
        if isinstance(url, str):
            normalized = _normalized(url)
            if normalized:
                urls.add(normalized)
    return urls


def _citations_known_only_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    del case
    allowed = _finding_urls(output)
    if not allowed:
        return False
    return _reported_citation_urls(output) <= allowed


def _gate_citations_known_only(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _citations_known_only_passes(output, case)
    return _agent_result(
        "citations_known_only",
        passed,
        "" if passed else "the report cites a url outside the known sources",
    )


def _refusals_logged_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every refusal and every drop is published with the reason it earned.

    §6.1 item 7: the evidence log prints every finding, every drop and every
    refused sentence. A refusal that reaches no reader, or a drop published
    without its reason, is a fact quietly removed from the run's record — so
    the drafted text of each refused point, and the reason of each dropped
    figure and finding, has to appear in the artifact itself.
    """
    del case
    evidence = _evidence_body(output)
    if not evidence.strip():
        return False
    composition = _artifact(output, "composition")
    if not isinstance(composition, Mapping):
        return False
    printed = " ".join(evidence.split())
    for refused in composition.get("rejected_points") or []:
        if not isinstance(refused, Mapping):
            return False
        reason = refused.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return False
        text = " ".join(str(refused.get("text") or "").split())
        if text and text not in printed:
            return False
    for item in composition.get("findings") or []:
        if not isinstance(item, Mapping):
            continue
        verification = item.get("verification")
        if not isinstance(verification, Mapping):
            continue
        if verification.get("status") == "dropped":
            reason = verification.get("dropped_reason")
            if not isinstance(reason, str) or f"dropped ({reason})" not in printed:
                return False
        for result in verification.get("figure_results") or []:
            if not isinstance(result, Mapping):
                continue
            reason = result.get("dropped_reason")
            if reason and f"dropped ({reason})" not in printed:
                return False
    return True


def _gate_refusals_logged(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _refusals_logged_passes(output, case)
    return _agent_result(
        "refusals_logged",
        passed,
        ""
        if passed
        else "a refused sentence or a dropped figure is not published with "
        "its reason",
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
    # The publication paths are written only by the terminal finalizer, from
    # a write that actually succeeded. A composition pass that records one has
    # claimed a publication it never performed.
    for key in (
        "path",
        "output_path",
        "report_path",
        "evidence_path",
        "quality_path",
    ):
        if result.get(key) is not None or state_update.get(key) is not None:
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

    The assessed rows and the verified findings, in that order: §6.1 numbers
    the reader's references from the findings a pass composed, and the
    assessed rows are what name each page's readable copy, so this cannot
    drift from the URLs a composing run would derive.
    """
    urls: list[str] = []
    for source in _field(case.state, "evaluated_sources") or ():
        url = _field(source, "url")
        if isinstance(url, str):
            normalized = _normalized(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
    for finding in _field(case.state, "verified_findings") or ():
        url = _field(finding, "source_url")
        if isinstance(url, str):
            normalized = _normalized(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
    return urls


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


def _statements_labelled_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every printed statement cites at least one known finding label.

    §6.2's rule, re-checked from the artifact: code keeps only points that
    cite a known label and attaches that finding's id to the statement it
    builds, so a statement with no finding id — or with one no label in the
    registry points at — is prose the reader has no way to trace.
    """
    del case
    composition = _artifact(output, "composition")
    if not isinstance(composition, Mapping):
        return False
    labels = composition.get("finding_labels")
    if not isinstance(labels, Mapping) or not labels:
        return False
    known = {str(value) for value in labels.values()}
    points: list[object] = []
    points.extend(composition.get("summary") or [])
    for section in composition.get("sections") or []:
        if isinstance(section, Mapping):
            points.extend(section.get("points") or [])
    if not points:
        return False
    for point in points:
        statement = point.get("statement") if isinstance(point, Mapping) else None
        if not isinstance(statement, Mapping):
            return False
        ids = statement.get("finding_ids")
        if not isinstance(ids, (list, tuple)) or not ids:
            return False
        if not {str(item) for item in ids} <= known:
            return False
    return True


def _conflicting_figures_published_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    """Every page the case names as conflicting appears in the references.

    A report that prints one forecaster's figure and drops the other has
    resolved a disagreement the evidence did not resolve — the failure this
    case exists to catch, and one no wording check may make (D8): what is
    graded is that both findings stayed citable.
    """
    conflicting = {
        normalized
        for url in _reference_strings(case, "conflicting_urls")
        for normalized in [_normalized(url)]
        if normalized
    }
    if not conflicting:
        return False
    return conflicting <= _listed_citation_urls(output)


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
    "evidence_verifier": {
        "verification_recorded": _gate_verification_recorded,
        "no_invented_evidence": _gate_no_invented_evidence,
        "drop_reasons_named": _gate_drop_reasons_named,
    },
    "report_writer": {
        "valid_report": _gate_valid_report,
        "citations_known_only": _gate_citations_known_only,
        "refusals_logged": _gate_refusals_logged,
        "no_persistence_calls": _gate_no_persistence_calls,
        "no_false_publication_claim": _gate_no_false_publication_claim,
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

# The vocabulary a planner-declared dimension is checked against. The
# claim-era version of this check filled every field of a probe object
# representing one checkable assertion and asked the deleted claim-cluster
# credit machinery whether a dimension matched one of its fields (D6/D8
# deleted that machinery with the Fact Checker and the claim clusters). What
# that reduced to for this caller — which always passed
# ``match_qualifiers=False`` — was lexical: a dimension is checkable when it
# starts with one of the three contract-wide prefixes, or when its own words
# overlap one of the signal groups below.
_DIMENSION_SIGNAL_WORDS: tuple[frozenset[str], ...] = (
    frozenset({"period", "time", "date", "year", "when", "horizon", "recency"}),
    frozenset(
        {"geography", "region", "place", "location", "country", "jurisdiction"}
    ),
    frozenset(
        {
            "scale", "magnitude", "quantity", "size", "capacity", "amount",
            "value", "rate", "level",
        }
    ),
    frozenset({"unit"}),
    frozenset({"population", "subject", "entity", "who"}),
    frozenset(
        {"mechanism", "how", "cause", "driver", "method", "instrument"}
    ),
    frozenset({"attribution", "source", "issuer", "publisher"}),
    frozenset({"share", "proportion", "percent", "denominator", "comparison"}),
)


def _dimension_is_checkable(dimension: str) -> bool:
    """Whether a planner-declared dimension names a concept evidence could fill."""
    folded = dimension.casefold()
    if folded.startswith(("answer form:", "evidence period:", "measure:")):
        return True
    tokens = {
        token.strip(".,:;()")
        for token in dimension.replace("-", " ").replace("_", " ").casefold().split()
    }
    return any(tokens & signals for signals in _DIMENSION_SIGNAL_WORDS)


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
    evidence could ever answer it, so the check is made against the signal
    vocabulary rather than against a list of acceptable wordings.

    Every required dimension must be creditable, not merely one of them: an
    obligation carrying one uncreditable dimension beside a creditable one can
    never be answered by any statement, so reading the check as a truthy/falsey
    whole would call that plan checkable and hand it the metric's weight.
    """
    targets = _counted_targets(output)
    if not targets:
        return False
    return all(
        all(_dimension_is_checkable(dimension) for dimension in target.required_dimensions)
        for target in targets
    )


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


def _read_url_identities(output: TargetOutput) -> set[str] | None:
    """Identities of the URLs this repetition proved it READ, or ``None``.

    ``None`` means the artifact cannot prove any read at all, and a
    read-provenance check must then fail closed: the field is absent, the
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
    # evidence verifier
    "verification_recorded": _verification_recorded_passes,
    "no_invented_evidence": _no_invented_evidence_passes,
    "drop_reasons_named": _drop_reasons_named_passes,
    "expected_outcome": _expected_outcome_passes,
    # report writer
    "reader_markdown_present": _reader_markdown_present_passes,
    "evidence_markdown_present": _evidence_markdown_present_passes,
    "citations_locally_derived": _citations_locally_derived_passes,
    "one_reference_per_work": _one_reference_per_work_passes,
    "statements_labelled": _statements_labelled_passes,
    "conflicting_figures_published": _conflicting_figures_published_passes,
    "refusals_logged": _refusals_logged_passes,
    "no_persistence_calls": _no_persistence_calls_passes,
    "no_false_publication_claim": _no_false_publication_claim_passes,
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
