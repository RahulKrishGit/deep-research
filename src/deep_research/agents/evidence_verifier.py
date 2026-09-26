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
``agents.verifier_batch_size`` findings, at most
``agents.verifier_concurrency`` batches in flight (shared with
``check_statements`` below; the module constants of the same shapes are only
the defaults, PD-12). A finding whose
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
    _opening_credits_organisation,
    _quote_states,
    _stated_dates,
    cosmetic_text,
    excerpt_matches,
    own_organisation_on_page,
    relay_attribution_on_page,
    snippet_span_text,
)
from deep_research.agents.figures import figure_in_text, is_a_date
from deep_research.agents.identity import deduplicate_findings, finding_fingerprint
from deep_research.agents.prompts import (
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.sources import publisher_identity
from deep_research.agents.steps import ReActRun
from deep_research.agents.verified_facts import (
    _period_stated_in,
    claimed_organisation,
    resolve_relative_period,
    same_organisation,
    same_period,
    same_subject,
)
from deep_research.agents.wording import stated_role, title_segments
from deep_research.providers import (
    ChatMessage,
    ProviderConfigurationError,
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


# The two Context Check bounds are the *defaults* for
# ``agents.verifier_batch_size`` and ``agents.verifier_concurrency``, which the
# Evidence Verifier reads from its own config (PD-12, PD-27); the Statement
# Check takes the same two values from its caller. They stay module constants
# only so a direct caller and the module's own tests have a defined default.
CONTEXT_CHECK_BATCH_SIZE = 5
CONTEXT_CHECK_CONCURRENCY = 8
CONTEXT_PASSAGE_CHARS = 6000

CONTEXT_CHECK_SYSTEM_PROMPT = (
    "You check the context of figures that a research system copied from web "
    "pages. For each figure the block prints the page's own title and owner, the "
    "page's own stated date, the fields the extractor recorded, the snippet the "
    "figure was copied from, and the surrounding passage of the same page. You "
    "have no tools and no web access. The snippet and the passage are the page's "
    "own words, and the only evidence any field may rest on; the page date is the "
    "date a relative period may be resolved against; the title and the owner say "
    "whose page this is, and the title may name the body whose document the page "
    "reproduces; the recorded fields are the extractor's claims, which your reply "
    "corrects. Reject a figure whose snippet or passage does not actually state "
    "it. Only figures are checked here: a finding with no figure has nothing to "
    "judge."
)

CONTEXT_CHECK_INSTRUCTION = (
    "Return one entry in figures for every figure listed, naming it by its "
    "finding label and figure number. For each figure give:\n"
    "- period: the period the page says the figure applies to, as the page "
    "writes it (\"2024\", \"Q3 2025\"); a level the page dates to a point in time "
    "keeps that date as its period (\"at the end of Q1'25\"); repeat the recorded "
    "period when the page confirms it; null when the page states none (a current "
    "price or a score usually has none — never invent one).\n"
    "When the page dates a figure only relatively (\"this year\", \"last quarter\"), "
    "give the period that the page's own stated date resolves it to and quote the "
    "relative words in evidence_words; never resolve against today's date.\n"
    "- scope: the segment or basis the page says the figure covers, as the page "
    "writes it (\"all sites\", \"members only\"), or null when the page states none. "
    "When the page counts things, the counted thing is the subject and this is the "
    "basis the count covers.\n"
    "- subject: the thing the page says the figure is about, as the page names it "
    "(a model, a place, a version, a named item); repeat the recorded subject when "
    "the page confirms it; when the page names that same thing differently, give "
    "the page's name under a correction; null when the page names none.\n"
    "- attribution: own when the page states the figure as its publisher's own; "
    "relayed when the page credits another organisation for it (\"according to\", "
    "\"reported by\", a possessive) or when the page reproduces another body's "
    "document and its own title names that body; unattributed when the "
    "page states it without saying whose it is.\n"
    "- organisation: for own, the page's publisher as the page names itself; for "
    "relayed, the organisation the page credits, exactly as the page or its own "
    "title names it; null for unattributed.\n"
    "- kind: actual for a quantity the page states as measured, reported, observed "
    "or in force; forecast for a projection, plan, expectation, target or announced "
    "future change. A date is not a quantity: keep it in the period or in the words "
    "you quote.\n"
    "- evidence_words: the exact words of the page that state this figure with this "
    "period and scope, copied character for character as one unbroken span of the "
    "page: the sentence that states the figure and, only where the span stays "
    "contiguous, the heading or preceding sentence that carries the period or the "
    "scope. Words that are not on the page make the figure unusable.\n"
    "- verdict: confirm when the recorded period, scope, subject and kind are right "
    "and you changed nothing; correct when you changed any of them; reject when the "
    "snippet or passage does not actually state this figure, or states it for "
    "something else.\n"
    "- reason: one short sentence.\n"
    "What code does with this reply, so a wrong proposal is not merely ignored: a "
    "correction is kept only when your wording appears in evidence_words; a period "
    "neither your words nor the page date resolves, or a scope your words do not "
    "carry, drops the whole figure; a null period or subject under correct clears "
    "the recorded one, while under confirm the recorded value stands. Never guess "
    "a period, a scope, a subject or an organisation the passage does not state."
)

_CONTEXT_CHECK_REPLY_EXAMPLES = (
    (
        # The block's own field names (review VER-2 defect 8), so the recorded
        # scope is visible and a fill is not taught as a correction.
        # The recorded-fields line omits empty fields, as the real block does, so
        # the example shows the shape the model is shown and no more (re-round 1,
        # EXTRA-3 note).
        "Example input: page: Example report (Example Institute) | page date: "
        "2025-03-12 (the finding's release date) | recorded fields: period: 2024 "
        "| figure 1: 12 percent | recorded period 2024 | recorded kind actual | "
        "snippet: \"The measured reduction was 12 percent\" | passage: \"The "
        "measured reduction was 12 percent in 2024, according to the Example "
        "Statistical Agency, measured across all sites.\"",
        '{"figures":[{"finding":"F01","figure":1,"period":"2024","scope":"all '
        'sites","subject":null,"attribution":"relayed","organisation":"Example '
        'Statistical Agency","kind":"actual","evidence_words":"The measured '
        'reduction was 12 percent in 2024, according to the Example Statistical '
        'Agency, measured across all sites","verdict":"correct","reason":"The '
        'recorded fields state no scope, the page states one, and it credits the '
        'agency."}]}',
    ),
)


class FigureCheckDraft(ContractModel):
    """One figure's context as the model returns it, before code enforcement."""

    finding: str
    figure: int
    period: str | None = None
    scope: str | None = None
    subject: str | None = None
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
    """``figure_match`` for this finding, whose snippets every figure here shares."""
    issuer: str | None = None   # evaluated_issuer(...) for the read (PD-25)
    page_date: str | None = None   # evaluated_page_date(...) for the read (D11, D12)


class VerifiedFindings(ContractModel):
    """What one Evidence Verifier run judged. Never sent to the provider."""

    findings: list[Finding] = Field(default_factory=list)


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
_YEAR = re.compile(r"(?:19|20)\d{2}")
# A host label written as an organisation: the Context Check's own block prints
# the page's host as its owner ("tester.example.test"), and that is the page
# itself rather than a body it might not own.
_HOST_LABEL = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")

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
    return title_segments(title)


def _cued_name_candidates(text: str, cues: Sequence[str]) -> list[str]:
    """A short run of capitalised words immediately after any of ``cues``.

    A cue that reads "from the Example Lab" names the body, not its article, so
    a leading article is skipped before the run is read.
    """
    candidates: list[str] = []
    pattern = re.compile(rf"(?:{'|'.join(cues)})\s*", re.IGNORECASE)
    for cue in pattern.finditer(text):
        tail = _LEADING_YEAR.sub("", text[cue.end() : cue.end() + 100].lstrip())
        tail = re.sub(r"^(?:the|a|an)\s+", "", tail, flags=re.IGNORECASE)
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
    """§5.2's bounded passage: every passage the snippet actually spans, centred on it."""
    text = snippet_span_text(read, locator or "", snippet or "") or read_text(read)
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


def evaluated_page_date(sources: Sequence[ScoredSource], read: ReadRecord) -> str | None:
    """D11: the publication date the Source Evaluator validated for this read, if any.

    The one date a relative period ("this year") may be resolved against, and
    the one every Context Check block prints (D12), so every batch a figure
    lands in resolves it from the same basis.
    """
    urls = {read.requested_url, read.resolved_url}
    for source in sources:
        if source.url in urls:
            date = source.temporal.publication_date
            return date.strip() if date and date.strip() else None
    return None


def _page_date_basis(item: ContextItem) -> tuple[str | None, str]:
    """The date a relative period resolves against, and where it came from (D11, D12).

    The block prints what ``_checked`` resolves from, so one helper gives both
    of them the same basis and the same words for it: a batch reads the date
    that decides the figure, whichever date that is. The finding's release
    date is admitted by the researcher's own quote rule; its statement date is
    checked here, because the extraction copies that draft field straight
    through, so an off-page one would resolve a relative period against a date
    the page cannot support (I7).
    """
    if item.page_date:
        return item.page_date, "from the Source Evaluator"
    finding = item.finding
    if finding.release_date:
        return finding.release_date, "the finding's release date"
    if finding.statement_date and _quote_states(read_text(item.read), (finding.statement_date,)):
        return finding.statement_date, "the finding's statement date"
    return None, ""


def _owns_page(read: ReadRecord, organisation: str, issuer: str | None) -> bool:
    """PD-18's own-page rule, then PD-25's validated issuer with the page's own word.

    The page has to *be* this organisation's. PD-18 answers that from the read
    itself: the organisation's own registrable host, or an institutional domain
    whose label spells it while the page names it. The Source Evaluator's
    validated issuer (PD-25) is a judgement about the read, so an identity-words
    match with it counts only beside the page's own credit of itself -- a body
    merely mentioned somewhere on the page is not its publisher.
    """
    if own_organisation_on_page(read, organisation):
        return True
    if not issuer or _identity_words(issuer) != _identity_words(organisation):
        return False
    return _opening_credits_organisation(read, organisation)


# The cues whose body *follows* them ("based on the latest reporting from X",
# "data from X", "according to X"), read out of a figure's own evidence words.
_NAME_AFTER_CUES = (
    r"report(?:s|ed|ing)?\s+from", r"data\s+from", r"according\s+to", r"per",
    r"sources?\s*:", r"released\s+by", r"reported\s+by", r"estimates?\s+from",
    r"published\s+by", r"prepared\s+by",
)
# The bodies a page *names*: capitalised runs, which is what keeps a lowercase
# "our survey" out of them.
_SOURCE_NOUN = (
    r"(?:survey|poll|study|research|reports?|analysis|data|figures?|numbers?|index|"
    r"outlook|forecasts?|estimates?)"
)
_NAME_BEFORE_CUE_PATTERN = re.compile(
    rf"(?P<name>{_RUN_WORD}(?:\s+{_RUN_WORD}){{0,5}})\s*(?:['\u2019]s\s+)?"
    rf"(?:(?i:latest|own|new|full|annual|preliminary)\s+){{0,2}}(?i:{_SOURCE_NOUN})(?![A-Za-z0-9])"
)


def _title_names_the_body(read: ReadRecord, name: str) -> bool:
    """Whether the page's own title carries this body's name (review VER-2 defect 2).

    The reproduced-document case: a page serving another body's instrument needs
    no attribution cue, because its own title says whose document it is
    ("<document> | <body> | <site>"). Read from the title's own segments, so
    nothing is credited that the page does not spell.
    """
    return any(same_organisation(segment, name) for segment in title_segments(read.title))


def _body_credited_in_words(words: str | None) -> str | None:
    """The body a finding's own evidence words credit, or ``None``.

    The Context Check can leave a relay unresolved while the words it quoted
    state the credit plainly ("based on the latest reporting from the U.S.
    Energy Information Administration"), and the label then contradicts the
    sentence the writer quotes from the page (the live pre-flight's
    review-01). Code reads the same cues back out of those words, in both
    directions, so the label agrees with the page's own sentence.
    """
    if not words:
        return None
    for candidate in _cued_name_candidates(words, _NAME_AFTER_CUES):
        name = _stripped_name(candidate)
        if name:
            return name
    match = _NAME_BEFORE_CUE_PATTERN.search(words)
    if match is not None:
        return _stripped_name(match.group("name")) or None
    return None


def resolve_attribution(
    *,
    proposed: FigureAttribution | None,
    organisation: str | None,
    finding: Finding,
    read: ReadRecord,
    issuer: str | None,
    words: str | None = None,
) -> tuple[FigureAttribution, str]:
    """PD-8: the Context Check proposes, the page's own words decide.

    ``issuer`` is ``evaluated_issuer(...)`` for the read (PD-25), or ``None``.
    ``words`` are the Context Check's own evidence words for this figure, when
    there are any: a verdict that leaves the attribution unresolved is repaired
    from them, so the label never contradicts the sentence the page states.
    """
    name = (organisation or "").strip()
    if proposed == "relayed" and name:
        if _owns_page(read, name, issuer):
            return "own", name
        if relay_attribution_on_page(read, finding.locator or "", finding.snippet or "", name):
            return "relayed", name
        if _title_names_the_body(read, name):
            # A page that reproduces another body's document (an instrument, a
            # standard, a recital) needs no cue beside the figure: the document's
            # body is named in the page's own title, and the reply's credit is
            # those words (review VER-2 defect 2). The page's own owner never
            # arrives here -- _owns_page answered that above.
            return "relayed", name
    admitted = finding.attributed_issuer
    if admitted and not (
        proposed == "relayed" and name and not same_organisation(name, admitted)
    ):
        # The finding-level admission is the whole snippet's issuer, and one
        # finding can state two bodies' figures (C1): when the Context Check
        # proposes a *different* body and the page carries no cue for it, the
        # admitted issuer is not this figure's, and crediting it would print a
        # figure that body never issued as its relay.
        if _owns_page(read, admitted, issuer):
            return "own", admitted
        return "relayed", admitted
    if proposed == "own" and name and _owns_page(read, name, issuer):
        return "own", name
    # The verdict left the attribution unresolved (no proposal, "unattributed",
    # a relay with no body, or an "own" its own name does not back): the page's
    # own words decide, when they credit a body at all.
    unresolved = (
        proposed in (None, "unattributed")
        or (proposed == "relayed" and not name)
        or (proposed == "own" and not (name and _owns_page(read, name, issuer)))
    )
    credited = _body_credited_in_words(words) if unresolved else None
    if credited:
        if _owns_page(read, credited, issuer):
            return "own", credited
        return "relayed", credited
    owner = issuer or page_owner(read)
    if proposed is None or (proposed == "own" and not name):
        return "own", owner
    if proposed == "own":
        if issuer and same_organisation(name, issuer):
            # PD-25: the body the Source Evaluator validated for this read is the
            # page's own organisation, which is what the verdict proposed.
            return "own", name
        page_host = publisher_identity(read.resolved_url)
        if same_organisation(name, page_host) or (
            _HOST_LABEL.fullmatch(name.casefold())
            and publisher_identity(f"https://{name}") == page_host
        ):
            # The verdict named this page's own owner -- as its name or as its
            # host (the Context Check's block prints the host) -- which is not "a
            # body the host does not own": the own-page reading stands (N3).
            return "own", owner
        # F2: otherwise the verdict named a body this page is not, so the page's
        # own cue beside the figure decides whether it is that body's relay;
        # nothing does, and the figure is attributed to nobody. Returning the
        # host as its own organisation here presented a relay as the issuer.
        if relay_attribution_on_page(read, finding.locator or "", finding.snippet or "", name):
            return "relayed", name
        return "unattributed", owner
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
        subject=figure.subject,
    )


def _differs(proposed: str | None, recorded: str | None) -> bool:
    return bool(proposed) and (
        recorded is None or cosmetic_text(proposed) != cosmetic_text(recorded)
    )


def _period_stated(words: str, period: str | None) -> bool:
    """Whether the words state this period: literally, or in another spelling.

    One rule for every period test the enforcement makes: a page that dates its
    figure "at the end of Q1'25" states the period a reply writes "Q1 2025"
    (round 3's pre-flight evidence), and a period neither spelling states is
    still unstated.
    """
    return bool(period) and (
        excerpt_matches(words, period) or _period_stated_in(words, period)
    )


def _agrees_with_the_years_the_words_state(words: str, resolved: str) -> bool:
    """Whether a relative period the code read agrees with the words' own years.

    The words can carry an explicit period and a relative phrase at once
    ("Firms added 4 GW in 2024; this year they plan more"), and fix round 1
    ruled only on the case where the *recorded* period is the explicit one. A
    resolution naming another year than the ones these words state is not the
    period these words are about, so no correction is kept from it (P2-2).
    """
    stated = {
        int(atom[:4])
        for atoms in _stated_dates(words)
        for atom in atoms
        if _YEAR.fullmatch(atom[:4])
    }
    if not stated:
        return True
    year = _YEAR.search(resolved)
    return year is not None and int(year.group()) in stated


def _checked(item: ContextItem, figure: FindingFigure, reply: FigureCheckDraft) -> FigureResult:
    """§5.2's enforcement of one reply: the page decides every correction.

    A period or scope correction is kept only when ``evidence_words`` carry
    it, except a period the words date relatively that the page's own stated
    date resolves to (D11), which is kept with the date it came from and only
    when it agrees with any year the words state themselves (P2-2). A period
    the words state themselves is never resolved relatively (fix round 1). A
    recorded period or subject the reply answers null on, under a `correct`
    verdict, is not backed by the words and is cleared rather than printed
    (I1). A subject is adopted only when the evidence words or the passage
    name it (ruling N2). An unnamed proposal never drops a figure for a
    subject it restates or a subject the figure's own evidence words state; it
    drops only a figure whose recorded subject its own words do not back (fix
    rounds 1 and 2).
    """
    words = reply.evidence_words.strip()

    def drop(reason: FigureDropReason) -> FigureResult:
        return FigureResult(figure=figure, matched=True, evidence_words=words or None,
                            dropped_reason=reason, reason=reply.reason or None)

    if reply.verdict == "reject":
        return drop("context_rejected")
    if not words or not excerpt_matches(read_text(item.read), words):
        return drop("evidence_not_on_page")
    finding = item.finding
    period, scope, subject = figure.period or finding.data_period, finding.measure_scope, figure.subject
    corrected = False
    resolved_from: str | None = None   # the page date a relative period came from (D11)
    # A date is not a measure (improvement 9): the date the page states *is* the
    # figure, so a reply that proposes it as the "period" corrects nothing, and
    # the ISO spelling of a date the words spell out is that same date, never a
    # correction that is not on the page. The recorded fields stand unchanged and
    # nothing is dropped for it.
    dated = is_a_date(figure.value, figure.unit)
    if not dated and reply.period and _differs(reply.period, period):
        if not _period_stated(words, reply.period):
            page_date, _ = _page_date_basis(item)
            # An explicit period the words themselves state beats a relative
            # reading (fix round 1): only a page that dates the figure
            # relatively lets code resolve one. Fix round 1 guarded the
            # *recorded* period; the words' own year is what has to agree, or a
            # figure the words date 2024 is kept under a relative 2026 (P2-2).
            dated_explicitly = _period_stated(words, period)
            resolved = None if dated_explicitly else resolve_relative_period(words, page_date)
            if (resolved is None or not same_period(resolved, reply.period)
                    or not _agrees_with_the_years_the_words_state(words, resolved)):
                # A period the page never states is not published, whatever the
                # reply's verdict: the figure's own period would then be one no
                # page states (D11; the relative-period scenario's second page).
                return drop("correction_not_on_page")
            resolved_from = page_date
            period, corrected = reply.period, period is not None
        else:
            # The words state the proposal, so adopting it is a correction only
            # where the figure had recorded a period at all (improvement 9): a
            # figure the extraction left undated, dated by the words the reply
            # quotes, is a fill, not a correction.
            period, corrected = reply.period, period is not None
    elif reply.verdict == "correct" and period and not _period_stated(words, period):
        # The Context Check answers null as its prompt instructs ("null when
        # the page states none"), so a recorded period its own words do not
        # state is not verified by them: the figure keeps its value and its
        # label reads "period not stated" (I1). A period the words do state
        # stands -- the quote backs it.
        period, corrected = None, True
    if reply.scope and _differs(reply.scope, scope):
        if excerpt_matches(words, reply.scope):
            scope, corrected = reply.scope, True
        elif reply.verdict == "correct":
            # Only the verdict that asserts a correction may cost a figure its
            # place (improvement 9). A reply confirming the figure that also
            # volunteers a scope its own words do not carry leaves the recorded
            # scope standing -- which is what the run's fourth date figure lost
            # its place to.
            return drop("correction_not_on_page")
    proposed = (reply.subject or "").strip()
    if proposed and _differs(proposed, subject):
        if excerpt_matches(words, proposed) or excerpt_matches(item.passage, proposed):
            subject, corrected = proposed, True
        elif subject and not (
            same_subject(proposed, subject) or excerpt_matches(words, subject)
        ):
            # Neither subject is backed by this figure's own words (fix round 2):
            # the proposal cannot displace the recorded one, and the recorded one
            # is not what these words state. A passage that names another
            # figure's subject never backs this figure's.
            return drop("correction_not_on_page")
    elif reply.verdict == "correct" and subject and not excerpt_matches(words, subject):
        # The same rule for the recorded subject (I1): the page names none the
        # words carry, so nothing backs it and it is dropped rather than
        # printed as the thing this figure is about.
        subject, corrected = None, True
    attribution, organisation = resolve_attribution(
        proposed=reply.attribution, organisation=reply.organisation,
        finding=finding, read=item.read, issuer=item.issuer, words=words,
    )
    corrected = corrected or (figure.kind is not None and figure.kind != reply.kind)
    return FigureResult(
        figure=figure, matched=True, evidence_words=words, corrected=corrected,
        reason=reply.reason or None,
        context=FigureContext(period=period, scope=scope, subject=subject, attribution=attribution,
                              organisation=organisation, kind=reply.kind,
                              period_resolved_from=resolved_from),
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
        date, source = _page_date_basis(item)
        # Only the fields this reply judges (review VER-2 defect 6): release
        # date, vintage and statement date have no field to be judged against and
        # were printed as if verified. The relative-period basis they may carry is
        # still printed, on the page date line below, where code resolves from it.
        recorded = "; ".join(
            f"{name}: {value}"
            for name, value in (
                ("period", finding.data_period), ("scope", finding.measure_scope),
                ("attributed to", finding.attributed_issuer),
            )
            if value
        ) or "none"
        figures = "\n".join(
            f"  figure {number}: {figure.value} {figure.unit} | recorded period "
            f"{figure.period or finding.data_period or 'none'} | recorded kind "
            f"{figure.kind or 'none'}"
            + (f" | recorded subject {figure.subject}" if figure.subject else "")
            for number, figure in enumerate(finding.figures, start=1)
        )
        blocks.append(
            f"## {item.label}\npage: {item.read.title} ({page_owner(item.read)})\n"
            + (
                f"page date: {date} ({source})\n" if date else "page date: not stated\n"
            )
            + f"recorded fields: {recorded}\nfigures:\n{figures}\n"
            f"snippet: {finding.snippet}\npassage: {item.passage}"
        )
    static = [
        f"# Response contract\n{CONTEXT_CHECK_INSTRUCTION}",
        "# Reply format\n"
        + render_structured_reply_format(_CONTEXT_CHECK_REPLY_EXAMPLES),
    ]
    material = ["# Figures to check\n" + "\n\n".join(blocks)]
    return [
        ChatMessage(role="developer", content=CONTEXT_CHECK_SYSTEM_PROMPT),
        ChatMessage(
            role="user", content=render_structured_request(static, material)
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

    async def run(self, state: ResearchState) -> AgentRun[VerifiedFindings]:
        verified = {finding_fingerprint(finding): finding for finding in state.verified_findings}
        pending = [
            finding for finding in deduplicate_findings(state.raw_findings)
            if (record := verified.get(finding_fingerprint(finding))) is None
            # A re-extraction can bind a target the verified record never
            # carried (P2-4). The binding lives on the finding, so judging the
            # record again is what lets the obligation it answers be read from
            # a verified record instead of staying Not found.
            or not set(finding.target_ids) <= set(record.target_ids)
        ]
        errors: list[ResearchError] = []
        async with self.tracker.agent_span(self.name) as span:
            judged = await self.verify(pending, state.read_records, errors, state.evaluated_sources)
            span.set_outputs({"agent_name": self.name, "findings": len(judged)})
        snapshot = _merged_snapshot(state.verified_findings, judged)
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
                # D21: only a figure ever reaches the Context Check (a
                # finding with none "has nothing to judge"), and the
                # Statement Check judges drafted report sentences, not raw
                # findings -- so neither check ever judges this finding for
                # relevance or attribution. Its snippet is on the page, and
                # that is all this status may claim.
                results[key] = FindingVerification(status="quoted")
            else:
                read = reads[finding.read_id or ""]
                items.append(ContextItem(label="", finding=finding, read=read,
                                         passage=context_passage(read, finding.locator, finding.snippet),
                                         match=match, issuer=evaluated_issuer(sources, read),
                                         page_date=evaluated_page_date(sources, read)))
        batches = [items[i : i + self.config.verifier_batch_size]
                   for i in range(0, len(items), self.config.verifier_batch_size)]
        gate = asyncio.Semaphore(self.config.verifier_concurrency)

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
        except ProviderConfigurationError:
            # A rejected model or effort is a configuration fault the node halts
            # on (P3-5): recording it as a failed batch would publish a report
            # whose every figure reads "unchecked context".
            raise
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


def _merged_snapshot(existing: Sequence[Finding], judged: Sequence[Finding]) -> list[Finding]:
    """PD-4's snapshot, with a re-judged finding's newer verdict in its place.

    ``run`` judges a finding again only when a later extraction bound a target
    the verified record did not carry (P2-4), so the newer verdict stands and
    both passes' bindings are kept -- the same argument
    ``deduplicate_findings`` makes for raw findings, and what keeps one record
    per finding, which is what readers of the snapshot count.
    """
    latest = {finding_fingerprint(finding): finding for finding in judged}
    merged: list[Finding] = []
    for finding in existing:
        replacement = latest.pop(finding_fingerprint(finding), None)
        if replacement is None:
            merged.append(finding)
            continue
        merged.append(replacement.model_copy(update={"target_ids": list(dict.fromkeys(
            [*finding.target_ids, *replacement.target_ids]))}))
    merged.extend(latest.values())
    return merged


def evidence_verified_event(findings: Sequence[Finding]) -> ResearchEvent:
    statuses = [f.verification.status for f in findings if f.verification is not None]
    return agent_event(
        agent_name=EVIDENCE_VERIFIER_NAME,
        event_type="evidence_verifier.verification.completed",
        message=f"Verified {len(findings)} findings.",
        metadata={
            "verified": statuses.count("verified"),
            "verified_corrected": statuses.count("verified_corrected"),
            "quoted": statuses.count("quoted"),
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
    "cites actually verified. You have no tools and no web access: judge only "
    "from what the block prints for each sentence. Its figures and its snippet "
    "are the cited finding's own verified words; its passage is the page around "
    "the snippet and may support a condition, an exception, a qualifier or the "
    "object a reported rule attaches to, never a new fact; its page line is the "
    "page's own title and the site it was read on, and a document that title "
    "names may be named by the sentence; a line saying where a statement was "
    "read names the site, not the body that made it, so a sentence presenting "
    "that site as the author of a document is not supported."
)

STATEMENT_CHECK_INSTRUCTION = (
    "Return one entry in statements for every sentence listed, naming it by "
    "its label. The question is context only: judge each sentence against its "
    "cited findings, not against what you think the question should cover. For "
    "each sentence give:\n"
    "- verdict: consistent when the sentence states only the numbers, "
    "dates, subject, scope, organisation, forecast-vs-actual distinction, "
    "and the conditions, exceptions and object a rule it reports attaches "
    "to, that its cited findings' figures, snippets and passages actually "
    "state, and keeps every qualifier the words carry (\"about\", \"nearly\", "
    "\"more than\"), every criterion a judgement was measured by, and every "
    "body the words credit — never presenting the page it was read on as the "
    "author of a document that page reproduces, while a document a cited page's "
    "own title names may be named as that title names it; corrected when a minimal "
    "rewording from the cited words would make it so; inconsistent when it "
    "states a number, date, subject, scope, organisation, forecast/actual "
    "distinction, condition, exception or object those figures and words do "
    "not support, presents the page it was read on as the author of a document "
    "that page reproduces, or invents anything. A sentence that drops a "
    "qualifier or a criterion their words carry, or states a conditional rule "
    "as unconditional, is not consistent either, and the rule below decides "
    "which of the two it gets. One rule "
    "decides between those two: a sentence is corrected when the cited words "
    "state what it should have said — a condition, an exception, a qualifier, a "
    "criterion or an issuer it dropped, or the number, date, kind or body it "
    "misstated — and inconsistent when it states a fact the cited words neither "
    "state nor can replace. A judgement, ranking or recommendation stated as fact "
    "rather than as the judgement of the source that made it is not supported "
    "as written: correct it by attributing it to that source. A page's "
    "caption, player title or condition label is not a judgement the page "
    "makes; a sentence presenting one as a rating is not supported.\n"
    "- corrected_text: for corrected, the minimally reworded sentence, built "
    "only from the cited findings' own words (snippets, evidence words, "
    "passages) and a document name as the page line's title names it, and no "
    "more than 600 characters; a longer correction is "
    "refused whole, so mark such a sentence inconsistent instead; otherwise "
    "empty.\n"
    "- reason: one short sentence.\n"
    "Never invent a number, date, subject, scope, organisation, condition, "
    "exception, qualifier or object the cited findings do not state, and never "
    "drop one their words carry."
)

_STATEMENT_CHECK_REPLY_EXAMPLES = (
    (
        "Example input: S01: The Example Institute projects that 4.1 thousand "
        "units will ship by the end of 2026. | F01: 4.1 thousand units | period "
        "2025 | scope none | subject none | kind actual | own (Example "
        "Institute) | evidence: \"by the end of 2025, 4.1 thousand units had "
        "shipped\" | page: Example report (example-institute.test) | snippet: "
        "\"Shipments reached 4.1 thousand units in 2025.\"",
        '{"statements":[{"label":"S01","verdict":"corrected","corrected_text":'
        '"The Example Institute reported that by the end of 2025, 4.1 thousand '
        'units had shipped.","reason":"The finding states an actual for 2025, not '
        'a forecast."}]}',
    ),
    (
        "Example input: S02: The grant covers travel. | F02: (no kept figures) "
        "| page: Example report (example-institute.test) | snippet: \"The grant "
        "covers travel when the visit is approved in "
        "advance\" | passage: \"The grant covers travel when the visit is "
        "approved in advance. It does not cover stays longer than five days.\"",
        '{"statements":[{"label":"S02","verdict":"corrected","corrected_text":'
        '"The grant covers travel when the visit is approved in advance, and not '
        'for stays longer than five days.","reason":"The cited words carry a '
        'condition and an exception the sentence omits."}]}',
    ),
)


class StatementCheckItem(ContractModel):
    """One drafted sentence, with the verified findings it cites."""

    label: str
    text: str
    findings: list[Finding]
    labels: list[str]
    passages: dict[str, str] = Field(default_factory=dict)
    """Finding id (``finding_fingerprint``) -> the bounded passage of its page.

    Improvement 8: a snippet is cut at the passage boundary, so the condition,
    exception or object a reported rule attaches to is often just outside it --
    "released under an open licence that allows for" ends where the exception
    to that rule begins. The caller that holds the run's reads supplies
    ``context_passage`` for each cited finding, and the block shows it beside
    the snippet; a caller with no reads in hand leaves this empty and the block
    is exactly what it was.
    """


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
    """Every cited finding's own page, its kept figures, its verified snippet.

    The ``page`` line is the read's own title with its host — the same title
    the writer's registry shows — so the checker can see the document a page
    reproduces and let a sentence name it (review re-round 1, C1): without it the
    writer names an instrument the checker cannot find and the point is refused
    by construction. A finding that kept no figure also carries who it belongs
    to: the admitted issuer (``attributed to``), or, when nothing was admitted,
    the site the statement was read on (``read at``) — a host is never the body
    that made a statement. A finding whose figure the Context Check kept does
    not repeat the extraction-time issuer: its figure line already carries the
    verdict (``own``/``relayed``/``unattributed`` and the organisation named for
    it), so one page states its attribution once.
    """
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
                        f"subject {ctx.subject or 'none'} | "
                        f"kind {ctx.kind} | {_cited_attribution(ctx, finding)} | "
                        f"evidence: {result.evidence_words or ''}"
                    )
        body = "; ".join(figures) if figures else "(no kept figures)"
        lines.append(f"  {label}: {body}")
        lines.append(
            f"    page: {finding.source_title} ({publisher_identity(finding.source_url)})"
        )
        lines.append(f'    snippet: "{finding.snippet or finding.content}"')
        passage = item.passages.get(finding_fingerprint(finding))
        if passage:
            # The wider words the snippet was cut out of (improvement 8), so a
            # condition or exception just past the cut is judged, not guessed.
            lines.append(f'    passage: "{passage}"')
        if figures:
            continue
        if finding.attributed_issuer:
            quote = (
                f' ("{finding.attribution_quote}")'
                if finding.attribution_quote else ""
            )
            lines.append(f"    attributed to: {finding.attributed_issuer}{quote}")
        else:
            lines.append(f"    read at: {publisher_identity(finding.source_url)}")
    return "\n".join(lines)


def _cited_attribution(context: FigureContext, finding: Finding) -> str:
    """The attribution a cited figure's line shows, with no body to claim for none.

    ``claimed_organisation``'s rule for the one line the Statement Check reads
    (improvement 7): an unattributed figure of a page that serves another body's
    work has no organisation for this reporter to name, and naming the page's
    owner is what invites a sentence that credits the relaying site. Every other
    figure keeps "attribution (organisation)" exactly as before.
    """
    organisation = claimed_organisation(context, finding)
    return f"{context.attribution} ({organisation})" if organisation else context.attribution



def statement_check_messages(
    items: Sequence[StatementCheckItem], *, question: str
) -> list[ChatMessage]:
    """One batch's request: every sentence, its label, its cited findings' figures.

    Static first (PD-29): the response contract and the reply format lead, so
    every batch of every sub-topic shares them as a cacheable prefix.
    """
    blocks = [
        f"## {item.label}\nsentence: {item.text}\ncited findings:\n{_statement_cited_lines(item)}"
        for item in items
    ]
    static = [
        f"# Response contract\n{STATEMENT_CHECK_INSTRUCTION}",
        "# Reply format\n"
        + render_structured_reply_format(_STATEMENT_CHECK_REPLY_EXAMPLES),
    ]
    material = [
        f"# Question\n{question}",
        "# Statements to check\n" + "\n\n".join(blocks),
    ]
    return [
        ChatMessage(role="developer", content=STATEMENT_CHECK_SYSTEM_PROMPT),
        ChatMessage(
            role="user", content=render_structured_request(static, material)
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
    except ProviderConfigurationError:
        # The same configuration fault as the Context Check's (P3-5): the run
        # halts instead of keeping every sentence as an unchecked draft.
        raise
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
    batch_size: int = CONTEXT_CHECK_BATCH_SIZE,
    concurrency: int = CONTEXT_CHECK_CONCURRENCY,
) -> tuple[dict[str, StatementVerdictDraft | None], list[ResearchError]]:
    """Spec §6.2's Statement Check (D8): does a drafted sentence state only
    what the verified findings it cites actually carry?

    Parallel batches of ``batch_size`` items, at most ``concurrency`` in
    flight — the same two bounds the Context Check runs under, and by default
    the module constants that stand in for them. The Report Writer passes its
    own configured ``agents.verifier_batch_size`` and
    ``agents.verifier_concurrency`` (PD-12), so one config value bounds both
    checks. ``None`` means the item was not judged: its own batch
    failed outright, or the reply did not name its label. Either way the
    caller keeps the sentence as drafted and one
    ``evidence_verifier_statement_check_failed`` error is recorded for the
    batch. A reply's ``corrected`` verdict with a blank ``corrected_text``
    is treated as ``inconsistent``; nothing else is applied to the reply.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    errors: list[ResearchError] = []
    batches = [
        items[i : i + batch_size]
        for i in range(0, len(items), batch_size)
    ]
    gate = asyncio.Semaphore(concurrency)

    async def one(batch: Sequence[StatementCheckItem]) -> dict[str, StatementVerdictDraft | None]:
        async with gate:
            return await _check_statement_batch(provider, batch, question, errors, fingerprint, split=True)

    results: dict[str, StatementVerdictDraft | None] = {}
    for batch_result in await asyncio.gather(*(one(batch) for batch in batches)):
        results.update(batch_result)
    return results, errors
