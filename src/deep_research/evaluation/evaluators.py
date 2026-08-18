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
from typing import TypeAlias

from langsmith.schemas import Example, Run
from pydantic import ValidationError

from deep_research.agents.critic import route_decision
from deep_research.agents.fact_checker import (
    claimed_domains_for,
    independent_domains,
)
from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.config import contains_secret
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    GateReport,
    GateResult,
    TargetOutput,
)
from deep_research.utils.types import Claim, ScoredSource, SubTopic

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
    missing = [
        name
        for name in case.expectations.required_output_fields
        if name not in result
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
    react = output.react
    if react is None:
        return GateResult(
            gate_id="budgets_respected",
            passed=False,
            detail="react summary missing; budget cannot be assessed",
        )
    iterations = _field(react, "iterations")
    tool_calls = _field(react, "tool_calls")
    violations: list[str] = []
    if (
        not isinstance(iterations, int)
        or iterations > case.expectations.max_iterations
    ):
        violations.append(
            f"iterations {iterations} exceed "
            f"{case.expectations.max_iterations}"
        )
    if (
        not isinstance(tool_calls, int)
        or tool_calls > case.expectations.max_tool_calls
    ):
        violations.append(
            f"tool_calls {tool_calls} exceed "
            f"{case.expectations.max_tool_calls}"
        )
    return GateResult(
        gate_id="budgets_respected",
        passed=not violations,
        detail="; ".join(violations),
    )


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
        normalize_source_url(url) for url in expectations.known_source_urls
    }
    scripted = _field(output.evidence, "scripted_search_urls") or ()
    allowed.update(
        normalize_source_url(url) for url in scripted if isinstance(url, str)
    )
    evidence_sources = _field(output.evidence, "sources") or ()
    for source in evidence_sources:
        url = _field(source, "url")
        if isinstance(url, str):
            allowed.add(normalize_source_url(url))
    evidence_findings = _field(output.evidence, "findings") or ()
    for finding in evidence_findings:
        url = _field(finding, "source_url")
        if isinstance(url, str):
            allowed.add(normalize_source_url(url))
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
    cited = {
        normalize_source_url(url)
        for url in (
            _url_like_strings(output.result)
            | _url_like_strings(output.state_update)
        )
    }
    unknown = sorted(cited - allowed)
    return GateResult(
        gate_id="citations_known",
        passed=not unknown,
        detail=(
            "unknown source urls: " + ", ".join(unknown) if unknown else ""
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
    score = 0.0
    for metric in case.expectations.deterministic_metrics:
        function = metric_functions.get(metric.metric_id)
        if function is None:
            raise MissingMetricError(
                f"no metric function registered for {metric.metric_id!r}"
            )
        try:
            if function(output, case):
                score += metric.weight
        except Exception:
            # One broken metric must not lose every other metric's score.
            continue
    return score


def evaluate_target(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
    metric_functions: Mapping[str, MetricFunction] | None = None,
) -> tuple[GateReport, float]:
    """General and agent-specific gates plus the deterministic score.

    The general gates come first, in ``GENERAL_GATE_IDS`` order, then the
    agent's own gates in ``AGENT_GATE_IDS`` order. ``metric_functions``
    defaults to the complete ``METRIC_FUNCTIONS`` table.
    """
    if metric_functions is None:
        metric_functions = METRIC_FUNCTIONS
    results = evaluate_general_gates(output, case, secrets=secrets)
    results.extend(evaluate_agent_gates(output, case))
    score = deterministic_quality(
        output, case, metric_functions=metric_functions
    )
    return GateReport(results=results), score


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
        "persistence_truthful",
    ),
    "critic": (
        "bounded_component_scores",
        "critique_actionable",
        "route_consistent",
    ),
}

# Derived from ``ScoredSource`` itself, never hardcoded: if a score field
# is added or renamed the bounded-score checks follow without a second
# place to forget.
_SCORE_FIELDS: tuple[str, ...] = tuple(
    name for name in ScoredSource.model_fields if name.endswith("_score")
)

_LIMITATION_PHRASES = ("limitation", "caveat", "what we could not")
_SIGNAL_KEYWORDS = ("authority", "recency", "reputation", "corroboration")
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
    report = _artifact(output, "report")
    return report if isinstance(report, str) else ""


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


def _forbidden_persistence_claims(reference: Mapping) -> list[str]:
    claims = reference.get("forbidden_persistence_claims")
    if isinstance(claims, list):
        return [str(item) for item in claims]
    return []


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
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        url = _field(finding, "source_url")
        if not isinstance(url, str) or normalize_source_url(url) not in allowed:
            return False
    return True


def _gate_no_invented_sources(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _no_invented_sources_passes(output, case)
    return _agent_result(
        "no_invented_sources",
        passed,
        "" if passed else "a finding cites a url outside the known sources",
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
        if _field(entry, "low_confidence") is True
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


def _evidence_linked_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        if _field(entry, "verdict") == "insufficient_evidence":
            continue
        evidence = _field(entry, "evidence")
        source_urls = _field(entry, "source_urls")
        if not isinstance(evidence, list) or not isinstance(source_urls, list):
            return False
        if not any(isinstance(item, str) and item.strip() for item in evidence):
            return False
        if not any(isinstance(item, str) and item.strip() for item in source_urls):
            return False
    return True


def _gate_evidence_linked(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _evidence_linked_passes(output, case)
    return _agent_result(
        "evidence_linked",
        passed,
        "" if passed else "a non-insufficient claim lacks evidence or sources",
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
    # What counts as retrieved evidence: the verification prompt only asks
    # the model to "quote or closely paraphrase" the retrieved passages
    # (``CLAIM_VERIFICATION_INSTRUCTION``), so URLs in the evidence strings
    # are optional. Count every URL the repetition recorded instead, the
    # way ``_no_invented_sources_passes`` does:
    #   - URL-like strings in trajectory thoughts/observation summaries
    #     (the verification loop's web_search results land there), and
    #   - ``scripted_search_urls`` on the evidence context (controlled
    #     runs).
    # Deliberately NOT counted: ``EvidenceContext.sources/findings/claims``
    # — those are the input evidence the agent was given, i.e. the claim's
    # own source set, which must never double as corroboration.
    recorded_urls: list[str] = []
    for step in output.trajectory:
        recorded_urls.extend(_url_like_strings(_field(step, "thought")))
        recorded_urls.extend(
            _url_like_strings(_field(step, "observation_summary"))
        )
    recorded_urls.extend(
        url
        for url in (_field(output.evidence, "scripted_search_urls") or ())
        if isinstance(url, str)
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
        evidence_urls = list(source_urls)
        for string in _field(entry, "evidence") or []:
            if isinstance(string, str):
                evidence_urls.extend(
                    url.rstrip(_URL_TRAILING_PUNCTUATION)
                    for url in _URL_PATTERN.findall(string)
                )
        evidence_urls.extend(recorded_urls)
        independent = independent_domains(
            evidence_urls, claimed_domains=claimed
        )
        if _registrable_family_count(independent, family=family) < minimum:
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
    passed = bool(report.strip())
    return _agent_result(
        "valid_report", passed, "" if passed else "report is missing or blank"
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


def _persistence_truthful_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    writes = _field(output.dependencies, "document_writes")
    if isinstance(writes, int) and writes > 0:
        return True
    report = _report_body(output).casefold()
    forbidden = _forbidden_persistence_claims(case.expectations.reference)
    if any(phrase.casefold() in report for phrase in forbidden):
        return False
    return "output_path" not in _state_update(output)


def _gate_persistence_truthful(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _persistence_truthful_passes(output, case)
    return _agent_result(
        "persistence_truthful",
        passed,
        "" if passed else "a persistence claim has no matching write_document call",
    )


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
    critique = _artifact(output, "critique")
    if _field(critique, "should_continue") is not True:
        return True
    gaps = _field(critique, "gaps")
    queries = _field(critique, "recommended_queries")
    if isinstance(gaps, list) and any(
        isinstance(item, str) and item.strip() for item in gaps
    ):
        return True
    if isinstance(queries, list) and any(
        isinstance(item, str) and item.strip() for item in queries
    ):
        return True
    return False


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
    critique = _artifact(output, "critique")
    score = _field(critique, "score")
    if not isinstance(score, int) or isinstance(score, bool):
        return False
    gaps = [
        item
        for item in (_field(critique, "gaps") or [])
        if isinstance(item, str)
    ]
    unsupported = [
        item
        for item in (_field(critique, "unsupported_claims") or [])
        if isinstance(item, str)
    ]
    try:
        expected, _ = route_decision(
            score=score,
            gaps=gaps,
            unsupported_claims=unsupported,
            iteration=case.state.iteration,
            max_iterations=case.state.max_iterations,
            has_report=case.state.report is not None,
        )
    except (TypeError, ValueError):
        return False
    return expected is (_field(critique, "should_continue") is True)


def _gate_route_consistent(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    passed = _route_consistent_passes(output, case)
    return _agent_result(
        "route_consistent",
        passed,
        "" if passed else "should_continue disagrees with route_decision",
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
        "persistence_truthful": _gate_persistence_truthful,
    },
    "critic": {
        "bounded_component_scores": _gate_bounded_component_scores,
        "critique_actionable": _gate_critique_actionable,
        "route_consistent": _gate_route_consistent,
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
    findings = _artifact(output, "findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        url = _field(finding, "source_url")
        if not isinstance(url, str) or normalize_source_url(url) not in trajectory_urls:
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
        "corroboration_score",
    )
    evaluated = _artifact(output, "evaluated_sources")
    if not isinstance(evaluated, list):
        return False
    for entry in evaluated:
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


def _verdict_correctness_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    expected = case.expectations.reference.get("expected_verdicts")
    if not isinstance(expected, Mapping):
        return True
    claims = _artifact(output, "verified_claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        text = _field(entry, "text")
        if not isinstance(text, str):
            continue
        wanted = expected.get(" ".join(text.split()))
        if wanted is None:
            continue
        if _field(entry, "verdict") != wanted:
            return False
    return True


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


def _coverage_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    report = _report_body(output).casefold()
    return all(
        _normalized_text(topic.title) in report for topic in case.state.sub_topics
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


def _no_false_persistence_claim_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    report = _report_body(output).casefold()
    forbidden = _forbidden_persistence_claims(case.expectations.reference)
    if any(phrase.casefold() in report for phrase in forbidden):
        return False
    return "output_path" not in _state_update(output)


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
    theme_words = {
        word.casefold()
        for theme in themes
        for word in theme.split()
        if len(word) >= 5
    }
    critique = _artifact(output, "critique")
    gaps = _field(critique, "gaps")
    if not isinstance(gaps, list):
        return True
    for gap in gaps:
        if not isinstance(gap, str):
            continue
        folded = " ".join(gap.split()).casefold()
        if any(theme.casefold() in folded for theme in themes):
            return False
        if theme_words & set(folded.split()):
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
    critique = _artifact(output, "critique")
    candidate_texts = [
        item for item in (_field(critique, "gaps") or []) if isinstance(item, str)
    ]
    candidate_texts += [
        item
        for item in (_field(critique, "recommended_queries") or [])
        if isinstance(item, str)
    ]
    folded = " ".join(candidate_texts).casefold()
    return any(gap.casefold() in folded for gap in known)


def _route_discipline_passes(output: TargetOutput, case: EvaluationCase) -> bool:
    critique = _artifact(output, "critique")
    if _field(critique, "should_continue") is True:
        return False
    return case.state.iteration >= case.state.max_iterations


def _conservative_score_passes(
    output: TargetOutput, case: EvaluationCase
) -> bool:
    maximum = case.expectations.reference.get("maximum_score")
    if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
        return True
    critique = _artifact(output, "critique")
    score = _field(critique, "score")
    return (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and score <= maximum
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
    # fact checker
    "verdict_correctness": _verdict_correctness_passes,
    "evidence_linked": _evidence_linked_passes,
    "confidence_calibrated": _confidence_calibrated_passes,
    "sources_known": _sources_known_passes,
    "independence_enforced": _independent_domains_passes,
    "conservative_on_failure": _conservative_on_failure_passes,
    "partial_verification_present": _partial_verification_present_passes,
    # synthesizer
    "report_present": _report_present_in_state_passes,
    "citations_known": _citations_known_only_passes,
    "coverage": _coverage_passes,
    "limitations_present": _limitations_passes,
    "persistence_truthful": _persistence_truthful_passes,
    "conflict_represented": _conflict_represented_passes,
    "no_overstatement": _no_overstatement_passes,
    "report_present_in_state": _report_present_in_state_passes,
    "no_false_persistence_claim": _no_false_persistence_claim_passes,
    # critic
    "score_bounded": _bounded_score_passes,
    "route_consistent": _route_consistent_passes,
    "rationale_present": _rationale_present_passes,
    "no_spurious_gaps": _no_spurious_gaps_passes,
    "gaps_actionable": _gaps_actionable_passes,
    "gaps_identified": _gaps_identified_passes,
    "route_discipline": _route_discipline_passes,
    "conservative_score": _conservative_score_passes,
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
        results = [
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
        results.append(
            {"key": "deterministic_quality", "score": quality, "comment": ""}
        )
        return {"results": results}

    return evaluate
