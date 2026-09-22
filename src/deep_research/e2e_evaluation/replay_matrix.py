"""The versioned offline matrix: eighteen real-agent scenarios.

The manifest below is the *declared inventory* the release proof is measured
against. Each row names a case id, the version of its semantics, the product
result the plan expects, the decisive assertion that makes it that result, and
the scenario builder that drives the real stack.

Every case runs the six production agents through the real graph. The
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
) -> ReplaySource:
    """One authored page, with the run's read of it scripted around it.

    The prose is the fixture's whole point: the claim is stated in a sentence
    the page really contains, by an issuer the page really names ("Published
    by ..."), because those two facts are what the read is judged on. The
    verdict travels with the page rather than with the pair, so a case can
    script an outage, a refusal, or a contradiction on exactly one read.
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
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=tuple(
                f"topic-{index:02d}-target-01" for index in range(1, 7)
            ),
            minimum_answerable_claims=6,
            required_report_phrases=(
                "40 percent",
                "12 million dollars",
                "1.2 million supplier records",
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
    """
    urban = (
        "the Acme widget adoption rate in urban households in the United States was "
        "40 percent in 2024"
    )
    rural = (
        "the Acme widget adoption rate in rural households in the United States was "
        "25 percent in 2024"
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
            _filler(
                3, "Widget funding", "Acme widget funding round", "12 million dollars"
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
            minimum_answerable_claims=3,
            required_report_phrases=("urban households", "rural households"),
            required_invariants=("both_accounts_cited",),
        ),
    )


def _refinement_evidence_recovery() -> ReplayScenario:
    """The missing account is published later, and the repair round finds it.

    The opening round can read one publisher: the claim is competent but
    uncorroborated, so its obligation is not met and the run owes itself a
    repair. The second round's search is what surfaces the second publisher,
    which is the only way the target can be answered - so a run that recovered
    it did so by acquiring new evidence, not by re-reading what it had.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="refinement-evidence-recovery",
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
            minimum_answerable_claims=3,
            required_invariants=("refinement_recovered_evidence",),
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
        content_type=PDF,
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
            minimum_answerable_claims=3,
            required_invariants=(
                "denied_url_not_retried",
                "mirror_not_double_counted",
            ),
        ),
    )


def _same_work_mirror() -> ReplayScenario:
    """One work on two hosts is one work, however many publishers print it.

    The same document text is served by two different publishers. Both reads
    are real and both are admitted, and the pair is still refused: the
    publisher differs, the work does not. The obligation stays unanswered, and
    the run must say so rather than promote a reprint to corroboration.
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
            terminal_quality="partial",
            exit_code=4,
            required_target_ids=("topic-02-target-01", "topic-03-target-01"),
            minimum_answerable_claims=2,
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
                "semantic_review_missing",
                # The topic whose one work cannot corroborate itself is done as
                # far as the Researcher can take it, so the pass that owes it
                # nothing records the skip instead of buying the same page.
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("no_false_verification", "mirror_not_double_counted"),
        ),
    )


def _semantic_duplicate_claims() -> ReplayScenario:
    """Paraphrases of one fact collapse; a different year does not.

    Two pages state the same 2024 figures in different words, and the run's
    equivalence pass is scripted to propose exactly that pair: one fact, two
    wordings, one row. A third page states the same measure for 2023, and
    nothing proposes it - a different period is a different claim, and merging
    it would put a stale figure behind a current answer.
    """
    long_form = (
        "the Acme widget adoption rate in urban households in the United States was "
        "40 percent in 2024"
    )
    short_form = (
        "urban household Acme widget adoption in the United States reached "
        "40 percent in 2024"
    )
    stale = (
        "the Acme widget adoption rate in urban households in the United States was "
        "32 percent in 2023"
    )
    return ReplayScenario(
        case_id="semantic-duplicate-claims",
        version=REPLAY_CASE_VERSION,
        question="What was urban Acme widget adoption in the United States in 2024?",
        topics=(
            _topic(
                1,
                "Urban adoption",
                "What was the urban household Acme widget adoption rate in the United "
                "States in 2024?",
                "rate",
                "urban Acme widget adoption United States 2024",
                (
                    _page(
                        "agency6.example.test",
                        "urban-2024",
                        "Urban adoption survey",
                        long_form,
                        issuer="Acme Institute 6",
                    ),
                    _page(
                        "bureau6.example.test",
                        "urban-2024",
                        "Urban adoption panel",
                        short_form,
                        issuer="Independent Bureau 6",
                    ),
                ),
                critical=True,
                labels=("Acme widget", "urban households"),
            ),
            _topic(
                2,
                "Adoption history",
                "What did the Acme widget adoption rate in urban households measure in "
                "2023?",
                "rate",
                "urban Acme widget adoption United States 2023",
                (
                    _page(
                        "archives6.example.test",
                        "urban-2023",
                        "Urban adoption archive",
                        stale,
                        issuer="Acme Archives 6",
                    ),
                ),
                critical=False,
                labels=("Acme widget", "urban households"),
            ),
            _filler(
                3, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
        ),
        equivalence_pairs=((1, 2),),
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-03-target-01"),
            minimum_answerable_claims=2,
            required_invariants=(
                "paraphrases_merged",
                "different_periods_stay_distinct",
            ),
        ),
    )


def _stalled_refinement() -> ReplayScenario:
    """A repair round that buys nothing must stop, and say what stopped it.

    One uncorroborated claim owes an obligation, the repair acquires, and the
    search returns the page the run already read. Nothing changes, so the
    second pass is the last one: a run that kept buying the same empty round
    would spend its budget proving nothing, and the case asserts the recorded
    stop reason rather than the exit code alone.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="stalled-refinement",
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
            minimum_answerable_claims=2,
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
                "semantic_review_missing",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("stalled_refinement_stopped",),
        ),
    )


def _primary_attribution() -> ReplayScenario:
    """An official measurement answered by primary attribution, never a pair.

    The row's decisive assertion is the *badge*: the report may state the
    figure the issuing body published, and must not dress it up as an
    independently corroborated pair, because nothing in the run read a second
    measurement of it.

    Two shapes have to be right for the target to be answered at all, and both
    are properties of the prose rather than of the harness: the clause must
    name its issuing body the way the attribution contract reads one, and it
    must state the value, the year and the geography the frozen contract
    requires. A page whose sentence omits any of them leaves the target
    unanswered no matter how authoritative it is.

    The four pages state the same figure in four different sentences on
    purpose. One official body publishing one number is exactly the shape that
    must never earn the independent-pair badge, and four pages carrying one
    identical sentence would be one claim recorded four times rather than four
    accounts of it.
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
            "official.example.test",
            url_slug,
            title,
            claim,
            issuer="Acme Institute",
            source_role="company_statement",
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
        case_id="primary-attribution",
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
                "verified pair",
                "independently corroborated",
            ),
            minimum_answerable_claims=4,
            # Both directions of the same claim: the four obligations are
            # answered from one publisher each, so the run must answer them
            # *without* recording an independent pair (the first checker) and
            # the pair badge, if it ever appeared, must rest on two publishers
            # and two complete reads (the second).
            required_invariants=(
                "primary_attribution_not_verified",
                "no_false_verification",
            ),
        ),
    )


def _current_versus_forecast() -> ReplayScenario:
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
        case_id="current-versus-forecast",
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
            minimum_answerable_claims=2,
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
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
                "mechanism",
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
            minimum_answerable_claims=2,
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
                "semantic_review_missing",
                "error:researcher_sub_topic_skipped",
                "error:researcher_sub_topic_without_findings",
            ),
            required_invariants=("mechanism_obligation_stays_unanswered",),
        ),
    )


def _judge_failure() -> ReplayScenario:
    """Complete reader artifacts, and no semantic judgement to accept them.

    The report is composed and published and every obligation is answered, and
    the terminal review cannot be made. A structural clean bill of health is
    not an acceptance: with no judgement recorded, the run must not pass
    strict mode, and the artifacts it did produce stay readable.
    """
    return ReplayScenario(
        case_id="judge-failure",
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
            minimum_answerable_claims=3,
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
            minimum_answerable_claims=3,
            required_invariants=("no_ranked_constraints_for_a_factual_answer",),
        ),
    )


def _late_contradiction() -> ReplayScenario:
    """A contradicting account stays visible and blocks a false settlement.

    Two publishers agree on the figure and a third states a different one, and
    the disagreement is recorded rather than averaged away. The run may not
    publish the contested number as settled while an account it read says
    otherwise: an obligation that looks answered is not answered if the
    evidence behind it disagrees.
    """
    agreed = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    contested = (
        "the Acme widget adoption rate in the United States was 30 percent in 2024"
    )
    return ReplayScenario(
        case_id="late-contradiction",
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
                    *_pair(1, "adoption-2024", "Adoption survey", agreed),
                    _page(
                        "contested1.example.test",
                        "adoption-rival",
                        "Rival adoption estimate",
                        contested,
                        issuer="Rival Statistics Office",
                        verdict="contradicts",
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
            forbidden_assertions=("independent estimates agree",),
            minimum_answerable_claims=2,
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
                "semantic_review_missing",
            ),
            required_invariants=("contradiction_recorded",),
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
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
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
            minimum_answerable_claims=2,
            required_gap_kinds=("hard:unanswered_critical_targets",),
            allowed_failure_classes=(
                "hard:unaccounted_required_targets",
                "unanswered_critical_target",
                "unaccounted_target",
                "semantic_review_missing",
            ),
            required_invariants=("memory_leads_are_not_reads",),
        ),
    )


def _validated_cache_reuse() -> ReplayScenario:
    """A read is downloaded once, and every later answer reuses it.

    The same page is the evidence for two obligations, and the second one is
    answered from the registry the first read populated: one body, two
    answers. Cache provenance is validated rather than trusted, which the
    companion test in the release-proof module exercises directly with a
    forged record.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    shared = _page(
        "agency11.example.test",
        "adoption-2024",
        "Adoption survey",
        claim,
        issuer="Acme Institute 11",
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
                (
                    shared,
                    _page(
                        "bureau11.example.test",
                        "adoption-panel-2024",
                        "Adoption panel",
                        claim,
                        issuer="Independent Bureau 11",
                    ),
                ),
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
            _filler(
                3, "Widget funding", "Acme widget funding round", "12 million dollars"
            ),
        ),
        max_iterations=3,
        expectation=CaseExpectation(
            terminal_quality="accepted",
            exit_code=0,
            required_target_ids=("topic-01-target-01", "topic-03-target-01"),
            minimum_answerable_claims=2,
            required_invariants=("read_downloaded_once",),
        ),
    )


def _decision_context_late_candidate() -> ReplayScenario:
    """The third candidate and the late section still reach the next request.

    Two of the three candidates for the topic carry nothing a claim can be
    made of, and the one that does is last in the list. The run has to keep
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
                    _page(
                        "agency12.example.test",
                        "cover-note",
                        "Adoption cover note",
                        "the note lists a title and a publication date and no measured "
                        "value",
                        issuer="Acme Institute 12",
                        verdict="insufficient_evidence",
                    ),
                    _page(
                        "agency12.example.test",
                        "method-note",
                        "Adoption method note",
                        "the note describes the method and states no measured value",
                        issuer="Acme Institute 12",
                        verdict="insufficient_evidence",
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
            minimum_answerable_claims=3,
            required_report_phrases=("40 percent",),
            required_invariants=(
                "late_candidate_reached_decision",
                "public_summary_stayed_short",
            ),
        ),
    )


def _reopen_unanswered_target() -> ReplayScenario:
    """An obligation reopens on its own evidence, with no Critic gap to prompt it.

    The Critic is scripted to report no gaps, so nothing but the unanswered
    required target can send the run back to work - and the page that answers
    it is only published in the second round. The obligation resumes, the
    second round reads it, and the answer comes from a statement the evidence
    supports rather than from the metadata the first round found.
    """
    claim = "the Acme widget adoption rate in the United States was 40 percent in 2024"
    return ReplayScenario(
        case_id="reopen-unanswered-target",
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
                        "the record lists a title and a publication date and no "
                        "measured value",
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
            minimum_answerable_claims=3,
            required_invariants=("required_target_reopened",),
        ),
    )


# --- the manifest ------------------------------------------------------------


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
        graph_only_historical: bool = False,
    ) -> None:
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
        title="Two populations, two accounts, no invented winner",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Explains differing populations/methods; no invented universal "
            "winner; both sources cited"
        ),
        build=_comparative_conflict,
    ),
    ReplayCaseEntry(
        case_id="refinement-evidence-recovery",
        version=REPLAY_CASE_VERSION,
        title="The missing account is acquired in the repair round",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Only missing target is acquired/rechecked; new support changes "
            "fingerprint; no repeated stable checks"
        ),
        build=_refinement_evidence_recovery,
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
        title="One work reprinted on two hosts is not a pair",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Critical independent-pair target has one underlying work; zero "
            "false verification"
        ),
        build=_same_work_mirror,
    ),
    ReplayCaseEntry(
        case_id="semantic-duplicate-claims",
        version=REPLAY_CASE_VERSION,
        title="Paraphrases collapse, different periods stay apart",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Queue/cutoff/PJM paraphrases collapse; conflicting units/years "
            "remain distinct"
        ),
        build=_semantic_duplicate_claims,
    ),
    ReplayCaseEntry(
        case_id="stalled-refinement",
        version=REPLAY_CASE_VERSION,
        title="A repair round that buys nothing stops",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Fully processed unchanged repair stops; correct unresolved "
            "target/cause"
        ),
        build=_stalled_refinement,
    ),
    ReplayCaseEntry(
        case_id="primary-attribution",
        version=REPLAY_CASE_VERSION,
        title="Official measurement answered as primary attribution",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "The exact official measurement question is answered as "
            "primary-attributed, and not falsely verified."
        ),
        build=_primary_attribution,
    ),
    ReplayCaseEntry(
        case_id="current-versus-forecast",
        version=REPLAY_CASE_VERSION,
        title="A projection is not the current figure",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Historical observation/future forecast cannot satisfy a required "
            "current estimate"
        ),
        build=_current_versus_forecast,
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
        case_id="judge-failure",
        version=REPLAY_CASE_VERSION,
        title="Complete artifacts, absent semantic assessment",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Complete reader artifacts may exist, but absent semantic "
            "assessment cannot pass strict mode"
        ),
        build=_judge_failure,
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
        case_id="late-contradiction",
        version=REPLAY_CASE_VERSION,
        title="A contradicting account blocks a false settlement",
        expected_product_result="partial / 4",
        decisive_assertion=(
            "Material conflicting passage/statement past prefix limits remains "
            "visible and blocks false settlement"
        ),
        build=_late_contradiction,
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
        case_id="reopen-unanswered-target",
        version=REPLAY_CASE_VERSION,
        title="A required obligation reopens without a Critic gap",
        expected_product_result="accepted / 0",
        decisive_assertion=(
            "Required topic work resumes despite one prior metadata finding "
            "and no matching Critic gap; completion comes from a substantive "
            "supported reader answer"
        ),
        build=_reopen_unanswered_target,
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
