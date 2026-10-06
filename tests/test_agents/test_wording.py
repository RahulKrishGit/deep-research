"""The wording rules the Evidence Verifier and the fact rows share."""

from __future__ import annotations

from deep_research.agents.wording import stated_role, stated_scopes


def test_stated_role_reads_a_forecast_an_actual_and_a_mixture() -> None:
    assert stated_role("16 GW was installed in 2025") == "actual"
    assert stated_role("EIA forecast that 18.2 GW would be added in 2025") == "forecast"
    assert (
        stated_role("the operator beat projections: 16 GW was installed in 2025")
        == "mixed"
    )


def test_a_verb_after_to_be_is_not_an_outcome() -> None:
    assert stated_role("18.2 GW is expected to be added in 2025") == "forecast"


def test_scopes_are_read_from_the_text() -> None:
    assert stated_scopes("18.9 GW of grid scale storage") == ["grid-scale"]
    assert stated_scopes("utility, C&I, and residential systems") == [
        "residential",
        "c&i",
    ]


def test_stated_scopes_consumes_the_longest_match_and_rejects_negation() -> None:
    assert stated_scopes("18.9 GW of commercial and industrial demand") == ["c&i"]
    assert stated_scopes("non-residential systems added capacity") == []
    assert stated_scopes("non residential systems added capacity") == []
