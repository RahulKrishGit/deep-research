"""Query-aware deterministic passage selection."""

from __future__ import annotations

from deep_research.tools.passage_selection import (
    select_passages_by_budget,
    select_relevant_passages,
)


def test_a_plural_query_term_matches_its_singular_form() -> None:
    """Morphological expansion is domain-neutral -- plural/singular,
    -ing/-ed -- never a word list mapping one word to a different one."""
    passages = {
        "page-1": "Table of contents and executive summary.",
        "page-2": "The project logged three delays before it was delivered.",
        "page-3": "Appendix and references.",
    }

    assert select_relevant_passages(passages, "project delay", 1) == ["page-2"]


def test_no_domain_specific_synonym_list_stands_in_for_the_query() -> None:
    """Selection must never fall back on a domain word list.

    The tool's ranking must not map different words to the same concept through
    domain-specific synonym groups. Selection may expand a token's own
    morphology; it may not decide two different words name the same thing, in
    energy or any other domain.
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
    """Weighted, length-normalised scoring beats plain term counting.

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


def test_real_review_site_navigation_shapes_are_link_dense() -> None:
    """The detector correctly identifies real navigation shapes, not toy strings
    with zero stop words. Real site navigation opens with masthead, category nav
    and "how we test" style labels, which is exactly the shape a plain
    connective-word ratio could not separate from real prose."""
    from deep_research.tools.passage_selection import is_link_dense

    soundguys_nav = (
        "SoundGuys Headphones Earbuds Speakers Podcasts Deals Reviews Guides "
        "About Us Contact Search Menu Best Headphones for 2026: Tested and "
        "Reviewed Best overall Best for Android Best on a budget Best for "
        "calls Best noise cancelling Best true wireless How we test What to "
        "look for Skip to main content"
    )
    cnet_nav = (
        "CNET Tech Money Home Wellness Deals Best Products Reviews How We "
        "Test Search Log In Subscribe Best Headphones to Buy in 2026, Tested "
        "and Reviewed Best overall Best budget Best for calls Best noise "
        "cancelling Best battery life How we test headphones What to look "
        "for Skip to main content Continue Reading Below"
    )
    whathifi_nav = (
        "What Hi-Fi Best Buys News Reviews Deals Awards Advice Sign in "
        "Subscribe Search Best Headphones 2026: The Top Wireless And Wired "
        "Models We Have Tested Best overall Best budget Best for calls Best "
        "noise cancelling How we test What to look for when choosing "
        "headphones Skip to main content"
    )
    business_insider_nav = (
        "Business Insider Tech Reviews Guides Deals Newsletters Subscribe "
        "Account Search The Best Headphones We Have Tested in 2026 Best "
        "overall Best budget pick Best for calls Best noise cancelling How "
        "we test our headphone picks What to look for when buying "
        "headphones Skip to main content Menu"
    )
    toms_hardware_nav = (
        "Tom's Hardware Reviews News Best Picks Forums Deals Subscribe "
        "Search Menu Best Gaming Headsets in 2026 Best overall Best budget "
        "pick Best wireless Best for calls and chat How we test our headset "
        "picks What to look for before you buy Skip to main content Sign up "
        "for our newsletter"
    )
    for nav in (
        soundguys_nav,
        cnet_nav,
        whathifi_nav,
        business_insider_nav,
        toms_hardware_nav,
    ):
        assert is_link_dense(nav), nav[:40]


def test_pros_cons_verdict_and_foreign_prose_are_not_link_dense() -> None:
    """The rule gates on sentence structure, never on English connective
    frequency: a pros/cons box, a verdict line, a feature list and a German
    or Spanish paragraph all break into ordinary clauses and must never be
    caught by a stop-word ratio."""
    from deep_research.tools.passage_selection import is_link_dense

    pros_cons = (
        "Pros and cons. Lightweight and comfortable for long listening "
        "sessions. Excellent noise cancelling and clear microphone quality "
        "on calls. Cons: touch controls are occasionally unresponsive. "
        "Battery life is merely average compared to rivals."
    )
    verdict_box = (
        "Verdict. Best overall noise-cancelling headphones, with excellent "
        "microphone quality for calls and industry-leading battery life."
    )
    feature_list = (
        "Key features. Adaptive noise cancelling. Clear microphone pickup "
        "on calls. Multipoint Bluetooth pairing. Thirty-hour battery life. "
        "Wear detection that pauses playback automatically."
    )
    german = (
        "Diese Kopfhoerer bieten eine hervorragende microphone quality fuer "
        "calls und Videokonferenzen. Die Klangqualitaet ist ebenfalls "
        "beeindruckend und die Akkulaufzeit haelt einen ganzen Arbeitstag "
        "durch."
    )
    spanish = (
        "Estos auriculares ofrecen una excelente microphone quality para "
        "calls y videollamadas. La calidad de sonido tambien es "
        "impresionante y la bateria dura toda la jornada laboral."
    )
    for prose in (pros_cons, verdict_box, feature_list, german, spanish):
        assert not is_link_dense(prose), prose[:40]


def test_a_short_title_page_and_a_real_opener_are_not_link_dense() -> None:
    """A short title page and a genuine title-plus-lede opener both keep
    their guaranteed lede: brevity and ordinary sentence breaks are what
    the digit/word-count floor and the clause check are for."""
    from deep_research.tools.passage_selection import is_link_dense

    short_title_page = "Annual Outlook 2025 - Issuer - April 2025"
    real_opener = (
        "Best Headphones for 2026: Tested and Reviewed. We spent three "
        "months testing dozens of headphones for sound quality and comfort. "
        "Microphone performance mattered just as much, so every pair was "
        "tested on real calls before it made our list."
    )
    assert not is_link_dense(short_title_page)
    assert not is_link_dense(real_opener)


def test_distinct_coverage_outweighs_one_repeated_term() -> None:
    """A chunk repeating one query word must not outrank a chunk that covers
    more of the query once each. A saturating term frequency plus a read-level
    inverse document frequency fixes the scoring directly."""
    query = "best wireless headphones microphone call quality noise cancelling"
    repeats_one_term = (
        "Best headphones best headphones best headphones best headphones "
        "best headphones best headphones for every budget and every use "
        "case this year."
    )
    covers_the_query = (
        "The microphone on this wireless headphone delivers clear call "
        "quality with effective noise cancelling, easily the best we tested."
    )
    passages = {"repeats": repeats_one_term, "covers": covers_the_query}
    assert select_relevant_passages(passages, query, 1) == ["covers"]


def test_a_short_document_falls_back_to_a_single_passage() -> None:
    """A read with only one or two passages ranks sensibly: with no other
    passage to compare against, the one that matches still wins over the
    one that does not."""
    passages = {
        "only": "The battery lasts twelve hours on a single charge.",
        "other": "Shipping resumes on Monday after the regional holiday.",
    }
    assert select_relevant_passages(passages, "battery hours charge", 1) == [
        "only"
    ]


def test_a_data_row_with_no_connective_words_is_not_navigation() -> None:
    """The nav detector must not catch structured data, only chrome.

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


def test_a_passage_with_no_shared_term_is_still_admitted() -> None:
    """Whole-page admission means every passage, not only the matches.

    A passage that shares no word with the query is still part of the page
    the run read, and a semantic answer can share no word with the query
    that names it -- ranking is not the filter for what becomes a unit.
    Ranked matches are admitted first; every passage that shares no term
    then fills whatever budget remains, in reader order, so a read's full
    text becomes evidence rather than only the sentences that happen to
    echo the query's own words.
    """
    passages = {
        "unrelated": "Shipping resumes on Monday after the regional holiday.",
        "answer": "The battery lasts twelve hours on a single charge.",
    }

    assert select_passages_by_budget(passages, "battery hours charge", 1000) == [
        "answer",
        "unrelated",
    ]


def test_a_zero_term_passage_yields_to_a_ranked_match_under_a_tight_budget() -> None:
    """The unmatched fill never displaces a ranked match that already fits.

    A budget too small for both passages keeps the ranked match and drops
    the unmatched one, the same "kept only while it still fits" rule the
    ranked pass already follows.
    """
    passages = {
        "unrelated": "Shipping resumes on Monday after the regional holiday.",
        "answer": "The battery lasts twelve hours on a single charge.",
    }
    budget = len(passages["answer"])

    assert select_passages_by_budget(
        passages, "battery hours charge", budget
    ) == ["answer"]
