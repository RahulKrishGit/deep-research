"""Spec §5.3, §6.4 and §6.6: facts are read from verified fields, never from prose."""

from __future__ import annotations

import pytest

from deep_research.agents.evidence_verifier import (
    ContextItem,
    FigureCheckDraft,
    figure_match,
    verify_finding,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.verified_facts import (
    _answers_organisation,
    _period_stated_in,
    _rows_share_a_subject,
    _target_fields,
    answered_target_ids,
    fact_rows,
    finding_answers,
    not_found_targets,
    release_key,
    resolve_relative_period,
    same_organisation,
    same_period,
    same_subject,
    subject_context,
    subject_named_in,
)
from deep_research.utils.types import (
    AcquisitionState,
    FigureContext,
    FigureResult,
    FindingVerification,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

EIA = "U.S. Energy Information Administration"


def ctx(organisation=EIA, attribution="own", period="2024", kind="actual", scope=None,
        subject=None):
    return FigureContext(period=period, scope=scope, attribution=attribution,
                         organisation=organisation, kind=kind, subject=subject)


def verified(finding, *contexts, unchecked=False, dropped=False):
    if dropped:
        verification = FindingVerification(status="dropped", dropped_reason="snippet_not_on_page")
    else:
        results = [FigureResult(figure=f, matched=True, context=c)
                   for f, c in zip(finding.figures, contexts)]
        verification = FindingVerification(status="verified", figure_results=results,
                                           context_unchecked=unchecked)
    return finding.model_copy(update={"verification": verification})


def eia_2024(value="10.4", **fields):
    read = make_read()
    finding = make_finding(read, "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
                           figures=[figure(value, "GW", "2024", "actual")],
                           target_ids=["topic-01-target-01"], **fields)
    return verified(finding, ctx())


@pytest.mark.parametrize(("left", "right", "same"), [
    (EIA, "EIA", True),
    ("EIA", "eia.gov", True),
    (EIA, "eia.gov", True),
    ("Wood Mackenzie", "woodmac.com", True),
    ("BloombergNEF", "bnef.com", True),
    ("American Clean Power Association", "ACP", True),
    ("Energy Information Administration", EIA, True),
    ("EIA", "eia.news", False),
    ("EIA", "IEA", False),
    ("Wood Mackenzie", "BloombergNEF", False),
    ("energy.gov", EIA, False),
    ("Energy", EIA, False),
    ("Wood", "Wood Mackenzie", False),
    ("American", "American Clean Power Association", False),
    ("SEIA", "Solar Energy Industries Association", True),
    ("seia.org", "Solar Energy Industries Association", True),
    ("EPRI", "Electric Power Research Institute", True),
    ("IEA", "International Energy Agency", True),
    ("iea.org", "International Energy Agency", True),
    (f"{EIA} (EIA)", "EIA", True),
])
def test_same_organisation(left, right, same) -> None:
    assert same_organisation(left, right) is same
    assert same_organisation(right, left) is same


def test_same_period() -> None:
    assert same_period("2025", "in 2025") and same_period("calendar year 2024", "2024")
    assert not same_period("2025", "Q3 2025") and not same_period(None, "2025")


def test_a_verified_figure_answers_its_matching_target() -> None:
    finding = eia_2024()
    assert finding_answers(finding, make_target(organisation="EIA"))
    assert not finding_answers(finding, make_target(organisation="Wood Mackenzie"))
    assert not finding_answers(finding, make_target(kind="forecast"))
    assert not finding_answers(finding, make_target(period="2025"))
    assert not finding_answers(finding, make_target("topic-02-target-01", period="2024"))
    assert not finding_answers(finding.model_copy(update={"verification": None}), make_target())


def test_a_qualitative_target_is_answered_by_naming_it() -> None:
    read = make_read()
    finding = make_finding(read, "Generators added 10.4 gigawatts", target_ids=["topic-01-target-01"])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified")})
    assert finding_answers(finding, make_target(unit_dimension=None, organisation="eia.gov"))


def test_an_own_page_and_its_relay_are_one_row_citing_the_own_page() -> None:
    own = eia_2024()
    relay_read = make_read("According to EIA, generators added 10.4 GW in 2024.",
                           url="https://www.utilitydive.com/news/x", title="x")
    relay = verified(make_finding(relay_read, "According to EIA, generators added 10.4 GW in 2024.",
                                  figures=[figure("10.4", "GW", "2024", "actual")],
                                  target_ids=["topic-01-target-01"]),
                     ctx(organisation="EIA", attribution="relayed"))
    [row] = fact_rows([relay, own], [make_target()])
    assert row.finding_id == finding_fingerprint(own) and row.attribution == "own"
    assert row.duplicate_finding_ids == [finding_fingerprint(relay)]
    assert row.row_id == "K001" and row.target_ids == ["topic-01-target-01"]


def test_a_later_release_is_a_revision_with_the_earlier_edition_noted() -> None:
    latest = eia_2024(release_date="2025-03-12")
    earlier = eia_2024(value="10.3", release_date="2025-02-10").model_copy(update={"snippet": "power providers added a record 10.3 GW"})
    [row] = fact_rows([earlier, latest], [make_target()])
    assert row.value == "10.4 GW" and row.release == "released 2025-03-12"
    assert [(e.value, e.release) for e in row.earlier] == [("10.3 GW", "released 2025-02-10")]


def test_forecast_and_actual_never_merge_and_two_organisations_stay_two_rows() -> None:
    actual = eia_2024()
    forecast = verified(actual.model_copy(update={"figures": [figure("10.4", "GW", "2024", "forecast")], "verification": None}),
                        ctx(kind="forecast"))
    other = verified(actual.model_copy(update={"verification": None}), ctx(organisation="Wood Mackenzie"))
    rows = fact_rows([actual, forecast, other], [make_target()])
    assert len(rows) == 3 and all(not row.earlier for row in rows)


def test_dropped_findings_answer_nothing_and_make_no_row() -> None:
    dropped = verified(eia_2024().model_copy(update={"verification": None}), dropped=True)
    assert answered_target_ids([dropped], [make_target()]) == {}
    assert fact_rows([dropped], [make_target()]) == []


def test_release_key() -> None:
    assert release_key(eia_2024(release_date="2025-03-12")) == (2025, 3, 12)
    assert release_key(eia_2024(vintage="January 2025 STEO")) == (2025, 1, 0)
    assert release_key(eia_2024()) is None


def test_not_found_lists_required_unanswered_targets_with_their_trail() -> None:
    required, optional = make_target("topic-02-target-01", kind="forecast", period="2025"), make_target("topic-02-target-02", required=False)
    topic = SubTopic(coverage_id="topic-02", title="EIA forecast", rationale="r", search_queries=["EIA STEO 2025 battery"],
                     success_criteria=["c"], priority=1, evidence_targets=[required, optional])
    acquisition = {"topic-02": AcquisitionState(read_urls=["https://www.eia.gov/outlooks/steo/"], consecutive_searches=1)}
    [row] = not_found_targets([topic], {}, acquisition)
    assert (row.target_id, row.queries, row.searched) == ("topic-02-target-01", ["EIA STEO 2025 battery"], True)
    assert row.pages_read == ["https://eia.gov/outlooks/steo"]


def test_earlier_editions_are_ordered_newest_first_regardless_of_input_order() -> None:
    a = eia_2024(value="18.2", release_date="2025-02-10")
    b = eia_2024(value="19.1", release_date="2025-02-20")
    c = eia_2024(value="20.0", release_date="2025-03-01")
    expected_earlier = [("19.1 GW", "released 2025-02-20"), ("18.2 GW", "released 2025-02-10")]
    for findings in ([a, b, c], [c, b, a], [a, c, b], [b, a, c]):
        [row] = fact_rows(list(findings), [make_target()])
        assert row.value == "20.0 GW"
        assert [(e.value, e.release) for e in row.earlier] == expected_earlier


def test_target_answering_refuses_a_scope_mismatch_but_treats_grid_scale_and_utility_scale_as_one() -> None:
    read = make_read()
    grid_scale_target = make_target(measure="grid-scale additions")
    woodmac = verified(
        make_finding(read, "Wood Mackenzie added 12.3 GW across all segments in 2024,",
                     figures=[figure("12.3", "GW", "2024", "actual")],
                     target_ids=["topic-01-target-01"]),
        ctx(organisation="Wood Mackenzie", scope="all segments"),
    )
    eia_utility_scale = verified(
        make_finding(read, "EIA added 10.4 GW of utility-scale storage in 2024,",
                     figures=[figure("10.4", "GW", "2024", "actual")],
                     target_ids=["topic-01-target-01"]),
        ctx(scope="utility-scale"),
    )
    assert not finding_answers(woodmac, grid_scale_target)
    assert finding_answers(eia_utility_scale, grid_scale_target)


def _stated(read, snippet, value, unit, *, period="2024", kind="actual"):
    finding = make_finding(read, snippet, figures=[figure(value, unit, period, kind)],
                           target_ids=["topic-01-target-01"])
    return verified(finding, ctx(organisation="Example Statistical Agency",
                                 period=period, kind=kind))


def test_a_currency_target_checks_period_and_kind_like_a_power_target() -> None:
    text = "Public spending was USD 4.2 billion in 2024."
    read = make_read(text)
    target = make_target(measure="public spending", unit_dimension="currency")
    assert finding_answers(_stated(read, text, "4.2", "USD billion"), target)
    assert not finding_answers(_stated(read, text, "4.2", "USD billion", period="2023"), target)
    assert not finding_answers(_stated(read, text, "4.2", "USD billion", kind="forecast"), target)


def test_a_percent_figure_does_not_answer_a_currency_target() -> None:
    text = "Public funding was 12 percent of the total in 2024."
    share = _stated(make_read(text), text, "12", "percent")
    assert not finding_answers(share, make_target(measure="public funding", unit_dimension="currency"))
    assert finding_answers(share, make_target(measure="public funding share", unit_dimension="percent"))


@pytest.mark.parametrize(("words", "page_date", "period"), [
    ("installed 4 GW this year", "2026-02-20", "2026"),
    ("so far this year the fleet grew", "2026-02-20", "2026"),
    ("added last year", "2026-02-20", "2025"),
    ("planned for next year", "February 2026", "2027"),
    ("up 3 percent in the last quarter", "2026-02-20", "Q4 2025"),
    ("sales this quarter", "2026-05-02", "Q2 2026"),
    ("orders this month", "2026-01-15", "January 2026"),
    ("the best of this season", "2026-02-20", None),
    ("over the past year", "2026-02-20", None),
    ("sales this quarter", "2026", None),
    ("installed 4 GW in 2024", "2026-02-20", None),
    ("installed 4 GW this year", None, None),
    # Fix round 1: a period the words state themselves, a window of time, and a
    # garbled page date are never resolved into a calendar period.
    ("sales rose in the last quarter of 2024", "2026-02-20", None),
    ("the last month of the year", "2026-02-20", None),
    ("grew 5 percent over the last year", "2026-02-20", None),
    ("sales rose in the last year", "2026-02-20", None),
    ("over the last quarter", "2026-02-20", None),
    ("this month", "2026-20-02", None),
])
def test_a_relative_period_resolves_only_against_the_page_date(words, page_date, period) -> None:
    assert resolve_relative_period(words, page_date) == period


def test_a_resolved_period_reaches_its_key_facts_row() -> None:
    text = "Operators installed 4 GW this year, the agency said."
    finding = make_finding(make_read(text), text, figures=[figure("4", "GW", None, "actual")],
                           target_ids=["topic-01-target-01"])
    resolved = FigureContext(period="2026", scope=None, attribution="own",
                             organisation="Example Statistical Agency", kind="actual",
                             period_resolved_from="2026-02-20")
    [row] = fact_rows([verified(finding, resolved)], [make_target(period="2026")])
    assert (row.period, row.period_resolved_from) == ("2026", "2026-02-20")


def _rated(subject, value="4.5", *, period=None, page="lab"):
    text = f"In our tests {subject or 'the kettle'} scored {value} out of 5 for noise."
    slug = (subject or "kettle").casefold().replace(" ", "-")
    read = make_read(text, url=f"https://{page}.example.test/{slug}", title="Kettle tests")
    finding = make_finding(read, text,
                           figures=[figure(value, "out of 5", period, "actual").model_copy(
                               update={"subject": subject})],
                           target_ids=["topic-01-target-01"])
    return verified(finding, FigureContext(period=period, scope=None, attribution="own",
                                           organisation="Example Test Lab", kind="actual",
                                           subject=subject))


RATING = {"question": "Which kettles do testers rate quietest?", "measure": "noise rating",
          "unit_dimension": "rating", "period": None, "geography": None}


@pytest.mark.parametrize(("left", "right", "rows"), [
    ("Model A", "Model B", 2),
    ("X200", "Acme X200", 1),
    (None, "kettle noise", 1),
    ("version 10.02", "version 10.03", 2),
])
def test_equal_values_about_different_subjects_stay_apart(left, right, rows) -> None:
    target = make_target(**RATING)
    assert len(fact_rows([_rated(left, period="2026"), _rated(right, period="2026")], [target])) == rows


def test_figures_with_no_period_are_one_fact_only_on_a_shared_subject() -> None:
    """Ruling N1: one rating on its own page and on a relay; a re-test is a revision."""
    target = make_target(**RATING)
    assert len(fact_rows([_rated("Kettle K1"), _rated("Kettle K1", page="news")], [target])) == 1
    assert len(fact_rows([_rated("Kettle K1"), _rated("Kettle K2", page="news")], [target])) == 2
    assert len(fact_rows([_rated(None), _rated(None, page="news")], [target])) == 2
    early = _rated("Kettle K1").model_copy(update={"release_date": "2026-01-10"})
    retest = _rated("Kettle K1", "4.7", page="news").model_copy(update={"release_date": "2026-02-10"})
    [row] = fact_rows([early, retest], [target])
    assert row.value == "4.7 out of 5" and [e.value for e in row.earlier] == ["4.5 out of 5"]


def test_a_subject_that_restates_the_target_names_nothing() -> None:
    target = make_target()
    words = subject_context([target.target_id], [target])
    fields = _target_fields([target.target_id], [target])
    assert not subject_named_in("Model B scored a 4.5.", "Model A")
    assert same_subject("United States", "battery storage", context_words=words,
                        target_fields=fields)
    assert not same_subject("Spain", "Italy", context_words=words, target_fields=fields)
    assert subject_named_in("The Acme X200 scored 4.5.", "Acme X200")
    assert not subject_named_in("Model B scored 4.5.", "Model A")
    assert subject_named_in("Anything at all.", None)


def test_two_releases_about_two_versions_are_not_one_revision() -> None:
    target = make_target(**RATING)
    early = _rated("version 10.02", period="2026").model_copy(update={"release_date": "2026-01-10"})
    late = _rated("version 10.03", period="2026").model_copy(update={"release_date": "2026-02-10"})
    rows = fact_rows([early, late], [target])
    assert len(rows) == 2 and all(not row.earlier for row in rows)


def test_a_figure_answers_only_its_own_subjects_sibling_target() -> None:
    """D11 (Fable §8.5): two targets that ask one thing of two places.

    The Italian figure binds both targets (the extraction could not tell them
    apart), and the subject is the whole of what places it: it answers Italy's
    and not Spain's. The last assertion used to read the other way — an Italian
    figure counted as answering the Spanish target, because every field but the
    subject matched. That is the same failure the live pre-flight's review-02
    recorded (a figure about another subject answering the target), so Defect
    B's subject rule is asserted here as well, and only the recorded answer is
    ``answered_target_ids``.
    """
    spain = make_target("topic-01-target-01", question="What was Spain's unemployment rate in 2024?",
                        measure="unemployment rate", unit_dimension="percent", geography="Spain",
                        organisation="Example Statistical Agency")
    italy = make_target("topic-01-target-02", question="What was Italy's unemployment rate in 2024?",
                        measure="unemployment rate", unit_dimension="percent", geography="Italy",
                        organisation="Example Statistical Agency")
    text = "Italy's unemployment rate was 6.5 percent in 2024."
    finding = make_finding(make_read(text), text,
                           figures=[figure("6.5", "percent", "2024", "actual").model_copy(
                               update={"subject": "Italy"})],
                           target_ids=[spain.target_id, italy.target_id])
    italian = verified(finding, FigureContext(period="2024", scope=None, attribution="own",
                                              organisation="Example Statistical Agency",
                                              kind="actual", subject="Italy"))
    assert set(answered_target_ids([italian], [spain, italy])) == {italy.target_id}
    assert not finding_answers(italian, spain)


# Fix round 1 (CRITICAL 1): a target whose question carries an article.
ARTICLE_RATING = {**RATING, "question": "What noise rating did testers give a kettle?"}


def test_an_article_in_the_targets_question_never_strips_a_subject() -> None:
    """Fix round 1 (CRITICAL 1): "a" is filler in the target's words, never in the subject's."""
    target = make_target(**ARTICLE_RATING)
    assert len(fact_rows([_rated("Model A"), _rated("Model B", page="news")], [target])) == 2
    early = _rated("Model A").model_copy(update={"release_date": "2026-01-10"})
    late = _rated("Model B", "4.7", page="news").model_copy(update={"release_date": "2026-02-10"})
    rows = fact_rows([early, late], [target])
    assert len(rows) == 2 and all(not row.earlier for row in rows)


def test_a_group_admits_only_figures_that_are_one_fact_with_every_member() -> None:
    """Fix round 1 (IMPORTANT 3): a subject-less figure is not a wildcard for its group."""
    target = make_target(**RATING)
    nothing = _rated(None, period="2026").model_copy(update={"release_date": "2026-01-05"})
    model_a = _rated("Model A", period="2026").model_copy(update={"release_date": "2026-02-05"})
    model_b = _rated("Model B", period="2026").model_copy(update={"release_date": "2026-02-06"})

    def rows(findings):
        return [(row.value, row.subject, len(row.duplicate_finding_ids))
                for row in fact_rows(findings, [target])]

    assert rows([nothing, model_a, model_b]) == rows([model_a, model_b, nothing])
    assert rows([nothing, model_a, model_b]) == [("4.5 out of 5", "Model A", 1),
                                                 ("4.5 out of 5", "Model B", 0)]


def test_a_revision_needs_the_same_subject_not_merely_a_nested_spelling() -> None:
    """Fix round 1 (Minor 4 ruling): a fold claims a release history; only one subject earns it."""
    target = make_target(**RATING)
    x200 = _rated("X200", period="2026").model_copy(update={"release_date": "2026-01-10"})
    x200_pro = _rated("X200 Pro", "4.7", page="news", period="2026").model_copy(
        update={"release_date": "2026-02-10"})
    rows = fact_rows([x200, x200_pro], [target])
    assert len(rows) == 2 and all(not row.earlier for row in rows)
    acme = _rated("Acme X200", period="2026")
    assert len(fact_rows([acme, _rated("X200", page="news", period="2026")], [target])) == 1


SPAIN, ITALY = "topic-01-target-01", "topic-01-target-02"


def _siblings():
    """Two targets asking one thing of two places (D11, Fable §8.5)."""
    return (make_target(SPAIN, question="What was Spain's unemployment rate in 2024?",
                        measure="unemployment rate", unit_dimension="percent", geography="Spain",
                        organisation="Example Statistical Agency"),
            make_target(ITALY, question="What was Italy's unemployment rate in 2024?",
                        measure="unemployment rate", unit_dimension="percent", geography="Italy",
                        organisation="Example Statistical Agency"))


def _unemployment_figure(subject, page, targets):
    """One 6.5 percent 2024 figure about ``subject``, bound to every target given."""
    text = "Spain and Italy both recorded 6.5 percent unemployment in 2024."
    read = make_read(text, url=f"https://{page}.example.test/{subject.casefold()}",
                     title="Labour statistics")
    finding = make_finding(read, text,
                           figures=[figure("6.5", "percent", "2024", "actual").model_copy(
                               update={"subject": subject})],
                           target_ids=[target.target_id for target in targets])
    return verified(finding, FigureContext(period="2024", scope=None, attribution="own",
                                           organisation="Example Statistical Agency",
                                           kind="actual", subject=subject))


def test_a_finding_bound_to_both_sibling_targets_keeps_its_own_subject() -> None:
    """Fix round 1 (IMPORTANT 2): the shared targets' words are their intersection, not their union."""
    spain, italy = _siblings()
    figures = [_unemployment_figure("Spain", "lab", [spain, italy]),
               _unemployment_figure("Italy", "news", [spain, italy])]
    rows = fact_rows(figures, [spain, italy])
    assert [row.subject for row in rows] == ["Spain", "Italy"]
    assert len(answered_target_ids(figures, [spain, italy])) == 2


def test_an_article_in_sibling_questions_does_not_mix_the_subjects() -> None:
    """Fix round 1 (CRITICAL 1, the sibling rule): the questions' "a" never strips "Model A"."""
    a_lab = make_target("topic-01-target-01", question="What did testers score Model A in a lab?",
                        measure="noise rating", unit_dimension="rating", period=None,
                        geography=None, organisation="Example Test Lab")
    b_lab = make_target("topic-01-target-02", question="What did testers score Model B in a lab?",
                        measure="noise rating", unit_dimension="rating", period=None,
                        geography=None, organisation="Example Test Lab")
    text = "In our tests Model A scored 4.5 out of 5 for noise."
    read = make_read(text, url="https://lab.example.test/model-a", title="Kettle tests")
    finding = make_finding(read, text,
                           figures=[figure("4.5", "out of 5", None, "actual").model_copy(
                               update={"subject": "Model A"})],
                           target_ids=[a_lab.target_id, b_lab.target_id])
    scored = verified(finding, FigureContext(period=None, scope=None, attribution="own",
                                             organisation="Example Test Lab", kind="actual",
                                             subject="Model A"))
    assert set(answered_target_ids([scored], [a_lab, b_lab])) == {a_lab.target_id}


# Task 5.6c: a comparison target names both options, and must not erase either subject.
COMPARISON_RATING = {**RATING, "question": ("How do the Kettle K1 and the Kettle K2 compare "
                                            "on the Example Tester noise rating for 2026?")}


def test_a_comparison_target_naming_both_products_keeps_them_apart() -> None:
    """Task 5.6c: the target that names both options must not erase either subject."""
    target = make_target(**COMPARISON_RATING)
    rows = fact_rows([_rated("Kettle K1", period="2026"),
                      _rated("Kettle K2", page="news", period="2026")], [target])
    assert [row.subject for row in rows] == ["Kettle K1", "Kettle K2"]
    assert all(not row.duplicate_finding_ids for row in rows)


def test_a_combined_target_naming_two_places_keeps_them_apart() -> None:
    """Task 5.6c: "Spain and Italy" in one target's question tells the two subjects apart."""
    target = make_target(question="How did Spain and Italy compare on unemployment in 2024?",
                         measure="unemployment rate", unit_dimension="percent", geography=None,
                         organisation="Example Statistical Agency")
    figures = [_unemployment_figure("Spain", "lab", [target]),
               _unemployment_figure("Italy", "news", [target])]
    rows = fact_rows(figures, [target])
    assert [row.subject for row in rows] == ["Spain", "Italy"]


def test_an_alias_the_target_never_names_still_matches() -> None:
    """Task 5.6c pin (ruling check 1): the target names "United States", never "US"."""
    target = make_target(question="What was battery storage capacity in the United States?")
    words = subject_context([target.target_id], [target])
    assert same_subject("US battery storage", "battery storage in the United States",
                        context_words=words)


def test_a_subject_that_restates_a_field_of_its_target_still_matches() -> None:
    """Task 5.6c: the target's own measure and geography are its topic, not two options.

    The committed e2e row ``single-subject-spellings`` pins this shape: three
    pages about widget adoption in the United States -- subject "United States",
    subject "widget adoption", and no subject at all -- are one fact, so the
    distinguishing rule stands down when each subject is a whole field of the
    target the two findings share.
    """
    target = make_target(question="What was widget adoption in the United States in 2025?",
                         measure="widget adoption", unit_dimension="percent", period="2025",
                         geography="United States")
    words = subject_context([target.target_id], [target])
    fields = _target_fields([target.target_id], [target])
    assert same_subject("United States", "widget adoption", context_words=words,
                        target_fields=fields)

    def survey(subject, page):
        text = "The institute measured widget adoption in the United States at 40 percent in 2025."
        read = make_read(text, url=f"https://{page}.example.test/adoption", title="Adoption survey")
        finding = make_finding(read, text,
                               figures=[figure("40", "percent", "2025", "actual").model_copy(
                                   update={"subject": subject})],
                               target_ids=[target.target_id])
        return verified(finding, FigureContext(period="2025", scope=None, attribution="own",
                                               organisation="Example Institute", kind="actual",
                                               subject=subject))

    rows = fact_rows([survey("United States", "agency21"),
                      survey("widget adoption", "bureau21"),
                      survey(None, "panel21")], [target])
    assert [(row.subject, len(row.duplicate_finding_ids)) for row in rows] == [("United States", 2)]


def _widget_figure(subject, page, target):
    """One 40 percent 2025 widget-adoption figure about ``subject``."""
    text = "The institute measured widget adoption in the United States at 40 percent in 2025."
    read = make_read(text, url=f"https://{page}.example.test/adoption", title="Adoption survey")
    finding = make_finding(read, text,
                           figures=[figure("40", "percent", "2025", "actual").model_copy(
                               update={"subject": subject})],
                           target_ids=[target.target_id])
    return verified(finding, FigureContext(period="2025", scope=None, attribution="own",
                                           organisation="Example Institute", kind="actual",
                                           subject=subject))


def test_a_subject_carrying_an_extra_question_word_stays_one_row() -> None:
    """Fix round 1 (Critical 1): the target's own fields decide, not a whole-subject match.

    "widget adoption rate" carries a word the measure does not state ("rate" is
    the target's own question word) and "United States" is its geography: the
    two name one topic, and BASE printed one row.
    """
    target = make_target(question="What was the widget adoption rate in the United States in 2025?",
                         measure="widget adoption", unit_dimension="percent", period="2025",
                         geography="United States")
    rows = fact_rows([_widget_figure("widget adoption rate", "lab", target),
                      _widget_figure("United States", "news", target)], [target])
    assert len(rows) == 1


def _grid_figure(subject, page, target):
    """One 10.4 GW 2024 capacity figure about ``subject`` (the live run's shape)."""
    text = "Grid-scale battery storage capacity added in the United States was 10.4 GW in 2024."
    read = make_read(text, url=f"https://{page}.example.test/capacity", title="Capacity report")
    finding = make_finding(read, text,
                           figures=[figure("10.4", "GW", "2024", "actual").model_copy(
                               update={"subject": subject})],
                           target_ids=[target.target_id])
    return verified(finding, FigureContext(period="2024", scope=None, attribution="own",
                                           organisation="Example Institute", kind="actual",
                                           subject=subject))


def test_a_benchmark_shaped_subject_keeps_every_spelling_in_one_row() -> None:
    """Fix round 1 (Critical 1): the live run's most important case.

    One benchmark-shaped target, one value, period, organisation and kind, under
    five spellings of one subject -- the place, its abbreviation, the place plus
    the measure, the measure alone, and no subject at all: one row, as at BASE.
    """
    target = make_target(question=("How much grid-scale battery storage capacity was added in "
                                   "the United States in 2024?"),
                         measure="grid-scale battery storage capacity added",
                         unit_dimension="power", period="2024", geography="United States")
    figures = [_grid_figure(subject, page, target) for subject, page in (
        ("United States", "agency21"), ("U.S.", "bureau21"),
        ("US grid-scale battery storage", "panel21"),
        ("grid-scale battery storage capacity", "survey21"), (None, "note21"))]
    rows = fact_rows(figures, [target])
    assert [(row.subject, len(row.duplicate_finding_ids)) for row in rows] == [("United States", 4)]


def test_an_article_only_give_away_needs_the_target_to_spell_it() -> None:
    """Fix round 1 (Minor 3): an article nobody wrote tells "a kettle" from "Kettle K2" nothing."""
    target = make_target(**COMPARISON_RATING)
    words = subject_context([target.target_id], [target])
    fields = _target_fields([target.target_id], [target])
    assert same_subject("a kettle", "Kettle K2", context_words=words, target_fields=fields)


# ---------------------------------------------------------------------------
# Task FF1 (final review, slice 1): the row a group prints, the figures the
# number parser cannot read, period spellings, and one value answering two
# measures.
# ---------------------------------------------------------------------------


def _kettle_page() -> str:
    return ("Our testers rated it 4.5 out of 5 in 2025. The Kettle K1 scored 4.5 out of 5 "
            "in 2025. The Kettle K2 scored 4.5 out of 5 in 2025.")


def _kettle_targets():
    return [
        make_target("topic-01-target-01", question="How did the Kettle K1 score?",
                    measure="kettle rating", unit_dimension=None, period="2025", kind="actual",
                    geography=None),
        make_target("topic-02-target-01", question="How did the Kettle K2 score?",
                    measure="kettle rating", unit_dimension=None, period="2025", kind="actual",
                    geography=None),
    ]


def test_a_group_with_a_named_subject_prints_that_subject() -> None:
    """Task FF1 (review I2): a subject-less figure and a named one can be one
    row -- the extraction order decides which joins which -- and the row must
    print the subject its group carries.

    A row with no subject beside a named one is read as a duplicate by the
    quality gate and hides which thing the figure is about, so the group's
    named member is also the row's own, and its obligations are the ones that
    member answers: the subject-less figure answers both targets because
    nothing can refuse it, and that binding must not answer the other model's
    obligation from a row about this one.
    """
    read = make_read(_kettle_page(), url="https://www.example.com/review", title="Kettle reviews")
    overall = make_finding(read, "Our testers rated it 4.5 out of 5 in 2025.",
                           figures=[figure("4.5", "out of 5", "2025", "actual")],
                           target_ids=["topic-01-target-01", "topic-02-target-01"])
    k1 = make_finding(read, "The Kettle K1 scored 4.5 out of 5 in 2025.",
                      figures=[figure("4.5", "out of 5", "2025", "actual").model_copy(
                          update={"subject": "Kettle K1"})],
                      target_ids=["topic-01-target-01"])
    k2 = make_finding(read, "The Kettle K2 scored 4.5 out of 5 in 2025.",
                      figures=[figure("4.5", "out of 5", "2025", "actual").model_copy(
                          update={"subject": "Kettle K2"})],
                      target_ids=["topic-02-target-01"])
    findings = [
        verified(overall, ctx(organisation="example.com", period="2025")),
        verified(k1, ctx(organisation="example.com", period="2025", subject="Kettle K1")),
        verified(k2, ctx(organisation="example.com", period="2025", subject="Kettle K2")),
    ]
    targets = _kettle_targets()

    rows = fact_rows(findings, targets)

    assert [(row.subject, row.target_ids) for row in rows] == [
        ("Kettle K1", ["topic-01-target-01"]),
        ("Kettle K2", ["topic-02-target-01"]),
    ]
    assert rows[0].finding_id == finding_fingerprint(k1)
    # The gate's own pair test over these rows: one row per model, no duplicate.
    assert not any(_rows_share_a_subject(a, b, targets)
                   for n, a in enumerate(rows) for b in rows[n + 1:])


def test_an_unreadable_figure_answers_only_its_own_dimensions_target() -> None:
    """Task FF1 (review I3): a currency symbol or a sign is not a number the
    parser reads, but the figure's unit still says what the figure is about.

    A price or a signed growth rate then answers the target it belongs to --
    the value text is on the page and verified -- while a figure whose unit is
    another dimension still answers nothing.
    """
    read = make_read("The K1 lists at $199 in 2025. Output fell -3.2 percent in 2025.")
    priced = make_finding(read, "The K1 lists at $199 in 2025.",
                          figures=[figure("$199", "USD", "2025", "actual")],
                          target_ids=["topic-01-target-01"])
    price_target = make_target("topic-01-target-01", measure="list price", unit_dimension=None,
                               period="2025", kind="actual", geography=None)

    [row] = fact_rows([verified(priced, ctx(organisation="example.com", period="2025"))],
                      [price_target])

    assert (row.target_ids, row.measure) == (["topic-01-target-01"], "list price")

    signed = make_finding(read, "Output fell -3.2 percent in 2025.",
                          figures=[figure("-3.2", "percent", "2025", "actual")],
                          target_ids=["topic-02-target-01"])
    kept = verified(signed, ctx(organisation="example.com", period="2025"))
    share = make_target("topic-02-target-01", measure="output growth",
                        unit_dimension="percent", period="2025", kind="actual", geography=None)
    power = make_target("topic-02-target-01", measure="storage additions",
                        unit_dimension="power", period="2025", kind="actual", geography=None)

    assert finding_answers(kept, share)
    assert not finding_answers(kept, power)


def test_period_spellings_of_one_fiscal_quarter_and_half_agree() -> None:
    """Task FF1 (review I4): a planner writes the question's own spelling and
    the page writes another; one period spelled two ways answers the same
    target, while a fiscal year stays distinct from a bare calendar year."""
    assert same_period("FY2025", "fiscal 2025")
    assert same_period("FY2025", "FY 2025")
    assert same_period("Q4 2025", "fourth quarter of 2025")
    assert same_period("H1 2025", "first half of 2025")

    assert not same_period("FY2025", "2025")
    assert not same_period("Q1 2025", "Q2 2025")
    assert not same_period("FY2025", "FY2024")


def test_two_figures_answering_different_targets_stay_two_rows() -> None:
    """Task FF1 (review I6): PD-9's fact key is the measure family -- the unit
    dimension plus the target the finding answers -- so two measures that
    happen to share one value are two rows, each answering its own obligation."""
    read = make_read("Participants lost 15 percent of body weight. Nausea affected 15 percent "
                     "of participants in the trial.",
                     url="https://www.journal.org/trial", title="Trial")
    targets = [
        make_target("topic-01-target-01", question="How much weight did participants lose?",
                    measure="mean body weight loss", unit_dimension="percent", period="2024",
                    kind="actual", geography=None),
        make_target("topic-02-target-01", question="What share had nausea?",
                    measure="nausea incidence", unit_dimension="percent", period="2024",
                    kind="actual", geography=None),
    ]
    findings = [
        verified(make_finding(read, "Participants lost 15 percent of body weight.",
                              figures=[figure("15", "percent", "2024", "actual")],
                              target_ids=["topic-01-target-01"]),
                 ctx(organisation="journal.org", period="2024")),
        verified(make_finding(read, "Nausea affected 15 percent of participants in the trial.",
                              figures=[figure("15", "percent", "2024", "actual")],
                              target_ids=["topic-02-target-01"]),
                 ctx(organisation="journal.org", period="2024")),
    ]

    rows = fact_rows(findings, targets)

    assert [(row.measure, row.target_ids) for row in rows] == [
        ("mean body weight loss", ["topic-01-target-01"]),
        ("nausea incidence", ["topic-02-target-01"]),
    ]


def test_a_comparison_base_is_never_resolved_as_the_figures_period() -> None:
    """Task FF1 (review P2-1): "over last year" names the base a figure is
    compared with, not the period the figure applies to, so no period is
    resolved from it -- while a plain relative phrase still resolves."""
    assert resolve_relative_period("Sales rose 12 percent over last year", "2026-02-20") is None
    assert resolve_relative_period("Sales were 12 percent higher than last year",
                                    "2026-02-20") is None
    assert resolve_relative_period("Sales are up from last year", "2026-02-20") is None
    assert resolve_relative_period("Revenue grew 12 percent compared with last year",
                                    "2026-02-20") is None
    assert resolve_relative_period("Sales rose 12 percent this year", "2026-02-20") == "2026"


# ---------------------------------------------------------------------------
# The live pre-flight's Defect B (review-02) and Defect A's label end to end.
# ---------------------------------------------------------------------------

PREFLIGHT_MEASURE = "projected grid-scale battery storage capacity additions"


def _battery_forecast(subject: str | None, value: str, url: str):
    text = f"Additions of {value} GW are projected for 2025."
    read = make_read(text, url=url, title="Storage outlook")
    return verified(
        make_finding(read, text, figures=[figure(value, "GW", "2025", "forecast").model_copy(
            update={"subject": subject})], target_ids=["topic-02-target-01"]),
        ctx(organisation="EIA", period="2025", kind="forecast", subject=subject),
    )


def test_a_figure_whose_subject_is_another_technology_answers_no_battery_target() -> None:
    """Defect B (review-02): the pre-flight printed the EIA's 2025 forecasts for
    32.5 GW of utility-scale solar, 7.7 GW of wind and 4.4 GW of natural gas
    under the measure "projected grid-scale battery storage capacity additions".

    A figure that names its own subject answers a target only when that subject
    shares something distinctive with the target's measure, or names nothing the
    target does not already state itself -- while a battery-storage figure and a
    subject-less one answer as before.
    """
    target = make_target("topic-02-target-01", question=(
        "How much grid-scale battery storage capacity is projected to be added in 2025?"),
        measure=PREFLIGHT_MEASURE, unit_dimension="power", period="2025", kind="forecast",
        geography=None, organisation="EIA")

    for subject in ("utility-scale solar capacity", "wind power",
                    "fossil fuel natural gas capacity"):
        assert not finding_answers(
            _battery_forecast(subject, "32.5", f"https://www.eia.gov/{subject.split()[0]}"),
            target), subject

    assert finding_answers(
        _battery_forecast("battery storage", "30.0", "https://www.eia.gov/battery"), target)
    assert finding_answers(_battery_forecast(None, "18.2", "https://www.eia.gov/none"), target)
    assert finding_answers(
        _battery_forecast("grid-scale battery storage", "19.6", "https://www.eia.gov/same"),
        target)

    # A subject that only restates the target's own words is not a rival claim
    # about something else: the D11 sibling row's "Spain" and the comparison
    # row's "Kettle K1" keep answering, which is what ``single-subject-spellings``
    # and ``two-subjects-one-value`` measure.
    spanish = make_target("topic-03-target-01",
                          question="How much battery storage capacity was added in Spain in 2025?",
                          measure="battery storage capacity added", unit_dimension="power",
                          period="2025", kind="forecast", geography="Spain", organisation=None)
    assert finding_answers(
        _battery_forecast("Spain", "2.1", "https://www.eia.gov/spain").model_copy(
            update={"target_ids": ["topic-03-target-01"]}),
        spanish)


def test_the_row_label_agrees_with_the_pages_own_attribution() -> None:
    """Defect A end to end (review-01): the Context Check answered that the
    source does not attribute the figure, the writer then quoted the page's own
    sentence, and the reviewer read the two as a contradiction.

    With the credit read back out of the words the check itself quoted, the
    row's label says exactly what the page says: relayed by the site that
    carried it, from the U.S. Energy Information Administration.
    """
    words = (
        "U.S. developers and power plant owners plan to significantly increase utility-scale "
        "battery storage over the next three years, reaching 30 GW by the end of 2025, based on "
        "the latest reporting from the U.S. Energy Information Administration (EIA)."
    )
    url = ("https://www.power-eng.com/energy-storage/batteries/"
           "eia-utility-scale-battery-storage-capacity-to-reach-30-gw-by-2026")
    read = make_read(words, url=url,
                     title="EIA: utility-scale battery storage capacity to reach 30 GW by 2026")
    finding = make_finding(read, words,
                           figures=[figure("30", "GW", "2025", "forecast").model_copy(
                               update={"subject": "utility-scale battery storage"})],
                           target_ids=["topic-02-target-01"])
    item = ContextItem(label="F12", finding=finding, read=read, passage=words,
                       match=figure_match(finding, {read.read_id: read}))
    judgement = verify_finding(item, {1: FigureCheckDraft(
        finding="F12", figure=1, period="2025", scope=None, subject="utility-scale battery storage",
        attribution="unattributed", organisation=None, kind="forecast",
        evidence_words=words, verdict="confirm", reason="The source does not attribute it.",
    )})
    judged = finding.model_copy(update={"verification": judgement})
    target = make_target("topic-02-target-01", question=(
        "How much grid-scale battery storage capacity is projected to be added in 2025?"),
        measure=PREFLIGHT_MEASURE, unit_dimension="power", period="2025", kind="forecast",
        geography=None, organisation="EIA")

    [row] = fact_rows([judged], [target])

    assert (row.attribution, row.organisation, row.relay_host) == (
        "relayed", "U.S. Energy Information Administration", "power-eng.com")


# ---------------------------------------------------------------------------
# Round 3, Defect A: a period the words state in another spelling.
# ---------------------------------------------------------------------------


def test_a_two_digit_year_reads_as_its_four_digit_year() -> None:
    """Round 3 (pre-flight run 3): the page dates its figure "at the end of
    Q1'25", and "Q1 2025" has to read as the same period — an apostrophe or a
    period abbreviation attaches a two-digit year, which folds by the usual
    pivot (00-49 is 20xx, 50-99 is 19xx). A bare two-digit number is not a year.
    """
    assert same_period("Q1 2025", "Q1\u201925")
    assert same_period("Q1 2025", "Q1'25")
    assert same_period("fiscal 2025", "FY25")
    assert same_period("fiscal 2025", "FY\u201925")
    assert same_period("H1 2025", "H1\u201925")
    assert same_period("Q4 1998", "Q4'98")

    assert not same_period("Q1 2025", "Q2\u201925")
    assert not same_period("fiscal 2025", "2025")
    assert not same_period("Q1 2025", "Q1 25")


def test_the_words_state_a_period_however_they_spell_it() -> None:
    """The words state a period when their folded tokens carry its key as one run."""
    words = ("The report says domestic storage capacity will rise from about 28 GW at the end "
             "of Q1\u201925 to 64.9 GW at the end of 2026.")

    assert _period_stated_in(words, "Q1 2025")
    assert _period_stated_in(words, "quarter 1 2025")
    assert _period_stated_in(words, "2026")

    assert not _period_stated_in(words, "Q2 2025")
    assert not _period_stated_in(words, "fiscal 2025")
    assert not _period_stated_in(words, None)


def test_a_bracketed_unit_answers_the_target_it_belongs_to() -> None:
    """Round 3 (pre-flight run 3): the Key facts row read `26 gigawatts (GW)`
    under the measure "stated figure", because the bracketed unit had no
    dimension for the target's own dimension to match."""
    text = ("Cumulative utility-scale battery storage capacity exceeded 26 gigawatts (GW) "
            "in 2024.")
    read = make_read(text, url="https://eia.gov/todayinenergy/detail.php?id=64705")
    finding = make_finding(read, text,
                           figures=[figure("26", "gigawatts (GW)", "2024", "actual")],
                           target_ids=["topic-01-target-01"])
    target = make_target("topic-01-target-01", question=(
        "How much utility-scale battery storage capacity was there in 2024?"),
        measure="utility-scale battery storage capacity", unit_dimension="power", period="2024",
        kind="actual", geography=None, organisation=None)

    [row] = fact_rows([verified(finding, ctx(organisation="eia.gov", period="2024"))], [target])

    assert row.measure == "utility-scale battery storage capacity"
    assert row.target_ids == ["topic-01-target-01"]


# ---------------------------------------------------------------------------
# Round 4, part 1: the review's findings on the two-digit-year fold, the range
# continuation, and the bracketed unit's qualifier.
# ---------------------------------------------------------------------------


def test_a_two_digit_year_needs_an_apostrophe_or_fy() -> None:
    """RevFF1r3's Important 1: only "'" and "FY" attach a two-digit year. A
    part number that happens to look like one ("H20 chips", "H100") is not a
    year, and neither is a bare "Q25"."""
    assert not same_period("half 2020", "H20")
    assert not same_period("half 1 2000", "H100")
    assert not same_period("half 2012", "H12")
    assert not same_period("2025", "Q25")

    # The spellings the rule does read keep working.
    assert same_period("Q1 2025", "Q1\u201925")
    assert same_period("H1 2025", "H1'25")
    assert same_period("fiscal 2025", "FY25")


def test_a_period_the_words_state_as_a_range_is_not_stated() -> None:
    """RevFF1r3's Important 2: "FY2024-25" and "FY25/26" are ranges, and the
    year a range *starts* in is not the period it states."""
    assert not _period_stated_in("India added 18 GW in FY2024-25, the ministry said.",
                                 "fiscal 2024")
    assert not _period_stated_in("India added 18 GW in FY24-25, the ministry said.",
                                 "fiscal 2024")
    assert not _period_stated_in("India added 18 GW in FY25/26, the ministry said.",
                                 "fiscal 2025")
    # The range itself, and a year that stands alone, are still stated.
    assert _period_stated_in("India added 18 GW in FY2024-25, the ministry said.",
                             "fiscal 2024 25")
    assert _period_stated_in("India added 18 GW in FY2024, the ministry said.", "fiscal 2024")
    assert _period_stated_in("India added 18 GW in 2024, the ministry said.", "2024")


# ---------------------------------------------------------------------------
# Round 4, part 2: Defect C (a qualitative target's organisation) and Defect D
# (a leading article, and an all-capitals first word).
# ---------------------------------------------------------------------------

TEXT_TARGET = dict(question="When does the new title enter closed beta?",
                   measure="beta date", unit_dimension=None, period=None, kind=None,
                   geography=None)


def _text_finding(url: str = "https://games-studio.test/news/beta",
                  target_id: str = "topic-01-target-01", **fields):
    text = "The new title enters closed beta next month, the studio said."
    read = make_read(text, url=url, title="Beta news")
    return verified(make_finding(read, text, target_ids=[target_id], **fields))


def test_an_own_site_text_finding_answers_a_qualitative_target() -> None:
    """Defect C (the controller's probe): a target with no unit dimension takes
    its organisation from the plan, and a host name is not evidence of who a page
    speaks for -- playvalorant.com is Riot Games's, github.blog is GitHub's --
    so an organisation its host does not spell must not refuse the page's own
    bound, citable finding. The figure test below is the bound: a *figure*
    target keeps the organisation gate."""
    target = make_target("topic-01-target-01", organisation="Example Games", **TEXT_TARGET)

    assert finding_answers(_text_finding(), target)
    assert set(answered_target_ids([_text_finding()], [target])) == {target.target_id}

    competitor = make_target("topic-01-target-02", organisation="Example Games",
                             measure="storage additions", unit_dimension="power", period="2024",
                             kind="actual", geography=None)
    facility = verified(
        make_finding(make_read(), "Generators added 10.4 gigawatts (GW) of new battery storage "
                                  "capacity in 2024,",
                     figures=[figure("10.4", "GW", "2024", "actual")],
                     target_ids=[competitor.target_id]),
        ctx(organisation="Some Other Body", period="2024"))

    assert not finding_answers(facility, competitor)


def test_a_text_finding_that_relays_another_body_answers_only_that_bodys_targets() -> None:
    """The rule's one gate: a page that states the finding as another body's
    answers that body's obligations, not the plan's other organisations."""
    relayed = _text_finding(attributed_issuer="Example Research")
    other_body = _text_finding(attributed_issuer="Example Games")

    assert not finding_answers(relayed, make_target("topic-01-target-01",
                                                    organisation="Example Games", **TEXT_TARGET))
    assert finding_answers(other_body, make_target("topic-01-target-01",
                                                   organisation="Example Games", **TEXT_TARGET))
    assert not finding_answers(relayed, make_target("topic-01-target-01",
                                                    organisation="Example Games Ltd",
                                                    **{**TEXT_TARGET, "measure": "beta date"}))
    # The body the page credits is the only one its obligation answers.
    assert finding_answers(relayed, make_target("topic-01-target-01",
                                                organisation="Example Research", **TEXT_TARGET))


def test_not_found_does_not_list_a_qualitative_target_its_bound_finding_answers() -> None:
    """The defect's visible effect: a required text target whose finding is bound
    and verified was reported "Not found" against its own evidence."""
    target = make_target("topic-02-target-01", organisation="Example Games", **TEXT_TARGET)
    topic = SubTopic(coverage_id="topic-02", title="Beta date", rationale="r",
                     search_queries=["closed beta date"], success_criteria=["c"], priority=1,
                     evidence_targets=[target])
    acquisition = {"topic-02": AcquisitionState(read_urls=["https://games-studio.test/news/beta"])}
    answered = answered_target_ids([_text_finding(target_id=target.target_id)], [target])

    assert not_found_targets([topic], answered, acquisition) == []
    # The same target without a bound finding is still listed.
    assert [row.target_id for row in not_found_targets([topic], {}, acquisition)] == [
        "topic-02-target-01"]


@pytest.mark.parametrize(("left", "right"), [
    ("the IPCC", "IPCC"),
    ("the IPCC", "Intergovernmental Panel on Climate Change"),
    ("IPCC", "the Intergovernmental Panel on Climate Change"),
    ("the EIA", "U.S. Energy Information Administration"),
])
def test_a_leading_article_never_blocks_a_match(left, right) -> None:
    """Defect D (i): a leading article is the page's grammar, not part of the
    name.

    The all-capitals-first-word rule is no longer part of this matcher
    (RevFF1p2's D-1): it lives in ``_answers_organisation``, which only the
    answering paths read, and
    ``test_an_acronym_leads_its_own_name_when_the_obligation_is_answered``
    covers it where it belongs.
    """
    assert same_organisation(left, right)
    assert same_organisation(right, left)


@pytest.mark.parametrize(("left", "right"), [
    ("Energy", "Energy Information Administration"),
    ("Wood", "Wood Mackenzie"),
    ("Tiobe", "TIOBE Software"),
    ("EIA", "eia.news"),
    ("energy.gov", "U.S. Energy Information Administration"),
])
def test_a_title_case_first_word_never_stands_for_the_rest(left, right) -> None:
    """The bound both matchers keep: only an all-capitals token may stand for the
    rest of a name.

    ``same_organisation`` never reads a first word as the whole name at all, and
    ``_answers_organisation`` -- the one predicate that reads an all-capitals
    token that way (RevFF1p2's D-1) -- refuses a Title Case spelling just the
    same, so a plan's "Energy Information Administration" is never answered by a
    page whose own name is "Energy" or "Tiobe".
    """
    assert not same_organisation(left, right)
    assert not same_organisation(right, left)
    assert not _answers_organisation(left, right)
    assert not _answers_organisation(right, left)


# ---------------------------------------------------------------------------
# Round 5: RevFF1p2's C-1, D-1 and D-2, and ReRevFF1p1's N1.
# ---------------------------------------------------------------------------


def test_a_relayed_figure_does_not_answer_a_qualitative_target() -> None:
    """RevFF1p2's C-1: a figure the Context Check read as another body's relay
    credits that body, so the finding answers that body's obligation and not the
    target's other organisation."""
    text = "Additions reached 15 GW in 2025, the firm said."
    read = make_read(text, url="https://storage-news.test/x", title="Storage")
    finding = verified(
        make_finding(read, text, figures=[figure("15", "GW", "2025", "forecast")],
                     target_ids=["topic-01-target-01"]),
        ctx(attribution="relayed", organisation="Wood Mackenzie", period="2025", kind="forecast"))
    target = make_target("topic-01-target-01", organisation="U.S. Energy Information Administration",
                         **TEXT_TARGET)
    own = make_target("topic-01-target-01", organisation="Wood Mackenzie", **TEXT_TARGET)

    assert not finding_answers(finding, target)
    assert finding_answers(finding, own)
    # A near miss the legal-form fold does not cover is still another body (F4
    # makes "Wood Mackenzie Inc" the same organisation as "Wood Mackenzie", so
    # that spelling is no longer the negative case).
    assert not finding_answers(finding, make_target("topic-01-target-01",
                                                   organisation="BloombergNEF",
                                                   **{**TEXT_TARGET, "measure": "storage added"}))


def test_two_bodies_whose_names_share_an_acronym_stay_two_rows() -> None:
    """RevFF1p2's D-1: the acronym rule is answering-only, so the IEA's figure
    and IEA PVPS's are two facts -- never one row with the other printed as its
    earlier edition, which would hide their disagreement as a revision."""
    iea = verified(
        make_finding(make_read(), "Generators added 553 GW of capacity in 2024,",
                     figures=[figure("553", "GW", "2024", "actual")],
                     target_ids=["topic-01-target-01"], release_date="2025-01-15"),
        ctx(organisation="IEA", period="2024"))
    pvps = verified(
        make_finding(make_read(), "Installations reached 600 GW of capacity in 2024,",
                     figures=[figure("600", "GW", "2024", "actual")],
                     target_ids=["topic-01-target-01"], release_date="2025-04-10"),
        ctx(organisation="IEA PVPS", period="2024"))

    rows = fact_rows([iea, pvps], [make_target()])

    assert [(row.organisation, row.value) for row in rows] == [("IEA", "553 GW"),
                                                              ("IEA PVPS", "600 GW")]
    assert all(not row.earlier for row in rows)


def test_an_acronym_leads_its_own_name_when_the_obligation_is_answered() -> None:
    """RevFF1p2's D-2 fix: the acronym rule survives where it belongs -- a figure
    whose organisation is the all-capitals first word of the target's does answer
    it -- and a programme is not the body whose acronym leads it."""
    read = make_read()
    rated = verified(
        make_finding(read, "The index rated it 4.5 out of 5 in 2024.",
                     figures=[figure("4.5", "out of 5", "2024", "actual")],
                     target_ids=["topic-01-target-01"]),
        ctx(organisation="TIOBE", period="2024"))
    programme = verified(
        make_finding(read, "The programme counted 600 GW of capacity in 2024,",
                     figures=[figure("600", "GW", "2024", "actual")],
                     target_ids=["topic-01-target-01"]),
        ctx(organisation="IEA PVPS", period="2024"))

    def ranked(organisation: str):
        return make_target("topic-01-target-01", question="How was it rated in 2024?",
                           measure="ranking score", unit_dimension="rating", period="2024",
                           kind="actual", geography=None, organisation=organisation)

    def capacity(organisation: str):
        return make_target("topic-01-target-01", question="How much capacity in 2024?",
                           measure="capacity added", unit_dimension="power", period="2024",
                           kind="actual", geography=None, organisation=organisation)

    assert finding_answers(rated, ranked("TIOBE Software"))
    assert not finding_answers(rated, ranked("Tiobe Software"))
    assert finding_answers(programme, capacity("IEA PVPS"))
    assert not finding_answers(programme, capacity("IEA"))
    assert not finding_answers(programme, capacity("IEA Wind TCP"))


def test_a_date_that_is_an_iso_date_states_its_year() -> None:
    """ReRevFF1p1's N1: the range guard reads a *year span*, so the year part of
    an ISO date is stated while a fiscal range still is not."""
    assert _period_stated_in("Solar capacity was 18 GW, per the report published 2024-01-15.",
                             "calendar 2024")
    assert _period_stated_in("Additions reached 18 GW in 2024-25.", "2024") is False
    assert _period_stated_in("Additions reached 18 GW in 2024\u201325.", "2024") is False


# ---------------------------------------------------------------------------
# Round 6: ReRevFF1r5's finding 1 -- a multi-year span's start year.
# ---------------------------------------------------------------------------


def test_a_multi_year_span_does_not_state_its_start_year() -> None:
    """ReRevFF1r5's finding 1: the span and the date readings are separate, so a
    three-year span states no year while a date's own year is stated."""
    for text in ("Additions reached 18 GW in 2024-25/26.",
                 "Additions reached 18 GW in 2024-25-26.",
                 "Additions reached 18 GW in 2024\u201325/26.",
                 "Additions reached 18 GW in 2024-25."):
        assert not _period_stated_in(text, "2024"), text

    for text in ("Solar capacity was 18 GW, per the report published 2024-01-05.",
                 "Solar capacity was 18 GW, per the report published 2024-1-5.",
                 "Solar capacity was 18 GW, per the report published 2024/01/15."):
        assert _period_stated_in(text, "calendar 2024"), text


# ---------------------------------------------------------------------------
# Round 7: the expert final review's F3, F4 and F8.
# ---------------------------------------------------------------------------


def test_targets_told_apart_only_by_words_no_subject_can_carry_defer_to_the_binding() -> None:
    """F3 (final review, smoke 2): two sibling price targets differ only by the
    publisher they cite ("the model RTINGS ranks highest" against "the model
    Wirecutter ranks highest"), words a product's subject cannot carry, so the
    sibling rule cannot decide and the extraction's own binding answers -- the
    finding is bound to the first and only the second says Not found."""
    def price_target(target_id: str, publisher: str):
        return make_target(target_id, question=(
            f"What is the current list price of the model {publisher} ranks highest?"),
            measure="current list price", unit_dimension="currency", period=None,
            kind="actual", geography=None, organisation="Roborock")

    rtings = price_target("topic-01-target-01", "RTINGS")
    wirecutter = price_target("topic-01-target-02", "Wirecutter")
    text = "The Roborock Saros Z70 lists at 1,600 USD."
    finding = verified(
        make_finding(make_read(text, url="https://www.roborock.com/x", title="Store"),
                     text, figures=[figure("1,600", "USD", None, "actual").model_copy(
                         update={"subject": "Roborock Saros Z70"})],
                     target_ids=[rtings.target_id]),
        ctx(organisation="Roborock", period=None, subject="Roborock Saros Z70"))

    assert finding_answers(finding, rtings, plan_targets=[rtings, wirecutter])
    assert not finding_answers(finding, wirecutter, plan_targets=[rtings, wirecutter])
    # Without the sibling the rule never engages, as before.
    assert finding_answers(finding, rtings, plan_targets=[rtings])


@pytest.mark.parametrize(("left", "right"), [
    ("Apple Inc.", "Apple"),
    ("Apple Inc.", "apple.com"),
    ("Sony Corporation", "Sony"),
    ("Riot Games, Inc.", "Riot Games"),
    ("Novo Nordisk A/S", "Novo Nordisk"),
    ("Example Lab Ltd", "Example Lab"),
])
def test_a_legal_form_suffix_never_splits_one_organisation(left, right) -> None:
    """F4 (final review): a legal form is not part of the name, so a filing's
    "Apple Inc." and a newsroom's "Apple" are one organisation -- in either
    direction, and against the organisation's own host."""
    assert same_organisation(left, right)
    assert same_organisation(right, left)


@pytest.mark.parametrize(("left", "right"), [
    ("IEA PVPS", "IEA"),
    ("Energy", "Energy Information Administration"),
    ("EIA-923", "EIA"),
    ("UN Women", "UN"),
    ("Department of Energy", "eia.gov"),
    ("Samsung Electronics", "Samsung"),
    ("Ford Motor Company", "Ford"),
])
def test_the_legal_form_fold_keeps_every_guard(left, right) -> None:
    """The bound on F4: dropping a legal form is not dropping the words that tell
    two organisations apart."""
    assert not same_organisation(left, right)
    assert not same_organisation(right, left)


def test_a_period_states_the_same_words_in_any_order() -> None:
    """F8 (final review): "Q3 2025" and "2025 Q3" are one period, while a month
    with its year is not the year alone."""
    assert same_period("Q3 2025", "2025 Q3")
    assert same_period("2025 Q3", "third quarter of 2025")
    assert same_period("H1 2026", "first half of 2026")

    assert not same_period("March 2026", "2026")
    assert not same_period("Q3 2025", "Q4 2025")
    assert not same_period("Q1 2025", "Q2 2025")


# ---------------------------------------------------------------------------
# Round 8: ReRevF2F4's N1 (the deferral's bounds) and N2 (legal forms).
# ---------------------------------------------------------------------------


def _sibling_price_targets(first: str, second: str, measure: str = "current list price",
                           organisation: str | None = "Roborock"):
    """Two sibling targets differing only in their own distinguishing words."""
    return [make_target("topic-01-target-01", question=f"What is the {measure} of {first}?",
                        measure=measure, unit_dimension="currency", period=None,
                        kind="actual", geography=None, organisation=organisation),
            make_target("topic-01-target-02", question=f"What is the {measure} of {second}?",
                        measure=measure, unit_dimension="currency", period=None,
                        kind="actual", geography=None, organisation=organisation)]


def _price_finding(target_ids, subject: str, organisation: str = "Roborock"):
    text = f"The {subject} lists at 1,600 USD."
    return verified(
        make_finding(make_read(text, url="https://www.roborock.com/x", title="Store"), text,
                     figures=[figure("1,600", "USD", None, "actual").model_copy(
                         update={"subject": subject})],
                     target_ids=list(target_ids)),
        ctx(organisation=organisation, period=None, subject=subject))


def test_an_item_family_never_defers_to_the_binding() -> None:
    """N1: siblings told apart by item identifiers ("Kettle K1" against "Kettle
    K2") are a family the subject is expected to state, so a figure about a third
    item is refused rather than deferred to the extraction's binding."""
    t1, t2 = _sibling_price_targets("the Kettle K1", "the Kettle K2")

    assert not finding_answers(_price_finding(["topic-01-target-01"], "Kettle K3"), t1,
                               plan_targets=[t1, t2])


def test_a_finding_bound_to_both_referent_siblings_answers_neither() -> None:
    """N1: the deferral needs exactly one binding -- a finding bound to both
    siblings cannot be the reason either one is answered."""
    t1, t2 = _sibling_price_targets("the model RTINGS ranks highest",
                                    "the model Wirecutter ranks highest")
    both = _price_finding(["topic-01-target-01", "topic-01-target-02"], "Roborock Saros Z70")

    assert not finding_answers(both, t1, plan_targets=[t1, t2])
    assert not finding_answers(both, t2, plan_targets=[t1, t2])
    assert answered_target_ids([both], [t1, t2]) == {}


def test_a_referent_only_sibling_pair_still_defers_to_its_single_binding() -> None:
    """The shape N1 keeps: the distinguishing words are two publishers no
    product's subject carries, and the finding binds exactly one target."""
    t1, t2 = _sibling_price_targets("the model RTINGS ranks highest",
                                    "the model Wirecutter ranks highest")
    bound_to_first = _price_finding(["topic-01-target-01"], "Roborock Saros Z70")

    assert finding_answers(bound_to_first, t1, plan_targets=[t1, t2])
    assert not finding_answers(bound_to_first, t2, plan_targets=[t1, t2])


def test_two_registrations_of_one_brand_stay_two_rows() -> None:
    """N2: "Siemens AG" and "Siemens SA" are different legal entities, so one
    entity's figure is never printed as the other's earlier edition."""
    assert not same_organisation("Siemens AG", "Siemens SA")
    assert not same_organisation("TotalEnergies SE", "TotalEnergies SA")
    assert not same_organisation("Sony Corporation", "Sony AB")
    assert not same_organisation("Nokia Oyj", "Nokia OY")

    def siemens(form: str, value: str, release: str):
        text = f"Siemens revenue was {value} in 2024."
        return verified(
            make_finding(make_read(text, url=f"https://siemens.example/{form}", title="Report"),
                         text, figures=[figure(value, "USD", "2024", "actual")],
                         target_ids=["topic-01-target-01"], release_date=release),
            ctx(organisation=f"Siemens {form}", period="2024"))

    rows = fact_rows([siemens("AG", "1", "2025-01-01"), siemens("SA", "2", "2025-02-01")],
                     [make_target("topic-01-target-01", measure="revenue",
                                  unit_dimension="currency", period="2024", kind="actual",
                                  geography=None, organisation=None)])

    assert [(row.organisation, row.value) for row in rows] == [("Siemens AG", "1 USD"),
                                                              ("Siemens SA", "2 USD")]
    assert all(not row.earlier for row in rows)


def test_a_name_without_a_legal_form_still_matches_its_formed_spelling() -> None:
    """The bound on N2: refusing two *different* forms must not refuse a name
    that writes none at all."""
    assert same_organisation("Apple", "Apple Inc.")
    assert same_organisation("Siemens", "Siemens AG")
    assert same_organisation("Siemens AG", "Siemens AG")
    assert same_organisation("Novo Nordisk", "Novo Nordisk A/S")


# ---------------------------------------------------------------------------
# Improvement 1A: the sub-topic fallback types.Finding.target_ids promises
# ---------------------------------------------------------------------------
#
# The live run this wave answers extracted every finding with no binding at all
# -- its fifteen figures, all of them dates, carried an empty ``target_ids`` --
# so the coverage gate declared two obligations the report's own pages answer
# "Not found". The contract on ``Finding.target_ids`` already promises the way
# out: a finding "with no planned target left is kept but can then be
# attributed only through the sub-topic that fetched its read".

WHEN_TITLE = "The dates the obligations start applying and the transition for items already placed"


def when_targets() -> list[EvidenceTarget]:
    """The run's two 'when' obligations: qualitative, required, one sub-topic."""
    return [
        make_target("topic-03-target-01", question="From what date do the obligations apply?",
                    measure="application date", unit_dimension=None, period=None, kind=None,
                    geography=None),
        make_target("topic-03-target-02",
                    question="By what date must an item already placed comply?",
                    measure="compliance date", unit_dimension=None, period=None, kind=None,
                    geography=None),
    ]


def when_sub_topics() -> list[SubTopic]:
    return [SubTopic(coverage_id="topic-03", title=WHEN_TITLE, rationale="the run's third",
                     search_queries=["q"], success_criteria=["c"], priority=1,
                     evidence_targets=when_targets())]


def dated_finding(*, sub_topic: str = WHEN_TITLE, target_ids: Sequence[str] = (),
                  value: str = "2 August 2025", **fields):
    """One unbound finding of the third sub-topic, on the shape the run produced."""
    text = f"The obligations apply from {value}."
    read = make_read(text, url="https://example-relay.example/law/12",
                     title="Article 12: Registration | Example Act | Example Relay")
    finding = make_finding(read, text, figures=[figure(value, "date", None, "actual")],
                           target_ids=target_ids, **fields)
    finding = finding.model_copy(update={"related_sub_topic": sub_topic})
    return verified(finding, ctx(organisation="Example Relay", attribution="unattributed",
                                 period=None))


def test_an_unbound_finding_answers_its_own_sub_topics_targets() -> None:
    """1A, on the run's shape: unbound findings of the third sub-topic answer both
    of its required targets, so the report stops declaring them Not found."""
    topics, targets = when_sub_topics(), when_targets()
    findings = [dated_finding(), dated_finding(value="2 August 2027")]

    # Before the fallback is handed the plan, every target stays unanswered --
    # which is exactly what the run's own gate reported.
    assert answered_target_ids(findings, targets) == {}
    assert [row.target_id for row in not_found_targets(topics, {}, {})] == [
        "topic-03-target-01", "topic-03-target-02"]

    answered = answered_target_ids(findings, targets, sub_topics=topics)
    assert set(answered) == {"topic-03-target-01", "topic-03-target-02"}
    assert sorted(answered["topic-03-target-01"]) == sorted(
        finding_fingerprint(f) for f in findings)
    assert not_found_targets(topics, answered, {}) == []


def test_the_fallback_answers_only_the_findings_own_sub_topic() -> None:
    topics = when_sub_topics()
    other = dated_finding(sub_topic="Battery storage")
    assert not any(finding_answers(other, target, sub_topics=topics)
                   for target in when_targets())
    # A finding whose sub-topic matches by title only differs in the coverage id
    # it resolves to, so it too answers nothing.
    renamed = SubTopic(coverage_id="topic-01", title=WHEN_TITLE, rationale="r",
                       search_queries=["q"], success_criteria=["c"], priority=1)
    assert not any(finding_answers(dated_finding(), target, sub_topics=[renamed])
                   for target in when_targets())


def test_a_finding_bound_to_another_target_never_answers_by_fallback() -> None:
    """The bound on 1A: an extraction that named a target answers only what it named."""
    topics = when_sub_topics()
    elsewhere = make_target("topic-01-target-01", unit_dimension=None, period=None,
                            kind=None, geography=None)
    finding = dated_finding(target_ids=["topic-01-target-01"])

    assert finding_answers(finding, elsewhere, sub_topics=topics)
    assert not any(finding_answers(finding, target, sub_topics=topics)
                   for target in when_targets())
    assert answered_target_ids([finding], [*when_targets(), elsewhere],
                               sub_topics=topics) == {
        "topic-01-target-01": [finding_fingerprint(finding)]}


def test_a_figure_target_is_answered_by_its_fields_not_by_the_sub_topic() -> None:
    """For a figure target the existing figure rules still decide (1A)."""
    topics = when_sub_topics()
    target = make_target("topic-03-target-01", measure="capacity added",
                         unit_dimension="power", period="2024", kind="actual", geography=None)
    text = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024."
    read = make_read(text)
    unbound = make_finding(read, text, figures=[figure("10.4", "GW", "2024", "actual")],
                           target_ids=()).model_copy(update={"related_sub_topic": WHEN_TITLE})
    fitting = verified(unbound, ctx(organisation="Example Lab", attribution="own", period="2024"))
    wrong_period = verified(
        unbound.model_copy(update={"figures": [figure("10.4", "GW", "2025", "actual")]}),
        ctx(organisation="Example Lab", attribution="own", period="2025"))

    assert finding_answers(fitting, target, sub_topics=topics)
    assert not finding_answers(wrong_period, target, sub_topics=topics)


def test_a_fallback_answer_reaches_the_row_it_builds() -> None:
    """The row a writer cites carries the obligation the fallback answered."""
    topics, targets = when_sub_topics(), when_targets()
    rows = fact_rows([dated_finding()], targets, sub_topics=topics)
    assert [row.target_ids for row in rows] == [
        ["topic-03-target-01", "topic-03-target-02"]]
    assert fact_rows([dated_finding()], targets)[0].target_ids == []
