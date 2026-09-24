"""The Evidence Verifier (spec §5, D8): Figure Match, then the Context Check.

Figure Match is code: only the finding's snippet must be on its read (spec
§5.1 step 1). It never parses ``content`` and never searches a whole page for
a number, and it no longer judges any individual figure -- every figure of a
snippet-matched finding goes to the Context Check, which now carries that
whole judgement itself (D8, spec d34fd21): the AI rejects a figure its
snippet or passage does not actually state, and code enforces only that a
kept figure's ``evidence_words`` are on the page, that any correction is
supported by ``evidence_words`` alone, and every attribution rule (PD-8,
PD-18, PD-25).

The Context Check is one batched, tool-free provider call per
``CONTEXT_CHECK_BATCH_SIZE`` findings, at most ``CONTEXT_CHECK_CONCURRENCY``
batches in flight (shared with ``check_statements`` below). A finding whose
batch fails, or whose figure the reply never names, keeps a figure only when
deterministic code can confirm the snippet itself states it
(``figures.figure_in_text``); that kept figure carries its recorded fields
and the finding is marked ``context_unchecked``, and a figure the snippet
does not state is dropped with ``context_unavailable`` (PD-26, P1-2).

``check_statements`` (spec §6.2, D8) is the sibling check for the Report
Writer's drafted sentences: the same batching, checking whether a sentence
states only what the verified findings it cites actually carry.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import Field, ValidationError

from deep_research.agents.base import (
    AgentRun,
    AgentTask,
    BaseAgent,
    StructuredCompleter,
)
from deep_research.agents.errors import agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import (
    _ATTRIBUTION_PHRASES,
    _document_text,
    _identity_words,
    _opening_credits,
    cosmetic_text,
    excerpt_matches,
    neighbouring_passage_text,
    own_organisation_on_page,
    relay_attribution_on_page,
)
from deep_research.agents.figures import figure_in_text
from deep_research.agents.identity import deduplicate_findings, finding_fingerprint
from deep_research.agents.prompts import render_structured_reply_format
from deep_research.agents.sources import publisher_identity
from deep_research.agents.steps import ReActRun
from deep_research.agents.verified_facts import same_organisation
from deep_research.agents.wording import stated_role
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
)
from deep_research.utils.types import (
    ContractModel,
    FigureAttribution,
    FigureContext,
    FigureDropReason,
    FigureKind,
    FigureResult,
    Finding,
    FindingFigure,
    FindingVerification,
    ReadRecord,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
)

EVIDENCE_VERIFIER_NAME = "evidence_verifier"


@dataclass(frozen=True)
class FigureMatch:
    """Spec §5.1 step 1: is the finding's snippet on its page?

    D8: code no longer judges any individual figure here -- the Context
    Check's own AI verdict carries that whole judgement now.
    """

    read_found: bool
    snippet_on_page: bool


def read_text(read: ReadRecord) -> str:
    """The read's stored text: every passage, in document order."""
    return " ".join(read.passages.values())


def figure_match(finding: Finding, reads: Mapping[str, ReadRecord]) -> FigureMatch:
    """Is the snippet on its page? Every figure then goes to the Context Check."""
    read = reads.get(finding.read_id or "")
    if read is None:
        return FigureMatch(read_found=False, snippet_on_page=False)
    if not finding.snippet or not excerpt_matches(read_text(read), finding.snippet):
        return FigureMatch(read_found=True, snippet_on_page=False)
    return FigureMatch(read_found=True, snippet_on_page=True)


CONTEXT_CHECK_BATCH_SIZE = 5
CONTEXT_CHECK_CONCURRENCY = 8
CONTEXT_PASSAGE_CHARS = 3000

CONTEXT_CHECK_SYSTEM_PROMPT = (
    "You check the context of figures that a research system copied from web "
    "pages. For each figure you are shown the snippet it was copied from, the "
    "surrounding passage of the same page, and the fields the extractor recorded. "
    "You have no tools and no web access: judge only from the passage printed "
    "for that figure, and reject a figure whose snippet or passage does not "
    "actually state it."
)

CONTEXT_CHECK_INSTRUCTION = (
    "Return one entry in figures for every figure listed, naming it by its "
    "finding label and figure number. For each figure give:\n"
    "- period: the period the page says the figure applies to, as the page "
    "writes it (\"2024\", \"2025\", \"Q3 2025\"); repeat the recorded period when "
    "the page confirms it.\n"
    "- scope: the segment or basis the page says the figure covers, as the page "
    "writes it (\"all segments\", \"utility-scale\", \"utility, C&I, and "
    "residential\"), or null when the page states none.\n"
    "- attribution: own when the page states the figure as its publisher's own; "
    "relayed when the page credits another organisation for it (\"according "
    "to\", \"reported by\", a possessive); unattributed when the page states it "
    "without saying whose it is.\n"
    "- organisation: for own, the page's publisher as the page names itself; for "
    "relayed, the organisation the page credits, exactly as the page names it; "
    "null for unattributed.\n"
    "- kind: actual for a measured or reported outcome; forecast for a "
    "projection, plan, expectation or target.\n"
    "- evidence_words: the exact words of the passage that state this figure "
    "with this period and scope, copied character for character, one sentence "
    "or less. Words that are not on the page make the figure unusable.\n"
    "- verdict: confirm when the recorded period, scope and kind are right; "
    "correct when you changed any of them; reject when the snippet or "
    "passage does not actually state this figure, or states it for "
    "something else.\n"
    "- reason: one short sentence.\n"
    "A correction is kept only when your corrected wording appears in "
    "evidence_words. Never guess a period, a scope or an organisation the "
    "passage does not state."
)

_CONTEXT_CHECK_REPLY_EXAMPLES = (
    (
        "Example input: F01, figure 1: 12 percent | recorded period 2024 | recorded "
        "kind actual; passage \"The measured reduction was 12 percent in 2024, "
        "according to the Example Statistical Agency, across all classes.\"",
        '{"figures":[{"finding":"F01","figure":1,"period":"2024","scope":"all '
        'classes","attribution":"relayed","organisation":"Example Statistical '
        'Agency","kind":"actual","evidence_words":"The measured reduction was 12 '
        'percent in 2024, according to the Example Statistical Agency, across all '
        'classes","verdict":"correct","reason":"The page states the '
        'scope and credits the agency."}]}',
    ),
)


class FigureCheckDraft(ContractModel):
    """One figure's context as the model returns it, before code enforcement."""

    finding: str
    figure: int
    period: str | None = None
    scope: str | None = None
    attribution: FigureAttribution
    organisation: str | None = None
    kind: FigureKind
    evidence_words: str
    verdict: Literal["confirm", "correct", "reject"]
    reason: str


class ContextCheckDraft(ContractModel):
    """The provider-facing reply for one batch."""

    figures: list[FigureCheckDraft]


@dataclass(frozen=True)
class ContextItem:
    """One finding in a Context Check batch, with its page and Figure Match."""

    label: str
    finding: Finding
    read: ReadRecord
    passage: str
    match: FigureMatch
    issuer: str | None = None   # evaluated_issuer(...) for the read (PD-25)


class VerifiedFindings(ContractModel):
    """What one Evidence Verifier run judged. Never sent to the provider."""

    findings: list[Finding] = Field(default_factory=list)


# Separators a page's own title uses between its headline and its site or
# publisher name ("Article Title | Site Name", "Article Title - Publisher").
_TITLE_CREDIT_SEPARATOR = re.compile(r"\s*[|\u2013\u2014]\s*|\s+-\s+")

# One word of a cued run: a capitalised word carrying no full stop at all
# ("Utility Dive" -- the stop after "Dive" ends the sentence, it does not
# continue the name), or an initialism such as "U.S.", whose own dots sit
# inside the name ("U.S. Energy Information Administration").
_RUN_WORD = r"(?:(?:[A-Z]\.){2,}|[A-Z][\w&'-]*)"

# A short run of consecutive such words immediately after a naming cue,
# bounded so it can never run on into an unrelated sentence a scraped page
# joins with no punctuation ("Wood Mackenzie Limited Terms of use" -- the
# fifth word onward is never capitalised, so the run stops there).
_CAPITALIZED_RUN = re.compile(rf"(?:{_RUN_WORD}\s+){{0,4}}{_RUN_WORD}")
_LEADING_YEAR = re.compile(r"^(?:19|20)\d{2}\s*")

# Cues a page's own text marks its publisher's name with: the copyright
# symbol, the word, or the ASCII "(c)" a plain-text page carries.
_COPYRIGHT_CUES = (r"©", r"copyright", r"\(c\)")

# A candidate's trailing punctuation is the sentence's, not the name's: its
# final "." is dropped ("Wood Mackenzie." credits Wood Mackenzie) unless it
# is an initialism's own dot ("U.S." is not "US").
_TRAILING_PUNCTUATION = re.compile(r"[\s.,;:!?)\]}\"'\u201d\u2019]+$")
_INITIALS_TAIL = re.compile(r"(?:[A-Z]\.)+$")


def _title_credit_candidates(title: str) -> list[str]:
    """The read's own title, split on its own separators.

    "2025 ... | Wood Mackenzie" credits "Wood Mackenzie" without guessing at
    anything the title does not itself spell; every segment is offered as a
    candidate because a title can name its publisher either first or last.
    """
    return [part.strip() for part in _TITLE_CREDIT_SEPARATOR.split(title) if part.strip()]


def _cued_name_candidates(text: str, cues: Sequence[str]) -> list[str]:
    """A short run of capitalised words immediately after any of ``cues``."""
    candidates: list[str] = []
    pattern = re.compile(rf"(?:{'|'.join(cues)})\s*", re.IGNORECASE)
    for cue in pattern.finditer(text):
        tail = _LEADING_YEAR.sub("", text[cue.end() : cue.end() + 100].lstrip())
        match = _CAPITALIZED_RUN.match(tail)
        if match:
            candidates.append(match.group().strip())
    return candidates


def _stripped_name(value: str) -> str:
    """``value`` without the punctuation a sentence or title put after it."""
    value = value.strip()
    if _INITIALS_TAIL.search(value):
        return value
    return _TRAILING_PUNCTUATION.sub("", value)


def _name_prefixes(candidate: str) -> list[str]:
    """``candidate``'s word prefixes, shortest first.

    A page's own words introduce a name with more than the name itself: a
    colon headline states "Wood Mackenzie: US energy storage market hits
    record", and a cue's run can carry a description ("Published by Wood
    Mackenzie Research Team"). The name is the shortest prefix
    ``same_organisation`` confirms against the host -- never a longer string
    the page does not state as the organisation's name.
    """
    words = candidate.split()
    return [" ".join(words[:count]) for count in range(1, len(words) + 1)]


def page_owner(read: ReadRecord) -> str:
    """The page's own organisation, when the page's own words name it.

    PD-18's sibling for the byline itself: a title, opening (the same
    bounded first-3-passages/2000-char window authorship reads), or
    copyright line naming an organisation is used only when
    ``verified_facts.same_organisation`` confirms that name is the same
    organisation as the serving host -- never a name the page merely
    resembles ("energy.gov" is never credited as "EIA"), and never a name
    the page does not itself state. Falls back to the registrable host
    (``eia.gov``) when nothing on the page names it.

    The confirmed name is the shortest word prefix of whichever candidate
    matches, without the punctuation that followed it: "Utility Dive. All
    rights reserved" credits Utility Dive, and a headline that begins with
    the organisation's name credits the organisation, not the headline. The
    one prefix refused is the host's own bare label ("Energy" is one word of
    the Department of Energy's name, never a stand-in for the whole of it),
    which the fallback states anyway.
    """
    host = publisher_identity(read.resolved_url)
    label = host.split(".", 1)[0].casefold()
    candidates = [
        *_title_credit_candidates(read.title),
        *_cued_name_candidates(_opening_credits(read), _ATTRIBUTION_PHRASES),
        *_cued_name_candidates(_document_text(read), _COPYRIGHT_CUES),
    ]
    for candidate in candidates:
        words = candidate.split()
        for prefix in _name_prefixes(candidate):
            name = _stripped_name(prefix)
            if not name:
                continue
            # A shorter prefix that merely respells the host's own label is
            # the fallback under another capitalisation: "Energy" is one word
            # of the Department of Energy's name, never a stand-in for the
            # whole of it (the reading ``verified_facts._single_token``
            # records). A candidate that states the label alone still names
            # the page ("Battery report | EIA" on eia.gov).
            if len(prefix.split()) < len(words) and cosmetic_text(name) == label:
                continue
            if same_organisation(name, host):
                return name
    return host


def context_passage(read: ReadRecord, locator: str | None, snippet: str | None) -> str:
    """§5.2's bounded passage: the snippet's passage and its neighbours, centred on it."""
    text = neighbouring_passage_text(read, locator or "") or read_text(read)
    if len(text) <= CONTEXT_PASSAGE_CHARS:
        return text
    anchor = text.casefold().find((snippet or "")[:40].casefold())
    start = max(0, anchor - CONTEXT_PASSAGE_CHARS // 2)
    return text[start : start + CONTEXT_PASSAGE_CHARS]


def evaluated_issuer(sources: Sequence[ScoredSource], read: ReadRecord) -> str | None:
    """PD-25: the issuer the Source Evaluator validated for this read, if any.

    ``identity_anchors`` holds only the anchors the read was shown to evidence,
    so this name is the page's own organisation, not a guess from its host.
    """
    urls = {read.requested_url, read.resolved_url}
    for source in sources:
        if source.url in urls:
            anchor = source.identity_anchors.get("issuer")
            if isinstance(anchor, list):
                anchor = next(iter(anchor), None)
            if isinstance(anchor, str) and anchor.strip():
                return anchor.strip()
            return None
    return None


def _owns_page(read: ReadRecord, organisation: str, issuer: str | None) -> bool:
    """PD-25 first (the validated issuer names it), then PD-18 (code confirms it)."""
    if issuer and _identity_words(issuer) == _identity_words(organisation):
        return True
    return own_organisation_on_page(read, organisation)


def resolve_attribution(
    *,
    proposed: FigureAttribution | None,
    organisation: str | None,
    finding: Finding,
    read: ReadRecord,
    issuer: str | None,
) -> tuple[FigureAttribution, str]:
    """PD-8: the Context Check proposes, the page's own words decide.

    ``issuer`` is ``evaluated_issuer(...)`` for the read (PD-25), or ``None``.
    """
    name = (organisation or "").strip()
    if proposed == "relayed" and name:
        if _owns_page(read, name, issuer):
            return "own", name
        if relay_attribution_on_page(read, finding.locator or "", finding.snippet or "", name):
            return "relayed", name
    admitted = finding.attributed_issuer
    if admitted:
        if _owns_page(read, admitted, issuer):
            return "own", admitted
        return "relayed", admitted
    if proposed == "own" and name and _owns_page(read, name, issuer):
        return "own", name
    owner = issuer or page_owner(read)
    if proposed in (None, "own"):
        return "own", owner
    return "unattributed", owner


def unchecked_context(finding: Finding, figure: FindingFigure, read: ReadRecord,
                      issuer: str | None) -> FigureContext:
    """The recorded fields, used when no Context Check reply exists for a figure.

    Only a figure the snippet itself states reaches here (P1-2, in
    ``verify_finding``); the finding keeps its Figure Match status (verified
    or verified_corrected) and carries ``context_unchecked``, and its label
    says "unchecked context" (PD-26).
    """
    attribution, organisation = resolve_attribution(
        proposed=None, organisation=None, finding=finding, read=read, issuer=issuer
    )
    kind = figure.kind or (
        "forecast" if stated_role(finding.snippet or "") == "forecast" else "actual"
    )
    return FigureContext(
        period=figure.period or finding.data_period,
        scope=finding.measure_scope,
        attribution=attribution,
        organisation=organisation,
        kind=kind,
    )


def _differs(proposed: str | None, recorded: str | None) -> bool:
    return bool(proposed) and (
        recorded is None or cosmetic_text(proposed) != cosmetic_text(recorded)
    )


def _checked(item: ContextItem, figure: FindingFigure, reply: FigureCheckDraft) -> FigureResult:
    words = reply.evidence_words.strip()

    def drop(reason: FigureDropReason) -> FigureResult:
        return FigureResult(figure=figure, matched=True, evidence_words=words or None,
                            dropped_reason=reason, reason=reply.reason or None)

    if reply.verdict == "reject":
        return drop("context_rejected")
    if not words or not excerpt_matches(read_text(item.read), words):
        return drop("evidence_not_on_page")
    finding = item.finding
    period, scope, corrected = figure.period or finding.data_period, finding.measure_scope, False
    for proposed, current, field in ((reply.period, period, "period"), (reply.scope, scope, "scope")):
        if not _differs(proposed, current):
            continue
        if not excerpt_matches(words, proposed):
            return drop("correction_not_on_page")
        corrected = True
        if field == "period":
            period = proposed
        else:
            scope = proposed
    attribution, organisation = resolve_attribution(
        proposed=reply.attribution, organisation=reply.organisation,
        finding=finding, read=item.read, issuer=item.issuer,
    )
    corrected = corrected or (figure.kind is not None and figure.kind != reply.kind)
    return FigureResult(
        figure=figure, matched=True, evidence_words=words, corrected=corrected,
        reason=reply.reason or None,
        context=FigureContext(period=period, scope=scope, attribution=attribution,
                              organisation=organisation, kind=reply.kind),
    )


def verify_finding(
    item: ContextItem, replies: Mapping[int, FigureCheckDraft] | None
) -> FindingVerification:
    """§5.2's enforcement for one figure-bearing finding whose snippet is on its page.

    ``replies`` maps a 1-based figure number to its reply; ``None`` means the
    batch's Context Check failed, and a figure the reply leaves out is that
    same case for that figure. An unjudged figure is kept only when
    deterministic code confirms the finding's own snippet states it
    (``figures.figure_in_text``): the model may have left it out exactly
    because it cannot find it stated, so nothing else may promote it. A
    confirmed one keeps its recorded fields and marks the finding
    ``context_unchecked`` (PD-26); an unconfirmed one is dropped with
    ``context_unavailable``.
    """
    results: list[FigureResult] = []
    unchecked = False
    for position, figure in enumerate(item.finding.figures, start=1):
        reply = None if replies is None else replies.get(position)
        if reply is not None:
            results.append(_checked(item, figure, reply))
            continue
        if not figure_in_text(figure.value, figure.unit, item.finding.snippet or ""):
            results.append(
                FigureResult(figure=figure, matched=True, dropped_reason="context_unavailable")
            )
            continue
        unchecked = True
        results.append(
            FigureResult(figure=figure, matched=True,
                         context=unchecked_context(item.finding, figure, item.read, item.issuer))
        )
    if not any(result.kept for result in results):
        return FindingVerification(status="dropped", figure_results=results,
                                   dropped_reason="all_figures_dropped",
                                   context_unchecked=unchecked)
    corrected = any(result.corrected or not result.kept for result in results)
    return FindingVerification(
        status="verified_corrected" if corrected else "verified",
        figure_results=results, context_unchecked=unchecked,
    )


def context_check_messages(items: Sequence[ContextItem]) -> list[ChatMessage]:
    """One batch's request: every finding's snippet, passage, fields and figures."""
    blocks: list[str] = []
    for item in items:
        finding = item.finding
        recorded = "; ".join(
            f"{name}: {value}"
            for name, value in (
                ("period", finding.data_period), ("scope", finding.measure_scope),
                ("attributed to", finding.attributed_issuer),
                ("release date", finding.release_date), ("vintage", finding.vintage),
                ("statement date", finding.statement_date),
            )
            if value
        ) or "none"
        figures = "\n".join(
            f"  figure {number}: {figure.value} {figure.unit} | recorded period "
            f"{figure.period or finding.data_period or 'none'} | recorded kind "
            f"{figure.kind or 'none'}"
            for number, figure in enumerate(finding.figures, start=1)
        )
        blocks.append(
            f"## {item.label}\npage: {item.read.title} ({page_owner(item.read)})\n"
            f"recorded fields: {recorded}\nfigures:\n{figures}\n"
            f"snippet: {finding.snippet}\npassage: {item.passage}"
        )
    return [
        ChatMessage(role="developer", content=CONTEXT_CHECK_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content="\n\n".join(
                (
                    "# Figures to check\n" + "\n\n".join(blocks),
                    f"# Response contract\n{CONTEXT_CHECK_INSTRUCTION}",
                    "# Reply format\n"
                    + render_structured_reply_format(_CONTEXT_CHECK_REPLY_EXAMPLES),
                )
            ),
        ),
    ]


class EvidenceVerifierAgent(BaseAgent[VerifiedFindings]):
    """Figure Match, then one batched, tool-free Context Check (spec §5)."""

    name = EVIDENCE_VERIFIER_NAME
    description = "Check each finding's snippet and figures against its page, then their context."
    allowed_tools = ()

    @property
    def output_schema(self) -> type[VerifiedFindings]:
        return VerifiedFindings

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return CONTEXT_CHECK_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        return AgentTask(instruction=state.original_question)

    async def finalize(self, task: AgentTask, run: ReActRun) -> VerifiedFindings | None:
        del task, run
        return None

    def state_update(
        self, result: VerifiedFindings | None, run: ReActRun
    ) -> ResearchStateUpdate:
        """The ``BaseAgent`` hook's own answer; ``run`` never calls this.

        ``run`` is overridden below and builds its own ``state_update`` dict
        directly, the same way ``SynthesizerAgent.run`` does, because it
        merges ``judged`` onto the *existing* ``verified_findings`` snapshot
        (``[*state.verified_findings, *judged]``) rather than replacing it
        with ``result.findings`` alone, and it adds the completion event this
        hook's signature has nowhere to return. This override exists only so
        a caller that invokes the hook directly (as the shared ``BaseAgent``
        contract allows) gets a real answer instead of the ``errors``-only
        default.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["verified_findings"] = result.findings
        return update

    async def run(self, state: ResearchState) -> AgentRun[VerifiedFindings]:
        done = {finding_fingerprint(finding) for finding in state.verified_findings}
        pending = [
            finding for finding in deduplicate_findings(state.raw_findings)
            if finding_fingerprint(finding) not in done
        ]
        errors: list[ResearchError] = []
        async with self.tracker.agent_span(self.name) as span:
            judged = await self.verify(pending, state.read_records, errors, state.evaluated_sources)
            span.set_outputs({"agent_name": self.name, "findings": len(judged)})
        snapshot = [*state.verified_findings, *judged]
        react = ReActRun(agent_name=self.name, stop_reason="finished", errors=errors)
        return AgentRun(
            agent_name=self.name, result=VerifiedFindings(findings=judged), react=react,
            errors=errors,
            state_update={"verified_findings": snapshot, "errors": errors,
                          "events": [evidence_verified_event(judged)]},
            call_fingerprints=dict(self._call_fingerprints),
        )

    async def verify(self, findings: Sequence[Finding], reads: Mapping[str, ReadRecord],
                     errors: list[ResearchError], sources: Sequence[ScoredSource] = ()) -> list[Finding]:
        results: dict[str, FindingVerification] = {}
        items: list[ContextItem] = []
        for finding in findings:
            key, match = finding_fingerprint(finding), figure_match(finding, reads)
            if not match.read_found:
                results[key] = FindingVerification(status="dropped", dropped_reason="read_not_found")
            elif not match.snippet_on_page:
                results[key] = FindingVerification(status="dropped", dropped_reason="snippet_not_on_page")
            elif not finding.figures:
                results[key] = FindingVerification(status="verified")
            else:
                read = reads[finding.read_id or ""]
                items.append(ContextItem(label="", finding=finding, read=read,
                                         passage=context_passage(read, finding.locator, finding.snippet),
                                         match=match, issuer=evaluated_issuer(sources, read)))
        batches = [items[i : i + CONTEXT_CHECK_BATCH_SIZE]
                   for i in range(0, len(items), CONTEXT_CHECK_BATCH_SIZE)]
        gate = asyncio.Semaphore(CONTEXT_CHECK_CONCURRENCY)

        async def one(batch: list[ContextItem]) -> dict[str, dict[int, FigureCheckDraft] | None]:
            async with gate:
                return await self._check(batch, errors, split=True)

        for replies in await asyncio.gather(*(one(batch) for batch in batches)):
            for item in items:
                key = finding_fingerprint(item.finding)
                if key in replies:
                    results[key] = verify_finding(item, replies[key])
        return [f.model_copy(update={"verification": results[finding_fingerprint(f)]})
                for f in findings]

    async def _check(self, batch: list[ContextItem], errors: list[ResearchError], *,
                     split: bool) -> dict[str, dict[int, FigureCheckDraft] | None]:
        """One call; on truncation or an invalid reply, one re-ask in two halves."""
        labelled = [replace(item, label=f"F{number:02d}") for number, item in enumerate(batch, 1)]
        try:
            self.fingerprint_call(ContextCheckDraft.__name__)
            reply = await self.provider.complete_structured(
                context_check_messages(labelled), ContextCheckDraft, agent_name=self.name
            )
            reply = ContextCheckDraft.model_validate(
                reply.model_dump() if isinstance(reply, ContextCheckDraft) else reply
            )
        except (ProviderOutputLimitError, StructuredOutputError, ValidationError) as error:
            if split and len(labelled) > 1:
                half = len(labelled) // 2
                first = await self._check(labelled[:half], errors, split=False)
                return {**first, **await self._check(labelled[half:], errors, split=False)}
            errors.append(context_check_failed_error(len(labelled), error))
            return {finding_fingerprint(item.finding): None for item in labelled}
        except ProviderError as error:
            errors.append(context_check_failed_error(len(labelled), error))
            return {finding_fingerprint(item.finding): None for item in labelled}
        by_label = {item.label: item for item in labelled}
        replies: dict[str, dict[int, FigureCheckDraft] | None] = {
            finding_fingerprint(item.finding): {} for item in labelled
        }
        ignored = 0
        for draft in reply.figures:
            item = by_label.get(draft.finding.strip())
            if item is None or not 1 <= draft.figure <= len(item.finding.figures):
                ignored += 1
                continue
            replies[finding_fingerprint(item.finding)].setdefault(draft.figure, draft)
        if ignored:
            errors.append(agent_error(
                agent_name=EVIDENCE_VERIFIER_NAME,
                error_type="evidence_verifier_unknown_reference",
                message="The Context Check named figures the batch did not list; they were ignored.",
                details={"ignored": ignored},
            ))
        return replies


def context_check_failed_error(batch_size: int, error: Exception) -> ResearchError:
    return agent_error(
        agent_name=EVIDENCE_VERIFIER_NAME,
        error_type="evidence_verifier_context_check_failed",
        message=("The Context Check failed for one batch; its findings keep their "
                 "Figure Match result and are cited only as unchecked context."),
        details={"findings": batch_size, "exception_type": type(error).__name__},
    )


def evidence_verified_event(findings: Sequence[Finding]) -> ResearchEvent:
    statuses = [f.verification.status for f in findings if f.verification is not None]
    return agent_event(
        agent_name=EVIDENCE_VERIFIER_NAME,
        event_type="evidence_verifier.verification.completed",
        message=f"Verified {len(findings)} findings.",
        metadata={
            "verified": statuses.count("verified"),
            "verified_corrected": statuses.count("verified_corrected"),
            "dropped": statuses.count("dropped"),
            "context_unchecked": sum(
                1 for f in findings if f.verification and f.verification.context_unchecked
            ),
        },
    )


# ---------------------------------------------------------------------------
# check_statements (spec §6.2, D8): the Report Writer's sibling check
# ---------------------------------------------------------------------------

STATEMENT_CHECK_SYSTEM_PROMPT = (
    "You check whether a drafted sentence states only what the findings it "
    "cites actually verified. You have no tools and no web access: judge "
    "only from the figures and evidence words shown for each sentence."
)

STATEMENT_CHECK_INSTRUCTION = (
    "Return one entry in statements for every sentence listed, naming it by "
    "its label. For each sentence give:\n"
    "- verdict: consistent when the sentence states only the numbers, "
    "dates, scope, organisation and forecast-vs-actual distinction its "
    "cited findings' figures actually state; corrected when a minimal "
    "rewording would make it so; inconsistent when it states a number, "
    "date, scope, organisation, or forecast/actual distinction those "
    "figures do not support, or invents anything.\n"
    "- corrected_text: for corrected, the minimally reworded sentence; "
    "otherwise empty.\n"
    "- reason: one short sentence.\n"
    "Never invent a number, date, scope, or organisation the cited "
    "findings do not state."
)

_STATEMENT_CHECK_REPLY_EXAMPLES = (
    (
        "Example input: S01: \"EIA forecasts battery storage will reach 43.6 "
        "GW by the end of 2025.\" | F01: 43.6 GW | period none | scope none | "
        "kind actual | own (U.S. Energy Information Administration) | "
        "evidence: \"the U.S. power system had 43.6 gigawatts (GW) of "
        "operational utility-scale battery storage nameplate capacity\"",
        '{"statements":[{"label":"S01","verdict":"corrected","corrected_text":'
        '"EIA reported that by the end of 2025 the U.S. power system had 43.6 '
        'GW of operational utility-scale battery storage capacity.",'
        '"reason":"The finding states an actual, not a forecast."}]}',
    ),
)


class StatementCheckItem(ContractModel):
    """One drafted sentence, with the verified findings it cites."""

    label: str
    text: str
    findings: list[Finding]
    labels: list[str]


class StatementVerdictDraft(ContractModel):
    """One sentence's verdict as the model returns it, before code enforcement."""

    label: str
    verdict: Literal["consistent", "corrected", "inconsistent"]
    corrected_text: str = ""
    reason: str = Field(min_length=1)


class StatementCheckDraft(ContractModel):
    """The provider-facing reply for one batch of statements."""

    statements: list[StatementVerdictDraft]


def _statement_cited_lines(item: StatementCheckItem) -> str:
    """Every cited finding's kept figures, as the prompt shows them."""
    lines: list[str] = []
    for finding, label in zip(item.findings, item.labels):
        verification = finding.verification
        figures: list[str] = []
        if verification is not None:
            for result in verification.figure_results:
                if result.kept and result.context is not None:
                    ctx = result.context
                    figures.append(
                        f"{result.figure.value} {result.figure.unit} | period "
                        f"{ctx.period or 'none'} | scope {ctx.scope or 'none'} | "
                        f"kind {ctx.kind} | {ctx.attribution} ({ctx.organisation}) | "
                        f"evidence: {result.evidence_words or ''}"
                    )
        body = "; ".join(figures) if figures else "(no kept figures)"
        lines.append(f"  {label}: {body}")
    return "\n".join(lines)


def statement_check_messages(
    items: Sequence[StatementCheckItem], *, question: str
) -> list[ChatMessage]:
    """One batch's request: every sentence, its label, and its cited findings' figures."""
    blocks = [
        f"## {item.label}\nsentence: {item.text}\ncited findings:\n{_statement_cited_lines(item)}"
        for item in items
    ]
    return [
        ChatMessage(role="developer", content=STATEMENT_CHECK_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content="\n\n".join(
                (
                    f"# Question\n{question}",
                    "# Statements to check\n" + "\n\n".join(blocks),
                    f"# Response contract\n{STATEMENT_CHECK_INSTRUCTION}",
                    "# Reply format\n"
                    + render_structured_reply_format(_STATEMENT_CHECK_REPLY_EXAMPLES),
                )
            ),
        ),
    ]


def statement_check_failed_error(batch_size: int, error: Exception) -> ResearchError:
    return agent_error(
        agent_name=EVIDENCE_VERIFIER_NAME,
        error_type="evidence_verifier_statement_check_failed",
        message=("The Statement Check failed for one batch; its sentences are "
                 "kept as drafted rather than checked."),
        details={"statements": batch_size, "exception_type": type(error).__name__},
    )


def statement_check_omitted_error(omitted: int) -> ResearchError:
    """A reply that answered only part of its batch.

    The labels it left out stay ``None``, so their sentences are kept as
    drafted: the same outcome, and the same error type, as a batch that
    failed outright -- recorded so the gate can see them.
    """
    return agent_error(
        agent_name=EVIDENCE_VERIFIER_NAME,
        error_type="evidence_verifier_statement_check_failed",
        message=("The Statement Check's reply omitted sentences from one batch; "
                 "they are kept as drafted rather than checked."),
        details={"statements": omitted, "reason": "label omitted from the reply"},
    )


async def _check_statement_batch(
    provider: StructuredCompleter,
    batch: Sequence[StatementCheckItem],
    question: str,
    errors: list[ResearchError],
    fingerprint: Callable[[str], None] | None,
    *,
    split: bool,
) -> dict[str, StatementVerdictDraft | None]:
    """One call; on truncation or an invalid reply, one re-ask in two halves."""
    if fingerprint is not None:
        fingerprint(StatementCheckDraft.__name__)
    try:
        reply = await provider.complete_structured(
            statement_check_messages(batch, question=question), StatementCheckDraft,
            agent_name=EVIDENCE_VERIFIER_NAME,
        )
        reply = StatementCheckDraft.model_validate(
            reply.model_dump() if isinstance(reply, StatementCheckDraft) else reply
        )
    except (ProviderOutputLimitError, StructuredOutputError, ValidationError) as error:
        if split and len(batch) > 1:
            half = len(batch) // 2
            first = await _check_statement_batch(
                provider, batch[:half], question, errors, fingerprint, split=False
            )
            return {
                **first,
                **await _check_statement_batch(
                    provider, batch[half:], question, errors, fingerprint, split=False
                ),
            }
        errors.append(statement_check_failed_error(len(batch), error))
        return {item.label: None for item in batch}
    except ProviderError as error:
        errors.append(statement_check_failed_error(len(batch), error))
        return {item.label: None for item in batch}
    labels = {item.label for item in batch}
    results: dict[str, StatementVerdictDraft | None] = {item.label: None for item in batch}
    for draft in reply.statements:
        if draft.label not in labels:
            continue
        if draft.verdict == "corrected" and not draft.corrected_text.strip():
            draft = draft.model_copy(update={"verdict": "inconsistent"})
        results[draft.label] = draft
    omitted = sum(1 for verdict in results.values() if verdict is None)
    if omitted:
        errors.append(statement_check_omitted_error(omitted))
    return results


async def check_statements(
    provider: StructuredCompleter,
    items: Sequence[StatementCheckItem],
    *,
    question: str,
    fingerprint: Callable[[str], None] | None = None,
) -> tuple[dict[str, StatementVerdictDraft | None], list[ResearchError]]:
    """Spec §6.2's Statement Check (D8): does a drafted sentence state only
    what the verified findings it cites actually carry?

    Parallel batches of ``CONTEXT_CHECK_BATCH_SIZE`` items, at most
    ``CONTEXT_CHECK_CONCURRENCY`` in flight -- the same bounds the Context
    Check runs under. ``None`` means the item was not judged: its own batch
    failed outright, or the reply did not name its label. Either way the
    caller keeps the sentence as drafted and one
    ``evidence_verifier_statement_check_failed`` error is recorded for the
    batch. A reply's ``corrected`` verdict with a blank ``corrected_text``
    is treated as ``inconsistent``; nothing else is applied to the reply.
    """
    errors: list[ResearchError] = []
    batches = [
        items[i : i + CONTEXT_CHECK_BATCH_SIZE]
        for i in range(0, len(items), CONTEXT_CHECK_BATCH_SIZE)
    ]
    gate = asyncio.Semaphore(CONTEXT_CHECK_CONCURRENCY)

    async def one(batch: Sequence[StatementCheckItem]) -> dict[str, StatementVerdictDraft | None]:
        async with gate:
            return await _check_statement_batch(provider, batch, question, errors, fingerprint, split=True)

    results: dict[str, StatementVerdictDraft | None] = {}
    for batch_result in await asyncio.gather(*(one(batch) for batch in batches)):
        results.update(batch_result)
    return results, errors
