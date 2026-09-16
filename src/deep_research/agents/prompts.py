"""Pure rendering of ReAct turns into provider messages.

Nothing here performs I/O, reads a clock, or consults a random source, so a
rendered prompt is a deterministic function of its inputs and can be asserted
on directly in tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.identity import merge_source_snapshot
from deep_research.agents.sources import SourceGroup, normalize_source_url
from deep_research.agents.steps import summarize_text
from deep_research.memory.entries import ScratchpadEntry
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Finding,
    MemorySnapshot,
    ScoredSource,
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
    "authority: how much the publisher's identity, expertise, and "
    "editorial process justify trust. Peer-reviewed venues, standards "
    "bodies, and primary institutional publications score high; anonymous "
    "posts, content farms, and vendor marketing score low.\n"
    "recency: how current the source's own content is for this question, "
    "judged from the dates, versions, and events its excerpts mention — "
    "not from when this system retrieved it. Use 0.5 when the excerpts "
    "carry no dating signal at all. A clearly current version scores high; "
    "a demonstrably superseded source on a time-sensitive topic scores low.\n"
    "relevance: how directly the excerpts answer the sub-topics the source "
    "was cited for, rather than merely mentioning them.\n"
    "The combined score is computed for you and is not yours to return.\n"
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
    "established. Search results are discovery leads, never evidence: read "
    "the page or document before asking for a verdict.\n"
    "A page from the claim's own publisher is not independent "
    "corroboration; look for a different organisation. Actively look for "
    "evidence that the claim is wrong, not only evidence that it is "
    "right.\n"
    # Measured: the verification loops made 146 web_search calls against about
    # 31 reads, and a claim whose loop read nothing independent is recorded
    # ``insufficient_evidence`` with no verdict call at all — so searches spent
    # without reading cost the claim its verdict. Same pathology the researcher
    # had, and the same instruction fixes it.
    "Spend your calls on reading, not on repeating searches. After a search, "
    "read the most promising result before searching again, and keep "
    "alternating: only a page or document you have actually read can settle "
    "the claim, so a verdict is impossible without one. Prefer primary "
    "documents — PDFs, filings, datasets and government or laboratory reports "
    "— which are published to be read and are far likelier to load than a "
    "publisher's article page. If a publisher refuses automated access to a "
    "page, do not try that page or that host again; find the same material as "
    "a document or from a different organisation.\n"
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
    "of themselves. Every passage must identify the read URL, source title, "
    "locator, bounded excerpt, and whether it supports or contradicts the "
    "claim. Search-result URLs alone are not passages."
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
    "Also return confidence as a number between 0 and 1 and a passages list. "
    "Each passage has source_url, source_title, locator, excerpt, and "
    "stance (supports or contradicts). Quote or closely paraphrase only "
    "independent read-bearing passages, and leave the list empty rather than "
    "filling it with restatements of the claim."
)

SYNTHESIZER_SYSTEM_PROMPT = (
    "You are the synthesizer of a multi-agent research system. You write "
    "the prose of the final report from evidence this system already "
    "collected and checked.\n"
    "You are shown the research question, every checked claim with its "
    "verdict, the labels that address those claims, the retrieved findings, "
    "each source's quality score when scored or explicit evaluation status "
    "otherwise, and the limitations this pass already knows about.\n"
    "Every statement you return is a point: one short statement carrying the "
    "labels of the checked claims it rests on and the source urls those "
    "claims carry. A point with no checked claim, or one citing a url those "
    "claims do not carry, is refused and never reaches the report.\n"
    "Report only what that evidence states. Never invent a source, a number, "
    "a claim, or a label that is not in front of you, and never present a "
    "claim that was not verified as though it were settled.\n"
    "The report's headings, citation numbering, reference list, uncertainty "
    "grouping, limitations, and evidence ledger are assembled by this "
    "system. Write the prose; do not write the skeleton."
)

REPORT_INSTRUCTION = (
    "Return an executive summary, a ranked constraint list, findings "
    "sections, and uncertainty notes. Every statement you return is a point "
    "with exactly three fields: text, claim_ids, and source_urls.\n"
    "executive_summary: three to six points answering the research question "
    "directly, naming what is settled and what is not. Do not open with a "
    "heading.\n"
    "ranked_constraints: the constraints a decision-maker must respect, most "
    "consequential first, as objects with constraint, "
    "deployment_mechanism, geography, claim_ids, and source_urls. State a "
    "mechanism or a geography only when the checked claims state it; write "
    "'not stated' otherwise. Never guess a jurisdiction.\n"
    "The mechanism and geography cells have no structured provenance field in "
    "this Task 6 contract: their semantic grounding is provider-only and not "
    "structurally validated locally. Treat them as provider-attested prose, "
    "and use 'not stated' whenever the supplied evidence does not say.\n"
    "sections: one object per theme worth its own heading, ordered as a "
    "reader should meet them, each with a short title and a points list.\n"
    "uncertainty_notes: what a reader should distrust and why — thin "
    "sourcing, conflicting evidence, questions the research did not reach. "
    "This is the one place source-free text belongs. Return an empty list "
    "when there is nothing to add.\n"
    "claim_ids: copy the labels exactly as printed in the checked-claims "
    "packet (such as C001). A label that is not in that packet is refused.\n"
    "source_urls: copy urls exactly from the claims you cite. A url that is "
    "not on one of those claims is refused, and the point is lost.\n"
    "One point carries one statement. If a sentence makes three separate "
    "factual assertions, return three points — or one point only if all "
    "three share exactly the same claims and sources.\n"
    "Do not write Markdown headings, citation markers, or a source list "
    "inside any text field: the markers and the reference list are added for "
    "you from the urls you attach."
)

CRITIC_SYSTEM_PROMPT = (
    "You are the critic of a multi-agent research system. You judge one "
    "finished report and say what another research pass would have to fix.\n"
    "Use web_search to spot-check a suspected gap or a figure that looks "
    "wrong, and query_memory to compare this report against what previous "
    "sessions established. Finish without calling a tool when the report "
    "and the evidence summary are enough to judge.\n"
    "Judge completeness against the research question, accuracy against the "
    "claim verdicts, source diversity and strength against each source's "
    "quality score when scored or explicit evaluation status otherwise, and "
    "whether uncertainty is disclosed rather than hidden.\n"
    "Report what the evidence in front of you supports. Do not invent a gap "
    "to look thorough, and do not excuse a thin report to look agreeable."
)

# The review request offers NO tools, so its prompt must not mention any.
# Announcing tools the request cannot accept made the model emit DeepSeek
# tool-invocation markup into the message text, where local JSON validation
# rejected it: 16 of 30 first attempts in a measured shape probe. This prompt
# is for the single structured judgement only; the tool-aware prompt above
# belongs to the ReAct spot-check loop, which does offer the tools.
CRITIC_REVIEW_SYSTEM_PROMPT = (
    "You are the critic of a multi-agent research system. You judge one "
    "finished report and say what another research pass would have to fix.\n"
    "Everything needed is printed below: the research question, every reader "
    "report section in its own fenced block, the deterministic quality "
    "snapshot, the sub-topics that were planned, canonical checked claims, "
    "each cited source's quality score when scored or its explicit "
    "evaluation status otherwise, and typed errors grouped by "
    "agent and stage. Judge "
    "that material alone.\n"
    "Judge completeness against the research question, accuracy against the "
    "claim verdicts, source diversity and strength against the source quality "
    "signals, and whether uncertainty is disclosed rather than hidden.\n"
    "Report what the evidence in front of you supports. Do not invent a gap "
    "to look thorough, and do not excuse a thin report to look agreeable."
)

CRITIQUE_INSTRUCTION = (
    "Return a score, targetable gap objects, the unsupported claims, the "
    "queries a further pass should run, and a rationale.\n"
    "score: an integer from 1 to 10. 1 is unusable; 10 answers the question "
    "completely from strong, diverse, well-cited sources.\n"
    "gaps: list a gap only when closing it would materially change the "
    "answer to the research question. Each gap is an object with "
    "coverage_id, problem, and recommended_queries. Copy coverage_id "
    "exactly from a planned sub-topic; use null when the gap is global or "
    "you cannot identify one from the plan. Never infer a coverage_id from a "
    "sub-topic title or from the problem text. A missing nicety is not a gap. "
    "Return an empty list when the report is materially complete.\n"
    "unsupported_claims: statements presented as fact that are neither "
    "clearly attributed to one of the report's cited sources nor backed by a "
    "verified claim. Do not mark a cited statement unsupported solely because "
    "it lacks a separate verified-claim entry: the claim digest is deliberately "
    "partial, so absence from it is not evidence of unsupportedness. A contrary "
    "claim verdict or spot-check evidence still makes a statement unsupported, "
    "however it is cited. Quote or closely paraphrase each one.\n"
    "recommended_queries: concrete search queries that would close the gaps "
    "you listed, in the order they should be run.\n"
    "rationale: two to four sentences naming the concrete signals behind "
    "the score. Never restate the score alone.\n"
    "Do not decide whether research continues — this system computes that "
    "from your score, your gaps, and the remaining budget."
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
    sections.append(f"## Notes so far\n{render_scratchpad(scratchpad)}")
    sections.append(f"## Budget\nIteration {iteration} of {max_iterations}.")
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


def render_source_quality(
    sources: Sequence[ScoredSource],
    *,
    max_sources: int = 36,
) -> str:
    """Render a bounded, canonical source-quality summary for prompts.

    Historical snapshots may still be handed to a renderer by callers that
    have not merged state yet. Canonicalize them here and retain the most
    relevant scored rows first, so prompt size is controlled without printing
    duplicate URLs or pretending an unscored source has a numeric quality.
    """
    if max_sources < 1:
        raise ValueError("max_sources must be at least 1")
    canonical = merge_source_snapshot([], sources)
    ranked = sorted(
        enumerate(canonical),
        key=lambda item: (
            item[1].evaluation_status != "scored",
            -(
                item[1].relevance_score
                if item[1].relevance_score is not None
                else -1.0
            ),
            -(
                item[1].overall_score
                if item[1].overall_score is not None
                else -1.0
            ),
            item[0],
        ),
    )[:max_sources]
    lines: list[str] = []
    for _, source in ranked:
        if source.overall_score is None:
            lines.append(
                f"- {normalize_source_url(source.url)}: "
                f"status={source.evaluation_status}"
            )
            continue
        flag = " low_confidence=true" if source.low_confidence else ""
        lines.append(
            f"- {normalize_source_url(source.url)}: "
            f"score={source.overall_score:.2f} "
            f"status={source.evaluation_status}{flag}"
        )
    return "\n".join(lines) or "(no sources scored)"


def render_claim_digest(
    claims: Sequence[Claim],
    *,
    limit: int = 240,
) -> str:
    """Render checked claims as one verdict-tagged, cited line each."""
    lines: list[str] = []
    for position, claim in enumerate(claims, start=1):
        text = summarize_text(claim.text, limit=limit)
        urls = ", ".join(claim.source_urls)
        lines.append(
            f"{position}. [{claim.verdict} {claim.confidence:.2f}] {text} "
            f"({urls})"
        )
    return "\n".join(lines) or "(no claims were checked)"


def render_report_claim_packet(
    packet: Sequence[tuple[str, Claim]],
    *,
    omitted: int = 0,
    limit: int = 240,
) -> str:
    """Render the labelled checked-claim packet a report draft cites.

    Each line is addressable: the model returns the label, and the validator
    resolves it against the same registry, so a claim can never be cited by a
    string that does not name a checked claim. Claims the packet's budget left
    out are counted here rather than silently missing, so the model knows the
    packet is partial.
    """
    if omitted < 0:
        raise ValueError("omitted must not be negative")
    lines: list[str] = []
    for label, claim in packet:
        text = summarize_text(claim.text, limit=limit)
        urls = ", ".join(claim.source_urls)
        coverage = ", ".join(claim.consumed_coverage_ids)
        suffix = f" coverage={coverage}" if coverage else ""
        lines.append(
            f"{label} [{claim.verdict} {claim.confidence:.2f}] {text} "
            f"({urls}){suffix}"
        )
    if omitted:
        lines.append(
            f"({omitted} further checked claim(s) were omitted for length; "
            "they cannot be cited by this draft.)"
        )
    return "\n".join(lines) or "(no claims were checked)"
