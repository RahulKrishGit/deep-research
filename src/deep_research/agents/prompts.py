"""Pure rendering of ReAct turns into provider messages.

Nothing here performs I/O, reads a clock, or consults a random source, so a
rendered prompt is a deterministic function of its inputs and can be asserted
on directly in tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.document_kind import derivative_self_description
from deep_research.agents.evidence import ReadDossier
from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import summarize_text
from deep_research.memory.entries import ScratchpadEntry
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    ContractModel,
    MemorySnapshot,
)

# The version of this prompt library. Every per-call configuration
# fingerprint records it, so an artifact says which instructions produced it
# without anyone diffing prompt text. Bump it when a prompt in this module
# changes meaning; an agent whose own prompt contract changed sets its own
# value on the class (``BaseAgent.prompt_version``).
PROMPT_VERSION = "1"

# The request itself carries the tools as provider-native function
# definitions, so this text must never advertise a catalogue or ask for an
# action envelope. Asking for one in text is what produced DeepSeek's DSML
# markup on 16 of 30 measured first attempts. Neither may it cap the model at
# one call per turn: every function call in one response is executed, so "at
# most one" only discards independent lookups the tools were given.
NATIVE_REACT_RESPONSE_CONTRACT = (
    "Call one or more tools supplied with this request when independent "
    "lookups or actions are needed. Use provider-native tool calling; never "
    "write or imitate a tool call in text, JSON, XML, DSML, or a Markdown "
    "fence. When no tool is needed, return the final answer directly."
)

# The one output-shape sentence every tool-free structured request carries, plus
# the notice that keeps a synthetic example subordinate to the real request.
STRUCTURED_REPLY_FORMAT = (
    "Return exactly one JSON object matching the supplied response schema, "
    "with no Markdown fence and no text before or after it."
)

STRUCTURED_EXAMPLE_NOTICE = (
    "The compact examples below show format and field relationships only. "
    "Each example input is separate from the real request. Do not copy its "
    "facts, URLs, or wording into the real answer."
)

# D10 (PD-29): the last line of every tool-free pipeline request. The static
# sections (the contract and the reply format) lead, so every call of one
# operation shares them as a prefix DeepSeek's context cache can reuse; the
# per-call material follows. The line avoids "JSON object", which
# STRUCTURED_REPLY_FORMAT alone carries.
STRUCTURED_REQUEST_END = "Return the reply for the material above."


def render_structured_request(
    static_sections: Sequence[str], material_sections: Sequence[str]
) -> str:
    """One request body: the static sections, the material, the closing line."""
    if not static_sections or not material_sections:
        raise ValueError("a structured request needs static sections and material")
    return "\n\n".join([*static_sections, *material_sections, STRUCTURED_REQUEST_END])


def render_structured_reply_format(
    examples: Sequence[tuple[str, str]],
) -> str:
    """Render one or two compact, complete JSON-object examples.

    Raises before any request is built, so a malformed or over-long example
    table fails where it is defined rather than reaching a paid call.
    """
    if not 1 <= len(examples) <= 2:
        raise ValueError("structured prompts require one or two examples")
    rendered: list[str] = []
    for label, payload in examples:
        if not label.strip() or "\n" in label:
            raise ValueError("example labels must be non-blank single lines")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("structured examples must be valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("structured examples must be JSON objects")
        compact = json.dumps(
            decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        rendered.append(f"{label.strip()}\nExample JSON output:\n{compact}")
    return (
        f"{STRUCTURED_REPLY_FORMAT}\n{STRUCTURED_EXAMPLE_NOTICE}\n"
        + "\n".join(rendered)
    )

SOURCE_EVALUATOR_SYSTEM_PROMPT = (
    "You are the source evaluator of a multi-agent research system. You "
    "judge how much each source behind the collected findings can be "
    "trusted.\n"
    "You are shown one dossier per source: its URL, its title, the "
    "sub-topics it was cited for, an excerpt of every finding drawn from "
    "it, and any reputation previous sessions recorded for it.\n"
    "Score only what the dossier supports. Do not assume a publisher you "
    "were not told about, and never invent a source that is not listed. "
    "Return one score object per listed source, using the exact url string "
    "from its dossier."
)

SOURCE_SCORING_INSTRUCTION = (
    "For each listed source return authority, recency, and relevance as "
    "numbers between 0 and 1, plus a one- or two-sentence rationale.\n"
    "All three scores use one direction: 0.0 is weakest and 1.0 is strongest. "
    "Use intermediate values in proportion to the evidence in the dossier.\n"
    "authority: how far the publisher is the body that produced, measured, "
    "judged or announced the information this source is cited for — a "
    "statistical agency for its statistic, a maker for its own release notes or "
    "prices, an independent tester or reviewer for a rating, a court or "
    "regulator for a rule, a peer-reviewed venue for a study — and how far its "
    "editorial process justifies trust. A page that repeats another body's "
    "information is a relay and scores as one; a body rating its own product is "
    "self-interested for that rating, not for its own prices or notes. "
    "Anonymous posts and content farms score low.\n"
    "A document that declares itself teaching material, a simulation or "
    "exercise, a sample or model answer, simplified or adapted content, "
    "content based on an encyclopedia's or a chatbot's entries, or content "
    "written or generated by AI (its Self-description line, or its own "
    "words) is a relay for that content: source_role derivative, and its "
    "authority is a relay's — of the source it names, where it names one, "
    "and of unnamed material where it names none — however its host reads. "
    "A body's own simplified or illustrative rendering of its own rule, "
    "figures or notes is that body's statement, not a relay.\n"
    "recency: how current the source's own content is for this question, "
    "judged from the dates, versions, and events its excerpts mention — "
    "not from when this system retrieved it. Use 0.5 when the excerpts "
    "carry no dating signal at all. A clearly current version scores high; "
    "a demonstrably superseded source on a time-sensitive topic scores low. "
    "A newly published document repeating old figures is not current, and an "
    "older rule that still governs is not stale.\n"
    "relevance: how directly the excerpts answer the sub-topics the source "
    "was cited for, rather than merely mentioning them.\n"
    "Relevance is judged on the document's own subject, not its title: a "
    "document about a different event, period, body or product than the "
    "sub-topics it was cited for has low relevance, whatever its title "
    "says.\n"
    "Then describe the source itself, from the dossier alone:\n"
    "source_role: original_report, independent_research, derivative, "
    "company_statement, mixed, or unknown — what the document is. Use "
    "derivative when it repeats another organisation's statistic, study, or "
    "dataset without adding its own measurement; independent_research when "
    "it did its own; mixed when it does both; and unknown when the dossier "
    "does not show who published it.\n"
    "transport_relation: original, mirror, syndication, or unknown — how the "
    "copy you were shown reached its host. A mirror is the same work served "
    "from elsewhere: it is still usable evidence and it is not a second "
    "publisher.\n"
    "self_interest: none, potential, evidenced, or unknown — whether the "
    "publisher stands to gain from what the document asserts. A company "
    "writing about its own product is evidenced.\n"
    "publication_date, data_period, forecast_horizon, effective_date: for each, "
    "the date the document states together with the words it states it in, as "
    '{"value": ..., "quote": ...}. quote is copied verbatim from the dossier '
    "and value is that same date written as YYYY, YYYY-MM, YYYY-MM-DD, or two "
    "of those joined by a dash for a period the document itself states as a "
    "period. The two must agree: the quote has to state the value's own "
    "numbers, in the same order, and a quote naming two ends is never returned "
    "as one of them. When the document spells a date in words, return the "
    "full date the words name — \"January 15, 2026\" is 2026-01-15 and "
    "\"January 2025\" is 2025-01 — because a date written in words is as "
    "precise as one written in digits, and never reduce it to a coarser one: "
    "return the date the words name and no finer one, so a document that "
    "names only its year is not given a month or a day. publication_date is "
    "when it was published, data_period is "
    "the period its data cover, forecast_horizon is the future period a "
    "projection refers to, and effective_date is when a rule or version took "
    "effect. Return null when the document does not state that date, never "
    "infer a date from another field or from what you know about the topic, "
    "give each field its own quote, and return null when two phrases could "
    "equally be the one asked for. An identifier, a URL, or a file path is "
    "not a date.\n"
    "freshness_status: current, superseded, stale_data, projection, "
    "effective, or unknown — which of those dates the recency judgement "
    "rests on. Use effective for an old rule that still governs, stale_data "
    "for a new publication repeating old figures, and projection for a "
    "forecast beyond its data.\n"
    "methods_score: 0.0 to 1.0 for how well the document explains how its "
    "result was produced, or null when the dossier does not show it.\n"
    "issuer, doi, year, report_number: the publisher, identifier, and year "
    "the document itself states. Copy them only from the dossier. A name "
    "that is merely mentioned — the subject of the article — is not its "
    "publisher, and an identifier you were not shown is not evidence.\n"
    "derived_from: the DOIs or report numbers the document says its data or "
    "figures come from — the report a story repeats, the dataset an analysis "
    "uses — copied only from the dossier; an empty list when it names none.\n"
    "The combined score is computed for you and is not yours to return.\n"
    "rationale: name the concrete signals you used. Never restate the "
    "numbers alone."
)


class AgentTask(ContractModel):
    """What one agent has been asked to do on this run."""

    instruction: str = Field(min_length=1)
    guidance: str = ""


def render_scratchpad(entries: Sequence[ScratchpadEntry]) -> str:
    """Render scratchpad notes oldest first, one kind-prefixed line each.

    Entry content is whitespace-collapsed onto a single line so that
    multi-line thoughts or final answers (which may contain Markdown
    headers) can never break the surrounding prompt's section grammar.
    """
    if not entries:
        return "(no notes yet)"
    return "\n".join(
        f"- [{entry.kind}] {' '.join(entry.content.split())}" for entry in entries
    )


def render_react_messages(
    *,
    system_prompt: str,
    task: AgentTask,
    scratchpad: Sequence[ScratchpadEntry],
    iteration: int,
    max_iterations: int,
    decision_context: str = "",
) -> list[ChatMessage]:
    """Build the two messages one ReAct turn sends to the provider.

    The tools travel as provider-native function definitions on the request
    itself, so this text carries no catalogue and no action envelope.
    """
    if not system_prompt.strip():
        raise ValueError("system_prompt must not be blank")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if iteration < 1:
        raise ValueError("iteration must be at least 1")
    if iteration > max_iterations:
        raise ValueError("iteration must not exceed max_iterations")

    sections = [f"## Task\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Guidance\n{task.guidance}")
    # D10 (S4): the notes grow oldest first and the acquisition context changes
    # every turn, so the notes come first and each turn shares the last one's prefix.
    sections.append(f"## Notes so far\n{render_scratchpad(scratchpad)}")
    if decision_context.strip():
        sections.append(f"## Acquisition context\n{decision_context}")
    budget = f"Iteration {iteration} of {max_iterations}."
    if iteration == max_iterations:
        # D10 (S3): a tool called on the last turn is never reasoned over.
        budget += (
            " This is the last iteration: return the final answer now without "
            "calling a tool."
        )
    sections.append(f"## Budget\n{budget}")
    sections.append(f"## How to respond\n{NATIVE_REACT_RESPONSE_CONTRACT}")

    return [
        ChatMessage(role="developer", content=system_prompt),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def render_memory_guidance(memory_context: MemorySnapshot) -> str:
    """Render recalled long-term memory as agent-facing context.

    Returns an empty string when nothing was recalled, so callers can pass
    the result straight into ``AgentTask.guidance`` and have the guidance
    section disappear from the prompt.
    """
    lines: list[str] = []
    if memory_context.similar_findings:
        lines.append(
            f"{len(memory_context.similar_findings)} finding(s) recalled "
            "from previous sessions:"
        )
        lines.extend(
            f"- {summarize_text(finding.content)} ({finding.source_url})"
            for finding in memory_context.similar_findings
        )
    if memory_context.suggested_strategies:
        lines.append("Strategies that worked before:")
        lines.extend(
            f"- {summarize_text(strategy)}"
            for strategy in memory_context.suggested_strategies
        )
    return "\n".join(lines)


def render_source_dossier(
    group: SourceGroup,
    *,
    index: int,
    reputation: float | None,
    excerpt_chars: int = 400,
) -> str:
    """Render everything the model may use to score one source.

    Written as an explicit loop rather than a comprehension: each finding
    excerpt has to be clamped before interpolation, and Python 3.11
    f-strings cannot hold a multi-line call expression.
    """
    lines = [
        f"Source {index}: {group.url}",
        f"Title: {group.title}",
        f"Cited for: {', '.join(group.sub_topics) or 'no sub-topic'}",
        f"Findings drawn from it: {len(group.findings)}",
    ]
    if reputation is None:
        lines.append("Known reputation: none on record")
    else:
        lines.append(f"Known reputation: {reputation:.2f}")
    lines.append("Excerpts:")
    if not group.findings:
        lines.append("- (no findings)")
    for finding in group.findings:
        lines.append(f"- {summarize_text(finding.content, limit=excerpt_chars)}")
    return "\n".join(lines)


def render_read_dossier(
    dossier: ReadDossier,
    *,
    index: int,
    reputation: float | None,
    excerpt_chars: int = 400,
) -> str:
    """Render one source from the read itself, not from a finding's summary.

    The model is shown where the bytes were served from, when this run read
    them, whether the extraction was complete, and the document's own words.
    That is the whole authority for the identity, transport, role, and dating
    fields it is asked to report: a judgement about the document has to be
    about the document, and an assertion the dossier does not carry stays
    empty rather than becoming a recorded fact.

    When the read declares its own derivative or teaching kind (D1), that
    declaration is the dossier's second line -- ahead of everything else the
    model is shown about the document -- so the evaluator sees it before it
    forms any other judgement.
    """
    read = dossier.read
    lines = [f"Source {index}: {dossier.url}"]
    self_description = derivative_self_description(read)
    if self_description is not None:
        lines.append(f"Self-description: {self_description}")
    lines += [
        f"Title: {read.title}",
        f"Serving host: {dossier.serving_host}",
        f"Cited for: {', '.join(dossier.cited_sub_topics) or 'no sub-topic'}",
        f"Read at: {read.retrieved_at}",
        (
            "Extraction: complete"
            if read.extraction_complete
            else "Extraction: partial - only some of this document could be read"
        ),
    ]
    if reputation is None:
        lines.append("Known reputation: none on record")
    else:
        lines.append(f"Known reputation: {reputation:.2f}")
    lines.append("Text read:")
    if not dossier.excerpts:
        lines.append("- (no excerpt)")
    for excerpt in dossier.excerpts:
        lines.append(f"- {summarize_text(excerpt, limit=excerpt_chars)}")
    return "\n".join(lines)
