"""Spec §5.3, §6.4 and §6.6: facts are read from verified fields, never from prose."""

from __future__ import annotations

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.verified_facts import (
    answered_target_ids,
    fact_rows,
    finding_answers,
    not_found_targets,
    release_key,
    resolve_relative_period,
    same_organisation,
    same_period,
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


def ctx(organisation=EIA, attribution="own", period="2024", kind="actual", scope=None):
    return FigureContext(period=period, scope=scope, attribution=attribution,
                         organisation=organisation, kind=kind)


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
