"""The Planner: turn one research question into a validated research plan.

The provider is asked for ``ResearchPlanDraft``, not for a plan of real
``SubTopic`` values. ``SubTopic`` declares ``Field(min_length=1)`` on its
string and list fields, which Pydantic renders as ``minLength``/``minItems``
— keywords outside OpenAI's strict structured-output subset. The draft
models carry no constraints at all, and ``validate_plan_draft`` applies the
domain rules locally where their failures can be turned into a repair prompt.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, TypeAlias, get_args

from pydantic import Field, ValidationError, field_validator, model_validator

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_EFFORT,
    OUTPUT_LIMIT_RETRY_OUTCOMES,
    OUTPUT_LIMIT_RETRY_READINGS,
    AgentCompleter,
    AgentRun,
    BaseAgent,
)
from deep_research.agents.errors import (
    AgentConfigurationError,
    PlanningError,
    agent_error,
    agent_provider_failure_details,
    planning_provider_error,
)
from deep_research.agents.events import agent_event, publish_live
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.toolset import AgentToolset
from deep_research.agents.validation import _invalid_fields
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.text import collapse_whitespace
from deep_research.utils.types import (
    MAX_TARGETS_PER_TOPIC,
    AnswerContract,
    AnswerKind,
    ContractModel,
    EvidenceTarget,
    FigureKind,
    MemorySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    SubTopic,
    _ENERGY_UNIT,
    _POWER_UNIT,
    counted_evidence_targets,
)

PLANNER_NAME = "planner"
# The floor is one sub-topic: a one-part question is one sub-topic, and
# requiring three made a small question's size — not its content — the reason
# a run reached ``graph_planning_failed`` (D11). Every reader of the bound
# follows this constant, so the instruction, the plan validators and the
# planning event's metadata all move together.
MIN_SUB_TOPICS = 1
MAX_SUB_TOPICS = 10
MIN_TARGETS_PER_TOPIC = 1
_COVERAGE_ID_WIDTH = 2

# How many times one planning run may ask the semantic review for a verdict:
# the initial verdict, and one confirming verdict on the plan repaired from it.
# Two rather than one because a repair nobody re-reviews is a plan accepted on
# hope; bounded at two because an unbounded review loop is a planning pass that
# never ends. ``_review_plan`` enforces it, so the flow cannot quietly grow a
# third call.
MAX_PLAN_REVIEW_CALLS = 2

# Which plan a problem came from. Every problem the planner reports is labelled
# with one of these, because the merged, unlabelled list is what made the live
# run 3 diagnosis read the draft's defects as the repaired plan's.
_PLAN_DRAFT_LABEL = "draft"
_PLAN_REPAIR_LABEL = "repair"
_PLAN_REVIEW_REPAIR_LABEL = "review_repair"

# The typed record for a planning pass that continued with unresolved defects.
# Non-halting on purpose: a stale-anchor lint or a review verdict is a
# judgement about meaning, and the run's own report-level gates judge the
# consequences of carrying it. The stage names the flow step the outcome
# belongs to, and ``_PLAN_DEFECT_MESSAGES`` is the sentence each one publishes.
# Private, like the ledger's ``_SUB_TOPIC_SKIP_ERROR_TYPE``: the *string* is
# the published contract and the constant is only how this module spells it, so
# a consumer reads the record rather than importing a name.
_PLAN_DEFECTS_ERROR_TYPE = "planner_plan_defects_unresolved"

_PLAN_DEFECT_MESSAGES: dict[str, str] = {
    "plan_checks": (
        "The plan stands with the local defects its one repair did not remove"
    ),
    "plan_repair": (
        "The repair of the plan's local defects was not usable, so the earlier "
        "plan stands"
    ),
    "review": (
        "The plan review reported defects that no repair removed, so they are "
        "recorded against the plan that stands"
    ),
    "plan_review": (
        "The plan review could not be produced, so the plan it never judged "
        "stands"
    ),
    "review_repair": (
        "The repair of the plan review's findings was not usable, so the plan "
        "the review judged stands"
    ),
    "confirming_review": (
        "The confirming plan review still reported defects, so the repaired "
        "plan stands"
    ),
}

# Mirrors the Researcher's injected clock: a callable returning a
# timezone-aware datetime. The planner stamps its as-of date from this and
# never from model knowledge or memory, so a test can pin "today" without
# patching the standard library.
Clock: TypeAlias = Callable[[], datetime]


def utc_now() -> datetime:
    """The wall clock the planner stamps ``as_of_date`` from by default."""
    return datetime.now(timezone.utc)


# --- answer-form vocabulary -------------------------------------------------

# What a satisfactory answer to each form looks like. The form is printed in
# the answer contract (``render_answer_contract``) that the planning and later
# requests carry, never written into every target's dimensions.
_ANSWER_FORM_REQUIREMENTS: dict[AnswerKind, str] = {
    "constraints": (
        "answer form: a list of the binding constraints, each with the "
        "instrument, rule, or authority that imposes it and the date it took "
        "effect"
    ),
    "comparison": (
        "answer form: the same dimension, measured or described, for every "
        "option compared, on one shared basis"
    ),
    "explanation": (
        "answer form: a causal mechanism with evidence for each step, not a "
        "correlation and not a restatement of the outcome"
    ),
    "factual": (
        "answer form: the specific thing asked for, in the form its evidence "
        "takes — a figure with its value, unit and date; items with their "
        "attributes; dated events; reasons; a rule's provisions"
    ),
    "historical": (
        "answer form: the state of affairs in the period the question names, "
        "dated to that period and never substituted with today's figures"
    ),
}

# Ordered deliberately: a question that both compares and concerns a
# regulation is a comparison first, because two incomparable measurements do
# not answer it. Each marker is matched case-insensitively against the
# normalized question.
_COMPARISON_MARKERS = (
    "compare",
    "compared with",
    "compared to",
    "versus",
    "vs",
    "difference between",
    "trade-off",
    "tradeoff",
    "which is better",
    "relative to",
)

# A comparative claim names a *referent*: "cost more than the 2019 one",
# "cost more than 2019 regulation", or "slower in California than in Texas".
# A bare inequality does not — "waited more than 5 years", "tariffs of more
# than 10%" are threshold questions — and treating those as comparisons
# stamped the comparison answer form ("the same measured dimension for every
# option compared") onto a quantity question, which Section 2.3 then judges
# the target unanswered against. A definite threshold noun is also not a
# referent: "greater than the 10% threshold" still asks about one rule.
_COMPARATIVE_CUES = (
    "more",
    "less",
    "fewer",
    "faster",
    "slower",
    "higher",
    "lower",
    "greater",
    "smaller",
    "larger",
    "cheaper",
    "costlier",
)
_COMPARATIVE_CUE_PATTERN = (
    rf"(?<![a-z0-9])(?:{'|'.join(_COMPARATIVE_CUES)})(?![a-z0-9])"
)
_COMPARATIVE_THAN_PREFIX = (
    rf"{_COMPARATIVE_CUE_PATTERN}[^.;?!]{{0,40}}?"
    rf"(?<![a-z0-9])than\s+"
)

# Comparison is proved by positive, complete syntax. It is never inferred from
# a token merely because a finite quantity vocabulary failed to recognize it.
# Each pattern consumes the complete right-hand side of the question, so
# ``first percentile`` cannot be accepted at ``first`` and ``the statutory
# limit`` cannot be accepted at ``the``.
_COMPARATIVE_RELATION_PATTERN = re.compile(
    _COMPARATIVE_THAN_PREFIX,
    re.IGNORECASE,
)
_COMPARATIVE_INTRODUCED_PATTERN = re.compile(
    rf"{_COMPARATIVE_THAN_PREFIX}(?:in|for|at|on)\s+[^.;?!]+[.?!]?\s*$",
    re.IGNORECASE,
)
_COMPARATIVE_ANAPHOR_PATTERN = re.compile(
    rf"{_COMPARATIVE_THAN_PREFIX}the\s+(?:(?:19|20)\d{{2}}\s+)?"
    rf"(?:one|ones|other|others|alternative|alternatives)[.?!]?\s*$",
    re.IGNORECASE,
)
_COMPARATIVE_OTHER_SET_PATTERN = re.compile(
    rf"{_COMPARATIVE_THAN_PREFIX}(?:the\s+)?other\s+"
    rf"[a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*){{0,5}}[.?!]?\s*$",
    re.IGNORECASE,
)
_COMPARATIVE_PARALLEL_YEAR_WORK_PATTERN = re.compile(
    rf"(?<![a-z0-9])(?P<left_year>(?:19|20)\d{{2}})\s+"
    rf"(?P<work>[a-z][a-z-]*(?:\s+[a-z][a-z-]*){{0,5}}?)\s+"
    rf"[^.;?!]{{0,40}}?{_COMPARATIVE_THAN_PREFIX}(?:the\s+)?"
    rf"(?P<right_year>(?:19|20)\d{{2}})\s+"
    rf"(?P=work)[.?!]?\s*$",
    re.IGNORECASE,
)
_COMPARATIVE_DIRECT_CANDIDATE_PATTERN = re.compile(
    rf"^(?:is|are|was|were|do|does|did|has|have|had|can|could|will|would|should)"
    rf"(?![a-z0-9])[^.;?!]{{0,80}}?{_COMPARATIVE_THAN_PREFIX}"
    rf"(?P<referent>[a-z][a-z0-9-]*)[.?!]?\s*$",
    re.IGNORECASE,
)
_COMPARATIVE_COUNT_YEAR_PATTERNS = (
    re.compile(
        rf"^(?:how\s+(?:many|much)|what\s+number\s+of)(?![a-z0-9])"
        rf"[^.;?!]{{0,120}}?{_COMPARATIVE_THAN_PREFIX}"
        rf"(?P<quantity>(?:19|20)\d{{2}})(?=\s+[a-z])",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:is|are|was|were)\s+there(?![a-z0-9])"
        rf"[^.;?!]{{0,80}}?{_COMPARATIVE_THAN_PREFIX}"
        rf"(?P<quantity>(?:19|20)\d{{2}})(?=\s+[a-z])",
        re.IGNORECASE,
    ),
)
_CONSTRAINTS_MARKERS = (
    "constraint",
    "requirement",
    "obligation",
    "regulation",
    "regulatory",
    "rule",
    "policy",
    "law",
    "legal",
    "standard",
    "permit",
    "compliance",
    "allowed",
    "eligible",
    "mandate",
    "ban",
)
_EXPLANATION_MARKERS = (
    "why",
    "how does",
    "how do",
    "how did",
    "explain",
    "mechanism",
    "reason for",
)
_HISTORICAL_MARKERS = (
    "historically",
    "history of",
    "in the past",
    "in the 19",
    "in the 20",
)

# Jurisdictions the planner recognizes by name. Deliberately a short, explicit
# list rather than a gazetteer: the only thing it decides is whether the
# question states a scope, and an unrecognized name simply means the plan says
# the scope is unspecified and names that as an assumption — which is honest,
# while a wrong guess is not.
#
# Geography aliases are matched *exactly* (regular plurals aside) and never with
# derived forms: "indian" is an alias for India, so a derived-form match would
# read "Indiana" as India, and "us" as a country would read "tell us about the
# rules" as the United States. The bare pronoun is absent for that reason; the
# standalone abbreviation is matched case-sensitively by
# ``_GEOGRAPHY_ABBREVIATION_PATTERN`` instead, so "US federal rules" resolves
# while "tell us about" does not.
_GEOGRAPHIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("United States", ("united states", "u.s.", "usa", "american")),
    ("California", ("california",)),
    ("Texas", ("texas",)),
    ("New York", ("new york",)),
    ("European Union", ("european union", "e.u.", "eu")),
    ("United Kingdom", ("united kingdom", "britain", "british", "uk")),
    ("Germany", ("germany", "german")),
    ("France", ("france", "french")),
    ("China", ("china", "chinese")),
    ("India", ("india", "indian")),
    ("Japan", ("japan", "japanese")),
    ("Canada", ("canada", "canadian")),
    ("Australia", ("australia", "australian")),
    ("Brazil", ("brazil", "brazilian")),
)
_GLOBAL_MARKERS = ("global", "worldwide", "world-wide", "internationally")

# Standalone two-letter country abbreviations, matched **case-sensitively**
# against the raw question: "US federal permitting rules" names the United
# States, while "tell us about the permitting rules" is the pronoun. Folding
# case here is what turned that question into a United States scope.
_GEOGRAPHY_ABBREVIATIONS = {
    "US": "United States",
    "EU": "European Union",
    "UK": "United Kingdom",
}
_GEOGRAPHY_ABBREVIATION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(US|EU|UK)(?![A-Za-z0-9])"
)

# A phrase that asks for the newest material rather than a fixed year.
# "recently" is listed because the derived-form rule only reaches single-word
# markers: "most recent" and "as of" are phrases.
_CURRENCY_MARKERS = (
    "current",
    "currently",
    "latest",
    "most recent",
    "today",
    "now",
    "as of",
    "recent",
    "recently",
)

# ``10%``, ``5 percentage points``, ``3 pp``. A cross-publisher agreement
# tolerance is a measurement claim: without a basis in the question or a named
# method it is invented, and the last measured plan invented four of them
# (baseline TR-04). The amount alone is not the defect — "did withdrawals
# exceed 15%?" is a perfectly ordinary question — so a tolerance is reported
# only when an *agreement frame* governs it (``_AGREEMENT_FRAME`` within
# ``_AGREEMENT_WINDOW`` characters before the amount). The frame is what makes
# the number a claim about how closely two sources must match.
_TOLERANCE_AMOUNT_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:percentage points?|percent|pp\b|%)",
    re.IGNORECASE,
)
_AGREEMENT_FRAME = re.compile(
    r"\b(?:within|agrees?|agreed|agreement|consistent|plus or minus)\b"
    r"|\+/-|±",
    re.IGNORECASE,
)
_AGREEMENT_WINDOW = 40
_TOLERANCE_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_PERCENTAGE_POINTS_PATTERN = re.compile(
    r"percentage points?|pp\b", re.IGNORECASE
)
_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
_ISO_DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
# A reader length is asked for by a length frame, never by a number that
# happens to sit before "words": the frame words are what say the question is
# about how long the answer should be, and without one a question's own subject
# ("the 2025 words of the treaty") becomes a four-digit limit nobody asked for.
_WORD_LIMIT_PATTERN = re.compile(
    r"(?i)\b(?:in|under|within|of|about|around|at most|no more than|fewer than|"
    r"less than|up to|maximum of|limit of|limited to)\s+"
    r"(\d[\d,]{0,8})\s*[- ]?\s*words?\b"
)


# What makes a stated tolerance legitimate: the criterion names the
# measurement or the method that establishes it, rather than asserting that
# two publishers simply agree.
_BASIS_MARKERS = (
    "measured",
    "measurement",
    "method",
    "methodology",
    "instrument",
    "resolution",
    "tolerance",
    "error bar",
    "error bars",
    "margin of error",
    "margins of error",
    "rounding",
    "significant figures",
    "as reported",
)

PLANNER_SYSTEM_PROMPT = (
    "You are the planner of a multi-agent research system. Your job is to "
    "turn one research question into a plan of distinct sub-topics that "
    "together answer it.\n"
    "You have one tool call in this loop and no more. Spend it on the one "
    "thing the question cannot scope by itself: query_memory when the "
    "recalled guidance below is empty, otherwise web_search for one term "
    "you do not understand. A later agent gathers the evidence, so do not "
    "research "
    "the question here: do not look for what the question asks for. If every "
    "term in the research question is familiar to you, finish without "
    "searching.\n"
    "Your final answer is the scoping note the plan call reads: name the "
    "question's own parts, and any term your one lookup settled, in two or "
    "three sentences. You cannot check whether a body's pages are reachable, "
    "what a page prints, or whether a period has closed, so write nothing "
    "here as though you had.\n"
    "Finish as soon as you understand the shape of the question."
)

# The plan request offers NO tools, so its prompt must not name any. It is a
# separate call from the ReAct scoping loop above: that loop really does carry
# the tools, and this one really does not.
PLANNER_PLAN_SYSTEM_PROMPT = (
    "You are the planner of a multi-agent research system. Turn the research "
    "question, context, and completed scoping notes printed below into a final "
    "research plan. Everything needed for this structured plan is already in "
    "the request. Do not propose or describe another lookup.\n"
    "The scoping notes and the recalled memory below are leads, not facts: they "
    "may shape scope and search wording, and no plan field carries an answer "
    "they suggest. When a `# Plan under repair` section is printed, the "
    "`# Repair` list names defects of that plan by target id: return that "
    "plan with each named defect corrected, not a fresh one."
)

PLAN_INSTRUCTION = (
    f"Produce a research plan of between {MIN_SUB_TOPICS} and "
    f"{MAX_SUB_TOPICS} distinct sub-topics that together answer the "
    "research question. Every sub-topic needs a title, a rationale saying why "
    "answering it is necessary, at least one concrete web search query, at "
    "least one success criterion naming the evidence that would settle it, and "
    "a priority where 1 is the most important; list the sub-topics in "
    "priority order, most important first.\n"
    f"Give every sub-topic between {MIN_TARGETS_PER_TOPIC} and "
    f"{MAX_TARGETS_PER_TOPIC} evidence_targets. Each target is one "
    "atomic obligation, written as a question whose answer is a fact, a "
    "measurement or a statement the evidence makes, not as an assertion the "
    "plan already believes. The text of a rule, a list, or a set of items the "
    "question asks for in one clause is one atomic target; never combine two "
    "measures, two periods or two jurisdictions into one target, and never "
    "require two sources to agree within a numeric tolerance unless the "
    "question itself states that tolerance. A target asks only for what a page "
    "can state: never for a verdict, a pick, a ranking or a comparison the "
    "run itself would have to make — a pick or a ranking a page states is "
    "evidence, the run's own is not — and never for a combination of other "
    "targets' answers.\n"
    "For every target fill the fields a program checks answers against. "
    "measure: the thing the evidence must state, in the question's own words "
    "when the question has them, otherwise the attribute or dimension the "
    "target asks for. unit_dimension: for a quantity, one word for the kind of "
    "quantity — power, energy, percent, currency, count, mass, volume, "
    "distance, time, rate; empty when the answer is not a quantity, and empty "
    "for a rule's own parameters (a date, a deadline, a threshold, a duration) "
    "unless the question asks for that quantity itself. period: the period the "
    "question names for it, or empty. kind: forecast when the question asks for a "
    "projected, expected or targeted outcome, whatever its date relative to the "
    "as-of date; actual for a measured or reported outcome and for a term a plan, "
    "proposal, law or provision itself sets, enacted or not; empty when the answer "
    "is not a quantity. geography: the scope the question or the answer "
    "contract names, or empty. organisation: the body the question names "
    "as its source; when "
    "it names none, the body that publishes the primary or official record of "
    "that measure — the name its pages will carry as the source, or empty when "
    "no single body does. Never stamp an adopting or enacting body, a joined "
    "pair of bodies, a description of a role, or an author no page credits: an "
    "organisation a page will not print is a target nothing can answer.\n"
    "A target is required only when the question itself asks for that thing. "
    "Everything the plan adds is optional: a sub-category, aspect, example, "
    "event, item or attribute the question does not name; a body the plan "
    "chooses where the question names none; a target you add to make another "
    "target checkable — a magnitude, a definition, a supporting statistic or a "
    "derived total; and any further body's evidence where the question asks "
    "for several without naming them. Optional targets never fail a run, while "
    "a required target no source answers fails acceptance, so require nothing "
    "the question did not ask for and expect nothing of a page: a required "
    "target is never a bet that one body's pages are reachable, or that they "
    "credit the name you stamped. Read the question as its parts: each figure, "
    "period, body, option, place or item it names, and each clause it asks, is "
    "a part a complete answer needs, and every part gets its own target. An "
    "item set with its attributes is one target per attribute, its measure "
    "naming the attribute, over the items the research finds — never one item "
    "selected by another target's answer. A question that asks why or how is "
    "answered by the reasons or the mechanism as sources state them: that is "
    "its required target, and the factors, episodes or examples you "
    "introduce to find them are optional. On a why or how question an "
    "optional target asks for a cause, a step of the mechanism, or a "
    "dispute about one -- never a figure, office, term, price or count "
    "that no step of the mechanism turns on. The number of targets follows "
    "the question: as many as its parts need, and no more. Plan one "
    "target per organisation, measure, period and kind the question asks "
    "for.\n"
    "A sub-topic whose targets are all optional answers no part of the "
    "question, so give it a lower priority than every sub-topic that "
    "carries a required target: research reaches the question's own parts "
    "before anything the plan added.\n"
    "Search queries say where to look, never what the answer is: a query may "
    "name an organisation, a standard, a jurisdiction or a year, and may not "
    "carry the value, date, item or pick a target asks for. Keep the class of "
    "source a query should reach in the criterion or the rationale, never "
    "inside the query text. Aim the queries at primary sources — the record, "
    "filing, dataset or text that states the facts directly. Do not assert a "
    "search term as a fact in any plan "
    "field: a term that came from a search, a scoping note or recalled memory "
    "is a lead, and no plan field carries an answer it suggests.\n"
    "Each success criterion must be measurable and name the evidence type and "
    "the measurement the sub-topic's targets ask for, so a reader can tell "
    "from the evidence it names whether the sub-topic was answered. Criteria and targets settle "
    "the same thing: a criterion never owes evidence its targets do not ask "
    "for.\n"
    "Keep the temporal contract: the latest available evidence, no cutoff "
    "inferred from a year in the question, actuals labelled apart from "
    "forecasts. A period is a target's period only when the question bounds "
    "when the evidence happened or applies; a year that names when the reader "
    "will act is not a period: leave it empty, the as-of date governs, and "
    "earlier evidence is still current. When the question bounds the time "
    "window it asks about, every figure target for that window carries it as "
    "its period. When the as-of date is past a period the question frames as a "
    "forecast, add an optional target for that period's actual outcome. Never "
    "make a required target depend on a source behind a paywall: name a freely "
    "reachable publication of the same figures, or leave the obligation out. A "
    "target never asks for a publication date, retrieval date or edition as "
    "its measure: those are context the researcher records beside the "
    "evidence.\n"
    "The answer contract below fixes the as-of date, the geographic scope and "
    "the answer form, and it is the scope of every sub-topic: do not restate "
    "it, narrow it or widen it, and do not assert an assumption it does not "
    "carry. Cover benefits and risks (or harms) only when the question asks "
    "about them — 'is it good', 'what are the drawbacks' — and cover what "
    "the question asks about and nothing else: a sub-topic the question did "
    "not call for, or a required target it did not ask for, widens its "
    "scope, which the plan review names as a defect.\n"
    "Two sub-topics must never share a title."
)

# Two examples, because one shape teaches a bias and the reply format is what
# the model has to get right: the first compares two transport options, the
# second traces a causal public-health question, and both name hypothetical
# bodies, so the request teaches the format and the field relationships rather
# than a domain's answer (Fable §2.3).
#
# The examples are plans the planner's own checks accept, which is the only
# property an example can teach: an earlier one modelled a "benefits and
# risks" sub-topic for a comparison question (scope widening) and a target
# asking two things at once ("Which documented risks does each option carry,
# and by which issuer?") — both of which the plan review, whose rules the
# instruction now states, names as defects. Every target here asks one
# question.
_PLAN_REPLY_EXAMPLES = (
    (
        "Example input: compare the ridership and capital cost of bus and "
        "rail options for a city.",
        '{"sub_topics":['
        '{"title":"travel demand and coverage",'
        '"rationale":"Establish which trips each option must serve.",'
        '"search_queries":["city bus rail travel demand route coverage"],'
        '"success_criteria":['
        '"A published ridership figure for each option, in passengers per year, '
        'with the year it covers."],"priority":1,'
        '"evidence_targets":['
        '{"question":"What ridership did the bus option carry in the most '
        'recent reported year?","required":true,'
        '"measure":"annual ridership","unit_dimension":"count",'
        '"period":"","kind":"actual",'
        '"geography":"the city","organisation":""},'
        '{"question":"What ridership did the rail option carry in the most '
        'recent reported year?","required":true,'
        '"measure":"annual ridership","unit_dimension":"count",'
        '"period":"","kind":"actual",'
        '"geography":"the city","organisation":""}]},'
        '{"title":"cost and delivery",'
        '"rationale":"Compare the resources and time required to deliver each '
        'option.",'
        '"search_queries":["city bus rail capital operating cost delivery '
        'time"],"success_criteria":['
        '"A published capital cost for each option, in the currency its own '
        'statement uses."],'
        '"priority":2,'
        '"evidence_targets":['
        '{"question":"What capital cost does each option report?",'
        '"required":true,'
        '"measure":"capital cost","unit_dimension":"currency",'
        '"period":"","kind":"actual",'
        '"geography":"the city","organisation":""}]},'
        '{"title":"service reliability",'
        '"rationale":"Establish how reliably each option delivers its '
        'timetable.",'
        '"search_queries":["city bus rail on-time performance reliability"],'
        '"success_criteria":['
        '"A published on-time performance percentage for each option, with the '
        'period it covers."],"priority":3,'
        '"evidence_targets":['
        '{"question":"What on-time performance did each option report?",'
        '"required":false,'
        '"measure":"on-time performance","unit_dimension":"percent",'
        '"period":"","kind":"actual",'
        '"geography":"the city","organisation":""}]}'
        "]}",
    ),
    (
        "Example input: why did measles cases rise in the region in 2024?",
        '{"sub_topics":['
        '{"title":"outbreak investigation findings",'
        '"rationale":"The causes the investigating body itself identified.",'
        '"search_queries":["regional health authority measles outbreak report 2024 causes"],'
        '"success_criteria":["A published outbreak report naming the causes it identified, with its release date."],'
        '"priority":1,"evidence_targets":['
        '{"question":"What causes of the 2024 rise do published outbreak '
        'investigations identify?","required":true,'
        '"measure":"causes identified by the outbreak investigations","unit_dimension":"",'
        '"period":"2024","kind":"",'
        '"geography":"the region","organisation":""}]},'
        '{"title":"vaccination coverage",'
        '"rationale":"Establish whether coverage fell before the rise.",'
        '"search_queries":["regional health authority MMR first dose coverage 2024"],'
        '"success_criteria":["A reported first-dose coverage figure for 2024 from '
        'the regional health authority."],'
        '"priority":2,"evidence_targets":['
        '{"question":"What MMR first-dose coverage did the regional health '
        'authority report for 2024?","required":false,'
        '"measure":"MMR first-dose coverage","unit_dimension":"percent",'
        '"period":"2024","kind":"actual",'
        '"geography":"the region","organisation":""}]},'
        '{"title":"immunity threshold",'
        '"rationale":"Background the reader needs to judge the coverage figure.",'
        '"search_queries":["national public health agency measles herd immunity threshold"],'
        '"success_criteria":["A stated immunity threshold from the national public '
        'health agency."],'
        '"priority":3,"evidence_targets":['
        '{"question":"What population immunity threshold does the national '
        'public health agency state for measles?","required":false,'
        '"measure":"stated immunity threshold","unit_dimension":"percent",'
        '"period":"","kind":"",'
        '"geography":"national","organisation":""}]}'
        "]}",
    ),
)


class EvidenceTargetDraft(ContractModel):
    """One model-proposed obligation, before the planner stamps it.

    No ``Field`` constraints, for the same reason ``SubTopicDraft`` has none:
    this model is converted to a strict JSON schema. The planner assigns the
    id; the model supplies the question the obligation answers, whether the
    question names it (``required``, spec §7.1), and the fields a program
    checks an answer against — empty when the question does not name one.
    """

    question: str
    # The model states whether the question names this obligation rather than
    # the planner deriving it, because only the question's own wording says
    # whether a part is one a complete answer needs (spec §7.1). It is
    # required of every construction, provider reply and replay double alike.
    required: bool
    """Whether the question names this obligation, so its answer is required (spec §7.1)."""
    # The fields a program checks an answer against; empty when the question
    # does not name one.
    measure: str = ""
    unit_dimension: str = ""
    period: str = ""
    kind: str = ""
    geography: str = ""
    organisation: str = ""


class SubTopicDraft(ContractModel):
    """One model-proposed sub-topic, before domain validation.

    Deliberately declares no ``Field`` constraints: this model is converted
    to a strict OpenAI JSON schema, which rejects ``minLength`` and
    ``minItems``. Constraints live in ``SubTopic``.
    """

    title: str
    rationale: str
    search_queries: list[str]
    success_criteria: list[str]
    priority: int
    evidence_targets: list[EvidenceTargetDraft]

    @field_validator("search_queries", "success_criteria", mode="before")
    @classmethod
    def _accept_a_lone_string(cls, value: object) -> object:
        """Read a bare string as a one-element list, and nothing else.

        A plan request sampled 11 times returned ``success_criteria`` with
        exactly one element every time, and two of the last four live CLI
        runs died at the planner with ``graph_planning_failed`` after 22,593
        and 17,433 tokens because both structured attempts reported
        ``type_mismatch`` on ``sub_topics.success_criteria`` for every
        sub-topic: the model wrote that one criterion as a bare string where
        the schema declares ``list[str]``. The value was always otherwise
        usable, so the whole session was lost to a missing pair of brackets.

        A ``mode="before"`` validator adds no JSON schema keyword — Pydantic
        renders constraints, not validators — so the schema the provider is
        handed stays byte-identical and the model is still asked for an
        array of strings. Only a ``str`` is wrapped; a dict, a number, or a
        list holding non-strings keeps failing exactly as before, where the
        repair prompt and ``PlanningError.problems`` can report it.
        """
        if isinstance(value, str):
            return [value]
        return value


class ResearchPlanDraft(ContractModel):
    """The provider-facing plan schema."""

    sub_topics: list[SubTopicDraft]


def planner_guidance(memory_context: MemorySnapshot) -> str:
    """Render planning context: procedural guidance, and leads that are not
    evidence.

    The session's startup recall runs with ``purpose="planning"``, so in
    production ``similar_findings`` is empty and this renders strategies
    only. A planner handed findings anyway — a replay, a resumed session, a
    caller that recalled for research — still gets them labelled for what
    they are: leads that may suggest where to look, never a settled premise
    and never a reason to prioritize one target over another.
    """
    sections: list[str] = []
    if memory_context.suggested_strategies:
        sections.append(
            render_memory_guidance(
                MemorySnapshot(
                    suggested_strategies=memory_context.suggested_strategies
                )
            )
        )
    if memory_context.similar_findings:
        leads = "\n".join(
            f"- {summarize_text(finding.content)} ({finding.source_url})"
            for finding in memory_context.similar_findings
        )
        sections.append(
            f"{len(memory_context.similar_findings)} finding(s) recalled from "
            "previous sessions — leads, not evidence. They may suggest where "
            "to look; they are never a settled premise, they never answer a "
            "target, and they never decide a target's priority.\n"
            f"{leads}"
        )
    return "\n\n".join(sections)


def has_startup_guidance(memory_context: MemorySnapshot) -> bool:
    """True when this session's startup recall produced procedural guidance.

    Keyed on ``suggested_strategies`` alone, and that is the whole ruling: the
    planner's own tool lookup is permitted only when startup recall returned
    nothing. A resumed session whose snapshot carries recalled leads but no
    strategies has *not* been given procedural guidance, so it keeps its one
    lookup — otherwise the planner would be denied both the startup guidance
    and the tool that could replace it.
    """
    return bool(memory_context.suggested_strategies)


class PlanReviewDraft(ContractModel):
    """The provider-facing verdict of one tool-free plan review.

    Every field is required and unconstrained, for the same reason the plan
    draft is: this model becomes a strict JSON schema. ``sound`` is the only
    field the planner decides on; the four lists are what a repair prompt
    gets to name, and ``repair_instruction`` is the reviewer's own wording
    for the single correction that would make the plan sound.
    """

    sound: bool
    missing_dimensions: list[str]
    atomicity_defects: list[str]
    unsupported_premises: list[str]
    repair_instruction: str


class PlanExtensionDraft(ResearchPlanDraft):
    """The provider-facing schema for a reviewed-omission extension.

    Deliberately the same shape as ``ResearchPlanDraft``: an extension is a
    plan of additional sub-topics, and the same validation applies to it.
    Keeping the schema identical means the model is not asked to learn a
    second format, and ``extend_plan`` enforces the "additional only" rule
    locally by refusing to touch anything that already exists.
    """


class ResearchPlan(ContractModel):
    """The validated plan ``PlannerAgent`` produces.

    Never sent to the provider — ``ResearchPlanDraft`` is — so its size
    bounds are free to be real constraints. The lower bound is enforced in a
    validator rather than as a field keyword because an extension is a plan
    of *additional* sub-topics and is judged by its own rule; every other
    plan carries the 1-10 the instruction asks for.

    ``answer_contract`` is the frozen contract the plan was written against.
    ``extension`` marks a plan that carries *only* the topics a later
    reviewed omission added: those topics append to the plan already in state
    instead of replacing it, so an extension can never shrink the plan.
    """

    sub_topics: list[SubTopic] = Field(max_length=MAX_SUB_TOPICS)
    repair_attempted: bool = False
    answer_contract: AnswerContract | None = None
    extension: bool = False

    @model_validator(mode="after")
    def validate_plan_size(self) -> ResearchPlan:
        if self.extension:
            if not self.sub_topics:
                raise ValueError(
                    "an extension plan must carry at least one sub-topic"
                )
            return self
        count = len(self.sub_topics)
        if count < MIN_SUB_TOPICS or count > MAX_SUB_TOPICS:
            raise ValueError(
                f"the plan has {count} sub-topics; a plan carries between "
                f"{MIN_SUB_TOPICS} and {MAX_SUB_TOPICS}"
            )
        return self


def _normalized_question(question: str) -> str:
    """Collapse whitespace and casefold, for marker matching only."""
    return collapse_whitespace(question).casefold()


def _mentions(
    normalized: str,
    markers: Sequence[str],
    *,
    derived_forms: bool = True,
) -> bool:
    """True when one of ``markers`` appears in ``normalized`` as a whole token.

    Substring matching is wrong for every list in this module: it read "tell us
    about the permitting rules" as a United States scope (the ``us`` alias),
    "Indiana" as India, and "known" as the currency word "now" — and a wrong
    jurisdiction or currency frame is stamped into every target's binding
    dimensions, where nothing downstream can see the error. Each marker is
    matched against a token boundary on both sides, so a marker only fires when
    the text actually contains that word or phrase.

    ``derived_forms`` is what keeps a whole class of markers working without
    reopening that hole. A *semantic* marker ("permit", "recent", "policy")
    also matches its derived forms — "permitting", "recently", "policies" —
    because a question asks about permitting far more often than about one
    permit, and a marker that only matches its base form silently stops
    classifying. A *name* (a jurisdiction alias) passes
    ``derived_forms=False``: "indian" would otherwise match "Indiana", which is
    the wrong-jurisdiction failure this boundary work exists to prevent.

    ``normalized`` must already be collapsed and casefolded.
    """
    return any(
        _marker_pattern(marker, derived_forms=derived_forms).search(normalized)
        is not None
        for marker in markers
    )


# Only forms with a known semantic relationship are added to a marker. The
# previous marker + ``[a-z]{0,4}`` heuristic made ``standardized`` satisfy the
# ``standard`` constraint marker. Regular plurals are generated below, while
# the few non-plural forms used by the planner are explicit and bounded.
_EXPLICIT_MARKER_VARIANTS: dict[str, tuple[str, ...]] = {
    "permit": ("permitted", "permitting"),
    "recent": ("recently",),
}


def _regular_plural(marker: str) -> str:
    """Return the regular plural for one single-word marker."""
    if marker.endswith(("s", "x", "z", "ch", "sh")):
        return marker + "es"
    if len(marker) > 1 and marker.endswith("y") and marker[-2] not in "aeiou":
        return marker[:-1] + "ies"
    return marker + "s"


def _marker_pattern(
    marker: str,
    *,
    derived_forms: bool = True,
) -> re.Pattern[str]:
    """One compiled token-boundary pattern per marker, built on first use."""
    key = f"{marker}\x00{derived_forms}"
    pattern = _MARKER_PATTERNS.get(key)
    if pattern is None:
        if " " in marker:
            # A phrase is matched exactly, with flexible whitespace: a suffix
            # would attach to its last word and turn "as of" into "as ofs".
            alternatives = [
                r"\s+".join(re.escape(word) for word in marker.split())
            ]
        else:
            alternatives = [re.escape(marker), re.escape(_regular_plural(marker))]
            if derived_forms:
                alternatives.extend(
                    re.escape(variant)
                    for variant in _EXPLICIT_MARKER_VARIANTS.get(marker, ())
                )
        pattern = re.compile(
            "(?<![a-z0-9])(?:" + "|".join(alternatives) + ")(?![a-z0-9])"
        )
        _MARKER_PATTERNS[key] = pattern
    return pattern


_ComparisonEvidence: TypeAlias = Literal["explicit", "ambiguous", "absent"]


@dataclass(frozen=True)
class _QuestionClassification:
    """One deterministic result consumed by both Planner stamping paths.

    ``ambiguous`` preserves uncertainty inside the local classifier without
    widening the persisted Task 2 contract. Only ``explicit`` earns the
    comparison answer form; ambiguous inequality language follows the ordinary
    answer-form rules.
    """

    comparison_evidence: _ComparisonEvidence
    answer_kind: AnswerKind


def _is_direct_referent(token: str) -> bool:
    """Recognize bounded one-token referent structures.

    Known one-token jurisdiction aliases use the same semantic vocabulary as
    scope stamping. Shape alone cannot distinguish an initialism from an
    ordinal variable or an alternative set from plural bounds, so every other
    bare token remains ambiguous. Named acronyms use introduced syntax (for
    example, ``than in PJM``), and sets use a contrastive ``other`` phrase.
    """
    normalized = token.casefold()
    return any(
        normalized == alias
        for _, aliases in _GEOGRAPHIES
        for alias in aliases
        if " " not in alias
    )


def _comparison_evidence_for(question: str) -> _ComparisonEvidence:
    """Return positive comparison evidence, uncertainty, or no relation.

    Explicit comparison words (``compare``, ``versus``, ``relative to``) are
    sufficient on their own. Inequalities require a complete grammatical
    shape: an introduced referent, an anaphor, parallel year/work phrases, or
    a closed yes/no question whose terminal token has a bounded referent
    structure. A remaining cue-plus-``than`` relation is deliberately
    ambiguous rather than guessed.
    """
    normalized = _normalized_question(question)
    if _mentions(normalized, _COMPARISON_MARKERS) or any(
        pattern.search(normalized) is not None
        for pattern in (
            _COMPARATIVE_INTRODUCED_PATTERN,
            _COMPARATIVE_ANAPHOR_PATTERN,
            _COMPARATIVE_OTHER_SET_PATTERN,
            _COMPARATIVE_PARALLEL_YEAR_WORK_PATTERN,
        )
    ):
        return "explicit"
    direct = _COMPARATIVE_DIRECT_CANDIDATE_PATTERN.search(normalized)
    if direct is not None and _is_direct_referent(direct.group("referent")):
        return "explicit"
    if _COMPARATIVE_RELATION_PATTERN.search(normalized) is not None:
        return "ambiguous"
    return "absent"


def _question_classification(
    question: str,
    *,
    clock_year: int | None,
) -> _QuestionClassification:
    """Classify the answer form from one semantic result."""
    normalized = _normalized_question(question)
    comparison_evidence = _comparison_evidence_for(question)
    years = _past_years(question)
    clock_year_is_stated = clock_year is not None and clock_year in years
    asks_for_currency = (
        _mentions(normalized, _CURRENCY_MARKERS) or clock_year_is_stated
    )
    past_years = [
        year for year in years if clock_year is None or year < clock_year
    ]

    if comparison_evidence == "explicit":
        answer_kind: AnswerKind = "comparison"
    elif _mentions(normalized, _EXPLANATION_MARKERS):
        answer_kind = "explanation"
    elif past_years and not asks_for_currency:
        answer_kind = "historical"
    elif _mentions(normalized, _CONSTRAINTS_MARKERS):
        answer_kind = "constraints"
    elif _mentions(normalized, _HISTORICAL_MARKERS):
        answer_kind = "historical"
    else:
        answer_kind = "factual"

    return _QuestionClassification(
        comparison_evidence=comparison_evidence,
        answer_kind=answer_kind,
    )


# Compiled once per marker string; the marker vocabulary is a module constant,
# so the cache is bounded by it.
_MARKER_PATTERNS: dict[str, re.Pattern[str]] = {}


def answer_kind_for(question: str, *, clock_year: int | None = None) -> AnswerKind:
    """Classify the answer form the question asks for, locally.

    The order of the tests is the precedence. A question that compares two
    things is a comparison even when it also names a regulation, because two
    incomparable measurements do not answer it. A question about what is
    permitted is a constraints question even when it names a past year,
    because the *form* of the answer is a list of binding rules and the
    period is already carried by ``as_of_date``. Nothing here reads a clock
    or memory, and an unrecognized question is ``factual`` — the narrowest
    form — rather than a guess.

    ``clock_year`` is what separates a historical question from a current one
    that happens to name a year: with a 2026 clock, "the 2024 figures" is a
    request for a number, not for 2024's state of affairs. A question that
    asks for currency at all is never classified historical.
    """
    return _question_classification(
        question,
        clock_year=clock_year,
    ).answer_kind


def answer_form_requirement(kind: AnswerKind) -> str:
    """The form's line in the answer contract.

    It is printed into the contract (``render_answer_contract``), which the
    planning and later requests carry, never written into a target's
    dimensions.
    """
    return _ANSWER_FORM_REQUIREMENTS[kind]


def _comparative_quantity_year_spans(normalized: str) -> set[tuple[int, int]]:
    """Four-digit inequality quantities that must not re-anchor the clock.

    A four-digit token is not enough evidence of a count. Positive count syntax
    (``how many ... more than 2000 projects`` or ``are there more than 2000
    projects``) excludes that operand. Historical comparisons keep every year,
    including repeated multiword year/work pairs. Spans are used instead of
    values so the same number can still appear elsewhere as a date.
    """
    parallel_year_spans = {
        match.span(group)
        for match in _COMPARATIVE_PARALLEL_YEAR_WORK_PATTERN.finditer(normalized)
        for group in ("left_year", "right_year")
    }
    return {
        span
        for pattern in _COMPARATIVE_COUNT_YEAR_PATTERNS
        for match in pattern.finditer(normalized)
        if (span := match.span("quantity")) not in parallel_year_spans
    }


def _past_years(question: str) -> list[int]:
    """Four-digit years the question states, excluding inequality counts."""
    normalized = _normalized_question(question)
    quantity_spans = _comparative_quantity_year_spans(normalized)
    return sorted(
        {
            int(match.group(0))
            for match in _YEAR_PATTERN.finditer(normalized)
            if match.span() not in quantity_spans
        }
    )


def geographic_scope_for(question: str) -> tuple[str, list[str]]:
    """The scope the question states, and the assumption when it states none.

    A named jurisdiction is read from a short explicit vocabulary, matched on
    token boundaries and without derived forms, so "Indiana" is not read as
    "India" and the pronoun "us" is not read as the United States. A standalone
    uppercase abbreviation ("US", "EU", "UK") is read case-sensitively from the
    raw question, because that is the only way "US federal rules" resolves
    without "tell us about the rules" resolving too. Anything else — including
    a jurisdiction this list does not know — resolves to ``"unspecified"`` with
    an explicit assumption, because a wrong jurisdiction stamped into every
    target's geography dimension is worse than an admitted gap.
    """
    normalized = _normalized_question(question)
    for canonical, aliases in _GEOGRAPHIES:
        if _mentions(normalized, aliases, derived_forms=False):
            return canonical, []
    abbreviation = _GEOGRAPHY_ABBREVIATION_PATTERN.search(question)
    if abbreviation is not None:
        return _GEOGRAPHY_ABBREVIATIONS[abbreviation.group(1)], []
    if _mentions(normalized, _GLOBAL_MARKERS):
        return "global", []
    return (
        "unspecified",
        [
            "The question names no geography, so the plan assumes none: give a "
            "target a geography only when the question or this contract's "
            "scope names one, leave it empty otherwise, and no regional "
            "sample may support a global conclusion.",
        ],
    )


def requested_word_limit_for(question: str) -> int | None:
    """The reader length the question explicitly asks for, or ``None``.

    A length is asked for by a length *frame* — "in under 500 words", "of about
    800 words" — never by a number that happens to sit before the word
    "words". Reading the bare shape, a question whose own subject filled it
    ("the 2025 words of the treaty", "in 2024 words like ... entered the
    debate") handed the writer's contract a four-digit reader length nobody
    asked for, and the writer's own request line then published it.
    """
    match = _WORD_LIMIT_PATTERN.search(question)
    if match is None:
        return None
    raw = match.group(1).replace(",", "")
    if _YEAR_PATTERN.fullmatch(raw):
        # "in 2024 words like ... entered the debate" wears the length frame
        # without asking for a length: no reader asks for exactly a year's
        # worth of words, and the year is the question's own subject.
        return None
    limit = int(raw)
    return limit if limit >= 1 else None


def _evidence_period_requirement(
    *,
    question: str,
    as_of_date: str,
    past_years: Sequence[int],
    future_years: Sequence[int],
    asks_for_currency: bool,
) -> str:
    """What period counts as current for this question.

    Publication date, data period, forecast horizon, effective policy date,
    and retrieval date are different things (Section 2.3), so this names the
    one the question is about instead of leaving "current" unqualified.
    """
    parts: list[str] = []
    if past_years:
        stated = ", ".join(str(year) for year in past_years)
        parts.append(
            f"the period the question names ({stated}); answer that period "
            f"from the latest evidence available as of {as_of_date} — a "
            "projection is reported as a forecast with its issuer and release "
            "vintage and a published outcome as an actual — and never "
            "substitute today's figures for the period the question names"
        )
    elif asks_for_currency:
        parts.append(
            "the latest available evidence as of "
            f"{as_of_date}; a fixed earlier year is not a current answer"
        )
    else:
        parts.append(f"evidence available as of {as_of_date}")
    if future_years:
        stated = ", ".join(str(year) for year in future_years)
        parts.append(
            f"{stated} is a forecast horizon, not a current value: report it "
            "as a projection and never as today's figure"
        )
    return "; ".join(parts)


# Natural-language phrases that state an explicit evidence cutoff, the way
# an ISO date in the question already does: "as of December 31, 2025", "as
# of the end of 2025", "at the end of 2025" and "by the end of 2025" all
# freeze the date they name (user decision 2). A bare observation year with
# no such phrase — "for 2025" — still does not: :func:`_past_years` reads
# that as the period the answer is *about*, not a cutoff.
_EXPLICIT_CUTOFF_PREFIX_PATTERN = re.compile(
    r"\b(?:as of|at the end of|by the end of)\s+(?:the end of\s+)?",
    re.IGNORECASE,
)
_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_NAME_ALTERNATION = "|".join(_MONTH_NAMES)
_EXPLICIT_MONTH_DAY_YEAR_PATTERN = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_ALTERNATION})\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_EXPLICIT_MONTH_YEAR_PATTERN = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_ALTERNATION})\s+"
    r"(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
# The ``(?!-\d)`` keeps this from re-reading an ISO date's year: "2025" in
# "as of 2025-12-31" is not a bare year, and :data:`_ISO_DATE_PATTERN` already
# reads that form exactly.
_EXPLICIT_YEAR_ONLY_PATTERN = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b(?!-\d)")


def _explicit_cutoff_date(question: str, *, clock_date: str) -> str | None:
    """The date an explicit cutoff phrase states, capped at ``clock_date``.

    Narrow on purpose: only a phrase that names a cutoff — "as of", "at the
    end of", "by the end of" — freezes anything. A month, day and year
    freezes that date; a month and year freezes that month's last day; a
    bare year freezes that year's last day. Every form is capped at the
    clock, because a stated cutoff that has not happened yet is not evidence
    anyone could have read.
    """
    for prefix in _EXPLICIT_CUTOFF_PREFIX_PATTERN.finditer(question):
        rest = question[prefix.end() :]
        month_day_year = _EXPLICIT_MONTH_DAY_YEAR_PATTERN.match(rest)
        if month_day_year is not None:
            month = _MONTH_NAMES[month_day_year.group("month").casefold()]
            day = int(month_day_year.group("day"))
            year = int(month_day_year.group("year"))
            try:
                candidate = date(year, month, day).isoformat()
            except ValueError:
                continue
            return min(candidate, clock_date)
        month_year = _EXPLICIT_MONTH_YEAR_PATTERN.match(rest)
        if month_year is not None:
            month = _MONTH_NAMES[month_year.group("month").casefold()]
            year = int(month_year.group("year"))
            last_day = calendar.monthrange(year, month)[1]
            candidate = date(year, month, last_day).isoformat()
            return min(candidate, clock_date)
        year_only = _EXPLICIT_YEAR_ONLY_PATTERN.match(rest)
        if year_only is not None:
            year = int(year_only.group("year"))
            candidate = date(year, 12, 31).isoformat()
            return min(candidate, clock_date)
    return None


def derive_answer_contract(
    *,
    question: str,
    now: datetime,
    requested_word_limit: int | None = None,
) -> AnswerContract:
    """Freeze the question, its scope, its as-of date, and its answer form.

    ``as_of_date`` comes from ``now`` — the run's injected clock — and never
    from model knowledge or memory. The one exception is a date the question
    itself states and the clock has already passed: a question that says "as of
    2025-12-31" is answered as of that date, and the contract says so rather
    than silently re-anchoring it to today. Years later than the clock's are
    forecast horizons, not as-of dates, so a 2035 projection cannot become
    today's cost.

    The years the question *observes* are its period, not a cutoff. Reading
    them as one put an untouched 2025-12-31 into every target's binding
    evidence period — "answer it as of 2025-12-31 and never substitute today's
    figures" — which forbids the later revisions and the published 2025
    outturn the reader needs, on a session whose question is exactly about
    them (user decision 2, review rank 4). The period requirement says which
    period the answer is *about*; the delivery date says when the evidence was
    read, and only a stated date freezes anything.

    Naming the clock's *own* year is a currency frame, not a closed period: a
    question about "the 2026 rules" asked on 2026-09-16 is answered as of
    2026-09-16, not as of 2026-12-31. Freezing a future date into the contract
    would put a date nobody has lived through into every target's binding
    obligation — the very defect class this contract exists to remove.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(
            "derive_answer_contract requires a timezone-aware clock value"
        )
    if not question.strip():
        raise ValueError("derive_answer_contract requires a question")

    clock_year = now.year
    clock_date = now.date().isoformat()
    years = _past_years(question)
    current_years = [year for year in years if year == clock_year]
    past_years = [year for year in years if year < clock_year]
    future_years = [year for year in years if year > clock_year]
    iso_dates = _ISO_DATE_PATTERN.findall(question)
    historical_iso = [value for value in iso_dates if value <= clock_date]
    explicit_cutoff = _explicit_cutoff_date(question, clock_date=clock_date)

    cutoff_candidates = list(historical_iso)
    if explicit_cutoff is not None:
        cutoff_candidates.append(explicit_cutoff)

    if cutoff_candidates:
        # A date the question states is the user's own cutoff: it is preserved
        # exactly, never moved further out than its own year's end, and never
        # re-anchored to the clock.
        as_of_date = max(cutoff_candidates)
    else:
        as_of_date = clock_date

    normalized = _normalized_question(question)
    asks_for_currency = _mentions(normalized, _CURRENCY_MARKERS) or bool(
        current_years
    )
    scope, assumptions = geographic_scope_for(question)
    kind = answer_kind_for(question, clock_year=clock_year)
    limit = (
        requested_word_limit
        if requested_word_limit is not None
        else requested_word_limit_for(question)
    )

    period = _evidence_period_requirement(
        question=question,
        as_of_date=as_of_date,
        past_years=past_years,
        future_years=future_years,
        asks_for_currency=asks_for_currency,
    )
    scope_statement = (
        f"{question.strip()} — answered for {scope} as of {as_of_date}, as a "
        f"{kind} answer; evidence period: {period}."
    )
    return AnswerContract(
        question=question.strip(),
        scope_statement=scope_statement,
        geographic_scope=scope,
        as_of_date=as_of_date,
        evidence_period_requirement=period,
        assumptions=assumptions,
        answer_kind=kind,
        requested_word_limit=limit,
    )


def frozen_contract_for(
    existing: AnswerContract,
    derived: AnswerContract,
) -> AnswerContract:
    """Return the contract a session is bound by: the one it already froze.

    Section 2.3 freezes the original question, the scope, and the as-of date
    for the session. A later planning pass may plan more work, but it may not
    re-anchor the period or widen the scope: an as-of date or geography that
    moves between passes silently changes what every already-stamped target's
    binding dimensions mean.

    Nothing is taken from ``derived``, including a field the frozen contract
    leaves empty. ``requested_word_limit = None`` is not a hole to fill: it
    records that *the frozen question* asked for no particular length, and a
    word limit can only appear in ``derived`` by coming from a different
    question — which this session is not answering. ``derived`` is passed (and
    ignored) so the call site reads as the decision it is: two candidate
    contracts, and the frozen one wins.
    """
    del derived
    return existing


def _normalized_title(title: str) -> str:
    return collapse_whitespace(title).casefold()


def coverage_id_for(position: int) -> str:
    """The id this planner stamps on its ``position``-th (1-based) sub-topic.

    ``position`` counts through the plan in priority order, so ``topic-01``
    is always the most important planned sub-topic. The id is local and
    positional: no provider ever proposes one, and the same ordered plan
    always produces the same ids — including on a plan that is repaired.
    """
    return f"topic-{position:0{_COVERAGE_ID_WIDTH}d}"


def _assign_coverage_ids(sub_topics: Sequence[SubTopic]) -> list[SubTopic]:
    """Order ``sub_topics`` by priority and stamp ``topic-01``, ``topic-02``…

    The plan instruction asks the model to list sub-topics in priority order
    and the model does not always obey, so the ordering is established here
    rather than trusted. ``sorted`` is stable, so sub-topics that share a
    priority keep the order the model produced — the same tie-break
    ``researcher._eligible_sub_topics`` applies downstream. Ids are stamped
    after that ordering, never before it, so an id always names a plan
    position rather than a draft position. Each target's id is re-stamped
    with its sub-topic's, because a target id is namespaced by it.
    """
    ordered = sorted(sub_topics, key=lambda sub_topic: sub_topic.priority)
    stamped: list[SubTopic] = []
    for position, sub_topic in enumerate(ordered, start=1):
        coverage_id = coverage_id_for(position)
        targets = [
            target.model_copy(
                update={
                    "coverage_id": coverage_id,
                    "target_id": target_id_for(coverage_id, target_position),
                }
            )
            for target_position, target in enumerate(
                sub_topic.evidence_targets, start=1
            )
        ]
        stamped.append(
            sub_topic.model_copy(
                update={
                    "coverage_id": coverage_id,
                    "evidence_targets": targets,
                }
            )
        )
    return stamped


def _structured_fields(target: EvidenceTargetDraft) -> dict[str, object]:
    """The draft's checkable fields, blanks stamped empty and a free dimension kept.

    ``unit_dimension`` is one lower-case word from the open vocabulary the
    rule hands the model — ``count`` and ``currency`` are as real as ``power``
    — so nothing but whitespace and case is normalized here (D10). ``kind`` is
    still checked against the two values the figure rules read, because a
    downstream check compares it to a vocabulary, not to prose.
    """

    def text(value: str) -> str | None:
        return " ".join(value.split()) or None

    dimension = (text(target.unit_dimension) or "").casefold()
    kind = (text(target.kind) or "").casefold()
    return {
        "measure": text(target.measure),
        "unit_dimension": dimension or None,
        "period": text(target.period),
        "kind": kind if kind in get_args(FigureKind) else None,
        "geography": text(target.geography),
        "organisation": text(target.organisation),
    }


def _draft_targets(
    item: SubTopicDraft, coverage_id: str
) -> list[EvidenceTarget]:
    """Convert one draft's obligations into provisional ``EvidenceTarget``s.

    Provisional in exactly one way: the ids are positional within the draft
    (``_assign_coverage_ids`` re-stamps them once the plan is ordered).
    Everything else — the question, ``required`` and the structured fields the
    model supplied — is carried through as it stands, so a draft that omits a
    question, states no measure, or proposes more obligations than a sub-topic
    may carry fails validation here and is reported as a repair problem.
    """
    return [
        EvidenceTarget(
            target_id=target_id_for(coverage_id, position),
            coverage_id=coverage_id,
            question=target.question,
            required=target.required,
            **_structured_fields(target),
        )
        for position, target in enumerate(item.evidence_targets, start=1)
    ]


def target_id_for(coverage_id: str, position: int) -> str:
    """The id this planner stamps on one target of one sub-topic.

    Namespaced by the sub-topic so a target id is unique across the whole
    plan without a second counter, and positional within the sub-topic so the
    same plan always produces the same ids. No provider ever proposes one.
    """
    return f"{coverage_id}-target-{position:0{_COVERAGE_ID_WIDTH}d}"


def stale_year_anchors(
    text: str,
    *,
    as_of_year: int,
    question_years: frozenset[int] = frozenset(),
) -> list[int]:
    """Years in ``text`` that a currency marker makes older than the as-of year.

    What the rule reads is a co-occurrence, not a frame: any currency marker
    anywhere in ``text`` brings every year below ``as_of_year`` that appears
    anywhere in it into the report. That is why ``question_years`` exists —
    a year the frozen question itself names is the question's subject, not a
    claim that the year is current, so it is dropped before the report is
    built. The audit question names 2024 under a contract whose as-of date is
    the session clock, and without this exemption the rule reported the
    question's own wording and ended two live runs before any research.

    The co-occurrence reading is deliberate in the other direction too: it
    over-reports rather than under-reports, and the plan review call is what
    judges meaning. The TR-04 defect this rule exists for is a plan that treats
    an earlier year as today — "the current 2024 figures", "2024 is current".
    """
    normalized = _normalized_question(text)
    if not _mentions(normalized, _CURRENCY_MARKERS):
        return []
    return sorted(
        {
            int(match)
            for match in _YEAR_PATTERN.findall(text)
            if int(match) < as_of_year and int(match) not in question_years
        }
    )


def _agreement_tolerances(text: str) -> list[str]:
    """The numeric tolerances in ``text`` that an agreement frame governs.

    The amount alone is not the defect: "did withdrawals exceed 15%?" is an
    ordinary question, and reporting it forced a repair — and then a failed
    planning pass when the model kept its correct number. What *is* a defect is
    a tolerance: a claim about how closely two sources must match, which is
    present only when a frame word ("within", "plus or minus", "agree", "±")
    governs the number.
    """
    found: list[str] = []
    for match in _TOLERANCE_AMOUNT_PATTERN.finditer(text):
        window = text[max(0, match.start() - _AGREEMENT_WINDOW):match.start()]
        if _AGREEMENT_FRAME.search(window) is None:
            continue
        found.append(" ".join(match.group(0).split()))
    return found


def _tolerance_key(value: str) -> tuple[str, str] | None:
    """The (number, unit) a tolerance states, for comparing two of them.

    The unit matters as much as the number. "Within 3%" and "within 3
    percentage points" are different requirements — one is a relative band, the
    other an absolute one — so a criterion may not satisfy a question's "within
    3 percentage points" by writing "within 3%". "%" and "percent" are the same
    unit spelled two ways and are folded together.
    """
    number = _TOLERANCE_NUMBER_PATTERN.search(value)
    if number is None:
        return None
    unit = (
        "percentage_points"
        if _PERCENTAGE_POINTS_PATTERN.search(value)
        else "percent"
    )
    return number.group(0), unit


def invented_tolerances(text: str, *, question: str) -> list[str]:
    """Numeric agreement tolerances in ``text`` with no basis in the question.

    A cross-publisher tolerance ("both sources agree within 10%") is a
    measurement claim: it is legitimate when the question asks for that
    precision, or when the criterion names the measurement or method that
    establishes it. Otherwise the planner invented it — the last measured
    plan invented 10%, 5 percentage points, 15%, and 3 percentage points
    (baseline TR-04) — and it is reported so the repair prompt can remove it.
    A number with no agreement frame is not a tolerance at all and is never
    reported; see ``_agreement_tolerances``. "The question states it" means the
    same number *and* the same unit.
    """
    found = _agreement_tolerances(text)
    if not found:
        return []
    question_tolerances = {
        key
        for value in _agreement_tolerances(question)
        if (key := _tolerance_key(value)) is not None
    }
    if _mentions(_normalized_question(text), _BASIS_MARKERS):
        return []
    return [
        value
        for value in found
        if _tolerance_key(value) not in question_tolerances
    ]


@dataclass(frozen=True)
class _PlanProblem:
    """One problem a plan carries, and whether it decides the plan's fate.

    ``structural`` marks the problems that decide whether anything can be
    researched at all: a sub-topic with no evidence target, and a target
    written as an assertion rather than a question. Those are the ones a run
    still stops on. Everything else is advisory — a stale anchor, an invented
    tolerance, an obligation no claim could be bound to, and a demand the
    question never made are judgements about meaning, which the review owns,
    and a run that dies on one has thrown away a researchable plan.
    """

    text: str
    kind: Literal["structural", "advisory"]


# What a sub-topic or criterion may demand the question never asked for. The
# planner's own instruction used to *require* a benefits-and-risks sub-topic
# for any technology question, which is the widening the plan review names as
# a defect: a plan stays inside the question it was given. The vocabulary is
# explicit and small, like the currency and tolerance tables, because a
# judgement about meaning is the review's and this check only names the one
# class the planner itself used to mandate.
_WIDENING_MARKERS = (
    "benefit",
    "risk",
    "harm",
    "drawback",
    "downside",
    "trade-off",
    "tradeoff",
    "pros and cons",
    "advantages and disadvantages",
)

# A question that asks for a value judgement is asking about benefits and
# harms in its own words — "Is AI good for healthcare?" — so a plan that
# covers them is answering the question rather than widening it.
_JUDGEMENT_MARKERS = (
    "good for",
    "good at",
    "better than",
    "worse than",
    "best",
    "worst",
    "safe",
    "safer",
    "safest",
    "worth it",
    "worthwhile",
    "should we",
    "should i",
)

# The interrogative words that make one demand of a question. A target that
# conjoins two of them asks two things at once, which is the compound
# obligation the plan review refuses ("a target that requires two measures,
# two rule dates, or two jurisdictions to be settled is compound even when it
# reads as one sentence").
_DEMAND_MARKERS = (
    "how",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "when",
    "where",
    "why",
)
_CONJUNCTION = re.compile(r"(?i)\s+and\s+|,\s*and\s+")


# What names the power/capacity family in a question's own prose, beside the
# unit tokens ``_POWER_UNIT`` already matches. "How much ... capacity was
# added" never spells "GW" or "MW", so the family has to be read from the
# word the question actually uses, exactly as ``_measure_bases`` reads a
# target's own unit tokens from its prose (researcher.py).
_CAPACITY_MARKERS = ("capacity",)

# What names the energy family in a question's own prose, beside the unit
# tokens ``_ENERGY_UNIT`` already matches. A question can ask for energy
# without ever spelling "MWh": "how much energy", "what duration", "how many
# hours of discharge" are all energy asks in words.
_ENERGY_MARKERS = ("energy", "duration", "hour")


def _unrequested_energy_measure(
    target: EvidenceTarget, *, contract: AnswerContract
) -> bool:
    """True when a target asks for an energy figure a capacity question never named.

    "How much grid-scale battery storage capacity was added" is a power
    question; a target that asks for MWh/GWh answers a different measure the
    question never asked for, and the researcher spends a whole acquisition
    loop on a figure nobody wanted (audit #3, C5). Read from the target's own
    prose — its question and its measure — because either can carry the unit
    and neither is the whole obligation; the question's own capacity wording
    is the anchor, so the check never has to guess what family a unit-free
    question asks in.

    A question that itself names the energy family — a literal unit
    (``_ENERGY_UNIT``) or a word for it (``_ENERGY_MARKERS``: energy,
    duration, hours) — is exempt: it really did ask for energy, and the
    target answers it.
    """
    question = _normalized_question(contract.question)
    if _ENERGY_UNIT.search(question) or _mentions(question, _ENERGY_MARKERS):
        return False
    if not (
        _POWER_UNIT.search(question) or _mentions(question, _CAPACITY_MARKERS)
    ):
        return False
    target_text = " ".join(filter(None, (target.question, target.measure)))
    return bool(_ENERGY_UNIT.search(target_text))


def _conjoined_demands(question: str) -> list[str]:
    """The separate demands one target's question makes, when it makes two."""
    parts = [part for part in _CONJUNCTION.split(question.rstrip("?")) if part]
    demands = [
        part
        for part in parts
        if _mentions(_normalized_question(part), _DEMAND_MARKERS)
    ]
    return demands if len(demands) > 1 else []


def _demanded_widening(sub_topic: SubTopic) -> list[str]:
    """The benefits-and-risks *dimension* one sub-topic demands, if it demands one.

    A dimension is a pairing, not a word. The check exists for the sub-topic an
    earlier instruction *mandated* — benefits and risks together — and a single
    marker is a domain word as often as it is a demand: "systemic risk" is what
    the EU AI Act calls the subject matter it regulates, so a live plan whose
    sub-topic used it bought a max-effort repair call for a good plan, and a
    repair can delete the dimension the question asked for (D14). Two distinct
    markers are the pairing; one marker is left to the plan review, which
    judges scope widening with meaning. The words are counted here, not judged,
    which is why the count has to be the shape the check was written for.
    """
    demanded = " ".join(
        [
            sub_topic.title,
            *sub_topic.search_queries,
            *sub_topic.success_criteria,
            *(
                text
                for target in sub_topic.evidence_targets
                for text in (target.question, target.measure or "")
            ),
        ]
    )
    normalized = _normalized_question(demanded)
    found = [
        marker
        for marker in _WIDENING_MARKERS
        if _mentions(normalized, (marker,))
    ]
    return found if len(found) > 1 else []


_EDITION_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Q[1-4])\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)

# "January of 2025" and "January 2025" name the same edition; only the
# insertion of "of" differs. Folded to the plain form before either side of
# the unrequested-vintage check reads it, so a question that names an edition
# the natural way is never read as silent about it just because a dimension
# (or the question itself) spelled the same date without "of".
_MONTH_OF_YEAR = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Q[1-4]))\s+of\s+((?:19|20)\d{2})\b",
    re.IGNORECASE,
)


def _normalize_month_of_year(text: str) -> str:
    """Fold "Month of YYYY" (or "QN of YYYY") to "Month YYYY"."""
    return _MONTH_OF_YEAR.sub(r"\1 \2", text)


# Grouped so each alternative is matched as a whole word: the prior ungrouped
# forecast|project|outlook\b read "project" as a bare substring of
# "projects", and of the present-tense verb ("EIA project the grid will
# add"), neither of which names a forecast edition. "forecast\w*" covers
# "forecast", "forecasts", "forecasted", and "forecasting"; "projected" and
# "projection" are named on their own because a bare "project"/"projects" is
# at least as often a noun ("projects larger than 1 MW") or a present-tense
# verb as it is a forecast cue.
_FORECAST_CUE = re.compile(
    r"\b(?:forecast\w*|projected|projection|outlook)\b", re.IGNORECASE
)

# A date is a *publication* edition only near one of these markers; a bare
# date is just as often the data period the figure covers. "as projected in"
# is a phrase, not a single word, so it is matched on its own.
_EDITION_PHRASE_MARKER = re.compile(
    r"\b(?:edition|vintage|outlook|monitor|released)\b|as\s+projected\s+in",
    re.IGNORECASE,
)
# Characters of context read on each side of a candidate date when deciding
# whether it sits in an edition phrase: enough to span "as projected in the"
# or "...'s ... edition" without reaching into an unrelated clause.
_EDITION_PHRASE_WINDOW = 40


def _in_edition_phrase(wording: str, match: "re.Match[str]") -> bool:
    """True when a marker names the match as a publication edition, not a period."""
    start = max(0, match.start() - _EDITION_PHRASE_WINDOW)
    end = min(len(wording), match.end() + _EDITION_PHRASE_WINDOW)
    return _EDITION_PHRASE_MARKER.search(wording[start:end]) is not None


def _unrequested_forecast_vintage(
    target: EvidenceTarget, contract: AnswerContract
) -> str:
    """A forecast's publication edition is not the projected data year.

    A period states the data year or quarter a figure covers, never the
    edition that published it — "Q4 2025" beside a question asking for "the
    fourth quarter of 2025" is one obligation spelled two ways, not a
    planner-invented vintage — so the ``period`` field is never scanned for a
    candidate edition date. What the target's question and measure state still
    has to sit inside an edition phrase (see _in_edition_phrase): a bare date
    elsewhere in either is still just as likely to be the data period as the
    release.
    """
    wording = _normalize_month_of_year(
        " ".join(filter(None, (target.question, target.measure)))
    )
    if not _FORECAST_CUE.search(wording):
        return ""
    question = _normalize_month_of_year(contract.question).casefold()
    for match in _EDITION_DATE.finditer(wording):
        if not _in_edition_phrase(wording, match):
            continue
        if match.group(0).casefold() not in question:
            return match.group(0)
    return ""


def _plan_problems(
    sub_topics: Sequence[SubTopic],
    contract: AnswerContract,
) -> list[_PlanProblem]:
    """Every problem one plan carries, in report order, each tagged.

    One pass, so the order the repair prompt reads is exactly the order the
    records keep, and ``target_problems`` — the unpartitioned view — stays
    byte-identical to the list it produced before the two kinds were told
    apart.
    """
    problems: list[_PlanProblem] = []
    as_of_year = int(contract.as_of_date[:4])
    question_years = frozenset(_past_years(contract.question))
    for sub_topic in sub_topics:
        count = len(sub_topic.evidence_targets)
        if count < MIN_TARGETS_PER_TOPIC or count > MAX_TARGETS_PER_TOPIC:
            problems.append(
                _PlanProblem(
                    f"{sub_topic.coverage_id} proposes {count} evidence "
                    f"targets; every sub-topic carries between "
                    f"{MIN_TARGETS_PER_TOPIC} and {MAX_TARGETS_PER_TOPIC}",
                    "structural",
                )
            )
            continue
        for target in sub_topic.evidence_targets:
            if not target.question.rstrip().endswith("?"):
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} is written as an assertion; "
                        "request the unknown as a question instead",
                        "structural",
                    )
                )
            stale = stale_year_anchors(
                target.question,
                as_of_year=as_of_year,
                question_years=question_years,
            )
            if stale:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} anchors currency to "
                        f"{', '.join(str(year) for year in stale)} for a "
                        f"session as of {contract.as_of_date}; ask for the "
                        "latest available evidence instead",
                        "advisory",
                    )
                )
            invented = invented_tolerances(
                target.question, question=contract.question
            )
            if invented:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} requires a numeric agreement "
                        f"tolerance ({', '.join(invented)}) that the question "
                        "does not state and no measurement basis establishes",
                        "advisory",
                    )
                )
            compound = _conjoined_demands(target.question)
            if compound:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} asks "
                        f"{len(compound)} questions at once "
                        f"({'; '.join(part.strip() for part in compound)}); "
                        "split it into one obligation per target, because a "
                        "target that needs two measures settled is compound "
                        "however it reads",
                        "advisory",
                    )
                )
            vintage = _unrequested_forecast_vintage(target, contract)
            if vintage:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} hard-codes forecast vintage "
                        f"{vintage} absent from the question; name the issuer's "
                        "latest available forecast across its published series, "
                        "and report the edition beside the eventual figure",
                        "advisory",
                    )
                )
            unrequested_measure = _unrequested_energy_measure(
                target, contract=contract
            )
            if unrequested_measure:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} is planned optional: it "
                        "asks for an energy figure the question "
                        "never named, and no claim about "
                        "capacity can discharge it",
                        "advisory",
                    )
                )
        widening = _demanded_widening(sub_topic)
        if widening and not (
            _mentions(
                _normalized_question(contract.question), _WIDENING_MARKERS
            )
            or _mentions(
                _normalized_question(contract.question), _JUDGEMENT_MARKERS
            )
        ):
            problems.append(
                _PlanProblem(
                    f"{sub_topic.coverage_id} demands "
                    f"{', '.join(widening)} the question never asks about; a "
                    "plan stays inside the question it was given, and the "
                    "review names a sub-topic the question did not call for "
                    "as scope widening",
                    "advisory",
                )
            )
        for criterion in sub_topic.success_criteria:
            stale = stale_year_anchors(
                criterion,
                as_of_year=as_of_year,
                question_years=question_years,
            )
            if stale:
                # The measured defect lived here: a criterion that required
                # "current" numbers pinned to 2024. A criterion that is *about*
                # an older year is untouched — only a currency frame is read.
                problems.append(
                    _PlanProblem(
                        f"{sub_topic.coverage_id} anchors currency to "
                        f"{', '.join(str(year) for year in stale)} in a "
                        f"success criterion for a session as of "
                        f"{contract.as_of_date}; ask for the latest available "
                        "evidence instead",
                        "advisory",
                    )
                )
            invented = invented_tolerances(
                criterion, question=contract.question
            )
            if invented:
                problems.append(
                    _PlanProblem(
                        f"{sub_topic.coverage_id} sets a numeric agreement "
                        f"tolerance ({', '.join(invented)}) in a success "
                        "criterion that the question does not state and no "
                        "measurement basis establishes",
                        "advisory",
                    )
                )
    return problems


def target_problems(
    sub_topics: Sequence[SubTopic],
    contract: AnswerContract,
) -> list[str]:
    """Semantic-adjacent defects the contract can name structurally.

    This is not an atomicity proof: one measure combined with another inside
    a single criterion is a meaning defect, and the plan review call is what
    judges meaning. What is checkable here is that every sub-topic carries
    1-6 obligations, that each obligation is asked as a question rather than
    asserted, that nothing anchors currency to a year the contract has already
    left behind, and that nothing invents a numeric agreement tolerance. The
    years the frozen question itself names are exempt from the anchor check:
    they are the question's subject, not a claim that an older year is current.

    Every problem is returned here, structural and advisory alike, because
    this is the view a caller that reports everything wants — ``extend_plan``
    refuses an extension on any of them. ``_plan_problems`` is the tagged view,
    and it is what decides which of them a planning pass can survive.
    """
    return [problem.text for problem in _plan_problems(sub_topics, contract)]


# What a value has to look like to be a body's own name, and what a
# *description* of a role looks like instead. The shape is the test, never a
# list of words: a blacklist of role words refused real names that happened to
# carry one ("Department of Health and Human Services" was emptied for its
# "Department") — and emptying a name loosens the binding rather than
# tightening it, because a quantity target with no organisation is answered by
# any page's figure — while still stamping descriptions whose role word sat
# outside the list ("the national statistical agency"). A centre-piece example
# needed the opposite too: "WHO" was read as a relative clause.
#
# A value names a body when, after dropping a leading article, either
#   * every significant token is capitalised (a token may be lower case only
#     when it is a connector: "of", "for", "and", "the", "de", "van"), or
#   * it carries an acronym shape: a token of two or more all-upper-case
#     letters ("WHO", "WHO Europe", "US EIA").
# A *lower-case* relative clause is a description ("the body that publishes
# the record"), matched case-sensitively so an acronym is never caught by it.
_BODY_NAME_CONNECTORS = frozenset(
    {
        "a", "an", "the", "of", "for", "and", "or", "in", "on", "at", "by",
        "to", "from", "with", "de", "del", "della", "di", "da", "van", "von",
        "der", "den", "und", "y", "e", "et", "el", "la", "le",
    }
)

# Lower case only, and compared against the raw token: "WHO" is a name, and
# "who" in "the body who publishes it" is the relative clause that makes the
# whole value a description.
_BODY_DESCRIPTION_CLAUSES = frozenset({"that", "which", "who", "whose"})

_BODY_WORD = re.compile(r"[^\W\d_][\w'\u2019-]*")


def _names_a_body(value: str) -> bool:
    """Whether ``value`` names a body, as a page's organisation label does.

    §6.6 binds a target's answer to a finding through ``same_organisation``,
    which matches names: a page's own organisation label, its acronym, or its
    host. A *description* of a body's role matches none of those, so a target
    stamped with one was reported Not found for the whole run while the report
    held its answer, and an extra pass was bought for it (live probe P3/P4 —
    "the manufacturer of semaglutide" for a drug's maker).

    The shape decides, in both directions. A description is lower case prose
    with a role or a relative clause in it ("the regional health authority",
    "the national statistical agency", "the regulator", "the manufacturer of
    semaglutide"), and a name is capitalised or an acronym ("Centers for
    Disease Control and Prevention", "Department for Energy Security and Net
    Zero", "WHO", "WHO Europe"). Refusing by *shape* is what keeps the guard
    from emptying real names — an emptied name loosens a quantity target's
    provenance instead of tightening it — and from stamping the descriptions a
    word list misses.

    Two bodies joined by a capitalised conjunction stay stamped ("Wood
    Mackenzie and the American Clean Power Association", "European Parliament
    and Council of the European Union"): that shape is a name-shaped value, and
    refusing it emptied real names that carry the conjunction too ("Centers for
    Disease Control and Prevention"). Whether a join is one body or two is the
    plan review's judgement, and a target bound to a label no page carries is
    then a plan-quality defect rather than a shape this check can decide.
    """
    tokens = _BODY_WORD.findall(value)
    if not tokens:
        return False
    if any(token in _BODY_DESCRIPTION_CLAUSES for token in tokens):
        return False
    if any(len(token) >= 2 and token.isupper() for token in tokens):
        return True
    return all(
        token.casefold() in _BODY_NAME_CONNECTORS or token[:1].isupper()
        for token in tokens
    )


def _stamped_organisation(value: str | None) -> str | None:
    """The organisation a plan may stamp: a body's name, or nothing.

    The ruling the check implements: a plan may stamp an organisation only when
    it is a name — one the question states, or the name of the body that
    publishes the primary record — and never a description of a role. A
    description is left empty, which is exactly the state the plan instruction
    already defines for a measure no single body publishes: the target is then
    answered on its measure, period and kind alone rather than pre-failed on a
    label no page carries.
    """
    if value is None:
        return None
    name = " ".join(value.split())
    if not name or not _names_a_body(name):
        return None
    return name


# The answer forms whose answer is an argument or a text rather than a
# measured quantity: a why-question's mechanism, and a question about a list of
# rules, provisions or dated changes. A target of such a question that carries
# a unit dimension or a kind is a figure the plan added of its own accord — the
# grader's P6 Roman Republic plan marked required count and currency targets of
# kind actual on a why-question — so the run owed figures nobody asked for while
# the reasons it did ask for were not what acceptance measured.
_FIGURE_ANSWER_FORMS = frozenset({"explanation", "constraints"})

# What says the question itself asks for a quantity to be measured: an ask for
# a magnitude, a unit token, or a word that names a measured magnitude. The
# vocabulary is general — how much/how many, a unit, a total, a rate — and never
# names a question, a domain or a body.
_QUANTITY_QUESTION_PHRASES = (
    "how much", "how many", "how large", "how big", "how long", "how high",
    "how far", "how fast", "how quickly", "how often", "what share",
    "what proportion", "what percentage", "what fraction", "what amount",
    "what rate", "what price", "what cost", "what level", "what total",
)

_MAGNITUDE_WORDS = (
    "total", "rate", "share", "percentage", "proportion", "fraction", "amount",
    "number", "count", "volume", "price", "cost", "level", "index", "ratio",
    "average", "median", "growth", "size", "value", "revenue", "sales",
    "capacity", "output", "spending", "expenditure",
)


def _question_asks_for_a_quantity(contract: AnswerContract) -> bool:
    """Whether the question itself asks for a quantity to be measured (§7.1).

    Read from the frozen question's own words: an ask for a magnitude ("how
    much", "what share"), a unit token, or a word that names a measured
    magnitude. This is the one condition under which a target of a
    text-answered question may carry a figure of its own.
    """
    question = _normalized_question(contract.question)
    return (
        _mentions(question, _QUANTITY_QUESTION_PHRASES)
        or _POWER_UNIT.search(question) is not None
        or _ENERGY_UNIT.search(question) is not None
        or _mentions(question, _MAGNITUDE_WORDS)
    )


# The words that introduce a year as the window the question's evidence falls
# in, rather than as when the reader will act on it: "since 1993", "in 2025".
# The instruction already says a year that names when the reader will buy,
# decide or use is not a period, so a year only becomes the run's window here
# when the question frames it that way and names no other.
_WINDOW_FRAMES = ("since", "in", "during", "over", "through", "between", "from", "as of")

# The words that make the year beside them the reader's own act rather than the
# window the evidence falls in: the instruction's own list — "names when the
# reader will buy, decide or use" — so "Which Kettle should I buy in 2026?"
# states no period at all, while "since 1993" and "in 2025" do.
_USE_FRAMES = (
    "buy", "buys", "buying", "purchase", "purchases", "purchasing",
    "decide", "decides", "deciding", "use", "uses", "using",
    "choose", "chooses", "choosing", "rent", "rents", "renting",
)
_USE_FRAME_WINDOW = 24


def _question_window(contract: AnswerContract) -> str | None:
    """The one time window the question bounds, as a period to stamp (§7.1).

    The final probe's P7 plan stamped "since 1993" on two of its three sea-level
    targets and left the third empty, and its P8 plan left the window off a
    share target while its siblings carried it: a figure target of a windowed
    question that states no period of its own is a defect the review has to
    repair. The window is read from the question's own words, and only when the
    question names exactly one year *and* frames it as the window it asks about,
    so "to buy in 2026" names when the reader buys and is no period at all.
    """
    years = set(_past_years(contract.question))
    if len(years) != 1:
        return None
    (year,) = years
    question = _normalized_question(contract.question)
    frames = "|".join(_WINDOW_FRAMES)
    if not re.search(rf"(?i)\b(?:{frames})\s+{year}\b", question):
        return None
    start = question.index(str(year))
    preceding = question[max(0, start - _USE_FRAME_WINDOW):start]
    if re.search(r"(?i)\b(?:" + "|".join(_USE_FRAMES) + r")\b", preceding):
        return None
    return str(year)


# What a *target's* own wording asks for, when its question asked for no
# quantity at all: a "how many"/"how much" of the target's own, or a count,
# total, share or rate *of something*. Only the ask counts, never a magnitude
# word on its own: the middle of a measure is where a magnitude word lives
# without any obligation behind it ("interconnection constraints" beside a
# sub-topic called "Queue totals"), and demoting that shape is how a word list
# removed `required` from every target of a headphones question in the probe
# re-run. Rule 4's other half — a question answered by a reason carries no
# figure obligation the question never asked for.
_TARGET_QUANTITY_ASKS = (
    "how much", "how many", "number of", "count of", "total of", "share of",
    "rate of", "percentage of", "amount of", "volume of",
)


def _target_asks_for_a_quantity(target: EvidenceTarget) -> bool:
    """Whether the target's own wording asks for a quantity of something."""
    text = _normalized_question(
        " ".join(filter(None, (target.question, target.measure or "")))
    )
    return _mentions(text, _TARGET_QUANTITY_ASKS)


def _figure_targets_are_planned(contract: AnswerContract) -> bool:
    """Whether this question's own form lets a target carry a figure (§7.1).

    A question answered by a mechanism or by a text plans no figure target of
    its own: its parts are reasons, rules, provisions or dated changes, and any
    figure the researcher later finds is evidence inside that finding rather
    than an obligation the run must answer. A question that asks for a quantity
    keeps its figures whatever its form — the audit question is answered by an
    argument in part and still asks "how much capacity was added".
    """
    if contract.answer_kind not in _FIGURE_ANSWER_FORMS:
        return True
    return _question_asks_for_a_quantity(contract)


def apply_answer_contract(
    sub_topics: Sequence[SubTopic],
    contract: AnswerContract,
) -> list[SubTopic]:
    """Re-stamp each target's id, organisation and ``required`` flag (spec §7.1).

    The model marks a target required only when the question names it, and that
    reading is enforced by the instruction, not by a word test: the probe re-run
    showed what a word test costs — a majority-of-distinctive-words rule removed
    ``required`` from every target of a headphones question whose measures were
    *paraphrases* of it ("sound quality rating (highest-ranked model)" against
    "best audio quality"), so the plan owed nothing and a report could omit every
    part and still pass. A word list cannot judge paraphrase, and the failure
    mode of a wrong demotion is worse than a wrongly-required aid, which costs
    one extra pass. The instruction therefore says which targets are optional
    (``PLAN_INSTRUCTION``), and one bounded code rule remains (Fable C-d): a
    target that asks for an energy figure (MWh) a capacity question never named
    is optional whatever the draft said.

    Two fields are corrected on the way: a question answered by an argument or a
    text plans no figure of its own (``_figure_targets_are_planned``), and a body
    the plan can only *describe* is emptied so a target is never bound to a label
    §6.6 can never match (``_stamped_organisation``). Nothing is ever promoted:
    required is the model's to grant.
    """
    stamped: list[SubTopic] = []
    figures_planned = _figure_targets_are_planned(contract)
    window = _question_window(contract)
    owed_before = any(
        target.required
        for sub_topic in sub_topics
        for target in sub_topic.evidence_targets
    )
    for sub_topic in sub_topics:
        targets = [
            EvidenceTarget(
                target_id=target_id_for(sub_topic.coverage_id, position),
                coverage_id=sub_topic.coverage_id,
                question=target.question,
                required=target.required
                and not _unrequested_energy_measure(target, contract=contract)
                and not (
                    not figures_planned and _target_asks_for_a_quantity(target)
                ),
                measure=target.measure,
                unit_dimension=(
                    target.unit_dimension if figures_planned else None
                ),
                period=(
                    target.period
                    or (
                        window
                        if figures_planned
                        and (target.unit_dimension or target.kind)
                        else None
                    )
                ),
                kind=target.kind if figures_planned else None,
                geography=target.geography,
                organisation=_stamped_organisation(target.organisation),
            )
            for position, target in enumerate(sub_topic.evidence_targets, start=1)
        ]
        stamped.append(sub_topic.model_copy(update={"evidence_targets": targets}))
    if owed_before and not any(
        target.required
        for sub_topic in stamped
        for target in sub_topic.evidence_targets
    ):
        # Rule 4's second half may not leave a plan that owes nothing: the probe
        # re-run measured that failure, so the first obligation of every
        # sub-topic is required again and the plan keeps owing its dimensions.
        stamped = [
            sub_topic.model_copy(
                update={
                    "evidence_targets": [
                        target.model_copy(update={"required": True})
                        if position == 1
                        else target
                        for position, target in enumerate(
                            sub_topic.evidence_targets, start=1
                        )
                    ]
                }
            )
            for sub_topic in stamped
        ]
    return stamped


def targets_requiring_replanning(
    sub_topics: Sequence[SubTopic],
) -> list[str]:
    """The coverage ids of sub-topics that carry no evidence target.

    A plan written before the target contract loads with an empty list —
    nothing is invented for it — and an empty list is exactly what says it
    cannot be executed: such a plan has to be replanned rather than treated
    as a plan with no obligations.
    """
    return [
        sub_topic.coverage_id
        for sub_topic in sub_topics
        if not sub_topic.evidence_targets
    ]


def inventory_target_ids(sub_topics: Sequence[SubTopic]) -> list[str]:
    """Every counted target id in plan order.

    Filtered through ``counted_evidence_targets`` because these ids populate
    the frozen coverage denominator: the reserved omission reference records a
    gap and is not an evidence obligation, so counting it would inflate the
    inventory Section 2.3 makes load-bearing.
    """
    return [
        target.target_id
        for sub_topic in sub_topics
        for target in counted_evidence_targets(sub_topic.evidence_targets)
    ]


def extend_plan(
    existing: Sequence[SubTopic],
    extension: ResearchPlanDraft,
    *,
    contract: AnswerContract,
) -> tuple[list[SubTopic], list[str]]:
    """Turn a reviewed-omission draft into additional sub-topics, and only.

    Returns the *new* sub-topics with ids continuing after ``existing``, plus
    any problems. Nothing here can touch a topic that already exists: no
    existing id is renumbered, no target is removed, no ``required`` flag is
    cleared, and the contract — question, scope, as-of date — is the one
    already frozen. A plan that no longer fits ``MAX_SUB_TOPICS`` is reported
    as an explicit capacity conflict naming the topics that cannot fit, which
    is not permission to delete a difficult topic.
    """
    problems: list[str] = []
    if len(extension.sub_topics) < 1:
        return [], ["the extension proposes no sub-topics; name the omission "
                    "and the sub-topics that close it"]

    additions: list[SubTopic] = []
    for index, item in enumerate(extension.sub_topics, start=1):
        coverage_id = coverage_id_for(len(existing) + index)
        try:
            additions.append(
                SubTopic.model_validate(
                    {
                        **item.model_dump(exclude={"evidence_targets"}),
                        "coverage_id": coverage_id,
                        "evidence_targets": [
                            target.model_dump()
                            for target in _draft_targets(item, coverage_id)
                        ],
                    }
                )
            )
        except ValidationError as error:
            problems.append(
                f"extension sub-topic {index} is invalid: check these "
                f"fields: {_invalid_fields(error)}"
            )
    if problems:
        return [], problems

    titles = {_normalized_title(sub_topic.title) for sub_topic in existing}
    for position, sub_topic in enumerate(additions, start=1):
        key = _normalized_title(sub_topic.title)
        if key in titles:
            problems.append(
                f"extension sub-topic {position} repeats an existing title; "
                "an extension adds a new dimension rather than restating one"
            )
        titles.add(key)

    total = len(existing) + len(additions)
    if total > MAX_SUB_TOPICS:
        # Nothing is returned here: a partially applied extension is exactly
        # the smaller-denominator failure this refusal exists to prevent.
        return [], [
            *problems,
            f"the plan already carries {len(existing)} sub-topics and the "
            f"extension adds {len(additions)}, which is {total}; a plan "
            f"carries at most {MAX_SUB_TOPICS}. Name which extension "
            "sub-topics to drop — an existing sub-topic is never removed to "
            "make room",
        ]

    stamped = apply_answer_contract(additions, contract)
    problems.extend(target_problems(stamped, contract))
    return stamped, problems


def validate_plan_draft(
    draft: ResearchPlanDraft,
) -> tuple[list[SubTopic], list[str]]:
    """Convert a model plan into ``SubTopic`` values, listing every problem.

    Returns the sub-topics that validated — ordered by priority and carrying
    their assigned ``coverage_id`` — and a list of problem strings. Problem
    text is generated here and never copied from provider output, so it is
    safe to place in a repair prompt and in ``PlanningError.problems``.
    """
    validated: list[tuple[int, SubTopic]] = []
    problems: list[str] = []

    for index, item in enumerate(draft.sub_topics, start=1):
        coverage_id = coverage_id_for(index)
        try:
            validated.append(
                (
                    index,
                    SubTopic.model_validate(
                        {
                            **item.model_dump(exclude={"evidence_targets"}),
                            # A provisional id, so ``SubTopic``'s own rules —
                            # including this field's — apply to every value
                            # here. ``_assign_coverage_ids`` re-stamps all of
                            # them in final order, so no caller ever observes
                            # a draft-position id.
                            "coverage_id": coverage_id,
                            "evidence_targets": [
                                target.model_dump()
                                for target in _draft_targets(item, coverage_id)
                            ],
                        }
                    ),
                )
            )
        except ValidationError as error:
            problems.append(
                f"sub-topic {index} is invalid: check these fields: "
                f"{_invalid_fields(error)}"
            )

    seen: dict[str, int] = {}
    for index, sub_topic in validated:
        key = _normalized_title(sub_topic.title)
        first = seen.get(key)
        if first is None:
            seen[key] = index
        else:
            problems.append(
                f"sub-topics {first} and {index} repeat the same title; "
                "every sub-topic must be distinct"
            )

    sub_topics = [sub_topic for _, sub_topic in validated]
    count = len(sub_topics)
    if count < MIN_SUB_TOPICS or count > MAX_SUB_TOPICS:
        problems.append(
            f"the plan has {count} valid sub-topics; produce between "
            f"{MIN_SUB_TOPICS} and {MAX_SUB_TOPICS}"
        )

    return _assign_coverage_ids(sub_topics), problems


def format_plan_problems(
    problems: Sequence[str], *, plan_printed: bool = True
) -> str:
    """Render plan problems as the corrective instruction for one repair.

    A problem names what it concerns — a target by its id, a sub-topic by its
    position, or the plan as a whole — so the wrapper says that rather than
    claiming every problem carries a target id. ``plan_printed`` is whether the
    request prints the plan under repair: a draft none of whose sub-topics
    validated has no plan to print, and the wrapper then says so instead of
    pointing at a section the request does not carry.
    """
    listed = "\n".join(f"- {problem}" for problem in problems)
    if plan_printed:
        return (
            "The plan under repair is printed above. Fix every problem listed "
            "below — each names the target, the sub-topic or the plan it "
            f"concerns — and return that plan corrected.\n{listed}"
        )
    return (
        "The previous plan could not be validated, so it is not printed. Fix "
        f"every problem listed below and return a corrected plan.\n{listed}"
    )


def requested_problems(review: PlanReviewDraft) -> list[str]:
    """The review's own findings, rendered as bounded problem lines.

    The reviewer's free text is never copied into a problem list unprepared:
    each finding is prefixed with the defect class it belongs to, so a reader
    of ``PlanningError.problems`` can tell a missing dimension from a
    compound target without reading prose.
    """
    problems = [
        f"plan review found a missing dimension: {detail}"
        for detail in review.missing_dimensions
    ]
    problems.extend(
        f"plan review found a compound obligation: {detail}"
        for detail in review.atomicity_defects
    )
    problems.extend(
        f"plan review found an unsupported premise: {detail}"
        for detail in review.unsupported_premises
    )
    if not problems:
        problems.append(
            "plan review reported the plan as unsound without naming a defect"
        )
    return problems


def format_review_problems(review: PlanReviewDraft) -> str:
    """Render a review's findings as the corrective instruction for repair."""
    lines = [f"- {problem}" for problem in requested_problems(review)]
    instruction = review.repair_instruction.strip()
    if instruction:
        lines.append(f"- the reviewer's correction: {instruction}")
    listed = "\n".join(lines)
    return (
        "The plan review found the plan under repair, printed above, "
        "unsound. The original question is unchanged and must not be "
        "rephrased, narrowed, or widened. Fix every defect listed below and "
        f"return that plan corrected.\n{listed}"
    )


def _render_notes(run: ReActRun) -> str:
    """Render what the scoping loop actually learned, one line each."""
    lines = [
        f"- {summarize_text(step.observation.summary)}"
        for step in run.steps
        if step.observation is not None and step.observation.success
    ]
    if run.final_answer is not None:
        lines.append(f"- {summarize_text(run.final_answer)}")
    return "\n".join(lines) or "(no scoping notes)"


def render_answer_contract(contract: AnswerContract) -> str:
    """Print the frozen contract into a request, one field per line."""
    assumptions = (
        "\n".join(f"- {assumption}" for assumption in contract.assumptions)
        or "- none"
    )
    word_limit = (
        "none requested"
        if contract.requested_word_limit is None
        else f"{contract.requested_word_limit} words"
    )
    return (
        f"- Original question (frozen, do not restate or narrow it): "
        f"{contract.question}\n"
        f"- Scope: {contract.geographic_scope}\n"
        f"- As of: {contract.as_of_date}\n"
        f"- Evidence period: {contract.evidence_period_requirement}\n"
        f"- Answer form: {contract.answer_kind} — "
        f"{answer_form_requirement(contract.answer_kind)}\n"
        f"- Reader length: {word_limit}\n"
        f"- Assumptions the plan makes:\n{assumptions}"
    )


def plan_messages(
    task: AgentTask,
    run: ReActRun,
    *,
    contract: AnswerContract | None = None,
    repair: str | None = None,
    plan_under_repair: Sequence[SubTopic] = (),
) -> list[ChatMessage]:
    """Build the messages that request one structured plan draft.

    Static first: the requirements and the reply format are the same text on
    every request, so a provider's prefix cache can reuse them, and they are
    what the model must read before the question they govern (PD-29).

    ``contract`` is the frozen answer contract. The planner always passes
    one; a caller that omits it gets the plan requirements without a frozen
    scope, which is what a replay of an older prompt looks like.
    """
    static = [
        f"# Plan requirements\n{PLAN_INSTRUCTION}",
        f"# Reply format\n{render_structured_reply_format(_PLAN_REPLY_EXAMPLES)}",
    ]
    material = [f"# Research question\n{task.instruction}"]
    if contract is not None:
        material.append(f"# Answer contract\n{render_answer_contract(contract)}")
    if task.guidance.strip():
        material.append(f"# Context\n{task.guidance}")
    material.append(f"# Scoping notes\n{_render_notes(run)}")
    if repair is not None:
        if plan_under_repair:
            # A repair request names its problems by target id
            # ("topic-01-target-02 asks 2 questions at once"), so it has to
            # print the plan those ids belong to: without it the model is
            # asked to correct a plan it cannot see, and the repair is a
            # fresh sample rather than a correction.
            material.append(
                "# Plan under repair (return this plan corrected, not unchanged)\n"
                f"{render_plan_for_review(plan_under_repair)}"
            )
        material.append(f"# Repair\n{repair}")
    return [
        ChatMessage(role="developer", content=PLANNER_PLAN_SYSTEM_PROMPT),
        ChatMessage(role="user", content=render_structured_request(static, material)),
    ]


PLAN_REVIEW_SYSTEM_PROMPT = (
    "You are reviewing a research plan before any research starts. You have no "
    "tools and need none: the question, the frozen answer contract, and the "
    "plan are printed in the request, every target field included. Judge the "
    "plan's meaning against the question, not its formatting."
)

PLAN_REVIEW_INSTRUCTION = (
    "Decide whether this plan, as written, would answer the original question. "
    "Report `sound: true` only when all of the following hold, and name every "
    "finding in the list it belongs to.\n"
    "- Every part of the original question is covered by some sub-topic, and "
    "every required flag matches the question: a target the question itself "
    "asks for is required, while a target the plan added — a sub-category, an "
    "aspect, an example, an item, an attribute the question does not name, a "
    "body the plan chose where the question names none, or an aid that makes "
    "another target checkable — is optional. Name each missing part of the "
    "question in `missing_dimensions`, and each required target the question "
    "does not ask for in `unsupported_premises`.\n"
    "- Every target's organisation is a name the evidence pages will carry as "
    "their source, or empty: never an adopting or enacting body, a joined pair "
    "of bodies, a description of a role, or an author no page credits, and a "
    "required target never rests on one chosen body's pages being reachable. "
    "Name each such target in `unsupported_premises`.\n"
    "- Every evidence target is atomic: one measure, one rule date, one "
    "jurisdiction. The text of a rule, a list or a set of items the question "
    "asks for in one clause is one atomic target; a target that requires two "
    "measures, two periods or two jurisdictions to be settled is compound even "
    "when it reads as one sentence. A target whose answer is a rule's date, "
    "deadline, threshold or duration carries no unit or kind unless the "
    "question asks for that quantity itself; name one that does in "
    "`atomicity_defects`. Name each compound target in `atomicity_defects`.\n"
    "- No target asks for a verdict, a pick, a ranking or a comparison the "
    "run itself would have to make — a pick or a ranking a page states is "
    "evidence, the run's own is not — or for a combination of other targets' "
    "answers, or for an item selected by another target's answer. Name each "
    "one in `atomicity_defects`.\n"
    "- No search query or success criterion assumes the answer, states a "
    "conclusion the plan has not established, or treats a recalled memory as "
    "evidence; a query says where to look, never the value, date, item or pick "
    "a target asks for. Name each such premise in `unsupported_premises`.\n"
    "- The plan stays inside the frozen scope and as-of date. Name a target "
    "that widens the scope or re-anchors the period in "
    "`unsupported_premises`. When the as-of date is past a period the "
    "question frames as a forecast, the plan holds an optional target for "
    "that period's actual outcome; name its absence in "
    "`missing_dimensions`.\n"
    "`repair_instruction` carries the corrections, one per defect you named; "
    "leave it empty when sound is true. Name the question's own parts, never "
    "rephrase the original question."
)

def render_plan_for_review(sub_topics: Sequence[SubTopic]) -> str:
    """Print a plan as the review request sees it, ids and all.

    Each target prints its id, whether the question names it, its question,
    and every field a program checks an answer against — measure, unit,
    period, kind, geography and organisation, ``none`` for an empty one — so
    the reviewer judges the fields that decide an answer rather than a
    summary of them.
    """
    lines: list[str] = []
    for sub_topic in sub_topics:
        lines.append(
            f"{sub_topic.coverage_id} (priority {sub_topic.priority}): "
            f"{sub_topic.title}"
        )
        lines.append(f"  rationale: {sub_topic.rationale}")
        lines.append(f"  queries: {'; '.join(sub_topic.search_queries)}")
        lines.append(f"  criteria: {'; '.join(sub_topic.success_criteria)}")
        for target in sub_topic.evidence_targets:
            requiredness = "required" if target.required else "optional"
            lines.append(
                f"  {target.target_id} [{requiredness}]: {target.question}"
            )
            fields = (
                ("measure", target.measure),
                ("unit", target.unit_dimension),
                ("period", target.period),
                ("kind", target.kind),
                ("geography", target.geography),
                ("organisation", target.organisation),
            )
            lines.append(
                "    fields: "
                + "; ".join(
                    f"{name} {value or 'none'}" for name, value in fields
                )
            )
    return "\n".join(lines)


def plan_review_messages(
    contract: AnswerContract,
    sub_topics: Sequence[SubTopic],
    *,
    repair: str | None = None,
) -> list[ChatMessage]:
    """Build the one tool-free request that reviews a plan's meaning."""
    sections = [
        f"# Original question (frozen)\n{contract.question}",
        f"# Answer contract\n{render_answer_contract(contract)}",
        f"# Plan under review\n{render_plan_for_review(sub_topics)}",
        f"# Review requirements\n{PLAN_REVIEW_INSTRUCTION}",
    ]
    if repair is not None:
        sections.append(f"# Correction already requested\n{repair}")
    return [
        ChatMessage(role="developer", content=PLAN_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def planning_started_event(state: ResearchState) -> ResearchEvent:
    """Announce that planning began, before any provider call."""
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.started",
        message="Planning started.",
        metadata={
            "iteration": state.iteration,
            "min_sub_topics": MIN_SUB_TOPICS,
            "max_sub_topics": MAX_SUB_TOPICS,
        },
    )


def memory_recalled_event(memory_context: MemorySnapshot) -> ResearchEvent:
    """Report how much long-term memory the session started with."""
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.memory.recalled",
        message="Memory recall complete.",
        metadata={
            "recalled_findings": len(memory_context.similar_findings),
            "known_source_reputations": len(
                memory_context.known_source_reputations
            ),
            "suggested_strategies": len(memory_context.suggested_strategies),
        },
    )


def planning_completed_event(outcome: AgentRun["ResearchPlan"]) -> ResearchEvent:
    """Report the finished plan's size, its sub-topics and how the scoping loop stopped.

    ``sub_topics`` lists each planned sub-topic's ``coverage_id`` and title, the
    title capped at 160 characters (live-briefs spec AC2): plan content a console
    shows the reader, never provider error text (``agents/events.py``).
    """
    plan = outcome.result
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
        message="Planning complete.",
        metadata={
            "sub_topic_count": 0 if plan is None else len(plan.sub_topics),
            "sub_topics": []
            if plan is None
            else [
                {
                    "coverage_id": sub_topic.coverage_id,
                    "title": summarize_text(sub_topic.title, limit=160),
                }
                for sub_topic in plan.sub_topics
            ],
            "repair_attempted": False if plan is None else plan.repair_attempted,
            "stop_reason": outcome.react.stop_reason,
            "iterations": outcome.react.iterations,
            "tool_calls": outcome.react.tool_calls,
        },
    )


_UNCATEGORIZED = "unclassified"


def structured_output_problems(error: StructuredOutputError) -> tuple[str, ...]:
    """Render each structured-validation diagnostic as one project-authored line.

    The provider throws its diagnostic away one frame above the catch that
    turns a failed plan request into a ``PlanningError``, so these lines are
    the last artifact that can explain a ``graph_planning_failed`` run.

    Only ``attempt``, ``field_paths`` and ``category`` are read: never the
    exception message, provider text, or the rejected model output. The
    contract already normalizes every field path, replacing anything that is
    not a plain schema path with ``"$"``, and already keeps at most two
    diagnostics — so this returns at most two short lines. A diagnostic that
    recorded no ``category`` renders as ``unclassified``.
    """
    return tuple(
        "the plan draft failed schema validation on attempt "
        f"{diagnostic.attempt} at {', '.join(diagnostic.field_paths)} "
        f"({diagnostic.category or _UNCATEGORIZED})"
        for diagnostic in error.diagnostics
    )


@dataclass(frozen=True)
class _PlanAttempt:
    """One plan request's result, with the plan its problems came from.

    ``plan`` is the label every problem this attempt carries is published
    under. The two problem lists are split because they are not equally fatal:
    ``structural`` problems decide whether anything can be researched at all —
    ``validate_plan_draft``'s own, plus the two local ones that make a plan
    unexecutable (a sub-topic carrying outside 1-6 evidence targets, and a
    target written as an assertion rather than a question) — while ``advisory``
    problems come from the contract-level checks — a stale anchor, an invented
    tolerance — and are a judgement about meaning, which is the review's to
    make.
    """

    plan: str
    sub_topics: list[SubTopic]
    structural: list[str]
    advisory: list[str]

    @property
    def usable(self) -> bool:
        """True when this attempt is a plan a researcher could be handed."""
        return not self.structural

    @property
    def problems(self) -> list[str]:
        """Every problem this attempt carries, unlabelled and in order.

        This is what the model's repair request reads: the labels are for the
        records, and a plan prefix inside the prompt is a silent change to
        model-visible input.
        """
        return [*self.structural, *self.advisory]

    @property
    def labelled(self) -> list[str]:
        """Every problem this attempt carries, labelled with its own plan."""
        return [f"{self.plan}: {problem}" for problem in self.problems]

    @property
    def labelled_advisory(self) -> list[str]:
        """The advisory problems, labelled with the plan they were found on."""
        return [f"{self.plan}: {problem}" for problem in self.advisory]


def _exception_name(error: BaseException) -> str:
    """The class name of a failure, and never its message.

    ``str(exception)`` can carry request text, URLs, and paths, and these
    details are copied into the run's state and published; the class is the
    whole diagnosis, which is why every other recorded failure in this project
    records ``exception_type`` instead of a rendered exception.
    """
    cause = error.__cause__ if error.__cause__ is not None else error
    return type(cause).__name__


def _raised_problems(
    error: PlanningError,
    *,
    label: str,
    what: str,
) -> list[str]:
    """The lines one failed plan request is recorded under, labelled.

    ``what`` names the request in project words, ``_exception_name`` gives the
    failure class, and the request's own static lines follow — each prefixed
    with the plan it concerns, like every other problem this planner reports,
    so a truncated repair is distinguishable from a schema failure without
    publishing anything a provider wrote.
    """
    return [
        f"{label}: {what} raised {_exception_name(error)}",
        *[f"{label}: {problem}" for problem in error.problems],
    ]


def _planner_output_limit_retry(
    error: Exception,
    *,
    operation: str,
    schema: str,
    max_tokens: int | None,
    outcome: str,
) -> ResearchError:
    """Record that a truncated plan-side request was re-asked at another effort.

    The reviewer's record, for the planner's own calls: one retry, the same
    output budget, and the outcome of the retry call itself. It exists because
    the retry is a second paid call — without it, a plan that took two
    requests is indistinguishable in the artifacts from one that took one — and
    the request, effort, budget and outcome are in the *message*, which is what
    a reader sees for an error type whose details are not projected.
    """
    if outcome not in OUTPUT_LIMIT_RETRY_OUTCOMES:
        raise ValueError(f"unknown retry outcome: {outcome!r}")
    return agent_error(
        agent_name=PLANNER_NAME,
        error_type="planner_output_limit_retry",
        message=(
            f"The {schema} plan request was truncated by the output limit; it "
            f"was re-asked once at reasoning_effort {OUTPUT_LIMIT_RETRY_EFFORT} "
            f"with the same {max_tokens}-token output budget, and "
            f"{OUTPUT_LIMIT_RETRY_READINGS[outcome]}"
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            operation,
            error,
            attempt=2,
            schema=schema,
            reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
            max_tokens=max_tokens,
            outcome=outcome,
        ),
    )


class PlannerAgent(BaseAgent[ResearchPlan]):
    """Convert ``original_question`` into 1-10 distinct, prioritized sub-topics.

    The ReAct loop is for scoping only — the session's own startup recall is
    the planner's single procedural lookup, and ``web_search`` is available
    for unfamiliar terminology. The plan itself is produced in ``finalize``
    by a structured-output call over what the loop learned, and is then
    reviewed once by a tool-free semantic call.
    """

    name = PLANNER_NAME
    description = "Turn a research question into a validated research plan."
    allowed_tools = ("query_memory", "web_search")
    preserve_provider_errors = True
    # The planner's prompt contract changed in Task 2: the plan request prints
    # the frozen answer contract and asks for atomic evidence targets, and a
    # tool-free plan review runs after it. An artifact therefore says which
    # planner instructions produced it.
    prompt_version = "planner-2"

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
                "PlannerAgent clock must return a timezone-aware datetime; "
                "got a naive datetime instead"
            )
        self._clock = clock
        # Set for the duration of one run by ``run``: the tools this run
        # offers, or ``None`` when the inherited toolset applies unchanged.
        self._restricted_toolset: AgentToolset | None = None
        # Set for the duration of one run by ``run`` from the state it was
        # handed: the contract this session already froze, if any.
        self._frozen_contract: AnswerContract | None = None
        # Set for the duration of one run by ``run`` from the state it was
        # handed: the coverage ids this session already planned, so a later
        # non-extension pass cannot re-emit one beside itself.
        self._planned_coverage_ids: frozenset[str] = frozenset()
        # Counted per run so the review/repair cycle stays bounded.
        self._review_calls = 0

    @property
    def output_schema(self) -> type[ResearchPlan]:
        """The validated plan. Never sent to the provider.

        ``finalize`` asks for ``ResearchPlanDraft`` instead, because
        ``ResearchPlan`` nests ``SubTopic``, whose ``Field`` constraints do
        not survive strict JSON schema conversion. Do not route this agent
        through ``complete_output``.
        """
        return ResearchPlan

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return PLANNER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        return AgentTask(
            instruction=state.original_question,
            guidance=planner_guidance(state.memory_context),
        )

    @property
    def frozen_contract(self) -> AnswerContract | None:
        """The contract this session already froze, if any.

        ``None`` until ``run`` has been handed a state; set from the state's
        own ``answer_contract`` so both ``finalize`` and ``state_update`` can
        honour it.
        """
        return self._frozen_contract

    @property
    def toolset(self) -> AgentToolset:
        """The tools this run offers, with the planner's lookup rule applied.

        ``query_memory`` is dropped when the session's startup recall already
        produced guidance: that recall *is* the planner's single procedural
        lookup, and a second one would repeat work the session has done. The
        restriction is applied per run, not at construction, because the
        decision depends on what recall found; ``web_search`` is unaffected,
        since scoping unfamiliar terminology is not a memory lookup.
        """
        restricted = self._restricted_toolset
        return self._toolset if restricted is None else restricted

    async def run(self, state: ResearchState) -> AgentRun[ResearchPlan]:
        """Run the inherited loop, bracketed by planning progress events.

        Two per-run facts are settled here because ``finalize`` and
        ``state_update`` receive only the task and the loop: whether the
        session's startup recall produced guidance (which decides whether
        ``query_memory`` is offered at all), and whether the session already
        has a frozen answer contract (which decides whether this pass may
        stamp one).
        """
        self._restricted_toolset = (
            self._toolset.without("query_memory")
            if has_startup_guidance(state.memory_context)
            else None
        )
        self._frozen_contract = state.answer_contract
        self._planned_coverage_ids = frozenset(
            sub_topic.coverage_id for sub_topic in state.sub_topics
        )
        self._review_calls = 0
        events = [
            planning_started_event(state),
            memory_recalled_event(state.memory_context),
        ]
        # Published live (live-briefs spec E3); the same objects are returned below.
        for event in events:
            publish_live(event)
        try:
            outcome = await super().run(state)
        except ProviderError as error:
            raise planning_provider_error("react_decision") from error
        finally:
            self._restricted_toolset = None
        completed = planning_completed_event(outcome)
        publish_live(completed)
        events.append(completed)
        return AgentRun(
            agent_name=outcome.agent_name,
            result=outcome.result,
            react=outcome.react,
            errors=outcome.errors,
            state_update={**outcome.state_update, "events": events},
            call_fingerprints=dict(outcome.call_fingerprints),
        )

    def answer_contract_for(self, question: str) -> AnswerContract:
        """Freeze this run's answer contract from the injected clock.

        A session that already froze one is bound by it, unchanged: the new
        derivation is not merged into it at all (``frozen_contract_for``), so a
        later planning pass cannot re-anchor the as-of date, widen the scope,
        or fill a field the frozen question never asked for.
        """
        derived = derive_answer_contract(question=question, now=self._clock())
        if self._frozen_contract is None:
            return derived
        return frozen_contract_for(self._frozen_contract, derived)

    async def _request_plan(
        self,
        task: AgentTask,
        run: ReActRun,
        *,
        contract: AnswerContract,
        plan: str,
        repair: str | None = None,
        plan_under_repair: Sequence[SubTopic] = (),
    ) -> _PlanAttempt:
        try:
            draft = await self._complete_plan_request(
                plan_messages(
                    task,
                    run,
                    contract=contract,
                    repair=repair,
                    plan_under_repair=plan_under_repair,
                ),
                ResearchPlanDraft,
                operation="plan_draft",
                run=run,
            )
        except StructuredOutputError as error:
            raise planning_provider_error(
                "plan_draft", problems=structured_output_problems(error)
            ) from error
        except ProviderError as error:
            raise planning_provider_error("plan_draft") from error
        return self._stamp(draft, contract, plan=plan)

    def _stamp(
        self,
        draft: ResearchPlanDraft,
        contract: AnswerContract,
        *,
        plan: str,
    ) -> _PlanAttempt:
        """Validate a draft, then attach the contract's binding obligations.

        The two problem lists are kept apart on purpose. A draft that fails
        ``validate_plan_draft`` is not stamped at all — there is nothing to
        stamp — and its problems are structural. A draft that passes may still
        fail the contract-level checks, and those split too: the target count
        and the question form decide whether the plan can be executed at all,
        while a stale anchor or an invented tolerance is a judgement about
        meaning that the review owns.
        """
        sub_topics, structural = validate_plan_draft(draft)
        if structural:
            return _PlanAttempt(
                plan=plan,
                sub_topics=sub_topics,
                structural=structural,
                advisory=[],
            )
        stamped = apply_answer_contract(sub_topics, contract)
        problems = _plan_problems(stamped, contract)
        return _PlanAttempt(
            plan=plan,
            sub_topics=stamped,
            structural=[
                problem.text
                for problem in problems
                if problem.kind == "structural"
            ],
            advisory=[
                problem.text
                for problem in problems
                if problem.kind == "advisory"
            ],
        )

    async def _review_plan(
        self,
        contract: AnswerContract,
        sub_topics: Sequence[SubTopic],
        *,
        already_requested: str | None = None,
        run: ReActRun,
    ) -> PlanReviewDraft:
        """Ask the one tool-free semantic review call about this plan.

        The review is a review problem like any other, so a provider failure
        here is reported the same way a failed plan draft is. The call count
        is bounded at ``MAX_PLAN_REVIEW_CALLS`` for the whole run: exceeding it
        is a defect in the flow, not a reason to keep asking.
        """
        if self._review_calls >= MAX_PLAN_REVIEW_CALLS:
            raise PlanningError(
                "The planner's bounded plan-review cycle was exceeded.",
                problems=[
                    f"more than {MAX_PLAN_REVIEW_CALLS} plan reviews were "
                    "requested in one planning run"
                ],
            )
        self._review_calls += 1
        try:
            return await self._complete_plan_request(
                plan_review_messages(
                    contract, sub_topics, repair=already_requested
                ),
                PlanReviewDraft,
                operation="plan_review",
                run=run,
            )
        except StructuredOutputError as error:
            raise planning_provider_error(
                "plan_review", problems=structured_output_problems(error)
            ) from error
        except ProviderError as error:
            raise planning_provider_error("plan_review") from error

    async def _complete_plan_request(
        self,
        messages: list[ChatMessage],
        schema: type[Any],
        *,
        operation: str,
        run: ReActRun,
    ) -> Any:
        """One plan-side structured request, re-asked once if it is truncated.

        Every plan-side call — the draft, its two repairs, the review and the
        confirming review — runs under ``planner_final_max_tokens``, and run 2's
        plan call ended at 90 % of it: a request that reasons past the cap raises
        ``ProviderOutputLimitError``, which is not retryable, and a truncated
        first draft ended the run before any research. A truncation is the one
        failure a different request can fix, so it is re-asked once with the
        same messages, schema and output budget at the shared retry effort,
        which leaves more of that budget for the answer — the reviewer's and
        the writer's rule (``OUTPUT_LIMIT_RETRY_EFFORT``), not a larger cap.

        The retry is recorded on the run whatever it returns. A second
        truncation propagates as a redacted copy through the caller's own
        failure path, exactly as a first truncation did before; any other
        failure of the retry propagates through that path too.
        """
        budget = self.config.planner_final_max_tokens
        self.fingerprint_call(schema.__name__, output_limit=budget)
        try:
            return await self.provider.complete_structured(
                messages, schema, agent_name=self.name, max_tokens=budget
            )
        except ProviderOutputLimitError as error:
            truncation = error
        self.fingerprint_call(
            schema.__name__,
            output_limit=budget,
            reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
        )

        def record(outcome: str) -> None:
            run.errors.append(
                _planner_output_limit_retry(
                    truncation,
                    operation=operation,
                    schema=schema.__name__,
                    max_tokens=budget,
                    outcome=outcome,
                )
            )

        try:
            reply = await self.provider.complete_structured(
                messages,
                schema,
                agent_name=self.name,
                max_tokens=budget,
                reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
            )
        except ProviderOutputLimitError as retry_error:
            record("truncated")
            raise retry_error.redacted_copy(
                ProviderOutputLimitError.SAFE_MESSAGE
            ) from None
        except ProviderError as retry_error:
            # The reviewer's rule: the retry's failure keeps its type and its
            # provider-free diagnostics (a schema failure's still reach
            # ``structured_output_problems``), but the caller gets a fresh copy
            # with the provider exception chain cut, never the raised object.
            record("failed")
            raise retry_error.redacted_copy(str(retry_error)) from None
        record("answered")
        return reply

    def _without_defective_targets(
        self,
        attempt: _PlanAttempt,
        *,
        contract: AnswerContract,
    ) -> _PlanAttempt:
        """The attempt with every target a surviving advisory defect names removed.

        The one repair is what a plan's own local defects get. A plan that keeps
        the very target its repair was told about has not taken the correction,
        and the live nine-question probe's P3, P4 and P6 plans shipped a
        planner_plan_defects_unresolved record for exactly that: an advisory
        naming a target the plan kept anyway. Removing the target removes the
        defect — the plan ships without it, its id leaves the frozen inventory
        because ids are inventoried after this point, and the pass records
        nothing about it.

        Structural problems are untouched: they decide whether a plan can be
        executed at all, and finalize's own branch is what judges them. A
        plan whose last target is named, or whose sub-topic loses all of them, is
        returned unchanged rather than emptied: a plan has to exist to be
        researched, and an empty one is the replanning case rather than this one.
        """
        named = {problem.split(" ", 1)[0] for problem in attempt.advisory}
        if not named:
            return attempt
        owed = any(
            target.required
            for sub_topic in attempt.sub_topics
            for target in sub_topic.evidence_targets
        )
        pruned: list[SubTopic] = []
        for sub_topic in attempt.sub_topics:
            kept = [
                target
                for target in sub_topic.evidence_targets
                if target.target_id not in named
            ]
            if not kept:
                continue
            pruned.append(
                sub_topic.model_copy(update={"evidence_targets": kept})
            )
        if not pruned:
            return attempt
        if owed and not any(
            target.required
            for sub_topic in pruned
            for target in sub_topic.evidence_targets
        ):
            # The dropped targets were the whole of what the plan owed. A plan
            # that owes nothing is the failure the probe re-run measured (every
            # target optional), so the first obligation of every sub-topic that
            # survived is required again: the plan keeps owing its dimensions
            # and still ships without the target its repair was told about.
            pruned = [
                sub_topic.model_copy(
                    update={
                        "evidence_targets": [
                            target.model_copy(update={"required": True})
                            if position == 1
                            else target
                            for position, target in enumerate(
                                sub_topic.evidence_targets, start=1
                            )
                        ]
                    }
                )
                for sub_topic in pruned
            ]
        problems = _plan_problems(pruned, contract)
        return _PlanAttempt(
            plan=attempt.plan,
            sub_topics=pruned,
            structural=[
                problem.text
                for problem in problems
                if problem.kind == "structural"
            ],
            advisory=[
                problem.text
                for problem in problems
                if problem.kind == "advisory"
            ],
        )

    def _record_defects(
        self,
        run: ReActRun,
        *,
        stage: str,
        plan: str,
        problems: Sequence[str],
    ) -> None:
        """Record one unresolved planning outcome without ending the run.

        The record is an ordinary recoverable error, so it reaches the CLI's
        warning block, the ledger's run-error table, and the quality record's
        error registry by the path every recorded error takes. ``plan`` names
        the plan the problems belong to and every problem carries that label:
        the merged, unlabelled list is what made the live run 3 diagnosis wrong.

        Nothing here is an acceptance gate. The report-level gates judge what
        the *research* produced; a planning defect that survived its bounded
        repair is a fact about how the plan was made, and it is recorded so a
        reader can see it rather than being allowed to decide the verdict.
        """
        if not problems:
            return
        run.errors.append(
            agent_error(
                agent_name=self.name,
                error_type=_PLAN_DEFECTS_ERROR_TYPE,
                message=f"{_PLAN_DEFECT_MESSAGES[stage]} (plan {plan}).",
                recoverable=True,
                details={
                    "stage": stage,
                    "plan": plan,
                    "problems": list(problems),
                },
            )
        )

    def _plan_from(
        self,
        attempt: _PlanAttempt,
        *,
        contract: AnswerContract,
        repaired: bool,
    ) -> ResearchPlan:
        """The plan these sub-topics carry, and how it was arrived at."""
        return ResearchPlan(
            sub_topics=attempt.sub_topics,
            repair_attempted=repaired,
            answer_contract=contract,
        )

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> ResearchPlan | None:
        """Request a plan, repair and review it at most once each, or fail.

        The bounds are unchanged — one repair of the local checks, one semantic
        review, at most one repair of what the review named, and one confirming
        review — but a surviving defect no longer ends the run. Planning raises
        only when no structurally valid plan exists: when both the draft and
        its repair fail the structural checks, which are the target count, the
        question form, and ``validate_plan_draft``. Anything else continues
        with the structurally valid candidate, and the defects that outlived
        their one repair — including the review's findings against a plan kept
        without one — are recorded as ``planner_plan_defects_unresolved``.

        That is the deliberate reading of the planner's own design: its lints
        disclaim authority over meaning ("the plan review call is what judges
        meaning"), the review's verdict cannot be relied on to be right — the
        planner's own instruction and example produce findings the review then
        rejects — and a repair is a fresh sample that can add defects rather
        than remove them. Three of four live CLI runs ended at
        ``graph_planning_failed`` before any research and none published a
        report; two of those three died on one false lint, and the third on a
        truncated repair.

        A repair request that cannot be produced at all — a provider failure,
        a truncation, a schema failure — is recorded and the run continues with
        the plan that stands: the draft it was repairing, or the plan the
        review judged. That matters most for the lint repair, which is the
        heaviest plan request of the cycle, so only a draft that is itself
        structurally invalid stays fatal when its repair fails.
        """
        if not run.succeeded:
            raise planning_provider_error("react_loop")

        contract = self.answer_contract_for(task.instruction)
        attempt = await self._request_plan(
            task, run, contract=contract, plan=_PLAN_DRAFT_LABEL
        )
        repaired = False
        if attempt.structural or attempt.advisory:
            repaired = True
            try:
                reattempt = await self._request_plan(
                    task,
                    run,
                    contract=contract,
                    repair=format_plan_problems(
                        attempt.problems,
                        plan_printed=bool(attempt.sub_topics),
                    ),
                    plan=_PLAN_REPAIR_LABEL,
                    plan_under_repair=attempt.sub_topics,
                )
            except PlanningError as error:
                # A repair that cannot be produced is not a reason to end a run
                # whose draft is researchable: repairs are the heaviest plan
                # request, and live run 2 truncated on one. When the draft is
                # not researchable either, there is nothing left to hand the
                # researcher, so the failure stays fatal and carries the
                # draft's own labelled problems.
                failures = _raised_problems(
                    error, label=_PLAN_REPAIR_LABEL, what="the plan repair"
                )
                if not attempt.usable:
                    raise PlanningError(
                        str(error),
                        problems=[*attempt.labelled, *failures],
                        operation=error.operation,
                    ) from error
                self._record_defects(
                    run,
                    stage="plan_repair",
                    plan=_PLAN_REPAIR_LABEL,
                    problems=failures,
                )
            else:
                if reattempt.structural and attempt.structural:
                    raise PlanningError(
                        "The planner could not produce a structurally valid "
                        "research plan after one repair attempt.",
                        problems=[*attempt.labelled, *reattempt.labelled],
                    )
                if reattempt.usable:
                    attempt = reattempt
                else:
                    # The repair is not a plan anyone can research, while the
                    # earlier attempt is — the branch above returned otherwise
                    # — so the draft stands and the repair's defects are
                    # recorded against the repair.
                    self._record_defects(
                        run,
                        stage="plan_repair",
                        plan=reattempt.plan,
                        problems=reattempt.labelled,
                    )
        attempt = self._without_defective_targets(attempt, contract=contract)
        self._record_defects(
            run,
            stage="plan_checks",
            plan=attempt.plan,
            problems=attempt.labelled_advisory,
        )

        try:
            review = await self._review_plan(
                contract, attempt.sub_topics, run=run
            )
        except PlanningError as error:
            # The review that runs on every planning pass is a request like any
            # other, and this one was the last unguarded call in the cycle: a
            # provider failure, a truncation or a schema failure on it raised
            # out of ``finalize`` and ended the run at ``graph_planning_failed``
            # with nothing published. The plan it never judged is structurally
            # valid and researched nothing yet, so it stands and the failure is
            # recorded as an ordinary plan defect — the same fallback the
            # confirming review, the lint repair and the review repair take.
            self._record_defects(
                run,
                stage="plan_review",
                plan=attempt.plan,
                problems=_raised_problems(
                    error, label=attempt.plan, what="the plan review"
                ),
            )
            return self._plan_from(attempt, contract=contract, repaired=repaired)
        if review.sound:
            return self._plan_from(attempt, contract=contract, repaired=repaired)

        requested = format_review_problems(review)
        # The findings the review made against the plan that stands. They are
        # recorded on both fallbacks below, because the research about to run
        # is the research that carries them unaddressed.
        rejected = [
            f"{attempt.plan}: {problem}"
            for problem in requested_problems(review)
        ]
        try:
            reattempt = await self._request_plan(
                task,
                run,
                contract=contract,
                repair=requested,
                plan=_PLAN_REVIEW_REPAIR_LABEL,
                plan_under_repair=attempt.sub_topics,
            )
        except PlanningError as error:
            # A repair that cannot be produced is not a reason to end the run:
            # the plan the review judged is structurally valid and researched
            # nothing yet, which is strictly better than no report at all.
            self._record_defects(
                run,
                stage="review",
                plan=attempt.plan,
                problems=rejected,
            )
            self._record_defects(
                run,
                stage="review_repair",
                plan=_PLAN_REVIEW_REPAIR_LABEL,
                problems=_raised_problems(
                    error,
                    label=_PLAN_REVIEW_REPAIR_LABEL,
                    what="the plan review repair",
                ),
            )
            return self._plan_from(attempt, contract=contract, repaired=True)
        if not reattempt.usable:
            self._record_defects(
                run,
                stage="review",
                plan=attempt.plan,
                problems=rejected,
            )
            self._record_defects(
                run,
                stage="review_repair",
                plan=reattempt.plan,
                problems=reattempt.labelled,
            )
            return self._plan_from(attempt, contract=contract, repaired=True)
        attempt = reattempt
        self._record_defects(
            run,
            stage="plan_checks",
            plan=attempt.plan,
            problems=attempt.labelled_advisory,
        )
        confirming: PlanReviewDraft | None = None
        try:
            confirming = await self._review_plan(
                contract, attempt.sub_topics, already_requested=requested, run=run
            )
        except PlanningError as error:
            self._record_defects(
                run,
                stage="confirming_review",
                plan=attempt.plan,
                problems=_raised_problems(
                    error,
                    label=attempt.plan,
                    what="the confirming plan review",
                ),
            )
        if confirming is not None and not confirming.sound:
            self._record_defects(
                run,
                stage="confirming_review",
                plan=attempt.plan,
                problems=[
                    f"{attempt.plan}: {problem}"
                    for problem in requested_problems(confirming)
                ],
            )
        return self._plan_from(attempt, contract=contract, repaired=True)

    def state_update(
        self,
        result: ResearchPlan | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The plan, and for a first plan the frozen contract and inventory.

        A pass on a session that already carries a plan reports only the
        sub-topics whose coverage ids the session does not have — which for a
        re-plan of the same question is none of them. That is what keeps the
        live topic list and the frozen inventory in agreement: ``sub_topics``
        appends, so re-emitting ``topic-01`` beside the existing ``topic-01``
        would put two different topics under one id while
        ``initial_target_ids`` — union-protected — kept counting both. The plan
        already reviewed stands, and a pass cannot replace or weaken it
        (Section 2.3); only genuinely new ids cross this boundary.

        The contract is stamped only when the session has none. A later plan
        therefore cannot re-anchor a session's as-of date or scope: the state
        keeps the contract it froze, and the pass's own
        ``result.answer_contract`` is already that frozen one
        (``frozen_contract_for``), so a replay of the same plan is a no-op
        rather than a rewrite.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is None:
            return update
        added = [
            sub_topic
            for sub_topic in result.sub_topics
            if sub_topic.coverage_id not in self._planned_coverage_ids
        ]
        target_ids = inventory_target_ids(added)
        if self._frozen_contract is None and result.answer_contract is not None:
            update["answer_contract"] = result.answer_contract
        if added:
            update["sub_topics"] = list(added)
            update["initial_target_ids"] = target_ids
        return update
