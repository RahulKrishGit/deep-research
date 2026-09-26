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

# A sentence boundary: one of the three stops followed by whitespace. Every
# passage is joined, in document order, before this runs -- so a sentence a
# page's own extraction split across two chunks (the audit's case: a
# footnote whose text crossed a locator boundary) is scanned whole rather
# than as two unmatched fragments.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

# Return the sentence verbatim, cut at this many characters on a word
# boundary -- never mid-word, never paraphrased.
_MAX_SENTENCE_CHARS = 300

_KIND_NOUNS = (
    r"role-play|roleplay|simulation|exercise|case study|"
    r"teaching case|teaching note|lesson|worksheet|sample essay|"
    r"model answer"
)
_WRITING_VERBS = r"written|prepared|designed|developed|adapted"

# Category 1: the document names itself teaching or exercise material and
# says so was written, prepared, designed, developed or adapted -- "this
# role-play was written by ...".
_TEACHING_MATERIAL = re.compile(
    rf"\bthis\s+(?:{_KIND_NOUNS})\b.{{0,80}}?\b(?:was|is|were|are)\s+"
    rf"(?:{_WRITING_VERBS})\b",
    re.IGNORECASE,
)

# Category 2: simplified, adapted or fictionalised for a teaching purpose --
# independent of category 1's kind noun, since a document may say this of
# itself without naming what kind of material it is.
_SIMPLIFIED_FOR_TEACHING = re.compile(
    r"\b(?:simplified|adapted|fictionalised|fictionalized)\b.{0,80}?\bfor\b"
    r".{0,40}?\b(?:educational|teaching|classroom|training|illustrative)"
    r"\s+purposes\b",
    re.IGNORECASE,
)

# Category 3: based on an encyclopedia's or a chatbot's content.
_BASED_ON_RELAY = re.compile(
    r"\bbased on\b.{0,80}?\b(?:an?\s+)?"
    r"(?:encyclopedia(?:'s)?(?:\s+entries)?|wikipedia|chatgpt|chatbot|ai|"
    r"a language model)\b",
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

_DECLARATION_PATTERNS = (
    _TEACHING_MATERIAL,
    _SIMPLIFIED_FOR_TEACHING,
    _BASED_ON_RELAY,
    _AI_WRITTEN,
)


def derivative_self_description(read: ReadRecord) -> str | None:
    """The document's own sentence declaring it derived or teaching content.

    Scans the read's passages joined in document order -- so a sentence a
    page's own extraction split across two chunks is still whole -- and
    returns the first sentence in which the document declares itself one
    of: teaching or exercise material written, prepared, designed,
    developed or adapted as such; simplified, adapted or fictionalised for
    an educational, teaching, classroom, training or illustrative purpose;
    based on an encyclopedia's or a chatbot's content; or AI-written by its
    own words.

    Verbatim, cut at 300 characters on a word boundary. ``None`` when the
    document makes no such declaration.
    """
    text = " ".join(" ".join(passage.split()) for passage in read.passages.values())
    for sentence in _sentences(text):
        if any(pattern.search(sentence) for pattern in _DECLARATION_PATTERNS):
            return _clamped(sentence)
    return None


def _sentences(text: str) -> list[str]:
    """Every sentence of ``text``, in order, stripped of outer whitespace."""
    sentences: list[str] = []
    start = 0
    for boundary in _SENTENCE_BOUNDARY.finditer(text):
        sentences.append(text[start : boundary.start()].strip())
        start = boundary.end()
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
