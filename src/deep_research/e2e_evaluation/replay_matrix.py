"""The versioned offline matrix: twenty-one real-agent scenarios.

The manifest below is the *declared inventory* the release proof is measured
against. Each row names a case id, the version of its semantics, the product
result the plan expects, the decisive assertion that makes it that result, and
the scenario builder that drives the real stack.

Every case runs the production agents through the real graph. The
``ScriptedGraphAgent`` cases in :mod:`deep_research.e2e_evaluation.cases`
remain as graph-only historical regression tests; nothing here instantiates
that double.

Two things about the fixtures are worth stating once, because every case
depends on them. A page states its own claim in prose, and the excerpt the run
is allowed to cite is a literal substring of that prose: a fixture cannot
assert entailment its own page does not carry. And a page is only half of an
independent pair when its host is a different registrable publisher *and* its
work is a different document, so every pair below is two publishers with two
works - except where a case exists to show that one work on two hosts is not
two.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from deep_research.e2e_evaluation.replay import (
    CaseExpectation,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
)
from deep_research.memory.entries import MemoryEntry

REPLAY_CASE_MANIFEST_VERSION = 1

# The case-schema version each case's semantics are pinned at. Bumping a case
# means changing its declaration here and in its own builder together, so a
# recorded result names the semantics it was produced under.
REPLAY_CASE_VERSION = 1

HTML = "text/html; charset=utf-8"
PDF = "application/pdf"


def _page(
    host: str,
    slug: str,
    title: str,
    claim: str,
    *,
    issuer: str,
    verdict: str = "verified",
    discovered: int = 1,
    status: int = 200,
    content_type: str = HTML,
    source_role: str = "original_report",
    transport_relation: str = "original",
    text: str | None = None,
    cache_artifact: Literal["", "valid", "stale", "forged"] = "",
    cached_text: str = "",
    context: dict[str, str] | None = None,
    statement: dict[str, str] | None = None,
    vintage: str = "",
    recorded_scope: str = "",
) -> ReplaySource:
    """One authored page, with the run's read of it scripted around it.

    The prose is the fixture's whole point: the claim is stated in a sentence
    the page really contains, by an issuer the page really names ("Published
    by ..."), because those two facts are what the read is judged on. The
    verdict travels with the page rather than with the pair, so a case can
    script an outage, a refusal, or a contradiction on exactly one read.

    ``cache_artifact`` is the read an earlier session left in the run's source
    cache for this page, and ``cached_text`` is the body a ``stale`` or
    ``forged`` one stores. A page with no artifact is a page this run has to
    fetch itself.
    """
    body = text or f"{title}. Published by {issuer}. The report states {claim}."
    if claim not in body:
        raise ValueError(f"the page for {slug!r} does not state its own claim")
    return ReplaySource(
        url=f"https://{host}/{slug}",
        title=title,
        text=body,
        excerpt=claim,
        claim=claim,
        issuer=issuer,
        verdict=verdict,
        source_role=source_role,
        transport_relation=transport_relation,
        discovered_on_search=discovered,
        status_code=status,
        content_type=content_type,
        cache_artifact=cache_artifact,
        cached_text=cached_text,
        context=context or {},
        statement=statement or {},
        vintage=vintage,
        recorded_scope=recorded_scope,
    )


def _pair(
    index: int,
    slug: str,
    title: str,
    claim: str,
    *,
    issuer_a: str | None = None,
    issuer_b: str | None = None,
    host_a: str | None = None,
    host_b: str | None = None,
    verdict_a: str = "verified",
    verdict_b: str = "verified",
    discovered_a: int = 1,
    discovered_b: int = 1,
    content_b: str = HTML,
) -> tuple[ReplaySource, ReplaySource]:
    """Two independent accounts of one claim, one read each.

    The two hosts are per-case (``agency3`` vs ``bureau3``) so no case can
    borrow another's publisher identity, and the two documents are distinct
    texts, which is what makes them a pair rather than a mirror.
    """
    return (
        _page(
            host_a or f"agency{index}.example.test",
            slug,
            title,
            claim,
            issuer=issuer_a or f"Acme Institute {index}",
            verdict=verdict_a,
            discovered=discovered_a,
        ),
        _page(
            host_b or f"bureau{index}.example.test",
            f"{slug}-panel",
            f"{title} (independent panel)",
            claim,
            issuer=issuer_b or f"Independent Bureau {index}",
            verdict=verdict_b,
            discovered=discovered_b,
            content_type=content_b,
        ),
    )


def _topic(
    index: int,
    title: str,
    question: str,
    dimension: str,
    query: str,
    sources: tuple[ReplaySource, ...],
    *,
    critical: bool = False,
    labels: tuple[str, str] | None = None,
    follow_up_queries: tuple[str, ...] = (),
) -> ReplayTopic:
    """One planned sub-topic, with the labels its answer row will carry.

    The labels are published only if the row's evidence attests their words,
    so they are checked against the pages here rather than trusted: a fixture
    whose label appears on no page would be asserting a row the composer
    repairs to ``not stated``.
    """
    if labels is not None and sources:
        attested = " ".join(source.claim for source in sources)
        for label in labels:
            if label and label not in attested:
                raise ValueError(
                    f"answer label {label!r} appears on no page of {title!r}"
                )
    return ReplayTopic(
        title=title,
        question=question,
        dimensions=(dimension,),
        critical=critical,
        query=query,
        sources=sources,
        answer_labels=labels or ("", ""),
        follow_up_queries=follow_up_queries,
    )


def _filler(index: int, title: str, subject: str, figure: str) -> ReplayTopic:
    """One ordinary answered topic, so a case's own subject can be the exception.

    The measure is a *value* in the one vocabulary both of the contract's
    tables read: the planning check that credits a claim with an obligation
    and the composition check that reads an answer back out. A dimension in
    neither table — "workforce", "supplier count" — is an obligation no
    evidence can ever be credited for however plainly the pages state it, and
    a fixture that declared one would be testing its own typo.
    """
    claim = f"the {subject} in the United States was {figure} in 2024"
    measure = subject.removeprefix("Acme widget ")
    return _topic(
        index,
        title,
        f"What was the {subject} in the United States in 2024?",
        "value",
        f"{subject} United States 2024",
        _pair(index, f"{title.lower().replace(' ', '-')}-2024", title, claim),
        labels=("Acme widget", measure),
    )


# --- the declared cases ------------------------------------------------------


def _broad_constraints() -> ReplayScenario:
    """Six obligations, five-item batching, and every one of them answered.

    The batch ceiling is five claims per extraction pass, so a plan with six
    obligations is the smallest plan where a late topic can be starved by
    scheduling rather than by evidence. Each topic is an independently
    supported pair, and the case names every figure in the published report,
    including the two that only a later batch can carry.
    """
    # The dimension of each topic is a word the contract can check on both of
    # its tables, and the figure is the one the page states: six ordinary
    # measured facts, each independently published by two bodies.
    authored = (
        ("rate", "Acme widget adoption rate", "was 40 percent"),
        ("amount", "Acme widget funding round", "raised 12 million dollars"),
        (
            "quantity of supplier records listed",
            "number of supplier records the Acme widget supply registry listed",
            "was 1.2 million",
        ),
        ("value", "Acme widget export volume", "was 3.4 million units"),
        ("capacity", "Acme widget production capacity", "was 8 million units"),
        ("level", "Acme widget workforce", "was 12 thousand people"),
    )
    topics: list[ReplayTopic] = []
    for index, (dimension, subject, rest) in enumerate(authored, start=1):
        claim = f"the {subject} in the United States {rest} in 2024"
        title = f"Acme widget measure {index}"
        topics.append(
            _topic(
                index,
                title,
                f"What was the {subject} in the United States in 2024?",
                dimension,
                f"{subject} United States 2024",
                _pair(index, f"acme-{index}-2024", title, claim),
                critical=index <= 2,
                labels=("Acme widget", subject),
            )
        )
    return ReplayScenario(
        case_id="broad-constraints",
        version=REPLAY_CASE_VERSION,
        question=(
            "What do the six published measures say about the Acme widget in 2024?"
        ),
        topics=tuple(topics),
        max_iterations=3,
        # Six obligations need six researched sub-topics: pinned independent
        # of the production default (spec §7.2 caps that default at 5) so
        # this case stays a real test of a six-topic plan regardless of it.
        agent_overrides={"max_sub_topics": 7},
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=tuple(
                f"topic-{index:02d}-target-01" for index in range(1, 7)
            ),
            minimum_answered_findings=6,
            # Each entry is the number-and-unit reading its page states, in the
            # order its own claim writes it. The third is worth spelling out:
            # its page says "the number of supplier records ... was 1.2
            # million", so the figure is "1.2 million" and what it counts is
            # the row's own label. Asserting "1.2 million supplier records"
            # would be asserting a word order no requirement pins and no
            # rendering of that claim produces.
            required_report_phrases=(
                "40 percent",
                "12 million dollars",
                "1.2 million",
                "3.4 million units",
                "8 million units",
                "12 thousand people",
            ),
        ),
    )


def _comparative_conflict() -> ReplayScenario:
    """Two populations measured two ways, and no invented national figure.

    Both accounts are separately corroborated and both are cited, and each is
    published as the reading it is: neither figure is promoted into a national
    one, because nothing in the run measured a nation. The question is written
    as the comparison it is - a question that reads as causal stamps a
    mechanism obligation onto every target, and a measurement can never fill
    one, so the case would be asserting the answer form rather than the
    comparison.

    Each figure is written as the share it is, naming the base it is taken of:
    the comparison answer form asks for one shared basis and unit, and the
    evidence has to carry that basis rather than leave it to be inferred from
    the population the sentence happens to mention. A percentage whose
    denominator is missing cannot fill the comparison dimension, which is the
    product's own contract for a share rather than a reading of it.

    The third topic is the plan's context work, and it is written the same
    way. The answer contract stamps the comparison's form onto *every* target
    the plan carries (``planner.apply_answer_contract``), so a topic whose
    evidence states a bare value is an obligation no reading of it can
    discharge - the same reason ``_filler`` cannot serve a comparison
    question, and the same reason this case's question is not written as a
    causal one.
    """
    urban = (
        "the Acme widget adoption rate was 40 percent of urban households in the "
        "United States in 2024"
    )
    rural = (
        "the Acme widget adoption rate was 25 percent of rural households in the "
        "United States in 2024"
    )
    suburban = (
        "the Acme widget adoption rate was 30 percent of suburban households in "
        "the United States in 2024"
    )
    return ReplayScenario(
        case_id="comparative-conflict",
        version=REPLAY_CASE_VERSION,
        question=(
            "What was the difference between the Acme widget adoption rate in "
            "urban households and in rural households in the United States in "
            "2024?"
        ),
        topics=(
            _topic(
                1,
                "Urban adoption",
                "What was the Acme widget adoption rate in urban households in the "
                "United States in 2024?",
                "rate",
                "Acme widget adoption urban households 2024",
                _pair(1, "urban-2024", "Urban household survey", urban),
                critical=True,
                labels=("Acme widget", "urban households"),
            ),
            _topic(
                2,
                "Rural adoption",
                "What was the Acme widget adoption rate in rural households in the "
                "United States in 2024?",
                "rate",
                "Acme widget adoption rural households 2024",
                _pair(2, "rural-2024", "Rural household survey", rural),
                critical=True,
                labels=("Acme widget", "rural households"),
            ),
            _topic(
                3,
                "Suburban adoption",
                "What was the Acme widget adoption rate in suburban households in "
                "the United States in 2024?",
                "rate",
                "Acme widget adoption suburban households 2024",
                _pair(3, "suburban-2024", "Suburban household survey", suburban),
                labels=("Acme widget", "suburban households"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            forbidden_assertions=(
                "definitive national rate",
                "nationally representative",
            ),
            minimum_answered_findings=3,
            required_report_phrases=("urban households", "rural households"),
            required_invariants=("both_accounts_cited",),
        ),
    )


def _extra_pass_recovers_missing_target() -> ReplayScenario:
    """The missing account is published later, and the repair round finds it.

    The opening round can read one publisher: the claim is competent but
    uncorroborated, so its obligation is not met and the run owes itself a
    repair. The second round's search is what surfaces the second publisher,
    which is the only way the target can be answered - so a run that recovered
    it did so by acquiring new evidence, not by re-reading what it had.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="extra-pass-recovers-missing-target",
        version=REPLAY_CASE_VERSION,
        question="What is the corroborated Acme widget adoption rate for 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                _pair(1, "adoption-2024", "Adoption survey", claim, discovered_b=2),
                critical=True,
                labels=("Acme widget", "adoption rate"),
                follow_up_queries=(
                    "Acme widget adoption rate United States 2024 second source",
                ),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_invariants=("extra_pass_recovers_missing_target",),
        ),
    )


def _blocked_html_pdf_fallback() -> ReplayScenario:
    """A refused page, an official document behind it, and a mirror.

    The landing page answers 403, so the run records that exact URL as denied
    and must stop asking for it. The figure is in the document the same issuer
    published, which a second publisher corroborates, and a third host mirrors
    the official document word for word: the mirror is a read, but it is not a
    second work, so it can neither make a pair nor be counted twice.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    landing = _page(
        "agency4.example.test",
        "report",
        "Adoption report landing page",
        claim,
        issuer="Acme Institute 4",
        verdict="insufficient_evidence",
        status=403,
    )
    official = _page(
        "agency4.example.test",
        "report.pdf",
        "Adoption report",
        claim,
        issuer="Acme Institute 4",
        content_type=PDF,
    )
    corroborating = _page(
        "bureau4.example.test",
        "panel.pdf",
        "Adoption panel",
        claim,
        issuer="Independent Bureau 4",
        content_type=PDF,
    )
    mirror = _page(
        "mirror4.example.test",
        "report.pdf",
        "Adoption report (mirror)",
        claim,
        issuer="Acme Institute 4",
        # Word for word is what a mirror is: the same body, served by a second
        # host. A fixture that re-worded the copy would hand the run two
        # different works, and the read it must not promote to a second origin
        # would not be the same read at all.
        text=official.text,
        content_type=PDF,
        # And a copy is what it says it is. Two reads of one work both
        # declared *original* leave the run no way to tell which host serves
        # the running copy, so the reference list keeps whichever one it
        # reaches first - which is the session's own read order, and the
        # published citation moves between hosts from run to run. Naming the
        # mirror is the fact the collapse rule reads to prefer the original.
        transport_relation="mirror",
    )
    return ReplayScenario(
        case_id="blocked-html-pdf-fallback",
        version=REPLAY_CASE_VERSION,
        question="What is the corroborated Acme widget adoption rate for 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (landing, official, corroborating, mirror),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            # The refusal is what this case is about: the landing page answers
            # 403, and the run records the denial before reading the document
            # behind it. A refusal that left no record would be
            # indistinguishable from a page nobody ever asked for, which is
            # the other half of the invariant this case asserts.
            allowed_failure_classes=("error:agent_tool_failed",),
            required_invariants=(
                "denied_url_not_retried",
                "mirror_not_double_counted",
            ),
        ),
    )


def _same_work_mirror() -> ReplayScenario:
    """One work on two hosts is one work, however many publishers print it.

    The same document text is served by two hosts. Both reads are real and both
    are admitted, and the fact is still one fact: PD-9 merges figures by value,
    organisation, period and kind, so the reprint neither doubles the key facts
    row nor mints a second account of the figure the original states. The row's
    assertion is the count and the label: one row, credited to the organisation
    both hosts print.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    same_text = (
        "Acme widget adoption bulletin. Published by Acme Institute 5. "
        "The report states the Acme widget adoption rate in the United States "
        "was 40 percent in 2024."
    )
    return ReplayScenario(
        case_id="same-work-mirror",
        version=REPLAY_CASE_VERSION,
        question="What is the corroborated Acme widget adoption rate for 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (
                    _page(
                        "agency5.example.test",
                        "bulletin-2024",
                        "Adoption bulletin",
                        claim,
                        issuer="Acme Institute 5",
                        text=same_text,
                    ),
                    _page(
                        "reprint5.example.test",
                        "bulletin-2024",
                        "Adoption bulletin (reprint)",
                        claim,
                        issuer="Acme Institute 5",
                        text=same_text,
                    ),
                ),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            # PD-9: the same value for the same organisation, period and kind is
            # one fact with or without a target. The reprint neither adds a row
            # nor blocks the obligation: the topic is answered by the row the
            # original page produced, and the case's whole assertion is that one
            # body served twice is one fact.
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_invariants=("no_false_verification", "mirror_not_double_counted"),
        ),
    )


def _extra_pass_finds_nothing() -> ReplayScenario:
    """A repair round that buys nothing must stop, and say what stopped it.

    One uncorroborated claim owes an obligation, the repair acquires, and the
    search returns the page the run already read. Nothing changes, so the
    second pass is the last one: a run that kept buying the same empty round
    would spend its budget proving nothing, and the case asserts the recorded
    stop reason rather than the exit code alone.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="extra-pass-finds-nothing",
        version=REPLAY_CASE_VERSION,
        question="What is the corroborated Acme widget adoption rate for 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (
                    _page(
                        "agency7.example.test",
                        "adoption-2024",
                        "Adoption survey",
                        claim,
                        issuer="Acme Institute 7",
                        verdict="insufficient_evidence",
                    ),
                ),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            minimum_answered_findings=2,
            required_gap_kinds=("hard:unaccounted_required_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "semantic_review_missing",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("extra_pass_finds_nothing",),
        ),
    )


def _relay_labelled_as_relay() -> ReplayScenario:
    """A relayed measurement is labelled as relayed, never as the relay's own.

    Review focus, honesty rule 1: a relay is never presented as the organisation
    it relays. The four pages here are a wire service's articles, each naming
    Acme Institute as the body that measured the figure. The Context Check
    proposes the relay and code confirms it against the page's own cue, so the
    reader's label has to name both: the site that relays, and the organisation
    the words credit. A run that resolved the figure to the site that published
    the article would print "Wire Service's own figure", which is the exact
    phrase this row forbids.

    Two shapes have to be right for the target to be answered at all, and both
    are properties of the prose rather than of the harness: the clause must
    name the organisation the way the attribution contract reads a name, and it
    must state the value, the year and the geography the frozen contract
    requires. A page whose sentence omits any of them leaves the target
    unanswered however plainly it relays.

    The four pages state the same figure in four different sentences on
    purpose. One relay of one figure is exactly the shape that must keep its
    attribution, and four pages carrying one identical sentence would be one
    claim recorded four times rather than four relays of it.
    """
    authored = (
        (
            "measurement report",
            "Acme Institute widget measurement report",
            "measurement",
            "the measured efficiency of the Acme widget",
            "measured value",
            "Acme widget",
            "measured efficiency",
            (
                "According to Acme Institute the measured efficiency of the "
                "Acme widget in the United States is 42 percent in 2025"
            ),
        ),
        (
            "measurement method",
            "Acme Institute widget measurement method note",
            "method",
            "the method the Acme Institute measured with",
            "observation period",
            "Acme Institute",
            "official method",
            (
                "According to Acme Institute the Acme widget efficiency "
                "measured under the official method in the United States is "
                "42 percent in 2025"
            ),
        ),
        (
            "label figure",
            "Acme Institute widget label figure note",
            "label",
            "the figure printed on the Acme widget label",
            "geography",
            "Acme widget",
            "United States",
            (
                "According to Acme Institute the efficiency printed on the "
                "Acme widget label in the United States is 42 percent in 2025"
            ),
        ),
        (
            "registry entry",
            "Acme Institute widget registry entry",
            "registry",
            "the Acme Institute's own registry entry",
            "attribution",
            "Acme Institute",
            "official registry",
            (
                "According to Acme Institute the Acme widget efficiency "
                "recorded in the official registry in the United States is 42 "
                "percent in 2025"
            ),
        ),
    )
    sources = {
        label: _page(
            # The relaying site: PD-8 reads the page's own words for the
            # figure, and the publication belongs to the relay. Every claim
            # names Acme Institute with an attribution cue, so the relay
            # resolves -- and the label the reader sees has to say so.
            "wire.example.test",
            url_slug,
            title,
            claim,
            issuer="Wire Service",
            source_role="company_statement",
            context={
                "attribution": "relayed",
                "organisation": "Acme Institute",
                "verdict": "confirm",
            },
        )
        for (
            label,
            title,
            url_slug,
            _subject,
            _dimension,
            _row_subject,
            _row_dimension,
            claim,
        ) in authored
    }
    topics = tuple(
        _topic(
            index,
            f"Acme widget {label}",
            (
                "What measured efficiency does the official Acme Institute "
                f"report state for {subject}?"
            ),
            "value",
            f"Acme Institute Acme widget {label} official measurement",
            (sources[label],),
            critical=index == 1,
            labels=(row_subject, row_dimension),
        )
        for index, (
            label,
            _title,
            _url_slug,
            subject,
            _dimension,
            row_subject,
            row_dimension,
            _claim,
        ) in enumerate(authored, start=1)
    )
    return ReplayScenario(
        case_id="relay-labelled-as-relay",
        version=REPLAY_CASE_VERSION,
        question=(
            "What measured efficiency does the official Acme Institute "
            "report state for the Acme widget?"
        ),
        topics=topics,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=tuple(
                f"topic-{index:02d}-target-01" for index in range(1, 5)
            ),
            forbidden_assertions=(
                "Wire Service's own figure",
                "independently corroborated",
            ),
            minimum_answered_findings=4,
            # The two halves of the honesty rule: the label names the relaying
            # site and the organisation the page credits, and the figure the
            # report rests on carries a resolved context rather than a
            # verification nobody made.
            required_invariants=(
                "relay_labelled_as_relay",
                "no_false_verification",
            ),
        ),
    )


def _forecast_versus_actual_kept_apart() -> ReplayScenario:
    """A projection cannot stand in for the current figure the question asks for.

    The question is about 2024 and the only page about it states what 2030 is
    expected to bring. The two dates are different facts about the world, and
    the run has to keep them apart: the projection stays a projection, the
    obligation for the current figure stays outstanding, and the report cannot
    present the forecast as the answer.
    """
    projection = (
        "the Acme widget adoption rate in the United States is projected to "
        "reach 55 percent in 2030"
    )
    current = (
        "the Acme widget adoption rate in the United States was 40 percent in 2024"
    )
    return ReplayScenario(
        case_id="forecast-versus-actual-kept-apart",
        version=REPLAY_CASE_VERSION,
        question="What is the Acme widget adoption rate in the United States today?",
        topics=(
            _topic(
                1,
                "Current adoption",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States current",
                (
                    _page(
                        "agency8.example.test",
                        "outlook-2030",
                        "Adoption outlook",
                        projection,
                        issuer="Acme Institute 8",
                        verdict="insufficient_evidence",
                    ),
                ),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _topic(
                3,
                "Adoption history",
                "What did the Acme widget adoption rate in the United States "
                "measure in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024 history",
                _pair(3, "history-2024", "Adoption history", current),
                critical=False,
                labels=("Acme widget", "adoption rate"),
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            # The forbidden string is the substitution itself, not the word
            # "today": the question names the present, so its own echo in the
            # title is the question being asked rather than an answer to it.
            forbidden_assertions=("currently 55",),
            minimum_answered_findings=2,
            required_gap_kinds=("hard:unaccounted_required_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "semantic_review_missing",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("forecast_not_substituted_for_observation",),
        ),
    )


def _unsupported_mechanism() -> ReplayScenario:
    """Citations and formatting cannot rescue an invented causal mechanism.

    Every page states what happened and none states why, and the scripted
    writer is handed two things to publish anyway: a cause, and a recommendation
    that follows from it. Neither is evidence, and the product refuses them in
    the two places it can. The recommendation is prose the composer is able to
    recognise - it never reaches the reader, and the case names the phrase as
    forbidden so a run that published it fails here. The invented cause cannot
    be recognised that way, so what the run refuses is the *answer*: the
    mechanism obligation stays outstanding, and the pair of pages the topic
    really did read cannot fill it, because a citation is not a cause.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="unsupported-mechanism",
        version=REPLAY_CASE_VERSION,
        question="Why did Acme widget adoption change in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                _pair(1, "adoption-2024", "Adoption survey", claim),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _topic(
                2,
                "Adoption mechanism",
                "What mechanism increased Acme widget adoption in the United States in "
                "2024?",
                # The obligation is about a cause, so the dimension has to read
                # as one to the composition table that decides whether the
                # recorded propositions state it — but a dimension whose head
                # *is* "mechanism" is one the planning table cannot credit to
                # any claim at all, however plainly the pages state the cause.
                # The planner's own dimensions are written "<kind>: <prose>"
                # ("measure: annual ridership"), which is the phrasing that
                # both tables read: the head names a measurable kind and the
                # prose names the cause.
                "measure: the mechanism behind the change",
                "Acme widget adoption mechanism United States 2024",
                _pair(2, "mechanism-2024", "Adoption mechanism note", claim),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                3, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
        ),
        invented_prose="the agency should subsidise Acme widget deployment",
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            # A negative case names no obligation it expects answered: the
            # cause is asked for by the question, and nothing read states one,
            # so every obligation stands unanswered and the gap is the result.
            required_target_ids=(),
            forbidden_assertions=("should subsidise",),
            minimum_answered_findings=2,
            required_gap_kinds=("hard:unaccounted_required_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "semantic_review_missing",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
                # The drafted recommendation is refused by the Statement
                # Check, and the refusal reaches the reader only as the
                # forbidden phrase's absence: a refused point is published in
                # the composition's own refusals rather than as an error,
                # because "no judgement" is not this case's subject.
            ),
            required_invariants=("mechanism_obligation_stays_unanswered",),
        ),
    )


def _review_unavailable() -> ReplayScenario:
    """Complete reader artifacts, and no semantic judgement to accept them.

    The report is composed and published and every obligation is answered, and
    the terminal review cannot be made. A structural clean bill of health is
    not an acceptance: with no judgement recorded, the run must not pass
    strict mode, and the artifacts it did produce stay readable.
    """
    return ReplayScenario(
        case_id="review-unavailable",
        version=REPLAY_CASE_VERSION,
        question="What do the published measures say about the Acme widget in 2024?",
        topics=(
            _filler(1, "Widget adoption", "Acme widget adoption rate", "40 percent"),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        review_failure=True,
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_gap_kinds=("semantic_review_missing",),
            allowed_failure_classes=(
                # The provider failure has a name of its own, and the case
                # requires it to be recorded: an outage that left no trace
                # would be indistinguishable from a review nobody ran.
                "error:graph_report_review_unavailable",
            ),
            required_report_phrases=("40 percent", "12 million dollars"),
            required_invariants=("review_missing_blocks_acceptance",),
        ),
    )


def _non_constraint_answer() -> ReplayScenario:
    """A factual question gets an answer table, not a ranking it never asked for.

    The answer kind here is factual, so the reader's structure is the direct
    answer: no ranked constraint rows, and nothing presented as a best option.
    A run that filled the ranking because the composer knows how to would be
    answering a question the user did not ask.
    """
    return ReplayScenario(
        case_id="non-constraint-answer",
        version=REPLAY_CASE_VERSION,
        question=(
            "What did Acme widget adoption and funding measure in the United "
            "States in 2024?"
        ),
        topics=(
            _filler(1, "Widget adoption", "Acme widget adoption rate", "40 percent"),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_invariants=("no_ranked_constraints_for_a_factual_answer",),
        ),
    )


def _empty_but_clean() -> ReplayScenario:
    """No usable evidence, clean headings, and no answer.

    Every page the run can reach carries a title and a date and nothing a
    claim can be made of. The report that comes out is tidy and says nothing,
    and a tidy report that answered no obligation is a failed run however
    clean its structure reads.
    """
    def _bare(index: int, subject: str) -> ReplaySource:
        title = f"{subject} record"
        # The claim is written into the body verbatim and in its own case: the
        # guard that a page states the claim it is read for is a substring
        # check, so a sentence recased to start with a capital is a page that
        # does not state its claim, and a case that cannot even be built.
        claim = (
            "The record lists a title and a publication date and no measured "
            "value"
        )
        body = (
            f"{title}. Published by Acme Registry {index}. "
            f"{claim} for any period."
        )
        return _page(
            f"registry{index}.example.test",
            f"record-{index}",
            title,
            claim,
            issuer=f"Acme Registry {index}",
            verdict="insufficient_evidence",
            text=body,
        )

    return ReplayScenario(
        case_id="empty-but-clean",
        version=REPLAY_CASE_VERSION,
        question="What did the Acme widget measures show in 2024?",
        topics=(
            _topic(
                1,
                "Widget adoption",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (_bare(1, "Adoption"),),
                critical=True,
                labels=("record", "publication date"),
            ),
            _topic(
                2,
                "Widget funding",
                "How much funding did the Acme widget programme raise in 2024?",
                "amount",
                "Acme widget funding round United States 2024",
                (_bare(2, "Funding"),),
                critical=True,
                labels=("record", "publication date"),
            ),
            _topic(
                3,
                "Widget exports",
                "What was the Acme widget export volume in the United States in 2024?",
                "value",
                "Acme widget export volume United States 2024",
                (_bare(3, "Export"),),
                critical=False,
                labels=("record", "publication date"),
            ),
        ),
        max_iterations=2,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_gap_kinds=("hard:unaccounted_required_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "semantic_review_missing",
                "no_quality_snapshot",
            ),
            required_invariants=("empty_answer_answered_nothing",),
        ),
    )


def _memory_is_not_read() -> ReplayScenario:
    """Remembered claims are leads, and a lead is not a read.

    Long-term memory holds two generated claims about one figure with
    different URLs. Neither is a read: the run owes an original-source read
    for either of them, so the obligation stays open and no remembered prose
    reaches the reader as a finding. The lead itself is admissible, which is
    what lets a later run spend its budget on the sources rather than on
    rediscovering them.
    """
    memory_claim = (
        "the Acme widget adoption rate in the United States was 47 percent in 2024"
    )
    remembered = (
        "the Acme widget adoption rate in the United States was 47 percent in 2024",
        "urban Acme widget adoption in the United States reached 47 percent in 2024",
    )
    lead_urls = (
        "https://memory-a.example.test/adoption-2024",
        "https://memory-b.example.test/urban-2024",
    )
    return ReplayScenario(
        case_id="memory-is-not-read",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (),
                critical=True,
                labels=("", ""),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        memory_entries=tuple(
            MemoryEntry(
                entry_type="finding",
                content=content,
                session_id="earlier-session",
                agent_id="researcher",
                confidence=0.95,
                source_url=url,
                source_title="Acme widget adoption note",
            )
            for content, url in zip(remembered, lead_urls)
        ),
        max_iterations=2,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            forbidden_assertions=(memory_claim,),
            minimum_answered_findings=2,
            required_gap_kinds=("hard:unaccounted_required_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "semantic_review_missing",
                # The critical topic declares no page of its own, and memory
                # holds the other session's claims rather than this run's
                # reads: the Researcher recalls the leads it is entitled to
                # and stops there, which the product records as a topic it
                # could take no further. The skips are the reopening pass
                # saying which topics it owed nothing, the same record the
                # other partial cases declare.
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("memory_leads_are_not_reads",),
        ),
    )


# The body an earlier session read for the adoption panel, before the panel
# revised it. It still states the figure the run's claim rests on — a stored
# version that did not would be a body no claim could be extracted from —
# which is what makes the changed page a *changed* page rather than a second
# account of something else.
_STALE_PANEL_BODY = (
    "Adoption panel draft. Published by Independent Bureau 11. The panel's "
    "first table states the Acme widget adoption rate in the United States "
    "was 40 percent in 2024; the survey instrument behind it was revised "
    "before publication."
)

# The body the forged record for the funding panel stores. Its record declares
# the digest of the page as it is now instead, so the artifact claims a
# provenance that belongs to a body it does not hold.
_FORGED_PANEL_BODY = (
    "Funding panel notes. Published by Independent Bureau 3. The panel's "
    "working notes were withdrawn before its report was published."
)


def _validated_cache_reuse() -> ReplayScenario:
    """Stored reads are validated and reused; the one that cannot be is not.

    An earlier session left three bodies in this run's source cache, and the
    run is handed them before its graph starts. The first is the survey page
    as it still is: this run validates it, admits it as an import, and
    answers two obligations from it without downloading it once — for the
    topic that carries it and again for the later topic that asks the same
    URL. The second is the version that session saw, which the live page is
    no longer: it is validated and reused as well, and the record says so —
    ``cache`` kind, the session that read the bytes, the moment this run
    validated them — so a stored version is never shown as this run's own
    fetch. The third is a forgery: a body filed under a digest that is not
    its own, which no consumer may serve, so the run fetches that page and
    keeps the read it made itself. Exactly one page is downloaded, and it is
    the forged one.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    shared = _page(
        "agency11.example.test",
        "adoption-2024",
        "Adoption survey",
        claim,
        issuer="Acme Institute 11",
        cache_artifact="valid",
    )
    stale = _page(
        "bureau11.example.test",
        "adoption-panel-2024",
        "Adoption panel",
        claim,
        issuer="Independent Bureau 11",
        cache_artifact="stale",
        cached_text=_STALE_PANEL_BODY,
    )
    funding = _filler(
        3, "Widget funding", "Acme widget funding round", "12 million dollars"
    )
    forged = replace(
        funding.sources[1],
        cache_artifact="forged",
        cached_text=_FORGED_PANEL_BODY,
    )
    return ReplayScenario(
        case_id="validated-cache-reuse",
        version=REPLAY_CASE_VERSION,
        question="What did the Acme widget adoption survey measure in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (shared, stale),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _topic(
                2,
                "Adoption rate (reported)",
                "Which Acme widget adoption rate did the survey report for the United "
                "States in 2024?",
                "rate",
                "Acme widget adoption survey reported rate 2024",
                (shared,),
                critical=False,
                labels=("Acme widget", "adoption rate"),
            ),
            replace(funding, sources=(funding.sources[0], forged)),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-03-target-01"),
            minimum_answered_findings=2,
            required_invariants=(
                "read_downloaded_once",
                "cache_provenance_is_validated",
            ),
            allowed_failure_classes=(
                "error:researcher_sub_topic_skipped",
                # The first read discharges topic-01's obligation and the
                # filler topic owes nothing, so both are skipped rather than
                # re-researched: the same record the other cases declare. The
                # second topic reusing the body the first already read is the
                # case's premise, and the verifier deduplicates the finding
                # that comes out of it rather than the run failing on it.
            ),
        ),
    )


def _decision_context_late_candidate() -> ReplayScenario:
    """The last candidate and the late section still reach the next request.

    Two of the candidates for the topic carry nothing a claim can be made of,
    and the account that does is last in the list. The run has to keep
    offering it: the decision packet lists it as a candidate, the extraction
    packet carries its excerpt, and the public observation summary stays at
    its shipped length rather than being enlarged to smuggle the passage
    through.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="decision-context-late-candidate",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (
                    # Both notes name the record they belong to and neither
                    # states a figure: the answer row drafted for them is
                    # checked against their own evidence, so a note that
                    # withheld even the subject would be refused as a drafted
                    # answer rather than read as a page with nothing to say.
                    _page(
                        "agency12.example.test",
                        "cover-note",
                        "Adoption cover note",
                        "the Acme widget adoption rate cover note lists a title "
                        "and a publication date and states no measured value",
                        issuer="Acme Institute 12",
                        verdict="insufficient_evidence",
                    ),
                    _page(
                        "agency12.example.test",
                        "method-note",
                        "Adoption method note",
                        "the Acme widget adoption rate method note describes the "
                        "method and states no measured value",
                        issuer="Acme Institute 12",
                        verdict="insufficient_evidence",
                    ),
                    _page(
                        "agency12.example.test",
                        "adoption-2024",
                        "Adoption survey",
                        claim,
                        issuer="Acme Institute 12",
                    ),
                    _page(
                        "bureau12.example.test",
                        "adoption-2024",
                        "Adoption panel",
                        claim,
                        issuer="Independent Bureau 12",
                    ),
                ),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_report_phrases=("40 percent",),
            required_invariants=(
                "late_candidate_reached_decision",
                "public_summary_stayed_short",
            ),
        ),
    )


def _missing_target_triggers_one_extra_pass() -> ReplayScenario:
    """A missing required target, and nothing else, buys the one extra pass.

    PD-5: the missing targets are computed by code and the Report Reviewer
    node stamps them on the record, so the run goes back for exactly the
    obligation that is missing — never for a judgement. The page that answers
    it is only published in the second round, so the extra pass is what turns
    the obligation into an answer, and the answer comes from a statement the
    evidence supports rather than from metadata the first round found.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="missing-target-triggers-one-extra-pass",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (
                    _page(
                        "agency13.example.test",
                        "record-2024",
                        "Adoption record",
                        # The record names what it is a record of — the answer
                        # row's subject and dimension are checked against the
                        # row's own evidence, so a page that withheld even the
                        # subject would be refused as a drafted answer rather
                        # than as an unsupported figure.
                        "the Acme widget adoption rate record lists a title "
                        "and a publication date and states no measured value",
                        issuer="Acme Registry 13",
                        verdict="insufficient_evidence",
                    ),
                    _page(
                        "bureau13.example.test",
                        "adoption-2024",
                        "Adoption panel",
                        claim,
                        issuer="Independent Bureau 13",
                        discovered=2,
                    ),
                    _page(
                        "agency13.example.test",
                        "adoption-2024",
                        "Adoption survey",
                        claim,
                        issuer="Acme Institute 13",
                        discovered=2,
                    ),
                ),
                critical=True,
                labels=("Acme widget", "adoption rate"),
                follow_up_queries=(
                    "Acme widget adoption rate United States 2024 second source",
                ),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_invariants=("missing_target_triggers_one_extra_pass",),
        ),
    )


# --- the manifest ------------------------------------------------------------


def _figure_not_on_page_dropped() -> ReplayScenario:
    """A figure the passage does not state is refused, and never published.

    The extractor here records 88 percent for a page that states 40. D8 puts
    that judgement in the Context Check -- the prompt tells it to reject a
    figure its snippet or passage does not actually state -- and code records
    the refusal with the checker's own reason. The obligation is still answered
    by the account that does state its figure, and the refused number reaches
    the reader nowhere.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    honest = _page(
        "agency12.example.test",
        "adoption-2024",
        "Adoption survey",
        claim,
        issuer="Acme Institute 12",
    )
    inflated = _page(
        "panel12.example.test",
        "adoption-2024-panel",
        "Adoption panel",
        claim,
        issuer="Independent Bureau 12",
        context={"verdict": "reject"},
    )
    # The figure the scripted extraction records is not the one the page
    # states: that is the fault Figure Match no longer judges and the Context
    # Check now does.
    inflated = replace(
        inflated, figures=(("88", "percent", "2024", "actual"),)
    )
    return ReplayScenario(
        case_id="figure-not-on-page-dropped",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (honest, inflated),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            forbidden_assertions=("88 percent",),
            minimum_answered_findings=3,
            required_invariants=("figure_not_on_page_dropped",),
        ),
    )


def _evidence_words_not_on_page_rejected() -> ReplayScenario:
    """A figure whose quoted words are not on its page is dropped, not published.

    D8 keeps exactly one code check on the Context Check's evidence: the words
    it quotes have to be the page's own. A reply that quotes prose the page
    does not carry is the one shape a confident model can still get wrong, and
    the finding is dropped with that reason rather than published on the
    model's word.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    honest = _page(
        "agency13.example.test",
        "adoption-2024",
        "Adoption survey",
        claim,
        issuer="Acme Institute 13",
    )
    quoted = _page(
        "panel13.example.test",
        "adoption-2024-panel",
        "Adoption panel",
        claim,
        issuer="Independent Bureau 13",
        context={
            "evidence_words": (
                "the panel measured 91 percent of every household in the country"
            ),
            "verdict": "confirm",
        },
    )
    return ReplayScenario(
        case_id="evidence-words-not-on-page-rejected",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (honest, quoted),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            forbidden_assertions=("91 percent",),
            minimum_answered_findings=3,
            required_invariants=("evidence_words_not_on_page_rejected",),
        ),
    )


def _scope_corrected_to_all_segments() -> ReplayScenario:
    """An all-segment figure written as grid-scale is corrected, and never printed.

    Review focus 4, the 18.9 GW audit finding: the page states
    "utility, C&I, and residential" and the extractor wrote "grid-scale". The
    Context Check corrects the scope, code keeps the correction because the
    words are the page's own, the finding is marked corrected, and the drafted
    sentence that kept the narrow wording is refused by the Statement Check
    with the scope named in its reason.
    """
    claim = (
        "The report states the U.S. energy storage market hit a record 18.9 "
        "gigawatts of battery energy storage system installations in 2025 "
        "across all segments"
    )
    return ReplayScenario(
        case_id="scope-corrected-to-all-segments",
        version=REPLAY_CASE_VERSION,
        question=(
            "How much battery energy storage did the United States install in 2025?"
        ),
        topics=(
            _topic(
                1,
                "Storage installations",
                "How much battery energy storage did the United States install in 2025?",
                "capacity",
                "United States battery energy storage installations 2025",
                (
                    _page(
                        "woodmac15.example.test",
                        "storage-2025",
                        "Storage market press release",
                        claim,
                        issuer="Wood Mackenzie",
                        recorded_scope="grid-scale",
                        context={
                            "scope": "all segments",
                            "evidence_words": (
                                "18.9 gigawatts of battery energy storage system "
                                "installations in 2025 across all segments"
                            ),
                            "verdict": "confirm",
                        },
                        statement={
                            "verdict": "inconsistent",
                            "reason": (
                                "the page states the figure across all segments, "
                                "not grid-scale"
                            ),
                        },
                    ),
                ),
                critical=True,
                labels=("energy storage market", "18.9 gigawatts"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            # The refusal is the case: the sentence that overstated the scope
            # never reaches the reader, so the obligation it carried publishes
            # under Not found rather than as an answer.
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            forbidden_assertions=("grid-scale",),
            minimum_answered_findings=2,
            required_gap_kinds=("hard:unaccounted_required_targets",),
            allowed_failure_classes=(
                "missing_required_target",
                "semantic_review_missing",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("scope_corrected_to_all_segments",),
        ),
    )


def _revision_noted() -> ReplayScenario:
    """Two editions of one fact are one row, and the reader is told.

    PD-9: the same value for the same organisation, period and kind is one
    fact, and two findings that answer one obligation while carrying different
    release keys are one fact *revised* rather than two readings. The earlier
    edition rides on the row the second one produced.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="revision-noted",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (
                    _page(
                        "agency16.example.test",
                        "adoption-2024",
                        "Adoption survey",
                        claim,
                        issuer="Acme Institute 16",
                        vintage="January 2025 edition",
                    ),
                    _page(
                        "agency16.example.test",
                        "adoption-2024-revised",
                        "Adoption survey (revised)",
                        claim,
                        issuer="Acme Institute 16",
                        vintage="February 2025 edition",
                    ),
                ),
                critical=True,
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_invariants=("revision_noted", "no_false_verification"),
        ),
    )


def _statement_check_failure_keeps_sentences() -> ReplayScenario:
    """A Statement Check that could not be made keeps every sentence.

    §5.4: the checker never stops the run. Every batch's call fails here, so
    every drafted sentence publishes as drafted, the failure is recorded, and
    the unjudged sentences are the reason the run does not pass strict mode --
    the report is published and the gap is named rather than the wording being
    waved through as judged.
    """
    return ReplayScenario(
        case_id="statement-check-failure-keeps-sentences",
        version=REPLAY_CASE_VERSION,
        question="What do the published measures say about the Acme widget in 2024?",
        topics=(
            _filler(1, "Widget adoption", "Acme widget adoption rate", "40 percent"),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        statement_failure=True,
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            minimum_answered_findings=3,
            required_gap_kinds=("hard:unjudged_sentences",),
            allowed_failure_classes=(
                "error:evidence_verifier_statement_check_failed",
            ),
            required_report_phrases=("40 percent", "12 million dollars"),
            required_invariants=("statement_failure_keeps_sentences",),
        ),
    )


class ReplayCaseEntry:
    """One declared matrix row: identity, expectation, and its builder.

    ``build`` is ``None`` for exactly the rows the matrix cannot replay: a
    historical row records what the product did when the six agents were
    scripted doubles, and there is no real-agent scenario to build for it.
    The two facts are the same fact, so the constructor refuses the pairings
    that would let them disagree.
    """

    def __init__(
        self,
        *,
        case_id: str,
        version: int,
        title: str,
        expected_product_result: str,
        decisive_assertion: str,
        build: Callable[[], ReplayScenario] | None,
        graph_only_historical: bool = False,
    ) -> None:
        if graph_only_historical != (build is None):
            raise ValueError(
                f"{case_id!r}: graph_only_historical must be True exactly "
                "when build is None"
            )
        self.case_id = case_id
        self.version = version
        self.title = title
        self.expected_product_result = expected_product_result
        self.decisive_assertion = decisive_assertion
        self.build = build
        self.graph_only_historical = graph_only_historical


REPLAY_CASE_MANIFEST: tuple[ReplayCaseEntry, ...] = (
    ReplayCaseEntry(
        case_id="broad-constraints",
        version=REPLAY_CASE_VERSION,
        title="Six obligations, five-item batching, all answered",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Six topics with substantive mechanisms; late topics survive "
            "five-item batching; independently supported critical conclusions"
        ),
        build=_broad_constraints,
    ),
    ReplayCaseEntry(
        case_id="comparative-conflict",
        version=REPLAY_CASE_VERSION,
        title="Three groups measured on one basis, no invented winner",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Explains differing populations/methods; no invented universal "
            "winner; both sources cited"
        ),
        build=_comparative_conflict,
    ),
    ReplayCaseEntry(
        case_id="extra-pass-recovers-missing-target",
        version=REPLAY_CASE_VERSION,
        title="The missing account is acquired in the repair round",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Only missing target is acquired/rechecked; new support changes "
            "fingerprint; no repeated stable checks"
        ),
        build=_extra_pass_recovers_missing_target,
    ),
    ReplayCaseEntry(
        case_id="blocked-html-pdf-fallback",
        version=REPLAY_CASE_VERSION,
        title="Denied page, published document, and a mirror that is one work",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Exact denied URL is not retried; discovered official PDF supports "
            "answer; mirror not double-counted"
        ),
        build=_blocked_html_pdf_fallback,
    ),
    ReplayCaseEntry(
        case_id="same-work-mirror",
        version=REPLAY_CASE_VERSION,
        title="One work reprinted on two hosts is one fact row",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "One body served twice produces one fact row, credited to the "
            "organisation both hosts print"
        ),
        build=_same_work_mirror,
    ),
    ReplayCaseEntry(
        case_id="extra-pass-finds-nothing",
        version=REPLAY_CASE_VERSION,
        title="A repair round that buys nothing stops",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Fully processed unchanged repair stops; correct unresolved "
            "target/cause"
        ),
        build=_extra_pass_finds_nothing,
    ),
    ReplayCaseEntry(
        case_id="relay-labelled-as-relay",
        version=REPLAY_CASE_VERSION,
        title="Official measurement answered as primary attribution",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The exact official measurement question is answered as "
            "primary-attributed, and not falsely verified."
        ),
        build=_relay_labelled_as_relay,
    ),
    ReplayCaseEntry(
        case_id="forecast-versus-actual-kept-apart",
        version=REPLAY_CASE_VERSION,
        title="A projection is not the current figure",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Historical observation/future forecast cannot satisfy a required "
            "current estimate"
        ),
        build=_forecast_versus_actual_kept_apart,
    ),
    ReplayCaseEntry(
        case_id="unsupported-mechanism",
        version=REPLAY_CASE_VERSION,
        title="An invented mechanism never reaches the reader",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Citations/formatting cannot rescue invented causal mechanism or "
            "recommendation"
        ),
        build=_unsupported_mechanism,
    ),
    ReplayCaseEntry(
        case_id="review-unavailable",
        version=REPLAY_CASE_VERSION,
        title="Complete artifacts, absent semantic assessment",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Complete reader artifacts may exist, but absent semantic "
            "assessment cannot pass strict mode"
        ),
        build=_review_unavailable,
    ),
    ReplayCaseEntry(
        case_id="non-constraint-answer",
        version=REPLAY_CASE_VERSION,
        title="A factual question gets an answer, not a ranking",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Factual/explanatory answer has suitable structure without "
            "meaningless rankings"
        ),
        build=_non_constraint_answer,
    ),
    ReplayCaseEntry(
        case_id="empty-but-clean",
        version=REPLAY_CASE_VERSION,
        title="Tidy headings around an empty answer",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Zero findings, clean headings, and 'should' never constitute a "
            "high-quality answer"
        ),
        build=_empty_but_clean,
    ),
    ReplayCaseEntry(
        case_id="memory-is-not-read",
        version=REPLAY_CASE_VERSION,
        title="A remembered claim is a lead, never a read",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Two remembered generated claims with different source URLs "
            "cannot establish a read or independent support; legacy memory "
            "remains a lead"
        ),
        build=_memory_is_not_read,
    ),
    ReplayCaseEntry(
        case_id="validated-cache-reuse",
        version=REPLAY_CASE_VERSION,
        title="One download, two answers, validated provenance",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Validated original evidence is reused without repeated body "
            "downloads; stale/changed/forged cache provenance cannot silently "
            "pass"
        ),
        build=_validated_cache_reuse,
    ),
    ReplayCaseEntry(
        case_id="decision-context-late-candidate",
        version=REPLAY_CASE_VERSION,
        title="The last candidate and the late section still reach the request",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "A needed third search result and late document section reach "
            "actual subsequent decision/extraction requests despite short "
            "public summaries"
        ),
        build=_decision_context_late_candidate,
    ),
    ReplayCaseEntry(
        case_id="missing-target-triggers-one-extra-pass",
        version=REPLAY_CASE_VERSION,
        title="A missing required target reopens the plan once",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The obligation code computed as missing is what buys the extra "
            "pass, and the second discovery round is what answers it"
        ),
        build=_missing_target_triggers_one_extra_pass,
    ),
    ReplayCaseEntry(
        case_id="figure-not-on-page-dropped",
        version=REPLAY_CASE_VERSION,
        title="A figure the page does not state is refused",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The Context Check's rejection drops the figure with its reason, "
            "and the number never reaches the reader"
        ),
        build=_figure_not_on_page_dropped,
    ),
    ReplayCaseEntry(
        case_id="evidence-words-not-on-page-rejected",
        version=REPLAY_CASE_VERSION,
        title="Evidence words the page does not carry are refused",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "A reply quoting prose the page does not carry drops its figure "
            "with the recorded reason"
        ),
        build=_evidence_words_not_on_page_rejected,
    ),
    ReplayCaseEntry(
        case_id="scope-corrected-to-all-segments",
        version=REPLAY_CASE_VERSION,
        title="An all-segment figure written as grid-scale is corrected",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "The kept figure's scope is all segments, the finding is marked "
            "corrected, the overstated sentence is refused, and no reader "
            "sentence says grid-scale"
        ),
        build=_scope_corrected_to_all_segments,
    ),
    ReplayCaseEntry(
        case_id="revision-noted",
        version=REPLAY_CASE_VERSION,
        title="A revised edition is one fact row with an earlier edition",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Two editions of one figure answering one obligation are one row "
            "carrying the earlier edition"
        ),
        build=_revision_noted,
    ),
    ReplayCaseEntry(
        case_id="statement-check-failure-keeps-sentences",
        version=REPLAY_CASE_VERSION,
        title="A failed Statement Check keeps every sentence as drafted",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "The failure is recorded, the sentences publish as drafted, and "
            "the unjudged sentences keep the run from passing strict mode"
        ),
        build=_statement_check_failure_keeps_sentences,
    ),
)

REPLAY_CASE_IDS: tuple[str, ...] = tuple(
    entry.case_id for entry in REPLAY_CASE_MANIFEST
)


def replay_scenarios() -> tuple[ReplayScenario, ...]:
    """Every declared scenario, freshly built."""
    return tuple(entry.build() for entry in REPLAY_CASE_MANIFEST)


def manifest_entry(case_id: str) -> ReplayCaseEntry:
    for entry in REPLAY_CASE_MANIFEST:
        if entry.case_id == case_id:
            return entry
    raise KeyError(f"unknown replay case {case_id!r}")


def scenario_by_id(case_id: str) -> ReplayScenario:
    entry = manifest_entry(case_id)
    if entry.build is None:  # pragma: no cover - the manifest's own invariant
        raise KeyError(f"replay case {case_id!r} declares no scenario")
    return entry.build()


# The scripted-double half of the controlled inventory: the rows the
# graph-historical harness runs. Three of them are the cases the whole-report
# campaign ran before the real-agent matrix existed — scripted dependencies, a
# scripted six-agent double, and a recorded product result — and two declare
# counted obligations, which is the shape the product's coverage gate reads.
# They are declared *beside* the manifest rather than inside it because they
# have no scenario to replay, and because the first three cases' ids are the
# same strings as the first three matrix rows — inside ``REPLAY_CASE_MANIFEST``
# they would collide with real-agent rows, and the manifest's own contract is
# that it is exactly the plan's twenty-one. The ``-graph`` suffix names the
# harness that produced the result, so one inventory's evidence can never be
# read as the other's.
GRAPH_ONLY_HISTORICAL_MANIFEST: tuple[ReplayCaseEntry, ...] = (
    ReplayCaseEntry(
        case_id="broad-constraints-graph",
        version=REPLAY_CASE_VERSION,
        title="Six obligations, five-item batching, all answered",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "graph-only historical regression: scripted six-agent double"
        ),
        build=None,
        graph_only_historical=True,
    ),
    ReplayCaseEntry(
        case_id="comparative-conflict-graph",
        version=REPLAY_CASE_VERSION,
        title="Three groups measured on one basis, no invented winner",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "graph-only historical regression: scripted six-agent double"
        ),
        build=None,
        graph_only_historical=True,
    ),
    ReplayCaseEntry(
        case_id="extra-pass-recovers-missing-target-graph",
        version=REPLAY_CASE_VERSION,
        title="The missing account is acquired in the repair round",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "graph-only historical regression: scripted six-agent double"
        ),
        build=None,
        graph_only_historical=True,
    ),
    ReplayCaseEntry(
        case_id="claimed-coverage-open-obligation-graph",
        version=REPLAY_CASE_VERSION,
        title="A claimed topic whose obligation the evidence cannot answer",
        expected_product_result="partial / coverage_below_0.80",
        decisive_assertion=(
            "A checked claim consumes the topic while the plan's independent "
            "pair stands open: the campaign reports the substantive ratio "
            "(0.75, not the claimed 1.00) and records coverage_below_0.80"
        ),
        build=None,
        graph_only_historical=True,
    ),
    ReplayCaseEntry(
        case_id="declared-obligations-answered-graph",
        version=REPLAY_CASE_VERSION,
        title="Every declared obligation answered",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The control: four topics whose declared obligations are all "
            "answered by independently corroborated statements reach the "
            "passing coverage verdict, so the stricter reading is not "
            "'always fails'"
        ),
        build=None,
        graph_only_historical=True,
    ),
)


def controlled_suite_inventory() -> tuple[ReplayCaseEntry, ...]:
    """The declared controlled inventory: the twenty-one rows, then the five."""
    return (*REPLAY_CASE_MANIFEST, *GRAPH_ONLY_HISTORICAL_MANIFEST)


__all__ = [
    "GRAPH_ONLY_HISTORICAL_MANIFEST",
    "REPLAY_CASE_IDS",
    "REPLAY_CASE_MANIFEST",
    "REPLAY_CASE_MANIFEST_VERSION",
    "REPLAY_CASE_VERSION",
    "ReplayCaseEntry",
    "controlled_suite_inventory",
    "manifest_entry",
    "replay_scenarios",
    "scenario_by_id",
]