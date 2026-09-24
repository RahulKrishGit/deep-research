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
from typing import Literal, TypeAlias, get_args

from pydantic import Field, ValidationError, field_validator, model_validator

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.claim_clusters import (
    LEGACY_COVERAGE_DIMENSION,
    METADATA_DIMENSIONS,
    checkable_dimensions,
    metadata_dimension_asked_for,
)
from deep_research.agents.errors import (
    AgentConfigurationError,
    PlanningError,
    agent_error,
    planning_provider_error,
)
from deep_research.agents.events import agent_event
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_structured_reply_format,
)
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.toolset import AgentToolset
from deep_research.agents.validation import _invalid_fields
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    StructuredOutputError,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.text import collapse_whitespace, unique_phrases
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
    UnitDimension,
    _ENERGY_UNIT,
    _POWER_UNIT,
    counted_evidence_targets,
)

PLANNER_NAME = "planner"
MIN_SUB_TOPICS = 3
MAX_SUB_TOPICS = 7
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

# What a satisfactory answer to each form looks like. The planner writes the
# matching requirement into every target's dimensions, so the obligation a
# researcher is handed already says what shape of answer closes it.
_ANSWER_FORM_REQUIREMENTS: dict[AnswerKind, str] = {
    "constraints": (
        "answer form: a list of the binding constraints, each with the "
        "instrument, rule, or authority that imposes it and the date it took "
        "effect"
    ),
    "comparison": (
        "answer form: the same measured dimension for every option in the "
        "comparison, on one shared basis and unit"
    ),
    "explanation": (
        "answer form: a causal mechanism with evidence for each step, not a "
        "correlation and not a restatement of the outcome"
    ),
    "factual": (
        "answer form: the specific fact asked for, with its value, unit, and "
        "the date the value applies to"
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
_WORD_LIMIT_PATTERN = re.compile(r"\b(\d{2,7})[- ]words?\b", re.IGNORECASE)

# Support-policy markers, in precedence order (see ``support_policy_for``).
_DERIVATION_MARKERS = (
    "calculate",
    "calculated",
    "compute",
    "computed",
    "derive",
    "derived",
    # "per" as a whole word, never " per ": a marker with its own surrounding
    # spaces cannot satisfy a token-boundary lookaround, so "cost per megawatt"
    # stopped matching at all.
    "per",
    "per capita",
    "per year",
    "per unit",
    "ratio",
    "rate of",
    "rates of",
    "convert",
    "normalize",
    "normalised",
    "normalized",
)
_PRIMARY_ATTRIBUTION_MARKERS = (
    # Phrases are listed in the forms questions use; a phrase never takes a
    # derived suffix, because the suffix would attach to its last word.
    "effective date",
    "effective dates",
    "in force",
    "came into force",
    "official",
    "definition of",
    "definitions of",
    "defined as",
    "regulator",
    "regulatory",
    "regulation",
    "statute",
    "standard specifies",
    "tariff",
    "fee schedule",
    "fee schedules",
    "permit",
    "licence",
    "license",
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
    "This session's startup memory recall has already run and its procedural "
    "guidance is printed in the context below; it is the planner's single "
    "memory lookup. Call query_memory only when that context carries no "
    "guidance at all, and then once. Use web_search only to scope unfamiliar "
    "terminology — a later agent gathers the evidence, so do not research "
    "the question here.\n"
    "If every term in the research question is familiar to you, finish "
    "without searching.\n"
    "Finish as soon as you understand the shape of the question."
)

# The plan request offers NO tools, so its prompt must not name any. It is a
# separate call from the ReAct scoping loop above: that loop really does carry
# the tools, and this one really does not.
PLANNER_PLAN_SYSTEM_PROMPT = (
    "You are the planner of a multi-agent research system. Turn the research "
    "question, context, and completed scoping notes printed below into a final "
    "research plan. Everything needed for this structured plan is already in "
    "the request. Do not propose or describe another lookup."
)

PLAN_INSTRUCTION = (
    f"Produce a research plan of between {MIN_SUB_TOPICS} and "
    f"{MAX_SUB_TOPICS} distinct sub-topics that together answer the "
    "research question.\n"
    "Every sub-topic needs a title, a rationale explaining why answering it "
    "is necessary, at least one concrete web search query, at least one "
    "success criterion describing what evidence would settle it, and a "
    "priority where 1 is the most important.\n"
    "List the sub-topics in priority order, most important first.\n"
    # The instruction used to require a benefits-and-risks sub-topic for any
    # technology question, which is exactly the scope widening the plan review
    # names as a defect ("Name any target that widens the scope"), so the
    # planner's own instruction and its own review contradicted each other.
    # Coverage is now conditional on the question, and the demand for content
    # the question never made is named where it comes from: the run's plan
    # asked for MWh beside a capacity question, for the agency's short-term
    # outlook series by name, and for a second independent publisher beside
    # figures only one agency issues (audit #3, C5).
    "Cover benefits and risks (or harms) when the question asks about them "
    "— 'is it good', 'what are the drawbacks' — and demand nothing the "
    "question does not ask for: not a different unit of measure than the "
    "one it names, not a named publication series, and not a second "
    "publisher where one issuer settles the fact. A sub-topic the question "
    "did not call for widens its scope, which the plan review names as a "
    "defect.\n"
    # A lexical ban used to forbid any capitalized word or four-digit year
    # the question did not contain. Search cannot reach a named regulation,
    # standard, jurisdiction, agency, or current-year primary source without
    # those tokens, so the ban made a whole class of evidence unfindable.
    # Its replacement permits the tokens and forbids asserting them.
    "Search queries may introduce organizations, standards, laws, acronyms, "
    "jurisdictions, and years needed to find authoritative current "
    "evidence.\n"
    "Do not assert those terms as facts in the plan; use them only as "
    "search targets.\n"
    "For an unqualified broad question, state the assumed scope and create "
    "distinct sub-topics for materially different mechanisms rather than "
    "bundling them.\n"
    "Each success criterion must name the evidence type, geography, and "
    "measurement or decision needed to consider the sub-topic answered.\n"
    # Verification needs a source independent of the one that produced a
    # claim: ``fact_checker.independent_domains`` refuses to corroborate a
    # claim with a page on the claim's own publisher's domain, and a claim
    # with no independent source is recorded as ``insufficient_evidence``.
    # The demand used to be unconditional, which made every obligation need a
    # pair — including the figures one agency publishes, where no second
    # measurement exists. The run spent 139 tool calls and 26 of its 44
    # minutes on 11 of 14 claims looking for a pair that could not exist, and
    # ended with 0 of 11 targets answered (audit #3, #10). The demand now
    # belongs to the policy the evidence earns, and the policy is a field the
    # plan states per target.
    #
    # Pairing a quantity "more than one body measures" was still an invitation
    # to the wrong pair. The plan read EIA's inventory and, independently, an
    # industry tracker as two measurements of the same 2024 addition, although
    # the two publish different segments — so the target demanded a pair that
    # cannot exist, exactly as the single-issuer figures did (review rank 2).
    # Two differently scoped figures are two targets. Only a figure named as
    # one issuer's own series earns that issuer's primary-attribution floor;
    # an unattributed empirical fact may instead have independent accounts.
    "Name every target's support_policy, and let the evidence settle it. Use "
    "independent_pair only when a second, independent account can state the "
    "whole finding on the same basis — a comparison, a ranking, or a causal "
    "conclusion — and then require that pair in the success criterion: state "
    "that at least two sources from different publishers must state the "
    "finding, and say how a reader would recognise the second one. A count, "
    "total, capacity, or projection that a named body publishes — an agency's "
    "inventory, a market monitor's count, a company's filing — is that body's "
    "own figure: name the issuer in its primary_attribution target. When a "
    "second body publishes its own count of the same market, give that body "
    "its own primary_attribution target, so the report shows both figures "
    "with their scopes side by side. A generic empirical fact with no named "
    "issuer takes independent_pair only when two independent accounts can "
    "each state the same fact in full. Two bodies that publish differently "
    "scoped figures — different segments, units, or vintages — are not a "
    "pair, because differently scoped figures are different measurements: "
    "never plan one independent_pair target across two issuers' figures, and "
    "never name two bodies in one target's source dimension. Use "
    "primary_attribution when a single authoritative issuer settles the "
    "fact — its own count, rule, definition, or methodology — and then do not "
    "demand a second publisher for it: a statistic only one body publishes "
    "has no second measurer, so name that body. A fact only one source states "
    "is recorded as unverified no matter how authoritative that source is, "
    "no independent pair of one agency's figure exists, and a criterion that "
    "demands one leaves the obligation unanswered. An explicit request for "
    "independent confirmation is the one demand none of this lowers: keep "
    "such a target on independent_pair.\n"
    # A source behind a subscription cannot be read at all, and an unanswered
    # required obligation fails acceptance however honest the report is, so a
    # required target must not depend on one (review rank 4).
    "Never make a required target depend on a source behind a paywall: name a "
    "freely reachable publication of the same figures, or leave the "
    "obligation out.\n"
    "Aim the queries at primary sources — regulations, standards, filings, "
    "and datasets that state the facts directly — and say which class of "
    "source each query should reach. For a target planned under "
    "independent_pair, include a query aimed at its independent second "
    "source.\n"
    # The as-of date and the geographic scope used to be prose the model was
    # asked to write into its own queries, with no field of its own. It wrote
    # whatever year it believed was current — 2024, in a September 2026
    # session — and the stale anchor reached the reader as a hard limit
    # (baseline TR-04). Both now come from the frozen answer contract printed
    # above, and the plan may not restate them as its own assumption.
    "The answer contract above fixes the as-of date, the geographic scope, "
    "and the answer form. Do not restate them as your own assumptions and do "
    "not narrow or widen them; every sub-topic is answered within them.\n"
    "Give every sub-topic between 1 and 4 evidence_targets. Each target is one "
    "atomic obligation, written as a question whose answer is a fact or a "
    "measurement, not as an assertion the plan already believes. Never combine "
    "two measures, two rule dates, or two jurisdictions into one target, and "
    "never require two sources to agree within a numeric tolerance unless the "
    "question itself states that tolerance.\n"
    "For every target also fill the fields a program checks answers against: "
    "measure (what is measured, in words: \"battery storage power capacity "
    "added\"), unit_dimension (power for a capacity in kW, MW or GW; energy for "
    "MWh or GWh; percent for a share; empty when the answer is not a quantity), "
    "period (the year or period the answer applies to), kind "
    "(actual for a measured outcome, forecast for a projection; empty when the "
    "answer is not a quantity), geography, and organisation (the one body whose "
    "figure the target asks for, or empty when any body's figure answers it). "
    "Plan one target per organisation, measure, period and kind.\n"
    "Mark a target critical when the question cannot be answered without it, "
    "and give every target the dimensions a reader needs to judge it: the "
    "measure, the period, the geography, and the kind of source that settles "
    "it. Do not add a metadata dimension — a publication date, a retrieval "
    "date, a data period, an effective date, a generation date — unless the "
    "question itself asks for that date: metadata is context, not evidence, "
    "and a target resting on it cannot be answered.\n"
    "Make every success criterion measurable, so a reader can tell from the "
    "evidence it names whether the sub-topic was answered.\n"
    "Two sub-topics must never share a title."
)

# One example, because there is no valid "empty plan" case to show: the plan
# requirements already state the 3-7 sub-topic bound, and an empty list would
# be a different failure mode rather than the opposite end of a scale.
#
# The example is a plan the planner's own checks accept, which is the only
# property an example can teach: an earlier one modelled a "benefits and
# risks" sub-topic for a comparison question (scope widening) and a target
# asking two things at once ("Which documented risks does each option carry,
# and by which issuer?") — both of which the plan review, whose rules the
# instruction now states, names as defects. Every target here asks one
# question, and each states the support policy its evidence earns: a
# ridership figure is the operator's own count (``primary_attribution``),
# while a capital cost is estimated independently by more than one body
# (``independent_pair``, whose criterion is the one that asks for the pair).
_PLAN_REPLY_EXAMPLES = (
    (
        "Example input: compare bus and rail options for a city.",
        '{"sub_topics":['
        '{"title":"travel demand and coverage",'
        '"rationale":"Establish which trips each option must serve.",'
        '"search_queries":["city bus rail travel demand route coverage"],'
        '"success_criteria":['
        '"Measured demand and coverage estimates are available for both '
        'options."],"priority":1,'
        '"evidence_targets":['
        '{"question":"What ridership did the bus option carry in the most '
        'recent reported year?","required_dimensions":["measure: annual '
        'ridership","period: most recent reported year","geography: the '
        'city","source: the operator\'s published ridership report"],'
        '"critical":true,"support_policy":"primary_attribution",'
        '"measure":"annual ridership","unit_dimension":"",'
        '"period":"most recent reported year","kind":"actual",'
        '"geography":"the city","organisation":"the operator"},'
        '{"question":"What ridership did the rail option carry in the most '
        'recent reported year?","required_dimensions":["measure: annual '
        'ridership","period: most recent reported year","geography: the '
        'city","source: the operator\'s published ridership report"],'
        '"critical":true,"support_policy":"primary_attribution",'
        '"measure":"annual ridership","unit_dimension":"",'
        '"period":"most recent reported year","kind":"actual",'
        '"geography":"the city","organisation":"the operator"}]},'
        '{"title":"cost and delivery",'
        '"rationale":"Compare the resources and time required to deliver each '
        'option.",'
        '"search_queries":["city bus rail capital operating cost delivery '
        'time"],"success_criteria":['
        '"Comparable cost estimates are available from two independent '
        'sources."],"priority":2,'
        '"evidence_targets":['
        '{"question":"What capital cost per route kilometre does each option '
        'report?","required_dimensions":["measure: capital cost per route '
        'kilometre","period: the most recent published estimate",'
        '"geography: the city","source: the cost analysis each body '
        'publishes"],'
        '"critical":false,"support_policy":"independent_pair",'
        '"measure":"capital cost per route kilometre","unit_dimension":"",'
        '"period":"the most recent published estimate","kind":"actual",'
        '"geography":"the city","organisation":""}]},'
        '{"title":"service reliability",'
        '"rationale":"Establish how reliably each option delivers its '
        'timetable.",'
        '"search_queries":["city bus rail on-time performance reliability"],'
        '"success_criteria":['
        '"A measured on-time performance figure is available for each '
        'option."],"priority":3,'
        '"evidence_targets":['
        '{"question":"What on-time performance did each option report?",'
        '"required_dimensions":["measure: on-time performance, in percent",'
        '"period: the most recent reported year","geography: the city",'
        '"source: the operator\'s performance report"],'
        '"critical":false,"support_policy":"primary_attribution",'
        '"measure":"on-time performance","unit_dimension":"percent",'
        '"period":"the most recent reported year","kind":"actual",'
        '"geography":"the city","organisation":"the operator"}]}'
        "]}",
    ),
)


class EvidenceTargetDraft(ContractModel):
    """One model-proposed obligation, before the planner stamps it.

    No ``Field`` constraints, for the same reason ``SubTopicDraft`` has none:
    this model is converted to a strict JSON schema. The planner assigns the
    id, the support policy, and the contract's own dimensions; the model
    supplies the question the obligation answers, the dimensions a reader
    needs, and whether the question can be answered without it.
    """

    question: str
    required_dimensions: list[str]
    critical: bool
    support_policy: str = ""
    """The policy the plan proposes for this obligation, or "" to accept the
    planner's own rule.

    Deliberately a defaulted ``str`` rather than a ``Literal`` or a required
    field, for the reason ``SubTopicDraft`` declares no constraints at all:
    this model becomes a strict JSON schema, and a plan whose reply omits a
    policy is still a plan. Whether independent measurement of a fact exists
    is a fact about the evidence, not about the question's wording — a figure
    one agency publishes has no second measurer, while a comparison needs two
    independent accounts by construction — so the model names it per target,
    and ``support_policy_for_target`` validates the name and keeps every
    policy the question's own form earns.
    """
    measure: str = ""
    unit_dimension: str = ""
    period: str = ""
    kind: str = ""
    geography: str = ""
    organisation: str = ""
    """The fields a program checks an answer against; empty when the question
    does not name one."""


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
    of *additional* sub-topics and legitimately carries fewer than three;
    every other plan carries the 3-7 the instruction asks for.

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
_SupportPolicy: TypeAlias = Literal[
    "independent_pair",
    "primary_attribution",
    "derivation",
]


@dataclass(frozen=True)
class _QuestionClassification:
    """One deterministic result consumed by both Planner stamping paths.

    ``ambiguous`` preserves uncertainty inside the local classifier without
    widening the persisted Task 2 contract. Only ``explicit`` earns the
    comparison answer form and its independent-pair burden; ambiguous
    inequality language follows the ordinary answer-form and policy rules.
    """

    comparison_evidence: _ComparisonEvidence
    answer_kind: AnswerKind
    support_policy: _SupportPolicy


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


# A question that asks what caused something, or what effect something had, is
# answered by an argument rather than by one issuer's figure. The vocabulary is
# wider than ``_EXPLANATION_MARKERS``, which also decides the *answer form*: a
# causal question's policy has to be protected even where its form is still
# factual ("Does battery storage reduce wholesale electricity prices?").
_CAUSAL_MARKERS = (
    "why",
    "how does",
    "how do",
    "how did",
    "explain",
    "mechanism",
    "reason for",
    "reason that",
    "cause",
    "causes",
    "caused",
    "effect",
    "effects",
    "impact",
    "impacts",
    "affect",
    "affects",
    "reduce",
    "reduces",
    "reduced",
    "drive",
    "drives",
    "drove",
    "led to",
    "leads to",
    "lead to",
    "result of",
    "results in",
    "resulting in",
    "determine",
    "determines",
    "influence",
    "influences",
    "because",
    "due to",
)


# A question that ranks or weighs things is a comparison even where the
# syntactic detector cannot prove the relation: "Which state added the most
# battery capacity?" is a ranking, and "Is lithium-ion safer than flow
# batteries?" is a weighing. Both are answered by comparing two accounts, so
# the plan may not lower them to one issuer's figure (review F5).
_RANKING_MARKERS = (
    "best",
    "better",
    "biggest",
    "fewer",
    "greatest",
    "highest",
    "largest",
    "least",
    "lowest",
    "safer",
    "safest",
    "smallest",
    "worse",
    "worst",
)

# "most" is the one ranking word that also heads every phrase a question about
# a current figure uses. "the most recently published outlook", "the most
# recent inventory" and "the most current data" rank nothing: they name one
# body's vintage. Matching the bare word read the question under audit as a
# ranking and stamped ``independent_pair`` on all three of its forecast
# targets, whose single-agency figures no second measurement can corroborate —
# the plan override that keeps a target unanswerable (review rank 2). The word
# is therefore matched on its own, by a pattern that only fires when a
# currency word does not follow it (hyphenated or spaced): "added the most
# capacity" still ranks.
_MOST_CURRENCY_FOLLOWERS = ("current", "currently", "recent", "recently")
_MOST_RANKING_PATTERN = re.compile(
    r"(?<![a-z0-9])most(?![a-z0-9])(?![\s-]+(?:"
    + "|".join(_MOST_CURRENCY_FOLLOWERS)
    + r")(?![a-z0-9]))"
)


def _mentions_ranking(normalized: str) -> bool:
    """True when the question ranks measured quantities against each other.

    Two halves, because "most" is two words: a superlative determiner that
    ranks ("added the most capacity"), and the head of a currency phrase that
    does not ("the most recently published outlook"). The other markers rank
    in every form they take, so they are matched as tokens.
    """
    return _mentions(normalized, _RANKING_MARKERS) or (
        _MOST_RANKING_PATTERN.search(normalized) is not None
    )


# An explicit request for a second, independent account. This is the one demand
# the local rule may not lower: the user asked for a second *measurement*, and
# whether one figure happens to have a second measurer cannot answer a request
# the user made (user decision 1). Matched three ways, because the request is
# written every way round: "independently confirm X", "verify X
# independently", "an independent verification of X", "confirmed by a second
# source", and "corroborated by another publisher" all ask for the same
# account. "independent power producers" and "the independent system
# operator" name a kind of body, not a request, so those two continuations
# never count as the independence half of the demand.
_INDEPENDENCE_MARKERS = ("independent", "independently")
_INDEPENDENCE_MARKER_PATTERN = re.compile(
    r"\bindependent(?:ly)?\b(?!\s+(?:power\s+producers?|system\s+operators?))"
)
_CONFIRMATION_MARKERS = (
    "confirm",
    "confirms",
    "confirmed",
    "confirming",
    "confirmation",
    "corroborate",
    "corroborates",
    "corroborated",
    "corroborating",
    "corroboration",
    "cross-check",
    "cross check",
    "verify",
    "verifies",
    "verified",
    "verifying",
    "verification",
    "check",
    "validate",
    "validated",
)
# A second body named without the word "independent" at all: "confirmed by a
# second source" and "corroborated by another publisher" ask for the same
# second measurement the adverb otherwise signals.
_SECOND_SOURCE_PHRASES = (
    "second source",
    "another source",
    "another publisher",
    "other sources",
)


def _demands_independent_confirmation(normalized: str) -> bool:
    """True when the question asks for a second, independent account."""
    if not _mentions(normalized, _CONFIRMATION_MARKERS):
        return False
    if _INDEPENDENCE_MARKER_PATTERN.search(normalized) is not None:
        return True
    return _mentions(normalized, _SECOND_SOURCE_PHRASES)


# How a target names the body whose series its figure is: the possessive the
# plan itself writes for it, followed — within a few words — by one of the
# publication nouns such a series is published as. "the agency's published
# capacity data", "the market monitor's latest published outlook" and "the
# operator's published ridership report" are all one named body's own series,
# while "the city's population" is a property the question describes and "the
# cost analysis each body publishes" names no single body at all. A quantity
# nobody in particular reports — "how many people live in New York City?" —
# names no series, so the plan's own proposal keeps deciding it: there is no
# blanket "it has a number" rule (user decision 1).
_REPORTED_SERIES_NOUNS = (
    "accounts",
    "analysis",
    "count",
    "counts",
    "data",
    "database",
    "dataset",
    "documentation",
    "estimate",
    "estimates",
    "figures",
    "filing",
    "filings",
    "forecast",
    "forecasts",
    "inventory",
    "methodology",
    "numbers",
    "outlook",
    "outlooks",
    "projection",
    "projections",
    "publication",
    "publications",
    "record",
    "records",
    "release",
    "releases",
    "report",
    "reports",
    "series",
    "statistics",
    "study",
    "survey",
    "surveys",
)
# The pronouns whose "'s" is never a body's own series ("it's", "there's"),
# and the possessors that name a time, an indefinite person, or a collective
# concept rather than any particular body — "today's figures", "last year's
# figures", "anyone's published data", "the world's best estimates", "the
# market's latest data" all read as an attribution but name no issuer at all,
# and the same guard catches the planner's own evidence-period sentence
# ("never substitute today's figures"). The rest of the pattern is what
# decides: a possessive followed by a publication noun is an attribution, and
# these are the possessives that only look like one. An indefinite article
# ("a reputable publisher's report") is filtered separately, by the
# ``article`` group rather than the guard set: it names no one in particular
# regardless of its head noun.
_SERIES_POSSESSIVE_GUARDS = frozenset(
    {
        "it",
        "that",
        "this",
        "there",
        "these",
        "those",
        "today",
        "yesterday",
        "tomorrow",
        "year",
        "week",
        "month",
        "day",
        "anyone",
        "someone",
        "everyone",
        "world",
        "market",
    }
)
_POSSESSIVE_SERIES_PATTERN = re.compile(
    r"\b(?:(?P<article>a|an)\s+|the\s+)?"
    r"(?P<possessor>[a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,3})"
    r"(?:'s|’s)\s+(?:[a-z][a-z-]*\s+){0,4}(?:"
    + "|".join(_REPORTED_SERIES_NOUNS)
    + r")(?![a-z0-9])"
)


def _named_possessive_series(text: str) -> bool:
    """A body's own series named in possessive form, or a false one filtered.

    "a reputable publisher's report" is filtered by its indefinite article,
    and "the world's best estimates" is filtered because "world" is the
    possessor's own last word, not a body (:data:`_SERIES_POSSESSIVE_GUARDS`).
    "the federal energy statistical agency's published capacity data" keeps
    neither guard: its possessor is definite and its last word, "agency",
    names a kind of issuing body rather than a time or a generic concept.
    ``text`` must already be normalized (:func:`_normalized_question`).
    """
    for match in _POSSESSIVE_SERIES_PATTERN.finditer(text):
        if match.group("article") is not None:
            continue
        possessor_words = match.group("possessor").split()
        if possessor_words[-1] in _SERIES_POSSESSIVE_GUARDS:
            continue
        return True
    return False


# A body named by its own proper-noun title instead of a possessive: "the
# Census Bureau population estimates" and "the US Energy Storage Monitor"
# name their issuer with no "'s" at all. Case-sensitive and read from the
# *raw* text — never the casefolded form the rest of this module matches
# against — because capitalisation is the only signal here: a generic
# descriptive phrase such as "the federal energy statistical agency" is
# deliberately left to the possessive form above, not this one.
_TITLE_ENTITY_PATTERN = re.compile(
    r"\bthe\s+(?P<entity>[A-Z][\w&]*(?:\s+[A-Z][\w&]*){1,4})"
    r"(?:\s+(?P<tail>(?:[a-z][a-z-]*\s+){0,3}(?:"
    + "|".join(_REPORTED_SERIES_NOUNS)
    + r"))(?![a-zA-Z0-9]))?"
)


_GEOGRAPHIC_TITLE_HEADS = frozenset(
    {"city", "county", "kingdom", "province", "region", "republic", "state", "states", "union"}
)
_PUBLICATION_TITLE_HEADS = frozenset(
    {"bulletin", "inventory", "monitor", "outlook", "report", "survey", "tracker"}
)


def _named_title_series(raw_text: str) -> bool:
    """Recognise an issuer's title, not every capitalised geographic name.

    A publisher's proper name must modify a publication noun (``Census
    Bureau population estimates``), or itself end in a publication title
    (``US Energy Storage Monitor``). A jurisdiction such as ``United States``
    is a geographic scope, even when followed by ``figures``; it is not the
    body that published those figures.
    """
    for match in _TITLE_ENTITY_PATTERN.finditer(raw_text):
        head = match.group("entity").split()[-1].casefold()
        if head not in _GEOGRAPHIC_TITLE_HEADS and (
            match.group("tail") is not None or head in _PUBLICATION_TITLE_HEADS
        ):
            return True
    return False


def _asks_a_named_issuers_series(
    question: str,
    required_dimensions: Sequence[str],
) -> bool:
    """Whether the target asks what one named body's series reports.

    Two halves, and both are needed. The *measure* has to be one the binding
    gate checks as a numeric value (:func:`checkable_dimensions`), because a
    figure is what an issuer's series reports: an obligation about a
    classification or a rule is settled by reading the document that states
    it, not by one body's number. The *attribution* has to be in the target —
    the question or one of its own requirements has to name the body whose
    series the figure is — and it has to name it *as* that body's series,
    either in possessive form (:func:`_named_possessive_series`) or by its own
    capitalised title (:func:`_named_title_series`). The recorded 2024
    addition target names its agency only in its source requirement, which is
    why the requirements are read here and not just the sentence.

    ``question`` is read in *both* forms: casefolded for the possessive
    check, which is lexical, and raw for the title check, which needs
    capitalisation to tell a proper-noun issuer from a generic phrase.
    """
    if not any(
        checkable_dimensions(requirement) == ("value",)
        for requirement in required_dimensions
    ):
        return False
    normalized_text = " ; ".join(
        [
            _normalized_question(question),
            *(_normalized_question(value) for value in required_dimensions),
        ]
    )
    if _named_possessive_series(normalized_text):
        return True
    raw_text = " ; ".join([question, *required_dimensions])
    return _named_title_series(raw_text)


def _earned_support_policy(
    normalized: str,
    *,
    comparison_evidence: _ComparisonEvidence,
    attributed_quantity: bool = False,
    demands_independent_confirmation: bool = False,
) -> _SupportPolicy | None:
    """The policy a question's own form earns, or ``None`` when it earns none.

    Every branch here is a *reason*: a comparison — explicit or only
    ambiguous — needs two independent accounts by construction, a computed
    quantity needs its premises supported, a causal question is answered by an
    argument rather than by one issuer's figure, an explicit request for
    independent confirmation is a demand the user made, and an official rule or
    definition is settled by the body that issues it. ``None`` is not a reason:
    it is where the local rule has nothing to say, and where a target whose
    evidence has one issuer can be planned as ``primary_attribution`` instead of
    demanding a pair that cannot exist (audit #3).

    ``demands_independent_confirmation`` is computed by the caller rather than
    read from ``normalized`` alone, because the request that matters can live
    in the session's frozen *contract* question rather than the target's own
    rewritten sentence — the planner always rewrites a target into an atomic
    question, and "independently confirm X" never survives that rewrite
    verbatim (review rank 1, user decision 1).

    ``attributed_quantity`` is the one branch that reads the target's
    *requirements* rather than its sentence, and it comes last on purpose: a
    measured quantity that the target itself attributes to a named body's
    published series (:func:`_asks_a_named_issuers_series`) is that body's own
    figure. A second body's count of the same market is a differently scoped
    measurement — a different claim — and a relay or a shared-data reanalysis
    is never a second measurement, so a verified pair of one figure cannot be
    formed at all: ``independent_pair`` on it is unanswerable by construction,
    which is what made the policy on the run's critical 2024 target a
    sample-to-sample coin flip (review rank 2, user decision 1). Comparison,
    derivation, ranking, causal and an explicit confirmation request all keep
    their own policy above this branch.

    The ambiguous comparison and the causal branches keep the *floor* the
    fallback used to provide. They change nothing about ``support_policy_for``
    — which still answers ``independent_pair`` for both — but the plan's own
    proposal is what decides now, so a question whose form earns a policy has
    to earn it here or the model can lower it (review F5).
    """
    if comparison_evidence == "explicit":
        return "independent_pair"
    if demands_independent_confirmation:
        return "independent_pair"
    if _mentions(normalized, _DERIVATION_MARKERS):
        return "derivation"
    # A threshold question about a rule stays the issuing body's to answer: the
    # inequality in "tariffs greater than ten percent" is a limit the rule
    # states, not a comparison between two accounts, so this comes before the
    # weaker comparison evidence below.
    if _mentions(normalized, _CONSTRAINTS_MARKERS) or _mentions(
        normalized, _PRIMARY_ATTRIBUTION_MARKERS
    ):
        return "primary_attribution"
    if comparison_evidence == "ambiguous" or _mentions(
        normalized, _COMPARATIVE_CUES
    ) or _mentions_ranking(normalized):
        return "independent_pair"
    if _mentions(normalized, _CAUSAL_MARKERS):
        return "independent_pair"
    if attributed_quantity:
        return "primary_attribution"
    return None


def _support_policy_from(
    normalized: str,
    *,
    comparison_evidence: _ComparisonEvidence,
) -> _SupportPolicy:
    """Classify evidence policy without a clock-dependent answer kind."""
    return (
        _earned_support_policy(
            normalized,
            comparison_evidence=comparison_evidence,
            demands_independent_confirmation=_demands_independent_confirmation(
                normalized
            ),
        )
        or "independent_pair"
    )


def _question_classification(
    question: str,
    *,
    clock_year: int | None,
) -> _QuestionClassification:
    """Classify answer form and support policy from one semantic result."""
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

    support_policy = _support_policy_from(
        normalized,
        comparison_evidence=comparison_evidence,
    )

    return _QuestionClassification(
        comparison_evidence=comparison_evidence,
        answer_kind=answer_kind,
        support_policy=support_policy,
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
    """The dimension text that says what shape of answer closes a target."""
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
            "The question names no geography, so the plan assumes none: every "
            "target states the geography its evidence covers, and no regional "
            "sample may support a global conclusion.",
        ],
    )


def requested_word_limit_for(question: str) -> int | None:
    """The reader length the question explicitly asks for, or ``None``."""
    match = _WORD_LIMIT_PATTERN.search(question)
    if match is None:
        return None
    limit = int(match.group(1))
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


def latest_available_obligation(contract: AnswerContract) -> str:
    """The one dimension every target carries about the evidence period.

    It is built from the frozen contract, so it cannot become a hard-coded
    older year: with a 2026 clock it asks for the latest evidence available as
    of 2026-09-16, while a question about 2021 keeps 2021 as the period the
    answer is *about* — the two are separate obligations, and only a date the
    question states moves the first one.
    """
    return f"evidence period: {contract.evidence_period_requirement}"


def geographic_obligation(contract: AnswerContract) -> str:
    """The dimension that keeps a regional sample from supporting the world."""
    if contract.geographic_scope == "unspecified":
        return (
            "geography: unspecified by the question, so state the geography "
            "each piece of evidence covers and do not generalise beyond it"
        )
    if contract.geographic_scope == "global":
        return "geography: global, with evidence that covers more than one region"
    return f"geography: {contract.geographic_scope}"


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
    ``researcher._ordered_sub_topics`` applies downstream. Ids are stamped
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


def earned_support_policy(
    question: str,
    *,
    required_dimensions: Sequence[str] = (),
    contract_question: str = "",
) -> str | None:
    """The support policy this question's own form earns, or ``None``.

    Published because two consumers have to ask the same question, and
    ``support_policy_for`` answers a different one: it falls back to
    ``independent_pair`` wherever the form earns nothing, so a caller judging
    whether a policy was *lowered* — the evaluation metric that scores a plan's
    policies, and :func:`support_policy_for_target` itself — cannot tell "the
    question earns a pair" from "the local rule had nothing to say".

    ``required_dimensions`` is the target's own statement of what its answer
    has to be, and the floor reads it because the sentence does not always
    carry the attribution: the run's 2024 addition target names its agency only
    in its source requirement, and a caller that passed only the question would
    price the target differently from the planner that stamps it. Callers that
    have no plan to read — the legacy classifiers — keep passing the question
    alone, and get the reading they always had.

    ``contract_question`` is the session's own frozen original question, read
    only for an explicit independent-confirmation request. The planner always
    rewrites a target into an atomic sentence, so "Independently confirm how
    much battery storage the US added in 2024" survives only in the contract,
    never in the rewritten target's own question — and a caller with no
    contract to read (the legacy classifiers, and every call site before the
    plan is stamped) keeps the reading it always had.
    """
    normalized = _normalized_question(question)
    demands_confirmation = _demands_independent_confirmation(normalized) or (
        bool(contract_question)
        and _demands_independent_confirmation(
            _normalized_question(contract_question)
        )
    )
    return _earned_support_policy(
        normalized,
        comparison_evidence=_comparison_evidence_for(question),
        attributed_quantity=_asks_a_named_issuers_series(
            question, required_dimensions
        ),
        demands_independent_confirmation=demands_confirmation,
    )




def support_policy_for_target(
    *,
    question: str,
    proposed: str = "",
    required_dimensions: Sequence[str] = (),
    contract_question: str = "",
) -> str:
    """The binding support policy for one target: the proposal, under the floor.

    Section 2.1 requires the policy to be assigned before any verdict exists,
    and the planner assigns it here, so no later stage may downgrade an
    obligation to pass a coverage gate.

    Two halves, and the order matters. A question whose own *form* earns a
    policy keeps it — a comparison is never downgraded to citing one authority
    because a plan proposed to, a computed quantity stays a derivation, an
    explicit request for independent confirmation stays a pair however it is
    phrased or wherever in the contract it is written, and a measured
    quantity the target attributes to a named body's published series is that
    body's own figure rather than a pair no evidence can verify. The question
    is read together with ``required_dimensions``, because a target states its
    attribution in either place, and with ``contract_question`` because a
    request for confirmation is not guaranteed to survive the planner's own
    rewrite of the target's sentence. Where the local rule has no reason to
    give, the plan's own proposal decides, because whether independent
    measurement of a fact exists is knowledge about the evidence: the capacity
    one agency's inventory publishes has no second measurer, so demanding a
    verified pair makes the target unanswerable, while the run's plan put 10 of
    its 11 obligations on ``independent_pair`` (audit #3, P0). An unusable
    proposal keeps the independent-pair default: a generic numeric value
    cannot become a named issuer's own series merely by being numeric.
    """
    earned = earned_support_policy(
        question,
        required_dimensions=required_dimensions,
        contract_question=contract_question,
    )
    if earned is not None:
        return earned
    if proposed in get_args(_SupportPolicy):
        return proposed
    return "independent_pair"


def _structured_fields(target: EvidenceTargetDraft) -> dict[str, object]:
    """The draft's checkable fields, blank ones and unknown values stamped empty."""

    def text(value: str) -> str | None:
        return " ".join(value.split()) or None

    dimension = (text(target.unit_dimension) or "").casefold()
    kind = (text(target.kind) or "").casefold()
    return {
        "measure": text(target.measure),
        "unit_dimension": dimension if dimension in get_args(UnitDimension) else None,
        "period": text(target.period),
        "kind": kind if kind in get_args(FigureKind) else None,
        "geography": text(target.geography),
        "organisation": text(target.organisation),
    }


def _draft_targets(
    item: SubTopicDraft, coverage_id: str
) -> list[EvidenceTarget]:
    """Convert one draft's obligations into provisional ``EvidenceTarget``s.

    Provisional in exactly two ways: the ids are positional within the draft
    (``_assign_coverage_ids`` re-stamps them once the plan is ordered), and the
    support policy is the planner's own rule applied to the question (the
    binding policy is stamped by ``apply_answer_contract``). Everything else —
    the question, the model's dimensions, ``critical`` — is carried through,
    so a draft that omits a question, lists no dimensions, or proposes more
    obligations than a sub-topic may carry fails validation here and is
    reported as a repair problem.
    """
    return [
        EvidenceTarget(
            target_id=target_id_for(coverage_id, position),
            coverage_id=coverage_id,
            question=target.question,
            required_dimensions=list(target.required_dimensions),
            required=True,
            critical=target.critical,
            support_policy=support_policy_for_target(
                question=target.question,
                proposed=target.support_policy,
                required_dimensions=target.required_dimensions,
            ),
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


def support_policy_for(*, question: str) -> str:
    """The support policy this target is answered under, decided locally.

    Section 2.1 requires the policy to be assigned before any verdict exists,
    so the planner assigns it here and no later stage may downgrade it to
    pass a coverage gate. The rules, in precedence order:

    - a target that compares two things is ``independent_pair``: a comparative
      conclusion is exactly what Section 2.1 says needs independent evidence,
      and it must not be downgraded to citing one authority because the
      comparison happens to be about permits or regulations. The comparison
      test therefore comes *before* the attribution test — "is permitting
      slower in California than in Texas?" is a comparison first.
    - a target that asks for a computed quantity is ``derivation``: its
      premises must be supported and its arithmetic reproducible;
    - a constraints target, or a target about an official rule, definition,
      or measurement, is ``primary_attribution``: the issuing body's own
      instrument settles it, and requiring a second organization to
      independently model the same official date would make an official date
      unanswerable;
    - everything else — causal and empirical conclusions — is
      ``independent_pair``.
    """
    return _question_classification(
        question,
        clock_year=None,
    ).support_policy


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

# A success criterion that demands a *second publisher*. Where a single
# authoritative issuer publishes the fact, no second measurement of it exists,
# so the demand is one the evidence can never meet — the run's plan asked for
# independent corroboration of figures only EIA issues, and 11 of 14 claims
# spent their whole tool budget looking for a pair that does not exist
# (audit #3 and #10).
_CORROBORATION_MARKERS = (
    "independent",
    "corroborated",
    "corroborating",
    "corroboration",
    "second source",
    "two sources",
    "another source",
    "different publishers",
    "second publisher",
    "two publishers",
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



def _plan_answerability(
    target: EvidenceTarget, *, contract: AnswerContract
) -> list[str] | None:
    """Name dimensions that no recorded proposition field could ever fill.

    A plan has no evidence yet. Checking a fabricated claim against exact
    years, units, or publisher classes would confuse its arbitrary example
    values with structural answerability. Contract-wide answer form and
    currency obligations are presentation rules, not claim prose.
    """
    non_prose = {
        answer_form_requirement(contract.answer_kind),
        latest_available_obligation(contract),
        LEGACY_COVERAGE_DIMENSION,
    }
    unanswerable = [
        requirement
        for requirement in target.required_dimensions
        if requirement not in non_prose and not checkable_dimensions(requirement)
    ]
    return unanswerable or None


def _demands_corroboration(sub_topic: SubTopic) -> bool:
    """Whether one sub-topic's criteria demand a second publisher."""
    normalized = _normalized_question(
        " ".join(sub_topic.success_criteria)
    )
    return _mentions(normalized, _CORROBORATION_MARKERS)


# The words that turn an independence demand into a demand for a *second body*:
# "the agency's published data and, independently, a tracker" names two, while
# "an independent analysis of the market" names one. The distinction is what
# the source requirement is checked for — two bodies' figures are two
# differently scoped measurements, which is one target per issuer, not one
# target with two sources.
_SECOND_BODY_MARKERS = (
    "additional",
    "and",
    "another",
    "both",
    "plus",
    "second",
    "separate",
    "separately",
    "two",
)

# Analyst outlooks that are sold rather than published. The researcher cannot
# read a subscription page, and an unanswered *required* obligation fails
# acceptance unless the acquisition trail shows a denied URL or two empty
# searches (``quality.unaccounted_required_targets``), so a required target
# whose only named source is one of these is a plan defect the review owns
# (review rank 4).
_PAYWALLED_OUTLOOKS = ("s&p", "bloombergnef", "bloomberg nef")


def _source_requirements(target: EvidenceTarget) -> list[str]:
    """The target's own requirements about where its answer comes from."""
    return [
        requirement
        for requirement in target.required_dimensions
        if requirement.partition(":")[0].strip().casefold() == "source"
    ]


def _demanded_second_body(target: EvidenceTarget) -> str:
    """The source requirement that demands a second body, or ``""``.

    The run's plan asked one target for "the federal energy statistical
    agency's published capacity data and, independently, an industry
    energy-storage market tracker": two bodies, two scopes, and a pair that
    cannot be verified — but the target is stamped ``primary_attribution``
    under the floor, so the two demands have to become two targets.
    """
    for requirement in _source_requirements(target):
        normalized = _normalized_question(requirement)
        if _mentions(normalized, _INDEPENDENCE_MARKERS) and _mentions(
            normalized, _SECOND_BODY_MARKERS
        ):
            return requirement
    return ""


# What separates one named source from another inside a single requirement:
# "S&P Global's outlook, or the agency's published data" names two, and the
# second is one the researcher can read.
_SOURCE_CLAUSE_SPLIT = re.compile(r"\s*(?:;|,| and | or )\s*", re.IGNORECASE)


def _paywalled_only_sources(target: EvidenceTarget) -> list[str]:
    """The paywalled outlooks a target names, when they are all it names.

    Read clause by clause, not requirement by requirement: a requirement that
    names a public publication beside the sold outlook — or a second
    requirement that names one — leaves the target reachable, and the
    researcher can read that source.
    """
    names: list[str] = []
    for source in _source_requirements(target):
        for clause in _SOURCE_CLAUSE_SPLIT.split(source):
            if not clause.strip():
                continue
            found = [
                publisher
                for publisher in _PAYWALLED_OUTLOOKS
                if _mentions(_normalized_question(clause), (publisher,))
            ]
            if not found:
                return []
            names.extend(found)
    return sorted(set(names))


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
    prose — its question and its required dimensions — because either can
    carry the unit and neither is the whole obligation; the question's own
    capacity wording is the anchor, so the check never has to guess what
    family a unit-free question asks in.

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
    target_text = " ".join((target.question, *target.required_dimensions))
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
    """The benefits-and-risks content one sub-topic demands, if it demands any."""
    demanded = " ".join(
        [
            sub_topic.title,
            *sub_topic.search_queries,
            *sub_topic.success_criteria,
            *(
                text
                for target in sub_topic.evidence_targets
                for text in (
                    target.question,
                    *target.required_dimensions,
                )
            ),
        ]
    )
    normalized = _normalized_question(demanded)
    return [
        marker
        for marker in _WIDENING_MARKERS
        if _mentions(normalized, (marker,))
    ]


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

    A "period:" requirement states the data year or quarter a figure covers,
    never the edition that published it -- "period: Q4 2025" beside a
    question asking for "the fourth quarter of 2025" is one obligation
    spelled two ways, not a planner-invented vintage -- so its own text is
    never scanned for a candidate edition date. What survives that filter
    still has to sit inside an edition phrase (see _in_edition_phrase): a
    bare date elsewhere in the question or another requirement is still just
    as likely to be the data period as the release.
    """
    non_period_dimensions = [
        dimension
        for dimension in target.required_dimensions
        if "period" not in dimension.partition(":")[0].casefold()
    ]
    wording = _normalize_month_of_year(
        " ".join((target.question, *non_period_dimensions))
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
            unanswerable = _plan_answerability(target, contract=contract)
            if unanswerable is not None:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} cannot be bound to any claim: "
                        f"{'; '.join(unanswerable)} names no dimension a "
                        "clause can be credited for. Restate the obligation "
                        "in terms a claim can state",
                        "advisory",
                    )
                )
            second_body = _demanded_second_body(target)
            if second_body:
                problems.append(
                    _PlanProblem(
                        f"{target.target_id} asks one source requirement for "
                        f"a second body beside the first ({second_body}); two "
                        "bodies' figures are two differently scoped "
                        "measurements, and no pair of them can be verified. "
                        "Give each issuer its own target — one target per "
                        "issuer, each naming its own source",
                        "advisory",
                    )
                )
            unrequested_measure = _unrequested_energy_measure(
                target, contract=contract
            )
            if unrequested_measure:
                problems.append(
                    _PlanProblem(
                        (
                            f"{target.target_id} is planned optional: it "
                            "asks for an energy figure (MWh) a capacity "
                            "question never named, and no claim about "
                            "capacity can discharge it"
                        )
                        if not target.required
                        else (
                            f"{target.target_id} is a required, critical "
                            "target that asks for an energy figure (MWh) a "
                            "capacity question never named; no claim about "
                            "capacity can discharge it, and an unanswered "
                            "required obligation fails acceptance. Restate "
                            "it in the question's own measure, or "
                            "reconsider whether this obligation is truly "
                            "critical"
                        ),
                        "advisory",
                    )
                )
            paywalled = _paywalled_only_sources(target)
            if paywalled:
                problems.append(
                    _PlanProblem(
                        (
                            f"{target.target_id} is planned optional: its "
                            "only named source is behind a paywall "
                            f"({', '.join(paywalled)}), and the researcher "
                            "cannot read a subscription page. Name a "
                            "freely reachable publication of the same "
                            "figures if one exists"
                        )
                        if not target.required
                        else (
                            f"{target.target_id} is a required, critical "
                            "target whose only named source is behind a "
                            f"paywall ({', '.join(paywalled)}); the "
                            "researcher cannot read a subscription page, "
                            "and an unanswered required obligation fails "
                            "acceptance. Name a freely reachable "
                            "publication of the same figures, or "
                            "reconsider whether this obligation is truly "
                            "critical"
                        ),
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
        if _demands_corroboration(sub_topic) and any(
            target.support_policy == "primary_attribution"
            for target in sub_topic.evidence_targets
        ):
            problems.append(
                _PlanProblem(
                    f"{sub_topic.coverage_id} requires independent "
                    "corroboration in a success criterion while it plans a "
                    "target under primary_attribution; a fact one body "
                    "publishes has no second measurement, so the criterion "
                    "asks for evidence that cannot exist",
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
    1-4 obligations, that each obligation is asked as a question rather than
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


def apply_answer_contract(
    sub_topics: Sequence[SubTopic],
    contract: AnswerContract,
) -> list[SubTopic]:
    """Attach the contract's binding dimensions and support policy.

    Every target keeps the dimensions the model proposed and gains the three
    the contract fixes: the answer form, the evidence period, and the
    geography rule. ``required`` is set here rather than taken from the
    draft: an obligation the planner stamped is required by definition,
    *unless* the target's own prose asks for a measure the question never
    named (an energy figure for a capacity question,
    ``_unrequested_energy_measure``) or names only a paywalled-only source
    (``_paywalled_only_sources``) — a required, unanswerable obligation
    invented by the plan rather than asked for by the question. Both are
    stamped optional here, at the one place ``required`` is decided, rather
    than left for the §2.3 gate to discover a target nobody could ever finish
    acquiring in a one-iteration run.

    A ``critical`` target is never downgraded by either rule
    (``required = target.critical or not (...)``): the model's own judgement
    that the question cannot be answered without this obligation overrides a
    hygiene heuristic, so a critical target stays required and the §2.3 gates
    still see it as a hard obligation. The plan-problem advisory still names
    the tension for a reviewer to act on, whichever way ``required`` lands. A
    target that could be marked optional for any other reason is still a
    target that can be dropped later without anyone deciding to drop it.
    """
    form = answer_form_requirement(contract.answer_kind)
    period = latest_available_obligation(contract)
    geography = geographic_obligation(contract)

    stamped: list[SubTopic] = []
    for sub_topic in sub_topics:
        targets = [
            EvidenceTarget(
                target_id=target_id_for(sub_topic.coverage_id, position),
                coverage_id=sub_topic.coverage_id,
                question=target.question,
                required_dimensions=unique_phrases(
                    [
                        *_asked_dimensions(
                            target.required_dimensions, contract=contract
                        ),
                        form,
                        period,
                        geography,
                    ]
                ),
                required=target.critical
                or not (
                    _unrequested_energy_measure(target, contract=contract)
                    or _paywalled_only_sources(target)
                ),
                critical=target.critical,
                support_policy=support_policy_for_target(
                    question=target.question,
                    proposed=target.support_policy,
                    required_dimensions=target.required_dimensions,
                    contract_question=contract.question,
                ),
                measure=target.measure,
                unit_dimension=target.unit_dimension,
                period=target.period,
                kind=target.kind,
                geography=target.geography,
                organisation=target.organisation,
            )
            for position, target in enumerate(
                sub_topic.evidence_targets, start=1
            )
        ]
        stamped.append(
            sub_topic.model_copy(update={"evidence_targets": targets})
        )
    return stamped


def _checkable_plan_requirement(requirement: str) -> str:
    """Spell publisher unit and qualitative-definition asks in atom vocabulary."""
    head, separator, detail = requirement.partition(":")
    if not separator:
        return requirement
    if head.strip().casefold() == "unit":
        return "unit"
    if head.strip().casefold() == "definition":
        return f"measure: {detail.strip()}"
    return requirement


def _asked_dimensions(
    required_dimensions: Sequence[str],
    *,
    contract: AnswerContract,
) -> list[str]:
    """The obligations to keep, with the metadata the question never asked for gone.

    Section 2.3's metadata rule is that a publication date, data period,
    forecast horizon, effective date, retrieval date or generation date is
    context unless the question asks for it — and ``dimension_is_answered``
    enforces exactly that at binding time. A plan that stamps one anyway owes
    an obligation no claim can discharge: the run's plan asked when the
    forecast was published, and no claim could ever be bound to that target
    (audit #3, replay C10). The requirement is dropped here rather than
    enforced later, because a target that names a dimension the question never
    asked for is the same defect as demanding MWh or a second publisher.

    A requirement naming any non-metadata dimension is kept whole: dropping
    half of a compound requirement would leave an obligation nobody wrote.
    """
    kept: list[str] = []
    for requirement in required_dimensions:
        dimensions = checkable_dimensions(requirement)
        if dimensions and all(
            dimension in METADATA_DIMENSIONS
            and not metadata_dimension_asked_for(contract.question, dimension)
            for dimension in dimensions
        ):
            continue
        kept.append(_checkable_plan_requirement(requirement))
    return kept


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
    existing id is renumbered, no target is removed, no critical flag is
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


def format_plan_problems(problems: Sequence[str]) -> str:
    """Render plan problems as the corrective instruction for one repair."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return (
        "The previous plan was rejected. Fix every problem listed below and "
        f"return a corrected plan.\n{listed}"
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
        "The plan review found the plan unsound. The original question is "
        "unchanged and must not be rephrased, narrowed, or widened. Fix every "
        f"defect listed below and return a corrected plan.\n{listed}"
    )


def _planned_omission(state: ResearchState) -> str | None:
    """Every original-question omission the graph routed to this node, or None.

    The instruction to extend the plan is the typed ``extend_plan`` jobs in
    ``state.refinement_targets`` — the same objects the refinement hop's edge
    dispatched on — so the Planner never re-reads the critique's prose to learn
    why it was entered, and cannot mistake a re-plan for an extension.

    Every job's omission is named, not just the first. One extension pass is
    one request, and a request that closes only the most severe omission
    leaves the other defects exactly where they were: the same critique comes
    back, and the pass that was bought to close them closed one.
    """
    problems = list(
        dict.fromkeys(
            job.problem.strip()
            for job in state.refinement_targets
            if job.action == "extend_plan" and job.problem.strip()
        )
    )
    if not problems:
        return None
    if len(problems) == 1:
        return problems[0]
    return "\n".join(f"- {problem}" for problem in problems)


def extension_messages(
    contract: AnswerContract,
    existing: Sequence[SubTopic],
    omission: str,
) -> list[ChatMessage]:
    """Build the request for additional sub-topics closing one omission.

    The existing plan is printed with its ids and obligations so the model
    can see what is already covered; the instruction then asks for *only*
    what is missing. Nothing in this request invites a replacement plan, and
    ``extend_plan`` refuses one that arrives anyway.
    """
    sections = [
        f"# Original question (frozen)\n{contract.question}",
        f"# Answer contract\n{render_answer_contract(contract)}",
        "# Plan so far (already frozen; never restate or replace it)\n"
        f"{render_plan_for_review(existing)}",
        "# Original-question omission to close\n"
        f"{omission.strip()}",
        "# Requirements\n"
        "Return ONLY the additional sub-topics that close this omission, in "
        "the same format as the plan above. Do not repeat a sub-topic or a "
        "target that already exists; the ids you would give them belong to "
        "the planner. Every added sub-topic carries between 1 and 4 atomic "
        "evidence_targets, each written as a question. Stay inside the frozen "
        "scope and as-of date.",
        f"# Reply format\n{render_structured_reply_format(_PLAN_REPLY_EXAMPLES)}",
    ]
    return [
        ChatMessage(role="developer", content=PLANNER_PLAN_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


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
) -> list[ChatMessage]:
    """Build the messages that request one structured plan draft.

    ``contract`` is the frozen answer contract. The planner always passes
    one; a caller that omits it gets the plan requirements without a frozen
    scope, which is what a replay of an older prompt looks like.
    """
    sections = [f"# Research question\n{task.instruction}"]
    if contract is not None:
        sections.append(f"# Answer contract\n{render_answer_contract(contract)}")
    if task.guidance.strip():
        sections.append(f"# Context\n{task.guidance}")
    sections.append(f"# Scoping notes\n{_render_notes(run)}")
    sections.append(f"# Plan requirements\n{PLAN_INSTRUCTION}")
    if repair is not None:
        sections.append(f"# Repair\n{repair}")
    sections.append(
        f"# Reply format\n{render_structured_reply_format(_PLAN_REPLY_EXAMPLES)}"
    )
    return [
        ChatMessage(role="developer", content=PLANNER_PLAN_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


PLAN_REVIEW_SYSTEM_PROMPT = (
    "You are reviewing a research plan before any research starts. You have "
    "no tools and need none: the question, the frozen answer contract, and "
    "the plan are printed in the request. Judge the plan's meaning against "
    "the question, not its formatting."
)

PLAN_REVIEW_INSTRUCTION = (
    "Decide whether this plan, as written, would answer the original "
    "question. Report `sound: true` only when all of the following hold.\n"
    "- Every required dimension of the original question is covered by some "
    "sub-topic; a plan that looks diverse but omits a dimension the question "
    "asks for is not sound. Name each missing dimension you find.\n"
    "- Every evidence target is atomic: one measure, one rule date, one "
    "jurisdiction. A target that requires two measures, two rule dates, or "
    "two jurisdictions to be settled is compound even when it reads as one "
    "sentence. Name each compound target.\n"
    "- No search query or success criterion assumes the answer, states a "
    "conclusion the plan has not established, or treats a recalled memory as "
    "evidence. Name each such premise.\n"
    "- The plan stays inside the frozen scope and as-of date. Name any target "
    "that widens the scope or re-anchors the period.\n"
    "- The batch is feasible: each sub-topic can be answered by a bounded "
    "number of reads, and no sub-topic carries more than four obligations.\n"
    "`repair_instruction` is the single correction that would make the plan "
    "sound; leave it empty when sound is true. Name dimensions, never "
    "rephrase the original question."
)


def render_plan_for_review(sub_topics: Sequence[SubTopic]) -> str:
    """Print a plan as the review request sees it, ids and all."""
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
            criticality = "critical" if target.critical else "supporting"
            lines.append(
                f"  {target.target_id} [{criticality}, "
                f"{target.support_policy}]: {target.question}"
            )
            lines.append(
                f"    dimensions: {'; '.join(target.required_dimensions)}"
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
    """Report the finished plan's size and how the scoping loop stopped."""
    plan = outcome.result
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
        message="Planning complete.",
        metadata={
            "sub_topic_count": 0 if plan is None else len(plan.sub_topics),
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
    unexecutable (a sub-topic carrying outside 1-4 evidence targets, and a
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


class PlannerAgent(BaseAgent[ResearchPlan]):
    """Convert ``original_question`` into 3-7 distinct, prioritized sub-topics.

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

        A third fact decides which of the two jobs this run has. When the
        graph's refinement hop routed an ``extend_plan`` job here — the typed
        expression of an original-question omission — the run *extends* the
        plan already in state and skips the scoping loop entirely. Anything
        else is a plan: a first plan, or a re-plan of a session that has none.
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
        omission = _planned_omission(state)
        if (
            omission is not None
            and state.sub_topics
            and state.answer_contract is not None
        ):
            return await self._extension_run(
                state, omission=omission, events=events
            )
        try:
            outcome = await super().run(state)
        except ProviderError as error:
            raise planning_provider_error("react_decision") from error
        finally:
            self._restricted_toolset = None
        events.append(planning_completed_event(outcome))
        return AgentRun(
            agent_name=outcome.agent_name,
            result=outcome.result,
            react=outcome.react,
            errors=outcome.errors,
            state_update={**outcome.state_update, "events": events},
            call_fingerprints=dict(outcome.call_fingerprints),
        )

    async def _extension_run(
        self,
        state: ResearchState,
        *,
        omission: str,
        events: list[ResearchEvent],
    ) -> AgentRun[ResearchPlan]:
        """Answer one reviewed omission with added sub-topics, and only.

        The scoping loop is skipped on purpose: this session is already
        scoped, the omission is already named by the Critic, and the one call
        that can close it is a structured extension request. Nothing is
        re-planned, so no existing id, priority, obligation, or the frozen
        contract can move (Section 2.3) — ``extend_plan`` itself refuses a
        capacity conflict rather than dropping a difficult topic.

        A failed extension is *recorded*, never raised. This node runs mid-loop,
        after a report already exists, so letting the ``PlanningError`` escape
        would halt the run as ``failed`` and discard a publishable artifact —
        while every other agent treats the same provider outage as a recoverable
        fact about the pass. ``state_update(None, react)`` surfaces the recorded
        error, and an ``error_type`` naming the provider is what the graph's
        ``provider_failure`` stop reason reads.
        """
        async with self.tracker.agent_span(self.name) as span:
            errors: list[ResearchError] = []
            plan: ResearchPlan | None
            try:
                plan = await self.extend_plan(state, omission=omission)
            except PlanningError as error:
                plan = None
                errors.append(
                    agent_error(
                        agent_name=self.name,
                        error_type=(
                            "planner_extension_provider_error"
                            if isinstance(error.__cause__, ProviderError)
                            else "planner_extension_failed"
                        ),
                        message=(
                            "The plan extension for a reviewed omission could "
                            "not be completed; the plan already in state stands "
                            "and the report is unaffected."
                        ),
                        recoverable=False,
                        details={
                            "exception_type": type(error).__name__,
                            "problems": list(error.problems),
                        },
                    )
                )
            react = ReActRun(
                agent_name=self.name,
                stop_reason="provider_error" if errors else "finished",
                errors=errors,
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "stop_reason": react.stop_reason,
                    "iterations": react.iterations,
                    "tool_calls": react.tool_calls,
                    "produced_result": plan is not None,
                    "call_fingerprints": dict(self._call_fingerprints),
                }
            )
        outcome: AgentRun[ResearchPlan] = AgentRun(
            agent_name=self.name,
            result=plan,
            react=react,
            errors=list(errors),
            state_update=self.state_update(plan, react),
            call_fingerprints=dict(self._call_fingerprints),
        )
        return AgentRun(
            agent_name=outcome.agent_name,
            result=outcome.result,
            react=outcome.react,
            errors=outcome.errors,
            state_update={
                **outcome.state_update,
                "events": [*events, planning_completed_event(outcome)],
            },
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
    ) -> _PlanAttempt:
        try:
            self.fingerprint_call(
                ResearchPlanDraft.__name__,
                output_limit=self.config.planner_final_max_tokens,
            )
            draft = await self.provider.complete_structured(
                plan_messages(task, run, contract=contract, repair=repair),
                ResearchPlanDraft,
                agent_name=self.name,
                max_tokens=self.config.planner_final_max_tokens,
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
            self.fingerprint_call(
                PlanReviewDraft.__name__,
                output_limit=self.config.planner_final_max_tokens,
            )
            return await self.provider.complete_structured(
                plan_review_messages(
                    contract, sub_topics, repair=already_requested
                ),
                PlanReviewDraft,
                agent_name=self.name,
                max_tokens=self.config.planner_final_max_tokens,
            )
        except StructuredOutputError as error:
            raise planning_provider_error(
                "plan_review", problems=structured_output_problems(error)
            ) from error
        except ProviderError as error:
            raise planning_provider_error("plan_review") from error

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
                    repair=format_plan_problems(attempt.problems),
                    plan=_PLAN_REPAIR_LABEL,
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
        self._record_defects(
            run,
            stage="plan_checks",
            plan=attempt.plan,
            problems=attempt.labelled_advisory,
        )

        review = await self._review_plan(contract, attempt.sub_topics)
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
                contract, attempt.sub_topics, already_requested=requested
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

        An extension reports only what it added: its sub-topics append to the
        plan already in state, and its targets belong to the *expanded*
        inventory, so neither the frozen contract nor the initial inventory is
        rewritten by a later pass.

        A non-extension pass on a session that already carries a plan reports
        only the sub-topics whose coverage ids the session does not have —
        which for a re-plan of the same question is none of them. That is what
        keeps the live topic list and the frozen inventory in agreement:
        ``sub_topics`` appends, so re-emitting ``topic-01`` beside the existing
        ``topic-01`` would put two different topics under one id while
        ``initial_target_ids`` — union-protected — kept counting both. The plan
        already reviewed stands, and a pass cannot replace or weaken it
        (Section 2.3); only genuinely new ids cross this boundary.

        The contract is stamped only when the session has none. A later
        non-extension plan therefore cannot re-anchor a session's as-of date
        or scope: the state keeps the contract it froze, and the pass's own
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
        if result.extension:
            update["sub_topics"] = list(added)
            update["expanded_target_ids"] = target_ids
            return update
        if self._frozen_contract is None and result.answer_contract is not None:
            update["answer_contract"] = result.answer_contract
        if added:
            update["sub_topics"] = list(added)
            update["initial_target_ids"] = target_ids
        return update

    async def extend_plan(
        self,
        state: ResearchState,
        *,
        omission: str,
    ) -> ResearchPlan:
        """Add sub-topics for one reviewed omission of the original question.

        Returns a plan carrying **only** the additional sub-topics, with ids
        continuing after the plan already in state. The frozen contract, the
        existing ids, and every existing obligation are untouched: this call
        can add a target and can never remove or weaken one. A capacity
        conflict is raised as a ``PlanningError`` naming what cannot fit,
        which is not permission to drop a difficult topic.
        """
        if state.answer_contract is None:
            raise PlanningError(
                "This session has no frozen answer contract, so no reviewed "
                "omission can be added to it: plan the session first.",
                problems=["answer_contract is missing"],
            )
        contract = state.answer_contract
        messages = extension_messages(contract, state.sub_topics, omission)
        try:
            self.fingerprint_call(
                PlanExtensionDraft.__name__,
                output_limit=self.config.planner_final_max_tokens,
            )
            draft = await self.provider.complete_structured(
                messages,
                PlanExtensionDraft,
                agent_name=self.name,
                max_tokens=self.config.planner_final_max_tokens,
            )
        except StructuredOutputError as error:
            raise planning_provider_error(
                "extend_plan", problems=structured_output_problems(error)
            ) from error
        except ProviderError as error:
            raise planning_provider_error("extend_plan") from error

        additions, problems = extend_plan(
            state.sub_topics, draft, contract=contract
        )
        if problems:
            raise PlanningError(
                "The planner could not add the reviewed omission to the plan.",
                problems=problems,
            )
        return ResearchPlan(
            sub_topics=additions,
            answer_contract=contract,
            extension=True,
        )
