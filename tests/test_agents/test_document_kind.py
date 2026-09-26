"""Tests for ``derivative_self_description``: what a document says about
itself.

D1/D2 (run-8 fix wave): nothing in the pipeline reads what a document says
about itself before it is credited. This contract scans a read's own text
for a sentence in which the document declares itself teaching or exercise
material, simplified or adapted for a teaching purpose, based on an
encyclopedia's or a chatbot's content, or AI-written by its own words --
verbatim, cut at 300 characters on a word boundary, or ``None``.
"""

from __future__ import annotations

from deep_research.agents.document_kind import derivative_self_description
from tests.evidence_fakes import make_read


# --- negatives: none of these declare the document itself derivative -------


def test_a_news_article_about_ai_tools_is_not_a_self_description() -> None:
    read = make_read("The company released AI-generated images.")
    assert derivative_self_description(read) is None


def test_explanatory_prose_naming_an_example_is_not_a_self_description() -> None:
    read = make_read("This example shows how the rate is computed.")
    assert derivative_self_description(read) is None


def test_an_authorship_line_is_not_a_self_description() -> None:
    read = make_read(
        "This article was written by Dr. Jane Vale, professor at "
        "Example University."
    )
    assert derivative_self_description(read) is None


def test_based_on_data_from_an_institute_is_not_a_self_description() -> None:
    read = make_read(
        "The report's figures are based on data from Example Institute."
    )
    assert derivative_self_description(read) is None


def test_a_page_that_merely_links_to_an_encyclopedia_is_not_a_self_description() -> None:
    read = make_read("For more background, see the article in the online encyclopedia.")
    assert derivative_self_description(read) is None


# --- positive: the audit's case, neutral stand-in ---------------------------


def test_a_footnote_split_across_two_chunks_is_read_as_one_sentence() -> None:
    """The audit's case (D1): a page's own footnote says its history is a
    relay of an encyclopedia's and a chatbot's content and that it was
    simplified for teaching -- and the extraction split the declaring
    sentence itself across two locators. Neither passage alone carries the
    kind noun and its verb together; only the joined text does, so this
    proves passages are joined in document order before sentences are cut.
    """
    passage_a = "This document overview precedes the following note. This role-play"
    passage_b = (
        "was written by the outreach program for a negotiation course. "
        "It is based on the events of a 2019 regulatory dispute. The "
        "statements in the text are essentially based on the corresponding "
        "entries in an online encyclopedia and chatbot research, and the "
        "account has been simplified and adapted for educational purposes."
    )
    read = make_read(
        f"{passage_a} {passage_b}",
        passages={"footnote-chunk-0": passage_a, "footnote-chunk-1": passage_b},
    )

    assert derivative_self_description(read) == (
        "This role-play was written by the outreach program for a "
        "negotiation course."
    )


def test_simplified_for_training_purposes_needs_no_kind_noun() -> None:
    read = make_read(
        "The included materials have been simplified and adapted for "
        "training purposes."
    )
    assert derivative_self_description(read) == (
        "The included materials have been simplified and adapted for "
        "training purposes."
    )


def test_based_on_an_encyclopedia_alone_is_a_self_description() -> None:
    read = make_read(
        "The article's timeline is largely based on the entries in an "
        "online encyclopedia."
    )
    assert derivative_self_description(read) == (
        "The article's timeline is largely based on the entries in an "
        "online encyclopedia."
    )


def test_an_ai_written_declaration_is_a_self_description() -> None:
    read = make_read(
        "This summary was produced by a language model without human review."
    )
    assert derivative_self_description(read) == (
        "This summary was produced by a language model without human review."
    )


def test_the_first_matching_sentence_wins_over_a_later_one() -> None:
    read = make_read(
        "This worksheet was written by the outreach program. It is also "
        "based on the entries in an online encyclopedia."
    )
    assert derivative_self_description(read) == (
        "This worksheet was written by the outreach program."
    )


def test_a_long_matching_sentence_is_cut_at_300_characters_on_a_word_boundary() -> None:
    filler = "additional detail " * 20
    sentence = (
        f"This case study was written by the outreach program to give "
        f"trainees a {filler}walkthrough for classroom use."
    )
    read = make_read(sentence)

    result = derivative_self_description(read)

    assert result is not None
    assert len(result) <= 300
    assert sentence.startswith(result)
    # Cut cleanly at a word boundary: what follows the cut in the original
    # sentence starts a new word, it is never sliced mid-word.
    assert len(result) == len(sentence) or sentence[len(result)] == " "
