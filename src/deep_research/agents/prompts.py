"""Pure rendering of ReAct turns into provider messages.

Nothing here performs I/O, reads a clock, or consults a random source, so a
rendered prompt is a deterministic function of its inputs and can be asserted
on directly in tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import summarize_text
from deep_research.agents.toolset import ToolDescriptor
from deep_research.memory.entries import ScratchpadEntry
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    ContractModel,
    Finding,
    MemorySnapshot,
    ScoredSource,
)

REACT_RESPONSE_CONTRACT = (
    "Respond with one decision.\n"
    'Set action to "use_tool" to call exactly one listed tool: put its name in '
    "tool_name and its arguments in tool_input_json as a JSON object string "
    '(for example {"value": "hello"}). Use "{}" when the tool takes no '
    "arguments, and leave final_answer empty.\n"
    'Set action to "finish" when you can answer without another tool call: put '
    "the answer in final_answer and leave tool_name empty. Use \"{}\" for "
    "tool_input_json when finishing.\n"
    "Always explain the choice in thought."
)

SOURCE_EVALUATOR_SYSTEM_PROMPT = (
    "You are the source evaluator of a multi-agent research system. You "
    "judge how much each source behind the collected findings can be "
    "trusted.\n"
    "You are shown one dossier per source: its URL, its title, the "
    "sub-topics it was cited for, an excerpt of every finding drawn from "
    "it, a corroboration score this system already computed, and any "
    "reputation previous sessions recorded for it.\n"
    "Score only what the dossier supports. Do not assume a publisher you "
    "were not told about, and never invent a source that is not listed. "
    "Return one score object per listed source, using the exact url string "
    "from its dossier."
)

SOURCE_SCORING_INSTRUCTION = (
    "For each listed source return authority, recency, and relevance as "
    "numbers between 0 and 1, plus a one- or two-sentence rationale.\n"
    "authority: how much the publisher's identity, expertise, and "
    "editorial process justify trust. Peer-reviewed venues, standards "
    "bodies, and primary institutional publications score high; anonymous "
    "posts, content farms, and vendor marketing score low.\n"
    "recency: how current the source's own content is for this question, "
    "judged from the dates, versions, and events its excerpts mention — "
    "not from when this system retrieved it. Use 0.5 when the excerpts "
    "carry no dating signal at all.\n"
    "relevance: how directly the excerpts answer the sub-topics the source "
    "was cited for, rather than merely mentioning them.\n"
    "Corroboration is computed for you and is not yours to return. Neither "
    "is the combined score.\n"
    "rationale: name the concrete signals you used. Never restate the "
    "numbers alone."
)

FACT_CHECKER_SYSTEM_PROMPT = (
    "You are the fact checker of a multi-agent research system. You verify "
    "exactly one claim at a time against sources independent of the ones "
    "that made it.\n"
    "Use web_search to find sources that could confirm or refute the "
    "claim, web_scraper to read a promising page, document_reader for PDFs "
    "and data files, and query_memory to recall what previous sessions "
    "established.\n"
    "A page from the claim's own publisher is not independent "
    "corroboration; look for a different organisation. Actively look for "
    "evidence that the claim is wrong, not only evidence that it is "
    "right.\n"
    "Finish once you have retrieved enough independent material to judge "
    "the claim, or once no further source is worth retrieving."
)

CLAIM_EXTRACTION_SYSTEM_PROMPT = (
    "You extract the major factual claims from a completed research pass. "
    "A claim is a specific, checkable statement of fact — a number, a "
    "date, an attribution, a causal assertion — not a summary, an opinion, "
    "or a restatement of the research question.\n"
    "Every claim must come from the retrieved findings you are shown, and "
    "every source URL you attach must be one of the URLs listed with those "
    "findings. Return an empty list rather than inventing a claim or a URL."
)

CLAIM_EXTRACTION_INSTRUCTION = (
    "Return the most load-bearing factual claims in the findings — the "
    "ones a reader would most want checked before trusting the report.\n"
    "Write each claim as one self-contained sentence that can be checked "
    "without reading the rest of the findings. Merge findings that state "
    "the same fact into a single claim carrying every source URL that "
    "stated it.\n"
    "Prefer claims drawn from sources marked LOW CONFIDENCE: those are the "
    "ones most in need of independent checking.\n"
    "Attach at least one source URL to every claim, copied exactly from "
    "the findings. Return an empty list when the findings support no "
    "checkable claim."
)

CLAIM_VERIFICATION_SYSTEM_PROMPT = (
    "You judge one claim against the evidence a verification loop actually "
    "retrieved. Report only what that evidence states.\n"
    "If the evidence does not settle the claim, say so. Never invent "
    "confidence, and never treat the claim's own sources as confirmation "
    "of themselves."
)

CLAIM_VERIFICATION_INSTRUCTION = (
    "Return one verdict for the claim, chosen from exactly these "
    "strings:\n"
    "verified: independent retrieved evidence states the claim.\n"
    "unverified: independent evidence was retrieved but none of it "
    "addresses the claim either way.\n"
    "contradicted: independent retrieved evidence states something "
    "incompatible with the claim.\n"
    "insufficient_evidence: nothing independent was retrieved, or what was "
    "retrieved is too thin to judge.\n"
    "Also return confidence as a number between 0 and 1, an evidence list "
    "quoting or closely paraphrasing the independent passages supporting "
    "your verdict, and a contradictions list holding every independent "
    "passage that conflicts with the claim. Leave a list empty rather than "
    "filling it with restatements of the claim."
)


class AgentTask(ContractModel):
    """What one agent has been asked to do on this run."""

    instruction: str = Field(min_length=1)
    guidance: str = ""


def render_tool_catalog(descriptors: Sequence[ToolDescriptor]) -> str:
    """Render the allowed tools as one line each, in declaration order."""
    if not descriptors:
        return "(no tools available)"
    return "\n".join(
        f"- {descriptor.name}: {descriptor.description} "
        f"Arguments: {json.dumps(descriptor.input_schema, sort_keys=True)}"
        for descriptor in descriptors
    )


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
    descriptors: Sequence[ToolDescriptor],
    scratchpad: Sequence[ScratchpadEntry],
    iteration: int,
    max_iterations: int,
) -> list[ChatMessage]:
    """Build the two messages one ReAct turn sends to the provider."""
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
    sections.append(f"## Tools\n{render_tool_catalog(descriptors)}")
    sections.append(f"## Notes so far\n{render_scratchpad(scratchpad)}")
    sections.append(f"## Budget\nIteration {iteration} of {max_iterations}.")
    sections.append(f"## Response contract\n{REACT_RESPONSE_CONTRACT}")

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
    corroboration: float,
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
        f"Corroboration (computed): {corroboration:.2f}",
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


def render_finding_digest(
    findings: Sequence[Finding],
    *,
    limit: int = 200,
) -> str:
    """Render findings as one numbered, sub-topic-tagged line each."""
    lines: list[str] = []
    for position, finding in enumerate(findings, start=1):
        content = summarize_text(finding.content, limit=limit)
        lines.append(
            f"{position}. [{finding.related_sub_topic}] {content} "
            f"({finding.source_url})"
        )
    return "\n".join(lines) or "(no findings)"


def render_source_quality(sources: Sequence[ScoredSource]) -> str:
    """Render scored sources so weak ones are visible in a prompt."""
    lines: list[str] = []
    for source in sources:
        flag = " (LOW CONFIDENCE)" if source.low_confidence else ""
        lines.append(f"- {source.url}: {source.overall_score:.2f}{flag}")
    return "\n".join(lines) or "(no sources scored)"
