"""Query-aware deterministic passage selection."""

from __future__ import annotations

from deep_research.tools.passage_selection import select_relevant_passages


def test_a_plural_query_term_matches_its_singular_form() -> None:
    """D1: morphological expansion is domain-neutral -- plural/singular,
    -ing/-ed -- never a word list mapping one word to a different one."""
    passages = {
        "page-1": "Table of contents and executive summary.",
        "page-2": "The project logged three delays before it was delivered.",
        "page-3": "Appendix and references.",
    }

    assert select_relevant_passages(passages, "project delay", 1) == ["page-2"]


def test_no_domain_specific_synonym_list_stands_in_for_the_query() -> None:
    """D1: selection must never fall back on a domain word list.

    The audited run's ranking mapped "interconnection" to "queue" through an
    energy-only synonym group, so a page about a queue outside a venue
    became a match for a query about grid interconnection. Selection may
    expand a token's own morphology; it may not decide two different words
    name the same thing, in energy or any other domain.
    """
    passages = {
        "queue-page": "The queue outside the venue stretched around the block.",
        "unrelated": "Ticket prices rose by ten percent this season.",
    }

    assert select_relevant_passages(passages, "interconnection", 1) == []


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


def test_a_focused_short_match_outranks_a_diffuse_long_one() -> None:
    """D1: weighted, length-normalised scoring beats plain term counting.

    Both passages below mention every query term exactly once, so a plain
    count of distinct terms ties them and reader order decides -- exactly
    the bug that let a long page which only brushes against the query
    outrank a short one that is actually about it. Density (matches over the
    passage's own length) breaks the tie correctly instead.
    """
    long_passage = (
        "This report opens with a general survey of consumer electronics "
        "trends before turning, several paragraphs later, to note in passing "
        "that battery life varies by model and that call quality is one "
        "factor reviewers weigh among many others when they publish a score."
    )
    short_passage = "Call quality and battery life both tested best in class."

    assert select_relevant_passages(
        {"long": long_passage, "short": short_passage},
        "call quality battery life",
        1,
    ) == ["short"]


def test_a_link_dense_passage_is_never_ranked_even_when_it_names_the_query() -> None:
    """D1: navigation is never admitted, however many query words it repeats.

    Navigation strings nouns together with almost none of the connective
    words a written sentence needs. A block shaped like that must never
    outrank real prose, however many times it repeats a query word as a
    link label.
    """
    passages = {
        "nav": (
            "Camera Battery Display Storage Price Review Specs Compare Deals "
            "News Battery Storage Battery Storage Battery Storage Battery "
            "Storage Support Warranty Shipping Returns Accessories Battery"
        ),
        "verdict": "Battery storage capacity was the best we measured this year.",
    }

    assert select_relevant_passages(passages, "battery storage", 1) == ["verdict"]


def test_a_data_row_with_no_connective_words_is_not_navigation() -> None:
    """D1: the nav detector must not catch structured data, only chrome.

    A CSV row (or any tabular data) strings bare values together with no
    connective words too, exactly like a menu -- but it is dense with
    numbers a page's own link rail never carries. It must still be ranked
    on its own merits, not zeroed out as chrome.
    """
    passages = {
        "row": (
            "Alpha,12,40,queue delay commissioning cost measured\n"
            "Bravo,18,55,queue delay commissioning cost measured\n"
        ),
        "unrelated": "Charlie reported no figures for this period at all.",
    }

    assert select_relevant_passages(passages, "queue delay commissioning", 1) == [
        "row"
    ]
