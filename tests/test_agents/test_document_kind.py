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

import pytest

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


# --- P1: "based on" flags the document as a relay only when the relay is --
# --- the source of *its own* content, not merely a nearby word ------------


@pytest.mark.parametrize(
    "sentence",
    [
        "This regulation is based on the risk classification of AI systems "
        "set out in the framework.",
        "The framework is based on input from AI experts at Example "
        "Institute.",
        "The ruling was based on evidence that AI models had copied the "
        "works.",
        "The service is based on ChatGPT and launched in 2023.",
        "This review is based on two weeks of testing with the phone's AI "
        "assistant.",
        "The film's plot is loosely based on an AI researcher's memoir.",
    ],
)
def test_a_document_that_is_merely_about_ai_is_not_a_self_description(
    sentence: str,
) -> None:
    """A primary regulatory, legal or review text on an AI topic is not a
    relay of an encyclopedia's or a chatbot's content just because the
    word "AI" sits near "based on"."""
    read = make_read(sentence)
    assert derivative_self_description(read) is None


def test_a_self_referencing_subject_based_on_a_relay_with_no_middle_noun() -> None:
    read = make_read(
        "This summary is based on content from an online encyclopedia."
    )
    assert derivative_self_description(read) == (
        "This summary is based on content from an online encyclopedia."
    )


# --- P2: an ambiguous kind (simulation, exercise, case study, scenario, ---
# --- game) counts only with a teaching cue in the same sentence; the ------
# --- inherently-teaching kinds (role-play, teaching case, teaching note, --
# --- lesson, worksheet, sample essay, model answer) never need one --------


@pytest.mark.parametrize(
    "sentence",
    [
        "This simulation was designed by the county emergency office to "
        "test response times.",
        "This exercise was developed by the fire department with teachers.",
        "This case study was prepared by Example Institute researchers "
        "using data from the 2019 regulation's filings.",
    ],
)
def test_an_ambiguous_kind_without_a_teaching_cue_is_not_a_self_description(
    sentence: str,
) -> None:
    """A news report on a real drill, or a research institute's own case
    study, is not teaching material just because it uses one of these
    ambiguous nouns with no teaching cue anywhere in the sentence."""
    read = make_read(sentence)
    assert derivative_self_description(read) is None


def test_an_ambiguous_kind_with_a_teaching_cue_is_a_self_description() -> None:
    read = make_read("This simulation was designed for a negotiation course.")
    assert derivative_self_description(read) == (
        "This simulation was designed for a negotiation course."
    )


# --- P2: phrasings the earlier pattern missed ------------------------------


def test_has_been_prepared_is_accepted_alongside_was_and_is() -> None:
    read = make_read(
        "This case study has been prepared by the outreach program for "
        "classroom discussion."
    )
    assert derivative_self_description(read) == (
        "This case study has been prepared by the outreach program for "
        "classroom discussion."
    )


def test_up_to_two_modifier_words_may_sit_between_this_and_the_kind_noun() -> None:
    read = make_read(
        "This negotiation role-play was written by the outreach program."
    )
    assert derivative_self_description(read) == (
        "This negotiation role-play was written by the outreach program."
    )


def test_an_abbreviation_inside_the_declaring_sentence_does_not_end_it_early() -> None:
    read = make_read(
        "This role-play, prepared with Prof. Vale of Example University, "
        "was written for a negotiation course."
    )
    assert derivative_self_description(read) == (
        "This role-play, prepared with Prof. Vale of Example University, "
        "was written for a negotiation course."
    )


# --- Fable prompt review: category 2's "illustrative" over-reaches onto ---
# --- a data table's own note and a regulator's own guidance on its rules --


@pytest.mark.parametrize(
    "sentence",
    [
        "Values in this table are simplified for illustrative purposes only.",
        "The examples in this guidance are illustrative and simplified for "
        "illustrative purposes.",
    ],
)
def test_illustrative_purposes_alone_is_not_a_self_description(
    sentence: str,
) -> None:
    """A statistical agency's table note and a regulator's own guidance on
    its own rules are not teaching material; "illustrative" alone is too
    common a hedge word on a primary data or rule page to signal a relay."""
    read = make_read(sentence)
    assert derivative_self_description(read) is None


def test_simplified_and_adapted_for_educational_purposes_is_still_a_self_description() -> None:
    read = make_read(
        "The account has been simplified accordingly and adapted for "
        "educational purposes."
    )
    assert derivative_self_description(read) == (
        "The account has been simplified accordingly and adapted for "
        "educational purposes."
    )
