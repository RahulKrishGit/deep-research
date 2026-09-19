"""The Planner: turn one research question into a validated research plan.

The provider is asked for ``ResearchPlanDraft``, not for a plan of real
``SubTopic`` values. ``SubTopic`` declares ``Field(min_length=1)`` on its
string and list fields, which Pydantic renders as ``minLength``/``minItems``
— keywords outside OpenAI's strict structured-output subset. The draft
models carry no constraints at all, and ``validate_plan_draft`` applies the
domain rules locally where their failures can be turned into a repair prompt.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

from pydantic import Field, ValidationError, field_validator, model_validator

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    PlanningError,
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
    MemorySnapshot,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    SubTopic,
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
        "answer form: the same measured dimension for every option compared, "
        "on one shared basis and unit"
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
    "When the question concerns a technology or intervention, ensure the "
    "plan explicitly covers both benefits and risks (or harms) in the "
    "subtopic titles or search queries.\n"
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
    # Verification needs a source independent of the one that produced a claim:
    # ``fact_checker.independent_domains`` refuses to corroborate a claim with a
    # page on the claim's own publisher's domain, and a claim with no independent
    # source is recorded as ``insufficient_evidence``. Measured twice: the
    # pipeline now reads plenty — 64 document reads and 11 scored sources in one
    # run — and still produced ZERO claims with a second independent publisher,
    # because a sub-topic is finished as soon as a single source answers it. The
    # earlier wording asked for corroboration *beside* the criteria, which was
    # advice the Researcher could satisfy and then stop anyway. The demand now
    # belongs to the criterion it stops on.
    "Every success criterion must require independent corroboration as part of "
    "what settles the sub-topic: state that at least two sources from different "
    "publishers must state each load-bearing number or finding, and say how a "
    "reader would recognise the second one. A fact only one source states is "
    "recorded as unverified no matter how authoritative that source is, so a "
    "sub-topic is not answered while a single publisher states it.\n"
    "Aim the queries at primary sources — regulations, standards, filings, "
    "and datasets that state the facts directly — and say which class of "
    "source each query should reach. Include, for each sub-topic, a query "
    "aimed at an independent second source for its key facts.\n"
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
    "Mark a target critical when the question cannot be answered without it, "
    "and give every target the dimensions a reader needs to judge it: the "
    "measure, the period, the geography, and the kind of source that settles "
    "it.\n"
    "Make every success criterion measurable, so a reader can tell from the "
    "evidence it names whether the sub-topic was answered.\n"
    "Two sub-topics must never share a title."
)

# One example, because there is no valid "empty plan" case to show: the plan
# requirements already state the 3-7 sub-topic bound, and an empty list would
# be a different failure mode rather than the opposite end of a scale.
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
        'city"],"critical":true},'
        '{"question":"What ridership did the rail option carry in the same '
        'reported year?","required_dimensions":["measure: annual ridership",'
        '"period: the same reported year as the bus figure","geography: the '
        'city"],"critical":true}]},'
        '{"title":"cost and delivery",'
        '"rationale":"Compare the resources and time required to deliver each '
        'option.",'
        '"search_queries":["city bus rail capital operating cost delivery '
        'time"],"success_criteria":['
        '"Comparable cost and delivery estimates are available."],"priority":2,'
        '"evidence_targets":['
        '{"question":"What capital cost per route kilometre does each option '
        'report?","required_dimensions":["measure: capital cost per route '
        'kilometre","period: the most recent published estimate"],'
        '"critical":false}]},'
        '{"title":"benefits and risks",'
        '"rationale":"Identify the main outcomes and failure modes for each '
        'option.",'
        '"search_queries":["city bus rail benefits risks evidence"],'
        '"success_criteria":['
        '"Measured benefits and documented risks are available for both '
        'options."],"priority":3,'
        '"evidence_targets":['
        '{"question":"Which documented risks does each option carry, and by '
        'which issuer?","required_dimensions":["measure: documented risk",'
        '"source: the issuing authority","geography: the city"],'
        '"critical":false}]}'
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


def _support_policy_from(
    normalized: str,
    *,
    comparison_evidence: _ComparisonEvidence,
) -> _SupportPolicy:
    """Classify evidence policy without a clock-dependent answer kind."""
    if comparison_evidence == "explicit":
        return "independent_pair"
    if _mentions(normalized, _DERIVATION_MARKERS):
        return "derivation"
    if _mentions(normalized, _CONSTRAINTS_MARKERS) or _mentions(
        normalized, _PRIMARY_ATTRIBUTION_MARKERS
    ):
        return "primary_attribution"
    return "independent_pair"


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
            f"the period the question names ({stated}); answer it as of "
            f"{as_of_date} and never substitute today's figures"
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


def derive_answer_contract(
    *,
    question: str,
    now: datetime,
    requested_word_limit: int | None = None,
) -> AnswerContract:
    """Freeze the question, its scope, its as-of date, and its answer form.

    ``as_of_date`` comes from ``now`` — the run's injected clock — and never
    from model knowledge or memory. The one exception is a period the question
    itself supplies and the clock has already passed: a question about 2021 is
    answered as of 2021, and the contract says so rather than silently
    re-anchoring it to today. Years later than the clock's are forecast
    horizons, not as-of dates, so a 2035 projection cannot become today's cost.

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

    if historical_iso:
        as_of_date = max(historical_iso)
    elif past_years:
        # Capped at the clock as well as at the year's end: a question about
        # the current year cannot reach this branch, and if a future one ever
        # did, the cap keeps the contract's date from moving past today.
        as_of_date = min(f"{max(past_years)}-12-31", clock_date)
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
    older year: with a 2026 clock it asks for the latest available evidence
    as of 2026-09-16, and a question about 2021 asks for 2021.
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


def _draft_targets(
    item: SubTopicDraft, coverage_id: str
) -> list[EvidenceTarget]:
    """Convert one draft's obligations into provisional ``EvidenceTarget``s.

    Provisional in exactly two ways: the ids are positional within the draft
    (``_assign_coverage_ids`` re-stamps them once the plan is ordered), and
    the support policy is the planner's own rule applied to the question (the
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
            support_policy=support_policy_for(question=target.question),
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


def stale_year_anchors(text: str, *, as_of_year: int) -> list[int]:
    """Years in ``text`` that anchor currency earlier than the as-of year.

    Only a *currency* frame is reported — "current as of 2024", "the latest
    2024 figures" — because a question or criterion may legitimately name an
    older year as its subject. What this must never do is let a plan treat an
    earlier year as today: that is the TR-04 defect. The check is deliberately
    conservative in the other direction too; the plan review call is what
    judges meaning, and this only refuses an explicitly stale anchor.
    """
    normalized = _normalized_question(text)
    if not _mentions(normalized, _CURRENCY_MARKERS):
        return []
    return sorted(
        {
            int(match)
            for match in _YEAR_PATTERN.findall(text)
            if int(match) < as_of_year
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
    left behind, and that nothing invents a numeric agreement tolerance.
    """
    problems: list[str] = []
    as_of_year = int(contract.as_of_date[:4])
    for sub_topic in sub_topics:
        count = len(sub_topic.evidence_targets)
        if count < MIN_TARGETS_PER_TOPIC or count > MAX_TARGETS_PER_TOPIC:
            problems.append(
                f"{sub_topic.coverage_id} proposes {count} evidence targets; "
                f"every sub-topic carries between {MIN_TARGETS_PER_TOPIC} and "
                f"{MAX_TARGETS_PER_TOPIC}"
            )
            continue
        for target in sub_topic.evidence_targets:
            if not target.question.rstrip().endswith("?"):
                problems.append(
                    f"{target.target_id} is written as an assertion; request "
                    "the unknown as a question instead"
                )
            stale = stale_year_anchors(
                target.question, as_of_year=as_of_year
            )
            if stale:
                problems.append(
                    f"{target.target_id} anchors currency to "
                    f"{', '.join(str(year) for year in stale)} for a session "
                    f"as of {contract.as_of_date}; ask for the latest "
                    "available evidence instead"
                )
            invented = invented_tolerances(
                target.question, question=contract.question
            )
            if invented:
                problems.append(
                    f"{target.target_id} requires a numeric agreement "
                    f"tolerance ({', '.join(invented)}) that the question does "
                    "not state and no measurement basis establishes"
                )
        for criterion in sub_topic.success_criteria:
            stale = stale_year_anchors(criterion, as_of_year=as_of_year)
            if stale:
                # The measured defect lived here: a criterion that required
                # "current" numbers pinned to 2024. A criterion that is *about*
                # an older year is untouched — only a currency frame is read.
                problems.append(
                    f"{sub_topic.coverage_id} anchors currency to "
                    f"{', '.join(str(year) for year in stale)} in a success "
                    f"criterion for a session as of {contract.as_of_date}; ask "
                    "for the latest available evidence instead"
                )
            invented = invented_tolerances(
                criterion, question=contract.question
            )
            if invented:
                problems.append(
                    f"{sub_topic.coverage_id} sets a numeric agreement "
                    f"tolerance ({', '.join(invented)}) in a success "
                    "criterion that the question does not state and no "
                    "measurement basis establishes"
                )
    return problems


def apply_answer_contract(
    sub_topics: Sequence[SubTopic],
    contract: AnswerContract,
) -> list[SubTopic]:
    """Attach the contract's binding dimensions and support policy.

    Every target keeps the dimensions the model proposed and gains the three
    the contract fixes: the answer form, the evidence period, and the
    geography rule. ``required`` is set here rather than taken from the
    draft: an obligation the planner stamped is required by definition, and a
    target that could be marked optional is a target that can be dropped
    later without anyone deciding to drop it. The model's ``critical`` flag
    is preserved — a critical obligation is one the question cannot be
    answered without.
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
                    [*target.required_dimensions, form, period, geography]
                ),
                required=True,
                critical=target.critical,
                support_policy=support_policy_for(question=target.question),
            )
            for position, target in enumerate(
                sub_topic.evidence_targets, start=1
            )
        ]
        stamped.append(
            sub_topic.model_copy(update={"evidence_targets": targets})
        )
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
        repair: str | None = None,
    ) -> tuple[list[SubTopic], list[str]]:
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
        return self._stamp(draft, contract)

    def _stamp(
        self,
        draft: ResearchPlanDraft,
        contract: AnswerContract,
    ) -> tuple[list[SubTopic], list[str]]:
        """Validate a draft, then attach the contract's binding obligations."""
        sub_topics, problems = validate_plan_draft(draft)
        if problems:
            return sub_topics, problems
        stamped = apply_answer_contract(sub_topics, contract)
        return stamped, target_problems(stamped, contract)

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

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> ResearchPlan | None:
        """Request a plan, repair and review it at most once each, or fail.

        The bounds are explicit because every unstated retry is a place a bad
        plan can quietly become an accepted one: one structural repair, one
        semantic review, at most one repair of what the review named, and one
        confirming review. A plan that is still unsound after that is
        reported with the reviewer's own defect list rather than accepted.
        """
        if not run.succeeded:
            raise planning_provider_error("react_loop")

        contract = self.answer_contract_for(task.instruction)
        sub_topics, problems = await self._request_plan(
            task, run, contract=contract
        )
        repaired = False
        if problems:
            sub_topics, problems = await self._request_plan(
                task,
                run,
                contract=contract,
                repair=format_plan_problems(problems),
            )
            repaired = True
            if problems:
                raise PlanningError(
                    "The planner could not produce a valid research plan after "
                    "one repair attempt.",
                    problems=problems,
                )

        review = await self._review_plan(contract, sub_topics)
        if review.sound:
            return ResearchPlan(
                sub_topics=sub_topics,
                repair_attempted=repaired,
                answer_contract=contract,
            )

        requested = format_review_problems(review)
        sub_topics, problems = await self._request_plan(
            task, run, contract=contract, repair=requested
        )
        repaired = True
        if problems:
            raise PlanningError(
                "The planner could not produce a valid research plan after "
                "one repair of the plan review's findings.",
                problems=[*requested_problems(review), *problems],
            )
        confirming = await self._review_plan(
            contract, sub_topics, already_requested=requested
        )
        if not confirming.sound:
            raise PlanningError(
                "The planner could not produce a sound research plan: the "
                "plan review still reports semantic defects after one repair.",
                problems=requested_problems(confirming),
            )
        return ResearchPlan(
            sub_topics=sub_topics,
            repair_attempted=repaired,
            answer_contract=contract,
        )

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
