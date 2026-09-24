"""The Researcher: gather source-backed findings for planned sub-topics.

Like the Planner, this module never sends a domain type to the provider.
``FindingDraft`` mirrors ``Finding`` with plain field types so it survives
strict JSON schema conversion; ``build_findings`` stamps the drafts with the
sub-topic and extraction time the model must not be trusted to supply.

Priority convention: lower is more important. Priority 1 is the most
important sub-topic, and ``HIGH_PRIORITY_THRESHOLD`` is the largest value
still considered high priority.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence
from datetime import datetime, timezone
from typing import NamedTuple

from pydantic import Field, JsonValue, ValidationError

from deep_research.agents.acquisition import (
    AcquisitionPolicy,
    ManifestSequence,
    build_acquisition_context,
)
from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import (
    ATTRIBUTION_CUE_PATTERN,
    _issuer_name_pattern,
    _quote_states,
    attribution_cue_adjacent,
    excerpt_matches,
    neighbouring_passage_text,
    retained_work_count,
)
from deep_research.agents.identity import deduplicate_findings
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_structured_reply_format,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import (
    ReActDecision,
    ReActRun,
    ReActStep,
    StopReason,
    read_evidence_urls,
    summarize_text,
)
from deep_research.agents.validation import _invalid_fields
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderError
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    MAX_SNIPPET_CHARS,
    QUALITY_CONTRACT_VERSION,
    AcquisitionState,
    ContractModel,
    CritiqueGap,
    EvidenceTarget,
    EvidenceUnit,
    FigureKind,
    Finding,
    FindingFigure,
    ReadRecord,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    SubTopic,
    _ENERGY_UNIT,
    _POWER_UNIT,
    _canonical_acquisition_url,
    counted_evidence_targets,
    sub_topic_owes_evidence,
)

RESEARCHER_NAME = "researcher"
HIGH_PRIORITY_THRESHOLD = 2
# The Planner's own ceiling is seven sub-topics, so one research pass attempts
# the whole plan by default rather than silently truncating it.
DEFAULT_MAX_SUB_TOPICS = 7
# Evidence kept per sub-topic, not coverage planned: a sub-topic may report at
# most six distinct findings drawn from at most four distinct sources. These
# are properties of the extraction contract, not deployment knobs.
MAX_FINDINGS_PER_SUB_TOPIC = 6
MAX_UNIQUE_SOURCES_PER_SUB_TOPIC = 4
DEFAULT_EVIDENCE_CHARS = 4000

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

RESEARCHER_SYSTEM_PROMPT = (
    "You are the researcher of a multi-agent research system. You gather "
    "evidence for exactly one sub-topic at a time.\n"
    "Use web_search to find candidate sources, web_scraper to read a "
    "promising page, document_reader for PDFs and data files, and "
    "query_memory to avoid repeating research a previous session already "
    "did.\n"
    "Prefer primary sources: laws and regulator orders, standards bodies, "
    "official datasets, original research, and issuer filings. Use secondary "
    "analysis to find or interpret primary material, not as the default "
    "support for load-bearing numbers. Read a source before reporting a "
    "finding from it. Record publication date and geographic applicability "
    "when the source provides them.\n"
    "A search result is a lead, never evidence: every claim you report must "
    "come from a page or document you actually read in this loop. If a tool "
    "fails, try another query or another source rather than giving up.\n"
    "Spend your calls on reading, not on repeating searches. After the first "
    "search for a sub-topic, read the most promising result it returned before "
    "searching again — web_scraper for a page, document_reader for a PDF or "
    "data file — and keep alternating until the sub-topic's success criteria "
    "are met. If a search returned nothing worth reading, search again instead. "
    "One page you have read is worth more than several more queries.\n"
    "Read the organisation's own page first. When a figure belongs to an "
    "organisation - an agency's inventory, a market monitor's release, a "
    "company's filing - read that organisation's own page or document (for "
    "example eia.gov or woodmac.com) before any story that repeats it. Use a "
    "relay only when the original is not reachable, and record it as a relay: "
    "cite the page where you read it and name the organisation it credits in "
    "attributed_issuer. Do not search for a second source to confirm a figure "
    "its own organisation publishes.\n"
    "If a publisher refuses automated access to a page, do not try that page "
    "or that host again. Read the same material as a document instead — "
    "document_reader handles PDFs, spreadsheets and data files, and primary "
    "reports are usually published that way — or find the same fact from a "
    "different publisher.\n"
    "Finish once the sub-topic's success criteria are met, or once no "
    "further source is worth retrieving."
)

EXTRACTION_SYSTEM_PROMPT = (
    "You extract findings from a completed research transcript. Report only "
    "what the retrieved sources state, and attribute every finding to the "
    "exact source URL and title it came from. Return an empty list rather "
    "than inventing a source. Confidence is a number between 0 and 1."
)


class FindingFigureDraft(ContractModel):
    """One figure the snippet states, before domain validation (no Field constraints)."""

    value: str
    unit: str
    period: str | None = None
    kind: str | None = None


class FindingDraft(ContractModel):
    """One model-extracted finding, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``extracted_at`` and ``related_sub_topic`` are deliberately
    absent — this project stamps those, not the model.
    """

    content: str
    source_url: str
    source_title: str
    confidence: float
    # The read_id, locator and snippet identifying the passage a finding came from. ``build_findings``
    # REQUIRES these whenever the acquisition path is active (``known_reads is
    # not None``): an optional membership check is one the model can skip, and
    # the reply example demonstrates the required shape rather than the
    # URL/title shape that no longer admits anything. They stay optional on the
    # model so a snapshot written for the legacy URL/title path still loads.
    read_id: str | None = None
    locator: str | None = None
    snippet: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    figures: list[FindingFigureDraft] = Field(default_factory=list)
    # The figures' dates, kept apart for the same reason ``SourceTemporal``
    # keeps a source's: the period a figure applies to, the date the source
    # states it, and the vintage of the data behind it are three different
    # facts, and a comparison of "the latest" needs all three. Optional, so a
    # finding about something undated stays admissible.
    data_period: str | None = None
    statement_date: str | None = None
    vintage: str | None = None
    # What the page hands to somebody else, and what the figure it reports is
    # measured over. A relay's copy of an issuer's figure is that issuer's
    # measurement, not the host's, and a market total is not the segment a
    # target asks about: both are the model's own readings of the packet, so
    # both travel as their own fields instead of being folded into the
    # finding's prose where no later stage can check them.
    attributed_issuer: str | None = None
    attribution_quote: str | None = None
    measure_scope: str | None = None
    release_date: str | None = None


class SubTopicFindingsDraft(ContractModel):
    """The provider-facing extraction schema for one sub-topic."""

    findings: list[FindingDraft]


class ResearchFindings(ContractModel):
    """The validated findings ``ResearcherAgent`` produces.

    Never sent to the provider — ``SubTopicFindingsDraft`` is. Do not route
    this agent through ``complete_output``.
    """

    findings: list[Finding] = Field(default_factory=list)


class SubTopicTask(AgentTask):
    """An ``AgentTask`` bound to the sub-topic its loop researches.

    Carrying the sub-topic on the task is what lets ``finalize(task, run)``
    know which sub-topic it is finalizing without the agent holding mutable
    state across await points.
    """

    sub_topic: SubTopic
    existing_sources: list[str] = Field(default_factory=list)


def _critic_gaps_by_target(
    state: ResearchState,
) -> dict[str, list[CritiqueGap]]:
    """Group the Critic's gaps by the plan ID each one was routed to.

    Only ``CritiqueGap.coverage_id`` decides the target. Titles and problem
    text are never parsed: a sub-topic whose title happens to appear inside
    another topic's prose must not receive that topic's gap, and a gap the
    Critic could not tie to the plan is global by construction.
    """
    critique = state.critique
    if critique is None:
        return {}
    grouped: dict[str, list[CritiqueGap]] = {}
    for gap in critique.gaps:
        if gap.coverage_id is None:
            continue
        grouped.setdefault(gap.coverage_id, []).append(gap)
    return grouped


def _is_critic_gap_target(
    sub_topic: SubTopic,
    gaps_by_target: Mapping[str, list[CritiqueGap]],
) -> bool:
    """True when the Critic routed at least one gap to this exact plan ID."""
    return sub_topic.coverage_id in gaps_by_target


def _cited_read_incidence(state: ResearchState) -> dict[str, tuple[str, str]]:
    """``{canonical URL: (read_id, content_sha256)}`` this run has already cited.

    Resolved from the run's own claim record — the evidence each checked claim
    selected, through the evidence registry to the read behind it — and never
    from the cache entry being validated, which would make the fingerprint
    check vacuous. A URL no claim cites contributes nothing, so an ordinary
    re-read stays free.
    """
    cited: dict[str, tuple[str, str]] = {}
    for claim in state.verified_claims:
        for evidence_id in claim.evidence_selection:
            unit = state.evidence_units.get(evidence_id)
            if unit is None:
                continue
            read = state.read_records.get(unit.read_id)
            if read is None:
                continue
            identity = (read.read_id, read.content_sha256)
            for url in (read.resolved_url, read.requested_url):
                candidate = _canonical_acquisition_url(url)
                if candidate:
                    cited.setdefault(candidate, identity)
    return cited


def _refinement_satisfied_sub_topics(
    state: ResearchState,
) -> list[tuple[SubTopic, str]]:
    """Topics a refinement pass may skip, each with the reason it is skipped.

    A topic is skipped only when it owes nothing: every required target it
    carries is answered, and the Critic asked for no new searches for it. The
    reason is reported per topic, so "we skipped it because its obligations
    are met" is distinguishable in the record from "we skipped it because a
    finding existed" (the legacy target-less case).
    """
    if state.critique is None:
        return []
    satisfied: list[tuple[SubTopic, str]] = []
    for sub_topic in state.sub_topics:
        if sub_topic_owes_evidence(state, sub_topic):
            continue
        reason = (
            "required_targets_completed"
            if counted_evidence_targets(sub_topic.evidence_targets)
            else "interim_satisfaction"
        )
        satisfied.append((sub_topic, reason))
    return satisfied


def _ordered_sub_topics(state: ResearchState) -> list[SubTopic]:
    """Order eligible sub-topics by Critic-targeted gaps, then priority.

    A sub-topic counts as gap-targeted when the Critic returned a gap whose
    ``coverage_id`` equals it exactly. Ties resolve by ``priority`` ascending
    (1 is most important), then by the order the planner produced. On a
    refinement pass, a topic that owes no evidence is omitted — which means
    its required targets are all answered and the Critic asked for no new
    searches, never merely that some finding mentions it. Initial passes keep
    every planned topic. Callers that need to know which sub-topics a
    ``max_sub_topics`` cap left out (``ResearcherAgent.run``) use this directly
    instead of ``select_sub_topics``, which only returns the truncated head.
    """
    gaps_by_target = _critic_gaps_by_target(state)

    def sort_key(item: tuple[int, SubTopic]) -> tuple[int, int, int]:
        index, sub_topic = item
        flagged = 0 if _is_critic_gap_target(sub_topic, gaps_by_target) else 1
        return (flagged, sub_topic.priority, index)

    ordered = sorted(enumerate(state.sub_topics), key=sort_key)
    if state.critique is None:
        return [sub_topic for _, sub_topic in ordered]
    return [
        sub_topic
        for _, sub_topic in ordered
        if sub_topic_owes_evidence(state, sub_topic)
    ]


def select_sub_topics(
    state: ResearchState,
    *,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
) -> list[SubTopic]:
    """Return the top eligible sub-topics, ordered by ``_ordered_sub_topics``.

    Initial passes include every planned sub-topic. Refinement passes omit the
    topics that owe no evidence. Sub-topics past the cap are truncated here
    with no record of their own — ``ResearcherAgent.run`` is responsible for
    recording what this cap drops and which satisfied topics it omitted.
    """
    if max_sub_topics < 1:
        raise ValueError("max_sub_topics must be at least 1")
    return _ordered_sub_topics(state)[:max_sub_topics]


def is_high_priority(
    sub_topic: SubTopic,
    *,
    threshold: int = HIGH_PRIORITY_THRESHOLD,
) -> bool:
    """True when an empty result for this sub-topic is worth warning about."""
    return sub_topic.priority <= threshold


def existing_sources_for(
    state: ResearchState,
    sub_topic: SubTopic,
) -> list[str]:
    """Return the source URLs already recorded for one sub-topic, in order."""
    seen: list[str] = []
    for finding in state.raw_findings:
        if finding.related_sub_topic != sub_topic.title:
            continue
        if finding.source_url not in seen:
            seen.append(finding.source_url)
    return seen


# Precedence for the merged ``stop_reason``, most urgent first. A caller
# only ever sees one stop reason for the whole merged run, so a reason that
# demands attention (a hard failure, or a budget that ran out) must never be
# masked by a later sub-topic that happened to finish cleanly. "finished" is
# the least informative outcome and sorts last for exactly that reason;
# "sufficient" is a deliberate early stop and outranks it but still yields
# to any budget or error condition.
_STOP_REASON_PRECEDENCE: tuple[StopReason, ...] = (
    "provider_error",
    "tool_budget_exhausted",
    "max_iterations",
    "sufficient",
    "finished",
)


def merge_react_runs(
    agent_name: str,
    runs: Sequence[ReActRun],
) -> ReActRun:
    """Fold per-sub-topic loops into one run record for ``AgentRun.react``.

    ``stop_reason`` is picked by ``_STOP_REASON_PRECEDENCE``: the highest-
    priority reason present across every run wins, so a real stop condition
    (an error, or an exhausted budget) is never masked by a later sub-topic
    that simply finished. Per-sub-topic stop reasons stay in the emitted
    events. ``final_answer`` joins every non-``None`` final answer across the
    merged runs, in order, separated by blank lines — sub-topic loops each
    contribute their own answer, and none should be silently dropped.
    """
    if not runs:
        return ReActRun(agent_name=agent_name, stop_reason="finished")

    steps: list[ReActStep] = []
    errors: list[ResearchError] = []
    final_answers: list[str] = []
    for run in runs:
        steps.extend(run.steps)
        errors.extend(run.errors)
        if run.final_answer is not None:
            final_answers.append(run.final_answer)
    final_answer = "\n\n".join(final_answers) if final_answers else None

    reasons = {run.stop_reason for run in runs}
    stop_reason: StopReason = next(
        reason for reason in _STOP_REASON_PRECEDENCE if reason in reasons
    )
    return ReActRun(
        agent_name=agent_name,
        steps=steps,
        stop_reason=stop_reason,
        iterations=sum(run.iterations for run in runs),
        tool_calls=sum(run.tool_calls for run in runs),
        cache_hits=sum(run.cache_hits for run in runs),
        # The totals above are whole-case sums; these are the largest single
        # loop's own totals. ``tool_budget`` is enforced per loop, so a budget
        # gate must compare against the per-loop maximum. A merged sum can
        # legitimately exceed the per-loop ceiling -- two in-budget loops of 6
        # and 5 sum to 11 -- and comparing the sum would fail a case whose
        # every loop respected its bound.
        max_loop_iterations=max(run.max_loop_iterations for run in runs),
        max_loop_tool_calls=max(run.max_loop_tool_calls for run in runs),
        final_answer=final_answer,
        errors=errors,
    )


def render_session_guidance(state: ResearchState) -> str:
    """Render Critic feedback and recalled memory as shared run context."""
    sections: list[str] = []
    critique = state.critique
    if critique is not None:
        lines = ["The critic asked for another research pass."]
        if critique.gaps:
            lines.append("Gaps to close:")
            lines.extend(
                f"- {summarize_text(gap.problem)}" for gap in critique.gaps
            )
        if critique.recommended_queries:
            lines.append("Run these recommended queries first:")
            lines.extend(
                f"- {summarize_text(query)}"
                for query in critique.recommended_queries
            )
        if critique.unsupported_claims:
            lines.append("Find sources for these unsupported claims:")
            lines.extend(
                f"- {summarize_text(claim)}"
                for claim in critique.unsupported_claims
            )
        sections.append("\n".join(lines))

    memory_section = render_memory_guidance(state.memory_context)
    if memory_section:
        sections.append(memory_section)
    return "\n\n".join(sections)


def _critic_queries_for(state: ResearchState, sub_topic: SubTopic) -> list[str]:
    """The queries routed to this exact plan ID, in order.

    Read from the typed ``acquire`` jobs first — the refinement hop's own
    union of the Critic's gaps and the terminal review's defects, so a defect
    the review named reaches the loop the same way a gap does — and then from
    the Critic's gaps, which is the durable record of its half of that union.
    Only jobs whose ``coverage_id`` equals the sub-topic's own are read, so a
    refinement pass never spends another topic's queries on this one.
    """
    queries: list[str] = []
    for job in state.refinement_targets:
        if job.action != "acquire" or job.coverage_id != sub_topic.coverage_id:
            continue
        for query in job.queries:
            if query not in queries:
                queries.append(query)
    for gap in _critic_gaps_by_target(state).get(sub_topic.coverage_id, []):
        for query in gap.recommended_queries:
            if query not in queries:
                queries.append(query)
    return queries


def render_sub_topic_guidance(
    sub_topic: SubTopic,
    existing_sources: Sequence[str],
    *,
    prioritized_queries: Sequence[str] = (),
) -> str:
    """Render one sub-topic's brief, including sources already collected.

    ``prioritized_queries`` are the ones the Critic routed to this exact plan
    ID. They are printed ahead of the planner's own suggestions, because
    closing a named gap is what this pass exists to do; the success criteria
    below still state when the sub-topic is done, and a query that appears in
    both lists is printed once.
    """
    first: list[str] = []
    for query in prioritized_queries:
        normalized = " ".join(query.split())
        if normalized and normalized not in first:
            first.append(normalized)
    planned = [query for query in sub_topic.search_queries if query not in first]
    lines = [
        f"Sub-topic: {sub_topic.title}",
        f"Why it matters: {sub_topic.rationale}",
        f"Priority: {sub_topic.priority} (1 is most important)",
    ]
    if first:
        lines.append("The critic routed a gap to this sub-topic.")
        lines.append("Run these queries first:")
        lines.extend(f"- {query}" for query in first)
        lines.append("Then run the planned queries for the success criteria:")
    else:
        lines.append("Suggested search queries:")
    lines.extend(f"- {query}" for query in planned)
    lines.append("This sub-topic is done when:")
    lines.extend(f"- {criterion}" for criterion in sub_topic.success_criteria)
    if existing_sources:
        lines.append(
            "Sources already collected for this sub-topic — do not repeat "
            "them:"
        )
        lines.extend(f"- {source}" for source in existing_sources)
    return "\n".join(lines)


def _successful_result(step: ReActStep) -> ToolResult | None:
    """The step's tool result, or ``None`` unless the call succeeded."""
    result = step.tool_result
    if result is None or not result.success:
        return None
    return result


def _payload_line(result: ToolResult, *, limit: int) -> str:
    """One clamped transcript line for a successful tool payload."""
    payload = json.dumps(result.data, default=str, ensure_ascii=False)
    return f"- [{result.tool_name}] {summarize_text(payload, limit=limit)}"


def _search_candidate_lines(data: JsonValue) -> list[str]:
    """Candidate ``title: url`` lines from one ``web_search`` payload.

    A hit the loop never opened stays visible to the extraction call as what
    it is — a lead with a URL the model may cite only if it also read the page.
    """
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    lines: list[str] = []
    for entry in results:
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
            continue
        title = entry.get("title")
        label = (
            summarize_text(title)
            if isinstance(title, str) and title.strip()
            else entry["url"]
        )
        lines.append(f"- {label}: {entry['url']}")
    return lines


def render_evidence(
    run: ReActRun,
    *,
    limit: int,
    discovery_payloads: bool = True,
) -> str:
    """Render every successful tool payload, each clamped to ``limit`` chars.

    Written as an explicit loop rather than a comprehension: the payload
    dump has to be summarized before interpolation, and Python 3.11
    f-strings cannot hold a multi-line call expression.

    ``discovery_payloads=False`` renders only what the loop READ: a payload
    from a discovery-only tool such as ``web_search`` is replaced by its
    candidate title/URL metadata, because a search hit the loop never opened
    is a lead, not evidence. The ReAct transcript keeps the whole payload
    either way, so discovery and debugging lose nothing.
    """
    lines: list[str] = []
    candidates: list[str] = []
    for step in run.steps:
        result = _successful_result(step)
        if result is None:
            continue
        if discovery_payloads:
            lines.append(_payload_line(result, limit=limit))
            continue
        if read_evidence_urls(step):
            lines.append(_payload_line(result, limit=limit))
        elif result.tool_name == "web_search":
            candidates.extend(_search_candidate_lines(result.data))
    if candidates:
        lines.append("Candidate sources found by search, not read:")
        lines.extend(candidates)
    return "\n".join(lines) or "(no evidence retrieved)"


def retrieved_finding_urls(run: ReActRun) -> tuple[str, ...]:
    """Every source URL this run actually READ, normalized and unique.

    The provenance allow-list for extraction. A search result is a DISCOVERY
    record and never appears here, so a finding may only be reported from a
    page or document the loop opened; a failed call, an empty payload, a
    malformed entry, and a write such as ``save_to_memory`` all contribute
    nothing either. The classification itself lives in
    ``steps.read_evidence_urls``, which this is the run-level fold of.
    """
    found: list[str] = []
    for step in run.steps:
        for url in read_evidence_urls(step):
            if url not in found:
                found.append(url)
    return tuple(found)


# One evidence-backed example. The response contract above still states the
# empty-list case, which is valid and is not the opposite end of a scale. On
# the acquisition path every finding must carry the registry fields it copied
# from the packet, so the example demonstrates that shape instead of the
# URL/title shape that no longer admits anything: the prompt must never show
# a bypass of the membership checks. Its target id is shaped like the plan's
# own (``topic-01-target-01``) rather than a bare ``target-01``, because the
# one thing the model must copy from the Planned targets list is the id, and
# an example whose id could never come from that list teaches the wrong shape.
_FINDING_REPLY_EXAMPLES = (
    (
        "Example input: passage read-111111111111111111111111 locator page-4-"
        "chunk-0 of the example report at "
        "https://evidence.example.test/report (fetched for topic-01); the "
        "passage reads \"The measured reduction was 12 percent, according to "
        "the Example Statistical Agency, across all classes.\"; the Planned "
        "targets list names topic-01-target-01 (how much capacity was "
        "added?).",
        '{"findings":[{"content":"The example report relays the Example '
        "Statistical Agency's measurement: a 12 percent reduction in 2024 "
        'across all classes, from the agency\'s January 2025 preliminary '
        'inventory.","source_url":"https://evidence.example.test/report",'
        '"source_title":"Example report","confidence":0.8,'
        '"read_id":"read-111111111111111111111111","locator":"page-4-chunk-0",'
        '"snippet":"The measured reduction was 12 percent, according to the '
        'Example Statistical Agency, across all classes.",'
        '"figures":[{"value":"12","unit":"percent","period":"2024","kind":"actual"}],'
        '"target_ids":["topic-01-target-01"],"data_period":"2024",'
        '"statement_date":"2025-03-12",'
        '"vintage":"January 2025 preliminary inventory",'
        '"attributed_issuer":"Example Statistical Agency",'
        '"attribution_quote":"according to the Example Statistical Agency",'
        '"measure_scope":"all classes","release_date":"2025-03-12"}]}',
    ),
)

# What every finding must say about the figures it reports, on either path.
# Appended to both response contracts because the failure it prevents is not
# acquisition-specific: a statement of a quantity with no date beside it
# cannot be ranked against a later or earlier statement of the same quantity.
_FINDING_DATES_CONTRACT = (
    " For every figure you report, fill in data_period with the period the "
    "figure applies to, statement_date with the date the source states or "
    "carries for it, and vintage with the dated edition of the data it rests "
    "on — each written as the source writes it (dates as YYYY, YYYY-MM, or "
    "YYYY-MM-DD) and each null when the source does not state it. The vintage "
    "is what tells the latest statement of a quantity from an older one."
)

# Who a figure belongs to, and what it is measured over. Both are properties
# of the figure, not of the page that carried it, and the audited run got both
# wrong in the same way: it credited a relay with the agency's own count — and
# so published a conflict where there was one figure published twice — and its
# extraction dropped a market monitor's all-segment total rather than record
# the segment the release states. A relay's copy is that body's measurement,
# and a total over every segment is not the segment a target asks about.
_FINDING_PROVENANCE_CONTRACT = (
    " Name the body the page attributes it to, separately from the page that "
    "served it: a release, a news story, or a data dive that repeats another "
    "organisation's figure credits that organisation, so put it in "
    "attributed_issuer and the page's own words for the attribution in "
    "attribution_quote — \"according to the U.S. Energy Information "
    "Administration (EIA)\" — and never credit the host that repeated it. "
    "The page's words for the attribution need not sit inside the excerpt you "
    "copied: an excerpt is one passage of the page, the attribution is "
    "another, and a sentence carrying on from an attributed one — \"the EIA "
    "projects that solar capacity will grow by another 32.5 GW in 2025. And "
    "... a record-breaking 18.2 GW ... are projected this year\" — is still "
    "that body's figure. Quote the page's own words for it, copied from the "
    "read rather than written in your own. "
    "Leave both null when the page states the figure as its own publisher's, "
    "and never state an attribution the passage does not carry, and keep the "
    "page's own words for what the figure measures: \"energy storage\" does "
    "not become \"battery storage\", and a count of installations does not "
    "become a count of something else. When the page states the scope, "
    "segment, or basis a figure covers, state the segment or basis the figure "
    "covers in measure_scope — \"all segments\", \"grid-scale\", "
    "\"utility-scale projects larger than 1 MW\" — and when the body the "
    "figure belongs to released it on a date the page states, record that "
    "date in release_date. A figure whose own scope differs from a target's "
    "wording is still a finding for the target whose measure it matches: "
    "record it with the scope the page states rather than dropping it, and "
    "never restate it in the target's own terms."
)


def render_planned_targets(targets: Sequence[EvidenceTarget]) -> str:
    """One line per planned target a finding may be bound to, in plan order.

    The id is what the reply has to copy, so it leads the line; the question
    is what decides the binding, so it follows in full, and any structured
    fields the plan set (measure, unit, period, kind, organisation) follow it
    so the model can bind a figure to the target it actually describes.
    Support policy and criticality stay off the line: they are the Fact
    Checker's business, and an extractor that tried to satisfy them would be
    guessing at a verdict it does not own.
    """
    lines: list[str] = []
    for target in targets:
        details = "; ".join(
            f"{name}: {value}"
            for name, value in (
                ("measure", target.measure),
                ("unit", target.unit_dimension),
                ("period", target.period),
                ("kind", target.kind),
                ("organisation", target.organisation),
            )
            if value
        )
        line = f"- {target.target_id} [{target.coverage_id}]: {target.question}"
        lines.append(f"{line} ({details})" if details else line)
    return "\n".join(lines)


# A passage "states a figure in the target's own measure unit" when a numeral
# stands beside a unit of the base that target names. The bases are power (W)
# and energy (Wh); the unit vocabulary is ``utils.types``' own — the very
# patterns ``qualifier_matches_requirement`` reads an atom's unit with — so
# what a passage owes and what the gate accepts cannot drift apart.
_MEASURE_UNITS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("energy", _ENERGY_UNIT),
    ("power", _POWER_UNIT),
)

# A numeral standing immediately before a unit, allowing the whitespace and
# brackets a figure is written with: "37,143 megawatt hours", "12,314 (MW)".
# "in 2020," is a year and a comma, not a figure, and "measured in MW" states
# no quantity at all.
_TRAILING_NUMERAL = re.compile(r"\d+(?:[.,]\d+)*[\s(]*$")


def _unit_mentions(text: str) -> list[tuple[str, re.Match[str]]]:
    """Every unit mention in ``text``, each with the base it measures.

    "megawatt hours" is the energy unit *and* contains the power word
    "megawatt", so an energy match owns its span and the power pattern is read
    only outside it. Without that rule the market monitor's "37,143 megawatt
    hours" would read as a power figure, and a target asking for megawatts
    would claim a figure that is somebody else's unit.
    """
    mentions: list[tuple[str, re.Match[str]]] = []
    energy_spans: list[tuple[int, int]] = []
    for base, pattern in _MEASURE_UNITS:
        for match in pattern.finditer(text):
            if base == "power" and any(
                start < match.end() and match.start() < end
                for start, end in energy_spans
            ):
                continue
            if base == "energy":
                energy_spans.append(match.span())
            mentions.append((base, match))
    return mentions


def _measure_bases(targets: Sequence[EvidenceTarget]) -> frozenset[str]:
    """The measure bases ("power", "energy") these targets ask for.

    A target asks for a quantity when its own prose names a unit: "in MW" is
    power, "in MWh" is energy. The question and the required dimensions are
    read together, because either carries the unit and neither is the whole
    obligation. A target that names no unit asks for no quantity, so no
    passage can owe it one — "measure: inclusion rule and counting treatment"
    must not turn every figure in the packet into extraction debt.
    """
    return frozenset(
        base
        for target in targets
        for text in (target.question, *target.required_dimensions)
        for base, _match in _unit_mentions(text)
    )


def _states_a_figure(text: str, bases: Collection[str]) -> bool:
    """True when ``text`` states a numeral in one of the given measure bases."""
    for base, match in _unit_mentions(text):
        if base not in bases:
            continue
        if _TRAILING_NUMERAL.search(text[: match.start()]):
            return True
    return False


def _units_owing_a_figure(
    evidence: Mapping[str, EvidenceUnit],
    *,
    target_ids: Sequence[str],
    bases: Collection[str],
    used: Collection[tuple[str, str]],
) -> list[EvidenceUnit]:
    """Selected units that state a figure in the target's own unit, unmined.

    Only units selected for the active target are asked, and only those no
    admitted finding used: a passage that produced a finding owes nothing, and
    a passage fetched for another topic is that topic's business. What is left
    and states a figure in a base the target asks for is evidence the
    extraction walked past — the audited run's "12,314 megawatts (MW) and
    37,143 megawatt hours (MWh) deployed" passage, selected, disposed of as
    irrelevant, and never mined for the MWh target that needed it.
    """
    admitted = set(used)
    owing: list[EvidenceUnit] = []
    for unit in evidence.values():
        if target_ids and not set(target_ids).intersection(unit.target_ids):
            continue
        if (unit.read_id, unit.locator) in admitted:
            continue
        if _states_a_figure(unit.excerpt, bases):
            owing.append(unit)
    return owing


def extraction_messages(
    task: SubTopicTask,
    run: ReActRun,
    *,
    evidence_chars: int,
    acquisition_context: str | None = None,
    planned_targets: Sequence[EvidenceTarget] = (),
    owed_passages: bool = False,
) -> list[ChatMessage]:
    """Build the messages that extract findings from one finished loop.

    ``planned_targets`` is the run's whole counted target inventory, not the
    active sub-topic's share of it. The read being mined was fetched for one
    topic, but its text may answer a target of another — the audible miss this
    parameter exists to stop — and the model can only bind a finding to a
    target it was shown. An empty sequence (a legacy plan, or a caller with no
    plan in hand) leaves the request without a list to bind against, which is
    what it was before this parameter existed.

    ``owed_passages`` marks the one bounded re-extraction's request. Its packet
    carries the selected passages a previous extraction returned no finding
    for even though each states a number in a unit a planned target asks for,
    so the request says what the packet is for instead of looking like a
    second helping of the evidence the model already declined.
    """
    criteria = "\n".join(
        f"- {criterion}" for criterion in task.sub_topic.success_criteria
    )
    registry_contract = (
        "Return one finding per distinct, source-backed figure or fact. Every finding "
        "MUST copy read_id and locator exactly as the acquisition context above prints "
        "them, and MUST carry a snippet: one or two sentences copied character for "
        "character from that passage, containing the finding's figures (at most "
        f"{MAX_SNIPPET_CHARS} characters). List every figure the snippet states for "
        "the finding in figures: value exactly as the snippet writes it (\"10.4\", "
        "\"12,314\"), unit as the snippet writes it (\"GW\", \"megawatts\", \"%\"), the "
        "period it applies to, and kind: actual for a measured or reported outcome, "
        "forecast for a projection, plan or expectation. Figures that measure "
        "different things - a yearly addition and a cumulative total - belong in "
        "separate findings. A finding MUST name in target_ids every planned target "
        "from the Planned targets list whose question its content answers — copy those ids from that "
        "list, never the targets= line of a read, which names only the "
        "sub-topic that fetched it, and mine each passage for every planned "
        "target rather than only the one that fetched it. A target id that is "
        "not in that list is dropped from the finding, and a finding with no "
        "planned target left is kept but can then be attributed only through "
        "the sub-topic that fetched its read. A finding whose snippet the "
        "locator does not contain is dropped. Copy source_url and source_title "
        "from the same read record, never from memory, a search snippet, or "
        "another finding's text; never substitute the URL or title the "
        "document names as its origin — a copy served from another host is "
        "cited where you read it, and the body it came from belongs in "
        "attributed_issuer — and never rewrite the read's title, not to drop "
        "the reader's own markers and not to put the document's own headline "
        "in their place. Never invent a content hash. Return an empty "
        "list when the evidence supports nothing."
        if acquisition_context is not None
        else "Return one finding per distinct, source-backed claim. Use the "
        "exact source_url and source_title from the evidence above. Return "
        "an empty list when the evidence supports nothing."
    )
    sections = [
        f"# Sub-topic\n{task.sub_topic.title}",
        f"# Success criteria\n{criteria}",
    ]
    if planned_targets:
        sections.append(
            "# Planned targets\n"
            "Every planned target of this run, in plan order. A finding whose "
            "content answers one of these questions names it in target_ids, "
            "whichever sub-topic the read was fetched for:\n"
            + render_planned_targets(planned_targets)
        )
    if owed_passages:
        sections.append(
            "# Passages owed a finding\n"
            "The passages below are selected evidence a previous extraction "
            "returned no finding for, and each states a number in a unit one "
            "of the planned targets asks for — the figure a target needs and "
            "the extraction walked past. Mine every passage here for every "
            "planned target whose question its content answers, in the same "
            "registry shape and with the same target ids the contract below "
            "requires."
        )
    sections.extend(
        [
            (
                "# Retrieved evidence\n"
                + (
                    acquisition_context
                    if acquisition_context is not None
                    else render_evidence(
                        run, limit=evidence_chars, discovery_payloads=False
                    )
                )
            ),
            f"# Response contract\n{registry_contract}{_FINDING_DATES_CONTRACT}"
            f"{_FINDING_PROVENANCE_CONTRACT}",
            (
                "# Reply format\n"
                f"{render_structured_reply_format(_FINDING_REPLY_EXAMPLES)}"
            ),
        ]
    )
    return [
        ChatMessage(role="developer", content=EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def _admitted_attribution(
    issuer: object,
    quote: object,
    *,
    read: ReadRecord | None,
    locator: str | None = None,
) -> tuple[str | None, str | None]:
    """The attribution a read evidences, or no attribution at all.

    The two halves are one claim — this page's own words hand the figure to
    that body — so they stand or fall together. Four things are all
    required, none of them alone: the quote has to be the read's verbatim
    text, the same containment an excerpt is admitted with, drawn from the
    excerpt's own passage or its immediate neighbour rather than anywhere in
    the document; it has to name the body the finding credits, matched the
    way every other issuer name in this project is matched; and a
    recognized attribution cue — "according to", "said", a possessive, or
    similar — has to sit beside that name, because a body the page only
    names, in passing or in contrast, is not a body the page credits with
    anything. Nothing else is admitted: an attribution the page does not
    state is the exact failure this pair exists to prevent, and the
    read-less legacy path has nothing to check a quote against, so it
    records none.

    Dropping the pair never drops the finding. Its excerpt is still the
    source's own text; what is lost is the claim about who published the
    figure, and with it any credit a later stage could give the wrong body.
    """
    if read is None:
        return None, None
    name = issuer.strip() if isinstance(issuer, str) else ""
    phrase = quote.strip() if isinstance(quote, str) else ""
    if not name or not phrase:
        return None, None
    document = neighbouring_passage_text(read, locator or "")
    if not excerpt_matches(document, phrase):
        return None, None
    name_match = re.search(_issuer_name_pattern(name), phrase, re.IGNORECASE)
    if name_match is None or not attribution_cue_adjacent(phrase, name_match):
        return None, None
    return name, phrase


def _admitted_measure_scope(read: ReadRecord | None, value: object) -> str | None:
    """The measured scope the read states, or ``None``.

    Admitted only when the page's own text carries it verbatim — the same
    containment an excerpt is admitted with — so a scope the model
    paraphrased or invented, the exact failure that let an all-segment total
    print as a target's narrower "grid-scale", is dropped rather than
    trusted.
    """
    if read is None:
        return None
    scope = value.strip() if isinstance(value, str) else ""
    if not scope:
        return None
    document = " ".join([read.title, *read.passages.values()])
    return scope if excerpt_matches(document, scope) else None


def _admitted_release_date(read: ReadRecord | None, value: object) -> str | None:
    """The release date the read states, or ``None``.

    Verified the way :mod:`evidence` verifies every other date this project
    records: the read's own text has to state this value, at this precision
    or a finer one, as a date rather than as a fragment of an identifier. A
    value the page never wrote is dropped rather than printed as the
    attributed body's own release day.
    """
    if read is None:
        return None
    date = value.strip() if isinstance(value, str) else ""
    if not date:
        return None
    document = " ".join([read.title, *read.passages.values()])
    return date if _quote_states(document, (date,)) else None


def build_findings(
    draft: SubTopicFindingsDraft,
    *,
    sub_topic: SubTopic,
    extracted_at: str,
    known_urls: Sequence[str],
    known_reads: Mapping[str, ReadRecord] | None = None,
    valid_target_ids: Sequence[str] | None = None,
    admitted_evidence_keys: list[tuple[str, str]] | None = None,
    dropped_target_ids: list[str] | None = None,
    dropped_figures: list[str] | None = None,
) -> tuple[list[Finding], list[str]]:
    """Stamp drafts into ``Finding`` values, naming the ones that were dropped.

    Rejection reasons are generated here and never copied from provider
    output, so they are safe to record in ``ResearchError.details``.

    ``known_urls`` is the provenance allow-list: the URLs this run actually
    retrieved. A syntactically valid URL that was never retrieved — most
    plausibly a copied prompt example — is dropped rather than entering
    research state, which closes a gap the earlier shape-only check left open.

    ``known_reads`` non-``None`` means the acquisition path is active, and
    there the registry fields are REQUIRED, not opt-in: a finding that names no
    admitted read id cannot be checked against the read registry at all, and an
    optional check is a check the model can skip. Every accepted finding is
    also appended to ``admitted_evidence_keys`` as its ``(read_id, locator)``
    when the caller passes a list, which is how the caller can tell a passage
    that produced a finding from a different passage of the same read.

    ``valid_target_ids`` is the plan's counted target inventory — every id the
    extraction was shown. A finding keeps the ids that are in it, whatever
    sub-topic it came from; an id outside it is dropped from the finding and
    named in ``dropped_target_ids``, but the finding itself is kept. That
    asymmetry is deliberate. The id vocabulary the extraction can confuse a
    plan target with is the one the packet prints on every read and evidence
    unit — the *coverage* id of the sub-topic that fetched it (``topic-01``
    against ``topic-01-target-01``) — so a model that copies the wrong one
    would lose every finding, and a run with no findings answers nothing.
    ``claim_attribution`` reads a finding with no planned target exactly as it
    read every finding before this binding existed: through the sub-topic that
    fetched its read, with the claim's own prose still deciding the binding.
    ``None`` means no plan was in hand (a legacy caller), and then no binding
    is checked or invented.

    A figure with no value or no unit is likewise dropped from the finding
    alone and named in ``dropped_figures``, never in the returned rejection
    list: an unusable figure is not a reason to distrust the finding it came
    from, so ``extract_findings`` must not tell an operator the finding
    itself was dropped when it was kept with its other figures.
    """
    findings: list[Finding] = []
    rejected: list[str] = []
    allowed = {normalize_source_url(url) for url in known_urls}
    for index, item in enumerate(draft.findings, start=1):
        read = None
        source_url = item.source_url
        source_title = item.source_title
        if known_reads is not None and item.read_id is None:
            rejected.append(
                f"finding {index}: the acquisition path requires an admitted "
                "read id"
            )
            continue
        if item.read_id is not None:
            read = None if known_reads is None else known_reads.get(item.read_id)
            if read is None:
                rejected.append(f"finding {index}: read id was not admitted")
                continue
            source_url = item.source_url or read.resolved_url
            source_title = item.source_title or read.title
            if normalize_source_url(source_url) != read.resolved_url:
                rejected.append(f"finding {index}: source url did not match read")
                continue
            if source_title != read.title:
                rejected.append(f"finding {index}: source title did not match read")
                continue
            if not item.locator or not item.snippet:
                rejected.append(
                    f"finding {index}: read id requires locator and snippet"
                )
                continue
            if len(item.snippet) > MAX_SNIPPET_CHARS:
                rejected.append(
                    f"finding {index}: snippet longer than {MAX_SNIPPET_CHARS} characters"
                )
                continue
            passage = read.passages.get(item.locator)
            if passage is None or not excerpt_matches(passage, item.snippet):
                rejected.append(
                    f"finding {index}: snippet was not admitted at locator"
                )
                continue
            if valid_target_ids is not None:
                kept, drops = _admitted_target_ids(
                    item.target_ids, valid_target_ids, index=index
                )
                if dropped_target_ids is not None:
                    dropped_target_ids.extend(drops)
            else:
                kept = list(item.target_ids)
        else:
            kept = list(item.target_ids)
        if normalize_source_url(source_url) not in allowed:
            rejected.append(f"finding {index}: source url was not retrieved")
            continue
        try:
            attributed_issuer, attribution_quote = _admitted_attribution(
                item.attributed_issuer,
                item.attribution_quote,
                read=read,
                locator=item.locator,
            )
            if read is not None:
                figures, figure_drops = _admitted_figures(item.figures, index=index)
                if dropped_figures is not None:
                    dropped_figures.extend(figure_drops)
            else:
                figures = []
            findings.append(
                Finding(
                    content=item.content,
                    source_url=source_url,
                    source_title=source_title,
                    extracted_at=extracted_at,
                    confidence=item.confidence,
                    related_sub_topic=sub_topic.title,
                    target_ids=kept,
                    data_period=item.data_period,
                    statement_date=item.statement_date,
                    vintage=item.vintage,
                    attributed_issuer=attributed_issuer,
                    attribution_quote=attribution_quote,
                    measure_scope=_admitted_measure_scope(read, item.measure_scope),
                    release_date=_admitted_release_date(read, item.release_date),
                    snippet=item.snippet if read is not None else None,
                    read_id=item.read_id if read is not None else None,
                    locator=item.locator if read is not None else None,
                    figures=figures,
                )
            )
        except ValidationError as error:
            rejected.append(f"finding {index}: invalid {_invalid_fields(error)}")
            continue
        if (
            admitted_evidence_keys is not None
            and item.read_id is not None
            and item.locator
        ):
            admitted_evidence_keys.append((item.read_id, item.locator))
    return findings, rejected


def _admitted_target_ids(
    named: Sequence[str],
    valid_target_ids: Sequence[str],
    *,
    index: int,
) -> tuple[list[str], list[str]]:
    """The planned ids ``named`` keeps, and the record of the ones it invented.

    The drop is named per id rather than per finding, because the interesting
    event is a specific id the plan never issued — that is what tells a later
    reader whether the extraction misread the plan or the plan was stale.
    """
    valid = set(valid_target_ids)
    kept: list[str] = []
    dropped: list[str] = []
    for target_id in named:
        if target_id in valid:
            if target_id not in kept:
                kept.append(target_id)
            continue
        dropped.append(
            f'finding {index}: dropped target id "{target_id}" '
            "(it is not a planned target)"
        )
    return kept, dropped


# The model's free-text figure kind is not constrained to the closed
# vocabulary: a live pass reported "plan" for EIA's own "planned additions"
# of 19.6 GW, which is a forecast in every sense but the exact spelling. A
# recognised forecast synonym is normalised rather than silently dropped to
# ``None`` — the honesty rule (spec §2) is "Forecasts are reported with
# issuer and release. Actuals are labelled as actuals.", and losing the
# verdict entirely would let a downstream reader print a plan as though it
# were a measured outcome. Anything else unrecognised is left ``None`` for
# the Context Check to set from the passage (§5.2); it never raises.
_FORECAST_KIND_SYNONYMS = frozenset(
    {
        "plan", "plans", "planned", "planning",
        "projected", "projection", "forecasted",
        "expected", "target", "targeted",
    }
)


def _normalized_figure_kind(kind: str) -> FigureKind | None:
    """``kind``, already folded, mapped onto the closed vocabulary, or ``None``."""
    if kind in ("actual", "forecast"):
        return kind  # type: ignore[return-value]
    return "forecast" if kind in _FORECAST_KIND_SYNONYMS else None


def _admitted_figures(
    drafts: Sequence[FindingFigureDraft], *, index: int
) -> tuple[list[FindingFigure], list[str]]:
    """The figures a finding may carry, and the ones it could not state.

    An unusable figure -- no value or no unit -- is named in the returned
    ``dropped`` list, never in the finding's own rejection reasons: a figure
    a draft could not usably state is not a reason to distrust the finding
    itself, so it is left off ``figures`` alone and the finding still stands.
    """
    figures: list[FindingFigure] = []
    dropped: list[str] = []
    for position, draft in enumerate(drafts, start=1):
        value, unit = draft.value.strip(), draft.unit.strip()
        if not value or not unit:
            dropped.append(
                f"finding {index}: figure {position} has no value or unit"
            )
            continue
        kind = (draft.kind or "").strip().casefold()
        figures.append(
            FindingFigure(
                value=value,
                unit=unit,
                period=(draft.period or "").strip() or None,
                kind=_normalized_figure_kind(kind),
            )
        )
    return figures, dropped


class BoundedFindings(NamedTuple):
    """What one sub-topic's extracted findings became after bounding."""

    retained: list[Finding]
    dropped_duplicate: int
    dropped_cap: int
    sources_retained: int
    publishers_retained: int
    source_urls_retained: int
    findings_retained: int
    works_retained: int


def bound_sub_topic_findings(
    findings: Sequence[Finding],
    *,
    reads: Sequence[ReadRecord] = (),
    max_findings: int = MAX_FINDINGS_PER_SUB_TOPIC,
    max_sources: int = MAX_UNIQUE_SOURCES_PER_SUB_TOPIC,
) -> BoundedFindings:
    """Fold restatements, then bound one sub-topic's kept evidence.

    The cap bounds *useful evidence*, never planned coverage: every planned
    sub-topic still gets its turn, and what a sub-topic could not keep is
    reported rather than silently discarded.

    Selection is by confidence, but not by confidence alone. Findings are
    grouped by *publisher* — not by URL — ranked by their strongest finding and
    then taken round-robin, so one verbose publisher cannot fill the whole
    allowance and push an independent second source out of the report. Within a
    publisher the strongest findings go first, duplicates keep their highest
    confidence, and the retained list comes back strongest-first.

    Grouping by URL did not do that, whatever this docstring said: four pages
    from one publisher were four groups, so they could take all four of
    ``max_sources`` and leave a genuinely independent publisher out. That is a
    corroboration problem, not a tidiness one — downstream, a claim can only be
    verified by a publisher other than the ones that made it.

    ``reads`` is the run's read registry. It is what makes ``works_retained`` a
    work count rather than a URL count under a second name: two URLs serving
    the same complete document resolve to one work, while a finding with no
    read in the registry stays its own unresolved entry.
    """
    if max_findings < 1 or max_sources < 1:
        raise ValueError("max_findings and max_sources must be at least 1")

    deduplicated = deduplicate_findings(findings)
    groups: dict[str, list[Finding]] = {}
    for finding in deduplicated:
        groups.setdefault(
            publisher_identity(finding.source_url), []
        ).append(finding)

    by_source = sorted(
        groups.values(),
        key=lambda group: max(finding.confidence for finding in group),
        reverse=True,
    )[:max_sources]
    for group in by_source:
        group.sort(key=lambda finding: finding.confidence, reverse=True)

    retained: list[Finding] = []
    depth = 0
    while len(retained) < max_findings and any(
        len(group) > depth for group in by_source
    ):
        for group in by_source:
            if depth >= len(group):
                continue
            retained.append(group[depth])
            if len(retained) == max_findings:
                break
        depth += 1

    retained.sort(key=lambda finding: finding.confidence, reverse=True)
    return BoundedFindings(
        retained=retained,
        dropped_duplicate=len(findings) - len(deduplicated),
        dropped_cap=len(deduplicated) - len(retained),
        sources_retained=len(
            {normalize_source_url(finding.source_url) for finding in retained}
        ),
        publishers_retained=len(
            {publisher_identity(finding.source_url) for finding in retained}
        ),
        source_urls_retained=len(
            {normalize_source_url(finding.source_url) for finding in retained}
        ),
        findings_retained=len(retained),
        works_retained=retained_work_count(
            [finding.source_url for finding in retained], reads
        ),
    )


def sub_topic_started_event(
    sub_topic: SubTopic,
    *,
    index: int,
    existing_sources: int,
) -> ResearchEvent:
    """Announce that one sub-topic's loop is about to run."""
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.started",
        message=f"Researching sub-topic {index}.",
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "priority": sub_topic.priority,
            "index": index,
            "existing_sources": existing_sources,
        },
    )


def tool_call_events(
    sub_topic: SubTopic,
    run: ReActRun,
) -> list[ResearchEvent]:
    """Report one event per tool call the sub-topic's loop made."""
    events: list[ResearchEvent] = []
    for step in run.steps:
        observation = step.observation
        if observation is None:
            continue
        events.append(
            agent_event(
                agent_name=RESEARCHER_NAME,
                event_type="researcher.tool_call",
                message=f"{observation.tool_name} call completed.",
                metadata={
                    "sub_topic": summarize_text(sub_topic.title),
                    "tool": observation.tool_name,
                    "proposal_id": step.proposal_id,
                    "iteration": step.iteration,
                    "success": observation.success,
                    "error_type": observation.error_type,
                },
            )
        )
    return events


def sub_topic_completed_event(
    sub_topic: SubTopic,
    run: ReActRun,
    *,
    index: int,
    findings: int,
    dropped_duplicate: int,
    dropped_cap: int,
    sources_retained: int,
    publishers_retained: int,
    source_urls_retained: int,
    findings_retained: int,
    works_retained: int = 0,
    successful_reads: int = 0,
    useful_evidence_yield: int = 0,
    acquired_work_count: int = 0,
    target_obligation_completed: bool = False,
) -> ResearchEvent:
    """Report one sub-topic's stop reason, counts, and finding total.

    ``findings`` is what entered research state; the four bounded-evidence
    counts say what extraction produced that did not, and why — restatements
    folded into an existing finding, distinct findings or sources past the
    per-sub-topic cap, and how many distinct intellectual works those sources
    actually are, which is not their URL count. ``successful_reads`` counts
    reads whose body was admitted this pass, ``useful_evidence_yield`` counts
    selected units for the active target, and ``acquired_work_count`` counts
    the unique network bodies this run actually downloaded — ``cache_hits``
    (from ``run``) is reported beside it so a reused body is never read as new
    acquisition.

    ``target_obligation_completed`` is the Task 3 signal that the active
    target's obligation advanced: at least one registry-admitted finding was
    extracted for it. The reader-level completion verdict (required
    dimensions, support policy) belongs to Task 5's claim machinery and is not
    claimed here.
    """
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.completed",
        message=f"Sub-topic {index} complete.",
        metadata={
            "sub_topic": summarize_text(sub_topic.title),
            "index": index,
            "stop_reason": run.stop_reason,
            "iterations": run.iterations,
            "tool_calls": run.tool_calls,
            "cache_hits": run.cache_hits,
            "findings": findings,
            "findings_dropped_duplicate": dropped_duplicate,
            "findings_dropped_cap": dropped_cap,
            "sources_retained": sources_retained,
            "publishers_retained": publishers_retained,
            "source_urls_retained": source_urls_retained,
            "findings_retained": findings_retained,
            "works_retained": works_retained,
            "successful_reads": successful_reads,
            "useful_evidence_yield": useful_evidence_yield,
            "acquired_work_count": acquired_work_count,
            "target_obligation_completed": target_obligation_completed,
        },
    )


def research_completed_event(
    *,
    sub_topics_planned: int,
    sub_topics_researched: int,
    sub_topics_skipped: int,
    findings: int,
) -> ResearchEvent:
    """Report the whole research pass.

    ``sub_topics_planned`` is every sub-topic the Planner produced;
    ``sub_topics_skipped`` is however many of those were never attempted —
    dropped by the ``max_sub_topics`` cap, omitted because an interim
    refinement-satisfaction rule passed, or left unstarted when a
    non-recoverable provider failure stopped the pass early. Together with
    ``sub_topics_researched`` this makes "was every planned sub-topic
    accounted for" answerable from the event stream alone, without cross-
    referencing ``state.errors``.
    """
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.research.completed",
        message="Research pass complete.",
        metadata={
            "sub_topics_planned": sub_topics_planned,
            "sub_topics_researched": sub_topics_researched,
            "sub_topics_skipped": sub_topics_skipped,
            "findings": findings,
        },
    )


def no_findings_error(sub_topic: SubTopic, run: ReActRun) -> ResearchError:
    """Warn that a high-priority sub-topic produced nothing citable.

    The caller must only call this once a sub-topic's loop and extraction
    are both known to have succeeded — a run that died to a provider
    failure gets ``extraction_provider_error`` or the loop-level
    ``provider_error`` instead, never this, so a coverage gap is never
    confused with an infrastructure outage.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_sub_topic_without_findings",
        message=(
            "A high-priority sub-topic produced no findings; the report will "
            "be incomplete for it."
        ),
        details={
            "sub_topic": summarize_text(sub_topic.title),
            "priority": sub_topic.priority,
            "stop_reason": run.stop_reason,
            "tool_calls": run.tool_calls,
        },
    )


def sub_topic_skipped_error(
    sub_topic: SubTopic,
    *,
    reason: str,
) -> ResearchError:
    """Warn that a planned sub-topic was never attempted at all.

    ``reason`` is one of four enumerated strings, never raw exception text:
    ``"cap"`` when ``max_sub_topics`` truncated the planned list before this
    sub-topic's turn came up, ``"provider_failure_stopped_processing"`` when an
    earlier sub-topic's non-recoverable provider failure stopped the pass
    before this sub-topic could run, ``"required_targets_completed"`` when
    every required target the topic carries is already answered by a reader
    statement and the Critic asked for no new searches, or
    ``"interim_satisfaction"`` for the legacy case of a topic whose plan
    carries no evidence target at all and which already has a finding.
    Recoverable: the rest of the report can still stand, just incomplete for
    this sub-topic.

    Every unattempted sub-topic gets one of these, whatever its priority:
    the record carries the sub-topic's ``coverage_id`` so the plans a pass
    did not reach are named rather than inferred from a count.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_sub_topic_skipped",
        message=(
            "A planned sub-topic was never researched; the report will be "
            "incomplete for it."
        ),
        details={
            "sub_topic": summarize_text(sub_topic.title),
            "coverage_id": sub_topic.coverage_id,
            "priority": sub_topic.priority,
            "reason": reason,
        },
    )


def extraction_provider_error(
    run: ReActRun,
    error: Exception,
) -> ResearchError:
    """Record that a sub-topic's extraction call could not reach the provider.

    Non-recoverable: the caller must stop researching remaining sub-topics,
    mirroring the ReAct-loop-level ``provider_error`` path. ``details``
    carries the static operation, safe provider snapshot, and counts, never
    ``str(error)`` — the same redaction discipline ``react.py`` and
    ``planner.py`` follow.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_extraction_provider_error",
        message=(
            "The model provider failed while extracting findings for a "
            "sub-topic; the research pass stopped before further "
            "sub-topics were researched."
        ),
        recoverable=False,
        details=agent_provider_failure_details(
            "researcher_finding_extraction",
            error,
            iterations=run.iterations,
            tool_calls=run.tool_calls,
        ),
    )


def owed_extraction_provider_error(
    run: ReActRun,
    error: Exception,
) -> ResearchError:
    """Record that a sub-topic's bounded re-extraction could not reach the model.

    Recoverable, unlike the first extraction call's failure: the re-extraction
    is an improvement on evidence already in hand, so its outage costs the
    retry alone. The findings the first call extracted stand, the pass
    continues to the remaining sub-topics, and every passage the retry would
    have mined keeps its explicit ``unmined_quantity`` disposition, which is
    what the ledger discloses. ``details`` carries the static operation, the
    safe provider snapshot, and counts, never ``str(error)`` — the same
    redaction discipline the rest of this module follows.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_re_extraction_provider_error",
        message=(
            "The model provider failed while re-extracting a sub-topic's "
            "unmined quantity passages; those passages were recorded as "
            "unmined and the research pass continued."
        ),
        details=agent_provider_failure_details(
            "researcher_owed_passage_extraction",
            error,
            iterations=run.iterations,
            tool_calls=run.tool_calls,
        ),
    )


class ResearcherAgent(BaseAgent[ResearchFindings]):
    """Run one bounded ReAct loop per selected sub-topic and extract findings.

    ``run`` is overridden because the spec requires a loop *per sub-topic*,
    which the single-loop ``BaseAgent.run`` cannot express. Everything below
    ``run`` — bounds, tracing, tool execution, scratchpad writes — is still
    the shared runtime's.
    """

    name = RESEARCHER_NAME
    description = "Gather source-backed findings for planned sub-topics."
    # The four read/discovery tools, and nothing that writes: a finding kept
    # in long-term memory before the pass is complete is a write nothing
    # downstream has validated. `save_to_memory` belongs to the agents that
    # finalize evidence, not to the one gathering it.
    allowed_tools = (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    )

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
        max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
        high_priority_threshold: int = HIGH_PRIORITY_THRESHOLD,
        evidence_chars: int = DEFAULT_EVIDENCE_CHARS,
        selected_passages_per_read: int | None = None,
        evidence_packet_chars: int | None = None,
        cache: MutableMapping[str, ReadRecord] | None = None,
        network_read_ids: set[str] | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
            model_profile=model_profile,
        )
        if max_sub_topics < 1:
            raise ValueError("max_sub_topics must be at least 1")
        if high_priority_threshold < 1:
            raise ValueError("high_priority_threshold must be at least 1")
        if evidence_chars < 1:
            raise ValueError("evidence_chars must be at least 1")
        selected_limit = (
            self.config.selected_passages_per_read
            if selected_passages_per_read is None
            else selected_passages_per_read
        )
        packet_limit = (
            self.config.evidence_packet_chars
            if evidence_packet_chars is None
            else evidence_packet_chars
        )
        if selected_limit < 1:
            raise ValueError("selected_passages_per_read must be at least 1")
        if packet_limit < 1:
            raise ValueError("evidence_packet_chars must be at least 1")
        probe = clock()
        if probe.tzinfo is None or probe.utcoffset() is None:
            raise AgentConfigurationError(
                "ResearcherAgent clock must return a timezone-aware "
                "datetime; got a naive datetime instead"
            )
        self._max_sub_topics = max_sub_topics
        self._high_priority_threshold = high_priority_threshold
        self._evidence_chars = evidence_chars
        self._selected_passages_per_read = selected_limit
        self._evidence_packet_chars = packet_limit
        self._clock = clock
        # These registries are intentionally run-scoped and shared by every
        # sub-topic loop. A body read for one target can therefore be selected
        # locally for another target without another network acquisition.
        self._run_reads: dict[str, ReadRecord] = {}
        self._run_evidence = {}
        self._run_dispositions = []
        self._run_boundary_audits = {}
        # The one counter every sub-topic's policy claims its manifests from.
        # A manifest id is fingerprinted from the job, the agent, the
        # operation and the sequence, and the first three are the same for
        # every sub-topic of a run, so a counter each policy owned would mint
        # the id an earlier sub-topic already used, with different contents,
        # and the shared mapping above would keep only the later one.
        self._run_audit_sequence = ManifestSequence()
        self._run_acquisition_states = {}
        self._run_seen_target_ids: set[str] = set()
        self._run_cache: dict[str, ReadRecord] = {}
        self._run_network_read_ids: set[str] = set()
        self._run_cited_reads: dict[str, tuple[str, str]] = {}
        self._shared_cache = cache if cache is not None else {}
        self._shared_network_read_ids = (
            network_read_ids if network_read_ids is not None else set()
        )
        self._active_acquisition: AcquisitionPolicy | None = None
        self._active_target_id: str | None = None
        self._run_source_state: ResearchState | None = None
        self._last_successful_reads = 0
        self._last_useful_evidence_yield = 0
        self._last_acquired_work_count = 0
        self._last_target_obligation_completed = False

    @property
    def output_schema(self) -> type[ResearchFindings]:
        """The validated findings. Never sent to the provider.

        ``extract_findings`` asks for ``SubTopicFindingsDraft`` instead,
        because ``Finding`` carries ``Field`` constraints that do not survive
        strict JSON schema conversion. Do not route this agent through
        ``complete_output``.
        """
        return ResearchFindings

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return RESEARCHER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        """Describe the run as a whole. ``sub_topic_task`` narrows it."""
        return AgentTask(
            instruction=state.original_question,
            guidance=render_session_guidance(state),
        )

    def build_decision_context(
        self,
        task: AgentTask,
        *,
        iteration: int,
        steps: Sequence[ReActStep],
    ) -> str:
        """Refresh and render the active target's complete acquisition state."""
        del task, iteration, steps
        policy = self._active_acquisition
        if policy is None:
            return ""
        return policy.context(limit=self._evidence_packet_chars)

    def _planned_targets(self) -> list[EvidenceTarget]:
        """Every counted target of the run's plan, in plan order.

        Read from the run's own state rather than from the active
        ``AcquisitionPolicy``: a policy knows the one coverage id it was built
        for, and the point of the extraction list is precisely that a read
        fetched for one topic may answer another topic's target. The order is
        the plan's, so the list is stable across a run's sub-topics and a
        replayed request differs only where its evidence does.

        A run with no plan in hand — a direct ``extract_findings`` call, or a
        snapshot predating the target inventory — yields none, and extraction
        then binds nothing, because there is no inventory to bind against.
        """
        state = self._run_source_state
        if state is None:
            return []
        return [
            target
            for sub_topic in state.sub_topics
            for target in counted_evidence_targets(sub_topic.evidence_targets)
        ]

    def _policy_for_task(self, task: SubTopicTask) -> AcquisitionPolicy:
        target_id = task.sub_topic.coverage_id
        existing = (
            None
            if target_id in self._run_seen_target_ids
            else self._run_acquisition_states.get(target_id)
        )
        if existing is None:
            acquisition_state = AcquisitionState(
                target_id=target_id,
                remaining_calls=self.config.tool_budget_for(self.name),
                remaining_model_turns=self.config.max_iterations,
            )
        else:
            acquisition_state = existing
        return AcquisitionPolicy(
            state=acquisition_state,
            session_id=(
                self._run_source_state.session_id
                if self._run_source_state is not None
                else "researcher-session"
            ),
            target_id=target_id,
            query=(
                f"{task.sub_topic.title} "
                + " ".join(task.sub_topic.success_criteria)
            ),
            origin="researcher",
            reads=self._run_reads,
            evidence=self._run_evidence,
            dispositions=self._run_dispositions,
            boundary_audits=self._run_boundary_audits,
            audit_sequence=self._run_audit_sequence,
            retrieved_at=lambda: self._clock().isoformat(),
            selected_passages_per_read=self._selected_passages_per_read,
            configuration_fingerprint=(
                f"selected={self._selected_passages_per_read};"
                f"packet={self._evidence_packet_chars}"
            ),
            cache=self._run_cache,
            network_read_ids=self._run_network_read_ids,
            cited_reads=self._run_cited_reads,
        )

    def sub_topic_task(
        self,
        base: AgentTask,
        sub_topic: SubTopic,
        existing_sources: Sequence[str],
        *,
        prioritized_queries: Sequence[str] = (),
    ) -> SubTopicTask:
        """Narrow the run-level task down to one sub-topic's loop."""
        sections = [
            section
            for section in (
                base.guidance.strip(),
                render_sub_topic_guidance(
                    sub_topic,
                    existing_sources,
                    prioritized_queries=prioritized_queries,
                ),
            )
            if section
        ]
        return SubTopicTask(
            instruction=(
                f'Gather evidence for the sub-topic "{sub_topic.title}" of '
                f"the research question: {base.instruction}"
            ),
            guidance="\n\n".join(sections),
            sub_topic=sub_topic,
            existing_sources=list(existing_sources),
        )

    async def extract_findings(
        self,
        task: SubTopicTask,
        run: ReActRun,
    ) -> tuple[list[Finding], list[ResearchError], bool]:
        """Turn one finished sub-topic loop into validated findings.

        Returns nothing — and makes no provider call — when the loop stopped
        on a provider failure or READ no source, so the extraction step can
        never invent one. "Read" is the shared read-bearing rule: a loop that
        only searched, or only wrote to memory, has nothing to extract from.

        The third element, ``provider_failed``, is ``True`` only when the
        extraction call itself could not reach the model provider. The
        caller must treat that the same way it treats a ReAct-loop-level
        ``provider_error``: stop researching further sub-topics, but keep
        every finding already collected.
        """
        # One call, two consumers: the same tuple gates the provider call and
        # becomes the provenance allow-list, so "did this loop read anything"
        # and "which URLs may a finding cite" cannot disagree.
        self._last_target_obligation_completed = False
        policy = self._active_acquisition
        retrieved = (
            tuple(
                dict.fromkeys(
                    read.resolved_url
                    for read in policy.reads.values()
                    if read.resolved_url in policy.state.read_urls
                )
            )
            if policy is not None
            else retrieved_finding_urls(run)
        )
        if not run.succeeded or not retrieved:
            return [], [], False

        if policy is not None:
            # The local extract step, taken before the provider call so the
            # continuation batch is part of the packet. Bounded and idempotent:
            # a batch already handed over inside the loop is not repeated, and
            # the batch bound drains the pending list instead of leaving a
            # gate acquisition can never pass.
            policy.extract_passage_batch()

        planned_targets = self._planned_targets() if policy is not None else []
        try:
            draft = await self.provider.complete_structured(
                extraction_messages(
                    task,
                    run,
                    evidence_chars=self._evidence_chars,
                    acquisition_context=(
                        policy.context(limit=self._evidence_packet_chars)
                        if policy is not None
                        else None
                    ),
                    planned_targets=planned_targets,
                ),
                SubTopicFindingsDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            return [], [extraction_provider_error(run, error)], True

        admitted_keys: list[tuple[str, str]] = []
        unplanned_target_ids: list[str] = []
        dropped_figures: list[str] = []
        known_reads = (
            {
                read_id: read
                for read_id, read in policy.reads.items()
                if read.resolved_url in policy.state.read_urls
            }
            if policy is not None
            else None
        )
        valid_target_ids = (
            [target.target_id for target in planned_targets]
            if planned_targets
            else None
        )

        def mine(
            draft: SubTopicFindingsDraft,
        ) -> tuple[list[Finding], list[str]]:
            """Stamp one provider reply into validated findings and drops."""
            return build_findings(
                draft,
                sub_topic=task.sub_topic,
                extracted_at=self._clock().isoformat(),
                known_urls=retrieved,
                known_reads=known_reads,
                valid_target_ids=valid_target_ids,
                admitted_evidence_keys=admitted_keys,
                dropped_target_ids=unplanned_target_ids,
                dropped_figures=dropped_figures,
            )

        findings, rejected = mine(draft)
        errors: list[ResearchError] = []
        owed: list[EvidenceUnit] = []
        if policy is not None:
            # The selected passages this sub-topic's own targets ask a figure
            # for and no admitted finding used. Only the units selected for
            # this target are asked, and only its own targets' measure units
            # decide what a passage owes.
            owed = _units_owing_a_figure(
                policy.evidence,
                target_ids=(
                    () if policy.target_id is None else (policy.target_id,)
                ),
                bases=_measure_bases(
                    counted_evidence_targets(task.sub_topic.evidence_targets)
                ),
                used=admitted_keys,
            )
            if owed:
                # ONE bounded second extraction, never a loop: a first packet
                # ranks dozens of passages, and the passage that carries the
                # figure the target asks for is exactly what a ranking can
                # bury. The re-ask is over those passages alone, so the model
                # is not asked to find them again in a packet they were lost
                # in. A provider failure here costs the retry only: the
                # findings already extracted stand, and every owed unit keeps
                # its own disposition, which is what the ledger discloses.
                try:
                    retry_draft = await self.provider.complete_structured(
                        extraction_messages(
                            task,
                            run,
                            evidence_chars=self._evidence_chars,
                            acquisition_context=build_acquisition_context(
                                policy.state,
                                policy.reads,
                                {unit.evidence_id: unit for unit in owed},
                                limit=self._evidence_packet_chars,
                                target_id=policy.target_id,
                                dispositions=policy.dispositions,
                                focus_ids=[unit.evidence_id for unit in owed],
                            ),
                            planned_targets=planned_targets,
                            owed_passages=True,
                        ),
                        SubTopicFindingsDraft,
                        agent_name=self.name,
                    )
                except ProviderError as error:
                    errors.append(owed_extraction_provider_error(run, error))
                else:
                    retry_findings, retry_rejected = mine(retry_draft)
                    findings = [*findings, *retry_findings]
                    rejected = [*rejected, *retry_rejected]
            # Unit-level, so a passage is "used" only when that exact passage
            # produced an admitted finding. Whatever the re-extraction left
            # unmined says so in its own reason rather than "irrelevant".
            policy.record_extraction_dispositions(
                admitted_keys,
                unmined_quantity_ids=[unit.evidence_id for unit in owed],
            )
            # The obligation this pass completed is the ACTIVE topic's, and a
            # finding bound to another topic's target does not complete it.
            # Mining a read for every planned target would otherwise let one
            # topic's binding stand in for another's. Only findings that
            # carry a binding are asked: an unbound finding (a legacy one, or
            # an extraction that named an id outside the plan) is attributed
            # through this very topic later, which is what the old reading —
            # "some passage of this topic was used" — already said.
            own_target_ids = {
                target.target_id
                for target in counted_evidence_targets(
                    task.sub_topic.evidence_targets
                )
            }
            bound = [finding for finding in findings if finding.target_ids]
            completed = policy.target_id is not None and bool(admitted_keys)
            if completed and own_target_ids and bound:
                completed = any(
                    own_target_ids.intersection(finding.target_ids)
                    for finding in bound
                )
            self._last_target_obligation_completed = completed
        if unplanned_target_ids:
            # Recorded even though the finding was kept: an id the plan never
            # issued is invisible in state otherwise, and every later stage
            # would read the surviving binding as the whole story.
            errors.append(
                agent_error(
                    agent_name=self.name,
                    error_type="researcher_unplanned_target",
                    message=(
                        "Some extracted findings named targets outside the "
                        "plan; those ids were dropped."
                    ),
                    details={
                        "sub_topic": summarize_text(task.sub_topic.title),
                        "dropped_target_ids": unplanned_target_ids,
                    },
                )
            )
        if dropped_figures:
            # Recorded even though the finding was kept: a figure with no
            # value or unit is not a reason to distrust the finding, so the
            # drop gets its own error type rather than the "malformed
            # finding" one, which would misreport a kept finding as dropped.
            errors.append(
                agent_error(
                    agent_name=self.name,
                    error_type="researcher_dropped_figure",
                    message=(
                        "Some extracted findings named a figure with no "
                        "value or unit; that figure was dropped."
                    ),
                    details={
                        "sub_topic": summarize_text(task.sub_topic.title),
                        "dropped_figures": dropped_figures,
                    },
                )
            )
        if policy is not None:
            policy.complete_extraction()
        if not rejected:
            return findings, errors, False
        errors.append(
            agent_error(
                agent_name=self.name,
                error_type="researcher_invalid_finding",
                message=(
                    "Some extracted findings were malformed and were dropped."
                ),
                details={
                    "sub_topic": summarize_text(task.sub_topic.title),
                    "rejected": rejected,
                },
            )
        )
        return findings, errors, False

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> ResearchFindings | None:
        """Adapt ``extract_findings`` to the ``BaseAgent`` hook.

        ``run`` calls ``extract_findings`` directly so it can keep the
        recoverable errors this hook signature has nowhere to return.
        """
        if not isinstance(task, SubTopicTask):
            raise AgentConfigurationError(
                "ResearcherAgent.finalize requires a SubTopicTask"
            )
        findings, _, _ = await self.extract_findings(task, run)
        return ResearchFindings(findings=findings)

    def state_update(
        self,
        result: ResearchFindings | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """Findings and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["raw_findings"] = list(result.findings)
        if (
            self._run_reads
            or self._run_evidence
            or self._run_dispositions
            or self._run_acquisition_states
        ):
            update.update(
                {
                    "read_records": dict(self._run_reads),
                    "evidence_units": dict(self._run_evidence),
                    "evidence_dispositions": list(self._run_dispositions),
                    "boundary_audits": dict(self._run_boundary_audits),
                    "acquisition_state_by_target": dict(
                        self._run_acquisition_states
                    ),
                    "quality_contract_version": QUALITY_CONTRACT_VERSION,
                }
            )
        return update

    async def _research_sub_topic(self, task: SubTopicTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes about the previous sub-topic
        are noise in this one's prompt. Context that genuinely carries over
        travels in ``task.guidance`` instead.
        """
        self.scratchpad.clear()
        toolset = self.toolset
        policy = self._policy_for_task(task)
        self._active_acquisition = policy
        self._active_target_id = task.sub_topic.coverage_id

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> tuple[ReActDecision, ...]:
            return await self._complete_react_decision(
                task,
                iteration=iteration,
                steps=steps,
            )

        react = await run_react_loop(
            agent_name=self.name,
            tracker=self.tracker,
            tools=toolset,
            decide=decide,
            max_iterations=self.config.max_iterations,
            tool_budget=self.config.tool_budget_for(self.name),
            on_step=self._record_step,
            is_sufficient=self.is_sufficient,
            summary_limit=self.config.observation_summary_chars,
            tool_policy=policy,
            job_id=f"{self.name}/{policy.session_id}/{policy.target_id}",
            propagate_provider_errors=False,
        )
        return react.model_copy(
            update={
                "errors": [*react.errors, *self.scratchpad.drain_errors()]
            }
        )

    async def run(self, state: ResearchState) -> AgentRun[ResearchFindings]:
        """Research each selected sub-topic in its own bounded loop."""
        self._run_source_state = state
        self._run_reads = dict(state.read_records)
        self._run_evidence = dict(state.evidence_units)
        self._run_dispositions = list(state.evidence_dispositions)
        self._run_boundary_audits = dict(state.boundary_audits)
        # ``merge_boundary_audits`` refuses one id with two different
        # contents, so any prior instance that wrote into
        # ``state.boundary_audits`` minted at most ``len(state.boundary_audits)``
        # distinct audits total across every agent — meaning this instance's
        # own highest-used sequence is strictly less than that count. A fresh
        # instance (e.g. a checkpoint restore reconstructing this agent
        # against already-populated state) would otherwise re-seed at 0 and
        # remint ids an earlier instance already claimed. Seeding here, in
        # place, before any ``AcquisitionPolicy`` is built from
        # ``self._run_audit_sequence``, is provably collision-free; it only
        # "wastes" unused sequence space, which costs nothing since sequence
        # values carry no meaning beyond uniqueness.
        self._run_audit_sequence.seed_at_least(len(state.boundary_audits))
        self._run_acquisition_states = dict(state.acquisition_state_by_target)
        self._run_seen_target_ids = set()
        self._run_cache = self._shared_cache
        # The cache is keyed by URL, never by read id: a lookup happens before
        # any body download and only knows the URL it is about to request.
        # Seeding it with read ids worked by accident (nothing looks a read id
        # up here) and would have silently polluted the shared cache Task 6
        # wires for the Fact Checker.
        for read in self._run_reads.values():
            if read.acquisition_kind != "network":
                continue
            self._run_cache.setdefault(read.resolved_url, read)
            self._run_cache.setdefault(read.requested_url, read)
        self._run_network_read_ids = self._shared_network_read_ids
        self._run_network_read_ids.update(
            {
                read.read_id
                for read in self._run_reads.values()
                if read.acquisition_kind == "network"
            }
        )
        # The read each citation was made against, keyed by canonical URL: a
        # cache entry that is no longer that read is re-fetched rather than
        # served under a citation to text nobody re-read.
        self._run_cited_reads = _cited_read_incidence(state)
        self._active_acquisition = None
        self._active_target_id = None
        base_task = self.build_task(state)
        ordered = _ordered_sub_topics(state)
        satisfied = _refinement_satisfied_sub_topics(state)
        selected = ordered[: self._max_sub_topics]
        capped = ordered[self._max_sub_topics :]
        events: list[ResearchEvent] = []
        errors: list[ResearchError] = []
        findings: list[Finding] = []
        runs: list[ReActRun] = []

        stopped_at: int | None = None
        for index, sub_topic in enumerate(selected, start=1):
            existing = existing_sources_for(state, sub_topic)
            task = self.sub_topic_task(
                base_task,
                sub_topic,
                existing,
                prioritized_queries=_critic_queries_for(state, sub_topic),
            )
            events.append(
                sub_topic_started_event(
                    sub_topic, index=index, existing_sources=len(existing)
                )
            )
            async with self.tracker.agent_span(self.name) as span:
                react = await self._research_sub_topic(task)
                (
                    sub_findings,
                    extraction_errors,
                    extraction_failed,
                ) = await self.extract_findings(task, react)
                if self._active_acquisition is not None:
                    policy = self._active_acquisition
                    self._last_successful_reads = sum(
                        read.resolved_url in policy.state.read_urls
                        for read in policy.reads.values()
                    )
                    self._last_useful_evidence_yield = sum(
                        policy.target_id in unit.target_ids
                        for unit in policy.evidence.values()
                    )
                    self._last_acquired_work_count = policy.acquired_work_count
                    if extraction_failed:
                        # A retryable extraction failure consumed nothing: the
                        # batch stays owed, visible in the persisted
                        # ``pending_extraction_ids``, so the next pass knows
                        # exactly which reads still need extracting.
                        policy.defer_extraction()
                    else:
                        policy.complete_extraction()
                    target_id = policy.target_id
                    if target_id is not None:
                        self._run_acquisition_states[target_id] = (
                            self._active_acquisition.state
                        )
                        self._run_seen_target_ids.add(target_id)
                self._active_acquisition = None
                self._active_target_id = None
                if extraction_failed:
                    # Mirror the loop-level provider_error path so the
                    # merged run (and this sub-topic's own completed event)
                    # never claims "finished" over an abort that actually
                    # happened during extraction.
                    react = react.model_copy(
                        update={"stop_reason": "provider_error"}
                    )
                bounded = bound_sub_topic_findings(
                    sub_findings, reads=list(self._run_reads.values())
                )
                span.set_outputs(
                    {
                        "agent_name": self.name,
                        "sub_topic": sub_topic.title,
                        "stop_reason": react.stop_reason,
                        "iterations": react.iterations,
                        "tool_calls": react.tool_calls,
                        "findings": len(bounded.retained),
                    }
                )

            runs.append(react)
            findings.extend(bounded.retained)
            errors.extend(react.errors)
            errors.extend(extraction_errors)
            events.extend(tool_call_events(sub_topic, react))
            events.append(
                sub_topic_completed_event(
                    sub_topic,
                    react,
                    index=index,
                    findings=len(bounded.retained),
                    dropped_duplicate=bounded.dropped_duplicate,
                    dropped_cap=bounded.dropped_cap,
                    sources_retained=bounded.sources_retained,
                    publishers_retained=bounded.publishers_retained,
                    source_urls_retained=bounded.source_urls_retained,
                    findings_retained=bounded.findings_retained,
                    works_retained=bounded.works_retained,
                    successful_reads=self._last_successful_reads,
                    useful_evidence_yield=self._last_useful_evidence_yield,
                    acquired_work_count=self._last_acquired_work_count,
                    target_obligation_completed=(
                        self._last_target_obligation_completed
                    ),
                )
            )
            if not react.succeeded:
                # A provider failure — whether from the ReAct loop or from
                # extraction — is non-recoverable; the next sub-topic would
                # almost certainly repeat it at cost. Findings already
                # collected from this and prior sub-topics are kept. This
                # check must run before the "no findings" check below: a
                # sub-topic that died to a provider error is an outage, not
                # a coverage gap, and must never be reported as one.
                stopped_at = index
                break
            if not bounded.retained and is_high_priority(
                sub_topic, threshold=self._high_priority_threshold
            ):
                errors.append(no_findings_error(sub_topic, react))

        # Every sub-topic that was never attempted — either truncated by the
        # max_sub_topics cap, or left unstarted when a provider failure
        # stopped the pass early — gets a structured, recoverable record
        # carrying its coverage id, whatever its priority. Without this,
        # state.errors and the event stream cannot be trusted as "every
        # planned sub-topic was attempted or explicitly skipped": sub-topics
        # could vanish with no trace. Only the warning for an *attempted*
        # sub-topic that produced nothing stays restricted to the
        # high-priority ones, so a thin low-priority topic is not noise.
        skipped_by_break = selected[stopped_at:] if stopped_at is not None else []
        unattempted: list[tuple[SubTopic, str]] = [
            *satisfied,
            *[(sub_topic, "cap") for sub_topic in capped],
            *[
                (sub_topic, "provider_failure_stopped_processing")
                for sub_topic in skipped_by_break
            ],
        ]
        for sub_topic, reason in unattempted:
            errors.append(sub_topic_skipped_error(sub_topic, reason=reason))

        if not selected and not state.sub_topics:
            errors.append(
                agent_error(
                    agent_name=self.name,
                    error_type="researcher_no_sub_topics",
                    message="No sub-topics were available to research.",
                )
            )
        events.append(
            research_completed_event(
                sub_topics_planned=len(state.sub_topics),
                sub_topics_researched=len(runs),
                sub_topics_skipped=len(unattempted),
                findings=len(findings),
            )
        )

        merged = merge_react_runs(self.name, runs).model_copy(
            update={"errors": errors}
        )
        result = ResearchFindings(findings=findings)
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=merged,
            errors=errors,
            state_update={
                **self.state_update(result, merged),
                "events": events,
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
