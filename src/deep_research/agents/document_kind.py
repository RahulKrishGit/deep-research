"""What a document says about itself: derived, teaching, or AI-written content.

Nothing here judges a source's authority or relevance -- the evaluator does
that (``prompts.SOURCE_SCORING_INSTRUCTION``). This module answers one
narrow question, from the read's own text alone: does the document declare
itself teaching or exercise material, a simplified or adapted rendering, a
relay of an encyclopedia's or a chatbot's content, or AI-written by its own
words? Both the evaluator's dossier and its scoring rule read this module's
verdict instead of re-deriving it, so the declaration is judged once.
"""

from __future__ import annotations

import re

from deep_research.utils.types import ReadRecord

# A sentence boundary: one of the three stops followed by whitespace and an
# uppercase letter -- not a break at a single-letter or known-abbreviation
# token ("Dr. Vale", "Prof. Vale", "U.S. Department" stay one sentence). The
# same rule the report writer's own splitter uses (``report_writer.
# _first_sentence``), kept as a small local copy rather than an import: this
# module is a leaf the rest of the pipeline depends on, and importing from a
# much larger agent module here would risk a cycle back through it.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

_ABBREVIATIONS = {
    "u.s.", "u.k.", "dr.", "mr.", "mrs.", "ms.", "st.", "no.", "vs.",
    "e.g.", "i.e.", "etc.", "jr.", "sr.", "prof.", "inc.", "ltd.", "co.",
}

# Return the sentence verbatim, cut at this many characters on a word
# boundary -- never mid-word, never paraphrased.
_MAX_SENTENCE_CHARS = 300

# Kinds that are teaching or exercise material by their own name -- no other
# word in the sentence has to say so.
_INHERENT_KIND_NOUNS = (
    r"role-play|roleplay|teaching case|teaching note|lesson|worksheet|"
    r"sample essay|model answer"
)
# Kinds a real-world report, drill or research study also uses for its own
# subject: these count as a self-declaration only when the same sentence
# also carries a teaching cue (course, classroom, students, ...). Without
# that guard, a news story about a county emergency drill or a research
# institute's own case study would be flagged as teaching material.
_AMBIGUOUS_KIND_NOUNS = r"simulation|exercise|case study|scenario|game"
_ALL_KIND_NOUNS = rf"{_INHERENT_KIND_NOUNS}|{_AMBIGUOUS_KIND_NOUNS}"
_AMBIGUOUS_KIND_ONLY = re.compile(rf"(?:{_AMBIGUOUS_KIND_NOUNS})", re.IGNORECASE)

_TEACHING_CUE_WORDS = (
    r"course|class|classroom|students?|participants?|trainees?|learners?|"
    r"seminar|curriculum|educational|teaching|training|instruction"
)
_TEACHING_CUE = re.compile(rf"\b(?:{_TEACHING_CUE_WORDS})\b", re.IGNORECASE)

_WRITING_VERBS = r"was|is|were|are|has been|have been"
_WRITING_ACTIONS = r"written|prepared|designed|developed|adapted"

# Category 1: the document names itself teaching or exercise material and
# says so was/is/has been written, prepared, designed, developed or adapted
# -- "this role-play was written by ...", "this negotiation role-play ...".
# Up to two modifier words may sit between "this" and the kind noun.
_TEACHING_MATERIAL = re.compile(
    rf"\bthis\s+(?:\w+\s+){{0,2}}?(?P<kind>{_ALL_KIND_NOUNS})\b.{{0,80}}?"
    rf"\b(?:{_WRITING_VERBS})\s+(?:{_WRITING_ACTIONS})\b",
    re.IGNORECASE,
)


def _is_teaching_material(sentence: str) -> bool:
    """Category 1, applied to one sentence.

    An ambiguous kind (a simulation, an exercise, a case study, a scenario,
    a game) counts only when the sentence also carries a teaching cue,
    checked with the fully matched kind text so "case study" is judged as
    the ambiguous kind it is, not misread as containing an inherent one. A
    kind that is teaching material by its own name needs no cue.
    """
    match = _TEACHING_MATERIAL.search(sentence)
    if match is None:
        return False
    if _AMBIGUOUS_KIND_ONLY.fullmatch(match.group("kind")):
        return _TEACHING_CUE.search(sentence) is not None
    return True


# Category 2: simplified, adapted or fictionalised for a teaching purpose --
# independent of category 1's kind noun, since a document may say this of
# itself without naming what kind of material it is. "illustrative" is
# deliberately absent: a statistical agency's table note ("simplified for
# illustrative purposes only") and a regulator's guidance on its own rules
# ("illustrative and simplified for illustrative purposes") both use it as
# an ordinary hedge word, not a declaration that the page is teaching
# material -- the writer's bottom-line floor would otherwise withhold a
# primary data or rule page for this alone.
_SIMPLIFIED_FOR_TEACHING = re.compile(
    r"\b(?:simplified|adapted|fictionalised|fictionalized)\b.{0,80}?\bfor\b"
    r".{0,40}?\b(?:educational|teaching|classroom|instructional|training)"
    r"\s+purposes\b",
    re.IGNORECASE,
)

# Category 3: based on an encyclopedia's or a chatbot's content. The relay
# has to be the source of the document's own content, not merely a nearby
# word -- otherwise a regulation, a ruling or a review *about* AI would be
# flagged for mentioning it near "based on". Either branch requires that:
# the sentence names what was relayed (entries, content, research, ... in,
# of, from or by the relay), or the document refers to itself (this
# article/page/text/document/content/summary, or "the statements ... in
# the text") and says directly that it is based on the relay.
_RELAY_SOURCE = (
    r"(?:an online encyclopedia|an encyclopedia|wikipedia|chatgpt|"
    r"a chatbot|an? ai(?: tool| system)?|a language model)"
)
_RELAY_CONTENT_NOUN = (
    r"(?:entries|articles?|content|research|output|answers|information|text)"
)
_BASED_ON_RELAY_CONTENT = re.compile(
    rf"\bbased on\s+(?:the\s+)?(?:corresponding\s+)?{_RELAY_CONTENT_NOUN}\s+"
    rf"(?:in|of|from|by)\s+{_RELAY_SOURCE}\b",
    re.IGNORECASE,
)
_SELF_REFERENCING_SUBJECT = (
    r"(?:this\s+(?:article|page|text|document|content|summary)|"
    r"the statements?\b[^.!?]{0,40}?\bin the text)"
)
_BASED_ON_RELAY_SELF_REF = re.compile(
    rf"{_SELF_REFERENCING_SUBJECT}.{{0,80}}?\bbased on\b.{{0,60}}?{_RELAY_SOURCE}\b",
    re.IGNORECASE,
)

# Category 4: the document says, of itself, that it was generated, written
# or produced by or with AI, a chatbot or a language model.
_AI_WRITTEN = re.compile(
    r"\bthis\s+(?:article|page|text|content|summary)\b.{0,60}?\b"
    r"(?:was|is|were|has been)\s+(?:generated|written|produced)\b"
    r".{0,40}?\b(?:by|with)\b.{0,20}?\b(?:ai|a chatbot|a language model)\b",
    re.IGNORECASE,
)

_DECLARATION_CHECKS = (
    _is_teaching_material,
    lambda sentence: _SIMPLIFIED_FOR_TEACHING.search(sentence) is not None,
    lambda sentence: _BASED_ON_RELAY_CONTENT.search(sentence) is not None,
    lambda sentence: _BASED_ON_RELAY_SELF_REF.search(sentence) is not None,
    lambda sentence: _AI_WRITTEN.search(sentence) is not None,
)


def derivative_self_description(read: ReadRecord) -> str | None:
    """The document's own sentence declaring it derived or teaching content.

    Scans the read's passages joined in document order -- so a sentence a
    page's own extraction split across two chunks is still whole -- and
    returns the first sentence in which the document declares itself one
    of: teaching or exercise material written, prepared, designed,
    developed or adapted as such (an ambiguous kind -- a simulation, an
    exercise, a case study, a scenario, a game -- only counts alongside a
    teaching cue in the same sentence); simplified, adapted or
    fictionalised for an educational, teaching, classroom, instructional or
    training purpose; based on an encyclopedia's or a chatbot's content; or
    AI-written by its own words.

    Verbatim, cut at 300 characters on a word boundary. ``None`` when the
    document makes no such declaration.

    A chunk boundary the reader introduced for a reason unrelated to the
    page's own sentences (a paragraph break, a column reflow) can fall
    inside the one sentence that declares this: when that happens, the
    string returned is the two chunks' text joined at that edge, not
    reformatted to the words the page prints as a continuous whole. The
    declaration and its words are still the document's own; only the exact
    reproduction of the page's own spacing at the join is not guaranteed.
    """
    text = " ".join(" ".join(passage.split()) for passage in read.passages.values())
    for sentence in _sentences(text):
        if any(check(sentence) for check in _DECLARATION_CHECKS):
            return _clamped(sentence)
    return None


def _is_abbreviation_break(text: str, position: int) -> bool:
    """True when the token right before ``position`` never ends a sentence
    on its own -- a single letter or a known abbreviation, periods and all."""
    before = text[:position].split()
    if not before:
        return False
    token = before[-1]
    bare = token.rstrip(".")
    return len(bare) <= 1 or token.lower() in _ABBREVIATIONS


def _sentences(text: str) -> list[str]:
    """Every sentence of ``text``, in order, stripped of outer whitespace.

    A break at a single-letter or known-abbreviation token ("Dr.", "Prof.",
    "U.S.") is not treated as a sentence end, so a declaring sentence that
    happens to name someone by title and initial is scanned whole.
    """
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if _is_abbreviation_break(text, match.start()):
            continue
        sentences.append(text[start : match.start()].strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return [sentence for sentence in sentences if sentence]


def _clamped(sentence: str) -> str:
    """``sentence``, cut to ``_MAX_SENTENCE_CHARS`` on a word boundary."""
    if len(sentence) <= _MAX_SENTENCE_CHARS:
        return sentence
    cut = sentence.rfind(" ", 0, _MAX_SENTENCE_CHARS + 1)
    return sentence[:cut] if cut > 0 else sentence[:_MAX_SENTENCE_CHARS]
