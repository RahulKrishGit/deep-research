"""Pure rendering of ReAct turns into provider messages.

Nothing here performs I/O, reads a clock, or consults a random source, so a
rendered prompt is a deterministic function of its inputs and can be asserted
on directly in tests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.evidence import ReadDossier
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
    "a demonstrably superseded source on a time-sensitive topic scores low. "
    "A newly published document repeating old figures is not current, and an "
    "older rule that still governs is not stale.\n"
    "relevance: how directly the excerpts answer the sub-topics the source "
    "was cited for, rather than merely mentioning them.\n"
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
    "value at the precision it writes in digits — the year, for \"January 15, "
    "2026\" — because the value has to be in the quote. publication_date is "
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

# The packet path's own response contract. The legacy instruction above asks
# for a ``passages`` list the packet schema rejects and describes the verdicts
# in terms of what a loop retrieved; this one describes the record the model is
# actually handed back — ids, per-id assessments, and what each field decides.
ADJUDICATION_INSTRUCTION = (
    "Return one verdict for the claim, chosen from exactly these strings:\n"
    "verified: two of the listed passages each support the WHOLE claim, in the "
    "claim's own scope, from independent sources.\n"
    "unverified: the listed passages address the claim and none of them "
    "settles it.\n"
    "contradicted: a listed passage states something incompatible with the "
    "claim, in the claim's own scope.\n"
    "insufficient_evidence: the listed passages do not address the claim, or "
    "are too thin to judge.\n"
    "Also return confidence as a number between 0 and 1, one assessment row "
    "per candidate id you judged, the ids you selected, and a rationale.\n"
    "Each assessment row has evidence_id, stance, complete_support, "
    "scope_compatible, dependence, origin_group_id, and rationale.\n"
    "stance: supports when the passage states the claim, contradicts when it "
    "states something incompatible with it, unrelated when it does neither. A "
    "passage that refutes the claim is contradicts — never supports with "
    "complete_support false.\n"
    "complete_support: true only when the passage supports the WHOLE atomic "
    "claim rather than a part of it. It says nothing about a refutation.\n"
    "scope_compatible: true when the passage measures the same period, unit, "
    "and population as the claim.\n"
    "support_ids and contradiction_ids: every id you judged, each under the "
    "stance you gave it. Never cite a URL, never retype an excerpt, and never "
    "name an id that is not listed above."
)

# The per-passage dependence judgement an adjudication returns. The model
# names how each passage stands to the origin of its figure; local code decides
# what that allows, and only an origin the packet printed can be named.
ADJUDICATION_DEPENDENCE_INSTRUCTION = (
    "Every assessment also carries dependence, origin_group_id, and "
    "rationale.\n"
    "dependence: primary when the passage is its source's own measurement "
    "or statement of the figure; independent_analysis when it applies its "
    "own method to data; derivative when it repeats another work's figure; "
    "unknown when the passage does not show which. Any other value is read "
    "as unknown. Only primary and independent_analysis passages can "
    "corroborate a claim.\n"
    "origin_group_id: leave empty to keep the origin printed beside the "
    "passage. When the passage's figure comes from the origin printed "
    "beside another candidate — an analysis of that candidate's data — copy "
    "that origin exactly. Name no origin that is not printed above: an "
    "unlisted origin refuses the passage for corroboration.\n"
    "rationale: one sentence on why the passage has that dependence."
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
    "sections, answer rows when the frozen answer form asks for them, and "
    "uncertainty notes. Every statement you return is a point with exactly "
    "four fields: text, claim_ids, source_urls, and basis.\n"
    "executive_summary: three to six points answering the research question "
    "directly, naming what is settled and what is not, led by what the "
    "evidence establishes and which distinctions would change the answer. Do "
    "not open with a heading.\n"
    "ranked_constraints: the constraints a decision-maker must respect, most "
    "consequential first, as objects with constraint, "
    "deployment_mechanism, geography, claim_ids, and source_urls. State a "
    "mechanism or a geography only when the checked claims state it; write "
    "'not stated' otherwise. Never guess a jurisdiction, and never invent a "
    "rank the evidence does not support.\n"
    "A mechanism or a geography cell is checked against the evidence the row "
    "cites: the wording of a cell must come from that evidence, and a cell "
    "the evidence does not carry is replaced with 'not stated' and recorded. "
    "Never guess a jurisdiction.\n"
    "answer_rows: only when the answer form section asks for them. Each entry "
    "has subject, dimension, finding, claim_ids, and source_urls; the finding "
    "is a settled statement like any other. Return an empty list when the "
    "form does not ask for rows.\n"
    "sections: one object per theme worth its own heading, ordered as a "
    "reader should meet them, each with a short title and a points list.\n"
    "uncertainty_notes: what a reader should distrust and why - thin "
    "sourcing, conflicting evidence, questions the research did not reach. "
    "Separate not acquired, uncertain or conflicting, and outside scope. Do "
    "not print a figure here that the checked claims do not carry, and do not "
    "claim a read was truncated, denied, or missing unless this pass recorded "
    "it. Return an empty list when there is nothing to add.\n"
    "basis: empty for a statement that asserts no more than its evidence. If "
    "you convert a unit or do arithmetic, state the derivation here as the "
    "premises and the result; a figure that is not in the evidence is refused "
    "unless the basis you give is checked against it.\n"
    "claim_ids: copy the labels exactly as printed in the checked-claims "
    "packet (such as C001). A label that is not in that packet is refused.\n"
    "source_urls: copy urls exactly from the claims you cite. A url that is "
    "not on one of those claims is refused, and the point is lost.\n"
    "One point carries one statement. If a sentence makes three separate "
    "factual assertions, return three points - or one point only if all "
    "three share exactly the same claims and sources.\n"
    "Do not recommend an action outside the answer section the question "
    "asked for: a findings point that tells someone what to do is refused.\n"
    "Do not write Markdown headings, citation markers, or a source list "
    "inside any text field: the markers and the reference list are added for "
    "you from the urls you attach."
)

# Task 5's claim consolidation. The provider proposes which extracted atoms
# *might* state one fact; local code validates every proposal before anything
# merges, so this prompt may be generous — the refusals are what carry the
# guarantee, and a missed duplicate costs a row in the ledger while a false
# merge deletes evidence.
CLAIM_EQUIVALENCE_SYSTEM_PROMPT = (
    "You compare atomic factual claims extracted from one research pass and "
    "say which of them state the same fact in different words.\n"
    "The claims are numbered. Return pairs of numbers only.\n"
    "Compare what the claims assert, never how they are worded. Two claims "
    "that state one fact are a pair even when they use different vocabulary, "
    "different sentence order, or a differently formatted number — "
    "'1,200 MW' and '1200 MW' are the same figure. A claim that merely "
    "mentions the same subject as another is not a pair: the year, the "
    "observation period, the geography, the population, the unit, the "
    "percentage denominator, the attribution, whether the number is observed "
    "or projected, and whether the claim is negated all have to agree.\n"
    "Return an empty list when nothing is a duplicate. Never pair two claims "
    "you cannot put in one sentence without changing what either asserts."
)

CLAIM_EQUIVALENCE_INSTRUCTION = (
    "Return the duplicate pairs you found, as objects with left and right, "
    "each one a number from the list you were shown. Every pair you return "
    "is checked locally before anything merges, so return only pairs you "
    "are confident about."
)

# The schema version of the equivalence proposal above. A change to the
# fields the provider returns changes it.
CLAIM_EQUIVALENCE_SCHEMA_VERSION = "1"

# The schema version of one claim-verification verdict. Task 5's
# reverification cache key includes it: a verdict reached under a different
# verdict contract is not the verdict this pass would reach.
CLAIM_VERIFICATION_SCHEMA_VERSION = "1"

CRITIC_SYSTEM_PROMPT = (
    "You are the critic of a multi-agent research system. You judge one "
    "finished report and say what another research pass would have to fix.\n"
    "You have no tools and need none: the exact candidate is printed for you. "
    "Judge completeness against the research question, accuracy against the "
    "claim verdicts and the read excerpts, source diversity and strength "
    "against each source's quality score when scored or explicit evaluation "
    "status otherwise, and whether uncertainty is disclosed rather than "
    "hidden.\n"
    "Report what the material in front of you supports. Do not invent a gap "
    "to look thorough, and do not excuse a thin report to look agreeable."
)

# The review request offers NO tools, so its prompt must not mention any.
# Announcing tools the request cannot accept made the model emit DeepSeek
# tool-invocation markup into the message text, where local JSON validation
# rejected it: 16 of 30 first attempts in a measured shape probe. Task 8
# removed the tool path entirely, so this prompt is the agent's only one.
CRITIC_REVIEW_SYSTEM_PROMPT = (
    "You are the critic of a multi-agent research system, and the last editor "
    "before this report reaches a reader. You judge one finished report and "
    "say, precisely, what is wrong with it and what would fix it.\n"
    "Everything needed is printed below and nothing else is available: you "
    "have no tools for this request, so nothing may be looked up. The material "
    "is the "
    "research question, the frozen answer contract, every reader section in "
    "its own fenced block, every reader statement with the evidence ids "
    "behind it, the evidence targets and which of their required dimensions "
    "are still unanswered, the exact read excerpts this run registered, the "
    "deterministic quality snapshot and hard checks, canonical checked "
    "claims, each cited source's quality score when scored or its explicit "
    "evaluation status otherwise, and typed errors grouped by agent and "
    "stage. Judge that material alone, and never claim to have checked "
    "anything that is not printed here.\n"
    "A search result, a snippet, or a remembered claim is not evidence: only "
    "an exact excerpt of a successful read is, and those are listed with "
    "their read ids and locators.\n"
    "Judge completeness against the question, accuracy against the claim "
    "verdicts and the excerpts, source diversity and strength against the "
    "source quality signals, and whether uncertainty is disclosed rather "
    "than hidden.\n"
    "Score the whole answer against the question. Deterministic hard checks "
    "can block acceptance; they can never earn a high score for a report the "
    "evidence does not support.\n"
    "Report what the material in front of you supports. Do not invent a gap "
    "to look thorough, and do not excuse a thin report to look agreeable."
)

# The typed defect vocabulary. Both lists are normative: the kind names what
# is wrong, and the action names the one thing that would fix it. Task 9
# routes on the action, so an action this contract does not name is a defect
# nobody can repair.
CRITIQUE_GAP_KINDS = (
    "coverage (a required obligation the report does not answer), "
    "missing_support (a statement with no evidence behind it), "
    "acquisition (required evidence no read supplied), "
    "identity (sources that are not actually independent, or not what they "
    "claim to be), "
    "contradiction (recorded evidence that disagrees, unresolved), "
    "semantic_duplicate (the same fact asserted twice), "
    "source_quality (cited evidence too weak for the claim it carries), "
    "mechanism (how or why is unstated), "
    "freshness (the evidence is older than the question allows), "
    "presentation (the answer is hard to read or repeats itself)"
)

CRITIQUE_GAP_ACTIONS = (
    "extend_plan (the question asks for something no planned target covers), "
    "acquire (evidence must be found for a named target), "
    "assess_source (a cited source must be evaluated), "
    "adjudicate (evidence that disagrees must be settled), "
    "consolidate (duplicate facts must be merged), "
    "synthesize (the answer must be rewritten or re-derived)"
)

CRITIQUE_INSTRUCTION = (
    "Return a score, typed gap objects, the unsupported claims, the queries a "
    "further pass should run, and a rationale.\n"
    "score: an integer from 1 to 10. 1 is unusable; 10 answers the question "
    "completely from strong, diverse, well-cited sources. Score the whole "
    "answer against the question, from its weakest load-bearing element. "
    "Length, confident prose, and a long source list are not quality.\n"
    "gaps: list a gap only when it is a real, material defect of this report. "
    "Each gap is an object with gap_id, target_ids, claim_cluster_ids, "
    "statement_ids, kind, severity, repair_action, problem, and "
    "recommended_queries. Copy every id exactly from the statement, target, "
    "or cluster lists above; never invent one, and never infer an id from a "
    "title or from the problem text.\n"
    f"kind is one of: {CRITIQUE_GAP_KINDS}.\n"
    f"repair_action is one of: {CRITIQUE_GAP_ACTIONS}.\n"
    "severity is critical, major, or minor. critical and major mean the "
    "defect must be closed before this report can be accepted; minor is a "
    "real but editorial observation, and one minor defect is not a reason to "
    "research again.\n"
    "Every critical or major gap must name at least one target, statement, "
    "or cluster it affects. An acquire gap must also name the target whose "
    "evidence obligation is missing: acquisition is per obligation, so a "
    'statement reference alone does not say what is owed. When the question '
    "itself asks for something no planned target covers, name "
    '"question" in target_ids with repair_action extend_plan; do not '
    "fabricate a target id.\n"
    "recommended_queries: concrete search queries, and only on an acquisition "
    "gap. A gap that repairs by extend_plan, assess_source, adjudicate, "
    "consolidate, or synthesize runs no search, so it carries no queries. "
    '"Improve the quality" and "find more sources" are not actionable gaps; '
    "say what is missing and which action can supply it. Return an empty gap "
    "list when the report is materially complete.\n"
    "unsupported_claims: statements presented as fact that are neither "
    "clearly attributed to one of the report's cited sources nor backed by a "
    "verified claim. Do not mark a cited statement unsupported solely because "
    "it lacks a separate verified-claim entry: the claim digest is deliberately "
    "partial, so absence from it is not evidence of unsupportedness. A contrary "
    "claim verdict or a read excerpt that disagrees still makes a statement "
    "unsupported, however it is cited. Quote or closely paraphrase each one.\n"
    "recommended_queries at the top level: the same acquisition queries, in "
    "the order they should be run. Leave it empty when no acquisition is "
    "needed.\n"
    "rationale: two to four sentences naming the concrete signals behind "
    "the score. Never restate the score alone.\n"
    "Do not decide whether research continues — this system computes that "
    "from your score, your gaps, and the remaining budget."
)

# The one repair request. It is deliberately not a second review: the model is
# told the packet is unchanged and asked only to correct the reply's shape, so
# a repaired review cannot quietly become a different judgement.
CRITIQUE_REPAIR_INSTRUCTION = (
    "Return the same five-field JSON object again, corrected. Do not re-review "
    "the report, do not change a score or a gap because of this request, and "
    "do not add commentary: the report, the evidence, and the packet "
    "fingerprint are unchanged. Return exactly one JSON object with no text "
    "before or after it."
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
    if decision_context.strip():
        sections.append(f"## Acquisition context\n{decision_context}")
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
    """
    read = dossier.read
    lines = [
        f"Source {index}: {dossier.url}",
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
    limit: int | None = None,
) -> str:
    """Render the labelled checked-claim packet a report draft cites.

    Each line is addressable: the model returns the label, and the validator
    resolves it against the same registry, so a claim can never be cited by a
    string that does not name a checked claim. Claims the packet's budget left
    out are counted here rather than silently missing, so the model knows the
    packet is partial.

    ``limit`` is ``None`` by default and that is the point: this is what the
    writer is *shown*, so a claim reaches it as the claim it can cite. The
    caller's character budget bounds how many claims fit and this goes on to
    name the ones that did not, which is a faithful packet; clamping each
    claim to a rendered-cell width was not, and a writer handed a cut sentence
    reported that the claim itself was recorded only in part. ``limit`` stays
    available for a caller that genuinely wants a bounded digest.
    """
    if omitted < 0:
        raise ValueError("omitted must not be negative")
    lines: list[str] = []
    for label, claim in packet:
        text = (
            summarize_text(claim.text, limit=limit)
            if limit is not None
            else claim.text
        )
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
