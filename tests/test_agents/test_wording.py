"""The wording rules the Evidence Verifier and the Report Writer share (PD-19)."""

from __future__ import annotations

from deep_research.agents.wording import (
    hardened_modality,
    hedge_marker,
    stated_role,
    stated_scopes,
    stated_years,
    unattested_names,
)


def test_the_moved_rules_behave_as_before() -> None:
    assert hedge_marker("capacity could set a record") == "could"
    assert stated_role("16 GW was installed in 2025") == "actual"
    assert stated_role("EIA forecast that 18.2 GW would be added in 2025") == "forecast"
    assert (
        stated_role("the operator beat projections: 16 GW was installed in 2025")
        == "mixed"
    )
    assert (
        hardened_modality(
            "the fleet will set a record", "capacity could set a record"
        )
        == "will"
    )
    # A page that already hedges its own claim is not a page whose wording the
    # report hardened: nothing to re-attach, so nothing is reported.
    assert (
        hardened_modality("Storage could grow 47% in 2025.", "capacity could grow")
        == ""
    )


def test_a_verb_after_to_be_is_not_an_outcome() -> None:
    assert stated_role("18.2 GW is expected to be added in 2025") == "forecast"


def test_names_must_be_attested_and_an_acronym_may_be_spelled_out() -> None:
    corpus = "U.S. Energy Information Administration expects 14 GW"
    assert (
        unattested_names("Analysts say EIA expects 14 GW", corpus.casefold(), corpus)
        == []
    )
    assert "BloombergNEF" in unattested_names(
        "Analysts say BloombergNEF expects 14 GW", corpus.casefold(), corpus
    )


def test_years_and_scopes_are_read_from_the_text() -> None:
    assert stated_years("From 2024 to 2025, 10.4 GW") == ["2024", "2025"]
    assert stated_scopes("18.9 GW of grid scale storage") == ["grid-scale"]
    assert stated_scopes("utility, C&I, and residential systems") == [
        "residential",
        "c&i",
    ]


def test_stated_scopes_consumes_the_longest_match_and_rejects_negation() -> None:
    assert stated_scopes("18.9 GW of commercial and industrial demand") == ["c&i"]
    assert stated_scopes("non-residential systems added capacity") == []
    assert stated_scopes("non residential systems added capacity") == []


def test_stated_years_reads_fiscal_year_and_range_forms() -> None:
    assert stated_years("FY2024 storage additions") == ["2024"]
    assert stated_years("Additions in 2025-26 rose") == ["2025", "2026"]
