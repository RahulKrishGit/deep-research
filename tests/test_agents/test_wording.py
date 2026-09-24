"""The wording rules the Evidence Verifier and the Report Writer share (PD-19)."""

from __future__ import annotations

from deep_research.agents.wording import (
    hardened_modality,
    hedge_forecast,
    hedge_marker,
    page_modal,
    stated_role,
    stated_scopes,
    stated_years,
    unattested_names,
)


def test_the_moved_rules_behave_as_before() -> None:
    assert hedge_marker("capacity could set a record") == "could"
    assert stated_role("16 GW was installed in 2025") == "actual"
    assert stated_role("EIA forecast that 18.2 GW would be added in 2025") == "forecast"
    assert stated_role("the operator beat projections: 16 GW was installed in 2025") == "mixed"
    assert hardened_modality("the fleet will set a record", "capacity could set a record") == "will"


def test_a_verb_after_to_be_is_not_an_outcome() -> None:
    assert stated_role("18.2 GW is expected to be added in 2025") == "forecast"


def test_names_must_be_attested_and_an_acronym_may_be_spelled_out() -> None:
    corpus = "U.S. Energy Information Administration expects 14 GW"
    assert unattested_names("Analysts say EIA expects 14 GW", corpus.casefold(), corpus) == []
    assert "BloombergNEF" in unattested_names("Analysts say BloombergNEF expects 14 GW", corpus.casefold(), corpus)


def test_years_and_scopes_are_read_from_the_text() -> None:
    assert stated_years("From 2024 to 2025, 10.4 GW") == ["2024", "2025"]
    assert stated_scopes("18.9 GW of grid scale storage") == ["grid-scale"]
    assert stated_scopes("utility, C&I, and residential systems") == ["residential", "c&i"]


def test_hedge_forecast_makes_a_forecast_read_as_one() -> None:
    eia = "U.S. Energy Information Administration"
    rewritten = hedge_forecast("EIA's outlook adds 14 GW in 2025.", eia)
    assert rewritten == "EIA's outlook adds 14 GW in 2025, according to U.S. Energy Information Administration's forecast."
    assert stated_role(rewritten) == "forecast"
    passive = hedge_forecast("In 2025, 18.2 GW was added.", eia)
    assert passive == "In 2025, 18.2 GW is expected to be added."
    assert stated_role(passive) == "forecast"
    active = hedge_forecast("Developers added 14 GW in 2025.", eia)
    assert active == "Developers expected to add 14 GW in 2025."
    assert stated_role(active) == "forecast"


def test_hedge_forecast_takes_the_pages_own_modal() -> None:
    eia = "U.S. Energy Information Administration"
    assert hedge_forecast("Storage will grow 47% in 2025.", eia, marker="could") == "Storage could grow 47% in 2025."
    assert hedge_forecast("Storage would reach 14 GW in 2025.", eia) == "Storage is expected to reach 14 GW in 2025."
    assert hardened_modality("Storage could grow 47% in 2025.", "capacity could grow") == ""
    assert page_modal("Battery storage capacity could grow by 47% (14 GW) in 2025.") == "could"
    assert page_modal("The outlook was released in May 2025.") == ""
