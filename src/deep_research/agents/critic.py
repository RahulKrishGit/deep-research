"""The Critic: score the report and recommend whether research continues.

The provider is asked for ``CritiqueDraft`` and never for ``Critique``:
``Critique`` declares ``CriticScore`` (1..10) and a non-blank rationale,
which strict structured outputs reject. Local code stamps the parts the
model must not be trusted with — the clamped score, the de-duplicated
notes, and above all the routing decision.

Routing convention: ``route_decision`` checks the iteration bound *first*.
"The critic must not continue forever" is the one rule no model judgement
may override, so it is settled before anything the model said is read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence

from pydantic import Field, model_validator

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.identity import (
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.prompts import (
    CRITIC_REVIEW_SYSTEM_PROMPT,
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    AgentTask,
    render_claim_digest,
    render_source_quality,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.researcher import render_evidence
from deep_research.agents.steps import ReActDecision, ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Critique,
    CritiqueGap,
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

CRITIC_NAME = "critic"

# The spec's acceptance threshold: a report scoring below this always buys
# another research pass while budget remains.
ACCEPTANCE_SCORE = 7
MIN_CRITIC_SCORE = 1
MAX_CRITIC_SCORE = 10

CRITIC_REPORT_CHARS = 6000
CRITIC_CLAIM_DIGEST = 40
CRITIC_EVIDENCE_CHARS = 2000
DEFAULT_MAX_NOTES = 10

# ``_render_balanced_report_sections`` renders this for the identity block of
# an empty report; a report that is present renders every reader section,
# using ``_SECTION_NOT_PRESENT`` for the ones it does not carry.
_NO_REPORT = "(no report)"
_SECTION_NOT_PRESENT = "(reader-report section not present)"

_READER_SECTION_ORDER = (
    ("summary", "Summary"),
    ("constraints", "Constraint ranking"),
    ("findings", "Findings"),
    ("uncertainty", "Uncertainty"),
    ("methodology", "Methodology"),
    ("references", "References"),
)


def _reader_section_key(heading: str) -> str | None:
    """Map a reader H2 heading to its stable prompt section key."""
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", heading.casefold()).split())
    if normalized.startswith(("summary", "executive summary")):
        return "summary"
    if "constraint" in normalized:
        return "constraints"
    if "finding" in normalized:
        return "findings"
    if any(word in normalized for word in ("uncertainty", "conflict", "gap")):
        return "uncertainty"
    if "method" in normalized:
        return "methodology"
    if any(word in normalized for word in ("reference", "citation")):
        return "references"
    return None


def _split_reader_report(report: str) -> dict[str, str]:
    """Split a rendered reader report into independent prompt sections.

    The Critic receives each section separately.  This keeps a character cap
    on one section from hiding all later sections in a combined prefix.
    Unknown headings remain in the identity bucket so no report text is
    silently discarded.
    """
    sections: dict[str, list[str]] = {key: [] for key, _ in _READER_SECTION_ORDER}
    identity: list[str] = []
    current: str | None = None
    for line in report.strip().splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = _reader_section_key(match.group(1))
            if current is None:
                identity.append(line)
            continue
        if current is None:
            identity.append(line)
        else:
            sections[current].append(line)
    return {
        "identity": "\n".join(identity).strip(),
        **{
            key: "\n".join(lines).strip() for key, lines in sections.items()
        },
    }


def _clamp_prompt_section(text: str, *, limit: int) -> str:
    """Clamp one independent report section without combining sections."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    value = text.strip()
    if not value:
        return _SECTION_NOT_PRESENT
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _render_balanced_report_sections(
    report: str,
    *,
    limit: int,
    report_sections: dict[str, str] | None = None,
) -> list[str]:
    """Render the report identity and every reader section under its own cap."""
    parsed = _split_reader_report(report)
    for raw_key, content in (report_sections or {}).items():
        key = raw_key if raw_key in parsed else _reader_section_key(raw_key)
        if key is not None:
            parsed[key] = content
    has_reader_section = any(parsed[key] for key, _ in _READER_SECTION_ORDER)
    identity = parsed["identity"]
    identity_fence_info = "reader-identity"
    if not report.strip():
        identity = _NO_REPORT
    elif not has_reader_section:
        # Compatibility for short/non-structured fixtures. A real rendered
        # report always has reader sections and follows the balanced path.
        identity = report.strip()
        identity_fence_info = _REPORT_FENCE_INFO
    sections = [("identity", "Report identity and declarations", identity)]
    sections.extend(
        (key, label, parsed[key]) for key, label in _READER_SECTION_ORDER
    )
    rendered: list[str] = []
    for key, label, content in sections:
        clamped = _clamp_prompt_section(content, limit=limit)
        fence = _report_fence(clamped)
        info = identity_fence_info if key == "identity" else f"reader-{key}"
        rendered.append(f"# {label}\n{fence}{info}\n{clamped}\n{fence}")
    return rendered

# The report is quoted inside a Markdown fence of its own: the opening fence is
# the begin marker and the closing fence is the end marker. A fence is the one
# Markdown construct that delimits a verbatim region, and its content is not
# parsed as Markdown, so no report heading can be read as a request section. The
# fence is made longer than any backtick run inside the report so the report
# cannot close it early.
_REPORT_FENCE_MIN = 3
_REPORT_FENCE_INFO = "report"


def _report_fence(report: str) -> str:
    """Return a backtick fence that no run inside ``report`` can close."""
    longest = max((len(run) for run in re.findall(r"`+", report)), default=0)
    return "`" * max(_REPORT_FENCE_MIN, longest + 1)


# This request's own sections are `#` (H1). The canonical report is H2 body
# sections (`REPORT_SECTIONS`) with H3 sub-groups, plus one H1 title,
# `# Research report: <question>`. Request sections were previously `##`, which
# put them at the same visual level as the report's own sections — `## Recorded
# problems` directly followed the report's `## Limitations` with nothing to
# distinguish them but the markers. At H1 every request heading outranks the
# report, so the report reads as content nested inside `# Report under review`.
# This also matches the spot-check path, which already introduces the report
# with the H1 title above.

# Two concrete, valid JSON instances that bracket the scale. The earlier
# skeleton used angle-bracket placeholders like `<integer 1-10>`, which is not
# valid JSON, so the model was shown something that was neither a schema nor an
# example. Two labelled examples demonstrate the range, and the band table below
# tells the model how to choose between them, because examples without
# calibration guidance invite it to split the difference. The provider already
# supplies the schema in a trailing system message, so this section supplies the
# examples the DeepSeek JSON Output guide asks for, and nothing that competes
# with it.
_LOW_EXAMPLE_SCORE = 3
_HIGH_EXAMPLE_SCORE = 9

_CRITIQUE_LOW_EXAMPLE_JSON = (
    '{"score": 3, "gaps": [{"coverage_id": null, "problem": "The report '
    'never states what share of cement emissions clinker substitution can '
    'remove, which is the figure the question turns on.", '
    '"recommended_queries": ["clinker substitution share of cement '
    'emissions"]}, {"coverage_id": null, "problem": "It gives no cost '
    'figures for the alternatives it recommends.", "recommended_queries": '
    '["low-carbon cement cost premium per tonne"]}], '
    '"unsupported_claims": ["The claim that commercial-scale '
    'deployment is accelerating, which no cited source measures."], '
    '"recommended_queries": ["clinker substitution share of cement emissions", '
    '"low-carbon cement cost premium per tonne"], "rationale": "The report '
    'names technologies and directions but supplies no measured figures, and '
    'its central claim about deployment rests on no cited source at all, so '
    'the question is answered only in generalities."}'
)

_CRITIQUE_HIGH_EXAMPLE_JSON = (
    '{"score": 9, "gaps": [{"coverage_id": null, "problem": "The report '
    'does not cover how durability data are expected to arrive.", '
    '"recommended_queries": ["low-carbon cement durability field trial '
    'results"]}], "unsupported_claims": [], "recommended_queries": '
    '["low-carbon cement durability field trial results"], "rationale": "The '
    'report answers the question completely, every load-bearing figure is '
    'attributed to a strong and diverse set of named sources, and it states '
    'its own durability uncertainty plainly instead of hiding it."}'
)

# How to choose a score. The thresholds themselves are not published to the
# model: routing is computed locally, and `CRITIQUE_INSTRUCTION` tells the model
# not to decide continuation. This table describes report quality, not policy.
_CRITIQUE_SCORE_BANDS = (
    "Choose the score from the report's weakest load-bearing element, not from "
    "its overall polish:\n"
    "1-3: the question is largely unanswered, or the central claims rest on no "
    "cited source.\n"
    "4-6: a partial answer whose key numbers, mechanisms, or trade-offs are "
    "missing or unsupported.\n"
    "7-8: the question is answered and every load-bearing claim is attributed, "
    "with at most narrow gaps.\n"
    "9-10: reserve for an answer that is complete, strongly and diversely "
    "sourced, and explicit about its own uncertainty."
)


_RATIONALE_CHARS = 600

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the recorded rationale.
ROUTING_REASONS = {
    "accepted_quality": "The report met the acceptance threshold.",
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "low_score": "The report scored below the acceptance threshold.",
    "critical_gaps": "Material gaps remain in the research.",
    "unsupported_claims": (
        "The report leans on statements no source supports."
    ),
    "missing_report": "No report was available to review.",
    "provider_unavailable": (
        "The model provider failed while the report was reviewed."
    ),
}

# The two conditions under which no model review exists at all.
CRITIQUE_FALLBACK_REASONS = ("missing_report", "provider_unavailable")


class CritiqueGapDraft(ContractModel):
    """One provider-reported gap before local plan-ID validation."""

    coverage_id: str | None = None
    problem: str
    recommended_queries: list[str]


class CritiqueDraft(ContractModel):
    """One model review, before domain validation.

    ``score`` is a plain ``int`` rather than ``CriticScore``: a model that
    answers 0 or 42 is making a formatting mistake, which ``clamp_score``
    fixes locally rather than discarding the whole review over.
    """

    score: int
    gaps: list[CritiqueGapDraft]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    rationale: str

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_gap_strings(cls, values: object) -> object:
        """Keep pre-Task-7 fixtures readable while the provider schema is typed."""
        if not isinstance(values, dict) or not isinstance(values.get("gaps"), list):
            return values
        converted = dict(values)
        converted["gaps"] = [
            {
                "coverage_id": None,
                "problem": gap,
                "recommended_queries": [],
            }
            if isinstance(gap, str)
            else gap
            for gap in values["gaps"]
        ]
        return converted


class CritiqueTask(AgentTask):
    """An ``AgentTask`` bound to the report and budget it reviews.

    Carrying the report and the iteration bounds on the task is what lets
    ``finalize(task, run)`` route without the agent holding mutable state
    across await points — the same reason ``ClaimTask`` exists.
    """

    report: str = ""
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=1, ge=1)
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    sub_topics: list[str] = []
    coverage_ids: list[str] = []
    error_count: int = Field(default=0, ge=0)
    quality: ReportQualitySnapshot | None = None
    report_sections: dict[str, str] = {}
    errors: list[ResearchError] = []
    error_groups: dict[str, list[ResearchError]] = {}


def _render_spot_check_guidance(
    report: str,
    sub_topics: Sequence[SubTopic],
    *,
    report_chars: int,
) -> str:
    """Render the report and the planner's search context for the spot check.

    The report is clamped with the same helper and budget the review prompt
    uses, so the spot-check view can never be larger than the review view and
    a long report is truncated identically in both.
    """
    lines: list[str] = []
    if report.strip():
        lines.append("Report under review:")
        lines.extend(_render_balanced_report_sections(report, limit=report_chars))
        lines.append("")
    lines.append(
        "When performing a spot check, use an applicable planned search "
        "query verbatim."
    )
    lines.append("Planned sub-topics and search queries:")
    for sub_topic in sub_topics:
        lines.append(f"- {sub_topic.title}")
        lines.extend(f"  - {query}" for query in sub_topic.search_queries)
    return "\n".join(lines)


def clamp_score(value: int) -> int:
    """Pin a model score into the ``CriticScore`` range."""
    return min(MAX_CRITIC_SCORE, max(MIN_CRITIC_SCORE, int(value)))


def normalize_notes(
    values: Sequence[str],
    *,
    limit: int = DEFAULT_MAX_NOTES,
) -> list[str]:
    """Collapse, de-duplicate, and cap one model-supplied note list."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    notes: list[str] = []
    for value in values:
        note = " ".join(value.split())
        if note and note not in notes:
            notes.append(note)
    return notes[:limit]


def normalize_gaps(
    values: Sequence[CritiqueGapDraft],
    *,
    known_coverage_ids: Collection[str] = (),
    limit: int = DEFAULT_MAX_NOTES,
) -> list[CritiqueGap]:
    """Normalize provider gaps and retain only plan-valid target IDs.

    A blank or unknown provider ID is intentionally converted to a global
    gap.  Titles and problem text are never consulted when deciding the
    target, so a similarly named topic cannot receive another topic's gap.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    gaps: list[CritiqueGap] = []
    seen: set[tuple[str | None, str, tuple[str, ...]]] = set()
    for draft in values:
        problem = " ".join(draft.problem.split())
        if not problem:
            continue
        coverage_id = draft.coverage_id or None
        if coverage_id is not None:
            coverage_id = coverage_id.strip() or None
        if coverage_id not in known_coverage_ids:
            coverage_id = None
        queries = tuple(normalize_notes(draft.recommended_queries))
        identity = (coverage_id, problem, queries)
        if identity in seen:
            continue
        seen.add(identity)
        gaps.append(
            CritiqueGap(
                coverage_id=coverage_id,
                problem=problem,
                recommended_queries=list(queries),
            )
        )
    return gaps[:limit]


def route_decision(
    *,
    score: int,
    gaps: Sequence[CritiqueGap],
    unsupported_claims: Sequence[str],
    iteration: int,
    max_iterations: int,
    has_report: bool,
) -> tuple[bool, str]:
    """Decide routing locally, in a fixed precedence.

    The iteration bound comes first and beats every quality signal. After
    that: a missing report is the most concrete thing to fix, then the
    score threshold, then gaps, then unsupported claims. Every gap the
    model listed counts as critical — ``CRITIQUE_INSTRUCTION`` tells it to
    list a gap only when closing it would materially change the answer.
    """
    if iteration >= max_iterations:
        return False, "max_iterations_reached"
    if not has_report:
        return True, "missing_report"
    if score < ACCEPTANCE_SCORE:
        return True, "low_score"
    if gaps:
        return True, "critical_gaps"
    if unsupported_claims:
        return True, "unsupported_claims"
    return False, "accepted_quality"


def _rationale(model_rationale: str, *, reason: str) -> str:
    """Extend the model's rationale with the routing reason this run used."""
    text = " ".join(model_rationale.split())
    explanation = ROUTING_REASONS[reason]
    body = f"{text} {explanation}" if text else explanation
    return body[:_RATIONALE_CHARS].rstrip()


def build_critique(
    draft: CritiqueDraft,
    *,
    iteration: int,
    max_iterations: int,
    known_coverage_ids: Collection[str] = (),
) -> tuple[Critique, str]:
    """Stamp one model review into a validated ``Critique`` and its route."""
    score = clamp_score(draft.score)
    gaps = normalize_gaps(
        draft.gaps, known_coverage_ids=known_coverage_ids
    )
    unsupported = normalize_notes(draft.unsupported_claims)
    queries = normalize_notes(draft.recommended_queries)
    should_continue, reason = route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=iteration,
        max_iterations=max_iterations,
        has_report=True,
    )
    return (
        Critique(
            score=score,
            gaps=gaps,
            unsupported_claims=unsupported,
            recommended_queries=queries,
            should_continue=should_continue,
            rationale=_rationale(draft.rationale, reason=reason),
        ),
        reason,
    )


def fallback_critique(
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Record a review that could not be made, with no invented score.

    A provider outage never buys another research cycle: an outage says
    nothing about the report, and a retry would almost certainly repeat it
    at cost. A missing report does buy one — there is something concrete to
    fix — unless the iteration bound already forbids it.
    """
    if reason not in CRITIQUE_FALLBACK_REASONS:
        raise ValueError(f"unknown fallback reason: {reason}")
    if reason == "provider_unavailable":
        should_continue = False
        route = (
            "max_iterations_reached"
            if iteration >= max_iterations
            else reason
        )
        gaps: list[CritiqueGap] = []
    else:
        should_continue, route = route_decision(
            score=MIN_CRITIC_SCORE,
            gaps=[],
            unsupported_claims=[],
            iteration=iteration,
            max_iterations=max_iterations,
            has_report=False,
        )
        gaps = [
            CritiqueGap(
                coverage_id=None,
                problem="No report was available to review.",
                recommended_queries=[],
            )
        ]
    sentences = [ROUTING_REASONS[reason]]
    if route != reason:
        sentences.append(ROUTING_REASONS[route])
    return (
        Critique(
            score=MIN_CRITIC_SCORE,
            gaps=gaps,
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=should_continue,
            rationale=" ".join(sentences),
        ),
        route,
    )


def _group_errors_by_agent_stage(
    errors: Sequence[ResearchError],
) -> dict[str, list[ResearchError]]:
    """Group typed errors by their safe agent and operation identifiers."""
    groups: dict[str, list[ResearchError]] = {}
    for error in errors:
        operation = error.details.get("operation")
        stage = (
            operation.strip()
            if isinstance(operation, str) and operation.strip()
            else error.error_type
        )
        key = f"{error.source} / {stage}"
        groups.setdefault(key, []).append(error)
    return groups


def _render_quality_snapshot(
    quality: ReportQualitySnapshot | None,
) -> str:
    """Render deterministic quality fields without reducing them to a score."""
    if quality is None:
        return "(no structured quality snapshot was recorded)"
    return json.dumps(
        quality.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def _render_error_groups(task: CritiqueTask) -> str:
    """Render all typed errors grouped by agent and operation/stage."""
    groups = task.error_groups or _group_errors_by_agent_stage(task.errors)
    if not groups:
        return (
            f"{task.error_count} error(s) were recorded during this pass; "
            "no typed error details were supplied."
        )
    lines = [
        f"{task.error_count or sum(len(rows) for rows in groups.values())} "
        "error(s) were recorded during this pass."
    ]
    for group in sorted(groups):
        rows = groups[group]
        lines.append(f"- {group}: {len(rows)} error(s)")
        for error in rows:
            severity = "recoverable" if error.recoverable else "fatal"
            coverage_id = error.details.get("coverage_id")
            coverage = (
                f" coverage_id={coverage_id}"
                if isinstance(coverage_id, str) and coverage_id.strip()
                else ""
            )
            lines.append(
                f"  - {error.error_type} ({severity}){coverage}: "
                f"{error.message}"
            )
    return "\n".join(lines)


def critique_messages(
    task: CritiqueTask,
    run: ReActRun,
    *,
    report_chars: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured review."""
    sub_topics = (
        "\n".join(f"- {title}" for title in task.sub_topics)
        or "(none planned)"
    )
    canonical_claims = merge_claim_snapshot([], task.claims)
    canonical_sources = merge_source_snapshot([], task.sources)
    sections = [
        f"# Research question\n{task.instruction}",
        (
            "# Report under review\n"
            "The reader report is split into independently bounded fenced "
            "sections below. Review every section; a section cap must not hide "
            "later sections; its own headings belong to the report rather than "
            "to this request."
        ),
        *_render_balanced_report_sections(
            task.report,
            limit=report_chars,
            report_sections=task.report_sections,
        ),
        (
            "# Sub-topics planned\n"
            f"{sub_topics}"
        ),
        (
            "# Deterministic quality snapshot\n"
            f"{_render_quality_snapshot(task.quality)}"
        ),
        (
            "# Claim verdicts — canonical checked claims\n"
            f"{render_claim_digest(canonical_claims[:claim_digest])}"
        ),
        (
            "# Source quality — cited-source assessments\n"
            f"{render_source_quality(canonical_sources)}"
        ),
        (
            "# Recorded problems by agent/stage\n"
            f"{_render_error_groups(task)}"
        ),
        (
            "# Spot checks\n"
            f"{render_evidence(run, limit=CRITIC_EVIDENCE_CHARS)}"
        ),
        f"# Response contract\n{CRITIQUE_INSTRUCTION}",
        f"# How to choose the score\n{_CRITIQUE_SCORE_BANDS}",
        (
            "# Reply format\n"
            "Return exactly one JSON object with these five fields and no "
            "others, with no text before or after it. Two complete examples, "
            "one for a weak report and one for a strong one, showing the scale "
            "in use:\n"
            f"Weak report, score {_LOW_EXAMPLE_SCORE}:\n"
            f"{_CRITIQUE_LOW_EXAMPLE_JSON}\n"
            f"Strong report, score {_HIGH_EXAMPLE_SCORE}:\n"
            f"{_CRITIQUE_HIGH_EXAMPLE_JSON}"
        ),
    ]
    return [
        ChatMessage(role="developer", content=CRITIC_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def critique_provider_error(error: Exception) -> ResearchError:
    """Record that the review call could not reach the provider.

    Non-recoverable: no review of this report exists. The run still ends
    with a routing decision, because a graph with no critique cannot route
    at all.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_provider_error",
        message=(
            "The model provider failed while the report was reviewed; the "
            "research pass was ended rather than repeated."
        ),
        recoverable=False,
        details=agent_provider_failure_details("critic_report_review", error),
    )


def missing_report_error() -> ResearchError:
    """Warn that there was no report to review."""
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_missing_report",
        message="No report was available to review.",
    )


def critique_started_event(
    *,
    iteration: int,
    max_iterations: int,
    claim_count: int,
    has_report: bool,
) -> ResearchEvent:
    """Announce that the review began, before any provider call."""
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.started",
        message="Report review started.",
        metadata={
            "iteration": iteration,
            "max_iterations": max_iterations,
            "claim_count": claim_count,
            "has_report": has_report,
        },
    )


def critique_completed_event(
    critique: Critique,
    run: ReActRun,
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> ResearchEvent:
    """Report the score, the counts, and the routing recommendation.

    ``reason`` is a ``ROUTING_REASONS`` key, never provider text, so a
    consumer can group runs by *why* they continued or stopped rather than
    parsing a rationale.
    """
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.completed",
        message="Report review complete.",
        metadata={
            "score": critique.score,
            "gap_count": len(critique.gaps),
            "unsupported_claim_count": len(critique.unsupported_claims),
            "recommended_query_count": len(critique.recommended_queries),
            "should_continue": critique.should_continue,
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "tool_calls": run.tool_calls,
            "stop_reason": run.stop_reason,
        },
    )


class CriticAgent(BaseAgent[Critique]):
    """Review the report, score it, and recommend a route.

    ``run`` is overridden to emit progress events and to skip the
    spot-check loop when there is no report or no tool budget; everything
    below it — bounds, tracing, tool execution, scratchpad writes — is
    still the shared runtime's.
    """

    name = CRITIC_NAME
    description = "Judge the report and recommend whether research continues."
    allowed_tools = ("web_search", "query_memory")

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        report_chars: int = CRITIC_REPORT_CHARS,
        claim_digest: int = CRITIC_CLAIM_DIGEST,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if report_chars < 1:
            raise ValueError("report_chars must be at least 1")
        if claim_digest < 1:
            raise ValueError("claim_digest must be at least 1")
        self._report_chars = report_chars
        self._claim_digest = claim_digest

    @property
    def output_schema(self) -> type[Critique]:
        """The validated critique. Never sent to the provider.

        ``review`` asks for ``CritiqueDraft`` instead, because ``Critique``
        carries ``CriticScore`` bounds and a non-blank rationale that do not
        survive strict JSON schema conversion. Do not route this agent
        through ``complete_output``.
        """
        return Critique

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return CRITIC_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> CritiqueTask:
        """Bind this review to the report and the remaining budget."""
        claims = merge_claim_snapshot([], state.verified_claims)
        sources = merge_source_snapshot([], state.evaluated_sources)
        errors = list(state.errors)
        return CritiqueTask(
            instruction=state.original_question,
            guidance=_render_spot_check_guidance(
                state.report or "",
                state.sub_topics,
                report_chars=self._report_chars,
            ),
            report=state.report or "",
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            claims=claims,
            sources=sources,
            sub_topics=[sub_topic.title for sub_topic in state.sub_topics],
            coverage_ids=[sub_topic.coverage_id for sub_topic in state.sub_topics],
            error_count=len(errors),
            quality=getattr(state, "quality", None),
            report_sections=_split_reader_report(state.report or ""),
            errors=errors,
            error_groups=_group_errors_by_agent_stage(errors),
        )

    async def review(
        self,
        task: CritiqueTask,
        run: ReActRun,
    ) -> tuple[Critique, str, list[ResearchError], bool]:
        """Judge one report from one finished spot-check loop.

        Returns ``(critique, reason, errors, provider_failed)``. No provider
        call is made when there is no report, so a score is never invented
        over an empty review.
        """
        if not task.report.strip():
            critique, reason = fallback_critique(
                reason="missing_report",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [missing_report_error()], False

        try:
            draft = await self.provider.complete_structured(
                critique_messages(
                    task,
                    run,
                    report_chars=self._report_chars,
                    claim_digest=self._claim_digest,
                ),
                CritiqueDraft,
                agent_name=self.name,
                max_tokens=self.config.critic_review_max_tokens,
            )
        except ProviderError as error:
            critique, reason = fallback_critique(
                reason="provider_unavailable",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [critique_provider_error(error)], True

        critique, reason = build_critique(
            draft,
            iteration=task.iteration,
            max_iterations=task.max_iterations,
            known_coverage_ids=set(task.coverage_ids),
        )
        return critique, reason, [], False

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> Critique | None:
        """Adapt ``review`` to the ``BaseAgent`` hook.

        ``run`` calls ``review`` directly so it can keep the routing reason
        and the errors this hook signature has nowhere to return.
        """
        if not isinstance(task, CritiqueTask):
            raise AgentConfigurationError(
                "CriticAgent.finalize requires a CritiqueTask"
            )
        critique, _, _, _ = await self.review(task, run)
        return critique

    def state_update(
        self,
        result: Critique | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The critique and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["critique"] = result
        return update

    async def _spot_check(self, task: CritiqueTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes from a previous iteration's
        review are noise in this one's prompt.
        """
        self.scratchpad.clear()
        toolset = self.toolset

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> ReActDecision:
            del steps
            return await self._complete_react_decision(task, iteration=iteration)

        react = await run_react_loop(
            agent_name=self.name,
            tracker=self.tracker,
            tools=toolset,
            decide=decide,
            max_iterations=self.config.max_iterations,
            tool_budget=self.config.tool_budget,
            on_step=self._record_step,
            is_sufficient=self.is_sufficient,
            summary_limit=self.config.observation_summary_chars,
            propagate_provider_errors=False,
        )
        return react.model_copy(
            update={"errors": [*react.errors, *self.scratchpad.drain_errors()]}
        )

    async def run(self, state: ResearchState) -> AgentRun[Critique]:
        """Spot-check what is worth checking, then score and route."""
        task = self.build_task(state)
        has_report = bool(task.report.strip())
        events: list[ResearchEvent] = [
            critique_started_event(
                iteration=task.iteration,
                max_iterations=task.max_iterations,
                claim_count=len(task.claims),
                has_report=has_report,
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            if has_report and self.config.tool_budget > 0:
                react = await self._spot_check(task)
            else:
                # Nothing to check against (no report) or nothing to check
                # with (no tool budget): spending a provider call here would
                # buy no information the review could use.
                react = ReActRun(agent_name=self.name, stop_reason="finished")
            errors.extend(react.errors)
            critique, reason, review_errors, provider_failed = await self.review(
                task, react
            )
            errors.extend(review_errors)
            if provider_failed:
                # Mirror the loop-level provider_error path so a caller
                # reading react.succeeded never sees "finished" over an
                # abort that happened during the review.
                react = react.model_copy(update={"stop_reason": "provider_error"})
            react = react.model_copy(update={"errors": errors})
            events.append(
                critique_completed_event(
                    critique,
                    react,
                    reason=reason,
                    iteration=task.iteration,
                    max_iterations=task.max_iterations,
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "score": critique.score,
                    "gap_count": len(critique.gaps),
                    "should_continue": critique.should_continue,
                    "reason": reason,
                    "tool_calls": react.tool_calls,
                    "stop_reason": react.stop_reason,
                }
            )

        return AgentRun(
            agent_name=self.name,
            result=critique,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(critique, react),
                "events": events,
            },
        )
