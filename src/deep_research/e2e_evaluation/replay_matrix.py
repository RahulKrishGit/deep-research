"""The versioned offline matrix: thirty-five real-agent scenarios.

The manifest below is the *declared inventory* the release proof is measured
against. Each row names a case id, the version of its semantics, the product
result the plan expects, the decisive assertion that makes it that result, and
the scenario builder that drives the real stack.

Every case runs the production agents through the real graph. The retired
scripted-double harness (a six-agent double replaying a graph that no longer
exists) is gone (PD-14): this matrix is the only controlled harness, and it
covers the new graph end to end.

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

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from deep_research.e2e_evaluation.replay import (
    CaseExpectation,
    ExtraEvidenceTarget,
    ReplayReviewDefect,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
)
from deep_research.memory.entries import MemoryEntry

REPLAY_CASE_MANIFEST_VERSION = 8
# Bumped to 8 for the whole-branch review's P0-1 row:
# ``extra-pass-redrafts-the-gaining-part`` guards the extra-pass writer
# against running in redraft mode off the previous pass's own review (a
# 35-row inventory must not be read as the 34-row one recorded under
# version 7).
#
# Bumped to 7 for RevFormatT6's fix round: the new row
# ``scoped-review-invalid-reply-falls-back`` exercises the T5 addendum's
# scoped-review fallback (a 34-row inventory must not be read as the 33-row
# one recorded under version 6).
#
# Bumped to 6 for format-build T6: the new row
# ``scoped-redraft-after-a-named-defect`` exercises the T5 addendum's scoped
# re-review path (a 33-row inventory must not be read as the 32-row one
# recorded under version 5).
#
# Bumped to 5 for Task 5.9 fix round 2's row,
# ``comparison-target-names-both-products``: a 32-row inventory must not be
# read as the 31-row one recorded under version 4.
#
# Was 4 for Task 5.9's ten rows: the other question shapes of Fable §8.8 --
# subjects that keep equal values apart, versions, subject spellings, prose
# with no figure, a count whose year decides it, a purchase year, a relative
# period the page's date resolves, an unattributed figure, a one-part plan and
# a maker's figure relayed by a news site. The version names the inventory a
# recorded result was produced under, so a suite run over 31 rows is never
# read as one over 21.
#
# Was 3 for the review's F4: two rows were renamed with the ``report-`` prefix
# (``report-relay-labelled-as-relay``,
# ``report-scope-corrected-to-all-segments``) because their old ids collided
# with per-agent cases of the same name in ``evaluation.cases``, where a case
# id keys a LangSmith dataset. A whole-report row and a per-agent case may
# cover one real-world scenario, but they are never one identity.

# The case-schema version each case's semantics are pinned at. Bumping a case
# means changing its declaration here and in its own builder together, so a
# recorded result names the semantics it was produced under. Bumped for the
# 4.9 re-review's N1 fix: ``extra-pass-recovers-missing-target`` now takes a
# real extra pass instead of a second discovery round inside the first pass.
REPLAY_CASE_VERSION = 2

HTML = "text/html; charset=utf-8"
PDF = "application/pdf"


# A claim states its figure in prose ("... was 40 percent in 2024"), and the
# figure the Context Check is shown has to be that reading: a page whose claim
# states no number is a page that carries no figure, which is the shape
# ``empty-but-clean`` exists to exercise. At most two unit words, so a claim
# like "40 percent of urban households" yields "percent" and not the clause
# that follows it.
_FIGURE_IN_CLAIM = re.compile(
    r"(?P<value>\d[\d.,]*)\s+(?P<unit>[A-Za-z]+(?:\s+[A-Za-z]+)?)"
    r"(?=\s+(?:of|in|for|per|across|each|a|an|the)\b|[,.;]|$)"
)


def _claimed_figure(claim: str) -> tuple[tuple[str, str, str | None, str | None], ...]:
    """The figure a page's own claim states, as the extraction records it."""
    match = _FIGURE_IN_CLAIM.search(claim)
    if match is None:
        return ()
    year = re.search(r"\b(?:19|20)\d{2}\b", claim)
    return (
        (
            match.group("value").rstrip(".,"),
            " ".join(match.group("unit").lower().split()),
            year.group(0) if year else None,
            "actual",
        ),
    )


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
    figures: tuple[tuple[str, str, str | None, str | None], ...] | None = None,
    figure_subjects: tuple[str | None, ...] = (),
    publication_date: tuple[str, str] | None = None,
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

    ``figure_subjects`` names what each figure is about, position for position
    (D11), and ``publication_date`` is the page's own stated date with the
    words it states it in -- the date a relative period ("this year") is
    resolved against, which has to be the page's text verbatim for the same
    reason an excerpt does.
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
        figures=_claimed_figure(claim) if figures is None else figures,
        figure_subjects=figure_subjects,
        publication_date=publication_date,
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
    measure: str,
    query: str,
    sources: tuple[ReplaySource, ...],
    *,
    labels: tuple[str, str] | None = None,
    follow_up_queries: tuple[str, ...] = (),
    **target_fields: object,
) -> ReplayTopic:
    """One planned sub-topic, with the labels its answer row will carry.

    ``measure`` is the obligation's own measured quantity, as the plan states
    it.

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
        query=query,
        sources=sources,
        answer_labels=labels or ("", ""),
        follow_up_queries=follow_up_queries,
        measure=measure,
        **target_fields,
    )


def _filler(index: int, title: str, subject: str, figure: str) -> ReplayTopic:
    """One ordinary answered topic, so a case's own subject can be the exception."""
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
    # The figure each topic's pages state, six ordinary measured facts, each
    # independently published by two bodies.
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
        max_extra_passes=1,
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
            required_report_phrases=("urban households", "rural households"),
            required_invariants=("both_accounts_cited",),
        ),
    )


def _extra_pass_recovers_missing_target() -> ReplayScenario:
    """The missing account is unreachable in the opening round; the extra pass finds it.

    Six rosters answer the opening round's search and all refuse the read,
    which spends the sub-topic's whole turn budget before the pair that
    states the figure -- reachable only by the follow-up search -- can be
    asked for. The opening round therefore ends with the obligation missing,
    code buys exactly one extra pass for it (D4, §6.5), and that pass issues
    the search the opening round could not: both independent accounts of the
    pair are read together, and it is that read which turns the obligation
    into an answer, never a second search folded into the first pass.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    rosters = tuple(
        _page(
            f"roster{position}.example.test",
            f"entry-{position}",
            f"Adoption roster {position}",
            "the adoption roster lists a title and a publication date and "
            "states no measured value",
            issuer=f"Acme Roster {position}",
            # A roster that refuses the read spends a turn and yields no
            # finding, which is what leaves the turn budget to be exhausted
            # without also filling the per-extraction finding cap.
            status=403,
        )
        for position in range(1, 7)
    )
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
                (
                    *rosters,
                    *_pair(
                        1,
                        "adoption-2024",
                        "Adoption survey",
                        claim,
                        discovered_a=2,
                        discovered_b=2,
                    ),
                ),
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
        max_extra_passes=1,
        # This case tests the D4 extra-pass mechanism itself, not whatever
        # the shipped tool_budget_overrides/max_iterations happen to be: it
        # needs the six refused rosters to exhaust the opening pass's whole
        # turn budget, so it pins the researcher's own budget and the
        # decision-turn cap rather than drifting with config.yaml's limits
        # lift (tool_budget_overrides.researcher 20 -> 40, max_iterations
        # 7 -> 15).
        agent_overrides={
            "tool_budget_overrides": {"researcher": 20},
            "max_iterations": 7,
        },
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            # The rosters' refusals are the case's own machinery: they are
            # what spends the opening round's turns, and a topic whose whole
            # opening round was refused produces no findings to extract.
            allowed_failure_classes=(
                "error:agent_tool_failed",
                "error:researcher_sub_topic_without_findings",
            ),
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
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
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
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
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
            required_invariants=("no_false_verification", "mirror_not_double_counted"),
        ),
    )


def _extra_pass_finds_nothing() -> ReplayScenario:
    """An extra pass that finds nothing publishes once, and says what is missing.

    One obligation is owed and the only page for it is one the Context Check
    refuses, so the target is still missing when the extra pass runs -- and the
    extra pass can acquire nothing new, because the page is already read and
    the topic has no second candidate. Under PD-23 the run publishes, lists the
    obligation under Not found, and finishes accepted: what it must not do is
    buy a second extra pass or claim the obligation was answered.
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
                        # The figure is refused, so the finding is dropped and
                        # the obligation it named stays owed: the shape that
                        # makes the extra pass necessary rather than optional.
                        context={"verdict": "reject"},
                    ),
                ),
                labels=("Acme widget", "adoption rate"),
                # The obligation asks for a measurement, so a dropped finding
                # cannot answer it by naming it (PD-7).
                unit_dimension="percent",
                period="2024",
                kind="actual",
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            # PD-23 again: the published report lists the unanswered
            # obligation, and that is an accepted result. The row's assertion
            # is the pass count and the Not found entry.
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            allowed_failure_classes=(
                "missing_required_target",
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
    the words credit. A run that resolved the figure to the site that
    published the article would record a fact row with no relayed
    attribution, which is the exact structural fact the
    ``relay_labelled_as_relay`` invariant forbids.

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
        case_id="report-relay-labelled-as-relay",
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
            # "Wire Service's own figure" was the code label the old renderer
            # printed on a mis-attributed row; the new renderer prints no such
            # label anywhere, so the structured check is what is left: the
            # already-required ``relay_labelled_as_relay`` invariant fails
            # outright when no fact row is recorded ``relayed``.
            forbidden_assertions=("independently corroborated",),
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
                "the Acme widget adoption rate",
                "Acme widget adoption rate United States current",
                (
                    _page(
                        "agency8.example.test",
                        "outlook-2030",
                        "Adoption outlook",
                        projection,
                        issuer="Acme Institute 8",
                        figures=(("55", "percent", "2030", "forecast"),),
                    ),
                ),
                # The obligation is an actual for 2024: without these the
                # target is qualitative (PD-7) and the 2030 projection would
                # answer it, which is exactly the substitution the row tests.
                unit_dimension="percent",
                period="2024",
                kind="actual",
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
                labels=("Acme widget", "adoption rate"),
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            # The forbidden string is the substitution itself, not the word
            # "today": the question names the present, so its own echo in the
            # title is the question being asked rather than an answer to it.
            forbidden_assertions=("currently 55",),
            # PD-23: the 2024 actual stays unanswered and is listed under Not
            # found, which is an accepted publication -- the substitution is
            # what the row forbids, not the missing answer.
            allowed_failure_classes=(
                "missing_required_target",
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
    be recognised that way, so what the run refuses is the drafted *sentence*:
    the invariant pins the reader-visible refusal (the drafted cause is
    refused by the Statement Check, and no causal marker reaches the report).
    Under PD-7, an outcome-only claim about the same topic still counts
    ``topic-02-target-01`` answered, because the target's dimension carries no
    unit for the Context Check to test the claim against; whether a mechanism
    obligation needs a substance test of its own is a plan-level question this
    row does not decide (escalated in the Task 4.9 review).
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
                labels=("Acme widget", "adoption rate"),
            ),
            _topic(
                2,
                "Adoption mechanism",
                "What mechanism increased Acme widget adoption in the United States in "
                "2024?",
                "the mechanism behind the change",
                "Acme widget adoption mechanism United States 2024",
                _pair(2, "mechanism-2024", "Adoption mechanism note", claim),
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                3, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
        ),
        invented_prose="the agency should subsidise Acme widget deployment",
        max_extra_passes=1,
        expectation=CaseExpectation(
            # The pages state an outcome and the question asks for a cause: the
            # row's assertion is that no reader sentence states one and that
            # the drafted recommendation is refused (see the invariant).
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(),
            forbidden_assertions=("should subsidise",),
            allowed_failure_classes=(
                "missing_required_target",
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
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
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
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
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
            f"Acme Registry {index}'s record lists a title and a publication date "
            "and no measured value"
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
                labels=("record", "publication date"),
                # The obligation asks for a measurement. With no unit
                # dimension (PD-7) a figureless finding would answer it, and
                # the row's premise -- a tidy report that answered nothing --
                # would be untestable.
                unit_dimension="percent",
                period="2024",
                kind="actual",
            ),
            _topic(
                2,
                "Widget funding",
                # A share question: the obligation asks for a measurement, so
                # a register that states no value cannot answer it by naming
                # the obligation the way a qualitative target (PD-7) lets a
                # verified finding do.
                "What share of the Acme widget programme's 2024 funding was "
                "public?",
                "share",
                "Acme widget funding round United States 2024 public share",
                (_bare(2, "Funding"),),
                labels=("record", "publication date"),
                unit_dimension="percent",
                period="2024",
                kind="actual",
            ),
            _topic(
                3,
                "Widget exports",
                "What share of Acme widget exports went to Europe in 2024?",
                "share",
                "Acme widget export share Europe 2024",
                (_bare(3, "Export"),),
                labels=("record", "publication date"),
                unit_dimension="percent",
                period="2024",
                kind="actual",
            ),
        ),
        max_extra_passes=1,
        # PD-23 needs a semantic judgement before a report is accepted, so this
        # row scripts the review a report of nothing is worth: 0.4 on every
        # dimension is below §6.3's 0.80 floor, and the run finishes partial.
        review_score=0.4,
        expectation=CaseExpectation(
            # The report still publishes and lists every obligation it could
            # not answer under Not found -- and the review refuses it, which is
            # the run's own verdict on a report that says nothing.
            terminal_quality="partial",
            exit_code=4,
            allowed_failure_classes=(
                "missing_required_target",
                "no_quality_snapshot",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
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
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            forbidden_assertions=(memory_claim,),
            allowed_failure_classes=(
                "missing_required_target",
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
                labels=("Acme widget", "adoption rate"),
            ),
            replace(funding, sources=(funding.sources[0], forged)),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-03-target-01"),
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
                    # Their titles carry padding prose (never read as a claim,
                    # since the read is judged on ``claim``/``issuer`` below,
                    # not on the title) so the candidate manifest -- title and
                    # url per candidate -- still overflows the public
                    # observation summary's clamp (200 -> 2000 chars in the
                    # limits lift) with the same four candidates and the same
                    # four reads the case always made: the case's own point is
                    # that the late candidate below still reaches the request
                    # despite that overflow.
                    _page(
                        "agency12.example.test",
                        "cover-note",
                        "Adoption cover note "
                        + "with a publication date and no measured value " * 20,
                        "the Acme widget adoption rate cover note lists a title "
                        "and a publication date and states no measured value",
                        issuer="Acme Institute 12",
                        verdict="insufficient_evidence",
                    ),
                    _page(
                        "agency12.example.test",
                        "method-note",
                        "Adoption method note "
                        + "describing the method and no measured value " * 20,
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
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            required_report_phrases=("40 percent",),
            required_invariants=(
                "late_candidate_reached_decision",
                "public_summary_stayed_short",
            ),
        ),
    )


def _missing_target_triggers_one_extra_pass() -> ReplayScenario:
    """The missing obligation buys one extra pass, and the pass answers it.

    The opening round's one search surfaces six registers, and every one of
    them refuses the read: the refusals consume the sub-topic's whole turn
    budget, so the second search is never issued and the page that states the
    figure -- which only that second search returns -- is never reached. The
    opening pass therefore ends with ``topic-01-target-01`` missing, code buys
    exactly one extra pass for it (D4, §6.5), and that pass issues the search
    the opening round could not: the read that follows is what turns the
    obligation into an answer.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    registers = tuple(
        _page(
            f"register{position}.example.test",
            f"record-{position}",
            f"Adoption register {position}",
            "the adoption register lists a title and a publication date and "
            "states no measured value",
            issuer=f"Acme Registry {position}",
            # A register that refuses the read spends a turn and yields no
            # finding, which is what leaves the turn budget to be exhausted
            # without also filling the per-extraction finding cap.
            status=403,
        )
        for position in range(1, 7)
    )
    return ReplayScenario(
        case_id="missing-target-triggers-one-extra-pass",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        max_extra_passes=1,
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                (
                    *registers,
                    _page(
                        "agency17.example.test",
                        "adoption-2024",
                        "Adoption survey",
                        claim,
                        issuer="Acme Institute 17",
                        discovered=2,
                    ),
                ),
                labels=("Acme widget", "adoption rate"),
                # The obligation asks for a measurement, so no register could
                # answer it even if it had been read: only the page the second
                # search returns states a figure.
                unit_dimension="percent",
                period="2024",
                kind="actual",
                follow_up_queries=(
                    "Acme widget adoption rate United States 2024 survey",
                ),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        # This case tests the D4 extra-pass mechanism itself, not whatever
        # the shipped tool_budget_overrides/max_iterations happen to be: it
        # needs the six refused registers to exhaust the opening pass's whole
        # turn budget, so it pins the researcher's own budget and the
        # decision-turn cap rather than drifting with config.yaml's limits
        # lift (tool_budget_overrides.researcher 20 -> 40, max_iterations
        # 7 -> 15).
        agent_overrides={
            "tool_budget_overrides": {"researcher": 20},
            "max_iterations": 7,
        },
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            # The registers' refusals are the case's own machinery: they are
            # what spends the opening round's turns, and a topic whose whole
            # opening round was refused produces no findings to extract.
            allowed_failure_classes=(
                "error:agent_tool_failed",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("missing_target_triggers_one_extra_pass",),
        ),
    )

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
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            forbidden_assertions=("88 percent",),
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
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            forbidden_assertions=("91 percent",),
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
        case_id="report-scope-corrected-to-all-segments",
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
                labels=("energy storage market", "18.9 gigawatts"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            # The obligation is answered, by the finding the Context Check
            # corrected (its scope is all segments), while the sentence that
            # overstated the scope is refused: the reader gets the figure and
            # never the wrong basis.
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            forbidden_assertions=("grid-scale",),
            allowed_failure_classes=(
                "missing_required_target",
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
    revised = "the Acme widget adoption rate in the United States was 42 percent in 2024"
    return ReplayScenario(
        case_id="revision-noted",
        version=REPLAY_CASE_VERSION,
        question="What was the Acme widget adoption rate in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "the Acme widget adoption rate",
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
                        revised,
                        issuer="Acme Institute 16",
                        vintage="February 2025 edition",
                    ),
                ),
                # The obligation states the measure, so each
                # edition's row is bound to it: PD-9's revision is
                # two rows for one obligation, and a row the target
                # never bound could not be one of them.
                unit_dimension="percent",
                period="2024",
                kind="actual",
                labels=("Acme widget", "adoption rate"),
            ),
            _filler(
                2, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
            _filler(
                3, "Widget exports", "Acme widget export volume", "3.4 million units"
            ),
        ),
        max_extra_passes=1,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
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
        max_extra_passes=1,
        expectation=CaseExpectation(
            # §5.4 and PD-10: a recorded batch failure keeps the sentences
            # legitimately, so the run publishes and finishes accepted -- the
            # error is the record, not a gate. The verdict map is what says
            # nothing was judged.
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-03-target-01",
            ),
            allowed_failure_classes=(
                "error:evidence_verifier_statement_check_failed",
                # The parallel writer (spec §6.6): the bottom line is fed
                # only checked, kept section statements, so when every
                # statement everywhere came back unchecked (this scenario's
                # whole premise) there is nothing to write it from. Fixed at
                # HEAD: this is a recoverable
                # ``report_writer_bottom_line_unchecked``, not the false
                # "every part failed" ``report_writer_provider_error`` -- the
                # sections all drafted and printed fine, only the bottom line
                # is empty, and the renderer says so in plain prose.
                "error:report_writer_bottom_line_unchecked",
            ),
            forbidden_assertions=(
                "No source we could check answers this question.",
            ),
            required_report_phrases=(
                "40 percent",
                "12 million dollars",
                "A summary could not be written this time",
            ),
            required_invariants=("statement_failure_keeps_sentences",),
        ),
    )


# --- Task 5.9: the other question shapes (D11; Fable §8.8) -------------------
#
# Ten rows for the shapes the rest of the matrix does not reach: two subjects
# keeping one value apart, two versions of one thing, three spellings of one
# subject, a question whose pages state no figure at all, a counted measure
# whose period decides which figure answers it, a purchase year whose
# obligation states no period, a relative period the page's own date resolves,
# prose that credits a body the page does not, a plan with one part, and a
# maker's own figure beside a news relay of another of them.
#
# Every fixture here is hypothetical (*.example.test, "Example …", "Kettle
# K1"): no row may be fitted to a question the live run might draw (D12).
#
# Task 5.9 fix round 2 (Review 5.9, Important 2) added
# ``comparison-target-names-both-products`` beside the two-subjects row: the
# same two products, asked about in the comparison wording that names both of
# them, which is the shape the product could not tell apart before Task 5.6c.


def _two_subjects_one_value() -> ReplayScenario:
    """Two products, one tester, one rating: only the subject tells them apart.

    D11: a rating is a number in a unit the parser does not scale and the
    target states no period, so two figures equal in value, organisation,
    period and kind are one fact unless their subjects differ. The two pages
    differ in nothing else -- Kettle K1 and Kettle K2, both rated 4.5 out of 5
    for 2026 by the same tester -- so a run that dropped the subject would
    print one row for two products, or refuse the second sentence as a
    restatement of the first.
    """

    def rated(model: str) -> ReplaySource:
        return _page(
            "tester.example.test",
            f"kettle-{model.lower()}",
            f"Kettle {model} noise test",
            f"the Example Tester rated the Kettle {model} noise at 4.5 out of 5 "
            "for 2026",
            issuer="Example Tester",
            figures=(("4.5", "out of 5", "2026", "actual"),),
            figure_subjects=(f"Kettle {model}",),
        )

    return ReplayScenario(
        case_id="two-subjects-one-value",
        version=REPLAY_CASE_VERSION,
        question=(
            "What noise rating did the Example Tester give the Kettle models "
            "for 2026?"
        ),
        topics=(
            _topic(
                1,
                "Noise ratings",
                "What noise rating did the Example Tester give the Kettle "
                "models for 2026?",
                "noise rating",
                "Example Tester Kettle noise rating 2026",
                (rated("K1"), rated("K2")),
                unit_dimension="rating",
                labels=("Example Tester", "noise"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
            # The Option column exists because these rows need it, and both
            # options are printed in it: the phrase is the table cell, which
            # no other part of the report writes, so a row that lost one fails
            # here rather than passing on the summary sentence alone.
            required_report_phrases=("| Option |", "| Kettle K1 |", "| Kettle K2 |"),
            required_invariants=("subjects_stay_apart",),
        ),
    )


def _comparison_target_names_both_products() -> ReplayScenario:
    """A target that names both products keeps them two rows (Task 5.6c).

    The same two products the ``two-subjects-one-value`` row rates, asked
    about in the comparison wording a live question draws ("How do the Kettle
    K1 and the Kettle K2 compare ..."). A target that states both subjects'
    every word is what Fable §8.6 strips those words against, and before Task
    5.6c that stripping erased the one thing telling the two figures apart: the
    run folded K2's page into K1's row as corroboration, the table lost the K2
    line, and the writer refused the K2 sentence as a restatement of K1's row
    (``restates K001``). The two figures are equal in value, organisation,
    period and kind, so their subjects are the whole of what tells them apart,
    and this row asserts the reader's side of it: two options table rows with
    both option names, both section sentences, and each sentence carrying its
    own row's mark.

    The value is a percent rather than the neighbouring row's "4.5 out of 5"
    because the labels and the restatement guard both reach only quantities the
    parser scales: a rating prints no label at all, so a sentence's label could
    not be asserted. The K2 rating reached the reader through a news relay of
    the tester's page, which is what gives the second row a label of its own --
    two own pages by one tester render one identical label for both rows, and a
    sentence carrying "only its own row's label" would then be unreadable.
    """

    def rated(model: str) -> ReplaySource:
        return _page(
            "tester.example.test",
            f"kettle-{model.lower()}",
            f"Kettle {model} noise test",
            f"the Example Tester rated the Kettle {model} noise at 40 percent "
            "for 2026",
            issuer="Example Tester",
            figures=(("40", "percent", "2026", "actual"),),
            figure_subjects=(f"Kettle {model}",),
        )

    def relayed(model: str) -> ReplaySource:
        return _page(
            "news.example.test",
            f"kettle-{model.lower()}-relay",
            f"Kettle {model} noise test (news relay)",
            "According to the Example Tester the Kettle "
            f"{model} noise rating is 40 percent for 2026",
            issuer="Example News",
            figures=(("40", "percent", "2026", "actual"),),
            figure_subjects=(f"Kettle {model}",),
            context={"attribution": "relayed", "organisation": "Example Tester"},
        )

    question = (
        "How do the Kettle K1 and the Kettle K2 compare on the Example "
        "Tester noise rating for 2026?"
    )
    return ReplayScenario(
        case_id="comparison-target-names-both-products",
        version=REPLAY_CASE_VERSION,
        question=question,
        topics=(
            _topic(
                1,
                "Noise ratings",
                question,
                "noise rating",
                "Example Tester Kettle noise rating 2026",
                (rated("K1"), relayed("K2")),
                unit_dimension="percent",
                labels=("Example Tester", "noise"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
            # Both rows, and each row's own option cell on the part column:
            # the sentences are written subject-first, so the K1 sentence
            # carries K1's mark and the K2 sentence K2's, and the two cells
            # differ (the tester's own figure against the news relay of it),
            # so a run that folded the two figures -- or that let a mark carry
            # the rival row's option -- cannot state both.
            required_report_phrases=(
                "| Option |",
                "| Kettle K1 |",
                "| Kettle K2 |",
                # No trailing stop: the marker moves before the sentence's own
                # final stop (spec §3.1 rule 3), so the printed text reads
                # "...for 2026 [1]." rather than "...for 2026. [1]".
                "Kettle K1: Example Tester reports 40 percent for 2026",
                "Kettle K2: Example Tester reports 40 percent for 2026",
                # The options-table cell credits the page's own resolved
                # identity (page_owner, spec §8): neither host here confirms
                # its fixture's "Example ..." issuer name against itself, so
                # both cells fall back to the host, verified against a real
                # replay of this case. FormatT2Table's P1-1 fix (6de7273): a
                # relaying page's cell also names the body it credits
                # ("{verdict}, according to {body} — {publisher}"), so the
                # relayed K2 row reads that way and only the K1 row (whose
                # page speaks for itself) keeps the plain shape.
                "40 percent — tester.example.test",
                "40 percent, according to Example Tester — news.example.test",
            ),
            required_invariants=("subjects_stay_apart",),
        ),
    )


def _two_versions_one_target() -> ReplayScenario:
    """Two editions of one maker's notes answer one obligation, and stay two rows.

    PD-9 folds two rows that answer one obligation, carry different releases
    and describe the same thing in the same period. These two describe
    *different* things -- version 10.02 and version 10.03, a month apart -- and
    state the same value, so a run without the subject (D11) would fold them
    into one row carrying an "earlier edition" the reader never earned. The
    ``revision-noted`` row is the other half: the same measure with one subject
    still folds.
    """

    def notes(version: str, edition: str) -> ReplaySource:
        return _page(
            "games.example.test",
            f"notes-{version.replace('.', '-')}",
            f"Kettle notes {version}",
            "the Example Games patch notes list 12 changes for 2026",
            issuer="Example Games",
            vintage=edition,
            figures=(("12", "changes", "2026", "actual"),),
            figure_subjects=(f"version {version}",),
        )

    return ReplayScenario(
        case_id="two-versions-one-target",
        version=REPLAY_CASE_VERSION,
        question="What changes do the Example Games patch notes list for 2026?",
        topics=(
            _topic(
                1,
                "Patch notes",
                "What changes do the Example Games patch notes list for 2026?",
                "the changes the notes list",
                "Example Games patch notes changes 2026",
                (
                    notes("10.02", "January 2026 edition"),
                    notes("10.03", "February 2026 edition"),
                ),
                unit_dimension="count",
                period="2026",
                kind="actual",
                labels=("Example Games", "12 changes"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
            required_report_phrases=("version 10.02", "version 10.03"),
            required_invariants=("versions_stay_apart",),
        ),
    )


def _single_subject_spellings() -> ReplayScenario:
    """Three spellings of one subject are one fact, not three rows.

    Fable §8.6 step 3: a subject that only restates what its own target already
    says names nothing, so "United States", "widget adoption" and no subject at
    all are the same subject here -- the target asks for widget adoption in the
    United States -- and three pages stating the same figure are one row. The
    pages are three publishers' copies of one measurement, which is why the row
    also exercises the corroboration path rather than only the fold.
    """
    claim = (
        "the Example Institute measured widget adoption in the United States at "
        "40 percent in 2025"
    )

    def survey(
        host: str, slug: str, title: str, subject: str | None
    ) -> ReplaySource:
        return _page(
            host,
            slug,
            title,
            claim,
            issuer="Example Institute",
            figures=(("40", "percent", "2025", "actual"),),
            figure_subjects=() if subject is None else (subject,),
        )

    return ReplayScenario(
        case_id="single-subject-spellings",
        version=REPLAY_CASE_VERSION,
        question="What was widget adoption in the United States in 2025?",
        topics=(
            _topic(
                1,
                "Widget adoption",
                "What was widget adoption in the United States in 2025?",
                "widget adoption",
                "widget adoption United States 2025",
                (
                    survey(
                        "agency21.example.test",
                        "adoption-2025",
                        "Adoption survey",
                        "United States",
                    ),
                    survey(
                        "bureau21.example.test",
                        "adoption-2025-panel",
                        "Adoption survey (independent panel)",
                        "widget adoption",
                    ),
                    survey(
                        "panel21.example.test",
                        "adoption-2025-note",
                        "Adoption note",
                        None,
                    ),
                ),
                unit_dimension="percent",
                period="2025",
                kind="actual",
                geography="United States",
                labels=("Example Institute", "widget adoption"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
            required_invariants=("one_fact_row",),
        ),
    )


def _prose_only_question() -> ReplayScenario:
    """A why question whose answers state no figure at all.

    A qualitative obligation (empty unit dimension and kind) is answered by a
    finding that names it, and three reasons are three such findings. Nothing
    in the run produces a figure, so the reader's report prints no table at
    all rather than an empty one (spec §4.1 rule 3) -- and the Statement
    Check is shown the figureless citation lines (``snippet:`` and the body
    it is ``attributed to:``), which the double's own format check holds it
    to.
    """
    question = "Why did Acme widget adoption rise in the United States in 2024?"

    def reason(host: str, slug: str, title: str, claim: str) -> ReplaySource:
        return _page(host, slug, title, claim, issuer="Example Institute")

    return ReplayScenario(
        case_id="prose-only-question",
        version=REPLAY_CASE_VERSION,
        question=question,
        topics=(
            _topic(
                1,
                "Retail rollout",
                "Why did Acme widget adoption rise in the United States in 2024?",
                "the reason adoption rose",
                "Acme widget adoption rise reason 2024",
                (
                    reason(
                        "retail.example.test",
                        "adoption-rise",
                        "Adoption note",
                        "the Example Institute attributes the rise in Acme widget "
                        "adoption to a wider retail rollout",
                    ),
                ),
                labels=("Example Institute", "adoption"),
            ),
            _topic(
                2,
                "Price",
                "Did a lower price contribute to the rise in Acme widget adoption?",
                "the price change behind the rise",
                "Acme widget adoption price 2024",
                (
                    reason(
                        "price.example.test",
                        "adoption-price",
                        "Price note",
                        "the Example Bureau attributes the rise in Acme widget "
                        "adoption to a lower unit price",
                    ),
                ),
                labels=("Example Bureau", "adoption"),
            ),
            _topic(
                3,
                "Availability",
                "Did availability contribute to the rise in Acme widget adoption?",
                "the availability change behind the rise",
                "Acme widget adoption availability 2024",
                (
                    reason(
                        "stock.example.test",
                        "adoption-stock",
                        "Availability note",
                        "the Example Panel attributes the rise in Acme widget "
                        "adoption to wider availability",
                    ),
                ),
                labels=("Example Panel", "adoption"),
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
            # "No figure passed the Evidence Verifier." was the placeholder
            # sentence the old table printed in its own absence; the new
            # table is simply omitted (spec §3.1 rule 4), so the structured
            # check is that ``composition.table`` is ``None``.
            required_invariants=("no_table_printed",),
        ),
    )


def _count_unit_period() -> ReplayScenario:
    """The year a count belongs to is what decides that it answers the obligation.

    The page states two counts of one measure -- 12,345 in 2025 and 10,000 in
    2024 -- and the obligation asks for 2025, so only the 2025 figure may answer
    it. The plan also declares a 2026 forecast obligation, and no page states a
    2026 projection: that obligation is required, it is what the review finds
    missing, so code buys the one extra pass for it (D4, §6.5) and the pass
    finds nothing. The reader is told rather than left with silence: the
    obligation is listed under Not found, which is what a scored report with a
    remaining obligation has to do (§6.4), and the run finishes accepted with
    ``missing_required_target`` recorded.

    ``extra_pass_finds_nothing`` is the invariant, not
    ``missing_target_triggers_one_extra_pass``: the pass is bought for the
    forecast obligation, which no page can answer, so a checker that required
    the pass to turn its obligation into an answer could never hold here.
    """
    return ReplayScenario(
        case_id="count-unit-period",
        version=REPLAY_CASE_VERSION,
        question=(
            "How many Kettle units shipped in 2025 and how many are expected "
            "in 2026?"
        ),
        max_extra_passes=1,
        topics=(
            _topic(
                1,
                "Shipments",
                "How many Kettle units shipped in 2025?",
                "the number of Kettle units shipped",
                "Kettle units shipped 2025 count",
                (
                    _page(
                        "counts.example.test",
                        "shipments",
                        "Kettle shipment counts",
                        "the number of Kettle units shipped was 12,345 units in "
                        "2025 and 10,000 units in 2024",
                        issuer="Example Tester",
                        figures=(
                            ("12,345", "units", "2025", "actual"),
                            ("10,000", "units", "2024", "actual"),
                        ),
                    ),
                ),
                unit_dimension="count",
                period="2025",
                kind="actual",
                labels=("Kettle units", "12,345"),
            ),
            _topic(
                2,
                "2026 forecast",
                "How many Kettle units are forecast to ship in 2026?",
                "the number of Kettle units forecast to ship",
                "Kettle units shipped 2026 forecast",
                (
                    _page(
                        "outlook.example.test",
                        "outlook",
                        "Kettle outlook",
                        "the Example Institute outlook discusses Kettle shipments "
                        "and states no projection figure",
                        issuer="Example Institute",
                    ),
                ),
                unit_dimension="count",
                period="2026",
                kind="forecast",
            ),
            _filler(
                3, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-03-target-01"),
            # The period is what keeps the two counts apart: a row that bound
            # the 2024 count to the 2025 obligation would be exactly the
            # merged-obligation defect ``count_period_binds_obligation``
            # checks for structurally (the phrase this used to forbid,
            # "The number of Kettle units shipped, 2024", can never print:
            # the cell uses ``row.subject``, which this fixture leaves
            # ``None``).
            allowed_failure_classes=("missing_required_target",),
            required_invariants=(
                "extra_pass_finds_nothing",
                "count_period_binds_obligation",
            ),
        ),
    )


def _purchase_year_empty_period() -> ReplayScenario:
    """The year a reader is buying in is not a period the evidence must fall in.

    Fable §8.9 item 6: a buy, use or decide year leaves the obligation's period
    empty, and a rating the tester published in 2025 still answers it -- there
    is no period for the figure to fail to match. The page carries its own
    stated date, which is what the reader's label dates it from.
    """
    claim = "the Example Tester rated the Kettle K1 noise at 4.5 out of 5 in 2025"
    return ReplayScenario(
        case_id="purchase-year-empty-period",
        version=REPLAY_CASE_VERSION,
        question="Which Kettle should I buy in 2026?",
        topics=(
            _topic(
                1,
                "Noise rating",
                "What noise rating does the Kettle K1 carry?",
                "noise rating",
                "Kettle K1 noise rating test",
                (
                    _page(
                        "purchase.example.test",
                        "kettle-k1-noise",
                        "Kettle K1 noise test",
                        claim,
                        issuer="Example Tester",
                        text=(
                            "Kettle K1 noise test. Published by Example Tester on "
                            f"2025-11-01. The report states {claim}."
                        ),
                        figures=(("4.5", "out of 5", "2025", "actual"),),
                        publication_date=("2025-11-01", "2025-11-01"),
                    ),
                ),
                unit_dimension="rating",
                labels=("Example Tester", "noise"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
        ),
    )


def _relative_period_resolved() -> ReplayScenario:
    """A relative period gets its year from the page's own date, or from nowhere.

    D11 (Fable §8.7): "this year" states no year by itself, so the Context
    Check's proposal of 2026 is kept only from a date the page really carries
    (2026-02-20 here), and the kept figure records the date it came from, which
    the reader's label names. The second topic is the same words on a page that
    states no date at all: the proposal is refused (``correction_not_on_page``)
    and the figure is dropped rather than published with a period no page
    stated.
    """
    claim = (
        "the Example Institute reports that 4 GW of Kettle capacity was added "
        "this year"
    )
    return ReplayScenario(
        case_id="relative-period-resolved",
        version=REPLAY_CASE_VERSION,
        question="How much Kettle capacity was added this year?",
        topics=(
            _topic(
                1,
                "Capacity added",
                "How much Kettle capacity was added this year?",
                "the capacity added",
                "Kettle capacity added this year",
                (
                    _page(
                        "dated.example.test",
                        "capacity",
                        "Kettle capacity note",
                        claim,
                        issuer="Example Institute",
                        text=(
                            "Kettle capacity note. Published by Example Institute. "
                            "Published 2026-02-20. The report states " + claim + "."
                        ),
                        figures=(("4", "GW", None, "actual"),),
                        context={"period": "2026"},
                        publication_date=("2026-02-20", "Published 2026-02-20"),
                    ),
                ),
                unit_dimension="power",
                period="2026",
                kind="actual",
                labels=("Example Institute", "4 GW"),
            ),
            _topic(
                2,
                "Undated note",
                "How much Kettle capacity does the undated note report?",
                "the capacity the undated note reports",
                "Kettle capacity undated note",
                (
                    _page(
                        "undated.example.test",
                        "capacity-note",
                        "Kettle capacity note (undated)",
                        claim,
                        issuer="Example Institute",
                        figures=(("4", "GW", None, "actual"),),
                        context={"period": "2026"},
                    ),
                ),
                unit_dimension="power",
                period="2026",
                kind="actual",
                required=False,
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
            # The old label text ("period resolved from the page date …") was
            # the code label the report cut (spec §3.1 rule 9); with a single
            # eligible row no table forms to carry it either, so the
            # structural ``period_resolved_from_page_date`` invariant below is
            # what is left to pin the date the period was resolved from.
            required_invariants=("period_resolved_from_page_date",),
        ),
    )


def _unattributed_relay_prose() -> ReplayScenario:
    """A figure the page does not attribute is the site's, never a name it drops.

    PD-8 and honesty rule 1: an unattributed figure is published as the site
    that carries it, and prose that credits a body the page does not credit is
    refused. The scripted writer drafts the page's own sentence, which credits
    Example Institute; the Statement Check corrects it to the site's own words,
    and the corrected sentence is what reaches the reader. "According to
    Example Institute" is forbidden, because no page attributes the figure to
    it.
    """
    prose = (
        "According to Example Institute the Kettle K1 noise rating beats every "
        "rival tested this year"
    )
    corrected = (
        "The Example News test found the Kettle K1 noise rating beats every "
        "rival tested this year."
    )
    return ReplayScenario(
        case_id="unattributed-relay-prose",
        version=REPLAY_CASE_VERSION,
        question="How good is the Kettle K1 noise rating?",
        topics=(
            _topic(
                1,
                "Noise rating",
                "How good is the Kettle K1 noise rating?",
                "noise rating",
                "Kettle K1 noise rating tested",
                (
                    _page(
                        "news.example.test",
                        "kettle-noise",
                        "Kettle noise write-up",
                        prose,
                        issuer="Example News",
                        figures=(),
                        statement={"verdict": "corrected", "text": corrected},
                    ),
                    _page(
                        "desk.example.test",
                        "kettle-noise-test",
                        "Kettle noise test results",
                        "the Kettle K1 noise rating measured at 4.5 out of 5 in "
                        "the Example News test",
                        issuer="Example News",
                        figures=(("4.5", "out of 5", "2026", "actual"),),
                        context={"attribution": "unattributed"},
                    ),
                ),
                unit_dimension="rating",
                period="2026",
                kind="actual",
                labels=("Example News", "noise rating"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
            forbidden_assertions=("according to Example Institute",),
            # The credit the reader has to see on the unattributed figure's
            # own row: the site that carries it, credited by name (the old
            # "(source does not attribute it)" suffix was the code label the
            # report cut, spec §3.1 rule 9; with a single eligible row no
            # table forms to carry a Who cell either, so the publisher's own
            # name is what is left to pin, through the corrected sentence
            # that names it). No trailing stop on the sentence itself: the
            # marker moves before the final stop (spec §3.1 rule 3).
            required_report_phrases=(
                corrected.removesuffix("."),
                "Example News",
            ),
        ),
    )


def _one_part_question() -> ReplayScenario:
    """A plan of one topic is not a structural defect, and the run completes.

    Task 5.2 set ``MIN_SUB_TOPICS = 1``: a question with one part gets one
    sub-topic, and the planner's own structural gate must not read that as a
    defect to repair.
    """
    claim = (
        "the Acme widget adoption rate in the United States was 40 percent in 2024"
    )
    return ReplayScenario(
        case_id="one-part-question",
        version=REPLAY_CASE_VERSION,
        question=(
            "What was the Acme widget adoption rate in the United States in 2024?"
        ),
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "the Acme widget adoption rate",
                "Acme widget adoption rate United States 2024",
                _pair(1, "adoption-2024", "Adoption survey", claim),
                unit_dimension="percent",
                period="2024",
                kind="actual",
                labels=("Acme widget", "adoption rate"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01",),
        ),
    )


def _maker_notes_vs_relay() -> ReplayScenario:
    """The maker's own figure and a news relay of another one of them.

    Honesty rule 1 (D7): a relay is never presented as the issuer, and the
    maker's own page is. Both rows carry the credit the reader needs -- the
    findings table's Who cell reads "Example Games" for the maker's own row
    and "Example Games, reported by news.example.test" for the relayed row
    (neither page's fixture issuer name confirms against its own host, so
    the relay's own credit falls back to it, verified against a real replay
    of this case) -- so a run that credited the relay site with the maker's
    figure, or the maker with the relay's summary, fails the phrases this
    row asserts.
    """
    return ReplayScenario(
        case_id="maker-notes-vs-relay",
        version=REPLAY_CASE_VERSION,
        question="What did the Example Games update change in 2026?",
        topics=(
            _topic(
                1,
                "Patch notes",
                "What did the Example Games patch notes report for 2026?",
                "the reduction the notes report",
                "Example Games patch notes load times 2026",
                (
                    _page(
                        "games.example.test",
                        "notes-2026",
                        "Patch notes 2026",
                        "the Example Games patch notes report a 42 percent cut "
                        "in load times for 2026",
                        issuer="Example Games",
                        figures=(("42", "percent", "2026", "actual"),),
                        context={"organisation": "Example Games"},
                    ),
                ),
                unit_dimension="percent",
                period="2026",
                kind="actual",
                labels=("Example Games", "42 percent"),
            ),
            _topic(
                2,
                "News relay",
                "What does the news report say the update cuts for 2026?",
                "the reduction the news report relays",
                "Example Games update matchmaking time 2026",
                (
                    _page(
                        "news.example.test",
                        "update-2026",
                        "Example Games update",
                        "According to Example Games the update cuts matchmaking "
                        "time by 30 percent for 2026",
                        issuer="Example News",
                        figures=(("30", "percent", "2026", "actual"),),
                        context={
                            "attribution": "relayed",
                            "organisation": "Example Games",
                        },
                    ),
                ),
                unit_dimension="percent",
                period="2026",
                kind="actual",
                labels=("Example Games", "30 percent"),
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-02-target-01"),
            # "Example Games" alone is also the report's own title text, so a
            # phrase match on the name cannot tell the maker's own row from
            # the relay's mention of it; ``maker_row_is_own`` reads the typed
            # fact row instead.
            required_report_phrases=(
                "Example Games, reported by news.example.test",
            ),
            required_invariants=(
                "relay_labelled_as_relay",
                "no_false_verification",
                "maker_row_is_own",
            ),
        ),
    )

def _scoped_redraft_after_a_named_defect() -> ReplayScenario:
    """A material defect on one part buys a redraft of only that part, and
    the second review is scoped, never a second full one (T5 addendum).

    Two required parts, each independently supported. The scripted first
    full review accepts every statement but also raises one material defect
    naming the export-volume part's own target -- the redraft that buys is
    routed to that part alone (spec §6.9), so the adoption-rate part carries
    over byte-identical and its statement is exactly the unchanged one that
    lets the second review be scoped rather than a second full one.
    """
    adoption_claim = (
        "the Acme widget adoption rate in the United States was 40 percent in 2024"
    )
    export_claim = (
        "the Acme widget export volume in the United States was 3.4 million "
        "units in 2024"
    )
    return ReplayScenario(
        case_id="scoped-redraft-after-a-named-defect",
        version=REPLAY_CASE_VERSION,
        question=(
            "What were the Acme widget adoption rate and export volume in "
            "the United States in 2024?"
        ),
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                _pair(1, "adoption-2024", "Adoption survey", adoption_claim),
                labels=("Acme widget", "adoption rate"),
            ),
            _topic(
                2,
                "Export volume",
                "What was the Acme widget export volume in the United States in 2024?",
                "value",
                "Acme widget export volume United States 2024",
                _pair(2, "export-2024", "Export survey", export_claim),
                labels=("Acme widget", "export volume"),
            ),
        ),
        review_defect=ReplayReviewDefect(
            target_ids=("topic-02-target-01",),
            kind="presentation",
            severity="major",
            problem=(
                "The export-volume section restates the figure without "
                "naming its own subject plainly; redraft it."
            ),
        ),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-02-target-01"),
            required_report_phrases=("40 percent", "3.4 million units"),
            required_invariants=("scoped_review_used",),
        ),
    )


def _scoped_review_invalid_reply_falls_back() -> ReplayScenario:
    """An invalid scoped reply falls back to exactly one full review (T5 addendum).

    Same premise as ``scoped-redraft-after-a-named-defect`` -- a defect on
    one part buys a redraft of that part alone, leaving the other part
    carried over byte-identical -- but the scoped attempt the graph makes
    after the redraft cannot be used (the provider raises), so the run must
    fall back to one fresh full review rather than accept a scoped-derived
    judgement or publish an unjudged report.
    """
    adoption_claim = (
        "the Acme widget adoption rate in the United States was 40 percent in 2024"
    )
    export_claim = (
        "the Acme widget export volume in the United States was 3.4 million "
        "units in 2024"
    )
    return ReplayScenario(
        case_id="scoped-review-invalid-reply-falls-back",
        version=REPLAY_CASE_VERSION,
        question=(
            "What were the Acme widget adoption rate and export volume in "
            "the United States in 2024?"
        ),
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                _pair(1, "adoption-2024b", "Adoption survey", adoption_claim),
                labels=("Acme widget", "adoption rate"),
            ),
            _topic(
                2,
                "Export volume",
                "What was the Acme widget export volume in the United States in 2024?",
                "value",
                "Acme widget export volume United States 2024",
                _pair(2, "export-2024b", "Export survey", export_claim),
                labels=("Acme widget", "export volume"),
            ),
        ),
        review_defect=ReplayReviewDefect(
            target_ids=("topic-02-target-01",),
            kind="presentation",
            severity="major",
            problem=(
                "The export-volume section restates the figure without "
                "naming its own subject plainly; redraft it."
            ),
        ),
        scoped_review_failure=True,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-02-target-01"),
            required_report_phrases=("40 percent", "3.4 million units"),
            required_invariants=("scoped_review_fallback_used",),
        ),
    )

def _extra_pass_redrafts_the_gaining_part() -> ReplayScenario:
    """A part that gains a new finding on the extra pass is redrafted, not
    carried over (P0-1: the extra-pass writer must not run in redraft mode
    off the previous pass's own review).

    Two required parts. Part 1 already carries a material review defect on
    pass 0. Part 2 has two targets: one answered on the opening pass (so
    the part already has a section) and one still missing -- six refused
    registers exhaust the opening round's turn budget before the page that
    answers it, reachable only by the follow-up query, can be read. Code
    buys the one extra pass the missing target justifies (D4, §6.5); the
    bug this guards is a writer that treats that extra-pass hop as a redraft
    of pass 0's *review* (routing only Part 1, the defect's own part, and
    leaving Part 2 -- which just gained a brand new finding -- silently
    carried over from a composition that could not have cited it).
    """
    adoption_claim = (
        "the Acme widget adoption rate in the United States was 40 percent in 2024"
    )
    export_claim = (
        "the Acme widget export volume in the United States was 3.4 million "
        "units in 2024"
    )
    capacity_claim = (
        "the Acme widget production capacity in the United States was 8 "
        "million units in 2025"
    )
    registers = tuple(
        _page(
            f"capregister{position}.example.test",
            f"record-{position}",
            f"Capacity register {position}",
            "the capacity register lists a title and a publication date and "
            "states no measured value",
            issuer=f"Acme Registry {position}",
            status=403,
        )
        for position in range(1, 7)
    )
    return ReplayScenario(
        case_id="extra-pass-redrafts-the-gaining-part",
        version=REPLAY_CASE_VERSION,
        question=(
            "What were the Acme widget adoption rate and export volume in "
            "the United States in 2024, and its production capacity in 2025?"
        ),
        max_extra_passes=1,
        topics=(
            _topic(
                1,
                "Adoption rate",
                "What was the Acme widget adoption rate in the United States in 2024?",
                "rate",
                "Acme widget adoption rate United States 2024",
                _pair(1, "adoption-2024c", "Adoption survey", adoption_claim),
                labels=("Acme widget", "adoption rate"),
            ),
            _topic(
                2,
                "Export volume",
                "What was the Acme widget export volume in the United States in 2024?",
                "value",
                "Acme widget export volume United States 2024",
                (
                    _page(
                        "exportsurvey.example.test",
                        "export-2024c",
                        "Export survey",
                        export_claim,
                        issuer="Acme Institute",
                    ),
                    *registers,
                    _page(
                        "capacitysurvey.example.test",
                        "capacity-2024",
                        "Capacity survey",
                        capacity_claim,
                        issuer="Acme Institute",
                        discovered=2,
                    ),
                ),
                labels=("Acme widget", "export volume"),
                follow_up_queries=(
                    "Acme widget production capacity United States 2024",
                ),
                extra_targets=(
                    ExtraEvidenceTarget(
                        question=(
                            "What was the Acme widget production capacity in "
                            "the United States in 2025?"
                        ),
                        measure="capacity",
                        unit_dimension="count",
                        period="2025",
                        kind="actual",
                    ),
                ),
            ),
        ),
        review_defect=ReplayReviewDefect(
            target_ids=("topic-01-target-01",),
            kind="presentation",
            severity="major",
            problem=(
                "The adoption-rate section restates the figure without "
                "naming its own subject plainly; redraft it."
            ),
        ),
        # This case tests the D4 extra-pass mechanism itself: it needs the
        # six refused registers to exhaust the opening pass's whole turn
        # budget for topic-02 (whose first read, the export survey, already
        # spends one), so it pins the researcher's own budget and the
        # decision-turn cap rather than drifting with config.yaml's limits.
        agent_overrides={
            "tool_budget_overrides": {"researcher": 20},
            "max_iterations": 8,
        },
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=(
                "topic-01-target-01",
                "topic-02-target-01",
                "topic-02-target-02",
            ),
            allowed_failure_classes=(
                "error:agent_tool_failed",
                # The extra pass's own extraction packet plans only the
                # missing target; re-extracting the export survey's already-
                # read page under that narrower plan is what drops its own
                # (correct, pass-0) target id as "unplanned" here -- a
                # harness-side artifact of a part with two targets, the first
                # this matrix exercises, never a defect in the finding kept.
                "error:researcher_unplanned_target",
            ),
            required_report_phrases=(
                "40 percent",
                "3.4 million units",
                "8 million units",
            ),
            required_invariants=("extra_pass_redrafts_the_gaining_part",),
        ),
    )





class ReplayCaseEntry:
    """One declared matrix row: identity, expectation, and its builder."""

    def __init__(
        self,
        *,
        case_id: str,
        version: int,
        title: str,
        expected_product_result: str,
        decisive_assertion: str,
        build: Callable[[], ReplayScenario],
    ) -> None:
        self.case_id = case_id
        self.version = version
        self.title = title
        self.expected_product_result = expected_product_result
        self.decisive_assertion = decisive_assertion
        self.build = build


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
        title="An extra pass that buys nothing stops, and says what is missing",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "One extra pass is bought for the missing obligation and finds "
            "nothing; the report publishes once with the target under Not found"
        ),
        build=_extra_pass_finds_nothing,
    ),
    ReplayCaseEntry(
        case_id="report-relay-labelled-as-relay",
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
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The 2024 actual keeps its own obligation and reaches Not found; "
            "the 2030 projection is never published as the current rate"
        ),
        build=_forecast_versus_actual_kept_apart,
    ),
    ReplayCaseEntry(
        case_id="unsupported-mechanism",
        version=REPLAY_CASE_VERSION,
        title="An invented mechanism never reaches the reader",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The pages state an outcome and no cause; the drafted causal "
            "recommendation is refused by the Statement Check, and no reader "
            "sentence states a cause"
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
            "No obligation is answered and every one reaches Not found; the "
            "review refuses the report (0.4 < 0.80), and clean headings with a "
            "'should' never make it an answer"
        ),
        build=_empty_but_clean,
    ),
    ReplayCaseEntry(
        case_id="memory-is-not-read",
        version=REPLAY_CASE_VERSION,
        title="A remembered claim is a lead, never a read",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Two remembered claims with different source URLs reach a decision "
            "packet and are never recorded as reads of this run or cited as a "
            "finding's source; the topic's obligation reaches Not found"
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
        case_id="report-scope-corrected-to-all-segments",
        version=REPLAY_CASE_VERSION,
        title="An all-segment figure written as grid-scale is corrected",
        expected_product_result="accepted / 0",
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
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The failure is recorded, every sentence publishes as drafted, and "
            "the composition records every printed sentence as unchecked -- "
            "the error is the record, not a gate"
        ),
        build=_statement_check_failure_keeps_sentences,
    ),
    ReplayCaseEntry(
        case_id="two-subjects-one-value",
        version=REPLAY_CASE_VERSION,
        title="Two products, one rating value, two rows",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Two figures equal in value, organisation, period and kind stay "
            "two rows because their subjects differ, and neither sentence is "
            "refused as a restatement"
        ),
        build=_two_subjects_one_value,
    ),
    ReplayCaseEntry(
        case_id="comparison-target-names-both-products",
        version=REPLAY_CASE_VERSION,
        title="A comparison target naming both products keeps two rows",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Two figures equal in value, organisation, period and kind stay "
            "two rows when the target's own question names both products, and "
            "each published sentence carries its own row's label"
        ),
        build=_comparison_target_names_both_products,
    ),
    ReplayCaseEntry(
        case_id="two-versions-one-target",
        version=REPLAY_CASE_VERSION,
        title="Two versions of one product are two facts, not one revision",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Two editions answering one obligation with the same value and "
            "different subjects stay two rows, and neither carries an earlier "
            "edition"
        ),
        build=_two_versions_one_target,
    ),
    ReplayCaseEntry(
        case_id="single-subject-spellings",
        version=REPLAY_CASE_VERSION,
        title="Three spellings of one subject are one fact row",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "A subject that restates its own target names nothing, so three "
            "pages stating one figure produce exactly one Key Facts row"
        ),
        build=_single_subject_spellings,
    ),
    ReplayCaseEntry(
        case_id="prose-only-question",
        version=REPLAY_CASE_VERSION,
        title="A why question whose pages state no figure",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Qualitative obligations are answered by figureless findings, the "
            "key facts section says no figure passed the verifier, and the "
            "Statement Check is shown each finding's snippet and body"
        ),
        build=_prose_only_question,
    ),
    ReplayCaseEntry(
        case_id="count-unit-period",
        version=REPLAY_CASE_VERSION,
        title="A count obligation is answered by its own year's count",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Of two counts on one page only the 2025 one answers the 2025 "
            "obligation, and the 2026 forecast obligation no page answers buys "
            "one extra pass, is listed under Not found, and leaves the run "
            "accepted"
        ),
        build=_count_unit_period,
    ),
    ReplayCaseEntry(
        case_id="purchase-year-empty-period",
        version=REPLAY_CASE_VERSION,
        title="A purchase year leaves the obligation's period empty",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "A target for a year the reader is deciding in states no period, "
            "so a rating the tester dated 2025 still answers it"
        ),
        build=_purchase_year_empty_period,
    ),
    ReplayCaseEntry(
        case_id="relative-period-resolved",
        version=REPLAY_CASE_VERSION,
        title="A relative period is resolved from the page's own date, or refused",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The kept figure's period is 2026 with the page date it came from, "
            "the label says so, and the same words on an undated page are "
            "refused with correction_not_on_page"
        ),
        build=_relative_period_resolved,
    ),
    ReplayCaseEntry(
        case_id="unattributed-relay-prose",
        version=REPLAY_CASE_VERSION,
        title="Prose that credits a body the page does not is refused",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The unattributed figure is the site's, the drafted sentence "
            "crediting another body is corrected to credit the site, and no "
            "reader sentence states the forbidden attribution"
        ),
        build=_unattributed_relay_prose,
    ),
    ReplayCaseEntry(
        case_id="one-part-question",
        version=REPLAY_CASE_VERSION,
        title="A plan with one part is not a structural defect",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "A one-topic plan is accepted by the planner's own structural gate "
            "and the run completes accepted"
        ),
        build=_one_part_question,
    ),
    ReplayCaseEntry(
        case_id="maker-notes-vs-relay",
        version=REPLAY_CASE_VERSION,
        title="The maker's own figure and a relay of another one",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The maker's own page is labelled as its own figure and the news "
            "relay is labelled as the relay it is, naming both the relay and "
            "the maker"
        ),
        build=_maker_notes_vs_relay,
    ),
    ReplayCaseEntry(
        case_id="scoped-redraft-after-a-named-defect",
        version=REPLAY_CASE_VERSION,
        title="A named defect redrafts one part; the re-review is scoped",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The redraft touches only the part the defect names; the "
            "untouched part carries over byte-identical and the second "
            "review is a scoped re-review, never a second full one"
        ),
        build=_scoped_redraft_after_a_named_defect,
    ),
    ReplayCaseEntry(
        case_id="scoped-review-invalid-reply-falls-back",
        version=REPLAY_CASE_VERSION,
        title="An unusable scoped reply falls back to one full review",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The scoped attempt is made and fails; the run's final judgement "
            "is one fresh full review, never a scoped-derived result"
        ),
        build=_scoped_review_invalid_reply_falls_back,
    ),
    ReplayCaseEntry(
        case_id="extra-pass-redrafts-the-gaining-part",
        version=REPLAY_CASE_VERSION,
        title="A part that gains a finding on the extra pass is rewritten",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The part with the missing target already has a pass-0 section; "
            "after the extra pass answers it, that part's status is "
            "'written', not 'carried_over', and its section cites the new "
            "finding, never silently dropping evidence the extra pass just "
            "bought"
        ),
        build=_extra_pass_redrafts_the_gaining_part,
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
    return manifest_entry(case_id).build()


__all__ = [
    "REPLAY_CASE_IDS",
    "REPLAY_CASE_MANIFEST",
    "REPLAY_CASE_MANIFEST_VERSION",
    "REPLAY_CASE_VERSION",
    "ReplayCaseEntry",
    "manifest_entry",
    "replay_scenarios",
    "scenario_by_id",
]