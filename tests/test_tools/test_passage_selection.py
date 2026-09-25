"""Query-aware deterministic passage selection."""

from __future__ import annotations

from deep_research.tools.passage_selection import select_relevant_passages


def test_selection_considers_every_locator_and_keeps_late_paraphrase() -> None:
    passages = {
        "page-1": "Contents and executive summary.",
        "page-2": "The project was commissioned after a queue delay.",
        "page-3": "Appendix and references.",
    }

    assert select_relevant_passages(
        passages, "interconnection waiting time project delivery", 1
    ) == ["page-2"]


def test_a_hyphenated_query_term_matches_its_spaced_form() -> None:
    passages = {
        "spec-sheet": "Dimensions and weight only.",
        "verdict": "The sound quality here is the best in its class.",
    }

    assert select_relevant_passages(passages, "sound-quality assessment", 1) == [
        "verdict"
    ]


def test_a_possessive_query_term_matches_the_bare_name() -> None:
    passages = {
        "menu": "Home About Contact Privacy",
        "profile": "Orion publishes measurements every year.",
    }

    assert select_relevant_passages(passages, "what is Orion's verdict", 1) == [
        "profile"
    ]


def test_a_typographic_apostrophe_leaves_no_single_letter_term() -> None:
    passages = {
        "chrome": (
            "Everyone\u2019s favourite picks were listed, and nobody\u2019s notes "
            "were kept."
        ),
        "profile": "Orion publishes measurements every year.",
    }

    assert select_relevant_passages(passages, "Orion\u2019s verdict", 1) == [
        "profile"
    ]


def test_function_words_alone_do_not_promote_page_chrome() -> None:
    passages = {
        "chrome": (
            "Can it be so? That was no accident: each of them has their own view, "
            "and these were those this week."
        ),
        "verdict": "Battery life is the best of the models we tested.",
    }

    assert select_relevant_passages(
        passages, "can that be so which model has the best battery life", 1
    ) == ["verdict"]


def test_benchmark_selection_over_statistical_figures_is_unchanged() -> None:
    query = (
        "utility-scale battery storage capacity additions in the United States "
        "in 2024 An actual installation figure for 2024 from the statistical "
        "agency's own release"
    )
    passages = {
        "nav": (
            "Skip to main content Menu Search Subscribe Home Topics Today in "
            "Energy Browse by Region"
        ),
        "toc": "Table of contents Executive summary Data sources References Appendix",
        "lede": (
            "In 2024, utility-scale battery storage capacity additions in the "
            "United States reached a record level, the agency reported."
        ),
        "chart": "Figure 2. Utility-scale battery storage capacity additions (GW)",
        "data": (
            "Utility-scale battery storage capacity additions totaled 10.4 GW in "
            "2024, the largest annual increase on record."
        ),
    }

    # The figure-bearing chunks keep the top slots and page chrome never
    # enters them, so the benchmark's own page keeps the same selection.
    assert sorted(select_relevant_passages(passages, query, 3)) == [
        "chart",
        "data",
        "lede",
    ]
