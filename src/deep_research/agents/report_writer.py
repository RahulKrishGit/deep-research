"""The Report Writer: the parallel writer.

One call per plan sub-topic ("part"), each request listing only that part's
own verified findings; each part's Statement Check starts the moment its own
draft returns, sharing one semaphore (``agents.verifier_concurrency``) with
every other part's batches and the bottom line's; the bottom line is written
last, fed only the checked section statements; a redraft re-asks only the
parts a material defect names, and carries every other part over unchanged.
Code keeps only the mechanical rules -- a point cites at least one known
label belonging to its part, is not built only from context-only findings,
names its subject, and fits the length and count shape. Every other question
about a sentence's wording -- its numbers, dates, scope, organisation,
forecast or actual -- is judged once for every drafted sentence by the
Statement Check. The assembly (the table, page credits and unreachable pages)
runs after every part and the bottom line finish, driven by the frozen
``answer_kind``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import Field, JsonValue, ValidationError

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_EFFORT,
    AgentCompleter,
    AgentRun,
    BaseAgent,
)
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event, publish_live
from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.planner import (
    Clock,
    answer_form_requirement,
    reader_answer_lines,
    utc_now,
)
from deep_research.agents.prompts import (
    AgentTask,
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.reader_notes import (
    WRITING_NOTES,
    render_reader_notes,
    steering_notes,
)
from deep_research.agents.report import (
    _carried_rows,
    _figure_label_for,
    render_finding_log,
    render_written_report,
    report_as_of,
    report_scope,
    written_citations,
)
from deep_research.agents.report_table import build_table as _build_table
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.verified_facts import (
    answered_target_ids,
    answers_by_fallback,
    citable_findings,
    claimed_organisation,
    fact_rows,
    finding_answers,
    not_found_targets,
)
from deep_research.agents.wording import stated_role
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderConfigurationError,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
)
from deep_research.tools.base import BaseTool, ToolError, ToolResult
from deep_research.tools.memory_tools import SaveToMemoryTool
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_LABEL_PREFIX,
    AcquisitionState,
    AnswerKind,
    BottomLineDraft,
    BottomLineLayout,
    BottomLineTopic,
    ContractModel,
    EvidenceTarget,
    FactRow,
    Finding,
    ItemMark,
    ItemMarkDraft,
    NotFoundTarget,
    PageCredit,
    ReaderAnswer,
    ReadRecord,
    RejectedDraftPoint,
    ReportComposition,
    ReportPart,
    ReportPoint,
    ReportReview,
    ReportSection,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ReviewDefect,
    ScoredSource,
    SectionDraft,
    SubTopic,
    TopicLineDraft,
    UnreachablePage,
    WriterPointDraft,
    active_reader_notes,
    note_label,
)

REPORT_WRITER_NAME = "report_writer"

# No strong limits: quality and latency come first over every artificial
# content cap. Every plan part gets its section (one call per part, no part
# dropped for count), and there is no cap on points per section or findings
# per part. MAX_ANSWER_SENTENCES (2) is the top of the bottom line's direct
# answer, one or two sentences, not a truncation of content: the bottom
# line's topic lines have no count cap.
# _SECTION_TITLE_CHARS is 80 (a drafted title prints raw and becomes an
# options-table column header, so a wider backstop would be too loose) and
# its cut is a word boundary, never mid-word; a title carrying a
# digit/quantity or a verdict word falls back to the sub-topic's own title
# instead of printing unchecked.
MAX_POINT_CHARS = 1200
MAX_POINT_WORDS = 60
MAX_ANSWER_SENTENCES = 2
MAX_BOTTOM_LINE_SENTENCE_WORDS = 60
_SECTION_TITLE_CHARS = 80
#: A section's short title: 1-3 words, at
#: most 24 characters, no digit and no verdict word, else the title stands in.
_SHORT_TITLE_WORDS = 3
_SHORT_TITLE_CHARS = 24
#: A bottom-line request that lists no topic: no topic line is kept.
_NO_TOPICS: Mapping[str, frozenset[str]] = {}
_MARK_SPAN_CHARS = 80
CONTEXT_ONLY_RELEVANCE = 0.5
DEFAULT_WRITER_AUTHORITY_FLOOR = 0.4
#: A safe, shared empty default for ``_bottom_line_fallback``'s
#: ``label_by_finding_id`` -- never mutated, so one shared instance is fine.
_EMPTY_MAPPING: Mapping[str, str] = {}
# Every part starts at once: 10 is at least the part count, and the target
# provider allows far higher concurrency -- see ``utils/config.py``'s
# ``writer_section_concurrency`` for the shipped value. This is only the
# fallback a caller with no configured value gets.
DEFAULT_WRITER_SECTION_CONCURRENCY = 10
# One defect's own sentence as the re-draft's request carries it: the whole
# sentence, not a clipped one -- the redraft has to see what is wrong in full.
_DEFECT_PROBLEM_CHARS = 2000
# The first attempt runs at the resolved profile's effort (config.yaml
# model_overrides.report_writer, the one effort source); a truncated draft is
# asked once more at high, the retry this writer's own call makes.
_WRITER_ATTEMPT_EFFORTS: tuple[str | None, ...] = (None, OUTPUT_LIMIT_RETRY_EFFORT)

# Fallbacks used when a caller passes no explicit bound, mirroring the
# evidence_verifier module constants of the same shape.
_CHECK_BATCH_SIZE_DEFAULT = 5
_CHECK_CONCURRENCY_DEFAULT = 8


# --- the section call's prompt contract ---------------------------------------

SECTION_SYSTEM_PROMPT = (
    "You write one section of a research report: the part of the question "
    "named in this request, from the verified findings listed for that part "
    "only. Code builds the table, the list of what could not be confirmed "
    "and the sources, and another request writes the bottom line from the "
    "checked sections. Every finding you may cite is listed with a label "
    "(F01, F02, ...), its verbatim snippet, and each of its figures with the "
    "figure's verified period, kind (actual or forecast), organisation and "
    "reader label. A finding also shows the researcher's own content, used "
    "only to identify what the snippet is about, and a finding with no "
    "figure shows the page passage around its snippet: take a judgement's "
    "subject from them, never from the snippet alone. A snippet is verbatim "
    "and may end mid-clause. A finding with no figure is listed with its "
    "snippet and the body the line attributes it to, and a host is where a "
    "statement was read, never the body that made it: when a page "
    "reproduces a document, state what that document provides, and keep the "
    "host to where it was read. Every sentence you write is judged against "
    "the findings it cites and may be corrected or refused."
)

SECTION_INSTRUCTION = (
    "Rules:\n"
    "- Cite by label only: every point lists in finding_labels the labels it rests on. "
    "Never write a URL.\n"
    "- A point's text is reader prose: never a finding label (F01, F02, ...), a URL, "
    "or a label's own words; labels go only in finding_labels.\n"
    "- State a forecast with a forecast verb (\"projects\", \"expects\", \"forecasts\"), never "
    "as a completed outcome, and an actual as a reported outcome with its period, never "
    "with a forecast verb.\n"
    "- A source's prediction or general theory (a statement its page makes in the "
    "future tense, or about what happens in general rather than about the case the "
    "question asks about) is stated as that source's prediction or theory, with the "
    "date the finding gives if it gives one, never as an account of what happened.\n"
    "- Use only the numbers and dates of the cited findings, and keep the finding's own "
    "qualifier with the number it qualifies (\"nearly\", \"more than\", \"about\").\n"
    "- Use the scope words the finding states, never the question's.\n"
    "- Report only the part of a snippet that is complete. A snippet is verbatim and may "
    "end mid-clause, so never supply an object, a condition or an ending it does not "
    "carry, and never state a cut rule as if it were whole.\n"
    "- Name only organisations and publications the cited findings name.\n"
    "- A figure belongs to the subject its finding names (a product, a place, a version): "
    "never move a figure from one subject to another, and name the subject as the finding "
    "names it.\n"
    "- Credit what you write the way the finding's label or line decides, in your own "
    "words: a figure the label credits to the finding's organisation is that "
    "organisation's; a relayed figure or statement is \"according to <organisation>, as "
    "reported by <site>\"; a figure the label does not attribute is stated as what the "
    "page says, named as the page names its own publisher, or by its host when the page "
    "names no publisher. A host is where a statement was read, never the body that made "
    "it. Never print a label's own words (\"own figure\", \"not stated\", \"does not "
    "attribute it\") in a point.\n"
    "- When a snippet or passage shows the page quoting a named author or work "
    "(quotation marks with the author named, a footnote or a parenthetical citation), "
    "credit that author or work as the passage names it, as reported by the page, even "
    "where the finding's line says only where it was read: \"according to Example "
    "Author, as quoted by example-register.test\". A page quoting someone is not the "
    "originator of the words. Never supply a name the passage does not give: quoted "
    "words with no name in the snippet or passage are \"a passage the page quotes\", "
    "named by the page's host and by no author.\n"
    "- When the source line or the self-description line describes the page's kind "
    "(a student paper, a class assignment, a teaching or role-play document, a "
    "simplified or adapted rendering, content based on an encyclopedia's or a "
    "chatbot's entries, an enthusiast site, a blog post, a reader comment, a podcast "
    "or course description), say so when you credit it, in that line's own words; "
    "never a kind neither line states: \"a student paper read at example.edu "
    "states …\", \"a role-play read at example.edu states …\".\n"
    "- Every judgement, ranking or recommendation is attributed to the source that made "
    "it, as the finding names it; where findings disagree, state each; never a pick, "
    "ranking, verdict or criterion of your own.\n"
    "- When the listed findings show that sources disagree about the answer (competing "
    "explanations, or a step one source states and another disputes or qualifies), "
    "state that disagreement in the first points for the target it concerns, before "
    "that target's other facts (required targets still come first, in the listed "
    "order): who holds which view, and where they agree. A point that states it "
    "cites the findings on each side and sets disputes to true (the point's own "
    "boolean field). A point that only reports a variance in an incidental date, or "
    "that only gives the date of a step, keeps disputes false.\n"
    "- When two findings give different values or dates for the same thing, state both "
    "in one point, say that they differ, and, where a cited finding shows it, name "
    "which source dates its value or names the document it rests on. Only values or "
    "dates of the same measured thing differ: a period and an event, or two different "
    "measures, are not a disagreement. Never say sources differ when they state "
    "different things. Such a point sets disputes to true only when the differing "
    "value or date is what the question itself asks for; a variance in an incidental "
    "date keeps disputes false.\n"
    "- When two findings state opposite judgements of the same thing, state both in "
    "one point, say they differ, and, where a cited finding shows it, name which one "
    "is dated later or which a cited finding's page calls the current view. Never "
    "pick one yourself.\n"
    "- Within the point budget this request states, state each distinct fact the "
    "listed findings carry for this part's targets once, in one point citing together "
    "every finding that states it, required targets first in the listed order, each in "
    "the form its evidence takes: a figure with its period and its organisation; a "
    "forecast with its issuer and release; items with their attributes, grouped or "
    "ordered on a basis the question or the findings give, and stated as the findings "
    "state them; reasons, mechanisms or provisions as the cited findings state them, "
    "and, for a mechanism, its last step states the outcome the question's subject "
    "reached, where a cited finding states that outcome, with its date where that "
    "finding gives it; a finding that dates the outcome, or the whole span the "
    "question's subject ran through, dates the last step. "
    "State an optional target's answer only where it adds a fact the required targets' "
    "points do not carry. Every required target a listed finding answers is still "
    "stated by at least one point citing a finding that answers it; a point never "
    "announces an absence of its own -- an unanswered target is code's to disclose, "
    "not yours. A target marked \"(through its sub-topic only)\" is answered by a "
    "finding matched to its sub-topic, not bound to that target explicitly; state it "
    "the same as any other answer.\n"
    "- In a part answering why or how, a point states a cause, a step of the "
    "mechanism, or a dispute about one; a count, a price, a variant account of an "
    "incidental detail, or a description of a work that no required target asks for "
    "is context, stated only where a step turns on it. The point that states the "
    "outcome the question's subject reached (the mechanism's last step, dated as "
    "the rule above says) sets outcome to true (the point's own boolean field); "
    "every other point keeps outcome false.\n"
    "- When several findings state the same fact, state it once and credit the "
    "sources together (\"Example Institute, example-register.test and Example News "
    "state that ...\", citing all their labels), never one clause per source; each "
    "source keeps the credit its own line decides, so a relay among them stays "
    "\"according to <organisation>, as reported by <site>\".\n"
    "- Except that when a weaker and a stronger source state the same fact, as their "
    "source lines describe them (a student paper, a blog post or an enthusiast site "
    "beside an institute, a journal or an official body), cite the stronger and leave "
    "the weaker out of that point -- the one case where a finding that states the "
    "fact is not cited; the weaker source's own distinct fact may still be stated, "
    "with its kind named when its source line names one.\n"
    f"- Keep every point under {MAX_POINT_CHARS} characters and under "
    f"{MAX_POINT_WORDS} words. A longer point is split at a sentence boundary and "
    "every piece kept with the same citations, so a sentence that long on its own "
    "is refused: write one fact per point.\n"
    "- A section title names the part of the question this section answers, in the "
    "question's own words where it has them: at most eight words, never a judgement or "
    "a status.\n"
    "- short_title names the same part in one to three words for a contents list "
    "(\"Published picks\", \"Opening hours\"): no number, no judgement, at most 24 "
    "characters.\n"
    "- Never print a page's housekeeping as a point: a copyright, revision or legal "
    "line, or a disclaimer, belongs to the evidence log and answers no question. An "
    "effective date is not housekeeping when the question asks when something "
    "applies: state it as the finding states it.\n"
    "- Never state a judgement while dropping the criterion it is measured by: a "
    "judgement the finding measures by a criterion the snippet does not name is "
    "not an answer, so state the criterion with it or leave the judgement out. The "
    "same rule covers a pick made on value or for the price and the newer or "
    "alternative item the page names beside that pick: state both with the pick or "
    "leave the judgement out.\n"
    "- Every judgement, pick or verdict names the item it is about, exactly as the "
    "finding's content or passage names it: never a bare pronoun (\"it\", \"this\", "
    "\"these\") or an unnamed reference (\"the model\", \"the device\"). Such a point is "
    "refused.\n"
    "- Name a person, place or period as the finding's passage identifies it (a title "
    "or family name shared by several people is given with the name and year the "
    "passage uses); never leave \"that followed\" or \"at that time\" without the event "
    "or year the passage gives.\n"
    "- A point never rests only on findings listed under \"# Context only\": they are "
    "background, not citable evidence.\n"
    "- Mark each option (a product, place, service or other thing the question asks to "
    "choose among or compare) your point is about, in items: name exactly as your "
    "point's text writes it, and the same way every time it recurs; verdict the "
    "shortest span of your point's text giving that source's verdict, score or price "
    "for it, including the criterion the sentence states it by, at most "
    f"{_MARK_SPAN_CHARS} characters (a longer span drops the mark); picked true when the "
    "finding reports a recommendation, pick or first-place ranking by the body it "
    "attributes -- the page itself, or a named organisation the page reports; by the "
    "label of the finding whose page reports the recommendation, required when the "
    "point cites more than one site. Leave items empty for a point about no option.\n"
    "- On a redraft, return your previous section with only the edits the listed "
    "defects need; keep every other point word for word.\n"
    "- Never use a verdict or corroboration word (verified, confirmed, corroborated, "
    "independently, insufficient evidence, contested) as the report's own assessment: "
    "inside a finding's own attributed statement, use the words the finding uses.\n"
    "- Never state the same figure twice."
)

_SECTION_REPLY_EXAMPLES = (
    (
        "Example input: ## F01: Example Register entry (example-register.test) | "
        "content: The outreach program closed in 2020 after its funding ended. | "
        "snippet: the outreach program closed in 2020. | F01 | statement | read at "
        "example-register.test | actual",
        '{"title":"Programme closure","short_title":"Closure","points":[{"text":'
        '"Example Register records that the outreach program closed in 2020.",'
        '"finding_labels":["F01"],"disputes":false,"outcome":true,"items":[]}]}',
    ),
    (
        "Example input: ## F02: Example Register entry (example-register.test) | "
        "content: Model B, a compact model, is the one to beat for the price. | "
        "snippet: it is the one to beat for the price. | passage: Model B, a compact "
        "model, is the one to beat for the price. | F02 | statement | read at "
        "example-register.test | actual ## F03: Example Tester's verdict "
        "(example-tester.test) | content: Example Tester names Model C, not Model "
        "B, the one to beat for the price. | snippet: Model C, not Model B, is the "
        "one to beat for the price. | F03 | statement | read at example-tester.test "
        "| actual ## F04: Example Tester's review (example-tester.test) | content: "
        "Example Tester's own lab measured Model A at a noise rating of 4.5 out of "
        "5. | snippet: Example Tester gives Model A a noise rating of 4.5 out of "
        "5. | F04 | figure 1: 4.5 out of 5 | subject Model A | period 2026 | kind "
        "actual | organisation Example Tester | label: Example Tester's own figure; "
        "actual",
        '{"title":"Value for money","short_title":"Value for money","points":'
        '[{"text":"example-register.test says '
        'Model B is the one to beat for the price, while Example Tester says Model '
        'C is the one to beat for the price; the two differ.","finding_labels":'
        '["F02","F03"],"disputes":true,"outcome":false,"items":[{"name":"Model B",'
        '"verdict":"the one to beat for the price","picked":true,"by":"F02"},'
        '{"name":"Model C","verdict":"the one to beat for the price","picked":true,'
        '"by":"F03"}]},{"text":"Example Tester gives Model A a noise rating of 4.5 '
        'out of 5.","finding_labels":["F04"],"disputes":false,"outcome":false,'
        '"items":[{"name":"Model A","verdict":"a noise rating of 4.5 out of 5",'
        '"picked":false,"by":"F04"}]}]}',
    ),
)


# --- the bottom-line call's prompt contract -----------------------------------

BOTTOM_LINE_SYSTEM_PROMPT = (
    "Write the bottom line from the checked statements listed: first a direct answer "
    "to the question in one or two sentences, then one line for each topic listed, in "
    "the listed order. Each statement was checked against the findings it cites; "
    "state nothing they do not."
)

BOTTOM_LINE_INSTRUCTION = (
    "Rules:\n"
    "- sentences: one or two sentences that answer the question directly, the most "
    "direct answer first. When # Reader answers is listed, give the answer the form "
    "those answers ask for \u2014 how many options, which area, for what purpose \u2014 "
    "naming only options, figures and picks the listed statements carry, each credited "
    "as its statement credits it; the reader's answers narrow what is answered, never "
    "what a statement says.\n"
    "- topics: one line per topic listed under # Checked statements, in the listed "
    "order, with topic set to the id at the start of that topic's heading. The line "
    "states the fact from that topic's own statements that best answers the question; "
    "when the answer sentences already state that fact and the topic has another that "
    "bears on the question, it states that one instead. It cites only labels that "
    "topic's own statements cite. Leave a topic out only when none of its statements "
    "bears on the question.\n"
    "- For a question whose answer form is a causal mechanism (one asking why or how "
    "something happened or works), give the mechanism as ordered steps within the "
    "answer's sentences: each step states a cause, its effect and the sources that "
    "state it, in order, and a sentence carries one step or consecutive steps; the "
    "last step states the outcome the question's subject reached, as a listed "
    "statement states it and dated where that statement dates it -- never an "
    "outcome no listed statement carries. Where several listed statements state "
    "the same step, say so by naming them and cite them all -- write \"X, Y and Z "
    "all state "
    "that …\", never \"X states …, while Y states …\"; agreement among the findings "
    "is a fact of the findings, not a verdict of your own.\n"
    "- When the checked statements dispute a step, a figure or a provision (one "
    "states it, another disputes or qualifies it, or gives a different value or "
    "date for the same measured thing), state both and say they differ; never "
    "state a disputed one as settled. State a dispute in the bottom line only "
    "when it concerns a step of the mechanism or the answer's own value; a "
    "variance in an incidental date belongs in its section.\n"
    "- Cite by label only: every sentence lists in finding_labels the labels it "
    "rests on, and every label must be one the listed statements cite -- never a "
    "label a listed statement does not carry.\n"
    "- A sentence's text is reader prose: never a finding label (F01, F02, ...), a "
    "URL, or a label's own words; labels go only in finding_labels.\n"
    "- Credit every figure, judgement, pick or ranking to its source exactly as the "
    "statement credits it, keeping \"according to <organisation>, as reported by "
    "<site>\" where the statement has it.\n"
    "- For a question asking which option is best, say which option each source "
    "picks and by what criterion, giving each source's pick where the sources "
    "differ, and never a pick, ranking or criterion of your own.\n"
    "- A forecast keeps its issuer, its release and a forecast verb.\n"
    "- Keep the qualifiers and the criteria the statements state.\n"
    "- Never state a page's own date (the sources list prints it); a forecast's "
    "release is not a page date and stays. Never announce an absence, never list "
    "every option (the table does), and never copy a statement word for word.\n"
    "- Every judgement, pick or verdict names the item it is about, exactly as the "
    "statement names it: never a bare pronoun or an unnamed reference. Such a "
    "sentence is refused.\n"
    "- Never refer to the question's subject by a pronoun or possessive (\"it\", "
    "\"its\", \"they\", \"their\"): name the subject in full every time, even where "
    "the same sentence named it a clause before -- a condensed clause loses its "
    "antecedent.\n"
    "- Name a person, place or period as the checked statement identifies it (a "
    "title or family name shared by several people keeps the name and year the "
    "statement gives); never leave \"that followed\" or \"at that time\" without "
    "the event or year the statement gives, and never add a name, event or year "
    "the statements do not carry.\n"
    "- Mark each option your sentence is about, in items, the same way a section "
    "does: name, verdict, picked, and by when the sentence cites more than one "
    "site.\n"
    f"- Keep every sentence under {MAX_POINT_CHARS} characters.\n"
    f"- Keep every sentence under {MAX_BOTTOM_LINE_SENTENCE_WORDS} words.\n"
    "- On a redraft, minimal edits: return your previous bottom line with only the "
    "edits the listed defects need."
)

_BOTTOM_LINE_REPLY_EXAMPLES = (
    (
        "Example input: # Reader answers - How many picks do you want? Just one (the "
        "reader's answer) # Checked statements ## topic-01 \u00b7 Noise ratings - Example "
        "Tester gives Model A a noise rating of 4.5 out of 5. (cites F01; options: Model "
        "A) ## topic-02 \u00b7 Value for money - Example Register says Model B is the one "
        "to beat for the price. (cites F02; options: Model B [picked])",
        '{"sentences":[{"text":"Example Register picks Model B as the one to beat for '
        'the price.","finding_labels":["F02"],"items":[{"name":"Model B","verdict":"the '
        'one to beat for the price","picked":true,"by":"F02"}]}],"topics":[{"topic":'
        '"topic-01","text":"Example Tester rates Model A 4.5 out of 5 for noise.",'
        '"finding_labels":["F01"],"items":[{"name":"Model A","verdict":"4.5 out of 5 '
        'for noise","picked":false,"by":"F01"}]},{"topic":"topic-02","text":"Example '
        'Register says Model B is the one to beat for the price.","finding_labels":'
        '["F02"],"items":[{"name":"Model B","verdict":"the one to beat for the price",'
        '"picked":true,"by":"F02"}]}]}',
    ),
    (
        "Example input: # Checked statements ## topic-01 \u00b7 Why the program closed - "
        "Example Institute reports that a 2018 funding cut reduced the outreach budget. "
        "(cites F04) - Example Register states that the reduced budget forced staff "
        "reductions through 2019. (cites F05) ## topic-02 \u00b7 When it closed - Example "
        "Register records that the outreach program closed in 2020 after its funding "
        "ended. (cites F06) # Outcome - Example Register records that the outreach "
        "program closed in 2020 after its funding ended. (cites F06)",
        '{"sentences":[{"text":"According to Example Institute, a 2018 funding cut '
        'reduced the outreach budget, and the program closed in 2020 after its funding '
        'ended, Example Register records.","finding_labels":["F04","F06"],"items":[]}],'
        '"topics":[{"topic":"topic-01","text":"Example Register states that the reduced '
        'budget forced staff reductions through 2019.","finding_labels":["F05"],'
        '"items":[]},{"topic":"topic-02","text":"The outreach program closed in 2020 '
        'after its funding ended, Example Register records.","finding_labels":["F06"],'
        '"items":[]}]}',
    ),
)


class ReportWriterTask(AgentTask):
    session_id: str
    iteration: int = 0
    max_extra_passes: int = 0
    question: str
    as_of: str = ""
    scope: str = ""
    generated_on: str = ""
    answer_kind: AnswerKind | None = None
    """The frozen contract's answer form, printed as ``Answer form:
    {answer_form_requirement(kind)}`` to both writer calls; ``None`` prints
    "not classified" (a legacy or contract-less run)."""
    sub_topics: list[SubTopic] = Field(default_factory=list)
    targets: list[EvidenceTarget] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)       # the whole verified snapshot
    sources: list[ScoredSource] = Field(default_factory=list)
    registry: list[tuple[str, Finding]] = Field(default_factory=list)
    facts: list[FactRow] = Field(default_factory=list)
    not_found: list[NotFoundTarget] = Field(default_factory=list)
    answered: dict[str, list[str]] = Field(default_factory=dict)
    reads: dict[str, ReadRecord] = Field(default_factory=dict)
    """Read id -> the page it read, so the Statement Check can see each cited
    finding's bounded passage. Empty for a caller with no reads
    in hand, which shows the snippet alone."""
    passages: dict[str, str] = Field(default_factory=dict)
    """Finding id -> its bounded registry passage, computed once
    at task-build time for the whole registry -- not only for cited findings."""
    self_descriptions: dict[str, str] = Field(default_factory=dict)
    """Read id -> the read's own ``derivative_self_description``, computed
    once per read at task-build time -- several findings can share one read,
    so this is keyed by read id, not finding id."""
    defects: list[ReviewDefect] = Field(default_factory=list)
    """The material defects this draft must answer, in the review's own order.

    Empty for a first draft. Non-empty means the graph bought this writer
    re-run *for* these defects: the run already composed a report, the terminal
    review scored it and named what is materially wrong, and no research pass
    can fix it from the same evidence -- so each defect is routed to the parts
    (or the bottom line) its statement or target ids name.
    """
    previous: ReportComposition | None = None
    """The prior pass's composition, read only on a redraft — a part with no
    routed defect is carried over from here unchanged — and after
    a note pass, when every part but the notes' own is."""
    note_pass_coverage_ids: list[str] = Field(default_factory=list)
    """The reader notes' own parts a note pass researched: after a note pass
    only these parts — and a part with no previous section — and the bottom
    line are drafted, and every other part is carried over from ``previous``
    unchanged; ``[]`` otherwise."""
    acquisition_state_by_target: dict[str, AcquisitionState] = Field(default_factory=dict)
    """Keyed by ``coverage_id`` (the field name is the type's own historical
    name; every caller in this codebase keys it by sub-topic). The
    ``unreachable`` list is built from each required sub-topic's own
    ``denied_urls`` and ``candidate_records`` here."""
    target_words: int = 2000
    """The reader-length point budget's word count -- the frozen
    contract's own ``requested_word_limit`` when the question asked for
    one, else ``agents.report_target_words``."""
    reader_notes: str = ""
    """The rendered reader-notes block, printed as
    ``# Reader notes`` in every section and bottom-line request; ``""`` for a
    run without notes, whose requests then carry no notes block."""
    reader_answers: list[ReaderAnswer] = Field(default_factory=list)
    """The reader's answers to the one-time check (``state.reader_answers``),
    printed as ``# Reader answers`` in the bottom-line request so its direct
    answer takes the form they ask for; ``[]``
    when the check asked nothing."""
    note_labels: dict[str, str] = Field(default_factory=dict)
    """``note-{note_id}`` -> ``Your note · {short}`` for every active reader note:
    the label of a note's topic line, and what
    orders note topics after the plan's, by receipt."""
    authority_floor: float = DEFAULT_WRITER_AUTHORITY_FLOOR
    """``agents.writer_authority_floor`` -- the bottom line's own
    per-statement floor filter (``_statement_meets_authority_floor``): a
    checked statement resting only on below-floor findings is dropped from
    the bottom line's citable pool once an above-floor statement exists
    elsewhere. ``is_context_only`` does not read it."""


class WrittenReport(ContractModel):
    markdown: str
    evidence_markdown: str
    composition: ReportComposition
    statement_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    refused_count: int = Field(ge=0)


def finding_registry(findings: Sequence[Finding], targets: Sequence[EvidenceTarget],
                     sub_topics: Sequence[SubTopic] = (),
                     sources: Sequence[ScoredSource] = ()) -> list[tuple[str, Finding]]:
    """One label per citable finding: answers to required targets first, then
    the rest. Within each group, its source's own ``authority_score``,
    descending, with a missing score last -- so the strongest
    sources of a target get the first labels, and a weaker source never
    outranks the target's own best evidence merely by extracting first.
    Within an authority tie (missing or equal scores are common --
    every score is missing when ``sources`` is empty, and evaluators often
    give several sources the same value), the required group falls
    back to the targets' own plan order, so two required targets' answers
    are not interleaved by citable-list order.

    The answers are resolved against every target, so an optional sibling
    still keeps a figure about its subject off a required target's answers,
    and only the required targets' answers are then ranked first. The
    plan resolves an unbound extraction's own sub-topic, so a
    finding that answers a required obligation that way ranks with the rest.
    """
    citable = citable_findings(findings)
    required = {t.target_id for t in targets if t.required}
    answered = {t: ids for t, ids in answered_target_ids(
        citable, targets, sub_topics=sub_topics).items() if t in required}
    first = list(dict.fromkeys(fid for ids in answered.values() for fid in ids))
    group = dict.fromkeys(first, 0)
    rank = {fid: n for n, fid in enumerate(first)}
    src_by_url = sources_by_url(sources)

    def sort_key(finding: Finding) -> tuple[int, int, float, int]:
        source = src_by_url.get(normalize_source_url(finding.source_url))
        score = source.authority_score if source is not None else None
        return (
            group.get(finding_fingerprint(finding), 1),
            0 if score is not None else 1,
            -score if score is not None else 0.0,
            rank.get(finding_fingerprint(finding), len(rank)),
        )

    ordered = sorted(citable, key=sort_key)
    return [(f"F{n:02d}", finding) for n, finding in enumerate(ordered, start=1)]


def statement_passages(findings: Sequence[Finding],
                       reads: Mapping[str, ReadRecord]) -> dict[str, str]:
    """Finding id -> the bounded passage of the page it was read from.

    A snippet is cut at its passage's boundary, so the condition, exception or
    object a reported rule attaches to can sit just past the cut and a sentence
    that drops it looks supported. ``context_passage`` is the same bounded
    window the Context Check judged the figure in; a finding whose read this
    task does not carry contributes nothing, and the block shows its snippet
    alone.
    """
    # Imported at call time for the same reason the checker is: the unit tests
    # substitute the checker's own names, and nothing here should bind before
    # that substitution can be seen.
    from deep_research.agents.evidence_verifier import context_passage

    passages: dict[str, str] = {}
    for finding in findings:
        read = reads.get(finding.read_id)
        if read is None:
            continue
        passages[finding_fingerprint(finding)] = context_passage(
            read, finding.locator, finding.snippet
        )
    return passages


def _read_self_descriptions(reads: Mapping[str, ReadRecord]) -> dict[str, str]:
    """Read id -> the read's own declaration that it is derivative or
    teaching content, computed once per read, not per finding --
    several findings can share one read.
    """
    # Imported at call time for the same reason ``context_passage`` is
    # above: nothing here should bind before a test's own substitution can
    # be seen.
    from deep_research.agents.document_kind import derivative_self_description

    descriptions: dict[str, str] = {}
    for read_id, read in reads.items():
        description = derivative_self_description(read)
        if description:
            descriptions[read_id] = description
    return descriptions


_RATIONALE_CHARS = 120
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
#: A token abutting the split point that never ends a sentence on its
#: own -- a single letter ("U" in "U.S.") or a known abbreviation. Checked
#: on the whole token immediately before the break, periods and all.
_RATIONALE_ABBREVIATIONS = {
    "u.s.", "u.k.", "dr.", "mr.", "mrs.", "ms.", "st.", "no.", "vs.",
    "e.g.", "i.e.", "etc.", "jr.", "sr.", "prof.", "inc.", "ltd.", "co.",
}


def _is_abbreviation_break(text: str, position: int) -> bool:
    before = text[:position].split()
    if not before:
        return False
    token = before[-1]
    bare = token.rstrip(".")
    return len(bare) <= 1 or token.lower() in _RATIONALE_ABBREVIATIONS


def _first_sentence(text: str) -> str:
    """The first real sentence break -- ``[.!?]`` followed by
    whitespace and an uppercase letter, skipping a break at a single-letter
    or known-abbreviation token ("U.S. Department" is not two sentences)."""
    for match in _SENTENCE_END.finditer(text):
        if not _is_abbreviation_break(text, match.start()):
            return text[:match.start()]
    return text


def _source_rationale_line(source: ScoredSource | None) -> str | None:
    """The Source Evaluator's own rationale, first sentence, at most
    ``_RATIONALE_CHARS`` characters -- so the writer (and through it the
    reader) learns what kind of page a weak source is, not just its host.
    ``None`` for a pipeline-generated rationale ("Cited for: ...", carrying
    no source-evaluator judgement at all)."""
    if source is None:
        return None
    rationale = source.rationale.strip()
    if not rationale or rationale.startswith("Cited for:"):
        return None
    first = _first_sentence(rationale).strip()
    if len(first) <= _RATIONALE_CHARS:
        return first
    cut = first[:_RATIONALE_CHARS]
    boundary = cut.rfind(" ")
    return (cut[:boundary] if boundary > 0 else cut).rstrip() + "…"


def registry_lines(label: str, finding: Finding,
                   passages: Mapping[str, str] | None = None,
                   sources: Mapping[str, ScoredSource] | None = None,
                   self_descriptions: Mapping[str, str] | None = None) -> list[str]:
    """One finding's registry block: header, source rationale, content,
    snippet, then its figure or statement lines.

    ``content`` is the researcher's own wording, which names the referent a
    snippet leaves as a pronoun. A finding with no kept figure also shows the
    bounded ``passage`` around its snippet (the same window the Statement
    Check reads), so the writer can name only what the checker can verify.
    """
    passages = passages or {}
    lines = [
        f"## {label}: {finding.source_title} ({publisher_identity(finding.source_url)})",
    ]
    if finding.disputes:
        # A finding the dissent re-ask returned disputes, qualifies or dates a
        # step another retained finding states -- the writer's own
        # disagreement-first rule and the bottom line's dispute guard both
        # need to see it.
        lines.append(
            "disputes: yes (the dissent re-ask returned this finding as disputing, "
            "qualifying or dating a step another finding of the run states; its "
            "snippet decides which -- a finding that only dates a step is a dating "
            "fact, not a disagreement)"
        )
    rationale_line = _source_rationale_line(
        (sources or {}).get(normalize_source_url(finding.source_url))
    )
    if rationale_line:
        lines.append(f"source: {rationale_line}")
    self_description = (self_descriptions or {}).get(finding.read_id)
    if self_description:
        # The read's own words on what it is -- a role-play, a
        # teaching case, or content based on an encyclopedia's or a
        # chatbot's entries -- reach the writer before it credits the
        # finding, so a derivative page never gets read as a primary
        # source merely because its host reads like one.
        lines.append(f"self-description: {self_description}")
    lines.extend([
        f"content: {finding.content}",
        f"snippet: {finding.snippet or finding.content}",
    ])
    number = 0
    for result in finding.verification.figure_results if finding.verification else []:
        if not result.kept or result.context is None:
            continue
        number += 1
        context = result.context
        subject = f" | subject {context.subject}" if context.subject else ""
        # An unattributed figure of a relay-shaped page has no
        # organisation to claim, and naming the page's owner here is what the
        # writer turned into "<the site> states …"; the label beside it already
        # says "source does not attribute it".
        organisation = claimed_organisation(context, finding)
        lines.append(
            f"{label} | figure {number}: {result.figure.value} {result.figure.unit}{subject} | period "
            f"{context.period or 'not stated'} | kind {context.kind}"
            + (f" | organisation {organisation}" if organisation else "")
            + f" | label: {_figure_label_for(finding, context)}"
        )
    if number == 0:
        passage = passages.get(finding_fingerprint(finding), "")
        if passage:
            lines.append(f"passage: {passage}")
        # The writer's two phrases -- "the body the
        # line attributes it to" and "a host is where a statement was
        # read" -- map to two different words here, the same two the
        # Statement Check's own block prints for the same fact. An admitted
        # issuer is ``attributed to``; a page that names nobody is ``read at``,
        # so the writer never has to guess a body from the shape of a domain.
        admitted = finding.attributed_issuer
        attribution = (
            f"attributed to {admitted}" if admitted
            else f"read at {publisher_identity(finding.source_url)}"
        )
        date = finding.statement_date or finding.release_date or finding.data_period
        lines.append(
            f"{label} | statement | {attribution} | "
            f"{stated_role(finding.snippet or finding.content)}"
            + (f" | dated {date}" if date else "")
        )
    return lines


# --- the partition ----------------------------------------------------------


@dataclass(frozen=True)
class PartPlacement:
    """One plan sub-topic's placed findings, before any writer call runs.

    Every ``sub_topics`` entry gets one of these, in plan order, even when its
    ``findings`` list is empty: an empty part makes no call.
    """

    coverage_id: str
    sub_topic_title: str
    findings: list[Finding]


def _target_answers(finding: Finding, target: EvidenceTarget,
                    targets: Sequence[EvidenceTarget], sub_topics: Sequence[SubTopic]) -> bool:
    return finding_answers(finding, target, plan_targets=targets, sub_topics=sub_topics)


def _finding_coverage_id(finding: Finding, targets: Sequence[EvidenceTarget],
                         sub_topics: Sequence[SubTopic]) -> str | None:
    """The coverage id one citable finding is placed under, or ``None``.

    Six categories, each swept over every target in plan order before the
    next is tried: the first target any category matches decides the part.
    """
    def explicit(target: EvidenceTarget) -> bool:
        return target.target_id in finding.target_ids

    for target in targets:
        if target.required and explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if target.required and not explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if not target.required and explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if not target.required and not explicit(target) and _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    for target in targets:
        if explicit(target) and not _target_answers(finding, target, targets, sub_topics):
            return target.coverage_id
    name = cosmetic_text(finding.related_sub_topic)
    for sub_topic in sub_topics:
        if cosmetic_text(sub_topic.title) == name:
            return sub_topic.coverage_id
    return None


def report_parts(
    findings: Sequence[Finding], targets: Sequence[EvidenceTarget],
    sub_topics: Sequence[SubTopic],
) -> tuple[list[PartPlacement], list[Finding]]:
    """Partition every citable finding into exactly one plan sub-topic.

    Returns one ``PartPlacement`` per sub-topic, in plan order (empty ones
    included, since an empty part still needs to be recorded as such), and
    the findings placed nowhere -- recorded in the evidence log, never
    written.
    """
    citable = citable_findings(findings)
    buckets: dict[str, list[Finding]] = {topic.coverage_id: [] for topic in sub_topics}
    unplaced: list[Finding] = []
    for finding in citable:
        coverage_id = _finding_coverage_id(finding, targets, sub_topics)
        if coverage_id is None or coverage_id not in buckets:
            unplaced.append(finding)
            continue
        buckets[coverage_id].append(finding)
    parts = [
        PartPlacement(coverage_id=topic.coverage_id, sub_topic_title=topic.title,
                     findings=buckets[topic.coverage_id])
        for topic in sub_topics
    ]
    return parts, unplaced


def sources_by_url(sources: Sequence[ScoredSource]) -> dict[str, ScoredSource]:
    """Every scored source, keyed by its normalized URL."""
    return {normalize_source_url(source.url): source for source in sources}


def finding_source_lines(
    findings: Sequence[Finding], sources: Mapping[str, ScoredSource]
) -> dict[str, str]:
    """Finding id (``finding_fingerprint``) -> its ``source:`` registry line
    when its source carries one.

    The same line ``registry_lines`` prints for the writer -- the run's own
    source evaluation of the page's kind, in that evaluation's own words --
    built once and handed to the Statement Check and the terminal review
    too, so a sentence naming a weak page's kind (the writer's own rule, "in
    the source line's own words") can be judged against what those checkers
    were actually shown, not invented against a block that never carried it.
    """
    lines: dict[str, str] = {}
    for finding in findings:
        rationale_line = _source_rationale_line(
            sources.get(normalize_source_url(finding.source_url))
        )
        if rationale_line:
            lines[finding_fingerprint(finding)] = rationale_line
    return lines


def is_context_only(finding: Finding, sources: Mapping[str, ScoredSource]) -> bool:
    """Unbound, and from a low-relevance or low-confidence source.

    Context-only findings are listed under a part's ``# Context only``
    heading rather than its verified-findings registry, and a point resting
    only on them is refused. A bound finding is never
    context-only, whatever its source's score.

    A *bound* weak-authority finding is never context-only, even when a
    stronger finding answers one of the same targets: a target-level
    comparison would erase distinct facts wholesale -- most of a part's
    findings could vanish because one stronger finding merely touched the
    same target. Citing the stronger source when both state the same
    fact is the model's own job (``SECTION_INSTRUCTION``'s "cite the
    stronger" rule), not code's to enforce by hiding the weaker finding
    entirely -- its own distinct fact stays citable. The bottom line's
    separate per-statement floor filter (``_statement_meets_authority_floor``)
    never gated a section's findings.
    """
    source = sources.get(normalize_source_url(finding.source_url))
    if source is None:
        return False
    if finding.target_ids:
        return False
    if source.low_confidence:
        return True
    return source.relevance_score is not None and source.relevance_score < CONTEXT_ONLY_RELEVANCE


# --- the section and bottom-line requests -----------------------------------


@dataclass(frozen=True)
class PartJob:
    """One part's request context, resolved once before its call runs."""

    coverage_id: str
    sub_topic_title: str
    order: int
    targets: list[EvidenceTarget]
    findings: list[Finding]
    """This part's placed, citable, non-context-only findings."""
    context_findings: list[Finding]
    """This part's placed findings that are context-only."""
    previous: ReportSection | None
    """The prior pass's section for this part, if one exists."""
    defects: list[ReviewDefect]
    """Defects routed to this part; non-empty only when ``redraft`` re-asks it."""
    redraft: bool
    """Whether this part's call runs at all: ``False`` means carried over
    unchanged from ``previous``, no call made."""
    part_weight_sum: int = 1
    """The sum of every drafted part's weight
    (``_part_weight``: 3 for a part that owns a required target, 1
    otherwise) -- the weighted reader-length point/word budget's own
    denominator, computed once across every ``PartJob`` a report builds.
    Defaults to 1 so a caller that builds one ``PartJob``
    directly (a unit test) still gets this part's own weight as the
    denominator: the whole budget, exactly as if no other part existed to
    share it with."""


# --- Writing's live progress --------------------------------------------------

#: The scope the bottom line's sentences are counted under; a part's is its coverage id.
_BOTTOM_LINE_SCOPE = "bottom_line"
_BOTTOM_LINE_SECTION = "Bottom line"


def _drafts_this_pass(job: PartJob) -> bool:
    """Whether ``_run_part`` makes a section call for this job.

    A job with no findings is empty, and one with ``redraft`` false and a
    previous section is carried over with no call; every other job,
    one with no previous section included, is drafted.
    """
    return bool(job.findings or job.context_findings) and (
        job.redraft or job.previous is None
    )


def writing_progress_event(metadata: Mapping[str, JsonValue]) -> ResearchEvent:
    """One ``report_writer.progress`` event: live-only."""
    return agent_event(
        agent_name=REPORT_WRITER_NAME,
        event_type="report_writer.progress",
        message="Writing progress.",
        metadata=metadata,
    )


class _WritingProgress:
    """One composition's running counts, published live.

    ``drafted`` maps each drafted part's coverage id -- and the bottom line's
    scope -- to the candidate keys handed to its Statement Check. A key is
    counted once, the first time a batch reports it (``backed``, ``removed``,
    or ``unchecked`` when its batch failed), so no total depends on how many
    events arrive or how sentences were batched. ``fraction``
    weighs each drafted part and the bottom line ``1/(P+1)``; a scope
    contributes its settled share once drafted, a part that returned nothing
    to check contributes in full, and the value never decreases.

    ``returned`` holds every part that has settled, written or failed, so the bar's
    arithmetic does not care how a part ended; ``failed`` is the subset that ended
    ``failed``, so the page can say how many were written. A part
    fails at one of two exits: its draft failed, or its draft returned and the
    Statement Check refused every point (or there were none), so nothing of it is
    written.
    """

    def __init__(self, parts_total: int) -> None:
        self.parts_total = parts_total
        self.phase = "sections"
        self.returned: set[str] = set()
        self.failed: set[str] = set()
        self.drafted: dict[str, frozenset[str]] = {}
        self.backed: set[str] = set()
        self.removed: set[str] = set()
        self.unchecked: set[str] = set()
        self.fraction = 0.0

    def publish(self, sample: dict[str, JsonValue] | None = None) -> None:
        settled = self.backed | self.removed | self.unchecked
        shares = [
            len(keys & settled) / len(keys) if keys else 1.0
            for keys in self.drafted.values()
        ]
        self.fraction = max(
            self.fraction, min(1.0, round(sum(shares) / (self.parts_total + 1), 3))
        )
        publish_live(writing_progress_event({
            "phase": self.phase,
            "parts_total": self.parts_total,
            "parts_returned": len(self.returned),
            "parts_failed": len(self.failed),
            "sentences_drafted": sum(len(keys) for keys in self.drafted.values()),
            "sentences_checked": len(self.backed) + len(self.removed),
            "backed": len(self.backed),
            "removed": len(self.removed),
            "unchecked": len(self.unchecked),
            "fraction": self.fraction,
            "sample": sample,
        }))

    def part_returned(
        self, coverage_id: str, keys: Sequence[str], *, failed: bool = False
    ) -> None:
        """A part's draft settled: it returned these candidate keys, or ``failed`` (then
        it has none, and ``parts_failed`` counts it)."""
        self.returned.add(coverage_id)
        if failed:
            self.failed.add(coverage_id)
        self.drafted[coverage_id] = frozenset(keys)
        self.publish()

    def part_failed(self, coverage_id: str) -> None:
        """A part whose draft returned ended ``failed`` anyway: its Statement Check
        refused every point, or there were none. It already counts in ``returned``
        (and its keys in ``drafted``), so ``failed <= returned`` and ``fraction`` stand."""
        self.failed.add(coverage_id)
        self.publish()

    def bottom_line_started(self) -> None:
        """A bottom-line call is about to start: the first attempt or the re-ask."""
        self.phase = "bottom_line"
        self.publish()

    def bottom_line_drafted(self, keys: Sequence[str]) -> None:
        """The bottom line's candidates about to be checked; a re-ask adds its own."""
        self.drafted[_BOTTOM_LINE_SCOPE] = (
            self.drafted.get(_BOTTOM_LINE_SCOPE, frozenset()) | frozenset(keys)
        )

    def count(
        self, section: str, items: Sequence[object], verdicts: Mapping[str, object]
    ) -> dict[str, JsonValue] | None:
        """Count the keys these items report for the first time; return the sample.

        The sample is the first newly counted sentence with a verdict, in batch
        order: its text (the corrected text for a ``corrected`` verdict, at most
        200 characters), ``backed`` or ``removed``, its cited findings' count and
        ``section``; ``None`` when no item had a verdict.
        """
        sample: dict[str, JsonValue] | None = None
        for item in items:
            label: str = getattr(item, "label")
            if label in self.backed or label in self.removed or label in self.unchecked:
                continue
            verdict = verdicts.get(label)
            if verdict is None:
                self.unchecked.add(label)
                continue
            kept = getattr(verdict, "verdict") in ("consistent", "corrected")
            (self.backed if kept else self.removed).add(label)
            if sample is None:
                corrected = getattr(verdict, "corrected_text", "") or ""
                text = (
                    corrected
                    if getattr(verdict, "verdict") == "corrected" and corrected.strip()
                    else getattr(item, "text")
                )
                sample = {
                    "text": summarize_text(text, limit=200),
                    "verdict": "backed" if kept else "removed",
                    "findings": len(getattr(item, "labels")),
                    "section": summarize_text(section, limit=160),
                }
        return sample

    def reporter(
        self, section: str
    ) -> Callable[[Sequence[object], Mapping[str, object]], None]:
        """The Statement Check's ``on_batch`` for one part's (or the bottom line's) check."""

        def on_batch(items: Sequence[object], verdicts: Mapping[str, object]) -> None:
            self.publish(self.count(section, items, verdicts))

        return on_batch

    def settle(
        self, section: str, items: Sequence[object], verdicts: Mapping[str, object]
    ) -> None:
        """Count, once the check returned, any key no batch reported; publish if one was."""
        before = len(self.backed) + len(self.removed) + len(self.unchecked)
        sample = self.count(section, items, verdicts)
        if len(self.backed) + len(self.removed) + len(self.unchecked) != before:
            self.publish(sample)


#: The composition in progress. ``compose_written_report`` sets it
#: before its part tasks start, so every part task, ``_check`` and each
#: bottom-line call of that composition read the same counts without a
#: parameter through the bottom-line helpers. Its value stays set for the rest
#: of the task that composed: nothing else in that task calls ``_check`` or the
#: bottom-line call, and the next composition sets its own.
_WRITING_PROGRESS: ContextVar[_WritingProgress | None] = ContextVar(
    "deep_research_writing_progress", default=None
)


def _answer_form_line(task: ReportWriterTask) -> str:
    """The frozen contract's answer-form requirement, under this module's own
    ``# Answer form`` heading -- ``_ANSWER_FORM_REQUIREMENTS``' values carry
    their own literal ``"answer form: "`` prefix for ``planner.render_answer_contract``'s
    inline use, which would otherwise double the label here."""
    if not task.answer_kind:
        return "not classified"
    requirement = answer_form_requirement(task.answer_kind)
    prefix = "answer form: "
    return requirement[len(prefix):] if requirement.lower().startswith(prefix) else requirement


def _target_line(
    target: EvidenceTarget, *, answered: Mapping[str, list[str]],
    label_by_id: Mapping[str, str], findings_by_id: Mapping[str, Finding],
    sub_topics: Sequence[SubTopic], own_finding_ids: set[str],
) -> str:
    """The target line, using only the finding ids ``# Verified findings
    for this part`` will actually list: a label placed in another part, or a
    context-only finding, must never be named as answering the target here --
    the writer would be told to cite a label the mechanical rules then refuse."""
    kind = "required" if target.required else "optional"
    finding_ids = [fid for fid in answered.get(target.target_id, []) if fid in own_finding_ids]
    labels = [label_by_id[fid] for fid in finding_ids if fid in label_by_id]
    if not labels:
        return f"- {target.target_id}: {target.question} ({kind}; no listed finding answers it)"
    # A fallback answer (no explicit binding) is labelled as such, so
    # the writer states it honestly without claiming an extraction-time bind.
    fallback_only = all(
        fid in findings_by_id and answers_by_fallback(findings_by_id[fid], target, sub_topics)
        for fid in finding_ids if fid in label_by_id
    )
    suffix = " (through its sub-topic only)" if fallback_only else ""
    return f"- {target.target_id}: {target.question} ({kind}; answered by {', '.join(labels)}{suffix})"


def _registry_block(findings: Sequence[Finding], registry: Sequence[tuple[str, Finding]],
                    passages: Mapping[str, str],
                    sources: Mapping[str, ScoredSource] | None = None,
                    self_descriptions: Mapping[str, str] | None = None) -> str:
    wanted = {finding_fingerprint(f) for f in findings}
    blocks = [
        "\n".join(registry_lines(label, finding, passages, sources, self_descriptions))
        for label, finding in registry
        if finding_fingerprint(finding) in wanted
    ]
    return "\n\n".join(blocks) if blocks else "(none)"


def _rendered_previous_section(section: ReportSection | None) -> str:
    if section is None:
        return "(none)"
    lines = [f"## {section.title}"]
    lines.extend(f"- {point.text}" for point in section.points)
    return "\n".join(lines)


def _rendered_previous_bottom_line(
    points: Sequence[ReportPoint], topics: Mapping[str, str] = _EMPTY_MAPPING,
) -> str:
    """The previous bottom line, each point prefixed with ``answer:`` or, for a
    topic line, its own ``{coverage_id}:``.
    ``topics`` maps a topic line's statement id to its coverage id."""
    return "\n".join(
        f"- {topics.get(point.statement_id, 'answer')}: {point.text}" for point in points
    ) or "(none)"


def material_defects(review: ReportReview | None) -> list[ReviewDefect]:
    """The defects one stored review says must be closed before acceptance.

    Read from the review record, never re-derived: the reviewer's own severity
    decides what is material (``ReviewDefect.material``), and an unscored review
    has no defect list to act on. This is the same reader the routing decision
    uses, so the writer is asked about exactly the defects that bought its
    re-run.
    """
    if review is None or review.status != "scored":
        return []
    return list(review.material_defects)


def _is_redraft_hop(state: ResearchState) -> bool:
    """A redraft (the same iteration re-run after review) carries the
    review's defects and the previous composition forward; a new iteration
    (an extra research pass) does not, even when a material defect is still
    on file from the iteration that bought the pass -- that review is
    against the composition the pass is about to replace with fresh
    evidence, not against this iteration's own draft, so carrying it here
    would silently apply "minimal edits" to a part that instead needs its
    new finding written from scratch.

    A reader note's loops keep the iteration and read
    as a fresh draft too: after a note pass the draft is over new evidence,
    and a note redraft drafts every part afresh with the notes. The latest
    loop marker decides.
    """
    if state.composition is None or state.composition.iteration != state.iteration:
        return False
    for event in reversed(state.events):
        if event.event_type == "graph.report.redraft_requested":
            return True
        if event.event_type in ("graph.note_pass.started", "graph.note_redraft.requested"):
            return False
    return True


# The loop markers a writer call can follow: the review's own redraft, an extra
# pass, and a reader note's two loops.
_LOOP_MARKERS = frozenset(
    {
        "graph.report.redraft_requested",
        "graph.extra_pass.started",
        "graph.note_pass.started",
        "graph.note_redraft.requested",
    }
)


def _note_pass_coverage_ids(state: ResearchState) -> list[str]:
    """The notes' own parts a note pass researched, when one is what this draft follows.

    Read from the latest loop marker. Only
    when it is ``graph.note_pass.started`` and the composition on hand is this
    iteration's does this draft carry every other part over; after any other
    loop, or with no composition to carry, ``[]``.
    """
    if state.composition is None or state.composition.iteration != state.iteration:
        return []
    marker = next(
        (event for event in reversed(state.events) if event.event_type in _LOOP_MARKERS),
        None,
    )
    if marker is None or marker.event_type != "graph.note_pass.started":
        return []
    note_ids = marker.metadata.get("note_ids")
    if not isinstance(note_ids, list):
        return []
    return [
        f"{NOTE_COVERAGE_PREFIX}{note_id}"
        for note_id in note_ids
        if isinstance(note_id, str)
    ]


def _defect_lines(defects: Sequence[ReviewDefect]) -> str:
    """One bounded line per defect: its id, kind, scope, and its own sentence.

    The sentence is the reviewer's own text; the packet spends characters on
    everything it carries, so it is still summarized to ``_DEFECT_PROBLEM_CHARS``
    -- generous enough that a redraft sees the defect whole in every case
    observed so far.
    """
    lines: list[str] = []
    for defect in defects:
        scope = ", ".join([*defect.target_ids, *defect.statement_ids]) or "the whole report"
        lines.append(
            f"- {defect.defect_id} ({defect.kind}; {scope}): "
            + summarize_text(defect.problem, limit=_DEFECT_PROBLEM_CHARS)
        )
    return "\n".join(lines)


def _required_targets_answered(job: PartJob, task: ReportWriterTask, own_finding_ids: set[str]) -> int:
    return sum(
        1 for target in job.targets
        if target.required
        and any(fid in own_finding_ids for fid in task.answered.get(target.target_id, []))
    )


def _part_weight(targets: Sequence[EvidenceTarget]) -> int:
    """A part that owns a required target pulls
    three times the reader length of a part that does not, so a required
    target's answer is never capped to the same handful of points as an
    optional part."""
    return 3 if any(target.required for target in targets) else 1


def _point_budget(task: ReportWriterTask, job: PartJob, own_finding_ids: set[str]) -> int:
    """The reader-length point budget, weighted toward the
    required target: an instruction to the model
    only -- code never truncates or drops a checked point for exceeding
    it. ``W_part`` is this part's own weighted share of the report's word
    budget: the budget times this part's weight (``_part_weight``)
    divided by the sum of every drafted part's weight
    (``job.part_weight_sum``). Points are ``max`` of the part's own
    required-target count, ``W_part`` at ~45 words per point, and 3
    (never fewer)."""
    weight = _part_weight(job.targets)
    w_part = task.target_words * weight / max(job.part_weight_sum, weight)
    return max(_required_targets_answered(job, task, own_finding_ids), round(w_part / 45), 3)


def _word_budget(task: ReportWriterTask, job: PartJob, own_finding_ids: set[str]) -> int:
    """The word budget, the same weighted share
    ``_point_budget`` uses: code refuses nothing over it;
    ``MAX_POINT_CHARS`` stays the runaway guard. Never below
    ``MAX_POINT_WORDS`` times the part's own required-target count."""
    weight = _part_weight(job.targets)
    w_part = task.target_words * weight / max(job.part_weight_sum, weight)
    floor = MAX_POINT_WORDS * _required_targets_answered(job, task, own_finding_ids)
    return max(round(w_part), floor)


def _point_budget_line(task: ReportWriterTask, job: PartJob, own_finding_ids: set[str]) -> str:
    points = _point_budget(task, job, own_finding_ids)
    words = _word_budget(task, job, own_finding_ids)
    return (
        f"Write at most {points} points for this part, about {words} words in total: "
        "the facts that best answer the question and this part's targets. The "
        "evidence log keeps every finding."
    )


def section_messages(task: ReportWriterTask, job: PartJob) -> list[ChatMessage]:
    """One part's section request: static-first, so every part
    shares a cacheable prefix."""
    label_by_id = {finding_fingerprint(f): label for label, f in task.registry}
    findings_by_id = {finding_fingerprint(f): f for f in task.findings}
    own_finding_ids = {finding_fingerprint(f) for f in job.findings}
    src_by_url = sources_by_url(task.sources)
    targets_block = "\n".join(
        _target_line(t, answered=task.answered, label_by_id=label_by_id,
                     findings_by_id=findings_by_id, sub_topics=task.sub_topics,
                     own_finding_ids=own_finding_ids)
        for t in job.targets
    ) or "(none)"
    static = [
        f"# Rules\n{SECTION_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_SECTION_REPLY_EXAMPLES),
    ]
    material = [
        f"# Question\n{task.question}",
        f"# Answer form\n{_answer_form_line(task)}",
    ]
    if task.reader_notes:
        material.append(f"# Reader notes\n{task.reader_notes}")
    material.append(
        f"# This part of the question\n{job.sub_topic_title}\n{targets_block}"
    )
    material.append(
        f"# Point budget\n{_point_budget_line(task, job, own_finding_ids)}"
    )
    if job.defects:
        material.append(f"# Your previous section\n{_rendered_previous_section(job.previous)}")
        material.append(f"# Defects to fix\n{_defect_lines(job.defects)}")
    material.append(
        f"# Verified findings for this part\n"
        f"{_registry_block(job.findings, task.registry, task.passages, src_by_url, task.self_descriptions)}"
    )
    context_block = (
        _registry_block(job.context_findings, task.registry, task.passages, src_by_url, task.self_descriptions)
        if job.context_findings else "(none)"
    )
    material.append(f"# Context only\n{context_block}")
    return [ChatMessage(role="developer", content=SECTION_SYSTEM_PROMPT),
            ChatMessage(role="user", content=render_structured_request(static, material))]


def bottom_line_messages(
    task: ReportWriterTask, sections: Sequence[ReportSection], *,
    previous: Sequence[ReportPoint] = (), previous_topics: Mapping[str, str] = _EMPTY_MAPPING,
    defects: Sequence[ReviewDefect] = (),
    disputed_statement_ids: frozenset[str] = frozenset(),
    outcome_statement_ids: frozenset[str] = frozenset(),
) -> list[ChatMessage]:
    """The bottom-line request: fed only the checked, kept section
    statements a caller passes in ``sections`` -- filtering to consistent or
    corrected verdicts is the caller's job (assembly needs the verdicts
    this function has no access to).

    ``disputed_statement_ids`` is the caller's
    own union, across every part, of the statement ids of the writer's kept,
    marked ``disputes: true`` points (``_run_bottom_line``). Label-scoped,
    not target-scoped: a required target can carry a dozen statements,
    and target scoping told the bottom line every one of them was disputed,
    the moment any one was marked. The block lists the marked points
    themselves, then every other checked statement that cites one of the
    same findings -- never an untouched statement that merely shares the
    marked points' target.

    ``outcome_statement_ids`` is the same kind of union for
    the writer's kept points marked ``outcome: true`` -- the mechanism's
    own last step. Listed under "# Outcome" so a mechanism's bottom line
    can end on it, credited and dated as the statement states it.
    """
    label_by_id = {finding_fingerprint(f): label for label, f in task.registry}
    blocks: list[str] = []
    dispute_lines: list[str] = []
    outcome_lines: list[str] = []
    marked_labels: set[str] = set()
    other_points: list[tuple[str, set[str]]] = []
    for section in sections:
        lines: list[str] = []
        for point in section.points:
            if point.statement is None:
                continue
            labels = [label_by_id[fid] for fid in point.statement.finding_ids if fid in label_by_id]
            options = "; ".join(
                f"{mark.name}{' [picked]' if mark.picked else ''}" for mark in point.statement.items
            )
            cites = f"cites {', '.join(labels)}" if labels else "cites nothing"
            suffix = f" ({cites}; options: {options})" if options else f" ({cites})"
            line = f"- {point.text}{suffix}"
            lines.append(line)
            if point.statement.statement_id in disputed_statement_ids:
                dispute_lines.append(line)
                marked_labels.update(labels)
            else:
                other_points.append((line, set(labels)))
            if point.statement.statement_id in outcome_statement_ids:
                outcome_lines.append(line)
        if lines:
            blocks.append(f"## {section.coverage_id} \u00b7 {section.title}\n" + "\n".join(lines))
    sharing_lines = [line for line, labels in other_points if labels & marked_labels]
    statements_block = "\n\n".join(blocks) if blocks else "(none)"
    static = [
        f"# Rules\n{BOTTOM_LINE_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_BOTTOM_LINE_REPLY_EXAMPLES),
    ]
    material = [
        f"# Question\n{task.question}",
        f"# Answer form\n{_answer_form_line(task)}",
    ]
    if task.reader_answers:
        material.append(
            "# Reader answers\n" + "\n".join(reader_answer_lines(task.reader_answers))
        )
    if task.reader_notes:
        material.append(f"# Reader notes\n{task.reader_notes}")
    material.append(f"# Checked statements\n{statements_block}")
    if dispute_lines:
        material.append(
            "# Disputed: state both sides, or leave the disputed step, figure or "
            "provision out\n"
            "The dispute, as a section point states it:\n"
            + "\n".join(dispute_lines)
            + "\nStatements that cite a finding the dispute cites:\n"
            + ("\n".join(sharing_lines) if sharing_lines else "(none)")
            + "\nA sentence that states what these statements dispute carries the "
            "dispute -- who states it and who disputes or qualifies it, as the "
            "point above states them -- or leaves it out; never state the "
            "disputed step, figure or provision as settled."
        )
    if outcome_lines:
        material.append(
            "# Outcome\n"
            "The outcome the question's subject reached, as a section point states "
            "it: on a mechanism answer the last step ends on one of these, credited "
            "and dated as it states it, within the answer's sentences.\n"
            + "\n".join(outcome_lines)
        )
    if previous:
        material.append(
            f"# Your previous bottom line\n{_rendered_previous_bottom_line(previous, previous_topics)}"
        )
    if defects:
        material.append(f"# Defects to fix\n{_defect_lines(list(defects))}")
    return [ChatMessage(role="developer", content=BOTTOM_LINE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=render_structured_request(static, material))]


# --- redraft routing ---------------------------------------------------------


def _route_defects(
    defects: Sequence[ReviewDefect], previous: ReportComposition | None,
    targets: Sequence[EvidenceTarget],
) -> tuple[set[str], dict[str, list[ReviewDefect]], list[ReviewDefect]]:
    """Every defect, routed by its statement ids (which part or the bottom
    line held them) and its target ids (which part owns them); a defect with
    no ids of either kind goes to every part and the bottom line."""
    all_coverage_ids = {t.coverage_id for t in targets}
    if previous is not None:
        all_coverage_ids |= {s.coverage_id for s in previous.sections if s.coverage_id}
    statement_owner: dict[str, str] = {}
    statement_in_bottom_line: set[str] = set()
    if previous is not None:
        for section in previous.sections:
            if not section.coverage_id:
                continue
            for point in section.points:
                if point.statement is not None:
                    statement_owner[point.statement.statement_id] = section.coverage_id
        for point in previous.summary:
            if point.statement is not None:
                statement_in_bottom_line.add(point.statement.statement_id)

    routed: set[str] = set()
    by_coverage: dict[str, list[ReviewDefect]] = {}
    bottom_line: list[ReviewDefect] = []
    for defect in defects:
        if not defect.statement_ids and not defect.target_ids:
            routed |= all_coverage_ids
            for coverage_id in all_coverage_ids:
                by_coverage.setdefault(coverage_id, []).append(defect)
            bottom_line.append(defect)
            continue
        ids: set[str] = set()
        goes_to_bottom_line = False
        for statement_id in defect.statement_ids:
            if statement_id in statement_in_bottom_line:
                goes_to_bottom_line = True
            owner = statement_owner.get(statement_id)
            if owner:
                ids.add(owner)
        for target_id in defect.target_ids:
            for target in targets:
                if target.target_id == target_id:
                    ids.add(target.coverage_id)
        routed |= ids
        for coverage_id in ids:
            by_coverage.setdefault(coverage_id, []).append(defect)
        if goes_to_bottom_line:
            bottom_line.append(defect)
    return routed, by_coverage, bottom_line


# --- mechanical rules; the Statement Check judges everything else -----------

#: Where a drafted point too long for ``MAX_POINT_CHARS`` may be cut: after a
#: sentence or clause end, and only where what follows starts a word. The cut is
#: verbatim -- the pieces are the drafted text's own words -- so a split never
#: invents, drops or reorders a word and every piece keeps the point's citations.
_CLAUSE_BOUNDARY = re.compile(r"(?<=[.;!?])\s+(?=[\"“(\[]?\S)")

#: The unnamed-subject guard: a judgement whose subject is a bare pronoun,
#: unquoted or inside a quotation, and that carries no option mark to name
#: the subject instead.
_UNNAMED_SUBJECT = re.compile(
    r"^[\"“'(]*\s*(it|this|that|these|those|they|he|she)\b\s+"
    r"(is|are|was|were|has|have|had|remains|offers|delivers|makes|sounds)\b",
    re.IGNORECASE,
)
_QUOTED_SPAN = re.compile(r"[\"“]([^\"”]+)[\"”]")

#: A leaked internal finding-label group in a point's own text --
#: "(F18, F28)" -- stripped together with the space before it. Deterministic
#: to detect, unlike the model's own compliance rule, and the point is never
#: refused for carrying one.
_LABEL_GROUP = re.compile(r"\s*\(\s*F\d{2,3}(?:\s*,\s*F\d{2,3})*\s*\)")


def _strip_label_groups(text: str) -> tuple[str, bool]:
    stripped = _LABEL_GROUP.sub("", text)
    return " ".join(stripped.split()), stripped != text


def _has_unnamed_subject(text: str) -> bool:
    if _UNNAMED_SUBJECT.match(text.strip()):
        return True
    return any(_UNNAMED_SUBJECT.match(match.group(1).strip()) for match in _QUOTED_SPAN.finditer(text))


#: The verbs that credit a point to someone. Matched case-sensitively --
#: these are the writer's own
#: lowercase, mid-sentence citation verbs ("Example Register states
#: that ..."); capitalised ("States", "Records") is a sentence-initial
#: common word or a proper noun ("United States"), never a credit.
#: Past-tense and plural forms are included, plus the forecast
#: verbs the section rule already asks for ("projects", "expects",
#: "forecasts") -- a forecast sentence credits its issuer through that
#: verb, not a separate one. These are the writer's own established
#: reporting verbs: any subject but a bare determiner credits (the
#: determiner rule below).
_REPORTING_VERB_WORDS = (
    "states", "state", "reports", "report", "says", "said", "writes", "wrote",
    "argues", "argued", "notes", "noted", "records", "record", "finds", "found",
    "dates", "dated", "gives", "gave", "carries", "relates", "describes",
    "projects", "expects", "forecasts", "stated", "reported", "counted",
    "estimated", "per",
)
_REPORTING_VERB_WORD = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in _REPORTING_VERB_WORDS) + r")\b"
)

#: The writer's own reply-example verbs ("Example Tester rates ...",
#: "... names Model B ..."), plus the bottom-line vocabulary the section
#: guard shares no verb with. Unlike the reporting verbs above, these are
#: ordinary action verbs any subject can take ("The data shows a steady
#: increase.", "Rome holds the record ...") -- "isn't a bare determiner" is
#: not enough, so ``_action_verb_credits`` requires the words directly
#: before the verb to actually look like a name.
_ACTION_VERB_WORDS = (
    "rates", "names", "measured", "measures", "shows", "traces", "attributes",
    "holds", "calls", "lists", "puts", "concludes", "cites", "quotes", "claims",
    "suggests", "warns",
)
_ACTION_VERB_WORD = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in _ACTION_VERB_WORDS) + r")\b"
)
#: Both tiers combined, for ``_AUTHOR_VERB_WORDS`` below: the title-author
#: construction is already gated on the exact segment name
#: sitting directly beside the verb, so it needs no separate subject rule.
_CREDIT_VERB_WORDS = _REPORTING_VERB_WORDS + _ACTION_VERB_WORDS

#: A determiner or possessive immediately before the
#: matched word makes it the noun ("The dates of the change are
#: uncertain."), not the verb -- unlike "state(s)" above, no common
#: nationality-adjective pattern applies to every word in this list,
#: so the general rule is simpler: a bare determiner directly adjacent
#: is unambiguous, but a noun between them ("the state reports ...")
#: is not touched, since a genuine subject sits there.
_DETERMINERS = frozenset({
    "the", "a", "an", "this", "that", "these", "those",
    "its", "their", "his", "her", "our", "your",
})


def _word_before(text: str, position: int) -> re.Match[str] | None:
    """The last word (and its span) ending at ``position``, if any."""
    return re.search(r"(\w+)\s*$", text[:position])


def _credit_verb_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """The first ``pattern`` match not immediately preceded by a bare
    determiner or possessive -- that word is always the noun, never the
    verb the match assumes."""
    for match in pattern.finditer(text):
        preceding = _word_before(text, match.start())
        if preceding is None or preceding.group(1).lower() not in _DETERMINERS:
            return match
    return None


#: The run of consecutive capitalised words ending
#: right before an action verb -- "Example Institute" out of "... In
#: 2024, Example Institute measured ...", stopping at the comma before
#: "2024" since a digit is never `[A-Z]`.
_ACTION_VERB_SUBJECT = re.compile(r"([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)\s*$")


def _action_verb_credits(text: str, match: re.Match[str], findings: Sequence[Finding]) -> bool:
    """Does the subject directly before this action-verb match look like
    a name? One of: a source-name phrase from ``findings``; two or more
    consecutive capitalised words ("Example Tester"); an all-caps
    acronym of two or more letters ("EIA"); or a single capitalised
    word that is not the sentence's first word (sentence-initial is as
    likely an ordinary subject, "Output measures ...", "Rome holds
    ...", as a name)."""
    subject_match = _ACTION_VERB_SUBJECT.search(text[:match.start()])
    if subject_match is None:
        return False
    subject = subject_match.group(1)
    for finding in findings:
        for phrase in _source_name_phrases(finding):
            if len(phrase) >= 3 and subject.lower() == phrase.lower():
                return True
    if len(subject.split()) >= 2:
        return True
    if re.fullmatch(r"[A-Z]{2,}", subject):
        return True
    return subject_match.start() != 0


#: Multi-word attribution openers -- used sentence-initially and
#: capitalised in the writer's own reply examples ("According to Example
#: Institute, ..."), so kept case-insensitive: unlike the single verbs
#: above, no common capitalised word collides with them.
_CREDIT_PHRASES = ("according to", "as quoted by")
_CREDIT_PHRASE = re.compile(
    r"\b(?:" + "|".join(re.escape(phrase) for phrase in _CREDIT_PHRASES) + r")\b",
    re.IGNORECASE,
)

#: "state(s)" is the
#: codebase's own primary crediting verb ("Wikipedia states the crisis
#: was..."), so it stays in the verb list above, but a determiner can
#: never sit directly in front of a verb -- "the [Adjective] state(s)"
#: is always the geopolitical noun ("the United States", "the Roman
#: state"), never a credited claim, whichever determiner
#: introduces it and whether or not one capitalised modifier (a
#: nationality or proper adjective) sits between them. "member states"
#: is the one common exception with no determiner at all. Stripped
#: before the verb match runs, so neither ever counts as a credit by
#: accident; a lower-case common noun before "state(s)" ("the report
#: states", "the findings state") is left alone, since a genuine
#: subject sits between the determiner and the verb.
_MULTIWORD_NAME_EXCLUSION = re.compile(
    r"\b(?i:the|a|an|this|that|these|those|its|his|her|their|our|your|each|every|such)\s+"
    r"(?:[A-Z][\w-]*\s+)?(?i:states?)\b"
    r"|\b(?i:member)\s+(?i:states?)\b"
)

#: The separators a page's own `<title>` uses between its
#: article headline and its site name.
_TITLE_SEPARATORS = (" | ", " - ", " « ", " — ")


def _site_name_segment(title: str) -> str:
    """The site-name segment of a page title -- the text after the last
    of the common title/site separators (" | ", " - ", " « ", " — "), so
    "Article Headline | Example Register" names "Example Register", not
    every word the headline shares with an unrelated point about the
    same subject."""
    best_end = -1
    for separator in _TITLE_SEPARATORS:
        index = title.rfind(separator)
        if index != -1:
            end = index + len(separator)
            if end > best_end:
                best_end = end
    return title[best_end:].strip() if best_end >= 0 else title.strip()


def _host_site_name(url: str) -> list[str]:
    """The host's own site-name label(s), its "www." and its TLD
    stripped -- "en.wikipedia.org" and "www.bbc.co.uk" both name
    "wikipedia" and "bbc", the whole name a point actually uses.
    A hyphenated host ("example-institute.test") also
    yields its words with spaces ("example institute"), since prose
    credits an organisation by its own name, never by its domain's own
    hyphenation."""
    identity = publisher_identity(url)
    if identity.startswith("www."):
        identity = identity[len("www."):]
    label = identity.split(".")[0]
    names = [label]
    if "-" in label:
        names.append(label.replace("-", " "))
    return names


#: Name particles a primary-source title's author
#: segment may carry ("Erasmus of Rotterdam", "Ludwig von Mises").
_NAME_PARTICLES = frozenset({"de", "von", "van", "of", "the"})


def _title_author_segment(title: str) -> str | None:
    """The author's name-shaped first segment of a title, if any --
    "Sallust, Catiline's War 5-16" gives "Sallust", "Vale, Jordan
    (1901-1960)" gives "Vale". Counts only when every word in the
    first comma-delimited segment is capitalised or a name particle,
    at least one word is capitalised (a segment of only particles, or
    of one lower-case word -- an ordinary sentence-shaped title's own
    opening clause -- never counts), and it is at most four words:
    long enough for a full name, short enough to exclude an ordinary
    title's opening clause. This is name-*shaped* only, not proof of a
    credit by itself: "Climate, the great challenge of our age" is
    exactly as name-shaped as "Sallust, Catiline's War" ("Climate" is
    one capitalised word); ``_credits_the_title_author`` below is what
    actually decides whether a point uses it as an author -- callers must
    never treat this segment as a plain
    substring."""
    first, comma, _ = title.partition(",")
    if not comma:
        return None
    words = first.split()
    if not words or len(words) > 4:
        return None
    has_capitalised = False
    for word in words:
        if word.lower() in _NAME_PARTICLES:
            continue
        if not word[:1].isupper():
            return None
        has_capitalised = True
    return first.strip() if has_capitalised else None


#: The verbs an authorial sentence uses beyond the plain
#: credit verbs above -- a title's author segment is name-shaped as
#: often as it is a person ("Sallust, Catiline's War" and "the
#: great challenge of our age"'s "Climate" are equally name-shaped),
#: so it is only ever a credit when the text actually uses it as an
#: author, never as a bare substring.
_AUTHOR_VERB_WORDS = tuple(dict.fromkeys(_CREDIT_VERB_WORDS + (
    "traced", "tells", "told", "recounts", "recounted", "narrates", "narrated",
    "describes", "described", "blames", "blamed", "attributes", "attributed",
    "explains", "explained", "wrote",
)))
_AUTHOR_POSSESSIVE_NOUNS = ("account", "view", "words", "narrative", "history", "text", "work")


def _credits_the_title_author(text: str, segment: str) -> bool:
    """Does ``text`` actually use ``segment`` as an author,
    not merely contain it? Three constructions: the segment directly
    before an authorial verb, at most one word (an adverb, "also")
    between them; the segment's own possessive before "account",
    "view", "words", "narrative", "history", "text" or "work"; or one
    of "according to <segment>", "per <segment>", "in <segment>'s", or
    "as <segment> puts it". A title-author segment must only ever
    reach this function, never the plain-substring check the other
    name phrases use."""
    escaped = re.escape(segment)
    verbs = "|".join(re.escape(word) for word in _AUTHOR_VERB_WORDS)
    nouns = "|".join(_AUTHOR_POSSESSIVE_NOUNS)
    pattern = (
        rf"\b{escaped}\b(?:\s+\w+)?\s+(?:{verbs})\b"
        rf"|\b{escaped}'s\s+(?:{nouns})\b"
        rf"|\b(?i:according to|per)\s+{escaped}\b"
        rf"|\b(?i:in)\s+{escaped}'s\b"
        rf"|\b(?i:as)\s+{escaped}\s+puts it\b"
    )
    return re.search(pattern, text) is not None


def _source_name_phrases(finding: Finding) -> list[str]:
    """Every whole name a point could credit this finding to as a
    plain substring: never a single
    shared word, since a page's title always shares the subject's own
    words with any point about it, so a token-overlap guard on "roman"
    and "bce" alone would let almost any point through. The page's own
    site-name segment, its host's site
    name, its attributed issuer (an admitted figure issuer or a quoted
    author -- the same field carries both), and every kept
    figure's own organisation (the registry line's own
    "organisation ..." clause). The title's own "Author, Work" author
    segment is deliberately excluded here: it is only ever a
    credit through ``_credits_the_title_author``'s named constructions,
    never through a plain substring check."""
    phrases = [_site_name_segment(finding.source_title), *_host_site_name(finding.source_url)]
    if finding.attributed_issuer:
        phrases.append(finding.attributed_issuer)
    if finding.verification is not None:
        for result in finding.verification.figure_results:
            if result.kept and result.context is not None and result.context.organisation:
                phrases.append(result.context.organisation)
    return [phrase for phrase in phrases if phrase]


def _names_a_source(text: str, findings: Sequence[Finding]) -> bool:
    """Does this point name the source it rests on --
    by a credit verb or attribution phrase, by a whole name its cited
    findings actually carry, or by a cited finding's title-author
    segment used as an author? A point that states a fact naming none
    of these is refused: a fact with no one behind it is not what any
    finding actually verified. A phrase under three letters (a
    ``.test`` fixture host's own bare label can be one letter,
    "a.test" -> "a") is never checked -- a single letter is a
    substring of almost any sentence.
    """
    verb_search_text = _MULTIWORD_NAME_EXCLUSION.sub("", text)
    if _CREDIT_PHRASE.search(text):
        return True
    if _credit_verb_match(_REPORTING_VERB_WORD, verb_search_text) is not None:
        return True
    for match in _ACTION_VERB_WORD.finditer(verb_search_text):
        preceding = _word_before(verb_search_text, match.start())
        if preceding is not None and preceding.group(1).lower() in _DETERMINERS:
            continue
        if _action_verb_credits(verb_search_text, match, findings):
            return True
    lowered = text.lower()
    for finding in findings:
        for phrase in _source_name_phrases(finding):
            if len(phrase) >= 3 and phrase.lower() in lowered:
                return True
        author_segment = _title_author_segment(finding.source_title)
        if author_segment and _credits_the_title_author(text, author_segment):
            return True
    return False


#: A small, general verdict lexicon -- a drafted
#: title carrying one of these (or a digit/quantity) prints raw as an
#: options-table column header, so it falls back to the sub-topic's own
#: title instead. Matched on whole words only, with simple inflections
#: (\w* lets "recommended"/"recommends", "tops"/"topped", "winners" through).
_VERDICT_WORDS = r"best|worst|top\w*|winner\w*|leading|recommend\w*"
_TITLE_VERDICT_WORD = re.compile(rf"\b(?:{_VERDICT_WORDS}|pick\w*)\b", re.IGNORECASE)
#: The short title's own set: the lexicon above without ``pick\w*``,
#: because the section prompt's own example short title is "Published
#: picks" -- a label that names a part of the question, not a pick made.
_SHORT_TITLE_VERDICT_WORD = re.compile(rf"\b(?:{_VERDICT_WORDS})\b", re.IGNORECASE)
_TITLE_DIGIT = re.compile(r"\d")


def _section_short_title(drafted_short_title: str, title: str) -> str:
    """The drafted short title when it has one to
    three words, at most 24 characters, no digit and no verdict word (the short
    title's own set, which leaves out ``pick\\w*``); otherwise the section's own
    title stands in."""
    short = " ".join(drafted_short_title.split())
    if (
        not short
        or len(short.split()) > _SHORT_TITLE_WORDS
        or len(short) > _SHORT_TITLE_CHARS
        or _TITLE_DIGIT.search(short)
        or _SHORT_TITLE_VERDICT_WORD.search(short)
    ):
        return title
    return short


def _section_title(drafted_title: str, sub_topic_title: str) -> str:
    """The section's title, cut at ``_SECTION_TITLE_CHARS`` on a word
    boundary; a title stating a digit/quantity or a verdict word (a raw,
    unchecked title becomes an options-table column header) falls back to
    the sub-topic's own title instead."""
    title = " ".join(drafted_title.split())
    if not title or _TITLE_DIGIT.search(title) or _TITLE_VERDICT_WORD.search(title):
        return sub_topic_title
    if len(title) <= _SECTION_TITLE_CHARS:
        return title
    cut = title[:_SECTION_TITLE_CHARS]
    boundary = cut.rfind(" ")
    return cut[:boundary] if boundary > 0 else cut



def _cosmetic_contains(haystack: str, needle: str) -> bool:
    if not needle:
        return True
    return cosmetic_text(needle).casefold() in cosmetic_text(haystack).casefold()


def _lead_in(text: str, cuts: Sequence[re.Match[str]]) -> str:
    """The point's own introduction, verbatim: up to its colon, else its first clause.

    A piece cut after a ';' begins mid-sentence -- "… (c) keep track of …" with
    no subject and none of the conditions the sentence opened with -- so
    such a piece is printed with this introduction in front of it. The colon a
    list hangs from is the list's own introduction, so everything up to and
    including it is the lead; a clause-separated point with no colon is
    introduced by its first clause. Empty only when the point has neither.
    """
    colon = text.find(":")
    if colon != -1:
        return text[:colon + 1]
    return text[:cuts[0].start()] if cuts else ""


def _split_oversize_point(text: str, limit: int = MAX_POINT_CHARS) -> list[str] | None:
    """``text`` as consecutive pieces under ``limit``, or ``None`` when one clause is over it.

    A drafted point longer than the bound is split at a sentence boundary
    into pieces that keep its citations, rather than dropped. A piece that
    begins mid-sentence -- after a ';' inside a list -- is printed with the
    point's own introduction, so no piece stands without its subject or
    its conditions. A point whose own clause is longer than the bound, or
    whose introduction plus one clause is, cannot be cut into pieces that
    stand alone: it returns ``None`` and the caller refuses it,
    rather than print a fragment.
    """
    cuts = list(_CLAUSE_BOUNDARY.finditer(text))
    clauses = _CLAUSE_BOUNDARY.split(text)
    if any(len(clause) > limit for clause in clauses):
        return None
    lead = _lead_in(text, cuts)
    marks = [text[cut.start() - 1] for cut in cuts]
    pieces: list[str] = []
    current = ""
    for index, clause in enumerate(clauses):
        mid_sentence = index > 0 and marks[index - 1] == ";"
        opening = f"{lead} {clause}".strip() if (mid_sentence and lead) else clause
        if not current:
            current = opening
        elif len(f"{current} {clause}") <= limit:
            current = f"{current} {clause}"
        else:
            pieces.append(current)
            current = opening
        if len(current) > limit:
            return None
    if current:
        pieces.append(current)
    return pieces or None


class _Verdict(Protocol):
    """Structural shape of ``evidence_verifier.StatementVerdictDraft``.

    Read by attribute only, never imported: the verdict is applied by code
    without re-judging the wording, so this module depends on the
    shape of the checker's reply rather than on the checker's own type -- and
    the tests' fake verdicts satisfy it without importing anything.
    """

    verdict: str
    corrected_text: str
    reason: str


@dataclass(frozen=True)
class _Candidate:
    """One drafted point that cleared the mechanical rules and is waiting on
    the Statement Check's verdict."""

    key: str                     # "P01.01", "B01"; also the temp ReportStatement.statement_id
    where: str                   # "section[topic-01].points[1]" or "bottom_line[0]"
    text: str                    # drafted, whitespace-collapsed
    finding_labels: list[str]
    findings: list[Finding]
    items: list[ItemMarkDraft]
    had_label_group: bool = False
    """Whether the drafted text carried a leaked finding-label
    group that ``_strip_label_groups`` removed -- the note this earns is
    recorded by ``_finalize_candidate`` only once the candidate is kept, so
    a refused candidate (whose key never reaches a printed S-id) never
    leaves an orphaned note in the log."""
    disputes: bool = False
    """The writer's own ``point.disputes`` -- a point marked
    ``disputes: true`` states a disagreement or dispute among the sources.
    Read by ``_run_part`` for a *kept* candidate to compute the part's
    disputed labels, which the bottom line reads to list
    disputed steps and to guard a sentence that states one as settled."""
    outcome: bool = False
    """The writer's own ``point.outcome`` -- a point marked
    ``outcome: true`` states the mechanism's own last step, the outcome
    the question's subject reached. Read by ``_run_part`` for a *kept*
    candidate to record the part's outcome statement ids, which the
    bottom line lists under "# Outcome" and requires a kept sentence to
    credit, on a mechanism answer."""


def _finding_label(finding: Finding) -> str:
    """Every kept figure's reader label for one finding, joined into one
    string -- the Statement Check gets one label string per cited finding,
    not one per figure."""
    labels: list[str] = []
    for result in finding.verification.figure_results if finding.verification else []:
        if result.kept and result.context is not None:
            label = _figure_label_for(finding, result.context)
            if label not in labels:
                labels.append(label)
    return "; ".join(labels)


def _consider_section_point(
    point: WriterPointDraft, where: str, *, part_labels: Mapping[str, Finding],
    all_labels: Mapping[str, Finding], context_only_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
) -> list[_Candidate]:
    drafted = " ".join(point.text.split())
    drafted, had_label_group = _strip_label_groups(drafted)
    wanted = [label.strip() for label in point.finding_labels]

    def refuse(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=where, text=drafted, finding_labels=list(point.finding_labels), reason=reason,
        ))

    if not drafted:
        refuse("empty text")
        return []
    if not wanted:
        refuse("cites no checked finding")
        return []
    unknown = [label for label in wanted if label not in all_labels]
    if unknown:
        refuse("unknown labels: " + ", ".join(unknown))
        return []
    outside = [label for label in wanted if label not in part_labels]
    if outside:
        refuse("cites a finding outside this part")
        return []
    if all(label in context_only_labels for label in wanted):
        refuse("rests only on context-only findings")
        return []
    cited_findings = [part_labels[label] for label in wanted]
    if not _names_a_source(drafted, cited_findings):
        refuse("states a fact without crediting the source that states it")
        return []
    if _has_unnamed_subject(drafted) and not point.items:
        refuse("a judgement with no named subject")
        return []
    pieces = [drafted] if len(drafted) <= MAX_POINT_CHARS else _split_oversize_point(drafted)
    if pieces is None:
        refuse(f"longer than {MAX_POINT_CHARS} characters")
        return []
    return [
        _Candidate(key=f"{key_prefix}{next(numbers):02d}",
                  where=where if len(pieces) == 1 else f"{where} part {n}",
                  text=piece, finding_labels=wanted,
                  findings=cited_findings, items=list(point.items),
                  had_label_group=had_label_group, disputes=point.disputes,
                  outcome=point.outcome)
        for n, piece in enumerate(pieces, start=1)
    ]


#: A bottom-line sentence that touches a disputed target
#: must say so. Matched as stems, not exact words -- an
#: exact-word list would miss "disputes", "disputed", "differently",
#: "rejected", "questioned" and every other inflection, and so refuse
#: exactly the sentences the disagreement-first rule asks the writer to
#: produce.
_DIFFERENCE_MARKER = re.compile(
    r"\b(?:differ\w*|disagree\w*|disput\w*|contest\w*|challeng\w*|question\w*|"
    r"reject\w*|however|although|though|but|while|others argue)\b",
    re.IGNORECASE,
)


def _consider_bottom_line_point(
    point: WriterPointDraft, where: str, *, cited_by_sections: Mapping[str, Finding],
    disputed_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
    split: bool = True,
) -> list[_Candidate]:
    drafted = " ".join(point.text.split())
    drafted, had_label_group = _strip_label_groups(drafted)
    wanted = [label.strip() for label in point.finding_labels]

    def refuse(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=where, text=drafted, finding_labels=list(point.finding_labels), reason=reason,
        ))

    if not drafted:
        refuse("empty text")
        return []
    if not wanted:
        refuse("cites no checked finding")
        return []
    unknown = [label for label in wanted if label not in cited_by_sections]
    if unknown:
        refuse("cites a finding no checked section statement cites")
        return []
    cited_findings = [cited_by_sections[label] for label in wanted]
    if set(wanted) & disputed_labels and not _DIFFERENCE_MARKER.search(drafted):
        refuse("states a disputed step without its dispute")
        return []
    if _has_unnamed_subject(drafted) and not point.items:
        refuse("a judgement with no named subject")
        return []
    if len(drafted) <= MAX_POINT_CHARS:
        pieces: list[str] | None = [drafted]
    else:
        pieces = _split_oversize_point(drafted) if split else None
    if pieces is None:
        refuse(f"longer than {MAX_POINT_CHARS} characters")
        return []
    return [
        _Candidate(key=f"{key_prefix}{next(numbers):02d}",
                  where=where if len(pieces) == 1 else f"{where} part {n}",
                  text=piece, finding_labels=wanted,
                  findings=cited_findings, items=list(point.items),
                  had_label_group=had_label_group, disputes=point.disputes,
                  outcome=point.outcome)
        for n, piece in enumerate(pieces, start=1)
    ]


def _consider_topic_line(
    line: TopicLineDraft, where: str, *, listed_topics: Mapping[str, frozenset[str]],
    seen_topics: set[str], cited_by_sections: Mapping[str, Finding], disputed_labels: set[str],
    numbers: Iterator[int], rejected: list[RejectedDraftPoint], key_prefix: str,
) -> list[_Candidate]:
    """One topic line. Refused when its topic is
    not one the request listed, when its topic already has a line, or when it
    cites a label its own topic's kept statements do not; then every rule a
    bottom-line sentence follows. A topic line is one line, so it is never split:
    one over ``MAX_POINT_CHARS`` is refused.

    ``listed_topics`` maps each listed topic's coverage id to the labels its
    kept statements cite.
    """
    topic = line.topic.strip()
    drafted, _ = _strip_label_groups(" ".join(line.text.split()))

    def refuse(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=where, text=drafted, finding_labels=list(line.finding_labels), reason=reason,
        ))

    if topic not in listed_topics:
        refuse("a line for a topic the request did not list")
        return []
    if topic in seen_topics:
        refuse("a second line for one topic")
        return []
    seen_topics.add(topic)
    if any(label.strip() not in listed_topics[topic] for label in line.finding_labels):
        refuse("a topic line cites a finding its topic does not")
        return []
    return _consider_bottom_line_point(
        line, where, cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
        numbers=numbers, rejected=rejected, key_prefix=key_prefix, split=False,
    )


@dataclass(frozen=True)
class _LineLayout:
    """The kept bottom line's shape by temporary statement key:
    the answer's keys, then ``(coverage_id, key)`` for each topic line."""

    answer_keys: tuple[str, ...]
    topic_keys: tuple[tuple[str, str], ...]
    assembled: bool


def _drafted_layout(points: Sequence[ReportPoint], topic_of: Mapping[str, str]) -> _LineLayout | None:
    if not points:
        return None
    return _LineLayout(
        answer_keys=tuple(p.statement_id for p in points if p.statement_id not in topic_of),
        topic_keys=tuple((topic_of[p.statement_id], p.statement_id) for p in points if p.statement_id in topic_of),
        assembled=False,
    )


def _assembled_layout(points: Sequence[ReportPoint], coverage_ids: Sequence[str]) -> _LineLayout | None:
    if not points:
        return None
    return _LineLayout(
        answer_keys=(),
        topic_keys=tuple(zip(coverage_ids, (point.statement_id for point in points))),
        assembled=True,
    )


def _apply_marks(
    marks: Sequence[ItemMarkDraft], *, final_text: str, labels: Sequence[str],
    label_urls: Mapping[str, str], label_finding_ids: Mapping[str, str],
    dropped: list[tuple[str, str]], statement_key: str,
) -> list[ItemMark]:
    """Each name/verdict a verbatim span of the final text, at most
    ``_MARK_SPAN_CHARS``; ``by`` resolved only among the point's own cited
    ``labels`` -- never the whole registry, or a mark could credit a page the
    sentence and its Statement Check never rested on. A failing mark is
    dropped -- the point stays -- and recorded.

    ``finding_id`` is resolved from ``by`` (or, when the point cites
    only one finding, that finding), never guessed from whichever finding
    happens to share the mark's page -- a page can carry more than one
    finding, and the table must not credit a pick to the wrong one of them.
    """
    kept: list[ItemMark] = []
    sites = {label_urls[label] for label in labels if label in label_urls}
    for mark in marks:
        name = mark.name.strip()
        verdict = mark.verdict.strip()
        if not name or len(name) > _MARK_SPAN_CHARS or not _cosmetic_contains(final_text, name):
            dropped.append((statement_key, f"'{mark.name}' is not in the sentence"))
            continue
        if verdict and (len(verdict) > _MARK_SPAN_CHARS or not _cosmetic_contains(final_text, verdict)):
            dropped.append((statement_key, f"'{mark.verdict}' is not in the sentence"))
            continue
        by = mark.by.strip()
        if len(sites) > 1:
            if by not in labels or by not in label_urls:
                dropped.append((statement_key, "names no finding this sentence cites"))
                continue
            source_url = label_urls[by]
        else:
            source_url = next(iter(sites), "")
        if by in labels and by in label_finding_ids:
            finding_id = label_finding_ids[by]
        elif len(labels) == 1:
            finding_id = label_finding_ids.get(labels[0])
        else:
            finding_id = None
        kept.append(ItemMark(name=name, verdict=verdict, picked=mark.picked, source_url=source_url,
                             finding_id=finding_id))
    return kept


def _finalize_candidate(
    candidate: _Candidate, verdicts: Mapping[str, _Verdict | None], *,
    stated_rows: set[str], task_facts: Sequence[FactRow], task_targets: Sequence[EvidenceTarget],
    label_urls: Mapping[str, str], label_finding_ids: Mapping[str, str],
    dropped_marks: list[tuple[str, str]], rejected: list[RejectedDraftPoint],
) -> tuple[ReportPoint | None, str]:
    """Apply the Statement Check's verdict, the restatement dedup, and the
    marks; return the printed point (or ``None``) and its verdict string."""

    def reject(reason: str) -> None:
        rejected.append(RejectedDraftPoint(
            where=candidate.where, text=candidate.text,
            finding_labels=list(candidate.finding_labels), reason=reason,
        ))

    verdict = verdicts.get(candidate.key)
    text = candidate.text
    if verdict is None:
        verdict_string = "unchecked"
    else:
        verdict_string = verdict.verdict
        if verdict.verdict == "corrected":
            correction = " ".join(verdict.corrected_text.split())
            if len(correction) > MAX_POINT_CHARS:
                reject(f"corrected text longer than {MAX_POINT_CHARS} characters")
                return None, verdict_string
            if not correction:
                reject(verdict.reason)
                return None, "inconsistent"
            text = correction
        elif verdict.verdict == "inconsistent":
            reject(verdict.reason)
            return None, verdict_string

    cited_ids = {finding_fingerprint(f) for f in candidate.findings}
    rows = {row.row_id for row in _carried_rows(text, cited_ids, task_facts, task_targets)}
    if rows and rows <= stated_rows:
        reject("restates " + ", ".join(sorted(rows)))
        return None, verdict_string
    stated_rows.update(rows)

    # A statement's target ids are explicit bindings only -- a
    # fallback answer (no target id of its own) is stated but never claims a
    # target it never named.
    explicit_targets = sorted({
        target.target_id for target in task_targets
        for finding in candidate.findings
        if target.target_id in finding.target_ids and finding_fingerprint(finding) in cited_ids
    })
    marks = _apply_marks(candidate.items, final_text=text, labels=candidate.finding_labels,
                         label_urls=label_urls, label_finding_ids=label_finding_ids,
                         dropped=dropped_marks, statement_key=candidate.key)
    own_first = sorted(candidate.findings, key=lambda f: 0 if any(
        r.context is not None and r.context.attribution == "own"
        for r in (f.verification.figure_results if f.verification else [])) else 1)
    statement = ReportStatement(
        statement_id=candidate.key, text=text,
        finding_ids=[finding_fingerprint(f) for f in candidate.findings],
        target_ids=explicit_targets, items=marks,
    )
    point = ReportPoint(text=text, source_urls=list(dict.fromkeys(f.source_url for f in own_first)),
                        statement=statement)
    if candidate.had_label_group:
        dropped_marks.append((candidate.key, "stripped a finding label from the point's text"))
    return point, verdict_string


async def _check(
    provider: AgentCompleter, candidates: Sequence[_Candidate], *, question: str,
    gate: asyncio.Semaphore, batch_size: int, fingerprint: Callable[[str], object] | None,
    passages: Mapping[str, str], source_lines: Mapping[str, str],
    part: tuple[str, str] | None = None,
) -> tuple[Mapping[str, _Verdict | None], list[ResearchError]]:
    """Run the Statement Check over one part's (or the bottom line's)
    candidates, through the shared gate.

    Inside a composition, each settled batch
    is counted and published live, under ``part`` -- ``(coverage_id, section
    title)`` -- or, when ``part`` is ``None``, under the bottom line. Once the
    check returns, a key no batch reported is counted from the verdicts
    returned, so a substituted checker is counted too.
    """
    if not candidates:
        return {}, []
    progress = _WRITING_PROGRESS.get()
    section = _BOTTOM_LINE_SECTION if part is None else part[1]
    if progress is not None and part is None:
        progress.bottom_line_drafted([candidate.key for candidate in candidates])
    # Imported at call time, not at module scope: the unit tests
    # substitute the checker by assigning
    # ``evidence_verifier.check_statements``, and a module-level ``from``
    # would bind the real function before that assignment could be seen.
    from deep_research.agents.evidence_verifier import (
        StatementCheckItem,
        check_statements,
    )

    items = [
        StatementCheckItem(label=c.key, text=c.text, findings=c.findings,
                           labels=[_finding_label(f) for f in c.findings], passages=passages,
                           source_lines=source_lines)
        for c in candidates
    ]
    try:
        verdicts, errors = await check_statements(
            provider, items, question=question, fingerprint=fingerprint,
            batch_size=batch_size, gate=gate,
            on_batch=None if progress is None else progress.reporter(section),
        )
    except ProviderConfigurationError:
        raise
    except (ProviderError, StructuredOutputError, ValidationError) as error:
        verdicts, errors = {}, [agent_error(
            agent_name=REPORT_WRITER_NAME,
            error_type="report_writer_statement_check_failed",
            message="The report writer's statement check failed; every drafted point was kept unchanged.",
            details={"exception_type": type(error).__name__},
        )]
    if progress is not None:
        progress.settle(section, items, verdicts)
    return verdicts, errors


# --- the section and bottom-line draft calls (the retry ladder) -------------


async def _attempt_section_draft(
    provider: AgentCompleter, messages: list[ChatMessage], *, agent_name: str,
    fingerprint: Callable[[str], object] | None, coverage_id: str,
) -> tuple[SectionDraft | None, list[ResearchError]]:
    errors: list[ResearchError] = []
    for attempt, effort in enumerate(_WRITER_ATTEMPT_EFFORTS, start=1):
        if fingerprint is not None:
            fingerprint(SectionDraft.__name__)
        try:
            if effort is None:
                result = await provider.complete_structured(messages, SectionDraft, agent_name=agent_name)
            else:
                result = await provider.complete_structured(
                    messages, SectionDraft, agent_name=agent_name, reasoning_effort=effort)
        except ProviderOutputLimitError as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_section_failed",
                message="A part's draft reached the provider's output limit.",
                details={"coverage_id": coverage_id, "attempt": attempt, "exception_type": type(error).__name__},
            ))
            continue
        except ProviderConfigurationError:
            raise
        except (ProviderError, StructuredOutputError, ValidationError) as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_section_failed",
                message="A part's draft failed.",
                details={"coverage_id": coverage_id, "attempt": attempt, "exception_type": type(error).__name__},
            ))
            return None, errors
        return result, errors
    errors.append(agent_error(
        agent_name=agent_name, error_type="report_writer_section_failed",
        message="A part's draft was truncated on every attempt.",
        details={"coverage_id": coverage_id, "attempt": len(_WRITER_ATTEMPT_EFFORTS)},
    ))
    return None, errors


async def _attempt_bottom_line_draft(
    provider: AgentCompleter, messages: list[ChatMessage], *, agent_name: str,
    fingerprint: Callable[[str], object] | None,
) -> tuple[BottomLineDraft | None, list[ResearchError]]:
    progress = _WRITING_PROGRESS.get()
    if progress is not None:
        progress.bottom_line_started()
    errors: list[ResearchError] = []
    for attempt, effort in enumerate(_WRITER_ATTEMPT_EFFORTS, start=1):
        if fingerprint is not None:
            fingerprint(BottomLineDraft.__name__)
        try:
            if effort is None:
                result = await provider.complete_structured(messages, BottomLineDraft, agent_name=agent_name)
            else:
                result = await provider.complete_structured(
                    messages, BottomLineDraft, agent_name=agent_name, reasoning_effort=effort)
        except ProviderOutputLimitError as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_bottom_line_failed",
                message="The bottom line's draft reached the provider's output limit.",
                details={"attempt": attempt, "exception_type": type(error).__name__},
            ))
            continue
        except ProviderConfigurationError:
            raise
        except (ProviderError, StructuredOutputError, ValidationError) as error:
            errors.append(agent_error(
                agent_name=agent_name, error_type="report_writer_bottom_line_failed",
                message="The bottom line's draft failed.",
                details={"attempt": attempt, "exception_type": type(error).__name__},
            ))
            return None, errors
        return result, errors
    errors.append(agent_error(
        agent_name=agent_name, error_type="report_writer_bottom_line_failed",
        message="The bottom line's draft was truncated on every attempt.",
        details={"attempt": len(_WRITER_ATTEMPT_EFFORTS)},
    ))
    return None, errors


# --- per-part orchestration -------------------------------------------------


@dataclass
class _PartOutcome:
    job: PartJob
    section: ReportSection | None
    status: str   # "written" | "carried_over" | "failed" | "empty"
    errors: list[ResearchError]
    verdicts: dict[str, str]                      # temp statement id -> verdict string
    rejected: list[RejectedDraftPoint] = field(default_factory=list)
    dropped_marks: list[tuple[str, str]] = field(default_factory=list)
    disputed_labels: frozenset[str] = frozenset()
    """The finding labels of every *kept* point this part wrote with
    ``disputes: true`` -- ``_run_bottom_line`` unions this across every part,
    then expands it (an exclusive hop) to every other checked statement
    sharing one of those labels, for each label no statement outside
    the dispute also cites, before the bottom line ever runs. The writer's
    own kept marks are the whole signal, not a finding-level one.
    Label-scoped, not target-scoped: a required target can carry a
    dozen statements, and target scoping would tell the guard every one of
    them was disputed the moment any one was marked."""
    disputed_statement_ids: frozenset[str] = frozenset()
    """The statement ids of the same kept, writer-marked
    points -- which *specific* point in this part was the mark, so the
    bottom-line fallback can prefer it over a naive first pick that only touches
    one of the disputed labels without stating the dispute."""
    outcome_statement_ids: frozenset[str] = frozenset()
    """The statement ids of this part's kept points marked
    ``outcome: true`` -- the mechanism's own last step. ``_run_bottom_line``
    unions this across every part to build the "# Outcome" block and the
    guard that re-asks once when a mechanism's bottom line names no
    outcome."""


async def _run_part(
    job: PartJob, task: ReportWriterTask, *, provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None, section_gate: asyncio.Semaphore,
    check_gate: asyncio.Semaphore, batch_size: int, label_urls: Mapping[str, str],
    label_finding_ids: Mapping[str, str],
) -> _PartOutcome:
    if not job.findings and not job.context_findings:
        return _PartOutcome(job=job, section=None, status="empty", errors=[], verdicts={})

    if not job.redraft and job.previous is not None:
        # Carried over unchanged -- same points, verdicts and marks, no call.
        verdicts = {}
        if task.previous is not None:
            for point in job.previous.points:
                if point.statement is not None:
                    verdicts[point.statement.statement_id] = task.previous.statement_verdicts.get(
                        point.statement.statement_id, "unchecked")
        return _PartOutcome(job=job, section=job.previous, status="carried_over", errors=[], verdicts=verdicts)

    # A redraft with no defect routed here still drafts the part when it has
    # no previous section to carry over -- it failed, or every one of its
    # points was refused, last pass. Silently relabelling it "empty" would
    # drop a required target's only answer without disclosing it.

    messages = section_messages(task, job)
    async with section_gate:
        draft, draft_errors = await _attempt_section_draft(
            provider, messages, agent_name=REPORT_WRITER_NAME, fingerprint=fingerprint,
            coverage_id=job.coverage_id,
        )
    if draft is None:
        progress = _WRITING_PROGRESS.get()
        if progress is not None:
            progress.part_returned(job.coverage_id, (), failed=True)
        return _PartOutcome(job=job, section=None, status="failed", errors=draft_errors, verdicts={})

    placed_ids = {finding_fingerprint(f) for f in [*job.findings, *job.context_findings]}
    part_labels = {label: f for label, f in task.registry if finding_fingerprint(f) in placed_ids}
    all_labels = dict(task.registry)
    context_ids = {finding_fingerprint(f) for f in job.context_findings}
    context_only_labels = {label for label, f in task.registry if finding_fingerprint(f) in context_ids}

    numbers = iter(range(1, 10_000))
    rejected: list[RejectedDraftPoint] = []
    dropped_marks: list[tuple[str, str]] = []
    candidates: list[_Candidate] = []
    for n, point in enumerate(draft.points):
        candidates.extend(_consider_section_point(
            point, f"section[{job.coverage_id}].points[{n}]", part_labels=part_labels,
            all_labels=all_labels, context_only_labels=context_only_labels,
            numbers=numbers, rejected=rejected, key_prefix=f"P{job.order + 1:02d}.",
        ))

    progress = _WRITING_PROGRESS.get()
    if progress is not None:
        progress.part_returned(job.coverage_id, [candidate.key for candidate in candidates])
    verdicts, check_errors = await _check(
        provider, candidates, question=task.question, gate=check_gate,
        batch_size=batch_size, fingerprint=fingerprint, passages=task.passages,
        source_lines=finding_source_lines(task.findings, sources_by_url(task.sources)),
        part=(job.coverage_id, _section_title(draft.title, job.sub_topic_title)),
    )

    stated_rows: set[str] = set()
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    disputed_labels: set[str] = set()
    disputed_statement_ids: set[str] = set()
    outcome_statement_ids: set[str] = set()
    for candidate in candidates:
        point, verdict_string = _finalize_candidate(
            candidate, verdicts, stated_rows=stated_rows, task_facts=task.facts,
            task_targets=task.targets, label_urls=label_urls, label_finding_ids=label_finding_ids,
            dropped_marks=dropped_marks, rejected=rejected,
        )
        if point is not None:
            points.append(point)
            verdict_map[point.statement_id] = verdict_string
            if candidate.disputes:
                disputed_labels.update(candidate.finding_labels)
                disputed_statement_ids.add(point.statement_id)
            if candidate.outcome:
                outcome_statement_ids.add(point.statement_id)

    title = _section_title(draft.title, job.sub_topic_title)
    section = ReportSection(
        title=title, short_title=_section_short_title(draft.short_title, title),
        points=points, coverage_id=job.coverage_id,
    ) if points else None
    # A draft that kept nothing (every point refused) is undisclosed
    # and unwritten, not "written" -- "written" with no section silently
    # hides a part that had findings, so the every-part-failed wording never
    # fires and the per-part evidence-log pointer never prints for it.
    status = "written" if points else "failed"
    if status == "failed" and progress is not None:
        progress.part_failed(job.coverage_id)
    return _PartOutcome(job=job, section=section, status=status,
                        errors=[*draft_errors, *check_errors], verdicts=verdict_map,
                        rejected=rejected, dropped_marks=dropped_marks,
                        disputed_labels=frozenset(disputed_labels),
                        disputed_statement_ids=frozenset(disputed_statement_ids),
                        outcome_statement_ids=frozenset(outcome_statement_ids))


def _bottom_line_fallback(
    outcomes: Sequence[_PartOutcome],
    disputed_labels: frozenset[str] = frozenset(),
    label_by_finding_id: Mapping[str, str] = _EMPTY_MAPPING,
    findings_by_id: Mapping[str, Finding] | None = None,
    sources: Mapping[str, ScoredSource] | None = None,
    authority_floor: float = 0.0,
    self_descriptions: Mapping[str, str] | None = None,
    any_above_floor: bool = False,
) -> tuple[list[ReportPoint], dict[str, str], set[str], list[str]]:
    """One kept checked point per topic, in
    plan order, note topics included -- each part's first ``consistent`` or
    ``corrected`` point, moved into the bottom line as that topic's line, as a
    new statement with its own flight key and the source statement's real
    verdict, never a section's own id and never a hard-coded "consistent".
    No answer sentence and no cap: a part with no eligible point has no
    line. Returns the fallback points, their verdicts, the source statement ids
    to remove from their sections (a "move": a sentence is printed once),
    and each point's coverage id.

    The naive first kept point of a part can
    itself be silent about a dispute one of its own cited findings
    carries -- the fallback existing only to stand in for an unchecked
    bottom line must not then print a disputed step as settled. When the
    naive pick cites one of ``disputed_labels`` but is not itself the
    part's marked ``disputes: true`` point, the marked point (when kept)
    is preferred instead. Label-scoped, not target-scoped: a required
    target can carry a dozen statements, and target scoping would prefer
    the marked point over every one of them, not only the ones that
    actually cite a disputed finding.

    The fallback exists only to stand in for an
    unchecked bottom line, and must honour the same per-statement floor
    the checked path does -- a below-floor or declared-derivative
    statement stays in its section rather than being picked here,
    exactly when ``any_above_floor`` (some other checked statement in
    this run does meet the floor).
    """
    findings_by_id = findings_by_id or {}
    sources = sources or {}
    self_descriptions = self_descriptions or {}
    points: list[ReportPoint] = []
    verdicts: dict[str, str] = {}
    moved: set[str] = set()
    coverage_ids: list[str] = []
    numbers = iter(range(1, 100))
    for outcome in outcomes:  # one outcome per part, in plan order
        section = outcome.section
        if section is None:
            continue
        kept: list[tuple[ReportPoint, str, str]] = []
        for source_point in section.points:
            if source_point.statement is None:
                continue
            source_id = source_point.statement.statement_id
            verdict = outcome.verdicts.get(source_id, "unchecked")
            if verdict not in ("consistent", "corrected"):
                continue
            if any_above_floor and not _statement_meets_authority_floor(
                source_point.statement.finding_ids, findings_by_id, sources, authority_floor,
                self_descriptions,
            ):
                continue
            kept.append((source_point, source_id, verdict))
        if not kept:
            continue
        chosen_point, chosen_id, chosen_verdict = kept[0]
        chosen_labels = {
            label_by_finding_id[fid] for fid in (chosen_point.statement.finding_ids if chosen_point.statement else [])
            if fid in label_by_finding_id
        }
        if (chosen_labels & disputed_labels) and chosen_id not in outcome.disputed_statement_ids:
            for candidate_point, candidate_id, candidate_verdict in kept:
                if candidate_id in outcome.disputed_statement_ids:
                    chosen_point, chosen_id, chosen_verdict = candidate_point, candidate_id, candidate_verdict
                    break
        new_id = f"B{next(numbers):02d}"
        new_statement = chosen_point.statement.model_copy(update={"statement_id": new_id})
        points.append(chosen_point.model_copy(update={"statement": new_statement}))
        verdicts[new_id] = chosen_verdict
        moved.add(chosen_id)
        coverage_ids.append(outcome.job.coverage_id)
    return points, verdicts, moved, coverage_ids



def _statement_meets_authority_floor(
    finding_ids: Sequence[str], findings_by_id: Mapping[str, Finding],
    sources: Mapping[str, ScoredSource], authority_floor: float,
    self_descriptions: Mapping[str, str] | None = None,
) -> bool:
    """Whether one of
    ``finding_ids``' sources is citable and above ``authority_floor`` -- the
    bottom line's own per-statement floor test (``is_context_only``
    makes no comparison like this one).

    A finding whose read declares itself derivative or teaching
    content counts as below the floor, whatever its host's own
    ``authority_score`` reads -- the same guard a low-confidence or
    sub-floor source already gets, so a role-play's own high host score
    never lets it stand in for a primary source.
    """
    self_descriptions = self_descriptions or {}
    for finding_id in finding_ids:
        finding = findings_by_id.get(finding_id)
        if finding is None:
            continue
        if finding.read_id in self_descriptions:
            continue
        source = sources.get(normalize_source_url(finding.source_url))
        if source is None or source.low_confidence:
            continue
        if source.authority_score is not None and source.authority_score > authority_floor:
            return True
    return False


async def _check_and_finalize_bottom_line(
    task: ReportWriterTask, draft: BottomLineDraft, *, provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None, check_gate: asyncio.Semaphore,
    batch_size: int, cited_by_sections: Mapping[str, Finding], label_urls: Mapping[str, str],
    label_finding_ids: Mapping[str, str], key_prefix: str, disputed_labels: frozenset[str] = frozenset(),
    listed_topics: Mapping[str, frozenset[str]] = _NO_TOPICS,
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]], list[tuple[str, str]], bool, dict[str, str]]:
    """One bottom-line draft's candidates, checked and finalized:
    at most ``MAX_ANSWER_SENTENCES`` answer sentences, then one line per listed
    topic.

    Returns (points, verdict_map, check_errors, rejected, dropped_marks,
    statement_check_refusals, fully_checked, topic_of), ``topic_of`` mapping a
    topic line's flight key to its coverage id. ``statement_check_refusals``
    is (drafted text, reason) for every candidate the Statement Check itself
    refused (verdict "inconsistent"), the re-ask's own input.
    ``fully_checked`` is True only when this draft had at least one
    candidate, no check error, no mechanical or Statement Check rejection,
    and a "consistent" or "corrected" verdict for every one of them:
    the re-ask adopts its own result only then, so a check outage or a
    partial refusal never swaps a checked bottom line for unchecked text.
    ``disputed_labels`` is the caller's own
    set, scoped to the writer's own kept ``disputes: true`` points' finding
    labels plus the exclusive hop;
    ``_run_bottom_line`` passes the same set to the re-ask too, so a
    re-asked sentence is guarded exactly as the first attempt's was.
    """
    numbers = iter(range(1, 100))
    rejected: list[RejectedDraftPoint] = []
    dropped_marks: list[tuple[str, str]] = []
    candidates: list[_Candidate] = []
    for n, point in enumerate(draft.sentences):
        candidates.extend(_consider_bottom_line_point(
            point, f"bottom_line[{n}]", cited_by_sections=cited_by_sections,
            disputed_labels=disputed_labels,
            numbers=numbers, rejected=rejected, key_prefix=key_prefix,
        ))

    # Cap before the check, so an overflow sentence never spends one,
    # and record its refusal with the real F-labels, not finding fingerprints.
    candidates, overflow_candidates = (
        candidates[:MAX_ANSWER_SENTENCES], candidates[MAX_ANSWER_SENTENCES:],
    )
    for extra in overflow_candidates:
        rejected.append(RejectedDraftPoint(
            where=extra.where, text=extra.text, finding_labels=list(extra.finding_labels),
            reason="over the direct answer's two sentences",
        ))
    topic_numbers = iter(range(1, 100))
    seen_topics: set[str] = set()
    topic_of: dict[str, str] = {}
    for n, line in enumerate(draft.topics):
        for candidate in _consider_topic_line(
            line, f"bottom_line.topics[{n}]", listed_topics=listed_topics, seen_topics=seen_topics,
            cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
            numbers=topic_numbers, rejected=rejected, key_prefix=f"{key_prefix}T",
        ):
            topic_of[candidate.key] = line.topic.strip()
            candidates.append(candidate)

    verdicts, check_errors = await _check(
        provider, candidates, question=task.question, gate=check_gate,
        batch_size=batch_size, fingerprint=fingerprint, passages=task.passages,
        source_lines=finding_source_lines(task.findings, sources_by_url(task.sources)),
    )

    # A topic line may restate the answer's fact when its topic has no other,
    # so the answer and the topic lines each keep their
    # own restatement guard.
    answer_rows: set[str] = set()
    topic_rows: set[str] = set()
    verdict_map: dict[str, str] = {}
    points: list[ReportPoint] = []
    refusals: list[tuple[str, str]] = []
    for candidate in candidates:
        point, verdict_string = _finalize_candidate(
            candidate, verdicts, stated_rows=topic_rows if candidate.key in topic_of else answer_rows,
            task_facts=task.facts,
            task_targets=task.targets, label_urls=label_urls, label_finding_ids=label_finding_ids,
            dropped_marks=dropped_marks, rejected=rejected,
        )
        if point is not None:
            points.append(point)
            verdict_map[point.statement_id] = verdict_string
        else:
            verdict_obj = verdicts.get(candidate.key)
            if verdict_obj is not None and verdict_obj.verdict == "inconsistent":
                refusals.append((candidate.text, verdict_obj.reason))

    # A sentence the disputed-target guard refused (never
    # reaching the Statement Check at all) must feed the one re-ask the
    # same way a Statement Check refusal does -- otherwise the step it
    # was guarding is simply dropped, the exact loss the re-ask
    # exists to prevent.
    refusals.extend(
        (entry.text, entry.reason) for entry in rejected
        if entry.reason == "states a disputed step without its dispute"
    )

    fully_checked = (
        bool(candidates) and not check_errors and not rejected
        and all(
            verdicts.get(c.key) is not None and verdicts.get(c.key).verdict in ("consistent", "corrected")
            for c in candidates
        )
    )
    return points, verdict_map, check_errors, rejected, dropped_marks, refusals, fully_checked, topic_of


def _bottom_line_disputed_labels(
    checked_sections: Sequence[ReportSection], label_by_finding_id: Mapping[str, str],
    marked_labels: frozenset[str], marked_statement_ids: frozenset[str],
) -> frozenset[str]:
    """The finding labels a bottom-line sentence must
    carry a difference marker to cite -- the writer's own kept, marked
    ``disputes: true`` points' labels (``marked_labels``), plus an
    exclusive hop: a *sharing* statement's own label (a checked
    statement, other than a marked point, that cites one of
    ``marked_labels``) joins only when no *other* checked statement --
    one that is neither a marked point nor itself a sharing statement
    -- also cites it. A finding only the disputed step's own statements
    cite is part of the dispute (a bottom-line sentence
    citing a pro-side source only that step's statement cites); a
    widely cited finding never joins, since a sentence citing it alone
    states an ordinary fact, not the step the "Disputed" block names --
    the guard must never reach a sentence the block itself never lists
    as disputed."""
    statement_labels: list[tuple[str, set[str]]] = []
    for section in checked_sections:
        for point in section.points:
            if point.statement is None:
                continue
            labels = {
                label_by_finding_id[fid] for fid in point.statement.finding_ids
                if fid in label_by_finding_id
            }
            if labels:
                statement_labels.append((point.statement.statement_id, labels))
    sharing_label_sets = [
        labels for statement_id, labels in statement_labels
        if statement_id not in marked_statement_ids and labels & marked_labels
    ]
    other_label_sets = [
        labels for statement_id, labels in statement_labels
        if statement_id not in marked_statement_ids and not (labels & marked_labels)
    ]
    other_labels: set[str] = set().union(*other_label_sets) if other_label_sets else set()
    disputed_labels: set[str] = set(marked_labels)
    for labels in sharing_label_sets:
        disputed_labels.update(label for label in labels if label not in other_labels)
    return frozenset(disputed_labels)


async def _run_bottom_line(
    task: ReportWriterTask, outcomes: Sequence[_PartOutcome], *, provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None, check_gate: asyncio.Semaphore,
    batch_size: int, label_urls: Mapping[str, str], label_finding_ids: Mapping[str, str],
) -> tuple[list[ReportPoint], dict[str, str], list[ResearchError], list[RejectedDraftPoint],
          list[tuple[str, str]], set[str], _LineLayout | None]:
    """The bottom-line call, started once every part task has
    finished. Returns (points, temp verdicts, errors, rejected, dropped_marks,
    moved_statement_ids -- source section statement ids a fallback moved into
    the bottom line, so the caller removes them from their sections and a
    sentence is never printed twice -- and the kept points' layout,
    ``None`` when nothing is kept)."""
    findings_by_id = {finding_fingerprint(f): f for f in task.findings}
    src_by_url = sources_by_url(task.sources)
    section_points: list[tuple[ReportSection, list[ReportPoint]]] = []
    any_above_floor = False
    for outcome in outcomes:
        if outcome.section is None:
            continue
        kept_points = [
            point for point in outcome.section.points
            if point.statement is not None
            and outcome.verdicts.get(point.statement.statement_id) in ("consistent", "corrected")
        ]
        if not kept_points:
            continue
        section_points.append((outcome.section, kept_points))
        if any(
            _statement_meets_authority_floor(
                point.statement.finding_ids, findings_by_id, src_by_url, task.authority_floor,
                task.self_descriptions,
            )
            for point in kept_points
        ):
            any_above_floor = True

    # Once at least one checked statement cites a finding at
    # or above the floor, a statement resting only on below-floor findings
    # is withheld from the bottom line's own candidate pool -- it stays
    # printed in its own section (this loop never touches ``outcome.section``
    # itself). When none does, every statement passes through unchanged.
    checked_sections: list[ReportSection] = []
    cited_by_sections: dict[str, Finding] = {}
    label_by_finding_id = {finding_fingerprint(f): label for label, f in task.registry}
    for section, kept_points in section_points:
        if any_above_floor:
            kept_points = [
                point for point in kept_points
                if _statement_meets_authority_floor(
                    point.statement.finding_ids, findings_by_id, src_by_url, task.authority_floor,
                    task.self_descriptions,
                )
            ]
        if not kept_points:
            continue
        checked_sections.append(section.model_copy(update={"points": kept_points}))
        for point in kept_points:
            for finding_id in point.statement.finding_ids:
                label = label_by_finding_id.get(finding_id)
                if label:
                    cited_by_sections[label] = dict(task.registry)[label]

    # Each listed topic and the labels its
    # kept statements cite -- a topic line may cite only those.
    listed_topics = {
        section.coverage_id: frozenset(
            label_by_finding_id[finding_id]
            for point in section.points if point.statement is not None
            for finding_id in point.statement.finding_ids if finding_id in label_by_finding_id
        )
        for section in checked_sections if section.coverage_id
    }
    # Scoped to the writer's own kept ``disputes: true`` marks
    # only, not every ``Finding.disputes`` -- that field still reaches the
    # writer on the registry line ("disputes: yes"), but folding it into
    # this set too would let the materiality rule and the guard disagree (a
    # finding can date or qualify a step the writer correctly judged
    # immaterial to the bottom line and so never marked).
    marked_labels = frozenset().union(*(outcome.disputed_labels for outcome in outcomes))
    marked_statement_ids = frozenset().union(*(outcome.disputed_statement_ids for outcome in outcomes))
    disputed_labels = _bottom_line_disputed_labels(
        checked_sections, label_by_finding_id, marked_labels, marked_statement_ids,
    )
    # The mechanism's own last step, the same way disputed_labels
    # scopes the dispute guard -- the kept, writer-marked ``outcome: true``
    # statements' own labels, computed from ``checked_sections`` so the
    # guard below never demands a citation to something the floor already
    # withheld.
    outcome_statement_ids = frozenset().union(*(outcome.outcome_statement_ids for outcome in outcomes))
    outcome_labels: set[str] = set()
    for section in checked_sections:
        for point in section.points:
            if point.statement is not None and point.statement.statement_id in outcome_statement_ids:
                outcome_labels.update(
                    label_by_finding_id[fid] for fid in point.statement.finding_ids
                    if fid in label_by_finding_id
                )
    outcome_labels = frozenset(outcome_labels)

    if not checked_sections:
        # A part whose draft succeeded but whose Statement Check
        # never came back is "written", not "failed" -- only every
        # non-empty part actually failing earns the "no bottom line" error
        # below. An all-refused draft is "failed" too (so the
        # renderer discloses it), which means this branch covers two
        # different causes that must not share one verdict: a real
        # provider/draft failure (``report_writer_section_failed`` on the
        # outcome) is the non-recoverable ``report_writer_provider_error``,
        # but a part whose draft succeeded and whose points were all
        # explicitly refused is a content outcome, not a provider failure --
        # a recoverable ``report_writer_all_parts_refused`` instead.
        non_empty = [outcome for outcome in outcomes if outcome.status != "empty"]
        if non_empty and all(outcome.status == "failed" for outcome in non_empty):
            provider_failed = any(
                any(e.error_type == "report_writer_section_failed" for e in outcome.errors)
                for outcome in non_empty
            )
            if provider_failed:
                error = agent_error(
                    agent_name=REPORT_WRITER_NAME, error_type="report_writer_provider_error",
                    message="Every part failed; the report has no bottom line.", recoverable=False,
                )
            else:
                error = agent_error(
                    agent_name=REPORT_WRITER_NAME, error_type="report_writer_all_parts_refused",
                    message="Every part's drafted points were refused; the report has no bottom line.",
                )
            return [], {}, [error], [], [], set(), None
        if non_empty:
            # Sections are printed, so the bottom line is left
            # honestly empty rather than claiming "no source could be
            # checked" (that wording is for no citable finding at all, not this).
            error = agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_unchecked",
                message="No section statement was checked and kept; the bottom line is left empty.",
            )
            return [], {}, [error], [], [], set(), None
        return [], {}, [], [], [], set(), None

    is_redraft = bool(task.defects)
    previous_points = task.previous.summary if (is_redraft and task.previous) else []
    previous_topics = (
        {line.statement_id: line.coverage_id for line in task.previous.bottom_line.topic_lines}
        if is_redraft and task.previous is not None and task.previous.bottom_line is not None
        else {}
    )
    bottom_line_defects: list[ReviewDefect] = []
    if is_redraft:
        _, _, bottom_line_defects = _route_defects(task.defects, task.previous, task.targets)
    messages = bottom_line_messages(task, checked_sections, previous=previous_points,
                                    previous_topics=previous_topics,
                                    defects=bottom_line_defects, disputed_statement_ids=marked_statement_ids,
                                    outcome_statement_ids=outcome_statement_ids)
    draft, draft_errors = await _attempt_bottom_line_draft(
        provider, messages, agent_name=REPORT_WRITER_NAME, fingerprint=fingerprint,
    )

    if draft is None:
        fallback_points, fallback_verdicts, moved, fallback_topics = _bottom_line_fallback(
            outcomes, disputed_labels=disputed_labels,
            label_by_finding_id=label_by_finding_id,
            findings_by_id=findings_by_id, sources=src_by_url,
            authority_floor=task.authority_floor, self_descriptions=task.self_descriptions,
            any_above_floor=any_above_floor,
        )
        error = agent_error(
            agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
            message="The bottom-line draft failed twice; one checked section point per topic stands in for it.",
        )
        return (fallback_points, fallback_verdicts, [*draft_errors, error], [], [], moved,
                _assembled_layout(fallback_points, fallback_topics))

    points, verdict_map, check_errors, rejected, dropped_marks, refusals, _, topic_of = (
        await _check_and_finalize_bottom_line(
            task, draft, provider=provider, fingerprint=fingerprint, check_gate=check_gate,
            batch_size=batch_size, cited_by_sections=cited_by_sections, label_urls=label_urls,
            label_finding_ids=label_finding_ids, key_prefix="B", disputed_labels=disputed_labels,
            listed_topics=listed_topics,
        )
    )

    # A mechanism answer whose kept bottom line cites no
    # outcome-marked statement's finding is missing the mechanism's own
    # last step -- the same one re-ask the dispute guard uses, merged
    # into a single re-ask rather than two. Gated on
    # ``outcome_labels`` (built from ``checked_sections``, after the floor
    # and verdict filter), never the writer's raw ``outcome_statement_ids``
    # -- when every outcome-marked point rests only on a sub-floor or
    # declared-derivative source, or was never checked ``consistent`` or
    # ``corrected``, no "# Outcome" block is ever printed, and gating on
    # the unfiltered set would demand a citation to a block that does not
    # exist, on every mechanism answer.
    missing_outcome = (
        task.answer_kind == "explanation" and bool(outcome_labels)
        and not any(
            {
                label_by_finding_id[fid] for fid in point.statement.finding_ids
                if fid in label_by_finding_id
            } & outcome_labels
            for point in points if point.statement is not None
        )
    )

    # One re-ask, carrying the Statement Check's own refusal reasons
    # the same way a redraft carries defects, when it refused a drafted
    # sentence, so a refusal never drops a mechanism step with no retry.
    # The re-ask's own result replaces this attempt's only when it is fully
    # checked (no check error, no rejection, a consistent or corrected
    # verdict for every one of its own candidates) -- never on a check
    # outage or a partial refusal, which would otherwise swap a checked
    # bottom line for unchecked or worse text. Attempt 1's own refusal
    # records stay in the log either way.
    if refusals or missing_outcome:
        reask_defects = [
            ReviewDefect(
                defect_id=f"bottom-line-reask-{n:02d}", kind="missing_support", severity="major",
                problem=(
                    f'The sentence "{text}" was dropped before the check: it cites a '
                    'finding that the statements under "Disputed" cite, and carries '
                    'no word of that dispute. If it states the disputed step, figure '
                    'or provision, restate it with both sides, as those statements '
                    'state and credit them, or leave that step out; if it states '
                    'something else, restate it citing only findings no statement '
                    'under "Disputed" cites, or leave it out.'
                ) if reason == "states a disputed step without its dispute" else (
                    f'The sentence "{text}" was refused by the check and dropped '
                    f'({reason}). Restate what it said from the listed statements '
                    'that state it, credited exactly as they credit it, or leave '
                    'it out when none does.'
                ),
            )
            for n, (text, reason) in enumerate(refusals, start=1)
        ]
        if missing_outcome:
            reask_defects.append(
                ReviewDefect(
                    defect_id=f"bottom-line-reask-{len(reask_defects) + 1:02d}",
                    kind="missing_support", severity="major",
                    problem=(
                        'The bottom line names no outcome. State the outcome the '
                        'statements under "Outcome" state, credited and dated as they '
                        "state it, within the answer's two sentences: fold it into the "
                        'last answer sentence or replace one, never add a third.'
                    ),
                )
            )
        reask_messages = bottom_line_messages(task, checked_sections, previous=points,
                                              previous_topics=topic_of,
                                              defects=reask_defects, disputed_statement_ids=marked_statement_ids,
                                              outcome_statement_ids=outcome_statement_ids)
        reask_draft, reask_draft_errors = await _attempt_bottom_line_draft(
            provider, reask_messages, agent_name=REPORT_WRITER_NAME, fingerprint=fingerprint,
        )
        draft_errors = [*draft_errors, *reask_draft_errors]
        if reask_draft is not None:
            (reask_points, reask_verdict_map, reask_check_errors, reask_rejected,
             reask_dropped_marks, _, reask_fully_checked, reask_topic_of) = await _check_and_finalize_bottom_line(
                task, reask_draft, provider=provider, fingerprint=fingerprint, check_gate=check_gate,
                batch_size=batch_size, cited_by_sections=cited_by_sections, label_urls=label_urls,
                label_finding_ids=label_finding_ids, key_prefix="R", disputed_labels=disputed_labels,
                listed_topics=listed_topics,
            )
            if reask_fully_checked:
                points, verdict_map, dropped_marks, topic_of = (
                    reask_points, reask_verdict_map, reask_dropped_marks, reask_topic_of,
                )
                rejected = [*rejected, *reask_rejected]
                check_errors = [*check_errors, *reask_check_errors]

    if not points:
        # A drafted bottom line that ends up with nothing kept
        # (every sentence refused by the label-subset rule, the Statement
        # Check, or another mechanical rule) still gets the fallback, not a
        # silent empty summary.
        fallback_points, fallback_verdicts, moved, fallback_topics = _bottom_line_fallback(
            outcomes, disputed_labels=disputed_labels,
            label_by_finding_id=label_by_finding_id,
            findings_by_id=findings_by_id, sources=src_by_url,
            authority_floor=task.authority_floor, self_descriptions=task.self_descriptions,
            any_above_floor=any_above_floor,
        )
        if fallback_points:
            error = agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_failed",
                message=(
                    "Every drafted bottom-line sentence was refused; one checked section point "
                    "per topic stands in for it."
                ),
            )
            return (fallback_points, fallback_verdicts,
                    [*draft_errors, *check_errors, error], rejected, dropped_marks, moved,
                    _assembled_layout(fallback_points, fallback_topics))

    layout = _drafted_layout(points, topic_of)
    errors = [*draft_errors, *check_errors]
    if layout is not None and not layout.answer_keys:
        # Topic lines kept, no answer sentence. The renderer prints
        # its own disclosure line; this keeps the loss in the run's record too, in plain
        # words (no provider text).
        errors.append(agent_error(
            agent_name=REPORT_WRITER_NAME, error_type="report_writer_bottom_line_no_answer",
            message=(
                "No direct-answer sentence was kept after the check; the bottom line "
                "holds the topic lines alone."
            ),
        ))
    return points, verdict_map, errors, rejected, dropped_marks, set(), layout


def _with_moved_points_restored(
    previous: ReportComposition | None, titles: Mapping[str, str],
) -> ReportComposition | None:
    """The previous composition as its parts wrote it, when its bottom
    line was the fallback's.

    The fallback *moves* one point of every topic out of its section into the
    bottom line, and a later pass (a note pass, a redraft) carries each part's previous
    section over while it drafts the bottom line afresh -- so every moved point would
    vanish from the report, its line having gone with the old bottom line. Each topic
    line of an assembled layout is that moved point (``previous.summary`` holds it,
    verdict in ``statement_verdicts``): it goes back at the head of its section, where
    the pick came from, and a section the move emptied is rebuilt from the plan's title
    for it and the line's own label. A drafted layout moved nothing, so ``previous`` is
    returned as it is. Routing a defect by statement id reads the restored sections
    too, so a defect on a moved point reaches the part that wrote it."""
    layout = previous.bottom_line if previous is not None else None
    if previous is None or layout is None or not layout.assembled:
        return previous
    by_id = {point.statement.statement_id: point for point in previous.summary if point.statement is not None}
    sections = list(previous.sections)
    position = {section.coverage_id: n for n, section in enumerate(sections) if section.coverage_id}
    for line in layout.topic_lines:
        point = by_id.get(line.statement_id)
        if point is None:
            continue
        held = position.get(line.coverage_id)
        if held is None:
            title = titles.get(line.coverage_id)
            if title is None:
                continue
            position[line.coverage_id] = len(sections)
            sections.append(ReportSection(
                title=title, short_title=line.label.removeprefix(NOTE_LABEL_PREFIX),
                points=[point], coverage_id=line.coverage_id,
            ))
        elif all(held_point.statement_id != line.statement_id for held_point in sections[held].points):
            sections[held] = sections[held].model_copy(update={"points": [point, *sections[held].points]})
    return previous.model_copy(update={"sections": sections})


def _topic_line_order(coverage_id: str, task: ReportWriterTask) -> tuple[int, int]:
    """The plan's topics in plan order, then the active notes' topics
    in receipt order (``n1`` first)."""
    if coverage_id in task.note_labels:
        return (1, int(coverage_id.removeprefix(f"{NOTE_COVERAGE_PREFIX}n")))
    plan = [topic.coverage_id for topic in task.sub_topics]
    return (0, plan.index(coverage_id) if coverage_id in plan else len(plan))


def _ordered_bottom_line(
    points: Sequence[ReportPoint], layout: _LineLayout | None, task: ReportWriterTask,
) -> tuple[list[ReportPoint], list[tuple[str, str]]]:
    """The kept bottom line in the order the report prints it: the
    answer, then the topic lines; and the topic lines' ``(coverage_id, key)``."""
    if layout is None:
        return list(points), []
    by_key = {point.statement_id: point for point in points}
    topic_keys = sorted(layout.topic_keys, key=lambda pair: _topic_line_order(pair[0], task))
    ordered = [by_key[key] for key in layout.answer_keys] + [by_key[key] for _, key in topic_keys]
    return ordered, topic_keys


def _renumber(
    bottom_line_points: list[ReportPoint], sections: list[ReportSection],
) -> tuple[list[ReportPoint], list[ReportSection], dict[str, str]]:
    """Statement ids renumbered S001… in render order (bottom
    line, then sections in plan order), so the output is identical whatever
    order the calls completed in."""
    remap: dict[str, str] = {}
    counter = iter(range(1, 100_000))

    def renumber_point(point: ReportPoint) -> ReportPoint:
        if point.statement is None:
            return point
        new_id = f"S{next(counter):03d}"
        remap[point.statement.statement_id] = new_id
        new_statement = point.statement.model_copy(update={"statement_id": new_id})
        return point.model_copy(update={"statement": new_statement})

    new_summary = [renumber_point(point) for point in bottom_line_points]
    new_sections = [
        section.model_copy(update={"points": [renumber_point(point) for point in section.points]})
        for section in sections
    ]
    return new_summary, new_sections, remap


# --- assembly: the table, page credits, unreachable -------------------------


def _findings_by_url(findings: Sequence[Finding]) -> dict[str, Finding]:
    """Normalized source URL -> the first citable finding seen for it."""
    by_url: dict[str, Finding] = {}
    for finding in findings:
        normalized = normalize_source_url(finding.source_url)
        if normalized not in by_url:
            by_url[normalized] = finding
    return by_url


def _page_credit(
    url: str, *, findings_by_url: Mapping[str, Finding], reads: Mapping[str, ReadRecord],
    sources: Sequence[ScoredSource],
) -> PageCredit:
    """The publisher from the page's own words (or the host),
    and the date in order: the read's own ``page_updated`` when it is later
    than its ``page_published`` (``date_kind`` marks which); else
    ``page_published``; else ``page_updated``; else the Source Evaluator's
    validated ``publication_date``, used only when the page carries no
    metadata date of its own -- the evaluator sees only excerpts and can
    admit a date the page's text merely mentions (a content date
    such as a product's release date is not the page's own date) -- never
    a figure's ``statement_date`` or its vintage: those are the figure's,
    not the page's.
    """
    # Imported at call time, matching this module's other evidence_verifier
    # seams: no import cycle (evidence_verifier never imports this module at
    # top level), and it keeps the substitution point tests use consistent.
    from deep_research.agents.evidence_verifier import evaluated_page_date, page_owner

    finding = findings_by_url.get(url)
    read = reads.get(finding.read_id) if finding is not None else None
    publisher = page_owner(read) if read is not None else publisher_identity(url)
    if read is not None:
        # A page's own later update outranks its original publication --
        # credited with the existing "(updated YYYY-MM-DD)" rendering, so a
        # reader is never told a page updated since is decades stale by its
        # creation date alone.
        if read.page_published and read.page_updated and read.page_updated > read.page_published:
            return PageCredit(publisher=publisher, date=read.page_updated, date_kind="updated")
        if read.page_published:
            return PageCredit(publisher=publisher, date=read.page_published, date_kind="published")
        if read.page_updated:
            return PageCredit(publisher=publisher, date=read.page_updated, date_kind="updated")
        validated = evaluated_page_date(sources, read)
        if validated:
            return PageCredit(publisher=publisher, date=validated, date_kind="published")
    return PageCredit(publisher=publisher, date=None, date_kind=None)


def _unreachable_pages(
    sub_topics: Sequence[SubTopic], acquisition_state_by_target: Mapping[str, AcquisitionState],
) -> list[UnreachablePage]:
    """For each sub-topic with >= 1 required target, its own
    denied URLs with the candidate record's title and denial reason,
    deduplicated, in plan order."""
    pages: list[UnreachablePage] = []
    seen: set[str] = set()
    for topic in sub_topics:
        if not any(target.required for target in topic.evidence_targets):
            continue
        state = acquisition_state_by_target.get(topic.coverage_id)
        if state is None:
            continue
        for url in state.denied_urls:
            normalized = normalize_source_url(url)
            if normalized in seen:
                continue
            seen.add(normalized)
            record = state.candidate_records.get(normalized) or state.candidate_records.get(url)
            pages.append(UnreachablePage(
                url=url,
                title=record.title if record is not None else "",
                reason=(record.denial_reason or "") if record is not None else "",
            ))
    return pages


def _assemble_composition(composition: ReportComposition, task: ReportWriterTask) -> ReportComposition:
    """Assemble, in order: the table (driven
    by ``answer_kind``, already frozen on ``composition``), then page
    credits for every URL the table or a kept statement now cites, then the
    unreachable pages. Table builders and citation order are pure functions
    of ``composition`` alone; nothing here re-reads a page.

    The table and the credits are mutually dependent: ``build_table`` reads
    ``composition.page_credits`` to name a relayed or unattributed row's
    publisher (``report_table._who_text``/``_recommended_by_cell``), but the
    final credits are keyed on ``written_citations(composition)``, which
    itself reads the table (the citation order is the bottom line, then the
    sections, then the table, as the report prints them). So a provisional
    map -- every finding URL's credit, the same
    ``_page_credit`` call the final map uses -- is set before the table is
    built; the final map then only re-keys it to the table's own citations,
    never recomputing a credit.
    """
    findings_by_url = _findings_by_url(composition.findings)
    provisional_credits = {
        url: _page_credit(url, findings_by_url=findings_by_url, reads=task.reads, sources=task.sources)
        for url in findings_by_url
    }
    composition = composition.model_copy(update={"page_credits": provisional_credits})
    table = _build_table(composition)
    composition = composition.model_copy(update={"table": table})
    page_credits = {
        citation.url: provisional_credits.get(citation.url) or _page_credit(
            citation.url, findings_by_url=findings_by_url, reads=task.reads, sources=task.sources,
        )
        for citation in written_citations(composition)
    }
    composition = composition.model_copy(update={"page_credits": page_credits})
    unreachable = _unreachable_pages(task.sub_topics, task.acquisition_state_by_target)
    return composition.model_copy(update={"unreachable": unreachable})


async def compose_written_report(
    task: ReportWriterTask,
    *,
    provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None = None,
    batch_size: int | None = None,
    concurrency: int | None = None,
    section_concurrency: int = DEFAULT_WRITER_SECTION_CONCURRENCY,
) -> ReportComposition:
    """Partition, draft every part in parallel, pipeline each part's
    Statement Check off its own draft, then write the bottom line last from
    the checked section statements. A redraft re-asks only the
    parts a material defect names, and a draft after a note pass only
    the notes' own parts; every other part
    is carried over unchanged. ``batch_size``/``concurrency`` are the Statement Check's
    bounds (``None`` uses this module's defaults); ``section_concurrency``
    bounds how many section drafts run at once.
    """
    resolved_batch_size = batch_size if batch_size is not None else _CHECK_BATCH_SIZE_DEFAULT
    resolved_concurrency = concurrency if concurrency is not None else _CHECK_CONCURRENCY_DEFAULT
    label_urls = {label: f.source_url for label, f in task.registry}
    label_finding_ids = {label: finding_fingerprint(f) for label, f in task.registry}

    if not task.registry:
        empty = ReportComposition(
            question=task.question, session_id=task.session_id, iteration=task.iteration,
            max_extra_passes=task.max_extra_passes, as_of=task.as_of, scope=task.scope,
            sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
            summary=[], sections=[], parts=[], rejected=[], rejected_points=[],
            fact_rows=list(task.facts), not_found=list(task.not_found), finding_labels={},
            statement_verdicts={}, statement_passages=task.passages, generated_on=task.generated_on,
            errors=[agent_error(
                agent_name=REPORT_WRITER_NAME, error_type="report_writer_no_verified_findings",
                message="No verified finding could be cited; the report is the recorded facts alone.",
            )],
            answer_kind=task.answer_kind,
            reader_answers=[answer.value for answer in task.reader_answers],
        )
        return _assemble_composition(empty, task)

    citable = citable_findings(task.findings)
    placements, _unplaced = report_parts(citable, task.targets, task.sub_topics)
    src_by_url = sources_by_url(task.sources)
    previous = _with_moved_points_restored(
        task.previous, {placement.coverage_id: placement.sub_topic_title for placement in placements},
    )
    previous_sections_by_coverage = {
        s.coverage_id: s for s in (previous.sections if previous else []) if s.coverage_id
    }
    is_redraft = bool(task.defects)
    if is_redraft:
        routed_coverage_ids, defects_by_coverage, _ = _route_defects(task.defects, previous, task.targets)
    else:
        routed_coverage_ids, defects_by_coverage = set(), {}

    part_findings: list[tuple[PartPlacement, list[Finding], list[Finding]]] = []
    for placement in placements:
        regular: list[Finding] = []
        context: list[Finding] = []
        for finding in placement.findings:
            bucket = context if is_context_only(finding, src_by_url) else regular
            bucket.append(finding)
        part_findings.append((placement, regular, context))
    targets_by_coverage = {
        placement.coverage_id: [t for t in task.targets if t.coverage_id == placement.coverage_id]
        for placement, _, _ in part_findings
    }
    part_weight_sum = max(1, sum(
        _part_weight(targets_by_coverage[placement.coverage_id])
        for placement, regular, _ in part_findings if regular
    ))

    jobs: list[PartJob] = []
    for order, (placement, regular, context) in enumerate(part_findings):
        targets_here = targets_by_coverage[placement.coverage_id]
        previous_section = previous_sections_by_coverage.get(placement.coverage_id)
        if task.note_pass_coverage_ids:
            # After a note pass only the
            # notes' own parts are drafted; every other part is carried over
            # unchanged, and one with no previous section is still drafted
            # (see ``_run_part``).
            redraft_this = placement.coverage_id in task.note_pass_coverage_ids
            defects_here = []
        elif not is_redraft:
            redraft_this, defects_here = True, []
        elif placement.coverage_id in routed_coverage_ids:
            redraft_this, defects_here = True, defects_by_coverage.get(placement.coverage_id, [])
        else:
            redraft_this, defects_here = False, []
        jobs.append(PartJob(
            coverage_id=placement.coverage_id, sub_topic_title=placement.sub_topic_title,
            order=order, targets=targets_here, findings=regular, context_findings=context,
            previous=previous_section, defects=defects_here, redraft=redraft_this,
            part_weight_sum=part_weight_sum,
        ))

    # Writing's counts, published live from here
    # on; the part tasks below copy this context, so each of them reads them too.
    progress = _WritingProgress(sum(1 for job in jobs if _drafts_this_pass(job)))
    _WRITING_PROGRESS.set(progress)
    progress.publish()

    section_gate = asyncio.Semaphore(max(1, section_concurrency))
    check_gate = asyncio.Semaphore(max(1, resolved_concurrency))
    outcomes: list[_PartOutcome | None] = [None] * len(jobs)

    async def run_and_store(index: int, job: PartJob) -> None:
        outcomes[index] = await _run_part(
            job, task, provider=provider, fingerprint=fingerprint, section_gate=section_gate,
            check_gate=check_gate, batch_size=resolved_batch_size, label_urls=label_urls,
            label_finding_ids=label_finding_ids,
        )

    try:
        async with asyncio.TaskGroup() as group:
            for index, job in enumerate(jobs):
                group.create_task(run_and_store(index, job))
    except* ProviderConfigurationError as caught:
        raise caught.exceptions[0]

    resolved_outcomes: list[_PartOutcome] = [outcome for outcome in outcomes if outcome is not None]

    (bottom_line_points, bottom_line_verdicts, bottom_line_errors, bottom_line_rejected,
     bottom_line_dropped_marks, bottom_line_moved_ids, line_layout) = await _run_bottom_line(
        task, resolved_outcomes, provider=provider, fingerprint=fingerprint,
        check_gate=check_gate, batch_size=resolved_batch_size, label_urls=label_urls,
        label_finding_ids=label_finding_ids,
    )

    # The bottom line is settled: its share of the bar is whole even when no
    # sentence of it reached the Statement Check (it fell back, every sentence was
    # refused, or no section was checked), so the bar ends at 1.0. Adding no key
    # leaves the keys already counted as they are.
    progress.bottom_line_drafted(())
    if progress.fraction < 1.0:
        progress.publish()

    # A point the bottom-line fallback promoted into the bottom line
    # is removed from its section, so it is never printed twice; a section
    # left with no points is dropped, matching how a fully-refused section
    # is already dropped elsewhere.
    sections: list[ReportSection] = []
    for outcome in resolved_outcomes:
        if outcome.section is None:
            continue
        remaining = [
            point for point in outcome.section.points
            if point.statement is None or point.statement.statement_id not in bottom_line_moved_ids
        ]
        if remaining:
            sections.append(outcome.section.model_copy(update={"points": remaining}))
    ordered_points, topic_keys = _ordered_bottom_line(bottom_line_points, line_layout, task)
    new_summary, new_sections, remap = _renumber(ordered_points, sections)
    # A topic line's label is its section's short title -- taken before the
    # fallback's move, which can empty a section -- or its note's label.
    short_titles = {
        outcome.job.coverage_id: outcome.section.short_title or outcome.section.title
        for outcome in resolved_outcomes if outcome.section is not None
    }
    bottom_line = None
    if line_layout is not None:
        bottom_line = BottomLineLayout(
            answer_ids=[remap[key] for key in line_layout.answer_keys],
            topic_lines=[
                BottomLineTopic(
                    coverage_id=coverage_id,
                    label=task.note_labels.get(coverage_id) or short_titles.get(coverage_id, coverage_id),
                    statement_id=remap[key],
                )
                for coverage_id, key in topic_keys
            ],
            assembled=line_layout.assembled,
        )

    temp_verdicts: dict[str, str] = dict(bottom_line_verdicts)
    for outcome in resolved_outcomes:
        temp_verdicts.update(outcome.verdicts)
    statement_verdicts = {remap[old]: verdict for old, verdict in temp_verdicts.items() if old in remap}

    parts_records = [
        ReportPart(
            coverage_id=job.coverage_id, sub_topic_title=job.sub_topic_title,
            finding_ids=[finding_fingerprint(f) for f in [*job.findings, *job.context_findings]],
            context_finding_ids=[finding_fingerprint(f) for f in job.context_findings],
            status=outcome.status,
        )
        for job, outcome in zip(jobs, resolved_outcomes)
    ]

    all_errors: list[ResearchError] = list(bottom_line_errors)
    all_rejected: list[RejectedDraftPoint] = []
    raw_dropped_marks: list[tuple[str, str]] = list(bottom_line_dropped_marks)
    for outcome in resolved_outcomes:
        all_errors.extend(outcome.errors)
        all_rejected.extend(outcome.rejected)
        raw_dropped_marks.extend(outcome.dropped_marks)
    all_rejected.extend(bottom_line_rejected)
    dropped_marks = [f"{remap.get(key, key)}: {message}" for key, message in raw_dropped_marks]

    composed = ReportComposition(
        question=task.question, session_id=task.session_id, iteration=task.iteration,
        max_extra_passes=task.max_extra_passes, as_of=task.as_of, scope=task.scope,
        sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
        summary=new_summary, sections=new_sections, parts=parts_records,
        rejected=[r.reason for r in all_rejected], rejected_points=all_rejected,
        fact_rows=list(task.facts), not_found=list(task.not_found),
        finding_labels={label: finding_fingerprint(f) for label, f in task.registry},
        statement_verdicts=statement_verdicts, statement_passages=task.passages,
        generated_on=task.generated_on, errors=all_errors, answer_kind=task.answer_kind,
        dropped_marks=dropped_marks, bottom_line=bottom_line,
        reader_answers=[answer.value for answer in task.reader_answers],
    )
    return _assemble_composition(composed, task)


def finding_memory_payload(finding: Finding, *, session_id: str) -> tuple[str, dict[str, object]]:
    """What long-term memory keeps of one cited finding of an accepted report."""
    figures = [
        f"{r.figure.value} {r.figure.unit} ({r.context.kind}, {r.context.period or 'period not stated'}, {r.context.organisation})"
        for r in (finding.verification.figure_results if finding.verification else [])
        if r.kept and r.context is not None
    ]
    metadata: dict[str, object] = {
        "session_id": session_id, "finding_id": finding_fingerprint(finding),
        "source_url": finding.source_url, "source_title": finding.source_title,
        "figures": "; ".join(figures),
        "verification": finding.verification.status if finding.verification else "unchecked",
        "context_unchecked": bool(finding.verification and finding.verification.context_unchecked),
    }
    return finding.snippet or finding.content, metadata


def report_written_event(result: WrittenReport) -> ResearchEvent:
    composition = result.composition
    failed_parts = [p.coverage_id for p in composition.parts if p.status == "failed"]
    return agent_event(
        agent_name=REPORT_WRITER_NAME,
        event_type="report_writer.report.written",
        message=(
            f"Wrote {result.statement_count} statement(s) citing {result.citation_count} "
            f"source(s); {result.refused_count} drafted point(s) refused."
        ),
        metadata={
            "statements": result.statement_count,
            "citations": result.citation_count,
            "refused": result.refused_count,
            "fact_rows": len(composition.fact_rows),
            "not_found": len(composition.not_found),
            "table": composition.table.shape if composition.table else None,
            "parts": len(composition.parts),
            "failed_parts": failed_parts,
        },
    )


class ReportWriterAgent(BaseAgent[WrittenReport]):
    """Write the reader-facing prose of a research report from verified findings.

    Runs no ReAct loop: one call drafts each plan part's section, pipelined
    against its own Statement Check, and one more writes the bottom line
    last. Everything structural -- the table, the list of what could not be
    confirmed, the labels and the sources -- is rendered locally from the
    verified snapshot, so the required sections exist and every fact is
    cited even when a part's call fails.
    """

    name = REPORT_WRITER_NAME
    description = "Write the reader-facing prose of a research report from verified findings."
    allowed_tools = ("write_document", "save_to_memory")

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
        clock: Clock = utc_now,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
            model_profile=model_profile,
        )
        probe = clock()
        if probe.tzinfo is None or probe.utcoffset() is None:
            raise AgentConfigurationError(
                "ReportWriterAgent clock must return a timezone-aware "
                "datetime; got a naive datetime instead"
            )
        self._clock = clock

    @property
    def output_schema(self) -> type[WrittenReport]:
        return WrittenReport

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return SECTION_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> ReportWriterTask:
        """Bind this run to the verified findings recorded so far."""
        targets = [target for topic in state.sub_topics for target in topic.evidence_targets]
        findings = list(state.verified_findings)
        citable = citable_findings(findings)
        answered = answered_target_ids(citable, targets, sub_topics=state.sub_topics)
        src_by_url = sources_by_url(state.evaluated_sources)
        findings_by_id = {finding_fingerprint(f): f for f in citable}
        authority_floor = self.config.writer_authority_floor
        # The Not-found computation excludes context-only answers, so
        # a required target answered only that way reads as unconfirmed; the
        # gate's own ``answered`` (below, unfiltered) still accounts for it.
        # A *bound* finding is never context-only, so this can exclude only
        # an unbound finding matched here through the sub-topic fallback.
        answered_for_report: dict[str, list[str]] = {}
        for target_id, finding_ids in answered.items():
            kept = [
                fid for fid in finding_ids
                if fid not in findings_by_id or not is_context_only(findings_by_id[fid], src_by_url)
            ]
            if kept:
                answered_for_report[target_id] = kept
        # The frozen contract's own requested length when the question
        # asked for one, else the config's reader-length default -- the
        # per-part point budget's own word count (section_messages).
        budget_words = (
            state.answer_contract.requested_word_limit
            if state.answer_contract and state.answer_contract.requested_word_limit is not None
            else self.config.report_target_words
        )
        # After a note pass only the notes' own parts and the bottom line
        # are drafted; the rest is carried.
        note_pass_coverage_ids = _note_pass_coverage_ids(state)
        return ReportWriterTask(
            instruction=state.original_question,
            session_id=state.session_id,
            iteration=state.iteration,
            max_extra_passes=state.max_extra_passes,
            question=state.original_question,
            as_of=report_as_of(findings=findings, reads=list(state.read_records.values())),
            scope=report_scope(state.sub_topics),
            generated_on=self._clock().date().isoformat(),
            answer_kind=state.answer_contract.answer_kind if state.answer_contract else None,
            sub_topics=list(state.sub_topics),
            targets=targets,
            findings=findings,
            sources=list(state.evaluated_sources),
            registry=finding_registry(findings, targets, state.sub_topics, state.evaluated_sources),
            facts=fact_rows(findings, targets, state.sub_topics),
            not_found=not_found_targets(state.sub_topics, answered_for_report, state.acquisition_state_by_target),
            answered=answered,
            reads=dict(state.read_records),
            passages=statement_passages(findings, state.read_records),
            self_descriptions=_read_self_descriptions(state.read_records),
            defects=material_defects(state.report_review) if _is_redraft_hop(state) else [],
            previous=(
                state.composition
                if _is_redraft_hop(state) or note_pass_coverage_ids
                else None
            ),
            note_pass_coverage_ids=note_pass_coverage_ids,
            acquisition_state_by_target=dict(state.acquisition_state_by_target),
            target_words=budget_words,
            reader_answers=list(state.reader_answers),
            note_labels={
                f"{NOTE_COVERAGE_PREFIX}{note.note_id}": note_label(note)
                for note in active_reader_notes(state.reader_notes)
            },
            # The steering views only; a note whose
            # only kind is new_angle is its own part of the report instead.
            reader_notes=render_reader_notes(
                steering_notes(active_reader_notes(state.reader_notes)),
                instruction=WRITING_NOTES,
            ),
            authority_floor=authority_floor,
        )

    async def _compose_result(self, task: ReportWriterTask) -> WrittenReport:
        composition = await compose_written_report(
            task, provider=self.provider, fingerprint=self.fingerprint_call,
            batch_size=self.config.verifier_batch_size,
            concurrency=self.config.verifier_concurrency,
            section_concurrency=self.config.writer_section_concurrency,
        )
        return WrittenReport(
            markdown=render_written_report(composition),
            evidence_markdown=render_finding_log(composition),
            composition=composition,
            statement_count=len(composition.statements),
            citation_count=len(written_citations(composition)),
            refused_count=len(composition.rejected_points),
        )

    async def finalize(self, task: AgentTask, run: ReActRun) -> WrittenReport | None:
        """Adapt composition to the ``BaseAgent`` hook.

        ``run`` calls the pieces directly so it can keep the errors this hook
        signature has nowhere to return.
        """
        del run
        if not isinstance(task, ReportWriterTask):
            raise AgentConfigurationError(
                "ReportWriterAgent.finalize requires a ReportWriterTask"
            )
        return await self._compose_result(task)

    def state_update(
        self,
        result: WrittenReport | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The ``BaseAgent`` hook's own answer; ``run`` never calls this."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["report"] = result.markdown
            update["report_evidence"] = result.evidence_markdown
            update["composition"] = result.composition
            update["unique_source_count"] = result.citation_count
            update["events"] = [report_written_event(result)]
        return update

    def _require_tool(self, name: str) -> BaseTool:
        """The declared tool the terminal writer needs, or a loud failure.

        ``allowed_tools`` is validated at construction, so a missing tool here
        means the agent was assembled around this method's back.
        """
        tool = self.toolset.get(name)
        if tool is None:
            raise AgentConfigurationError(f"{name} was not injected")
        return tool

    async def publish_document(
        self,
        *,
        filename: str,
        content: str,
    ) -> ToolResult:
        """Write one composed artifact through ``write_document``.

        Called only by the terminal finalizer. Returns the tool's own result,
        including a failure: a write that did not happen is an outcome the
        finalizer records, not an exception it has to catch.
        """
        tool = self._require_tool("write_document")
        return await tool.execute(filename=filename, content=content)

    async def publish_finding(
        self,
        *,
        content: str,
        metadata: Mapping[str, object],
    ) -> ToolResult:
        """Save one verified claim through ``save_to_memory``.

        Called only by the terminal finalizer, and only for a report whose
        terminal quality status is ``accepted``.
        """
        tool = self._require_tool("save_to_memory")
        return await tool.execute(content=content, metadata=dict(metadata))

    async def publish_findings(
        self,
        *,
        findings: Sequence[tuple[str, Mapping[str, object]]],
    ) -> ToolResult:
        """Save every cited finding in one memory write.

        Called only by the terminal finalizer, for an accepted report. A
        failed result -- any finding refused, or a memory tool without a batch
        path -- is the finalizer's cue to write the findings one by one, so
        each failure is still recorded against its own finding.
        """
        tool = self._require_tool("save_to_memory")
        if not isinstance(tool, SaveToMemoryTool):
            return ToolResult(
                tool_name="save_to_memory",
                success=False,
                error=ToolError(
                    type="batch_unsupported",
                    message="the memory tool cannot save a batch",
                ),
                latency_ms=0.0,
            )
        return await tool.save_many(findings)  # type: ignore[arg-type]

    async def run(self, state: ResearchState) -> AgentRun[WrittenReport]:
        """Partition, draft and compose the report, recording every count.

        No ReAct loop runs, so the returned ``ReActRun`` is synthetic with
        zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when there was something to cite, every
        non-empty part failed, and at least one of them is a genuine
        provider/draft failure: an empty verified snapshot
        is not a failure, it is an honest report of no evidence, and a part
        whose draft succeeded but whose points were all refused is a content
        outcome the recoverable ``report_writer_all_parts_refused`` records
        -- ``agents.steps.ReActRun.succeeded`` reads only ``"provider_error"``
        as non-recoverable, so that case must still finish.
        """
        task = self.build_task(state)
        async with self.tracker.agent_span(self.name) as span:
            result = await self._compose_result(task)
            span.set_outputs({
                "agent_name": self.name,
                "statement_count": result.statement_count,
                "citation_count": result.citation_count,
                "refused_count": result.refused_count,
            })
        composition = result.composition
        any_written = any(part.status in ("written", "carried_over") for part in composition.parts)
        provider_failed = any(error.error_type == "report_writer_provider_error" for error in composition.errors)
        stop_reason = (
            "provider_error" if (not any_written and task.registry and provider_failed) else "finished"
        )
        react = ReActRun(
            agent_name=self.name,
            stop_reason=stop_reason,
            errors=list(composition.errors),
        )
        written = report_written_event(result)
        publish_live(written)  # returned below as well
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=react,
            errors=list(composition.errors),
            state_update={
                "report": result.markdown,
                "report_evidence": result.evidence_markdown,
                "composition": result.composition,
                "unique_source_count": result.citation_count,
                "errors": list(composition.errors),
                "events": [written],
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
