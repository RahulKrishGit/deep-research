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
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import datetime, timezone
from typing import NamedTuple

from pydantic import Field, JsonValue, ValidationError

from deep_research.agents.acquisition import (
    AcquisitionPolicy,
)
from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import excerpt_matches
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
    QUALITY_CONTRACT_VERSION,
    AcquisitionState,
    ContractModel,
    CritiqueGap,
    Finding,
    ReadRecord,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    SubTopic,
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
    "Verification needs independence, so a sub-topic is not finished when its "
    "key facts come from a single publisher. Find a second source on a "
    "different site that states each load-bearing number or finding: a fact "
    "only one source states is recorded as unverified no matter how "
    "authoritative that source is. Prefer spending a remaining call on that "
    "second source over another query for the same one.\n"
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
    # Newer extraction callers may identify the exact registry item. These
    # remain optional for compatibility with the original URL/title schema;
    # when supplied, build_findings verifies every field against the read.
    read_id: str | None = None
    locator: str | None = None
    excerpt: str | None = None
    target_ids: list[str] = Field(default_factory=list)


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


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


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


def _has_prior_finding(state: ResearchState, sub_topic: SubTopic) -> bool:
    title = _normalized(sub_topic.title)
    return any(
        _normalized(finding.related_sub_topic) == title
        for finding in state.raw_findings
    )


def _refinement_satisfied_sub_topics(state: ResearchState) -> list[SubTopic]:
    """Return non-gap topics already satisfied by a prior raw finding."""
    if state.critique is None:
        return []
    gaps_by_target = _critic_gaps_by_target(state)
    return [
        sub_topic
        for sub_topic in state.sub_topics
        if not _is_critic_gap_target(sub_topic, gaps_by_target)
        and _has_prior_finding(state, sub_topic)
    ]


def _ordered_sub_topics(state: ResearchState) -> list[SubTopic]:
    """Order eligible sub-topics by Critic-targeted gaps, then priority.

    A sub-topic counts as gap-targeted when the Critic returned a gap whose
    ``coverage_id`` equals it exactly. Ties resolve by ``priority`` ascending
    (1 is most important), then by the order the planner produced. On a
    refinement pass, untargeted topics with a prior finding whose normalized
    related sub-topic matches their title are omitted as interim-satisfied.
    Initial passes keep every planned topic. Callers that need to know which
    sub-topics a ``max_sub_topics`` cap left out (``ResearcherAgent.run``) use
    this directly instead of ``select_sub_topics``, which only returns the
    truncated head.
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
        if _is_critic_gap_target(sub_topic, gaps_by_target)
        or not _has_prior_finding(state, sub_topic)
    ]


def select_sub_topics(
    state: ResearchState,
    *,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
) -> list[SubTopic]:
    """Return the top eligible sub-topics, ordered by ``_ordered_sub_topics``.

    Initial passes include every planned sub-topic. Refinement passes omit
    non-gap topics already covered by a prior finding. Sub-topics past the cap
    are truncated here with no record of their own — ``ResearcherAgent.run``
    is responsible for recording what this cap drops and what refinement
    satisfaction omitted.
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
    """The queries the Critic routed to this exact plan ID, in order.

    Only the gaps whose ``coverage_id`` equals the sub-topic's own are read,
    so a refinement pass never spends another topic's queries on this one.
    """
    queries: list[str] = []
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
# empty-list case, which is valid and is not the opposite end of a scale.
_FINDING_REPLY_EXAMPLES = (
    (
        "Example input: an example report at "
        "https://evidence.example.test/report states that the measured "
        "reduction was 12 percent.",
        '{"findings":[{"content":"The example report measured a 12 percent '
        'reduction.","source_url":"https://evidence.example.test/report",'
        '"source_title":"Example report","confidence":0.8}]}',
    ),
)


def extraction_messages(
    task: SubTopicTask,
    run: ReActRun,
    *,
    evidence_chars: int,
    acquisition_context: str | None = None,
) -> list[ChatMessage]:
    """Build the messages that extract findings from one finished loop."""
    criteria = "\n".join(
        f"- {criterion}" for criterion in task.sub_topic.success_criteria
    )
    sections = [
        f"# Sub-topic\n{task.sub_topic.title}",
            f"# Success criteria\n{criteria}",
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
        (
            "# Response contract\nReturn one finding per distinct, "
            "source-backed claim. Use the exact source_url and source_title "
            "from the evidence above. When a read_id, locator, and excerpt "
            "are present, copy those registry fields exactly; never invent a "
            "content hash. Return an empty list when the evidence supports "
            "nothing."
        ),
        (
            "# Reply format\n"
            f"{render_structured_reply_format(_FINDING_REPLY_EXAMPLES)}"
        ),
    ]
    return [
        ChatMessage(role="developer", content=EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def build_findings(
    draft: SubTopicFindingsDraft,
    *,
    sub_topic: SubTopic,
    extracted_at: str,
    known_urls: Sequence[str],
    known_reads: Mapping[str, ReadRecord] | None = None,
    target_id: str | None = None,
) -> tuple[list[Finding], list[str]]:
    """Stamp drafts into ``Finding`` values, naming the ones that were dropped.

    Rejection reasons are generated here and never copied from provider
    output, so they are safe to record in ``ResearchError.details``.

    ``known_urls`` is the provenance allow-list: the URLs this run actually
    retrieved. A syntactically valid URL that was never retrieved — most
    plausibly a copied prompt example — is dropped rather than entering
    research state, which closes a gap the earlier shape-only check left open.
    """
    findings: list[Finding] = []
    rejected: list[str] = []
    allowed = {normalize_source_url(url) for url in known_urls}
    for index, item in enumerate(draft.findings, start=1):
        read = None
        source_url = item.source_url
        source_title = item.source_title
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
            if not item.locator or not item.excerpt:
                rejected.append(
                    f"finding {index}: read id requires locator and excerpt"
                )
                continue
            passage = read.passages.get(item.locator)
            if passage is None or not excerpt_matches(passage, item.excerpt):
                rejected.append(
                    f"finding {index}: excerpt was not admitted at locator"
                )
                continue
            if target_id is not None and target_id not in item.target_ids:
                rejected.append(f"finding {index}: target id was not admitted")
                continue
        if normalize_source_url(source_url) not in allowed:
            rejected.append(f"finding {index}: source url was not retrieved")
            continue
        try:
            findings.append(
                Finding(
                    content=item.content,
                    source_url=source_url,
                    source_title=source_title,
                    extracted_at=extracted_at,
                    confidence=item.confidence,
                    related_sub_topic=sub_topic.title,
                )
            )
        except ValidationError as error:
            rejected.append(f"finding {index}: invalid {_invalid_fields(error)}")
    return findings, rejected


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
        # Task 4 enriches findings with explicit work identities. Until then,
        # a distinct retained source is the conservative work-count alias.
        works_retained=len(
            {normalize_source_url(finding.source_url) for finding in retained}
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
    works_retained: int,
    successful_reads: int = 0,
    useful_evidence_yield: int = 0,
) -> ResearchEvent:
    """Report one sub-topic's stop reason, counts, and finding total.

    ``findings`` is what entered research state; the three bounded-evidence
    counts say what extraction produced that did not, and why — restatements
    folded into an existing finding, and distinct findings or sources past the
    per-sub-topic cap.
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

    ``reason`` is one of three enumerated strings, never raw exception text:
    ``"cap"`` when ``max_sub_topics`` truncated the planned list before this
    sub-topic's turn came up, or ``"provider_failure_stopped_processing"``
    when an earlier sub-topic's non-recoverable provider failure stopped
    the pass before this sub-topic could run, or ``"interim_satisfaction"``
    when a non-gap topic already has a matching prior finding on a refinement
    pass. Recoverable: the rest of the report can still stand, just incomplete
    for this sub-topic.

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
        self._run_acquisition_states = {}
        self._run_seen_target_ids: set[str] = set()
        self._run_cache: dict[str, ReadRecord] = {}
        self._run_network_read_ids: set[str] = set()
        self._shared_cache = cache if cache is not None else {}
        self._shared_network_read_ids = (
            network_read_ids if network_read_ids is not None else set()
        )
        self._active_acquisition: AcquisitionPolicy | None = None
        self._active_target_id: str | None = None
        self._run_source_state: ResearchState | None = None
        self._last_successful_reads = 0
        self._last_useful_evidence_yield = 0

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
            retrieved_at=lambda: self._clock().isoformat(),
            selected_passages_per_read=self._selected_passages_per_read,
            configuration_fingerprint=(
                f"selected={self._selected_passages_per_read};"
                f"packet={self._evidence_packet_chars}"
            ),
            cache=self._run_cache,
            network_read_ids=self._run_network_read_ids,
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
                ),
                SubTopicFindingsDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            return [], [extraction_provider_error(run, error)], True

        findings, rejected = build_findings(
            draft,
            sub_topic=task.sub_topic,
            extracted_at=self._clock().isoformat(),
            known_urls=retrieved,
            known_reads=(
                {
                    read_id: read
                    for read_id, read in policy.reads.items()
                    if read.resolved_url in policy.state.read_urls
                }
                if policy is not None
                else None
            ),
            target_id=(policy.target_id if policy is not None else None),
        )
        if policy is not None:
            policy.record_extraction_dispositions(
                [finding.source_url for finding in findings]
            )
        if not rejected:
            if policy is not None:
                policy.complete_extraction()
            return findings, [], False
        if policy is not None:
            policy.complete_extraction()
        return findings, [
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
        ], False

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
                bounded = bound_sub_topic_findings(sub_findings)
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
            (sub_topic, "interim_satisfaction") for sub_topic in satisfied
        ] + [
            (sub_topic, "cap") for sub_topic in capped
        ] + [
            (sub_topic, "provider_failure_stopped_processing")
            for sub_topic in skipped_by_break
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
