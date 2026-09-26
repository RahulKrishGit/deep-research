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

import asyncio
import json
import logging
import re
from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Literal, NamedTuple, TypeAlias

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
    _issuer_name_pattern,
    _quote_states,
    attribution_cue_adjacent,
    excerpt_matches,
    locate_snippet,
    neighbouring_passage_text,
    retained_work_count,
)
from deep_research.agents.figures import is_a_date
from deep_research.agents.identity import deduplicate_findings
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_structured_reply_format,
    render_structured_request,
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
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
)
from deep_research.request_budget import RequestAttemptLimitError
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.tools.passage_selection import _tokens
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    _ENERGY_UNIT,
    _POWER_UNIT,
    MAX_SNIPPET_CHARS,
    QUALITY_CONTRACT_VERSION,
    AcquisitionState,
    ContractModel,
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
    counted_evidence_targets,
)

_logger = logging.getLogger(__name__)

RESEARCHER_NAME = "researcher"
HIGH_PRIORITY_THRESHOLD = 2
# The Planner's own ceiling is seven sub-topics, so one research pass attempts
# the whole plan by default rather than silently truncating it.
DEFAULT_MAX_SUB_TOPICS = 10
# Evidence kept per sub-topic, not coverage planned. These are runaway
# guards, not the extraction contract's own limits -- the user's standing
# ruling is to lift content caps and keep only runaway guards -- so a
# sub-topic may report at most 120 distinct findings drawn from at most 48
# distinct sources: high enough that a genuinely broad topic's own evidence
# is never cut for volume alone, and still a backstop against a runaway
# extraction.
MAX_FINDINGS_PER_SUB_TOPIC = 120
MAX_UNIQUE_SOURCES_PER_SUB_TOPIC = 48
# How many findings one required target may keep outside those caps. The
# exemption is what stops a cap from deleting the answer the run was sent to
# get; the ceiling is what stops an extraction that binds its whole output to
# one required target from making the cap meaningless. An obligation's answer
# is one claim and its strongest restatement, not twenty-five of them.
MAX_EXEMPT_PER_REQUIRED_TARGET = 2
# How many dispute findings (D2, Fable's audit of run 7) one disputed
# statement's own target ids may keep outside those caps -- same guarantee,
# same shape as the required-target exemption above, since the caps would
# otherwise drop a page's own rejection of a step exactly as readily as they
# drop a required target's answer. A handful of pages disputing the same
# statement is still one dissent, not unbounded volume.
MAX_DISPUTE_EXEMPT_PER_TARGET = 4
# How the bounded re-extraction is bounded at its input. The pre-flights that
# handed one call every owed passage of a topic's read ended at a 32,768- or
# 49,152-token output from about 10,000 tokens of input, so a packet carries at
# most eight passages and a pass spends at most two packets: what a bounded
# packet cannot hold stays unmined, and the passages that owe the most are the
# ones asked about first.
MAX_OWED_PASSAGES_PER_BATCH = 8
# How many disputed statements the dissent re-ask's own packet may list
# (RevZ1, run 7 fix wave review, P1): built only from the passages that
# actually made the packet, capped at the top few by overlap -- without a
# cap, a pool of hundreds of retained findings turns into a packet of
# hundreds of D-lines, tens of thousands of characters the model must
# still pick the right target ids out of.
MAX_DISPUTED_STATEMENTS = 8
# The cross-topic sweep's own floor (Fable's audit of run 6, Appendix 2): one
# shared token let chrome and bibliography passages fill every packet slot
# -- 42 packets and 269k output tokens for ten weak findings, none of them
# the content the sweep exists to catch. Two distinct, non-generic tokens is
# still a low bar for a passage that genuinely answers the target's own
# question, and high enough that a passage sharing only the plan's own
# repeated subject word never qualifies alone. The own-topic owed re-ask
# keeps its one-token floor unchanged: it retries a page already selected
# for this topic's own query, not a page fetched for someone else's.
MIN_CROSS_TOPIC_SHARED_TOKENS = 2
MAX_OWED_BATCHES = 2
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
    "The acquisition state line in this request is binding: only the actions "
    "it lists under allowed_actions can run, and anything else is refused "
    "before it reaches a tool. Two searches in a row, in one turn or "
    "across turns, are followed by a read, "
    "a search on the last turn or in the last call of the budget is refused, "
    "and only a URL a search result, a memory lead or a document link "
    "discovered can be read.\n"
    "Spend your calls on reading, not on repeating searches: after a search, "
    "read the most promising result it returned before searching again, and "
    "when the state lists both read and search, read.\n"
    "Prefer primary sources: laws and regulator orders, standards bodies, "
    "official datasets, original research, and issuer filings. Use secondary "
    "analysis to find or interpret primary material, not as the default "
    "support for load-bearing numbers.\n"
    "Read the organisation's own page first, reached through a search that "
    "names that organisation: when a figure belongs to an organisation - an "
    "agency's statistics, an institute's report, a company's filing - read "
    "that organisation's own site, report page or filing before any story "
    "that repeats it, and use a relay only when the original is not "
    "reachable. Do not search for a second source to confirm a figure its own "
    "organisation publishes.\n"
    "A search result is a lead, never evidence: only a page or document you "
    "read in this loop is evidence. For a rule, a list or a set of items, "
    "read the page section that states the whole provision, with its "
    "conditions and exceptions, rather than a summary that mentions it.\n"
    "If a page is refused, empty or a challenge page, the refusal is a fact "
    "about that URL and not about the publisher: read the same document at "
    "another URL or another official host, or read it as a document — "
    "document_reader handles PDFs, spreadsheets and data files — and if a "
    "tool fails, try another query or another source.\n"
    "Finish once the sub-topic's obligations are answered — the obligations "
    "line lists what this sub-topic owes — or once no further source is worth "
    "retrieving. A required obligation is answered only by a page about that "
    "obligation, not one that merely mentions it in passing: when your first "
    "search for a required obligation returns only general pages, search once "
    "more in the obligation's own words and read the page it returns about "
    "that obligation before you finish. Write one line "
    "saying that you are stopping and why: no other text you write is read, "
    "because a separate extraction step reads the pages this loop read and "
    "its findings are what the run keeps."
)

EXTRACTION_SYSTEM_PROMPT = (
    "You extract findings from the pages a research loop read. The research "
    "question and the sub-topic's planned targets are in this request. Report "
    "only what those pages state, and cite every finding to the exact read it "
    "came from — its read_id, locator, source_url and source_title. Return an "
    "empty list rather than inventing a source. Confidence is how directly "
    "the passage states what a planned target asks for, this sub-topic's own "
    "targets first: a passage that states one of this sub-topic's own targets "
    "outright scores highest, one that states another sub-topic's target "
    "outright scores next, and one that only mentions either in passing "
    "scores low; code ranks findings by it when a sub-topic holds more "
    "evidence than it may keep."
)


class FindingFigureDraft(ContractModel):
    """One figure the snippet states, before domain validation (no Field constraints)."""

    value: str
    unit: str
    period: str | None = None
    kind: str | None = None
    subject: str | None = None


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


def _eligible_sub_topics(state: ResearchState) -> list[SubTopic]:
    """The sub-topics this pass may run, in priority order (spec §6.5, §7.2).

    The first pass runs every planned sub-topic: a topic is planned because
    the question needs it, so its priority orders the pass rather than
    excluding anything from it. An extra pass runs only the topics that own a
    required target still missing a verified finding — ``state.
    extra_pass_target_ids`` is that pass's whole job list, replaced at every
    write — and every other topic is simply not part of the pass, which is not
    a coverage gap and is never recorded as one.

    Ties resolve by ``priority`` ascending (1 is most important), then by the
    order the planner produced, so the same plan always yields the same pass.
    Pure: it reads ``state`` and nothing else, so the pass that selects a
    topic and the pass that folds its results agree without shared state.
    """
    ordered = sorted(state.sub_topics, key=lambda topic: topic.priority)
    wanted = set(state.extra_pass_target_ids)
    if not wanted:
        return ordered
    return [
        topic
        for topic in ordered
        if any(target.target_id in wanted for target in topic.evidence_targets)
    ]


def select_sub_topics(
    state: ResearchState,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
) -> list[SubTopic]:
    """The first pass researches every planned sub-topic; an extra pass only the
    sub-topics that own a missing required target (spec §6.5, §7.2)."""
    if max_sub_topics < 1:
        raise ValueError("max_sub_topics must be at least 1")
    return _eligible_sub_topics(state)[:max_sub_topics]


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
    """Render the run's shared context: the guidance its recall produced."""
    sections: list[str] = []
    memory_section = render_memory_guidance(state.memory_context)
    if memory_section:
        sections.append(memory_section)
    return "\n\n".join(sections)


def render_sub_topic_guidance(
    sub_topic: SubTopic,
    existing_sources: Sequence[str],
) -> str:
    """Render one sub-topic's brief, including sources already collected.

    The queries are the plan's own; a sub-topic's searches are what its
    ``search_queries`` say. Its planned targets are printed as the obligations
    the loop owes, because coverage is judged on those and not on the
    criteria: a loop that stopped when the criteria looked met left required
    targets unanswered, and it cannot aim at an obligation it was never shown.
    The success criteria still follow — they say what the evidence has to
    establish — but the obligations decide when the loop is done.
    """
    lines = [
        f"Sub-topic: {sub_topic.title}",
        f"Why it matters: {sub_topic.rationale}",
        f"Priority: {sub_topic.priority} (1 is most important)",
        "Suggested search queries:",
    ]
    lines.extend(f"- {query}" for query in sub_topic.search_queries)
    obligations = counted_evidence_targets(sub_topic.evidence_targets)
    if obligations:
        lines.append(
            "The answers this sub-topic owes (its obligations; [required] "
            "ones decide coverage):"
        )
        lines.append(render_planned_targets(obligations, fields=False))
    lines.append("What the evidence has to establish:")
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
# Two examples, and neither fills a field its passage does not state: the
# first is a figure finding whose only date is the period the page writes, and
# the second is a text finding — no figures at all — bound to a required
# target, with the body the page's own words credit put in attributed_issuer.
# The second exists because the first cannot teach that shape: a rule, a
# reproduced instrument or a relayed statement is usually all a run has for a
# qualitative target, and an example set of figures alone taught restating.
_FINDING_REPLY_EXAMPLES = (
    (
        "Example input: passage read-111111111111111111111111 locator page-4-"
        "chunk-0 of the example report at "
        "https://evidence.example.test/report (fetched for topic-01); the "
        "passage reads \"The 2025 edition of the survey puts the median "
        "annual premium at 480 euros in 2024, up from 455 euros in 2023.\"; "
        "the Planned targets list names topic-01-target-01 [required] (the "
        "median annual premium the survey reports for 2024).",
        '{"findings":[{"content":"The 2025 edition of the survey puts the median '
        "annual premium at 480 euros in 2024, up from 455 euros in 2023.\","
        '"source_url":"https://evidence.example.test/report",'
        '"source_title":"Example report","confidence":0.9,'
        '"read_id":"read-111111111111111111111111","locator":"page-4-chunk-0",'
        '"snippet":"The 2025 edition of the survey puts the median annual '
        'premium at 480 euros in 2024, up from 455 euros in 2023.",'
        '"figures":[{"value":"480","unit":"euros","period":"2024",'
        '"kind":"actual","subject":"median annual premium"},'
        '{"value":"455","unit":"euros","period":"2023","kind":"actual",'
        '"subject":"median annual premium"}],'
        '"target_ids":["topic-01-target-01"],"data_period":"2024",'
        '"vintage":"the 2025 edition of the survey"}]}',
    ),
    (
        "Example input: passage read-222222222222222222222222 locator page-2-"
        "chunk-1 of the page served at https://record.example.test/notice "
        "(fetched for topic-02); the passage reads \"According to the record "
        "office's circular, a filing is late when it arrives after the last "
        "day of the second month that follows the period it covers.\"; the "
        "Planned targets list names topic-02-target-01 [required] (when a "
        "filing counts as late).",
        '{"findings":[{"content":"According to the record office\'s circular, '
        "a filing is late when it arrives after the last day of the second "
        "month that follows the period it covers.\","
        '"source_url":"https://record.example.test/notice",'
        '"source_title":"Example notice","confidence":0.85,'
        '"read_id":"read-222222222222222222222222","locator":"page-2-chunk-1",'
        '"snippet":"According to the record office\'s circular, a filing is '
        'late when it arrives after the last day of the second month that '
        'follows the period it covers.","figures":[],'
        '"target_ids":["topic-02-target-01"],'
        '"attributed_issuer":"the record office",'
        '"attribution_quote":"According to the record office\'s circular"}]}',
    ),
)

# What every finding must say about the figures it reports, on either path.
# Appended to both response contracts because the failure it prevents is not
# acquisition-specific: a statement of a quantity with no date beside it
# cannot be ranked against a later or earlier statement of the same quantity.
_FINDING_DATES_CONTRACT = (
    "\n- Fill in the date fields for every finding, with or without figures: "
    "data_period with the period the finding's statement applies to, "
    "statement_date with a date the page gives for that statement itself — "
    "never the page's own date, and never the day you read it — vintage with "
    "the edition the page names for the data it rests on, and, per figure, "
    "period with the period that figure applies to. Write each as the page "
    "writes it, in any form that carries a number (\"2024\", \"Q1 2024\", "
    "\"2024-03\", \"Q1'25\", \"2024-2026\"), and null when the page states "
    "none of it.\n"
    "- A period is what the page dates the statement or figure *with*, never "
    "a phrase that dates it against the page: leave it null when the page "
    "only says \"this year\", \"the latest quarter\" or \"so far this "
    "year\". Those phrases are the page's own relative wording, the excerpt "
    "carries them, and code resolves the period they name from the page's own "
    "date and records which date it came from. A level the page puts at a "
    "point in time takes that reference as its period (a figure the page "
    "places \"at the end of Q1'25\" is a Q1'25 figure).\n"
    "- The vintage is only what the page names as the edition, and it is what "
    "tells the latest statement of a quantity from an older one."
)

# Who a figure belongs to, and what it is measured over. Both are properties
# of the figure, not of the page that carried it, and the audited run got both
# wrong in the same way: it credited a relay with the agency's own count — and
# so published a conflict where there was one figure published twice — and its
# extraction dropped a market monitor's all-segment total rather than record
# the segment the release states. A relay's copy is that body's measurement,
# and a total over every segment is not the segment a target asks about.
_FINDING_PROVENANCE_CONTRACT = (
    "\n- Name the body the page's words give the statement or figure to, "
    "separately from the page that served it, for every finding and not only "
    "for figures: a release, a news story or a data dive that repeats another "
    "organisation's work credits that organisation, so put it in "
    "attributed_issuer and the page's own words for the attribution in "
    "attribution_quote — \"according to the Example Statistical Agency "
    "(ESA)\" — and never credit the host that repeated it. The body that "
    "wrote the words is what counts: a page serving another body's document, "
    "even one printing that body's own first-person words, is still that "
    "body's, and \"own\" never means the host that served a copy.\n"
    "- attribution_quote is copied character for character from the read: "
    "from the excerpt's own passage, from the passage immediately before or "
    "after it, or from the page's own title or the heading of the excerpt's "
    "own passage where that "
    "names the body or the instrument. Words further down the page attribute "
    "nothing. A naming becomes an attribution through a cue beside the name: "
    "a preposition phrase (\"according to\", \"reported by\", \"reporting "
    "from\"), a possessive (\"the agency's figure\"), a body's own source "
    "noun (\"agency data\", \"the institute's report\"), a reporting verb "
    "straight after the name (\"the agency reports\", \"the institute "
    "found\") or a title opening on its name (\"ESA: output to reach 30 "
    "thousand units\"). A sentence carrying on from "
    "an attributed one — \"the agency projects that the price will rise by "
    "another 3.2 percent next year, and a record 41,000 transactions are "
    "projected this year\" — is still that body's. Copy the page's words "
    "rather than writing your own.\n"
    "- Leave both null when the page states the statement as its own "
    "publisher's, and never state an attribution the passage does not carry, "
    "and keep the page's own words for what the finding measures: a broader "
    "category on the page never becomes the narrower one a target asks for — "
    "\"all sites\" does not become \"sites in the north\" — and a count of "
    "one thing does not become a count of another.\n"
    "- When the page states the scope, segment or basis a statement covers, "
    "state that wording in measure_scope — \"all sites\", \"the northern "
    "region only\", \"bodies above a stated size\" — and record release_date "
    "only when the page ties a date to the release or edition of the body the "
    "statement belongs to, never the page's own date.\n"
    "- A statement whose own scope differs from a target's wording is "
    "recorded with the scope the page states and never restated in the "
    "target's own terms, but it does not answer that target: bind a figure "
    "only when the same thing is measured."
)


def render_planned_targets(
    targets: Sequence[EvidenceTarget],
    *,
    coverage_titles: Mapping[str, str] | None = None,
    fields: bool = True,
) -> str:
    """One line per planned target a finding may be bound to, in plan order.

    The id is what the reply has to copy, so it leads the line; whether the
    run must answer the target is marked beside it, because code exempts a
    required target's findings from the per-sub-topic caps and re-asks for the
    ones left unanswered, so a binding to one matters more than a binding to
    an optional neighbour; the question is what decides the binding, so it
    follows in full, with the sub-topic that owns the target where the caller
    can name it, so the model can bind a statement to a topic by its subject.

    The structured fields the plan set (measure, unit, period, kind,
    organisation) follow where the caller wants them: they describe the
    evidence a target expects, and the section that prints them says so,
    because a field read as a restriction refuses evidence from the body that
    actually carries the answer. ``fields=False`` is for a caller that prints
    the list as the obligations a loop owes rather than as a binding
    vocabulary.
    """
    titles = coverage_titles or {}
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
        coverage = titles.get(target.coverage_id)
        owner = (
            f"{target.coverage_id}: {coverage}"
            if coverage
            else target.coverage_id
        )
        marker = " [required]" if target.required else ""
        line = f"- {target.target_id} [{owner}]{marker}: {target.question}"
        lines.append(
            f"{line} ({details})" if details and fields else line
        )
    return "\n".join(lines)


def render_disputable_statements(findings: Sequence[Finding]) -> str:
    """One line per retained finding a dissent re-ask's passages may dispute.

    ``D1``, ``D2``... are local to this one packet, never the reader-facing
    finding labels a later stage assigns: the model needs something short to
    reason about while it reads, and the target ids beside each line are
    what actually binds a disputing passage's own finding to the right
    obligation.
    """
    lines: list[str] = []
    for index, finding in enumerate(findings, start=1):
        targets = ", ".join(finding.target_ids) or "-"
        lines.append(f"- D{index} [{targets}]: {finding.content}")
    return "\n".join(lines)


# A passage "states a figure in the target's own measure unit" when a numeral
# stands beside a unit of the base that target names. The bases are power (W)
# and energy (Wh), and the unit vocabulary is ``utils.types``' own, so what a
# passage owes and what a target declares cannot drift apart. Bounded: the
# energy-market units only (Fable C-f); a target in any other unit owes no
# passage, so the re-extraction is a no-op outside that domain.
_MEASURE_UNITS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("energy", _ENERGY_UNIT),
    ("power", _POWER_UNIT),
)

# The bases a structured ``unit_dimension`` can name: the units this parser
# scales, and no others. A dimension outside them (``percent``, or an open
# word a later plan uses) is a real obligation but owes no passage a figure.
_MEASURE_BASES = frozenset(base for base, _pattern in _MEASURE_UNITS)

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

    A target asks for a quantity when its structured ``unit_dimension`` names
    one: the field is what the plan sets for a figure the answer has to state,
    so a target carrying no unit dimension asks for no quantity, and no passage
    can owe it one (PD-7).
    """
    return frozenset(
        target.unit_dimension
        for target in targets
        if target.unit_dimension in _MEASURE_BASES
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


def _unbound_required_targets(
    targets: Sequence[EvidenceTarget], findings: Sequence[Finding]
) -> list[EvidenceTarget]:
    """This topic's required obligations no admitted finding answers yet.

    Only the ones with no unit dimension: a figure target asks for a figure,
    and whether a passage owes it one is the unit test's decision above, not
    its own words'. Only this topic's own targets: the read was fetched for
    one sub-topic, and a passage fetched for another is that topic's business.
    """
    bound = {
        target_id for finding in findings for target_id in finding.target_ids
    }
    return [
        target
        for target in targets
        if target.required
        and target.unit_dimension is None
        and target.target_id not in bound
    ]


def _required_targets_unbound_by_read(
    targets: Sequence[EvidenceTarget],
    findings: Sequence[Finding],
    read_id: str,
) -> list[EvidenceTarget]:
    """Required, qualitative targets no finding from ``read_id`` alone binds.

    D7/D10: the per-topic sweep above asks a page about only the reading
    sub-topic's own required targets, answered nowhere in the pass -- so a
    page read for one sub-topic's own targets that also holds a *different*
    sub-topic's required answer is never asked about it. The unit here is
    the one read, not the pass and not the topic: a target another read
    already bound stays out, and a target this exact read has not bound
    yet, whoever planned it, stays in. ``targets`` is meant to be the plan's
    inventory with the reading topic's own already excluded -- those are
    this function's sibling's job -- so the two sweeps never both pay for
    the same target on the same read.
    """
    bound = {
        target_id
        for finding in findings
        if finding.read_id == read_id
        for target_id in finding.target_ids
    }
    return [
        target
        for target in targets
        if target.required
        and target.unit_dimension is None
        and target.target_id not in bound
    ]


def _own_words(target: EvidenceTarget) -> frozenset[str]:
    """The words one target asks in its own voice: the question it states."""
    return frozenset(_tokens(target.question))


_CAPITALIZED_WORD = re.compile(r"[A-Za-z]+")
_SENTENCE_TERMINATOR = re.compile(r"[.!?](?=\s|$)")
_RAIL_CAPITALIZED_RATIO = 0.5
_RAIL_MIN_WORDS = 22
# Question and reporting words no plan's own stop-word list drops (RevY1
# P2): "do", "does", "state" and the rest name the act of asking or
# reporting, never the plan's own subject, so a passage sharing only one
# of them with a required question is no likelier to answer it than a page
# that shares nothing. Kept local to the cross-topic sweep -- the global
# ``_STOP_WORDS`` list (``tools.passage_selection``) is shared with every
# other selector in the codebase, and widening it is out of this sweep's
# blast radius.
_CROSS_TOPIC_GENERIC_WORDS = frozenset(
    {"do", "did", "does", "who", "why", "what", "which", "how", "state", "states"}
)


def _generic_plan_tokens(targets: Sequence[EvidenceTarget]) -> frozenset[str]:
    """The plan's own shared subject words: tokens every target's question repeats.

    A word every planned target's question carries -- the plan's own
    subject -- tells a cross-topic sweep nothing about which target a
    passage answers: a page sharing only that word with a required
    question is no likelier to answer it than a page that shares nothing.
    Excluded here on top of the stop words :func:`_tokens` already drops,
    so what is left of a target's own words is what actually distinguishes
    its question from the plan's other targets'. Fewer than two targets
    share nothing meaningfully, so the intersection is empty rather than
    one target's whole vocabulary. :data:`_CROSS_TOPIC_GENERIC_WORDS` is
    excluded either way, whether the plan carries one target or many.
    """
    questions = [set(_tokens(target.question)) for target in targets]
    shared = set.intersection(*questions) if len(questions) >= 2 else set()
    return frozenset(shared | _CROSS_TOPIC_GENERIC_WORDS)


def _cross_topic_target_words(
    target: EvidenceTarget, generic: frozenset[str]
) -> frozenset[str]:
    """One target's own words for the cross-topic sweep, generic tokens
    stripped -- unless stripping would leave fewer than
    :data:`MIN_CROSS_TOPIC_SHARED_TOKENS`, in which case the target's full
    own words stand instead (RevY1 P2). Two narrow questions on the same
    subject can share most of their own words ("the capacity of Model A",
    "the price of Model A"): stripping every shared word can leave a target
    with only one word of its own, which the floor would then make
    permanently unsweepable. The fallback is logged, not silent: a target
    this thin is worth an operator's look, even though its own words are
    still what a candidate passage is judged against.
    """
    own = _own_words(target)
    stripped = own - generic
    if len(stripped) < MIN_CROSS_TOPIC_SHARED_TOKENS:
        _logger.debug(
            "cross-topic sweep: target %s's non-generic words %s are under "
            "the %d-token floor; using its full own words instead",
            target.target_id,
            sorted(stripped),
            MIN_CROSS_TOPIC_SHARED_TOKENS,
        )
        return own
    return stripped


def _is_chrome_or_bibliography(text: str) -> bool:
    """True when ``text`` reads as a page's own link rail, not prose.

    Not :func:`is_link_dense` (RevY1 P0): that is the passage selector's
    own lede-only heuristic, and it flags any ordinary paragraph that
    merely contains one sentence of 22 words or more among several -- the
    run-6 D2 historiography paragraph and the D11 archaeology caveat both
    read as chrome under it, and so did 32 of the 44 units that actually
    yielded a finding across three real traces. A genuine rail -- a run of
    link labels -- carries no sentence terminator anywhere in it, however
    many words it strings together; an ordinary paragraph breaks into
    sentences long before any one of them runs this long. The rule here
    only ever fires on a passage with no terminator at all, and even then
    only when more than half its words are capitalised, so a long run-on
    clause of ordinary lower-case prose still is not read as one. Dropped
    the year-citation rule entirely (RevY1 P2): a paragraph summarising
    several dated studies is often exactly how a disagreement is stated,
    and the pattern also matched plain parenthetical numbers that name no
    year at all.
    """
    if _SENTENCE_TERMINATOR.search(text):
        return False
    words = _CAPITALIZED_WORD.findall(text)
    if len(words) < _RAIL_MIN_WORDS:
        return False
    capitalized = sum(1 for word in words if word[0].isupper())
    return capitalized / len(words) > _RAIL_CAPITALIZED_RATIO


def _units_owing_cross_topic_words(
    evidence: Mapping[str, EvidenceUnit],
    *,
    read_id: str,
    own_words: Mapping[str, frozenset[str]],
    used: Collection[tuple[str, str]],
) -> list[EvidenceUnit]:
    """One read's own admitted units that plausibly answer an unbound
    cross-topic target's own words (Fable's audit of run 6, Appendix 2).

    Tighter than the topic's own owed re-ask (:func:`_units_owing_own_words`):
    ``own_words`` here has the plan's own generic, shared-subject tokens
    already excluded, so at least :data:`MIN_CROSS_TOPIC_SHARED_TOKENS`
    distinct, specific words of the target's own question -- not the plan's
    repeated subject alone -- must appear in the passage, and a passage
    :func:`_is_chrome_or_bibliography` reads as chrome never counts,
    however many words it repeats. Every unit of the read counts, not only
    the ones the reading sub-topic's own query selected: the read's own
    admission fills its whole budget (whole-page admission), so what that
    sub-topic's own query happened to rank is not the boundary of what its
    read actually holds.
    """
    if not own_words:
        return []
    admitted = set(used)
    owing: list[EvidenceUnit] = []
    for unit in evidence.values():
        if unit.read_id != read_id:
            continue
        if (unit.read_id, unit.locator) in admitted:
            continue
        if _is_chrome_or_bibliography(unit.excerpt):
            continue
        stated = set(_tokens(unit.excerpt))
        if not any(
            len(stated & words) >= MIN_CROSS_TOPIC_SHARED_TOKENS
            for words in own_words.values()
        ):
            continue
        owing.append(unit)
    return owing


# D2's own cues (Fable's audit of run 7): case-insensitive, and deliberately
# broad -- the cue alone never sends a packet, it only makes a passage a
# candidate the token-overlap test below then has to earn. "surveys",
# "excavations" and "archaeolog*" name a discipline that revises an earlier
# account, not a domain: any field's own later fieldwork can revise an
# earlier claim the same way. They are also exactly the words a reference
# list's own titles repeat (RevZ1, run 7 fix wave review, P1), which is
# what :func:`_is_reference_list` exists to keep out of this selector.
_DISSENT_CUE = re.compile(
    r"\b("
    r"however"
    r"|challenged"
    r"|rejected"
    r"|disputed"
    r"|contested"
    r"|no longer(?: widely)? accepted"
    r"|little evidence"
    r"|no (?:good )?evidence"
    r"|overstated"
    r"|exaggerated"
    r"|revised"
    r"|revisionist"
    r"|myth"
    r"|misconception"
    r"|contrary to"
    r"|in fact"
    r"|recent (?:scholarship|research|studies|work)"
    r"|(?:scholars|historians|researchers|experts) (?:now|today|increasingly)"
    r"|surveys?"
    r"|excavations?"
    r"|archaeolog\w*"
    r")\b",
    re.IGNORECASE,
)
MIN_DISSENT_SHARED_TOKENS = 2
# A "(Year)." marker recurring densely is a bibliography entry's own shape
# ("Smith, J. (2001). Title. Journal, 5, 12-30."), whatever else a
# passage's prose contains (RevZ1, run 7 fix wave review, P1): unlike a
# page's link rail, a reference list carries plenty of sentence
# terminators, so :func:`_is_chrome_or_bibliography`'s own no-terminator
# rule never catches it, and ranking by raw cue count let ten such entries
# outrank and crowd out the one genuine dissent passage they outnumbered.
_REFERENCE_ENTRY_MARKER = re.compile(r"\(\d{4}\)\.")
_REFERENCE_LIST_MIN_ENTRIES = 2
_REFERENCE_LIST_MAX_ENTRY_SPAN = 400


def _is_reference_list(text: str) -> bool:
    """True when ``text`` reads as a run of "Author (Year). Title." entries.

    Judged by how densely a "(Year)." marker recurs, not by matching a
    citation format exactly: a passage that cites one work in an aside is
    not a reference list, a run of several close together is.
    """
    markers = _REFERENCE_ENTRY_MARKER.findall(text)
    if len(markers) < _REFERENCE_LIST_MIN_ENTRIES:
        return False
    return len(text) / len(markers) <= _REFERENCE_LIST_MAX_ENTRY_SPAN


# The share of the retained pool's own snippets a word may appear in
# before it stops counting as one of that snippet's own distinctive words,
# and the smallest pool this filter applies to at all (RevZ1, run 7 fix
# wave review, P1). Below the floor, any word two of the pool's few
# snippets happen to share would already clear "more than 20%", which
# would strip a small pool's own snippets down to nothing rather than
# catch the plan-wide repetition the filter exists for.
_POOL_FREQUENT_WORD_SHARE = 0.2
_POOL_FREQUENT_WORD_MIN_POOL = 5


def _pool_frequent_words(
    snippet_words: Sequence[frozenset[str]],
) -> frozenset[str]:
    """Words present in more than :data:`_POOL_FREQUENT_WORD_SHARE` of the
    retained pool's own snippets, or none at all under
    :data:`_POOL_FREQUENT_WORD_MIN_POOL` of them.

    A run's whole finding pool repeats its own subject words across almost
    every snippet the same way a plan's own targets repeat theirs
    (:func:`_generic_plan_tokens`), and without stripping them too, a long
    passage shares two such words with nearly every finding the run has
    ever kept: the measurement behind this fix found 120 of 120 qualifying
    passages passing against a median of 357 of the pool's 567 findings.
    """
    total = len(snippet_words)
    if total < _POOL_FREQUENT_WORD_MIN_POOL:
        return frozenset()
    counts: dict[str, int] = {}
    for words in snippet_words:
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    floor = total * _POOL_FREQUENT_WORD_SHARE
    return frozenset(word for word, count in counts.items() if count > floor)


# A read's own passages are addressed ``chunk-0``, ``chunk-1``... or
# ``page-N-chunk-0``, ``page-N-chunk-1``... (``evidence.passages_from_
# chunks``); the sentence that resolves what a disputing passage disputes
# often sits on the far side of one of these boundaries, not inside the
# passage that carries the cue.
_LOCATOR_CHUNK_INDEX = re.compile(r"^(?:page-(?P<page>\d+)-)?chunk-(?P<index>\d+)$")


def _adjacent_locators(locator: str, available: Collection[str]) -> list[str]:
    """The locator(s) immediately before and after ``locator``, in the
    read's own chunk order, or none for a locator this pattern misses.

    Crosses from a page's own last chunk to the next page's own chunk 0
    when ``available`` holds no further chunk of the same page (RevZ1, run
    7 fix wave review, P1): the sentence that resolves what a disputing
    passage disputes is exactly as likely to sit just past a page break as
    a plain chunk boundary, and the read's own pagination must not hide it.
    """
    match = _LOCATOR_CHUNK_INDEX.match(locator)
    if match is None:
        return []
    page = match.group("page")
    index = int(match.group("index"))
    prefix = f"page-{page}-chunk-" if page is not None else "chunk-"
    neighbours: list[str] = []
    forward = f"{prefix}{index + 1}"
    if forward in available:
        neighbours.append(forward)
    elif page is not None:
        crossed = f"page-{int(page) + 1}-chunk-0"
        if crossed in available:
            neighbours.append(crossed)
    if index > 0:
        neighbours.append(f"{prefix}{index - 1}")
    return neighbours


def _dissent_units_for_read(
    evidence: Mapping[str, EvidenceUnit],
    *,
    read_id: str,
    retained: Sequence[Finding],
    generic: frozenset[str],
    used: Collection[tuple[str, str]],
    max_passages: int = MAX_OWED_PASSAGES_PER_BATCH,
    max_statements: int = MAX_DISPUTED_STATEMENTS,
) -> tuple[list[EvidenceUnit], list[Finding]]:
    """One read's own admitted units that plausibly dispute, qualify or
    date a retained, targeted finding's own snippet (D2, Fable's audit of
    run 7).

    A unit anchors a dissent when it carries a dissent cue, is not a
    passage :func:`_is_chrome_or_bibliography` or :func:`_is_reference_list`
    reads as chrome or a bibliography, and shares at least
    :data:`MIN_DISSENT_SHARED_TOKENS` distinctive tokens with some retained
    finding's snippet -- the plan's own generic words stripped, *and* any
    word :func:`_pool_frequent_words` finds repeated across the retained
    pool itself (RevZ1, run 7 fix wave review, P1): without that second
    strip, a long passage shares two such words with almost every finding
    the run has ever kept, whatever it is actually about. Only a finding
    with its own target ids can be disputed -- an unbound retained finding
    has no obligation a disputing passage could bind to.

    An anchor's own neighbours, in the read's own chunk order, are placed
    immediately after it, whether or not they independently qualify,
    unless they too read as chrome or a bibliography. Anchors are ranked
    by their own distinct dissent cues, capped at one, then by token
    overlap (RevZ1 P1): revised, survey(s), excavation(s) and archaeolog*
    are exactly the words a reference list's own titles repeat, and
    ranking by raw cue count let such entries outrank the dissent they
    outnumbered.

    Returns the qualifying units in that order, capped at
    ``max_passages`` -- the caller's own packet bound -- and the distinct
    retained findings that a unit *in that capped set* disputes, ranked by
    overlap and capped at ``max_statements``: built only from what
    actually reaches the packet, and only that many statements, so a pool
    of hundreds of findings does not turn into a packet of hundreds of
    D-lines (RevZ1 P1).
    """
    admitted = set(used)
    by_locator = {
        unit.locator: unit
        for unit in evidence.values()
        if unit.read_id == read_id
    }
    disputable = [
        finding
        for finding in retained
        if finding.snippet and finding.target_ids
    ]
    snippet_words = [
        set(_tokens(finding.snippet)) - generic for finding in disputable
    ]
    pool_generic = generic | _pool_frequent_words(snippet_words)
    distinctive_words = [words - pool_generic for words in snippet_words]

    def _reads_as_reference_or_chrome(text: str) -> bool:
        return _is_chrome_or_bibliography(text) or _is_reference_list(text)

    anchor_ranks: dict[str, tuple[int, int]] = {}
    anchor_disputes: dict[str, dict[int, int]] = {}
    for locator, unit in by_locator.items():
        if (unit.read_id, unit.locator) in admitted:
            continue
        if _reads_as_reference_or_chrome(unit.excerpt):
            continue
        distinct_cues = {
            match.group(0).lower()
            for match in _DISSENT_CUE.finditer(unit.excerpt)
        }
        if not distinct_cues:
            continue
        stated = set(_tokens(unit.excerpt)) - pool_generic
        overlaps = {
            index: len(stated & words)
            for index, words in enumerate(distinctive_words)
            if len(stated & words) >= MIN_DISSENT_SHARED_TOKENS
        }
        if not overlaps:
            continue
        anchor_ranks[locator] = (
            min(len(distinct_cues), 1),
            max(overlaps.values()),
        )
        anchor_disputes[locator] = overlaps
    if not anchor_ranks:
        return [], []

    ordered_locators: list[str] = []
    seen: set[str] = set()
    for locator in sorted(
        anchor_ranks, key=lambda locator: anchor_ranks[locator], reverse=True
    ):
        if locator in seen:
            continue
        ordered_locators.append(locator)
        seen.add(locator)
        for neighbour_locator in _adjacent_locators(locator, by_locator):
            if neighbour_locator in seen:
                continue
            neighbour = by_locator.get(neighbour_locator)
            if neighbour is None:
                continue
            if (neighbour.read_id, neighbour.locator) in admitted:
                continue
            if _reads_as_reference_or_chrome(neighbour.excerpt):
                continue
            ordered_locators.append(neighbour_locator)
            seen.add(neighbour_locator)

    units = [by_locator[locator] for locator in ordered_locators][:max_passages]
    packet_locators = {unit.locator for unit in units}
    best_overlap: dict[int, int] = {}
    for locator in packet_locators:
        for index, overlap in anchor_disputes.get(locator, {}).items():
            if overlap > best_overlap.get(index, 0):
                best_overlap[index] = overlap
    ranked_indexes = sorted(
        best_overlap, key=lambda index: best_overlap[index], reverse=True
    )[:max_statements]
    disputed = [disputable[index] for index in ranked_indexes]
    return units, disputed


def _units_owing_own_words(
    evidence: Mapping[str, EvidenceUnit],
    *,
    target_ids: Sequence[str],
    targets: Sequence[EvidenceTarget],
    used: Collection[tuple[str, str]],
) -> list[EvidenceUnit]:
    """Selected units that state an unanswered target's own words, unmined.

    A target with no unit of measure carries no figure to look for (PD-7), so
    what a passage states instead is the target's own words: the passage must
    carry at least one word of a required question this topic has not answered
    and no admitted finding used.

    One word, and deliberately not a majority. The obligation a passage
    answers is stated in the passage's own vocabulary — a statutory
    application clause names the article and the date and little else — so
    every word of the target's wording is exactly what such a sentence does
    not repeat; a majority floor would refuse the passage a legal text needs
    and the run would answer "Not found" with its evidence in hand. The floor
    is there to keep an unrelated page out: a read that shares not one word
    with the target owes it nothing, and the bounded re-ask is not spent on
    it. What is left is the only claim this test makes — the passage states
    the target's words — and the extraction still decides whether it answers
    the question.
    """
    if not targets:
        return []
    admitted = set(used)
    own = {target.target_id: _own_words(target) for target in targets}
    owing: list[EvidenceUnit] = []
    for unit in evidence.values():
        if target_ids and not set(target_ids).intersection(unit.target_ids):
            continue
        if (unit.read_id, unit.locator) in admitted:
            continue
        stated = set(_tokens(unit.excerpt))
        if not any(stated.intersection(words) for words in own.values()):
            continue
        owing.append(unit)
    return owing


def _owed_signals(
    unit: EvidenceUnit,
    *,
    bases: Collection[str],
    own_words: Mapping[str, frozenset[str]],
) -> int:
    """How many things one passage states that the targets ask for.

    A numeral in a measure base a target asks for is one, a word of an
    unanswered target's own question is another, and the count decides which
    passages a bounded re-extraction asks about first.
    """
    counted = sum(
        1
        for base, match in _unit_mentions(unit.excerpt)
        if base in bases
        and _TRAILING_NUMERAL.search(unit.excerpt[: match.start()])
    )
    stated = set(_tokens(unit.excerpt))
    return counted + max(
        (len(stated.intersection(words)) for words in own_words.values()),
        default=0,
    )


def _ordered_owed_units(
    units: Sequence[EvidenceUnit],
    *,
    bases: Collection[str],
    own_words: Mapping[str, frozenset[str]],
) -> list[EvidenceUnit]:
    """The owed passages, the ones that owe the most first, each once.

    This order is the re-extraction's focus, because its input is bounded: the
    passages that state the most of what the targets ask for are the ones
    whose packets are sent first, and what the bound cannot hold stays unmined
    rather than filling a packet that cannot carry it. A passage that owes
    both a figure and a target's own words appears in both owed lists and is
    asked about once; ties keep the figure-owed list's order and then the
    word-owed one's, so a packet differs only where its evidence does.
    """
    ordered: dict[str, EvidenceUnit] = {}
    for unit in units:
        ordered.setdefault(unit.evidence_id, unit)
    return sorted(
        ordered.values(),
        key=lambda unit: _owed_signals(
            unit, bases=bases, own_words=own_words
        ),
        reverse=True,
    )


def _owed_batches_by_page(
    units: Sequence[EvidenceUnit],
) -> dict[str, list[list[EvidenceUnit]]]:
    """The bounded packets a re-extraction asks about, in order, per page.

    At most :data:`MAX_OWED_PASSAGES_PER_BATCH` passages per packet and at
    most :data:`MAX_OWED_BATCHES` packets -- **per page**, not shared across
    a sub-topic's pages (user ruling: no strong limits, only runaway guards;
    a shared budget that let one page's owed passages crowd out another
    page's could drop a required target's only remaining chance of an
    answer, which is exactly the kind of content-dropping cap the ruling
    forbids). A packet that carried a whole page's read is what ran the
    extraction away to its output cap, so what one page's own bound cannot
    hold is left unmined and reported rather than sent -- but a second page
    owing its own passages still gets its own two packets regardless.

    A batch never mixes two pages' units (S6): each page's own extraction
    left its own passages owed, so a re-extraction packet stays scoped to
    the one page it retries, exactly like that page's own main extraction
    call. The returned mapping's own key order is the priority order each
    page's own highest-ranked owed unit appears in ``units`` -- the same
    order a single shared queue already used -- so a caller that runs one
    page's batches per page, concurrently, still asks the highest-priority
    page's own passages without waiting on another page's queue position.
    """
    grouped: dict[str, list[EvidenceUnit]] = {}
    for unit in units:
        grouped.setdefault(unit.read_id, []).append(unit)
    return {
        read_id: [
            page_units[start : start + MAX_OWED_PASSAGES_PER_BATCH]
            for start in range(0, len(page_units), MAX_OWED_PASSAGES_PER_BATCH)
        ][:MAX_OWED_BATCHES]
        for read_id, page_units in grouped.items()
    }


def extraction_messages(
    task: SubTopicTask,
    run: ReActRun,
    *,
    evidence_chars: int,
    acquisition_context: str | None = None,
    planned_targets: Sequence[EvidenceTarget] = (),
    owed_passages: bool = False,
    owed_targets: Sequence[EvidenceTarget] = (),
    dissent_passages: bool = False,
    disputed_statements: Sequence[Finding] = (),
    question: str | None = None,
    coverage_titles: Mapping[str, str] | None = None,
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
    or the words of a required obligation the pass has answered nowhere yet,
    so the request says what the packet is for instead of looking like a
    second helping of the evidence the model already declined.

    ``owed_targets`` names those unanswered obligations, in the plan's own
    words. It is what lets the model bind an owed passage to the obligation it
    answers rather than re-reporting it unbound: a passage that states a
    required target's own words is exactly the evidence a binding can be
    made from, and the target's question is the binding instruction.

    ``dissent_passages`` marks the dissent re-ask's own request (D2, Fable's
    audit of run 7). Its packet carries passages that carry a cue of
    disagreement or revision and share words with a statement the run has
    already kept, so the request says the packet is for finding what
    disputes, qualifies or dates that statement, never for restating it.

    ``disputed_statements`` lists those statements, each with the target
    ids a disputing passage should be bound to: what a disputing passage
    overturns is exactly what tells the model which obligation its own
    finding answers.

    ``question`` is the run's original question. The extraction judges what
    bears on the question and what merely sits on the page, and until this
    parameter existed it saw only the sub-topic's title, criteria and target
    questions — never the question they all serve — so a page's own furniture
    could look as relevant as its evidence.

    ``coverage_titles`` maps a coverage id to the sub-topic's title, so a
    planned-target line names the subject of the topic that owns it. A
    statement can then be bound by what it is about ("when the obligations
    apply") rather than by the id alone.
    """
    criteria = "\n".join(
        f"- {criterion}" for criterion in task.sub_topic.success_criteria
    )
    registry_contract = (
        "Return one finding per distinct fact the passages state that bears on "
        "the research question or on any planned target. "
        "Navigation, site furniture, counters, carts, subscription prompts and "
        "legal boilerplate are never findings. Neither is a caption, an "
        "embedded video's or audio player's title, or a parenthetical "
        "condition label a page prints beside a name: "
        "a label reading \"(test conditions)\" states how or where something "
        "was measured, not a judgement of it, and reporting that label as a "
        "verdict is the same error as reporting page furniture. Nor is a "
        "summary or key-takeaways box a page shows beside, above or below "
        "an article, including one the page generates rather than an "
        "author writes; nor an episode or course blurb, a table of "
        "contents, or a reader comment posted below the content. Where "
        "the article's body is among the passages shown and states what "
        "such a box states, the finding is the body's: snippet the "
        "body's sentence, never the box's. A document's own abstract, "
        "executive summary or key-findings section, written by its "
        "author as part of the document's own text, is the page's own "
        "statement, not a box beside it.\n"
        "- Every finding MUST copy read_id and locator exactly as the "
        "# Retrieved evidence section below prints them, and MUST carry a "
        "snippet: one or two sentences copied character for character from "
        "that passage, stating what the finding reports completely — a rule "
        "with its conditions, exceptions and object — and at most "
        f"{MAX_SNIPPET_CHARS} characters. When the statement is longer, split "
        "it across findings at a sentence or clause boundary rather than "
        "cutting inside a clause, and never let a snippet end where the "
        "sentence continues into a condition, an exception or an object it "
        "does not carry. A snippet that reports a judgement MUST carry the "
        "subject that judgement is about: when the passage names that subject "
        "in the sentence next to it, take the neighbouring sentence into the "
        "snippet too, within the character limit above, rather than quoting "
        "the judgement alone.\n"
        "- content is one sentence restating the snippet's fact in the page's "
        "own terms and carrying nothing the snippet does not, except that it "
        "may name a judgement's subject when the passage names it, even where "
        "the snippet itself quotes only the pronoun or demonstrative standing "
        "for that name. It is what tells one finding from another, so two "
        "findings that restate one passage the same way are one finding.\n"
        "- List in figures every measured quantity the snippet states: value "
        "exactly as the snippet writes it, with its qualifier when it has one "
        "(\"nearly 65\", \"up to 30\"), unit as the snippet writes it "
        "(\"kilometres\", \"per cent\", \"euros\", \"days\"), the period it "
        "applies to, kind: forecast for an outcome the page presents as "
        "projected, expected, planned or targeted rather than reported as "
        "having happened — it stays a forecast whatever the run's date; "
        "actual for a quantity the page states "
        "as measured, reported or observed, and for a term a plan, "
        "proposal, law or provision itself sets (the limit, threshold or "
        "amount it imposes), enacted or not, in force yet or not; an "
        "outcome such a text aims at by a later date is a forecast; and "
        "subject: the thing the "
        "figure is about, as the page names it — a model, a place, a body, a "
        "version, a named item — copied from the page, or null when the page "
        "names none and the figure is about the topic as a whole. A date is "
        "not a figure: record it in the snippet and in the date fields below, "
        "never in figures. One snippet may carry several figures with "
        "different subjects; give each its own subject rather than splitting "
        "the snippet. Quantities that measure different things — a monthly "
        "figure and a total for the year — belong in separate findings, each "
        "with the snippet that carries it.\n"
        "- Bind in an ordered step. For each finding, walk the whole Planned "
        "targets list, one target at a time, and name in target_ids every "
        "target whose question its content answers — copy those ids from that "
        "list, never the targets= line of a read, which names only the "
        "sub-topic that fetched it. The measure, unit, period, kind and "
        "organisation on a target line describe the evidence that target "
        "expects; they never restrict which body, unit or period may answer "
        "it.\n"
        "- A finding names a planned target only when its content states the "
        "fact, item, mechanism or provision the target asks for, not when it "
        "merely concerns the topic.\n"
        "- A passage that disputes, qualifies or dates a reason, mechanism, "
        "provision or figure a target asks about is also a finding for that "
        "target: snippet the sentence that disputes, qualifies or dates it. "
        "For example, a page stating \"Later field surveys found the "
        "flooding played little part in the decline the early accounts "
        "blamed on it\" is a finding for a target asking what caused the "
        "decline.\n"
        "- A target id that is not in that list is dropped from the finding, "
        "and a finding with no planned target left is kept but can then be "
        "attributed only through the sub-topic that fetched its read.\n"
        "- A finding whose snippet the locator does not contain is dropped.\n"
        "- Copy source_url and source_title from the same read record, never "
        "from memory, a search snippet, or another finding's text; never "
        "substitute the URL or title the document names as its origin — a "
        "copy served from another host is cited where you read it, and the "
        "body it came from belongs in attributed_issuer — and never rewrite "
        "the read's title, not to drop the reader's own markers and not to "
        "put the document's own headline in their place.\n"
        "- Return an empty list when the evidence supports nothing."
        if acquisition_context is not None
        else "Return one finding per distinct, source-backed claim. Use the "
        "exact source_url and source_title from the evidence below. Return "
        "an empty list when the evidence supports nothing."
    )
    sections = [
        f"# Sub-topic\n{task.sub_topic.title}",
        f"# Success criteria\n{criteria}",
    ]
    if question:
        sections.insert(0, f"# Research question\n{question}")
    if planned_targets:
        sections.append(
            "# Planned targets\n"
            "Every planned target of this run, in plan order, marked required "
            "when the run must answer it. The question decides the binding: "
            "the fields on a line describe the evidence that target expects "
            "and never restrict which body, unit or period may answer it. A "
            "finding whose content answers one of these questions names it in "
            "target_ids, whichever sub-topic the read was fetched for:\n"
            + render_planned_targets(
                planned_targets, coverage_titles=coverage_titles
            )
        )
    if owed_passages:
        owed_lines = [
            "# Passages owed a finding",
            "The passages below are selected evidence a previous extraction "
            "returned no finding for. They are candidates, not answers: each "
            "shares a word with a required question this page has not "
            "answered, or states a number in a power or energy unit a "
            "planned target asks for.",
        ]
        if owed_targets:
            owed_lines.append(
                "The required questions this page has not answered are:"
            )
            owed_lines.append(
                render_planned_targets(
                    owed_targets, coverage_titles=coverage_titles
                )
            )
        owed_lines.append(
            "Return an empty list when none of these passages states what a "
            "planned target asks for. Mine every passage that does for every "
            "planned target whose question its content answers, in the same "
            "registry shape and with the same target ids the contract above "
            "requires."
        )
        sections.append("\n".join(owed_lines))
    if dissent_passages:
        dissent_lines = [
            "# Statements these passages may dispute",
            "Some passages below carry a cue of disagreement or revision "
            "and share words with a statement the run has already kept. "
            "They are candidates, not answers: only a passage that "
            "actually disputes, qualifies or dates a step, cause, figure "
            "or provision one of these statements gives is a finding in "
            "this request. A passage that restates one of these "
            "statements, or states a fact that disputes, qualifies or "
            "dates none of them, yields no finding here, even where the "
            "contract above would count it as a fact bearing on a "
            "target.",
        ]
        if disputed_statements:
            dissent_lines.append(
                "The statements these passages may dispute are:"
            )
            dissent_lines.append(
                render_disputable_statements(disputed_statements)
            )
        dissent_lines.append(
            "Return one finding per passage that disputes, qualifies or "
            "dates one of these statements: snippet the disputing "
            "sentence, and put in target_ids only that statement's own "
            "ids, copied from its D-line, and no other target, even "
            "where the contract above would bind more -- a finding bound "
            "to any other target is dropped. Everything else keeps the "
            "registry shape the contract above requires. Return an empty "
            "list when none of these passages does."
        )
        sections.append("\n".join(dissent_lines))
    sections.append(
        (
            "# Retrieved evidence\n"
            + (
                acquisition_context
                if acquisition_context is not None
                else render_evidence(
                    run, limit=evidence_chars, discovery_payloads=False
                )
            )
        )
    )
    static = [
        f"# Response contract\n{registry_contract}{_FINDING_DATES_CONTRACT}"
        f"{_FINDING_PROVENANCE_CONTRACT}",
        "# Reply format\n" + render_structured_reply_format(_FINDING_REPLY_EXAMPLES),
    ]
    return [
        ChatMessage(role="developer", content=EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content=render_structured_request(static, sections)),
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
    # Three admissible windows, each the page's own words about this excerpt:
    # the excerpt's passage, its immediate neighbour, and the page's own title
    # or heading, where an instrument or a body is named rather than described.
    # The title is read because a reproduced document is often attributed by
    # the card, heading or masthead that introduces it — a label naming the
    # instrument — and a rule that read only the two passages left such a page
    # crediting the host that served it (review RES-6 §3).
    windows = [neighbouring_passage_text(read, locator or ""), read.title]
    if not any(excerpt_matches(window, phrase) for window in windows):
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


def _admitted_stated_date(read: ReadRecord | None, value: object) -> str | None:
    """A date the read states, or ``None``.

    Verified the way :mod:`evidence` verifies every other date this project
    records: the read's own text has to state this value, at this precision
    or a finer one, as a date rather than as a fragment of an identifier. A
    value the page never wrote is dropped rather than printed as a fact about
    the page — as the attributed body's own release day, or as when the source
    said so.

    Both dates a finding carries are admitted here: ``release_date``, and
    ``statement_date``, which is published as the finding's own statement date
    and is the Evidence Verifier's last-resort basis for resolving a relative
    period. That reader labels the basis a page date, so a model's guess
    standing in for one was published as a page's own statement (live probe).
    """
    if read is None:
        return None
    date = value.strip() if isinstance(value, str) else ""
    if not date:
        return None
    document = " ".join([read.title, *read.passages.values()])
    return date if _quote_states(document, (date,)) else None


def _admitted_period(value: object) -> str | None:
    """The period a finding may record, or ``None`` when it only dates it relatively.

    A period is comparable only when it carries the time it covers — a year, a
    quarter, a half, a month, a day, and every one of those is written with a
    number. A phrase that carries none ("this year", "the most recent quarter")
    states no period: it dates the figure *against the page*, and the page's
    own date is what resolves it.

    Recording such a phrase as the period is what made the Evidence Verifier
    refuse the Context Check's resolved year as ``correction_not_on_page``:
    code reads a recorded period as one the page's words state themselves, so
    it never resolves it relatively, and the figure is dropped although the
    page dates it and the date is known (live probe: "this year" recorded, 2026
    proposed, dropped). The phrase itself is not lost — the excerpt is the
    page's own words, and the verifier resolves a relative phrase from the
    page's date and records which date it came from (D11) — so a relative
    phrase is recorded through that path and never as an explicit period.
    """
    period = value.strip() if isinstance(value, str) else ""
    if not period:
        return None
    return period if any(character.isdigit() for character in period) else None


def _snippet_admitted_at(read: ReadRecord, locator: str, snippet: str) -> str | None:
    """The locator ``snippet`` is admitted at, or ``None`` when it is not the read's own words.

    Admission reads the whole page, never merely ``locator``'s own passage or
    a fixed neighbour either side of it: :func:`evidence.locate_snippet`
    finds where ``snippet`` runs, verbatim and contiguous, in the read's own
    text. That position -- not the model's own claim -- is what every later
    stage reads the finding's context from, so ``locator`` only breaks a tie
    when the same words genuinely appear more than once on the page. A
    paraphrase, or a snippet stitched from two spans the page never runs
    together, still finds nowhere to admit.
    """
    return locate_snippet(read, snippet, claimed_locator=locator)


# RES-4's snippet rule: a verdict must carry the thing it judges. A bare
# pronoun or demonstrative ("this", "that", "these", "those", "it", "they")
# standing alone as a clause's whole subject, immediately before a linking
# verb, states a judgement about something the snippet never names — the
# audited run reported "..., this is the model to beat." with no way for
# anything downstream to say what "this" was. The trigger fires at a clause
# boundary as well as a sentence start, because that audited sentence put its
# judgement after a comma, not at the sentence's own head. "They" is
# restricted to true linking verbs ("have"/"has"/"had" state possession, not
# a verdict), and "It" followed by an impersonal "is/was/has been ... that/to"
# construction ("It is estimated that...") is never a judgement about "it" at
# all, so neither ever reaches the referent check below.
_BARE_JUDGEMENT_SUBJECT = re.compile(
    r"(?:\A|(?<=[.!?,;:]))\s*("
    r"(?:This|That|These|Those|It)\s+(?:is|are|was|were|has|have|had|"
    r"remains?|stays?|becomes?|seems?|looks?|sounds?)"
    r"|They\s+(?:is|are|was|were|remains?|stays?|becomes?|seems?|looks?|"
    r"sounds?)"
    r")\b",
    re.IGNORECASE,
)
# "It is estimated that...", "It was found that...", "It has been shown
# that...": a passive report of somebody else's finding, not a judgement
# about the pronoun "it". Two tokens ahead is enough to tell that shape from
# a genuine verdict ("It is the model to beat" never reaches a bare "that" or
# "to" at that distance).
_IMPERSONAL_IT = re.compile(
    r"It\s+(?:is|was|has been)\s+\S+\s+(?:that|to)\b", re.IGNORECASE
)
# Closed-class words that carry no identity of their own: excluded from
# referent detection wherever they sit, capitalised or not.
_SENTENCE_STARTER_WORDS = frozenset(
    {
        "this", "that", "these", "those", "it", "they", "the", "a", "an",
        "in", "on", "at", "for", "with", "and", "but", "or", "so", "as",
        "by", "of", "to", "from", "its", "their", "his", "her",
    }
)
_ASCII_UPPERCASE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_WORD_PUNCTUATION = "\"'\u201c\u201d\u2018\u2019.,;:!?()[]"
# A sentence boundary for the referent search: "." "!" "?" followed by
# whitespace. Independent of the judgement trigger above, which also fires at
# a clause boundary (comma, semicolon, colon) — a referent one clause over in
# the *same* sentence is still inside "the pronoun's own sentence" and does
# not count, only an earlier sentence does.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of every sentence in ``text``, in order."""
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in _SENTENCE_BOUNDARY.finditer(text):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))
    return spans


def _sentence_start_at(text: str, position: int) -> int:
    """The start offset of the sentence that contains ``position``."""
    start = 0
    for span_start, _span_end in _sentence_spans(text):
        if span_start > position:
            break
        start = span_start
    return start


def _is_referent_word(word: str, *, sentence_initial: bool) -> bool:
    """True when ``word`` names something, whatever the pronoun stands for.

    An internal capital ("iPhone") or a leading capital outside plain ASCII
    ("\u0160koda") always counts, because neither is explained by English's
    own rule of capitalising a sentence's first word. A plain ASCII leading
    capital ("Sony", but also "Prices") counts only when it is *not* that
    first word: sentence-initial position alone never distinguishes a name
    from an ordinary word that simply opens a sentence.
    """
    stripped = word.strip(_WORD_PUNCTUATION)
    if not stripped or stripped.lower() in _SENTENCE_STARTER_WORDS:
        return False
    if any(character.isupper() for character in stripped[1:]):
        return True
    first = stripped[0]
    if not first.isupper():
        return False
    if first not in _ASCII_UPPERCASE:
        return True
    return not sentence_initial


# F5 (controller decision, ReRevResearcherR3 final round): unlike the
# snippet, content's own sentence-initial word usually *is* the subject
# ("Sony is the model to beat."), so it is excluded only when it names
# nothing on its own: a closed-class word, this specific introductory
# word ("According", followed by "to" rather than a comma), or a word a
# comma sets off as a sentence adverb or introductory phrase ("Overall,",
# "However,", "Meanwhile,").
_CONTENT_INTRODUCTORY_WORDS = frozenset({"according"})


def _content_names_a_referent(content: str) -> bool:
    """True when ``content`` itself names the judgement's subject.

    A sentence's first word counts as a referent unless it is a
    closed-class word, a known introductory word, or immediately followed
    by a comma; every other word counts exactly as the snippet's own does:
    an internal capital or a leading capital outside plain ASCII always
    names something, and a plain ASCII leading capital counts everywhere
    but that first, ambiguous position.
    """
    for start, end in _sentence_spans(content):
        for index, word in enumerate(content[start:end].split()):
            stripped = word.strip(_WORD_PUNCTUATION)
            if not stripped or stripped.lower() in _SENTENCE_STARTER_WORDS:
                continue
            if any(character.isupper() for character in stripped[1:]):
                return True
            first = stripped[0]
            if not first.isupper():
                continue
            if first not in _ASCII_UPPERCASE:
                return True
            if index == 0 and (
                stripped.lower() in _CONTENT_INTRODUCTORY_WORDS
                or word.endswith(",")
            ):
                continue
            return True
    return False


def _snippet_names_a_referent_before(snippet: str, sentence_start: int) -> bool:
    """True when a sentence of ``snippet`` before ``sentence_start`` names one."""
    for start, end in _sentence_spans(snippet):
        if start >= sentence_start:
            break
        for index, word in enumerate(snippet[start:end].split()):
            if _is_referent_word(word, sentence_initial=index == 0):
                return True
    return False


def _bare_pronoun_judgement(snippet: str, content: str) -> bool:
    """True when ``snippet`` states a judgement with no named subject.

    Triggered only by a clause whose whole subject is a bare pronoun or
    demonstrative (``_BARE_JUDGEMENT_SUBJECT``), minus the impersonal "It ...
    that/to" shape and, for "They", every verb but a true linking one; a
    snippet that never makes that shape of claim is never refused here,
    whatever else it says. Once triggered, the finding is refused unless a
    referent is named either by an earlier sentence of the snippet or by the
    finding's own ``content`` — because RES-4 asks the model to take the
    neighbouring sentence into the snippet, or to name the referent in
    content, when the passage carries it.
    """
    if _content_names_a_referent(content):
        return False
    for match in _BARE_JUDGEMENT_SUBJECT.finditer(snippet):
        position = match.start(1)
        if _IMPERSONAL_IT.match(snippet, position):
            continue
        sentence_start = _sentence_start_at(snippet, position)
        if _snippet_names_a_referent_before(snippet, sentence_start):
            continue
        return True
    return False


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
        locator = item.locator
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
            admitted_locator = _snippet_admitted_at(read, item.locator, item.snippet)
            if admitted_locator is None:
                rejected.append(
                    f"finding {index}: snippet was not admitted at locator"
                )
                continue
            locator = admitted_locator
            if _bare_pronoun_judgement(item.snippet, item.content):
                rejected.append(
                    f"finding {index}: snippet's subject is a bare pronoun "
                    "with no referent"
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
                locator=locator,
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
                    data_period=_admitted_period(item.data_period),
                    statement_date=_admitted_stated_date(read, item.statement_date),
                    vintage=item.vintage,
                    attributed_issuer=attributed_issuer,
                    attribution_quote=attribution_quote,
                    measure_scope=_admitted_measure_scope(read, item.measure_scope),
                    release_date=_admitted_stated_date(read, item.release_date),
                    snippet=item.snippet if read is not None else None,
                    read_id=item.read_id if read is not None else None,
                    locator=locator if read is not None else None,
                    figures=figures,
                )
            )
        except ValidationError as error:
            rejected.append(f"finding {index}: invalid {_invalid_fields(error)}")
            continue
        if (
            admitted_evidence_keys is not None
            and item.read_id is not None
            and locator
        ):
            admitted_evidence_keys.append((item.read_id, locator))
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

    A calendar date is not a measure, so it is refused the same way here
    rather than admitted into a slot only a quantity fits (improvement 9's
    researcher half): a figure whose value or unit is date-shaped
    (``figures.is_a_date``) is dropped from ``figures`` alone, and the date
    itself stays where it is evidence — in the excerpt the finding rests on,
    and in its statement date and data period.
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
        if is_a_date(value, unit):
            dropped.append(
                f"finding {index}: figure {position} states a date, not a "
                "measure"
            )
            continue
        kind = (draft.kind or "").strip().casefold()
        figures.append(
            FindingFigure(
                value=value,
                unit=unit,
                period=_admitted_period(draft.period),
                kind=_normalized_figure_kind(kind),
                subject=(draft.subject or "").strip() or None,
            )
        )
    return figures, dropped


def extra_pass_unfunded_error(
    targets: Sequence[str],
    sub_topics: Sequence[SubTopic],
) -> ResearchError:
    """Record that an extra pass was not opened because it could call nothing.

    ``details`` carries the pass's own job list and the sub-topics that own it,
    so nothing the pass was bought for is hidden: a reader sees that the pass
    was refused, which targets it existed for, and why.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_extra_pass_unfunded",
        message=(
            "The extra pass was not opened: every sub-topic that owns a "
            "missing required target has spent its acquisition budget and owes "
            "no extraction, so no tool call and no extraction was possible; "
            "those targets stay unanswered."
        ),
        recoverable=True,
        details={
            "targets": list(targets),
            "sub_topics": [sub_topic.coverage_id for sub_topic in sub_topics],
            "reason": "acquisition_budget_spent",
        },
    )


def _extra_pass_has_nothing_to_do(
    state: ResearchState,
    selected: Sequence[SubTopic],
) -> bool:
    """Whether an extra pass's whole job list has no call and no extraction left.

    ``remaining_calls`` is the run's, not one pass's: ``merge_acquisition_states``
    keeps the minimum and the state carries it, so a sub-topic whose earlier
    pass used its whole budget resumes at zero and the policy refuses every call
    ("the acquisition budget of 'X' is spent"). The loop would still spend its
    model turns being refused, which is neither evidence nor a saving — so a
    pass that can neither call nor extract is not opened.

    Owed extraction is the exemption: reads a deferred pass left in
    ``pending_extraction_ids`` can be mined with no tool call, so a spent budget
    alone is not "nothing to do".

    A pass that is not an extra pass is never skipped: the guard reads the
    pass's own job list, and ``extra_pass_target_ids`` is empty on the first
    pass and replaced by the graph at every extra one.
    """
    if not state.extra_pass_target_ids or not selected:
        return False
    recorded = state.acquisition_state_by_target
    return all(
        (item := recorded.get(sub_topic.coverage_id)) is not None
        and item.remaining_calls <= 0
        and not item.pending_extraction_ids
        for sub_topic in selected
    )


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
    required_target_ids: Collection[str] = (),
    own_target_ids: Collection[str] = (),
    max_findings: int = MAX_FINDINGS_PER_SUB_TOPIC,
    max_sources: int = MAX_UNIQUE_SOURCES_PER_SUB_TOPIC,
    max_exempt_per_target: int = MAX_EXEMPT_PER_REQUIRED_TARGET,
    max_dispute_exempt_per_target: int = MAX_DISPUTE_EXEMPT_PER_TARGET,
) -> BoundedFindings:
    """Fold restatements, then bound one sub-topic's kept evidence.

    The cap bounds *useful evidence*, never planned coverage: every planned
    sub-topic still gets its turn, and what a sub-topic could not keep is
    reported rather than silently discarded.

    A finding bound to a required target is exempt from both caps: it is the
    answer the run was sent to get, not corroborating volume, and a
    confidence ranking over a page's findings can otherwise drop it — the
    audited run lost two required answers to a per-sub-topic cap while a
    menu's own paragraphs kept their slots. Exempt findings are kept in
    addition to the capped set rather than counted inside it, so an answer
    never spends a slot another finding needed.

    The exemption is a guarantee, not a bypass: at most
    ``max_exempt_per_target`` findings per required target escape the caps,
    the most confident first, and every other finding bound to that target is
    evidence like any other and ranked by the caps. Without that ceiling an
    extraction that binds its whole output to a required target keeps all of
    it — twenty-five restatements of one obligation, and a per-sub-topic cap
    that bounds nothing.

    A dispute finding (``Finding.disputes``, D2, Fable's audit of run 7) is
    exempt the same way, grouped by the target ids it is bound to rather
    than by required-target membership: at most
    ``max_dispute_exempt_per_target`` dispute findings per shared target id
    escape the caps, the most confident first, since the dissent re-ask
    exists to put a page's own rejection of a step into the report and a
    confidence ranking over a page's findings can drop it exactly as
    readily as it drops a required target's own answer.

    Selection is not by confidence alone. Findings are grouped by
    *publisher* — not by URL — ranked by their strongest finding and then
    taken round-robin, so one verbose publisher cannot fill the whole
    allowance and push an independent second source out of the report.
    Within a publisher a finding bound to this sub-topic's own required
    obligation goes first. One bound to *any* required obligation — this
    sub-topic's own or another's, which is the shape a cross-sub-topic
    sweep's finding takes — ranks next, ahead of a finding bound only to
    this sub-topic's own non-required obligation: a required answer must
    never be the first thing the ordinary cap drops merely for arriving on
    a read the model scored less directly. A finding bound to neither ranks
    last, and confidence orders the rest and breaks every tie; duplicates
    keep their highest confidence, and the retained list comes back ranked
    the same way. ``own_target_ids`` names this sub-topic's own evidence
    targets, required or not; ``required_target_ids`` names every required
    target the plan carries, whichever sub-topic owns it.

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
    if (
        max_findings < 1
        or max_sources < 1
        or max_exempt_per_target < 1
        or max_dispute_exempt_per_target < 1
    ):
        raise ValueError(
            "max_findings, max_sources, max_exempt_per_target and "
            "max_dispute_exempt_per_target must be at least 1"
        )

    deduplicated = deduplicate_findings(findings)
    required = set(required_target_ids)
    exempt_indexes: set[int] = set()
    for target_id in required:
        bound_indexes = [
            index
            for index, finding in enumerate(deduplicated)
            if target_id in finding.target_ids
        ]
        bound_indexes.sort(
            key=lambda index: deduplicated[index].confidence, reverse=True
        )
        exempt_indexes.update(bound_indexes[:max_exempt_per_target])
    dispute_target_ids = {
        target_id
        for index, finding in enumerate(deduplicated)
        if finding.disputes
        for target_id in finding.target_ids
    }
    for target_id in dispute_target_ids:
        dispute_bound_indexes = [
            index
            for index, finding in enumerate(deduplicated)
            if finding.disputes and target_id in finding.target_ids
        ]
        dispute_bound_indexes.sort(
            key=lambda index: deduplicated[index].confidence, reverse=True
        )
        exempt_indexes.update(
            dispute_bound_indexes[:max_dispute_exempt_per_target]
        )
    exempt = [
        finding
        for index, finding in enumerate(deduplicated)
        if index in exempt_indexes
    ]
    bounded_pool = [
        finding
        for index, finding in enumerate(deduplicated)
        if index not in exempt_indexes
    ]
    own = set(own_target_ids)

    def _cap_rank(finding: Finding) -> tuple[bool, bool, bool, float]:
        """How ``finding`` ranks for the ordinary, non-exempt cap.

        A finding bound to *any* required obligation outranks one that is
        not, whatever the model's confidence score says and whichever
        sub-topic owns the obligation: the cross-sub-topic sweep exists to
        bind a required target from a read another sub-topic made, and a
        rank that put this sub-topic's own optional row ahead of it would
        make the sweep's own answer the first thing the ordinary cap drops.
        Within that tier, one bound to this sub-topic's own obligation --
        required or not -- outranks one that is not, so a page's own
        paragraphs still do not lose their slot to another sub-topic's row
        merely because the model scored it more directly. Confidence orders
        the rest and breaks every tie.
        """
        bound = set(finding.target_ids)
        bound_to_own = bool(bound & own)
        bound_to_required = bool(bound & required)
        return (
            bound_to_own and bound_to_required,
            bound_to_required,
            bound_to_own,
            finding.confidence,
        )

    groups: dict[str, list[Finding]] = {}
    for finding in bounded_pool:
        groups.setdefault(
            publisher_identity(finding.source_url), []
        ).append(finding)

    by_source = sorted(
        groups.values(),
        key=lambda group: max(_cap_rank(finding) for finding in group),
        reverse=True,
    )[:max_sources]
    for group in by_source:
        group.sort(key=_cap_rank, reverse=True)

    retained: list[Finding] = list(exempt)
    depth = 0
    while len(retained) < len(exempt) + max_findings and any(
        len(group) > depth for group in by_source
    ):
        for group in by_source:
            if depth >= len(group):
                continue
            retained.append(group[depth])
            if len(retained) == len(exempt) + max_findings:
                break
        depth += 1

    retained.sort(key=_cap_rank, reverse=True)
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
    elapsed_s: float = 0.0,
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

    ``elapsed_s`` is this loop's own wall seconds, rounded to a tenth. With
    several loops in flight the CLI prints every sub-topic event at node
    completion, where the log's own timestamps can no longer separate them;
    this is the number that survives.

    ``target_obligation_completed`` is the Task 3 signal that the active
    target's obligation advanced: at least one registry-admitted finding was
    extracted for it. Whether the reader's report answers the target is judged
    later, on the report's own statements, and is not claimed here.
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
            "elapsed_s": elapsed_s,
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
    ``sub_topics_skipped`` is how many of the pass's own selection were never
    attempted — dropped by the ``max_sub_topics`` cap, or left unstarted when
    a non-recoverable provider failure stopped the pass early. Together with
    ``sub_topics_researched`` this makes "was every sub-topic this pass set
    out to run accounted for" answerable from the event stream alone, without
    cross-referencing ``state.errors``. On an extra pass the selection is
    ``state.extra_pass_target_ids``' own topics, so ``planned`` counts the
    whole plan while ``researched`` counts the pass.
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
    """Warn that a selected sub-topic was never attempted at all.

    ``reason`` is one of two enumerated strings, never raw exception text:
    ``"cap"`` when ``max_sub_topics`` truncated the pass's selection before
    this sub-topic's turn came up, or ``"provider_failure_stopped_processing"``
    when an earlier sub-topic's non-recoverable provider failure stopped the
    pass before this sub-topic could run. Recoverable: the rest of the report
    can still stand, just incomplete for this sub-topic.

    Every unattempted sub-topic gets one of these, whatever its priority:
    the record carries the sub-topic's ``coverage_id`` so the sub-topics a
    pass did not reach are named rather than inferred from a count. A
    sub-topic the pass was never sent for gets no record: an extra pass is
    confined to the targets still missing an answer, and leaving a topic out
    of that job list is not a coverage gap.
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


ExtractionFailure: TypeAlias = Literal["", "provider", "output_limit"]
"""Why one sub-topic's extraction call produced no findings, or ``""`` for none.

``"provider"`` is a call that could not reach the provider: the pass stops
researching further sub-topics, the way a ReAct-loop-level ``provider_error``
does, and every sub-topic still queued is recorded as skipped. ``"output_limit"``
is a call that reached the provider and came back truncated by its output cap:
that is a fact about one sub-topic's extraction, not about the provider, so the
pass carries on and a truncation cannot cost the other sub-topics their
research (the class of failure the planner's own review calls meet).
"""


def extraction_output_limit_error(
    run: ReActRun,
    error: Exception,
) -> ResearchError:
    """Record that a sub-topic's extraction reply was too long to complete.

    Recoverable, unlike an extraction that could not reach the provider: the
    provider answered, and an answer cut off at the output cap is a fact about
    this one extraction rather than about the run's transport. The sub-topic's
    reads stay owed — the caller defers the batch rather than consuming it — so
    the passages are still visibly unmined and a later pass can extract them.
    ``details`` carries the static operation, the safe provider snapshot and
    counts, never ``str(error)`` — the same redaction discipline the rest of
    this module follows.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_extraction_output_limit",
        message=(
            "The model's reply for a sub-topic's findings hit the output "
            "limit; the reads stay owed and the research pass continued."
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            "researcher_finding_extraction",
            error,
            iterations=run.iterations,
            tool_calls=run.tool_calls,
        ),
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


@dataclass(frozen=True, slots=True)
class _PageExtraction:
    """One page's own extraction result (S6: per-page parallel extraction).

    ``error`` is set instead of ``findings`` on a provider failure for this
    one page's own call -- the read's own record, never the whole
    sub-topic's: another page's task is not cancelled and its own findings
    still merge in. ``failed_provider`` distinguishes an unreachable
    provider (this page's whole call never got a reply) from an output-limit
    truncation (the provider replied, cut off at its own cap); both keep the
    other pages' findings, and neither invents a finding this page's call
    never made.
    """

    read_id: str
    findings: list[Finding] = field(default_factory=list)
    admitted_keys: list[tuple[str, str]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    unplanned_target_ids: list[str] = field(default_factory=list)
    dropped_figures: list[str] = field(default_factory=list)
    error: ResearchError | None = None
    failed_provider: bool = False
    elapsed_s: float = 0.0


class _LoopWithExtraction(NamedTuple):
    """One sub-topic's finished ReAct loop, and its own per-page extraction
    tasks (S6): every read the loop admitted started its own background
    extraction call the moment it was admitted, and this is the fixed,
    read-admission order the caller merges them in.
    """

    react: ReActRun
    extraction_tasks: dict[str, "asyncio.Task[_PageExtraction]"]
    admitted_read_order: list[str]
    extraction_gate: asyncio.Semaphore


@dataclass(frozen=True, slots=True)
class _OwedPageResult:
    """One page's own bounded owed re-extraction (S6).

    Each owing page gets its own up-to-``MAX_OWED_BATCHES`` batches, and
    every owing page's own batches run concurrently under the same
    ``extraction_concurrency`` gate that page's own main call ran under --
    a shared budget across a sub-topic's pages would let one page's owed
    passages crowd out another's, which is exactly the content-dropping cap
    the user's "no strong limits" ruling forbids. ``findings``/``rejected``/
    ``admitted_keys``/``unplanned_target_ids``/``dropped_figures`` are this
    page's own local accumulators, merged into the sub-topic's shared ones
    only after every owing page's task has returned, in a fixed page order
    -- never completion order -- for the same determinism reason the main
    per-page merge keeps one.
    """

    read_id: str
    findings: list[Finding] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    errors: list[ResearchError] = field(default_factory=list)
    admitted_keys: list[tuple[str, str]] = field(default_factory=list)
    unplanned_target_ids: list[str] = field(default_factory=list)
    dropped_figures: list[str] = field(default_factory=list)
    asked_evidence_ids: list[str] = field(default_factory=list)



@dataclass(frozen=True, slots=True)
class _SubTopicOutcome:
    """One sub-topic loop's own results, folded in plan order by ``run``.

    Everything here belongs to exactly one loop. With several loops in flight
    (D9) there is no run-level "active" acquisition, target or counter to keep
    on the agent — a field would be a race waiting to be written — so the
    loop's own stop reason, findings, errors, events, counters and acquisition
    snapshot travel back with the loop that produced them.
    """

    react: ReActRun
    findings: list[Finding]
    errors: list[ResearchError]
    events: list[ResearchEvent]
    target_id: str | None
    target_state: AcquisitionState | None


async def _cancel_and_gather_pages(
    page_extractions: Mapping[str, "asyncio.Task[_PageExtraction]"],
) -> None:
    """Cancel every per-page task not already done, then await all of them
    (S6, RevSelectionR3 P1).

    Called on every path that stops waiting for a sub-topic's background
    extraction tasks without having awaited each one to completion: a loop
    that failed, a merge that raised, or a caller that is giving up on the
    pass for some other reason. Without this, a task already scheduled keeps
    calling the provider after the pass has halted, its result is silently
    discarded, and a non-``ProviderError`` it later raises can only surface
    as an unretrieved task exception -- never as anything this run reports.
    ``return_exceptions=True`` is what makes a cancelled task's own
    ``CancelledError`` (and any other exception a task ends with) safe to
    await here instead of propagating out of this cleanup itself.
    """
    for page_task in page_extractions.values():
        if not page_task.done():
            page_task.cancel()
    if page_extractions:
        await asyncio.gather(*page_extractions.values(), return_exceptions=True)



class ResearcherAgent(BaseAgent[ResearchFindings]):
    """Run one bounded ReAct loop per selected sub-topic and extract findings.

    ``run`` is overridden because the spec requires a loop *per sub-topic*,
    which the single-loop ``BaseAgent.run`` cannot express. Everything below
    ``run`` — bounds, tracing, tool execution, scratchpad writes — is still
    the shared runtime's.
    """

    name = RESEARCHER_NAME
    description = "Gather source-backed findings for planned sub-topics."
    # The prompt-fix wave changed this agent's prompt contract: the loop is
    # told the rules the policy enforces and stops on the sub-topic's
    # obligations, and the extraction binds in an ordered step with its date,
    # provenance and completeness rules restated. An artifact therefore says
    # which researcher instructions produced it.
    prompt_version = "researcher-2"
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
        read_admission_chars: int | None = None,
        evidence_packet_chars: int | None = None,
        sub_topic_concurrency: int | None = None,
        decision_context_chars: int | None = None,
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
        admission_limit = (
            self.config.read_admission_chars
            if read_admission_chars is None
            else read_admission_chars
        )
        packet_limit = (
            self.config.evidence_packet_chars
            if evidence_packet_chars is None
            else evidence_packet_chars
        )
        decision_limit = (
            self.config.decision_context_chars
            if decision_context_chars is None
            else decision_context_chars
        )
        if admission_limit < 1:
            raise ValueError("read_admission_chars must be at least 1")
        if packet_limit < 1:
            raise ValueError("evidence_packet_chars must be at least 1")
        if decision_limit < 1:
            raise ValueError("decision_context_chars must be at least 1")
        resolved_concurrency = (
            self.config.sub_topic_concurrency
            if sub_topic_concurrency is None
            else sub_topic_concurrency
        )
        if resolved_concurrency < 1:
            raise ValueError("sub_topic_concurrency must be at least 1")
        probe = clock()
        if probe.tzinfo is None or probe.utcoffset() is None:
            raise AgentConfigurationError(
                "ResearcherAgent clock must return a timezone-aware "
                "datetime; got a naive datetime instead"
            )
        self._max_sub_topics = max_sub_topics
        self._high_priority_threshold = high_priority_threshold
        self._evidence_chars = evidence_chars
        self._read_admission_chars = admission_limit
        self._evidence_packet_chars = packet_limit
        self._decision_context_chars = decision_limit
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
        self._shared_cache = cache if cache is not None else {}
        self._shared_network_read_ids = (
            network_read_ids if network_read_ids is not None else set()
        )
        # How many sub-topic loops may run at once (PD-27). Every other loop
        # value is a local of the loop that owns it: with several loops in
        # flight there is no single "active" acquisition, target or counter to
        # keep here, and a field would only be a race waiting to be written.
        self._sub_topic_concurrency = resolved_concurrency
        self._run_source_state: ResearchState | None = None

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

    def _planned_targets(self) -> list[EvidenceTarget]:
        """Every target this pass's extraction may bind a finding to.

        Read from the run's own state rather than from the active
        ``AcquisitionPolicy``: a policy knows the one coverage id it was built
        for, and the point of the extraction list is precisely that a read
        fetched for one topic may answer another topic's target. The order is
        the plan's, so the list is stable across a run's sub-topics and a
        replayed request differs only where its evidence does.

        On an extra pass the list is confined to ``state.
        extra_pass_target_ids``: that pass exists to acquire the required
        targets still missing a verified finding, so its contract offers the
        model those targets and no others — a finding bound to a target the
        pass was not sent for would be dropped as unplanned anyway.

        A run with no plan in hand — a direct ``extract_findings`` call, or a
        snapshot predating the target inventory — yields none, and extraction
        then binds nothing, because there is no inventory to bind against.
        """
        state = self._run_source_state
        if state is None:
            return []
        planned = [
            target
            for sub_topic in state.sub_topics
            for target in counted_evidence_targets(sub_topic.evidence_targets)
        ]
        wanted = set(state.extra_pass_target_ids)
        if not wanted:
            return planned
        return [target for target in planned if target.target_id in wanted]

    def _recorded_findings(self) -> tuple[Finding, ...]:
        """The findings the run already holds, for the packet's own steering.

        Read from the run's incoming state, which is where every earlier pass's
        record lives: a later pass that re-reads a page should see that the
        sentence it is about to mine is already a finding, instead of mining it
        and letting the fold collapse the duplicate afterwards.
        """
        state = self._run_source_state
        if state is None:
            return ()
        return tuple(state.verified_findings)

    def _admitted_evidence_keys(self) -> list[tuple[str, str]]:
        """The ``(read_id, locator)`` passages the run has already mined.

        ``raw_findings`` is append-only across research rounds, so it is the
        run's whole record of what an extraction admitted, in order. The keys
        are what the owed-passage re-extraction reads: a later pass asks for
        the passages *the run* has no finding from, not for the passages this
        pass alone has no finding from.
        """
        state = self._run_source_state
        if state is None:
            return []
        return list(
            dict.fromkeys(
                (finding.read_id, finding.locator)
                for finding in state.raw_findings
                if finding.read_id and finding.locator
            )
        )

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
            # The turn cap is per loop; the call budget is the run's.
            # ``start_turn`` decrements ``remaining_model_turns`` once per model
            # turn, so the state this pass resumed carries the *previous* loop's
            # countdown — zero, for a target an earlier pass worked through —
            # while the packet renders it beside "Iteration 1 of N". The model
            # then reads a loop with no turns left over a loop that has all of
            # them, which invites it to stop before spending the budget the pass
            # was bought for. The calls are deliberately kept: that an earlier
            # pass spent them is a fact ``merge_acquisition_states`` preserves.
            acquisition_state = existing.model_copy(
                update={"remaining_model_turns": self.config.max_iterations}
            )
        return AcquisitionPolicy(
            state=acquisition_state,
            session_id=(
                self._run_source_state.session_id
                if self._run_source_state is not None
                else "researcher-session"
            ),
            target_id=target_id,
            query=(
                " ".join(
                    target.question
                    for target in counted_evidence_targets(
                        task.sub_topic.evidence_targets
                    )
                )
                + (
                    f" {self._run_source_state.original_question}"
                    if self._run_source_state is not None
                    else ""
                )
            ).strip(),
            origin="researcher",
            reads=self._run_reads,
            evidence=self._run_evidence,
            findings=self._recorded_findings(),
            dispositions=self._run_dispositions,
            boundary_audits=self._run_boundary_audits,
            audit_sequence=self._run_audit_sequence,
            retrieved_at=lambda: self._clock().isoformat(),
            read_admission_chars=self._read_admission_chars,
            configuration_fingerprint=(
                f"admission={self._read_admission_chars};"
                f"packet={self._evidence_packet_chars};"
                f"decision={self._decision_context_chars}"
            ),
            cache=self._run_cache,
            network_read_ids=self._run_network_read_ids,
        )

    def sub_topic_task(
        self,
        base: AgentTask,
        sub_topic: SubTopic,
        existing_sources: Sequence[str],
    ) -> SubTopicTask:
        """Narrow the run-level task down to one sub-topic's loop."""
        sections = [
            section
            for section in (
                base.guidance.strip(),
                render_sub_topic_guidance(sub_topic, existing_sources),
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

    async def _extract_one_page(
        self,
        task: SubTopicTask,
        run: ReActRun,
        policy: AcquisitionPolicy,
        read_id: str,
        *,
        gate: asyncio.Semaphore,
        valid_target_ids: Sequence[str] | None,
        planned_targets: Sequence[EvidenceTarget],
        question: str | None,
        coverage_titles: Mapping[str, str],
    ) -> _PageExtraction:
        """One page's own extraction call (S6): started as soon as ``read_id``
        is admitted, bounded by ``agents.extraction_concurrency``.

        The packet is scoped to this read alone (``read_ids=[read_id]``), so
        every sibling page of this sub-topic sends the identical static
        prefix -- system prompt, response contract, sub-topic, planned
        targets -- and only the "Retrieved evidence" section, the very end
        of the request, differs page to page. That is what lets the
        provider's own prefix cache serve every page after the first one.

        ``known_reads``/``known_urls`` are this one read alone, resolved from
        ``policy.reads`` at call time rather than a broader snapshot taken
        when the task was scheduled: this page's own packet shows the model
        nothing but this read, so a finding it drafts can only ever cite this
        read's own id and url, and a hallucinated reference to any other read
        must be rejected exactly as ``build_findings`` already rejects one it
        was never shown.

        A failure here is this page's alone: the caller merges every page's
        own result, in read order, and one page's provider failure never
        costs the pages that already succeeded.
        """
        started_at = perf_counter()
        async with gate:
            try:
                draft = await self.provider.complete_structured(
                    extraction_messages(
                        task,
                        run,
                        evidence_chars=self._evidence_chars,
                        acquisition_context=policy.context(
                            limit=self._evidence_packet_chars,
                            read_ids=[read_id],
                        ),
                        planned_targets=planned_targets,
                        question=question,
                        coverage_titles=coverage_titles,
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                )
            except ProviderOutputLimitError as error:
                return _PageExtraction(
                    read_id=read_id,
                    error=extraction_output_limit_error(run, error),
                    elapsed_s=round(perf_counter() - started_at, 1),
                )
            except ProviderError as error:
                return _PageExtraction(
                    read_id=read_id,
                    error=extraction_provider_error(run, error),
                    failed_provider=True,
                    elapsed_s=round(perf_counter() - started_at, 1),
                )
        read = policy.reads.get(read_id)
        # Every read of this same URL, not only the one ``read_id`` names:
        # a URL a prior pass already recorded, admitted again this pass, can
        # resolve to a second read id for the identical body (a cache-hit
        # that adopted a different id, or a run whose registry predates this
        # one) -- a draft is free to cite either, and both describe the one
        # page this call's own packet showed.
        known_reads = (
            {}
            if read is None
            else {
                candidate_id: candidate
                for candidate_id, candidate in policy.reads.items()
                if candidate.resolved_url == read.resolved_url
            }
        )
        retrieved = () if read is None else (read.resolved_url,)
        admitted_keys: list[tuple[str, str]] = []
        unplanned_target_ids: list[str] = []
        dropped_figures: list[str] = []
        findings, rejected = build_findings(
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
        if findings:
            # S6 acceptance 4: a still-running loop's later decision turn
            # must see this page's findings, not just the merge after the
            # loop finishes. ``policy.context()`` renders ``policy.findings``
            # beside the evidence on every subsequent call, so recording them
            # here -- synchronously, with no ``await`` in between reading and
            # writing -- is the whole mechanism; no lock is needed because
            # nothing yields to the event loop mid-update.
            policy.findings = [*policy.findings, *findings]
        return _PageExtraction(
            read_id=read_id,
            findings=findings,
            admitted_keys=admitted_keys,
            rejected=rejected,
            unplanned_target_ids=unplanned_target_ids,
            dropped_figures=dropped_figures,
            elapsed_s=round(perf_counter() - started_at, 1),
        )

    async def _retry_owed_batches_for_page(
        self,
        task: SubTopicTask,
        run: ReActRun,
        policy: AcquisitionPolicy,
        read_id: str,
        batches: Sequence[list[EvidenceUnit]],
        *,
        gate: asyncio.Semaphore | None,
        retrieved: Sequence[str],
        known_reads: Mapping[str, ReadRecord] | None,
        valid_target_ids: Sequence[str] | None,
        planned_targets: Sequence[EvidenceTarget],
        unanswered: Sequence[EvidenceTarget],
        question: str | None,
        coverage_titles: Mapping[str, str],
        dissent_statements: Sequence[Finding] | None = None,
    ) -> _OwedPageResult:
        """One page's own owed re-ask: up to ``MAX_OWED_BATCHES`` packets of
        its own owed passages alone (S6). ``dissent_statements`` switches
        the packet from an owed re-ask to the dissent re-ask (D2, Fable's
        audit of run 7): when given, every batch's own finding is marked
        ``disputes=True`` after admission, bound to whichever of
        ``dissent_statements`` its own content disputes.

        Run under the same per-page ``extraction_concurrency`` gate that
        page's own main call ran under (``gate=None`` only for the legacy
        caller that scheduled no per-page tasks at all, which never runs more
        than one page's owed batches concurrently in the first place), so
        several owing pages' own re-asks overlap instead of running one after
        another. Every accumulator is local to this page's own call: the
        caller merges every page's own result back in a fixed page order
        after every owing page's task has returned, never completion order.
        """
        findings: list[Finding] = []
        rejected: list[str] = []
        errors: list[ResearchError] = []
        admitted_keys: list[tuple[str, str]] = []
        unplanned_target_ids: list[str] = []
        dropped_figures: list[str] = []
        asked_ids: list[str] = []
        for batch in batches:
            asked_ids.extend(unit.evidence_id for unit in batch)

            async def _call() -> SubTopicFindingsDraft:
                return await self.provider.complete_structured(
                    extraction_messages(
                        task,
                        run,
                        evidence_chars=self._evidence_chars,
                        acquisition_context=build_acquisition_context(
                            policy.state,
                            policy.reads,
                            {unit.evidence_id: unit for unit in batch},
                            limit=self._decision_context_chars,
                            target_id=policy.target_id,
                            dispositions=policy.dispositions,
                            focus_ids=[unit.evidence_id for unit in batch],
                        ),
                        planned_targets=planned_targets,
                        owed_passages=dissent_statements is None,
                        owed_targets=(
                            () if dissent_statements is not None else unanswered
                        ),
                        dissent_passages=dissent_statements is not None,
                        disputed_statements=dissent_statements or (),
                        question=question,
                        coverage_titles=coverage_titles,
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                    # The one call whose cap was not lifted: it looped to its
                    # cap at 32,768 and at 49,152 tokens, so a larger cap only
                    # lengthened the runaway.
                    max_tokens=self.config.re_extraction_max_tokens,
                )

            try:
                if gate is not None:
                    async with gate:
                        retry_draft = await _call()
                else:
                    retry_draft = await _call()
            except ProviderError as error:
                errors.append(owed_extraction_provider_error(run, error))
                continue
            retry_findings, retry_rejected = build_findings(
                retry_draft,
                sub_topic=task.sub_topic,
                extracted_at=self._clock().isoformat(),
                known_urls=retrieved,
                known_reads=known_reads,
                valid_target_ids=valid_target_ids,
                admitted_evidence_keys=admitted_keys,
                dropped_target_ids=unplanned_target_ids,
                dropped_figures=dropped_figures,
            )
            if dissent_statements is not None:
                # Validated post-admission (RevZ1, run 7 fix wave review,
                # P2; narrowed rather than dropped on an extra target per
                # Fable's prompt review, R3): every returned finding was
                # previously marked disputes=True unconditionally,
                # trusting the reply's own read_id/locator and target_ids
                # without checking either against what the packet actually
                # asked about. Kept only when its own passage is one the
                # packet actually sent, and its own target ids are
                # narrowed to their intersection with the disputed
                # statements' own targets -- a conscientious model that
                # also binds one more target it believes the content
                # answers must not lose the dispute finding entirely for
                # it. Dropped only when its passage is not the packet's
                # own, or that intersection is empty (an unbound reply, or
                # one bound to targets none of the statements name).
                # ``build_findings`` itself stays ignorant of the dissent
                # re-ask (minimal blast radius).
                batch_keys = {(unit.read_id, unit.locator) for unit in batch}
                disputed_target_ids = {
                    target_id
                    for statement in dissent_statements
                    for target_id in statement.target_ids
                }
                validated: list[Finding] = []
                for finding in retry_findings:
                    if (finding.read_id, finding.locator) not in batch_keys:
                        admitted_keys.remove(
                            (finding.read_id, finding.locator)
                        )
                        rejected.append(
                            f"{read_id}: dissent reply names a passage "
                            "outside its own packet, dropped"
                        )
                        continue
                    kept_target_ids = [
                        target_id
                        for target_id in finding.target_ids
                        if target_id in disputed_target_ids
                    ]
                    if not kept_target_ids:
                        admitted_keys.remove(
                            (finding.read_id, finding.locator)
                        )
                        rejected.append(
                            f"{read_id}: dissent reply is not bound to any "
                            "listed disputed statement's own target, "
                            "dropped"
                        )
                        continue
                    validated.append(
                        finding.model_copy(
                            update={
                                "disputes": True,
                                "target_ids": kept_target_ids,
                            }
                        )
                    )
                retry_findings = validated
            findings.extend(retry_findings)
            # Prefixed with this page's own read id (RevSelectionR3 P3,
            # ReRevS6): two different pages that both return a malformed
            # owed retry finding would otherwise both report "finding 1:
            # ...", indistinguishable in the merged errors.
            rejected.extend(f"{read_id}: {reason}" for reason in retry_rejected)
        return _OwedPageResult(
            read_id=read_id,
            findings=findings,
            rejected=rejected,
            errors=errors,
            admitted_keys=admitted_keys,
            unplanned_target_ids=unplanned_target_ids,
            dropped_figures=dropped_figures,
            asked_evidence_ids=asked_ids,
        )

    async def extract_findings(
        self,
        task: SubTopicTask,
        run: ReActRun,
        policy: AcquisitionPolicy | None = None,
        page_extractions: Mapping[str, "asyncio.Task[_PageExtraction]"] | None = None,
        admitted_read_order: Sequence[str] = (),
        gate: asyncio.Semaphore | None = None,
    ) -> tuple[list[Finding], list[ResearchError], ExtractionFailure, bool]:
        """Turn one finished sub-topic loop into validated findings.

        Returns nothing — and makes no provider call — when the loop stopped
        on a provider failure or READ no source, so the extraction step can
        never invent one. "Read" is the shared read-bearing rule: a loop that
        only searched, or only wrote to memory, has nothing to extract from.

        ``policy`` is this loop's own acquisition policy, passed in rather than
        read from an "active" field: several sub-topic loops share the agent
        (D9), so the policy a call acts on has to be the caller's own.

        S6: ``page_extractions`` and ``admitted_read_order`` are the
        background per-page extraction tasks ``_research_sub_topic`` started
        as each read was admitted, and the fixed order to merge them in --
        read order, never completion order, so the sub-topic's output is
        identical whatever order the concurrent calls actually finish in. A
        caller with no per-page tasks (``finalize()``'s legacy entry point,
        which has no policy of its own to have scheduled any) falls back to
        one call over the whole run's evidence, exactly as before S6.

        The third element, ``failure``, is why the extraction call produced
        nothing at all, and there are three answers. ``""`` is no failure, or
        a partial page failure that left every succeeding page's own
        findings standing (S6: one page's provider error is that page's own
        record, reported as its own error, never a run-halting one).
        ``"provider"`` is every page's call failing to reach the model
        provider at once -- a genuine outage, not one page's own trouble --
        and the caller must treat that the same way it treats a
        ReAct-loop-level ``provider_error``: stop researching further
        sub-topics, but keep every finding already collected.
        ``"output_limit"`` is the legacy single-call path's own truncated
        reply, or S6's own signal for a partial page failure. In a mixed
        result only the failed page's own units are excluded from this
        pass's ``complete_extraction`` (so they are not marked consumed the
        moment they failed) and disposed of as ``EXTRACTION_FAILED_REASON``
        rather than ``irrelevant``; every succeeded page's own batch is
        still consumed as usual. The fourth is the target-obligation flag
        this extraction completed, which the caller reports in the
        sub-topic's own completed event.
        """
        # One call, two consumers: the same tuple gates the provider call and
        # becomes the provenance allow-list, so "did this loop read anything"
        # and "which URLs may a finding cite" cannot disagree.
        target_obligation_completed = False
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
            if page_extractions:
                # Some pages may have already completed their own
                # extraction call before the loop failed (a later decision
                # call's ProviderError, a re-raised ``RequestAttemptLimit
                # Error``, or the sub-topic simply being cancelled): those
                # findings were already paid for and must not be thrown
                # away, even though the run is about to stop researching
                # further sub-topics. Nothing still in flight should keep
                # calling the provider after the pass has halted
                # (RevSelectionR3 P1).
                salvaged: list[Finding] = []
                for read_id in admitted_read_order:
                    page_task = page_extractions.get(read_id)
                    if page_task is None or not page_task.done():
                        continue
                    if page_task.cancelled() or page_task.exception() is not None:
                        continue
                    page = page_task.result()
                    if page.error is None:
                        salvaged.extend(page.findings)
                await _cancel_and_gather_pages(page_extractions)
                return salvaged, [], "", False
            return [], [], "", False

        if policy is not None:
            # The local extract step, taken before the provider call so the
            # continuation batch is part of the packet. Bounded and idempotent:
            # a batch already handed over inside the loop is not repeated, and
            # the batch bound drains the pending list instead of leaving a
            # gate acquisition can never pass.
            policy.extract_passage_batch()

        planned_targets = self._planned_targets() if policy is not None else []
        coverage_titles = {
            sub_topic.coverage_id: sub_topic.title
            for sub_topic in (
                self._run_source_state.sub_topics
                if self._run_source_state is not None
                else ()
            )
        }
        question = (
            self._run_source_state.original_question
            if self._run_source_state is not None
            else None
        )
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

        findings: list[Finding] = []
        rejected: list[str] = []
        admitted_keys: list[tuple[str, str]] = []
        unplanned_target_ids: list[str] = []
        dropped_figures: list[str] = []
        errors: list[ResearchError] = []
        sub_topic_extraction_failure: ExtractionFailure = ""

        if policy is not None and page_extractions is not None and admitted_read_order:
            # S6: merge every page's own extraction, in read order -- never
            # completion order -- so the sub-topic's output is identical
            # whatever order the concurrent calls actually finish in.
            page_failures: list[str] = []
            failed_read_ids: set[str] = set()
            pages_run = 0
            try:
                for read_id in admitted_read_order:
                    page_task = page_extractions.get(read_id)
                    if page_task is None:
                        continue
                    pages_run += 1
                    page = await page_task
                    if page.error is not None:
                        errors.append(page.error)
                        page_failures.append(
                            "provider" if page.failed_provider else "output_limit"
                        )
                        failed_read_ids.add(read_id)
                        continue
                    findings.extend(page.findings)
                    admitted_keys.extend(page.admitted_keys)
                    # Prefixed with this page's own read id (RevSelectionR3
                    # P3): each page numbers its own rejections from
                    # "finding 1", so an unprefixed merge could report
                    # "finding 1: ..." twice for two different pages' own
                    # drops.
                    rejected.extend(
                        f"{read_id}: {reason}" for reason in page.rejected
                    )
                    unplanned_target_ids.extend(page.unplanned_target_ids)
                    dropped_figures.extend(page.dropped_figures)
            except BaseException:
                # A page task raised something ``_extract_one_page`` itself
                # never catches (only ``ProviderOutputLimitError``/
                # ``ProviderError`` are caught there) -- a bug, or an
                # external cancellation. Every task not yet awaited here
                # must not keep calling the provider after this loop has
                # stopped waiting for it (RevSelectionR3 P1).
                await _cancel_and_gather_pages(page_extractions)
                raise
            if pages_run and len(page_failures) == pages_run:
                # Every page's own call failed: nothing was extracted at
                # all, exactly the legacy single-call early return -- the
                # owed-batch step below has no admitted evidence of this
                # pass's own to work from, so it is skipped rather than
                # asking a provider that just failed for more. A genuine
                # outage (every failure unreachable, never one page's own
                # output-limit trouble) is the run-halting condition a
                # loop-level ``provider_error`` is; anything else defers the
                # batch without halting the run. Every read stays in
                # ``pending_extraction_ids`` (neither ``complete_extraction``
                # nor ``record_extraction_dispositions`` runs on this path),
                # exactly the pre-S6 outage behaviour.
                if all(failure == "provider" for failure in page_failures):
                    return findings, errors, "provider", False
                return findings, errors, "output_limit", False
            if page_failures:
                # A mixed result: some pages succeeded, so their findings
                # stand and the owed-batch step below still runs over
                # whatever evidence they produced -- "every other page's
                # findings stand" is the whole point of per-page isolation.
                sub_topic_extraction_failure = "output_limit"
        else:
            failed_read_ids = set()
            # The legacy single-call path: no policy (``finalize()``'s own
            # entry point has none of its own to schedule per-page tasks
            # from), a policy this caller built without scheduling any, or
            # -- S6 -- a pass that admitted no new read at all (a repeat
            # pass whose one page was already read by an earlier pass, and
            # so never fires ``on_read_admitted`` again). That last case
            # still deserves a chance to re-mine the topic's accumulated
            # evidence: a single-call extraction always covered every read
            # the topic held, not only the ones read this pass, and the
            # owed-batch step below only asks about a passage that states a
            # figure or a target's own words, which a page with neither (the
            # "no projection figure" case) never qualifies for -- without
            # this fallback such a pass would make no extraction call at
            # all and report a spurious no-findings error for a topic whose
            # only page was already read and already mined.
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
                        question=question,
                        coverage_titles=coverage_titles,
                    ),
                    SubTopicFindingsDraft,
                    agent_name=self.name,
                )
            except ProviderOutputLimitError as error:
                # The provider answered and the answer was cut off at the
                # output cap. That is this sub-topic's extraction failing,
                # not the run's transport, so the pass continues: the caller
                # defers the batch, so the reads stay owed rather than
                # consumed.
                return (
                    [],
                    [extraction_output_limit_error(run, error)],
                    "output_limit",
                    False,
                )
            except ProviderError as error:
                return [], [extraction_provider_error(run, error)], "provider", False
            findings, rejected = build_findings(
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

        # The passages earlier passes mined. They are kept out of
        # ``admitted_keys`` itself — that list is this pass's own admissions,
        # and the sub-topic's "the obligation was completed" reading comes from
        # it — but the owed-passage re-extraction must not spend a passage the
        # run already has a finding from, so it is gated on both.
        mined_earlier = self._admitted_evidence_keys()

        unanswered: list[EvidenceTarget] = []
        if policy is not None:
            own_targets = counted_evidence_targets(task.sub_topic.evidence_targets)
            # A page whose own extraction call failed this pass is excluded
            # from the owed-unit sets entirely (RevSelectionR3 P1): the
            # provider never actually saw or walked past that page's own
            # passages, so they can be neither "unmined" nor "irrelevant" --
            # they stay owed instead, via ``except_read_ids`` below.
            owed_eligible_evidence = (
                policy.evidence
                if not failed_read_ids
                else {
                    evidence_id: unit
                    for evidence_id, unit in policy.evidence.items()
                    if unit.read_id not in failed_read_ids
                }
            )
            # The selected passages this sub-topic's own targets ask a figure
            # for and no admitted finding used. Only the units selected for
            # this target are asked, and only its own targets' measure units
            # decide what a passage owes.
            owed_figures = _units_owing_a_figure(
                owed_eligible_evidence,
                target_ids=(
                    () if policy.target_id is None else (policy.target_id,)
                ),
                bases=_measure_bases(own_targets),
                # The run's keys as well as this pass's: a passage an earlier
                # pass already mined is not *owed* a figure extraction here.
                # The words-owed re-ask below deliberately keeps this pass's
                # keys alone — its whole purpose is to bind a target from a
                # passage the run has read, so a passage an earlier pass mined
                # without that binding must stay eligible for it.
                used=[*mined_earlier, *admitted_keys],
            )
            # The same bounded re-ask for a required obligation this pass left
            # unanswered: a target with no unit of measure has no figure to
            # look for, so the passages that state its own words are what can
            # bind it, and an extraction that walked past them is the run
            # answering half a question.
            unanswered = _unbound_required_targets(own_targets, findings)
            owed_own_words = _units_owing_own_words(
                owed_eligible_evidence,
                target_ids=(
                    () if policy.target_id is None else (policy.target_id,)
                ),
                targets=unanswered,
                used=admitted_keys,
            )
            # The passages that owe the most are the ones asked about first
            # within each page's own queue, and each page's own packets are
            # bounded: a request that carried every owed passage of the page
            # is what ran this call away to its output cap.
            batches_by_page = _owed_batches_by_page(
                _ordered_owed_units(
                    [*owed_figures, *owed_own_words],
                    bases=_measure_bases(own_targets),
                    own_words={
                        target.target_id: _own_words(target)
                        for target in unanswered
                    },
                )
            )
            # The obligation this pass completed is the ACTIVE topic's, and a
            # finding bound to another topic's target does not complete it.
            # Mining a read for every planned target would otherwise let one
            # topic's binding stand in for another's. Only findings that
            # carry a binding are asked: an unbound finding (a legacy one, or
            # an extraction that named an id outside the plan) is attributed
            # through this very topic later, which is what the old reading —
            # "some passage of this topic was used" — already said. Hoisted
            # here, before the cross-topic sweep below, which needs this
            # topic's own target ids to leave them to the sweep above instead
            # of asking about them twice.
            own_target_ids = {
                target.target_id
                for target in counted_evidence_targets(
                    task.sub_topic.evidence_targets
                )
            }
            # D7/D10: every admitted read is also asked about the plan's
            # required, qualitative targets no finding from THAT read binds,
            # whichever sub-topic owns them -- the sweep above only ever asks
            # a page about the reading sub-topic's own required targets, so a
            # page read for one topic that holds another topic's required
            # answer is never asked about it (this topic's own targets are
            # excluded here: the sweep above already covers them). Candidates
            # come from the read's own admitted units, not only the ones the
            # active target's own query selected, and at most one bounded
            # packet is sent per read -- a runaway guard, not a content cap --
            # so a read left with several targets still unbound still costs
            # one call, never one per target. Computed from this main
            # extraction's own findings, before the own-topic sweep above's
            # own packets are even sent -- both sweeps go out in the same
            # round trip below (RevX1Sweep P2-1) -- at the accepted cost that
            # a target the own sweep alone binds is not seen here and may be
            # asked about twice.
            #
            # The bound check reads the run's whole record, not only this
            # pass's own findings (RevX1Sweep P3-1): an extra pass may
            # re-admit a read this topic already held, and the target that
            # read already bound may have been bound by an earlier pass, not
            # this one. ``used`` includes ``mined_earlier`` for the same
            # reason: a passage an earlier pass already mined is not owed a
            # second, reworded finding.
            cross_topic_required_targets = [
                target
                for target in planned_targets
                if target.required
                and target.unit_dimension is None
                and target.target_id not in own_target_ids
            ]
            cross_topic_batches: dict[str, list[EvidenceUnit]] = {}
            cross_topic_unanswered: dict[str, list[EvidenceTarget]] = {}
            run_findings = (
                self._run_source_state.raw_findings
                if self._run_source_state is not None
                else ()
            )
            # The plan's own repeated subject words (Fable's audit of run 6,
            # Appendix 2): computed once per pass over every planned target,
            # shared by the cross-topic sweep below and the dissent re-ask
            # further down, not just the cross-topic candidates, because a
            # target this sub-topic owns is as much a source of the plan's
            # shared vocabulary as one it does not.
            generic_tokens = _generic_plan_tokens(planned_targets)
            if cross_topic_required_targets:
                own_read_ids = dict.fromkeys(
                    unit.read_id
                    for unit in owed_eligible_evidence.values()
                    if policy.target_id is None
                    or policy.target_id in unit.target_ids
                )
                for read_id in own_read_ids:
                    owed_here = _required_targets_unbound_by_read(
                        cross_topic_required_targets,
                        [*run_findings, *findings],
                        read_id,
                    )
                    if not owed_here:
                        continue
                    non_generic_words = {
                        target.target_id: _cross_topic_target_words(
                            target, generic_tokens
                        )
                        for target in owed_here
                    }
                    candidates = _units_owing_cross_topic_words(
                        owed_eligible_evidence,
                        read_id=read_id,
                        own_words=non_generic_words,
                        used=[*mined_earlier, *admitted_keys],
                    )
                    if not candidates:
                        continue
                    cross_topic_batches[read_id] = _ordered_owed_units(
                        candidates,
                        bases=frozenset(),
                        own_words=non_generic_words,
                    )[:MAX_OWED_PASSAGES_PER_BATCH]
                    cross_topic_unanswered[read_id] = owed_here
            owed_cross_topic_units = [
                unit for batch in cross_topic_batches.values() for unit in batch
            ]
            # D2: the dissent re-ask, one packet per admitted read (Fable's
            # audit of run 7, CODE 1). A page can hold both a stated step
            # and, elsewhere on that same page or a different one the run
            # has read, another page's rejection, qualification or dating
            # of it -- the main extraction only ever asks what a passage
            # states, never what it disputes about a claim the run has
            # already kept, and the same omission has recurred across four
            # heads. Checked only for a read this pass itself admitted (S6's
            # own per-page architecture is what "that read's main
            # extraction" refers to); the retained pool is this sub-topic's
            # own findings so far, on top of the run's whole record, so a
            # passage on a page read for this topic can dispute a finding
            # another topic's read produced, and the reverse.
            dissent_batches: dict[str, list[EvidenceUnit]] = {}
            dissent_statements_by_read: dict[str, list[Finding]] = {}
            if admitted_read_order:
                retained_for_dissent = [*run_findings, *findings]
                # A passage the same gather's own owed or cross-topic
                # packet already carries is excluded here too (RevZ1, run
                # 7 fix wave review, P2): those packets are built above but
                # not yet sent, so ``admitted_keys`` alone cannot yet know
                # they claim it, and without this a passage could be asked
                # about twice at once, in two concurrent packets.
                concurrent_batch_keys = {
                    (unit.read_id, unit.locator)
                    for page_batches in batches_by_page.values()
                    for batch in page_batches
                    for unit in batch
                } | {
                    (unit.read_id, unit.locator)
                    for batch in cross_topic_batches.values()
                    for unit in batch
                }
                for read_id in admitted_read_order:
                    if read_id in failed_read_ids:
                        continue
                    dissent_units, disputed = _dissent_units_for_read(
                        owed_eligible_evidence,
                        read_id=read_id,
                        retained=retained_for_dissent,
                        generic=generic_tokens,
                        used=[
                            *mined_earlier,
                            *admitted_keys,
                            *concurrent_batch_keys,
                        ],
                    )
                    if not dissent_units:
                        continue
                    dissent_batches[read_id] = dissent_units
                    dissent_statements_by_read[read_id] = disputed
            # One round trip for all three sweeps (RevX1Sweep P2-1, extended
            # to the dissent re-ask): every packet runs in the same gather,
            # under the same per-page gate, instead of waiting on an
            # earlier sweep's whole round trip to finish first. Sliced back
            # apart by count afterwards, not zipped, so each keeps the
            # fixed, read-ordered sequence its own dict built -- own owed
            # first, then cross-topic, then dissent.
            combined_jobs = [
                (read_id, page_batches, unanswered, None)
                for read_id, page_batches in batches_by_page.items()
            ] + [
                (read_id, [batch], cross_topic_unanswered[read_id], None)
                for read_id, batch in cross_topic_batches.items()
            ] + [
                (read_id, [batch], (), dissent_statements_by_read[read_id])
                for read_id, batch in dissent_batches.items()
            ]
            if combined_jobs:
                all_results = await asyncio.gather(
                    *(
                        self._retry_owed_batches_for_page(
                            task,
                            run,
                            policy,
                            read_id,
                            page_batches,
                            gate=gate,
                            retrieved=retrieved,
                            known_reads=known_reads,
                            valid_target_ids=valid_target_ids,
                            planned_targets=planned_targets,
                            unanswered=page_unanswered,
                            question=question,
                            coverage_titles=coverage_titles,
                            dissent_statements=page_dissent_statements,
                        )
                        for read_id, page_batches, page_unanswered, page_dissent_statements
                        in combined_jobs
                    )
                )
            else:
                all_results = []
            own_owed_results = all_results[: len(batches_by_page)]
            cross_topic_results = all_results[
                len(batches_by_page) : len(batches_by_page) + len(cross_topic_batches)
            ]
            dissent_results = all_results[
                len(batches_by_page) + len(cross_topic_batches) :
            ]
            asked: set[str] = set()
            for owed in own_owed_results:
                findings = [*findings, *owed.findings]
                rejected = [*rejected, *owed.rejected]
                errors.extend(owed.errors)
                admitted_keys.extend(owed.admitted_keys)
                unplanned_target_ids.extend(owed.unplanned_target_ids)
                dropped_figures.extend(owed.dropped_figures)
                asked.update(owed.asked_evidence_ids)
            # This topic's own telemetry, captured before the cross-topic
            # and dissent findings below are merged in (RevX1Sweep P3-2):
            # either kind is always bound to whatever statement it answers
            # or disputes, not necessarily this topic's own target, and
            # folding them into ``bound`` first would let one turn an
            # all-unbound-own-findings pass from "completed" into "not
            # completed" -- a regression neither sweep must cause, since
            # neither says anything about whether THIS topic's own
            # obligation advanced.
            bound = [finding for finding in findings if finding.target_ids]
            completed = policy.target_id is not None and bool(admitted_keys)
            if completed and own_target_ids and bound:
                completed = any(
                    own_target_ids.intersection(finding.target_ids)
                    for finding in bound
                )
            target_obligation_completed = completed
            for owed in [*cross_topic_results, *dissent_results]:
                findings = [*findings, *owed.findings]
                rejected = [*rejected, *owed.rejected]
                errors.extend(owed.errors)
                admitted_keys.extend(owed.admitted_keys)
                unplanned_target_ids.extend(owed.unplanned_target_ids)
                dropped_figures.extend(owed.dropped_figures)
                asked.update(owed.asked_evidence_ids)
            # Unit-level, so a passage is "used" only when that exact passage
            # produced an admitted finding. What the pass asked about and left
            # unmined says so in its own reason rather than "irrelevant";
            # whatever the bound never asked about keeps the plain one.
            owed_figure_ids = [
                unit.evidence_id
                for unit in owed_figures
                if unit.evidence_id in asked
            ]
            policy.record_extraction_dispositions(
                admitted_keys,
                unmined_quantity_ids=owed_figure_ids,
                unmined_target_ids=[
                    unit.evidence_id
                    for unit in [*owed_own_words, *owed_cross_topic_units]
                    if unit.evidence_id in asked
                    and unit.evidence_id not in set(owed_figure_ids)
                ],
                failed_read_ids=failed_read_ids,
            )
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
            policy.complete_extraction(except_read_ids=failed_read_ids)
        if not rejected:
            return findings, errors, sub_topic_extraction_failure, target_obligation_completed
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
        return findings, errors, sub_topic_extraction_failure, target_obligation_completed

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> ResearchFindings | None:
        """Adapt ``extract_findings`` to the ``BaseAgent`` hook.

        ``run`` calls ``extract_findings`` directly so it can keep the
        recoverable errors this hook signature has nowhere to return, and so
        it can hand the call its own loop's policy.
        """
        if not isinstance(task, SubTopicTask):
            raise AgentConfigurationError(
                "ResearcherAgent.finalize requires a SubTopicTask"
            )
        findings, _, _, _ = await self.extract_findings(task, run)
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

    async def _research_sub_topic(
        self,
        task: SubTopicTask,
        policy: AcquisitionPolicy,
        scratchpad: ScratchpadMemory,
        tool_lock: asyncio.Lock,
    ) -> "_LoopWithExtraction":
        """Run one bounded ReAct loop for one sub-topic.

        The loop gets its own scratchpad, its own policy and the run's tool
        lock, so its prompt renders its own observations and its own
        acquisition state however many sibling loops are running (D9).
        Context that genuinely carries over between sub-topics travels in
        ``task.guidance`` instead of through shared notes.

        S6: each read's own extraction call starts in the background the
        moment it is admitted (``policy.on_read_admitted``), bounded by
        ``agents.extraction_concurrency``, while the loop keeps running --
        more searches and reads. The tasks and the fixed order they were
        admitted in travel back with the loop so the caller can await and
        merge them once it has finished, deterministically, in that same
        order rather than whichever order the concurrent calls complete in.
        A placeholder run stands in for the loop's own final ``ReActRun`` in
        an early-started page's error context (iterations/tool_calls used
        only for an error's own telemetry, never the request itself, which
        is built entirely from ``task``/``policy``): the loop has not
        finished when the first page's call is issued, and a page's own
        provider failure is reported against the counts at task-start
        rather than the loop's eventual final tally.
        """
        toolset = self.toolset
        extraction_gate = asyncio.Semaphore(self.config.extraction_concurrency)
        extraction_tasks: dict[str, asyncio.Task[_PageExtraction]] = {}
        admitted_read_order: list[str] = []
        placeholder_run = ReActRun(agent_name=self.name, stop_reason="finished")
        planned_targets = self._planned_targets()
        coverage_titles = {
            sub_topic.coverage_id: sub_topic.title
            for sub_topic in (
                self._run_source_state.sub_topics
                if self._run_source_state is not None
                else ()
            )
        }
        question = (
            self._run_source_state.original_question
            if self._run_source_state is not None
            else None
        )
        valid_target_ids = (
            [target.target_id for target in planned_targets]
            if planned_targets
            else None
        )

        def on_read_admitted(read_id: str) -> None:
            if read_id in extraction_tasks:
                return
            admitted_read_order.append(read_id)
            extraction_tasks[read_id] = asyncio.create_task(
                self._extract_one_page(
                    task,
                    placeholder_run,
                    policy,
                    read_id,
                    gate=extraction_gate,
                    valid_target_ids=valid_target_ids,
                    planned_targets=planned_targets,
                    question=question,
                    coverage_titles=coverage_titles,
                )
            )

        policy.on_read_admitted = on_read_admitted

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> tuple[ReActDecision, ...]:
            return await self._complete_react_decision(
                task,
                iteration=iteration,
                steps=steps,
                decision_context=policy.context(
                    limit=self._decision_context_chars, for_decision=True
                ),
                scratchpad=scratchpad,
            )

        async def record(step: ReActStep) -> None:
            await self._record_step(step, scratchpad=scratchpad)

        try:
            react = await run_react_loop(
                agent_name=self.name,
                tracker=self.tracker,
                tools=toolset,
                decide=decide,
                max_iterations=self.config.max_iterations,
                tool_budget=self.config.tool_budget_for(self.name),
                on_step=record,
                is_sufficient=self.is_sufficient,
                summary_limit=self.config.observation_summary_chars,
                tool_policy=policy,
                job_id=f"{self.name}/{policy.session_id}/{policy.target_id}",
                tool_lock=tool_lock,
                propagate_provider_errors=False,
            )
        except BaseException:
            # ``run_react_loop`` re-raises some failures rather than folding
            # them into a ``ReActRun`` (a re-raised ``RequestAttemptLimit
            # Error``, for one) -- whatever page tasks this loop already
            # started must not be left running unobserved after the loop
            # itself has failed to even return (RevSelectionR3 P1).
            await _cancel_and_gather_pages(extraction_tasks)
            raise
        react = react.model_copy(
            update={"errors": [*react.errors, *scratchpad.drain_errors()]}
        )
        return _LoopWithExtraction(
            react=react,
            extraction_tasks=extraction_tasks,
            admitted_read_order=admitted_read_order,
            extraction_gate=extraction_gate,
        )

    async def _research_one(
        self,
        index: int,
        task: SubTopicTask,
        tool_lock: asyncio.Lock,
    ) -> _SubTopicOutcome:
        """Run one sub-topic's loop and extraction inside its own agent span.

        Nothing here writes to the agent: the loop's findings, errors, events
        and counters are returned for ``run`` to fold in plan order.
        """
        sub_topic = task.sub_topic
        events = [
            sub_topic_started_event(
                sub_topic,
                index=index,
                existing_sources=len(task.existing_sources),
            )
        ]
        scratchpad = ScratchpadMemory(
            session_id=self._scratchpad.session_id,
            agent_name=self._scratchpad.agent_name,
            max_entries=self._scratchpad.max_entries,
        )
        policy = self._policy_for_task(task)
        started_at = perf_counter()
        async with self.tracker.agent_span(self.name) as span:
            # Own the tasks here (RevSelectionR3 P1): whatever exception
            # escapes this block, every per-page task this loop started must
            # be cancelled and awaited before it propagates, so none keeps
            # calling the provider after this sub-topic's own pass has given
            # up on it. ``extract_findings`` and ``_research_sub_topic``
            # each already clean up their own known failure paths; this is
            # the last-resort net for anything that still gets past them.
            loop_result: _LoopWithExtraction | None = None
            try:
                loop_result = await self._research_sub_topic(
                    task, policy, scratchpad, tool_lock
                )
                react = loop_result.react
                elapsed_s = round(perf_counter() - started_at, 1)
                (
                    sub_findings,
                    extraction_errors,
                    extraction_failure,
                    target_obligation_completed,
                ) = await self.extract_findings(
                    task,
                    react,
                    policy,
                    page_extractions=loop_result.extraction_tasks,
                    admitted_read_order=loop_result.admitted_read_order,
                    gate=loop_result.extraction_gate,
                )
            finally:
                if loop_result is not None:
                    await _cancel_and_gather_pages(loop_result.extraction_tasks)
            successful_reads = sum(
                read.resolved_url in policy.state.read_urls
                for read in policy.reads.values()
            )
            useful_evidence_yield = sum(
                policy.target_id in unit.target_ids
                for unit in policy.evidence.values()
            )
            acquired_work_count = policy.acquired_work_count
            if extraction_failure == "provider":
                # A retryable extraction failure consumed nothing: the batch
                # stays owed, visible in the persisted
                # ``pending_extraction_ids``, so the next pass knows exactly
                # which reads still need extracting.
                policy.defer_extraction()
                # Mirror the loop-level provider_error path so the merged run
                # (and this sub-topic's own completed event) never claims
                # "finished" over an abort that actually happened during
                # extraction. Only an unreachable provider does that: the pass
                # stops, and the sub-topics still queued record
                # ``provider_failure_stopped_processing``.
                react = react.model_copy(
                    update={"stop_reason": "provider_error"}
                )
            elif extraction_failure == "output_limit":
                # The provider answered and the answer was too long. This is
                # one sub-topic's extraction failing, not the run's transport,
                # so the pass continues to the next sub-topic; the batch stays
                # owed here too, because nothing consumed it.
                policy.defer_extraction()
            else:
                policy.complete_extraction()
            bounded = bound_sub_topic_findings(
                sub_findings,
                reads=list(self._run_reads.values()),
                required_target_ids={
                    target.target_id
                    for target in self._planned_targets()
                    if target.required
                },
                own_target_ids={
                    target.target_id
                    for target in counted_evidence_targets(
                        sub_topic.evidence_targets
                    )
                },
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
        errors = [*react.errors, *extraction_errors]
        if react.succeeded and not bounded.retained and is_high_priority(
            sub_topic, threshold=self._high_priority_threshold
        ):
            # Reported after the loop's own errors and before the completed
            # event, exactly where the sequential fold reported it.
            errors.append(no_findings_error(sub_topic, react))
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
                successful_reads=successful_reads,
                useful_evidence_yield=useful_evidence_yield,
                acquired_work_count=acquired_work_count,
                target_obligation_completed=target_obligation_completed,
                elapsed_s=elapsed_s,
            )
        )
        return _SubTopicOutcome(
            react=react,
            findings=bounded.retained,
            errors=errors,
            events=events,
            target_id=policy.target_id,
            target_state=policy.state,
        )

    async def run(self, state: ResearchState) -> AgentRun[ResearchFindings]:
        """Research every selected sub-topic, at most ``sub_topic_concurrency``
        loops at once, and fold their results in plan order.

        Each loop is independent — its own scratchpad, policy and bounded
        ReAct run — and they share exactly two things: the run-wide tool lock
        (D9), so a page two loops want is downloaded once, and the run's own
        read/evidence registry, so a body one loop read can serve another
        without a second acquisition. A provider failure in one loop stops
        the topics that have not started yet (they record
        ``provider_failure_stopped_processing``) and lets the running ones
        finish; a run-wide attempt ceiling is re-raised after every loop has
        settled, so the node still halts the run.
        """
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
        # up here) and would have silently polluted the shared cache a later
        # acquisition consumer is handed.
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
        base_task = self.build_task(state)
        eligible = _eligible_sub_topics(state)
        selected = eligible[: self._max_sub_topics]
        capped = eligible[self._max_sub_topics :]
        events: list[ResearchEvent] = []
        errors: list[ResearchError] = []
        findings: list[Finding] = []
        runs: list[ReActRun] = []

        if _extra_pass_has_nothing_to_do(state, selected):
            # The pass was bought for targets this pass can no longer act on:
            # every sub-topic owning one has spent its acquisition budget and
            # owes no extraction. Opening the loops would spend seven model
            # turns per sub-topic on calls the policy refuses, for no evidence
            # and about two and a half minutes of a thirty-minute budget, so
            # the refusal is recorded and no loop opens. The record names the
            # pass's whole job list and the sub-topics behind it, and the
            # pass-level completed event reports the topics it did not
            # research, so nothing is silently dropped.
            errors.append(
                extra_pass_unfunded_error(
                    targets=state.extra_pass_target_ids,
                    sub_topics=selected,
                )
            )
            events.append(
                research_completed_event(
                    sub_topics_planned=len(state.sub_topics),
                    sub_topics_researched=0,
                    sub_topics_skipped=len(capped) + len(selected),
                    findings=0,
                )
            )
            merged = merge_react_runs(self.name, []).model_copy(
                update={"errors": errors}
            )
            result = ResearchFindings(findings=[])
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

        # One tool lock for the whole run, never a module global (D9): every
        # sub-topic loop of this run shares it, so two loops can never be
        # inside a research tool's section -- and its admission to the run's
        # cache and ledger -- at the same time. It is made per run and dies
        # with it.
        tool_lock = asyncio.Lock()
        gate = asyncio.Semaphore(self._sub_topic_concurrency)
        # Set by the first loop whose work ends in a non-recoverable provider
        # failure. A loop that has not started yet checks it as it acquires
        # the gate and stops there, which is what keeps
        # ``provider_failure_stopped_processing`` for the topics that never
        # got a turn, while the loops already running finish. It is set before
        # the gate is released, so a waiter cannot slip past it.
        stop = asyncio.Event()

        async def research(
            index: int, sub_topic: SubTopic
        ) -> _SubTopicOutcome | None:
            """Run one sub-topic, or skip it when the pass has stopped."""
            async with gate:
                if stop.is_set():
                    return None
                task = self.sub_topic_task(
                    base_task,
                    sub_topic,
                    existing_sources_for(state, sub_topic),
                )
                try:
                    outcome = await self._research_one(index, task, tool_lock)
                except BaseException:
                    # A loop that RAISES stops the pass exactly as one that
                    # returns a provider failure does. The error is re-raised
                    # to the gather below, so every topic still queued behind
                    # this gate must not start: its model turns would be spent
                    # on work the re-raise throws away. (The plan's ceiling is
                    # seven sub-topics and the default cap is five, so queued
                    # work is the ordinary case, not a corner.) The flag is set
                    # before the gate is released, so a waiter cannot slip past
                    # it.
                    stop.set()
                    raise
                if not outcome.react.succeeded:
                    stop.set()
            return outcome

        settled_results = await asyncio.gather(
            *(
                research(index, sub_topic)
                for index, sub_topic in enumerate(selected, start=1)
            ),
            return_exceptions=True,
        )
        # Every sibling has settled by now, so a halt cancels no work: a
        # run-wide attempt ceiling is re-raised and still stops the run
        # (``graph/nodes.py`` halts on it), and any other unexpected failure is
        # re-raised rather than swallowed by the gather.
        refusals = [
            item
            for item in settled_results
            if isinstance(item, RequestAttemptLimitError)
        ]
        if refusals:
            raise refusals[0]
        settled: list[_SubTopicOutcome | None] = []
        for item in settled_results:
            if isinstance(item, BaseException):
                raise item
            settled.append(item)

        # The folds below iterate the plan-ordered task list, never the order
        # the loops finished in: findings, runs and events are the plan's, so a
        # report cannot depend on which loop happened to be scheduled first.
        unstarted: list[SubTopic] = []
        for sub_topic, outcome in zip(selected, settled, strict=True):
            if outcome is None:
                unstarted.append(sub_topic)
                continue
            if outcome.target_id is not None:
                if outcome.target_state is not None:
                    self._run_acquisition_states[outcome.target_id] = (
                        outcome.target_state
                    )
                self._run_seen_target_ids.add(outcome.target_id)
            runs.append(outcome.react)
            findings.extend(outcome.findings)
            errors.extend(outcome.errors)
            events.extend(outcome.events)

        # Every sub-topic that was never attempted -- either truncated by the
        # max_sub_topics cap, or left unstarted when a provider failure
        # stopped the pass early -- gets a structured, recoverable record
        # carrying its coverage id, whatever its priority. Without this,
        # state.errors and the event stream cannot be trusted as "every
        # sub-topic this pass set out to run was attempted or explicitly
        # skipped": sub-topics could vanish with no trace. Only the warning
        # for an *attempted* sub-topic that produced nothing stays restricted
        # to the high-priority ones, so a thin low-priority topic is not
        # noise. A topic the pass was never sent for is not in this list at
        # all: an extra pass is confined to its own job list, and a topic
        # outside it is no gap in the pass's coverage.
        unattempted: list[tuple[SubTopic, str]] = [
            *[(sub_topic, "cap") for sub_topic in capped],
            *[
                (sub_topic, "provider_failure_stopped_processing")
                for sub_topic in unstarted
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
