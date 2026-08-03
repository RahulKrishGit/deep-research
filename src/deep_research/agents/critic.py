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

from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.prompts import (
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    AgentTask,
    render_claim_digest,
    render_source_quality,
)
from deep_research.agents.researcher import render_evidence
from deep_research.agents.steps import ReActRun
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Critique,
    ScoredSource,
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


class CritiqueDraft(ContractModel):
    """One model review, before domain validation.

    ``score`` is a plain ``int`` rather than ``CriticScore``: a model that
    answers 0 or 42 is making a formatting mistake, which ``clamp_score``
    fixes locally rather than discarding the whole review over.
    """

    score: int
    gaps: list[str]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    rationale: str


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
    error_count: int = Field(default=0, ge=0)


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


def route_decision(
    *,
    score: int,
    gaps: Sequence[str],
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
) -> tuple[Critique, str]:
    """Stamp one model review into a validated ``Critique`` and its route."""
    score = clamp_score(draft.score)
    gaps = normalize_notes(draft.gaps)
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
        gaps: list[str] = []
    else:
        should_continue, route = route_decision(
            score=MIN_CRITIC_SCORE,
            gaps=[],
            unsupported_claims=[],
            iteration=iteration,
            max_iterations=max_iterations,
            has_report=False,
        )
        gaps = ["No report was available to review."]
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


def _clamp_report(text: str, *, limit: int) -> str:
    """Clamp the report for a prompt without flattening its headings.

    ``summarize_text`` is deliberately not used here: it joins on
    whitespace, which would run every Markdown heading into one line.
    """
    report = text.strip()
    if not report:
        return "(no report)"
    if len(report) <= limit:
        return report
    return report[: limit - 3].rstrip() + "..."


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
    sections = [
        f"## Research question\n{task.instruction}",
        (
            "## Report under review\n"
            f"{_clamp_report(task.report, limit=report_chars)}"
        ),
        f"## Sub-topics planned\n{sub_topics}",
        (
            "## Claim verdicts\n"
            f"{render_claim_digest(list(task.claims)[:claim_digest])}"
        ),
        f"## Source quality\n{render_source_quality(task.sources)}",
        (
            "## Recorded problems\n"
            f"{task.error_count} error(s) were recorded during this pass."
        ),
        (
            "## Spot checks\n"
            f"{render_evidence(run, limit=CRITIC_EVIDENCE_CHARS)}"
        ),
        f"## Response contract\n{CRITIQUE_INSTRUCTION}",
    ]
    return [
        ChatMessage(role="developer", content=CRITIC_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]
