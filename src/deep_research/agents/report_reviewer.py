"""The terminal semantic report review: one call, the report judged against its findings.

Task 10 replaced a structural proxy — a keyword-and-length formula over counts
— with a review that reads the report and the evidence behind it. Step 4
(Task 4.2, spec §6.2-§6.3) makes it the one judgement the graph runs: the fact
checker, the claim clusters and the Critic left with step 4 (D2, D6, PD-16), so
this review judges the *report* against the question and the verified findings
behind each sentence, and nothing else asks a second reviewer the same question.

Four properties make this review different from the proxy in kind rather than
in degree:

* **It sees the whole report.** ``reader_content`` is the candidate verbatim,
  and no section, statement, or finding snippet is prefix-clipped.
* **It judges against the findings, not against itself.** The packet carries
  the reader report, every reader statement with the code-built label the
  reader sees at its end, the labels, hosts and snippets of the findings each
  statement cites, the key facts, the obligations Not found could not answer,
  and the deterministic gate results. It carries no score from another
  reviewer, no threshold, and no suggested verdict: a reviewer told what the
  acceptance bar is would be answering a different question.
* **One request carries the whole judgement.** The reply holds the seven
  dimensions, a disposition for every statement, and the typed defects. A
  statement the reply leaves without a disposition is recorded
  ``not_reviewed`` and the review is ``incomplete``: reading a statement is not
  judging it, and a partial review may never read as a scored one.
* **No judgement can read as an acceptance.** A review that could not be made
  is ``incomplete`` or ``provider_failed`` with no dimensions at all, and a
  review whose dispositions leave a statement unsupported derives the material
  defect that blocks acceptance, so one narrow finding cannot be recorded as
  an observation while the report still passes. Acceptance itself is
  ``semantic_review_passes``: the seven dimensions, a mean at or above the
  threshold, and no unresolved critical or major defect.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, ClassVar, cast

from pydantic import BaseModel, Field, ValidationError

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_EFFORT,
    OUTPUT_LIMIT_RETRY_OUTCOMES,
    OUTPUT_LIMIT_RETRY_READINGS,
    StructuredCompleter,
    call_configuration_fingerprint,
)
from deep_research.agents.errors import (
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.prompts import (
    render_structured_reply_format,
    render_structured_request,
)
from deep_research.agents.report import (
    ReportComposition,
    # The label builders this review must never re-derive (R1): ``_point_labels``
    # is the list a rendered statement ends with, ``_row_label`` is one key
    # facts row's label, ``_figure_label_for`` is one kept figure's label from
    # the finding that states it (all three over ``figure_label``), and
    # ``_finding_registry_pairs`` pairs each finding with its *own* registered
    # label — the one walk that survives two revision editions sharing a
    # fingerprint (P2).
    _figure_label_for,
    _finding_registry_pairs,
    _point_labels,
    _row_label,
)
from deep_research.agents.report_writer import (
    # The other half of R1: one label string per cited finding.
    _finding_label,
)
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
)
from deep_research.providers.validation import validation_diagnostic
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    GAP_KINDS,
    GAP_MATERIAL_SEVERITIES,
    GAP_SEVERITIES,
    QUESTION_TARGET_ID,
    REVIEW_DIMENSIONS,
    REVIEW_RUBRIC_VERSION,
    SEMANTIC_REVIEW_MEAN,
    UNSETTLED_STATEMENT_DISPOSITIONS,
    UNREVIEWED_STATEMENT_DISPOSITION,
    AnswerContract,
    ContractModel,
    FactRow,
    Finding,
    GapKind,
    GapSeverity,
    ReportPoint,
    ReportSection,
    ReportStatement,
    TableCell,
    ReportReview,
    ResearchError,
    ResearchState,
    ReviewDefect,
    StatementReviewDisposition,
    UnitScore,
)

REPORT_REVIEWER_ROLE = "report_reviewer"
"""The service role this review resolves its configuration under.

An extra, independently configured call role rather than an agent: it has no
tool path, no ReAct loop, and no slot in ``ResearchAgents``' agent list.
Preflight validates it exactly like an agent, so a misconfigured reviewer
fails the run before any collaborator exists.
"""

REPORT_REVIEW_PROMPT_VERSION = "report-review-5"
"""The prompt and reply contract this review's requests are versioned under.

Version 2 was the whole-report Critic-era packet, which carried checked claims,
corroboration badges, evidence batches and coverage targets. Version 3 is the
step-4 review: the statements with their code-built labels and the findings
behind them, one request, dispositions in the three-valued step-4 vocabulary.
Version 4 is D10's: the same packet in the static-first layout, with the shared
reply format and one example, and the rule for sentences that end with no label.
Version 5 (spec §11.1) is the consumer report format's: ``# Key facts`` becomes
``# Verified figures``, ``# Not found`` becomes ``# What the report could not
confirm``, a new ``# Table`` block carries the question-shaped table's own
backing statement and fact-row ids, each finding block gains a ``status:``
line, and the system prompt no longer tells the reviewer a sentence carries a
label *beside* it in the report -- labels are code-built material this packet
shows, never printed prose (spec §3.1 rule 8).
The version is what keeps a stored judgement of the older packet from being
read as a judgement of this one.
"""


REPORT_REVIEW_OPERATION = "report_review"
"""The operation label this reviewer's records carry.

One role makes one kind of request — the review and the single re-ask a
truncation buys — so one label names them all, and a reader grouping the run's
warnings by operation sees this reviewer's records together.
"""


def report_review_output_limit_retry(
    error: Exception,
    *,
    schema: str,
    reasoning_effort: str,
    max_tokens: int,
    outcome: str,
) -> ResearchError:
    """Record that a truncated review request was re-asked at another effort.

    One retry, the same output budget, and no run ending. The record exists
    because the retry is a second paid call — without it, a review that took
    two requests is indistinguishable in the artifacts from one that took a
    single request.

    The request, the effort, the budget it kept, and what came back are in the
    *message*, not only in ``details``: this project publishes details only for
    the error types whose projection it has vetted, and an effort a reader
    cannot see is a retry a reader cannot find.
    """
    if outcome not in OUTPUT_LIMIT_RETRY_OUTCOMES:
        raise ValueError(f"unknown retry outcome: {outcome!r}")
    return agent_error(
        agent_name=REPORT_REVIEWER_ROLE,
        error_type="report_review_output_limit_retry",
        message=(
            f"The {schema} review request was truncated by the output limit; "
            f"it was re-asked once at reasoning_effort {reasoning_effort} with "
            f"the same {max_tokens}-token output budget, and "
            f"{OUTPUT_LIMIT_RETRY_READINGS[outcome]}"
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            REPORT_REVIEW_OPERATION,
            error,
            attempt=2,
            schema=schema,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            outcome=outcome,
        ),
    )


MAX_REVIEW_DEFECTS = 12
"""The smallest defect bound any review is given, however small its packet.

The bound itself is :func:`review_defect_limit`: a fixed twelve contradicted
the instruction to report every defect, since a report with more than twelve
unsupported statements has more than twelve true findings.
"""


def review_defect_limit(packet: ReportReviewInput) -> int:
    """How many distinct defects one review of ``packet`` may carry.

    One per address a defect can be scoped to — every statement the packet
    carries and every target its statements answer — and never fewer than
    ``MAX_REVIEW_DEFECTS``, so a small packet can still hold several kinds of
    defect against one statement. The request states this number and the review
    enforces it: a reply beyond it is refused whole, never cut, because the
    tail of a padded list is where one material defect can hide.
    """
    return max(
        MAX_REVIEW_DEFECTS,
        len(packet.statements) + len(packet.known_target_ids),
    )


# The dimensions, in a stable order, with the semantic definition of each. The
# names are the whole-report campaign's own seven; the definitions are what
# Task 10 changed from a formula to a judgement.
DIMENSION_GUIDANCE: tuple[tuple[str, str], ...] = (
    (
        "completeness",
        "Does the report answer the original question, as the answer contract "
        "frames it, including every part the question actually asked for — each "
        "part in the form its evidence takes: a figure, items with their "
        "attributes, dated events, reasons, a rule's provisions?",
    ),
    (
        "prioritization",
        "Are the report's ordering and emphasis justified by the evidence — a "
        "basis the question or the sources give: a comparison basis, a price, a "
        "date, a size — rather than by how much was written about something, or "
        "by a model's stated confidence?",
    ),
    (
        "evidence_quality",
        "Is each substantive assertion carried by the findings cited for it: "
        "the right kind of source for the claim, and evidence that entails "
        "what the sentence says, at the scope and period it says it?",
    ),
    (
        "attribution",
        "Does every claim's provenance read as what it is — the publisher's "
        "own figure, a relay credited to its originator, or an unattributed "
        "page — with no relay presented as the organisation it relays?",
    ),
    (
        "uncertainty",
        "Is the report's uncertainty calibrated and useful: real limits and "
        "contradictions disclosed, and no limitation invented that the "
        "evidence does not record?",
    ),
    (
        "readability",
        "Is the report coherent and economical, without repetition that hides "
        "a contradiction or padding that displaces the answer?",
    ),
    (
        "actionability",
        "Is the report useful for the task that was actually asked? A factual "
        "question is answered by the fact; obligations to recommend or to "
        "instruct belong here only when the question asked for them.",
    ),
)

REPORT_REVIEW_SYSTEM_PROMPT = (
    "You are the terminal reviewer of a finished research report. You judge "
    "the report a reader will receive against the findings this run verified. "
    "You have no tools: everything you may rely on is in this request, and a "
    "fact that is not in the findings you were shown is not established by "
    "anything you know.\n"
    "\n"
    "Judge substance, not presentation. A fluent report that asserts things "
    "its cited findings do not state is a failed report however well it reads, "
    "and a plain report that answers the question on adequate findings is a "
    "good one whether or not it tells the reader what to do.\n"
    "\n"
    "For every statement id you were shown, record exactly one disposition: "
    "supported when the findings it cites state what the sentence says, "
    "unsupported when they do not. A statement with no disposition is recorded "
    "not_reviewed and the review is incomplete rather than a result, so leave "
    "no statement id out.\n"
    "\n"
    "Judge each statement against the findings it cites — each statement "
    "names them by their registry labels, whose snippets and figure labels "
    "are below — and against the verified labels of the figures it states. "
    "Those labels are built by code from the verified figure, not by the "
    "writer, and are not printed beside the sentence: unless a findings "
    "table prints them, the sentence itself is the only place the reader "
    "learns who issued a figure and whether it is a forecast; the sources "
    "list names only the page's publisher. A relay must read as "
    "relayed from the organisation its verified figure names, an actual must "
    "read as an actual, a forecast must carry its issuer and its release, and "
    "no period, scope, kind or organisation in the prose may contradict a "
    "figure it states. That mismatch is a defect you record against that "
    "statement's id. Judge a statement that states no figure, or one in a "
    "unit this report does not label (a price, a count, a rating), or one no "
    "cited finding carries, against the snippets of the findings it cites "
    "and against their figure labels, and record as unsupported one that "
    "asserts more than those findings state; a pick, ranking or verdict "
    "stated as fact, rather than as the judgement of the source that made "
    "it, asserts more. A sentence that credits a body its cited findings do "
    "not name — \"according to X\" beside findings that attribute the "
    "figure to no one or to another organisation — is unsupported. The "
    "table is assembled by code from the statements' option marks and the "
    "verified figures: a cell crediting a source with a verdict or pick its "
    "backing statement does not carry is a defect against that statement's "
    "id.\n"
    "\n"
    "Report every defect you find as a typed defect against the ids in this "
    "request, and only against ids in this request. When nothing is wrong, "
    "return no defects — but a report you could not verify is not a clean "
    "report, so record what you could not verify as a disposition rather than "
    "as silence."
)

REPORT_REVIEW_INSTRUCTION = (
    "The reply carries these fields:\n"
    "- dimensions: seven scores in [0,1], one per named dimension.\n"
    "- statement_dispositions: one entry per statement id you were shown, "
    "each with the statement id and its disposition (supported, unsupported, "
    "or not_reviewed).\n"
    "- defects: the typed defects you found, each naming the statement ids it "
    "affects.\n"
    "- rationale: why the report scores as it does.\n"
    "Every id you cite must be one this request showed you. A statement you "
    "leave without a disposition is recorded not_reviewed, and the review is "
    "recorded incomplete rather than as a result."
)

REVIEW_DEFECT_RULES = (
    "Every defect must also satisfy these rules:\n"
    "- A defect names the statement ids it affects, and only ids this request "
    "showed you. An id this request does not carry is dropped from the defect; "
    "the defect stays if its problem stays.\n"
    "- kind is one of: coverage, missing_support, acquisition, identity, "
    "contradiction, semantic_duplicate, source_quality, mechanism, freshness, "
    "presentation. severity is one of: critical, major, minor. A defect whose "
    "kind or severity is none of these is dropped rather than recorded.\n"
    "- critical and major are the material defects: a report cannot be "
    "accepted while one is open. minor is a real but editorial observation.\n"
    "- Severity follows the defect's consequence, not how it reads: a defect "
    "that leaves one of the question's own parts with no named answer, or "
    "names a sentence the cited findings do not carry, is major (critical if "
    "it also states something the findings contradict); a defect about "
    "phrasing, ordering, or a planned addition beyond the question's own "
    "parts is minor. A coverage defect naming a required target is treated "
    "as major or worse whatever severity you give it.\n"
    "- Report each distinct problem once. When one problem affects several "
    "statements, name them all in one defect rather than repeating it."
)


_REVIEW_REPLY_EXAMPLES = (
    (
        "Example input: statements S001 and S002; S001 restates the actual that "
        "F01 reports, and S002 calls the actual that F02 reports a forecast.",
        '{"dimensions":{"completeness":0.7,"prioritization":0.8,'
        '"evidence_quality":0.5,"attribution":0.6,"uncertainty":0.7,'
        '"readability":0.9,"actionability":0.7},'
        '"statement_dispositions":[{"statement_id":"S001","disposition":"supported",'
        '"problem":""},{"statement_id":"S002","disposition":"unsupported",'
        '"problem":"F02 reports an actual; the sentence calls it a forecast."}],'
        '"defects":[{"kind":"contradiction","severity":"major",'
        '"statement_ids":["S002"],"target_ids":[],'
        '"problem":"S002 presents the actual F02 reports as a forecast."}],'
        '"rationale":"S001 is supported by F01; S002 misstates the kind of '
        'F02\'s figure, a material defect."}',
    ),
)


def _render_defect_contract(packet: ReportReviewInput) -> str:
    """The defect rules with this packet's own bound, as the review enforces it."""
    return (
        f"{REVIEW_DEFECT_RULES}\n"
        f"- Return at most {review_defect_limit(packet)} defects in total. "
        "That is one per statement and target in this request, so every real "
        "finding fits; more is refused whole, never cut."
    )


def semantic_review_passes(review: ReportReview | None) -> bool:
    """Whether a semantic review accepts the report it judged.

    Local and pure. A missing review never passes: the object is optional so a
    caller can ask the question of a run that has not been reviewed yet, and
    the answer must not depend on which caller asked.

    The mean is taken over ``REVIEW_DIMENSIONS`` rather than over a literal, so
    the divisor cannot drift from the dimension set. A perfect mean cannot
    rescue a material defect, and no defect can lower a score that was never
    given: the two conditions are independent, which is what stops one strong
    dimension from averaging away one false claim.
    """
    if review is None:
        return False
    scores = review.dimensions
    return (
        review.status == "scored"
        and set(scores) == REVIEW_DIMENSIONS
        and all(_usable_score(value) for value in scores.values())
        and sum(scores.values()) / len(REVIEW_DIMENSIONS)
        >= SEMANTIC_REVIEW_MEAN
        and not any(defect.material for defect in review.defects)
        and review.coverage_complete
        and _dispositions_complete(review)
    )


def _dispositions_complete(review: ReportReview) -> bool:
    """Whether every statement this review read carries a recorded judgement.

    ``reviewed_statement_ids`` is the review's account of what it read; a
    disposition is what it concluded about it. Reading a statement and
    recording nothing is the per-statement support review being skipped while
    the review still reports itself complete, and one entry per statement is
    what this review's own response contract asks for — so a review missing a
    disposition has not performed the judgement acceptance is resting on.
    """
    return set(review.reviewed_statement_ids).issubset(
        review.per_statement_dispositions
    )


def _usable_score(value: object) -> bool:
    """A dimension that can be averaged: finite, and inside [0, 1]."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = float(value)
    return number == number and number not in (float("inf"), float("-inf")) and (
        0.0 <= number <= 1.0
    )


# --- the packet one review reads --------------------------------------------


class ReviewStatementView(ContractModel):
    """One reader statement, with the code-built labels a reader sees.

    ``label`` is the label a rendered statement *ends with* — the key facts
    label of every figure the sentence itself states — built by the report's
    own label builders and never re-derived here (R1, D7). ``finding_refs``
    names the findings the sentence cites in the registry's own notation
    (``F01``), the same notation the finding blocks below the statements are
    headed with, and ``finding_labels`` carries their code-built reader labels:
    the material a sentence's wording has to agree with, since a label knows
    the organisation, attribution, kind, period and release the Evidence
    Verifier established, whatever the prose says.

    ``finding_refs`` is not decoration: two findings from one publisher with
    the same kind and release render *identical* reader labels, so the refs are
    the only thing that tells the reviewer which snippet a sentence rests on.

    ``target_ids`` is the address a defect against this statement routes by
    (Task 4.4's extra pass).
    """

    statement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    label: str = ""
    finding_refs: list[str] = Field(default_factory=list)
    finding_labels: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)


class ReviewFindingView(ContractModel):
    """One cited finding, as the reviewer may read it.

    The label is the code-built reader label of the finding's kept figures
    (organisation and attribution first, then kind with a forecast's release);
    the snippet is the verbatim passage the Evidence Verifier verified. This is
    the evidence a statement is judged against, so it is shown whole and never
    re-worded. ``status`` is the finding's own verified/corrected/quoted
    outcome (spec §11.1), rendered in the same words the evidence log uses.
    """

    label: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    host: str = Field(min_length=1)
    snippet: str = Field(min_length=1)
    figure_labels: list[str] = Field(default_factory=list)
    status: str = ""


class ReviewDeterministic(ContractModel):
    """The deterministic results the reviewer is entitled to see.

    The run's own integrity readings about this candidate — a failed hard
    check, a kept sentence the Statement Check never judged, a duplicate fact
    row, an unresolved citation, a settled point with no citation. These are
    facts about the candidate, and not acceptance coaching: there is no
    threshold here, no score from another reviewer, and no suggested
    conclusion.
    """

    hard_checks: list[str] = Field(default_factory=list)
    unjudged_sentences: list[str] = Field(default_factory=list)
    duplicate_fact_rows: int = Field(default=0, ge=0)
    unresolved_citations: int = Field(default=0, ge=0)
    uncited_settled_points: int = Field(default=0, ge=0)


class ReportReviewInput(ContractModel):
    """Everything one semantic review is allowed to judge, and nothing else.

    The whole reader report, every reader statement with its code-built label
    and the labels of the findings it cites, those findings' titles, hosts and
    snippets, the key facts lines, the obligations Not found could not answer,
    and the deterministic checks over the same candidate. It carries no checked
    claim, no corroboration badge, no evidence batch, no Critic score, no prior
    run's judgement, no threshold, and no suggested verdict.

    ``fingerprint`` covers the exact material above. A review is reused only
    for an identical fingerprint, and a returned review is recorded against the
    fingerprint it was opened on, so a judgement can never be attached to text
    it did not read.
    """

    question: str = Field(min_length=1)
    answer_contract: AnswerContract | None = None
    reader_content: str = ""
    statements: list[ReviewStatementView] = Field(default_factory=list)
    findings: list[ReviewFindingView] = Field(default_factory=list)
    fact_rows: list[str] = Field(default_factory=list)
    table_lines: list[str] = Field(default_factory=list)
    """The question-shaped table's own rows (spec §11.1), one line per row,
    with the cell texts and the backing statement and fact-row ids -- built
    once by :func:`_table_lines` from ``composition.table`` and never a raw
    dump of it, so the packet stays the reader-facing wording."""
    not_found: list[str] = Field(default_factory=list)
    deterministic: ReviewDeterministic = Field(default_factory=ReviewDeterministic)
    required_target_ids: list[str] = Field(default_factory=list)
    """The plan's own required targets, so a returned defect can be judged
    against them (D11): a ``coverage`` defect naming one of these is always
    material, whatever severity the reply gave it — the routing layer and
    ``_defects`` both read this list rather than trusting the model's own
    account of what the question required.
    """
    rubric_version: int = Field(default=REVIEW_RUBRIC_VERSION, ge=1)
    composition_fingerprint: str = ""
    """The semantic fingerprint of the composition this packet was built from.

    Carried so the review that comes back can record the same value: the state
    merge compares it against an incoming composition to decide whether a
    stored judgement still describes this report. It is derived from the
    composition alone, so it is stable across the terminal re-render that
    changes only the presentation badge.
    """
    fingerprint: str = ""

    @property
    def expected_statement_ids(self) -> list[str]:
        """Statement ids a review must cover and disposition.

        Every statement the packet carries is one the review must judge:
        ``_statement_views`` builds one view per statement the composition
        renders, and each one is reader-visible content.
        """
        return [statement.statement_id for statement in self.statements]

    @property
    def known_target_ids(self) -> list[str]:
        """The target ids a defect may name, in the statements' own order."""
        target_ids: list[str] = []
        for statement in self.statements:
            for target_id in statement.target_ids:
                if target_id not in target_ids:
                    target_ids.append(target_id)
        return target_ids

    def statement(self, statement_id: str) -> ReviewStatementView | None:
        return next(
            (
                statement
                for statement in self.statements
                if statement.statement_id == statement_id
            ),
            None,
        )


def build_report_review_input(
    state: ResearchState,
    composition: ReportComposition | None = None,
) -> ReportReviewInput:
    """Build the one packet a semantic review reads, from the exact candidate.

    ``composition`` defaults to ``state.composition``. The report text comes
    from ``state.report`` verbatim — never a prefix — and the statements,
    labels, findings, key facts and Not found come from the composition the
    Report Writer composed, so a defect can only ever cite a record this packet
    carries. The deterministic block is the quality snapshot's own reading of
    the same candidate, never a second computation that could disagree with it.
    """
    if composition is None:
        composition = state.composition
    statements = _statement_views(composition)
    quality = state.quality
    packet = ReportReviewInput(
        question=state.original_question,
        answer_contract=state.answer_contract,
        reader_content=state.report or "",
        statements=statements,
        findings=_finding_views(composition),
        fact_rows=_fact_row_lines(composition),
        table_lines=_table_lines(composition),
        not_found=[target.question for target in composition.not_found]
        if composition is not None
        else [],
        deterministic=ReviewDeterministic(
            hard_checks=list(quality.hard_failures) if quality else [],
            unjudged_sentences=list(quality.unjudged_sentences) if quality else [],
            duplicate_fact_rows=quality.duplicate_fact_rows if quality else 0,
            unresolved_citations=quality.unresolved_citations if quality else 0,
            uncited_settled_points=quality.uncited_settled_points if quality else 0,
        ),
        required_target_ids=list(quality.required_target_ids) if quality else [],
        composition_fingerprint=composition_semantic_fingerprint(composition),
    )
    return packet.model_copy(
        update={"fingerprint": report_review_input_fingerprint(packet)}
    )


def _statement_views(
    composition: ReportComposition | None,
) -> list[ReviewStatementView]:
    """Every statement the composition renders, with its code-built labels.

    Walked through ``composition.statements``, which is the composition's own
    reader-ordered list — first occurrence wins, so a fact the summary states
    and the answer table lists is one statement judged once.

    The cited findings are resolved through ``_finding_registry_pairs``, the
    report's own label-to-finding walk: two revision editions of one page share
    a ``finding_fingerprint``, so a fingerprint-keyed lookup would hand the
    later edition's snippet to a statement citing the earlier one (P2) and a
    statement citing either would name no registry label at all (P1).
    """
    if composition is None:
        return []
    pairs = [
        (label, finding)
        for label, finding in _finding_registry_pairs(composition)
        if label is not None
    ]
    labels = _statement_labels(composition)
    views: list[ReviewStatementView] = []
    for statement in composition.statements:
        cited_ids = set(statement.finding_ids)
        cited = [
            (label, finding)
            for label, finding in pairs
            if finding_fingerprint(finding) in cited_ids
        ]
        views.append(
            ReviewStatementView(
                statement_id=statement.statement_id,
                text=statement.text,
                label=labels.get(statement.statement_id, ""),
                finding_refs=[label for label, _ in cited],
                finding_labels=[_finding_label(finding) for _, finding in cited],
                target_ids=list(statement.target_ids),
            )
        )
    return views


def _statement_labels(composition: ReportComposition) -> dict[str, str]:
    """Each statement's code-built reader label, as the rendered suffix.

    Reused from the reader report's own builder rather than re-derived (R1):
    ``_point_labels`` reads exactly a point's text and its statement's finding
    ids, so a statement rendered as a table cell or an uncertainty note — a
    bare ``ReportStatement`` with no point of its own — gets the same label the
    reader would see beside that text.
    """
    labels: dict[str, list[str]] = {}
    for point in composition.summary:
        if point.statement is not None:
            labels.setdefault(point.statement.statement_id, []).extend(
                _point_labels(point, composition)
            )
    for section in composition.sections:
        for point in section.points:
            if point.statement is not None:
                labels.setdefault(point.statement.statement_id, []).extend(
                    _point_labels(point, composition)
                )
    for statement in composition.statements:
        if statement.statement_id in labels:
            continue
        labels[statement.statement_id] = _point_labels(
            ReportPoint(text=statement.text, statement=statement), composition
        )
    return {
        statement_id: " | ".join(dict.fromkeys(values))
        for statement_id, values in labels.items()
    }


def _finding_views(
    composition: ReportComposition | None,
) -> list[ReviewFindingView]:
    """One view per cited finding, paired with its own registered label.

    Paired by ``_finding_registry_pairs`` rather than by inverting
    ``finding_labels`` into a fingerprint-keyed map: two revision editions of
    one page share a fingerprint, and that map hands the later edition's
    snippet and figure labels to both labels (P2), so a statement citing the
    earlier one would be judged against the wrong evidence.
    """
    if composition is None:
        return []
    views: list[ReviewFindingView] = []
    for label, finding in _finding_registry_pairs(composition):
        if label is None:
            # A finding the report never gave a label is never cited by a
            # statement, and the finding blocks are the citable registry.
            continue
        views.append(
            ReviewFindingView(
                label=label,
                finding_id=finding_fingerprint(finding),
                source_title=finding.source_title,
                host=publisher_identity(finding.source_url),
                snippet=finding.snippet or finding.content,
                figure_labels=[
                    _figure_label_for(finding, result.context)
                    for result in _kept_results(finding)
                ],
                status=_finding_status_label(finding),
            )
        )
    return views


def _kept_results(finding: Finding) -> list[Any]:
    """The finding's verified figure results, in the order they were verified."""
    if finding.verification is None:
        return []
    return [
        result
        for result in finding.verification.figure_results
        if result.kept and result.context is not None
    ]


def _fact_row_lines(composition: ReportComposition | None) -> list[str]:
    """The key facts lines, one per row, with the row's own code-built label."""
    if composition is None:
        return []
    return [_fact_row_line(row) for row in composition.fact_rows]


def _fact_row_line(row: FactRow) -> str:
    """One key facts line: the reader's columns, and the row's own label.

    ``_row_label`` is the builder the rendered statement suffix uses, imported
    rather than re-derived (R1), so the reviewer reads the same words about a
    figure that the reader does.
    """
    return (
        f"- {row.value} ({row.measure}) | subject {row.subject or 'not stated'} "
        f"| period {row.period or 'not stated'} "
        f"| kind {row.kind} | scope {row.scope or 'not stated'} "
        f"| release or edition {row.release or 'not stated'} "
        f"| {_row_label(row)}"
    )


_FINDING_STATUS_LABELS: dict[str, str] = {
    "verified": "verified",
    "verified_corrected": "verified with corrections",
    "quoted": "quoted, not checked for context",
}


def _finding_status_label(finding: Finding) -> str:
    """The finding's own verified/corrected/quoted outcome, in reader words
    (spec §11.1). ``dropped`` never reaches here: a dropped finding earns no
    registry label, so ``_finding_registry_pairs`` never cites it."""
    if finding.verification is None:
        return ""
    status = finding.verification.status
    return _FINDING_STATUS_LABELS.get(status, status)


def _table_entry_publisher(url: str, composition: ReportComposition) -> str:
    credit = composition.page_credits.get(normalize_source_url(url))
    return credit.publisher if credit is not None else publisher_identity(url)


def _table_cell_text(
    cell: TableCell,
    composition: ReportComposition,
    *,
    shape: str,
    is_last_column: bool,
) -> str:
    """One reviewer-facing cell's own text (spec §11.1; R1's fix round).

    An options-table cell's verbatim span lives in ``cell.text`` (the
    "Option" column) or in its ``entries`` (a part cell or Recommended by),
    never in both; a findings-table cell's text is already ``cell.text``
    except its own Source column, which names its finding ids instead. Only
    an entryless, textless, finding-less cell -- an option with nothing
    marked for that part -- ever reads as an em dash.
    """
    if cell.text:
        return cell.text
    if shape == "findings" and is_last_column:
        return ", ".join(cell.finding_ids)
    if not cell.entries:
        return ""
    parts: list[str] = []
    for entry in cell.entries:
        publisher = _table_entry_publisher(entry.source_url, composition)
        if entry.text:
            parts.append(f"{entry.text} — {publisher}")
        elif is_last_column and entry.date:
            parts.append(f"{publisher} ({entry.date})")
        else:
            parts.append(publisher)
    return "; ".join(parts)


def _table_lines(composition: ReportComposition | None) -> list[str]:
    """The question-shaped table's own rows, one line per row (spec §11.1).

    Each line carries every cell's own text -- the writer's verbatim span
    when it marked one, else the page(s) behind it -- headed by its column
    name, with that cell's own backing statement ids attached to it, so a
    mis-credited cell can be pinned to the statement that supposedly backs
    it (R1) rather than only to the row.
    """
    if composition is None or composition.table is None:
        return []
    table = composition.table
    last = len(table.columns) - 1
    lines: list[str] = []
    for row in table.rows:
        segments: list[str] = []
        for position, (column, cell) in enumerate(zip(table.columns, row)):
            text = _table_cell_text(
                cell, composition, shape=table.shape, is_last_column=position == last
            )
            segment = f"{column}: {text or '—'}"
            if cell.statement_ids:
                segment += f" [statements: {', '.join(sorted(cell.statement_ids))}]"
            segments.append(segment)
        row_ids = sorted({rid for cell in row for rid in cell.row_ids})
        parts = ["- " + "; ".join(segments)]
        if row_ids:
            parts.append(f"  backing fact rows: {', '.join(row_ids)}")
        lines.append("\n".join(parts))
    return lines


def composition_semantic_fingerprint(
    composition: ReportComposition | None,
) -> str:
    """The digest of everything in one composition a review judges.

    The rule is "replacing the composition invalidates the stored review unless
    its semantic fingerprint matches", and this is that fingerprint: the reader
    statements (text and record), the finding ids the report cites, the key
    facts rows, Not found, the question-shaped table, the parts (spec §6.1)
    and the unreachable pages. Option marks already ride on the statements, so
    the table's own hash catches only what a rebuild from the same marks could
    still change: its shape and cap. Those are exactly what the review reads.

    Deliberately *not* a whole-composition dump, deliberately not
    ``quality_status`` — a generated presentation badge the terminal finalizer
    rewrites on the way out, whose hashing would make stamping "accepted" onto
    a report invalidate the judgement that accepted it — and deliberately not
    the plan: the reviewer never sees the plan, and a re-plan alone does not
    change the report it judged.
    """
    if composition is None:
        return ""
    projection = {
        "statements": [
            statement.model_dump(mode="json")
            for statement in composition.statements
        ],
        "finding_ids": [
            finding_fingerprint(finding) for finding in composition.findings
        ],
        "fact_rows": [
            row.model_dump(mode="json") for row in composition.fact_rows
        ],
        "not_found": [
            target.model_dump(mode="json") for target in composition.not_found
        ],
        "table": (
            composition.table.model_dump(mode="json")
            if composition.table is not None
            else None
        ),
        "parts": [
            part.model_dump(mode="json") for part in composition.parts
        ],
        "unreachable": [
            page.model_dump(mode="json") for page in composition.unreachable
        ],
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def report_review_input_fingerprint(packet: ReportReviewInput) -> str:
    """The stable digest of the exact material one review judges.

    Twelve hex characters over the packet's canonical JSON, with its own
    ``fingerprint`` field excluded. Every field is part of it on purpose — a
    statement's text, a finding's snippet, a key facts line, the reader content
    and the deterministic readings are all things whose change makes the stored
    judgement about a different report — and the packet deliberately carries no
    presentation field: ``quality_status`` is a generated badge this packet
    never reads, so stamping "accepted" onto a composition cannot invalidate a
    judgement of its content, while a content or reference change always does.
    """
    payload = packet.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


# --- the provider-facing contract -------------------------------------------

# A report is quoted inside a Markdown fence of its own, made longer than any
# backtick run inside it so the report cannot close the fence early and have its
# own headings read as request sections. The Critic's renderer did this and is
# deleted with step 4 (PD-21), so the rule lives here rather than behind a
# doomed import.
_REPORT_FENCE_MIN = 3
_REPORT_FENCE_INFO = "report"


def _report_fence(report: str) -> str:
    """Return a backtick fence that no run inside ``report`` can close."""
    longest = max((len(run) for run in re.findall(r"`+", report)), default=0)
    return "`" * max(_REPORT_FENCE_MIN, longest + 1)


class ReviewDimensionScores(ContractModel):
    """The seven dimension scores, as the reply carries them.

    Seven named fields rather than a mapping: the dimension set is then
    structural in the response schema, so a reply that omits one is a schema
    failure with a field path rather than a dict that quietly averages over six.
    """

    completeness: UnitScore
    prioritization: UnitScore
    evidence_quality: UnitScore
    attribution: UnitScore
    uncertainty: UnitScore
    readability: UnitScore
    actionability: UnitScore

    def as_dimensions(self) -> dict[str, float]:
        return {
            "completeness": float(self.completeness),
            "prioritization": float(self.prioritization),
            "evidence_quality": float(self.evidence_quality),
            "attribution": float(self.attribution),
            "uncertainty": float(self.uncertainty),
            "readability": float(self.readability),
            "actionability": float(self.actionability),
        }


class StatementDispositionDraft(ContractModel):
    """One provider-reported per-statement disposition."""

    statement_id: str = Field(min_length=1)
    disposition: StatementReviewDisposition
    problem: str = ""


class ReviewDefectDraft(ContractModel):
    """One provider-reported defect, before this review resolves its scope.

    ``kind`` and ``severity`` are plain strings rather than the closed
    ``GapKind``/``GapSeverity`` literals: a reply that invents a category is a
    reply this review must be able to *drop one defect from*, and a schema that
    refused the whole reply would trade a single unusable defect for the entire
    judgement. :func:`_defects` is where the vocabulary is enforced.
    """

    kind: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    statement_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    problem: str = Field(min_length=1)


class ReportReviewDraft(ContractModel):
    """One provider-reported whole-report review, before local resolution.

    One reply carries the whole judgement the graph runs: the seven dimension
    scores, a disposition for every statement offered, the typed defects, and
    the reviewer's own rationale. There is no per-batch reply any more — the
    packet carries no batch — so nothing has to be merged across requests.
    """

    dimensions: ReviewDimensionScores
    statement_dispositions: list[StatementDispositionDraft] = Field(
        default_factory=list
    )
    # No static ``max_length``: the bound depends on the packet, so the review
    # enforces ``review_defect_limit`` and the request states it. A schema cap
    # of twelve refused truthful reviews of larger reports whole.
    defects: list[ReviewDefectDraft] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ReportReviewContractViolation(RuntimeError):
    """A reply that passed its schema and broke the review's own contract."""


# --- the request ------------------------------------------------------------


def _render_answer_contract(contract: AnswerContract | None) -> str:
    if contract is None:
        return "(no answer contract was frozen for this run)"
    return "\n".join(
        (
            f"- question: {contract.question}",
            f"- scope: {contract.scope_statement}",
            f"- geography: {contract.geographic_scope}",
            f"- as of: {contract.as_of_date}",
            f"- answer form: {contract.answer_kind}",
            f"- evidence period: {contract.evidence_period_requirement}",
            f"- requested word limit: {contract.requested_word_limit}",
            "- assumptions: "
            + ("; ".join(contract.assumptions) or "(none recorded)"),
        )
    )


def _render_statements(packet: ReportReviewInput) -> str:
    """Every statement with its text, its labels, and the findings it cites."""
    lines: list[str] = []
    for statement in packet.statements:
        parts = [f"- {statement.statement_id}"]
        parts.append(f"  {statement.text}")
        if statement.label:
            parts.append(f"  reader label: {statement.label}")
        if statement.finding_refs:
            parts.append("  cites: " + ", ".join(statement.finding_refs))
        if statement.finding_labels:
            parts.append(
                "  cited finding labels: " + " | ".join(statement.finding_labels)
            )
        if statement.target_ids:
            parts.append("  answers targets: " + ", ".join(statement.target_ids))
        lines.append("\n".join(parts))
    return "\n".join(lines) or "(no reader statement records were supplied)"


def _render_findings(packet: ReportReviewInput) -> str:
    """Every cited finding whole: its labels, its page, its status, and its snippet."""
    blocks: list[str] = []
    for finding in packet.findings:
        blocks.append(
            f"### {finding.label}\n"
            f"source: {finding.source_title} — {finding.host}\n"
            f"status: {finding.status or 'verified'}\n"
            f"figure labels: "
            f"{' | '.join(finding.figure_labels) or '(no figure was kept)'}\n"
            f"snippet:\n{finding.snippet}"
        )
    return "\n\n".join(blocks) or "(no verified finding was cited)"


def _render_fact_rows(packet: ReportReviewInput) -> str:
    return "\n".join(packet.fact_rows) or "(no figure passed the Evidence Verifier)"


def _render_not_found(packet: ReportReviewInput) -> str:
    return "\n".join(
        f"- {question}" for question in packet.not_found
    ) or "(every planned obligation was answered)"


def _render_table(packet: ReportReviewInput) -> str:
    return "\n".join(packet.table_lines) or "(no table qualified for this report)"


def _render_deterministic(packet: ReportReviewInput) -> str:
    deterministic = packet.deterministic
    lines = [f"- {check}" for check in deterministic.hard_checks] or [
        "- no deterministic hard failure was recorded"
    ]
    lines.extend(
        (
            f"- kept sentences with no Statement Check verdict: "
            f"{', '.join(deterministic.unjudged_sentences) or 'none'}",
            f"- duplicate fact rows: {deterministic.duplicate_fact_rows}",
            f"- unresolved citations: {deterministic.unresolved_citations}",
            f"- settled points with no citation: "
            f"{deterministic.uncited_settled_points}",
        )
    )
    return "\n".join(lines)


def _render_dimension_guidance() -> str:
    return "\n".join(
        f"- {name}: {guidance}" for name, guidance in DIMENSION_GUIDANCE
    )


def _render_manifest(packet: ReportReviewInput) -> str:
    required = set(packet.required_target_ids)
    return "\n".join(
        (
            "Statement ids in this packet: "
            + (", ".join(packet.expected_statement_ids) or "(none)"),
            "Target ids in this packet: "
            + (
                ", ".join(
                    f"{target_id} [required]"
                    if target_id in required
                    else target_id
                    for target_id in packet.known_target_ids
                )
                or "(none)"
            ),
            "Finding registry labels in this packet: "
            + (", ".join(finding.label for finding in packet.findings) or "(none)"),
        )
    )


def _reader_statements_block(packet: ReportReviewInput) -> str:
    return (
        "# Reader statements\n"
        "Every sentence the report prints, with the code-built reader label "
        "its cited figures would carry -- built by code, never printed "
        "beside the sentence -- and the finding labels it cites (F01…, "
        "whose snippets and figure labels follow below). A defect may "
        "cite a statement id from this list and no other.\n"
        + _render_statements(packet)
    )


def _cited_findings_block(packet: ReportReviewInput) -> str:
    return (
        "# Cited findings\n"
        "The verified findings the statements rest on, with the reader "
        "labels built from their verified context, each finding's own "
        "verified/corrected/quoted status, and the snippet the Evidence "
        "Verifier checked against the page. This is the evidence a "
        "sentence is judged against.\n" + _render_findings(packet)
    )


def _verified_figures_block(packet: ReportReviewInput) -> str:
    return (
        "# Verified figures\n"
        "The report's own verified figures, each with the label a "
        "sentence stating it would carry. A forecast's label carries its "
        "issuer and its release, or says the page stated no release; an "
        "actual's label says actual.\n" + _render_fact_rows(packet)
    )


def _table_block(packet: ReportReviewInput) -> str:
    return (
        "# Table\n"
        "The question-shaped table code assembled from the statements' "
        "option marks and the verified figures, one line per row, with "
        "the backing statement and fact-row ids. A cell crediting a "
        "source with a verdict or pick its backing statement does not "
        "carry is a defect against that statement's id.\n"
        + _render_table(packet)
    )


def _not_found_block(packet: ReportReviewInput) -> str:
    return (
        "# What the report could not confirm\n"
        "The obligations no verified finding answered. The report must "
        "list these and must not present them as answered.\n"
        + _render_not_found(packet)
    )


def _deterministic_block(packet: ReportReviewInput) -> str:
    return (
        "# Deterministic checks\n"
        "Integrity results the run computed for itself. A failed check is a "
        "fact about the candidate, not a verdict — and none of these "
        "numbers is a target to reach.\n" + _render_deterministic(packet)
    )


def review_messages(packet: ReportReviewInput) -> list[ChatMessage]:
    """The one request a review makes: the report, its statements, its findings.

    Static first (PD-29, D10): the response contract, what each dimension
    means and the reply format lead, so a second review of the same run's
    packet shares them as a prefix; the packet itself follows.

    Nothing here is truncated. The reader content is carried whole in a fence of
    its own — a report whose end is cut off is a report whose closing
    contradiction, invented limitation, or mislabelled sentence cannot be
    judged — and so is every finding snippet the statements rest on.
    """
    static = [
        f"# Response contract\n{REPORT_REVIEW_INSTRUCTION}\n\n{_render_defect_contract(packet)}",
        "# What each dimension means\n"
        "Score each dimension in [0,1] against its own definition:\n"
        + _render_dimension_guidance(),
        "# Reply format\n" + render_structured_reply_format(_REVIEW_REPLY_EXAMPLES),
    ]
    material = [
        f"# Research question\n{packet.question}",
        (
            "# Packet fingerprint\n"
            f"Packet fingerprint: {packet.fingerprint}\n"
            "This review is of exactly this material: the reader report, the "
            "statement records, their labels, the cited findings and the "
            "deterministic checks below are the whole of what is being judged."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        (
            "# Reader content — the complete candidate\n"
            "The report is quoted in full below and nothing is removed from its "
            "end. Its headings belong to the report rather than to this request. "
            "This is the complete report a reader would receive, not a prefix: "
            "judge it whole, including its closing sections.\n\n"
            f"{_report_fence(packet.reader_content)}{_REPORT_FENCE_INFO}\n"
            f"{packet.reader_content.rstrip()}\n"
            f"{_report_fence(packet.reader_content)}"
        ),
        _reader_statements_block(packet),
        _cited_findings_block(packet),
        _verified_figures_block(packet),
        _table_block(packet),
        _not_found_block(packet),
        _deterministic_block(packet),
        f"# Manifest of what you were shown\n{_render_manifest(packet)}",
    ]
    return [
        ChatMessage(role="developer", content=REPORT_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content=render_structured_request(static, material)),
    ]


# --- resolving a reply into one review --------------------------------------


def _dispositions(
    drafts: Sequence[StatementDispositionDraft],
    *,
    packet: ReportReviewInput,
) -> dict[str, StatementReviewDisposition]:
    """One disposition per statement, with disagreement resolved safely.

    A statement the reply judges twice in two ways resolves to the *less*
    settled reading: "this sentence is not carried by its findings" may not be
    overwritten by the same reply's "supported", or a self-contradicting answer
    would be recorded as agreement.

    An id the packet does not carry is refused, exactly as an unresolvable
    defect scope is not: a disposition is a judgement *about a record*, and a
    judgement about a record this request never showed cannot be recorded at
    all. A defect is a *problem* whose ids are an address — losing the address
    keeps the finding — but a disposition has nothing left once it is detached
    from the statement it judges.
    """
    resolved: dict[str, StatementReviewDisposition] = {}
    for draft in drafts:
        statement_id = draft.statement_id.strip()
        if not statement_id:
            continue
        statement = packet.statement(statement_id)
        if statement is None:
            raise ReportReviewContractViolation(
                f"the reply judged statement {statement_id!r}, which this "
                "packet does not carry"
            )
        previous = resolved.get(statement_id)
        if previous is None or (
            draft.disposition in UNSETTLED_STATEMENT_DISPOSITIONS
            and previous not in UNSETTLED_STATEMENT_DISPOSITIONS
        ):
            resolved[statement_id] = draft.disposition
    return resolved


def _defects(
    drafts: Sequence[ReviewDefectDraft],
    *,
    packet: ReportReviewInput,
) -> tuple[list[ReviewDefect], list[str]]:
    """The typed defects a reply returned, and a note for every draft dropped.

    A draft whose ``kind`` or ``severity`` is not in this project's vocabulary
    is dropped, with a note naming it, rather than forced into a category: the
    vocabulary is closed, and recording an invented one would make "which
    defects does this system find?" unanswerable. A draft whose problem is
    blank is dropped for the same reason the record requires one.

    A *scope* id this packet does not carry is removed from the defect, which
    stays: the reviewer found something real, and what it got wrong is the
    address. A defect whose ids all resolve to nothing is still a material
    observation if it says so, and routing ignores it — but a phantom id never
    enters the record as a resolvable scope.
    """
    known_statements = {statement.statement_id for statement in packet.statements}
    known_targets = set(packet.known_target_ids)
    required_targets = set(packet.required_target_ids)
    defects: list[ReviewDefect] = []
    notes: list[str] = []
    for index, draft in enumerate(drafts, start=1):
        kind = draft.kind.strip()
        severity = draft.severity.strip()
        problem = draft.problem.strip()
        unknown: list[str] = []
        if kind not in GAP_KINDS:
            unknown.append(f"{draft.kind!r} is not a defect kind")
        if severity not in GAP_SEVERITIES:
            unknown.append(f"{draft.severity!r} is not a defect severity")
        if unknown:
            notes.append(
                f"Defect {index} was dropped: " + " and ".join(unknown) + "."
            )
            continue
        if not problem:
            notes.append(f"Defect {index} was dropped: it names no problem.")
            continue
        target_ids = [
            target_id
            for target_id in dict.fromkeys(draft.target_ids)
            if target_id in known_targets
        ]
        if (
            kind == "coverage"
            and severity not in GAP_MATERIAL_SEVERITIES
            and set(target_ids) & required_targets
        ):
            # D11: a coverage defect naming a required target withholds
            # acceptance whatever severity the reply gave it — the model
            # called an identical missing-half-answer defect major in one
            # review and minor in the next, so the floor is code, not asked.
            severity = "major"
        defects.append(
            ReviewDefect(
                defect_id=f"review-{len(defects) + 1:02d}",
                kind=cast(GapKind, kind),
                severity=cast(GapSeverity, severity),
                target_ids=target_ids,
                statement_ids=[
                    statement_id
                    for statement_id in dict.fromkeys(draft.statement_ids)
                    if statement_id in known_statements
                ],
                problem=problem,
            )
        )
    return defects, notes


def _derived_defects(
    packet: ReportReviewInput,
    dispositions: Mapping[str, StatementReviewDisposition],
    defects: Sequence[ReviewDefect],
) -> tuple[list[ReviewDefect], list[str]]:
    """Material defects for the statements a disposition left unestablished.

    The reviewer's own disposition is a judgement — "this sentence is not in
    the findings" — and it must not be recordable while the review still
    passes, which is exactly what would happen if an unsettled statement were
    an observation and the pass rule only read ``defects``. So an unsettled
    disposition that no returned material defect names gets a project-derived
    material defect, and the statements that produced one are recorded: the
    record says which defects the reviewer returned and which this project
    derived from its dispositions.

    The skip test asks about a **material** defect, because that is what the
    record contract requires an unsettled statement to be named by: asking
    whether *any* defect named it let a ``minor`` observation about a sentence
    stand in for the finding that the sentence is not carried by its evidence.
    """
    derived: list[ReviewDefect] = []
    derived_statements: list[str] = []
    for statement_id in sorted(dispositions):
        disposition = dispositions[statement_id]
        if disposition not in UNSETTLED_STATEMENT_DISPOSITIONS:
            continue
        if any(
            statement_id in defect.statement_ids
            for defect in defects
            if defect.material
        ):
            continue
        statement = packet.statement(statement_id)
        if statement is None:
            continue
        derived.append(
            ReviewDefect(
                defect_id=f"review-{len(defects) + len(derived) + 1:02d}",
                target_ids=list(statement.target_ids) or [QUESTION_TARGET_ID],
                statement_ids=[statement_id],
                kind="missing_support",
                severity="major",
                problem=(
                    f"The terminal review recorded this statement as "
                    f"{disposition!r}: "
                    + (
                        "the review never judged it, so nothing establishes it "
                        "as written."
                        if disposition == UNREVIEWED_STATEMENT_DISPOSITION
                        else "the findings behind it do not establish it as "
                        "written."
                    )
                ),
            )
        )
        derived_statements.append(statement_id)
    return derived, derived_statements


def _merge_review(
    packet: ReportReviewInput,
    *,
    dimension_scores: Mapping[str, float] | None,
    dispositions: Mapping[str, StatementReviewDisposition],
    defects: Sequence[ReviewDefect],
    derived_statements: Sequence[str],
    rationale: str,
    status: str,
) -> ReportReview:
    """Assemble the recorded review from the reply that came back."""
    reviewed = [
        statement_id
        for statement_id in packet.expected_statement_ids
        if dispositions.get(statement_id) not in (None, UNREVIEWED_STATEMENT_DISPOSITION)
    ]
    unreviewed = [
        statement_id
        for statement_id in packet.expected_statement_ids
        if statement_id not in reviewed
    ]
    return ReportReview(
        status=status,  # type: ignore[arg-type]
        dimensions=dict(dimension_scores or {}),
        defects=list(defects),
        per_statement_dispositions=dict(dispositions),
        reviewed_statement_ids=reviewed,
        unreviewed_statement_ids=unreviewed,
        derived_defect_statement_ids=list(derived_statements),
        input_fingerprint=packet.fingerprint,
        composition_fingerprint=packet.composition_fingerprint,
        rubric_version=packet.rubric_version,
        rationale=rationale,
    )


def _status_for(
    packet: ReportReviewInput,
    *,
    dimension_scores: Mapping[str, float] | None,
    dispositions: Mapping[str, StatementReviewDisposition],
) -> str:
    """``scored`` only when this review actually judged what it was given.

    Coverage is a precondition of a score, not a note beside one: a review that
    skipped a statement or a dimension has not judged the report, and recording
    it as ``scored`` would let missing coverage read as a pass. The same rule is
    stated on ``ReportReview`` itself, so an incomplete score cannot be
    constructed even by a later caller.

    Reading is not judging, so a disposition is required for every statement
    too, and ``not_reviewed`` is not one: it is the honest record of a statement
    the review never reached, which is exactly the shape that must not be
    ``scored``.
    """
    if dimension_scores is None:
        return "incomplete"
    if set(dimension_scores) != REVIEW_DIMENSIONS:
        return "incomplete"
    judged = {
        statement_id
        for statement_id, disposition in dispositions.items()
        if disposition != UNREVIEWED_STATEMENT_DISPOSITION
    }
    if set(packet.expected_statement_ids).difference(judged):
        return "incomplete"
    return "scored"


def _incomplete_reason(
    packet: ReportReviewInput,
    *,
    unreviewed_statement_ids: Sequence[str],
) -> str:
    missing = [
        statement_id
        for statement_id in packet.expected_statement_ids
        if statement_id in set(unreviewed_statement_ids)
    ]
    if not missing:
        return "This review returned no complete judgement."
    return (
        "This review is incomplete: no disposition was recorded for "
        "statement(s) " + ", ".join(missing) + "."
    )


# --- the reviewer -----------------------------------------------------------


class ReportReviewer:
    """The tool-free terminal reviewer, configured as its own service role.

    Not a ``BaseAgent``: there is no ReAct loop, no toolset, no scratchpad, and
    no slot in the six-agent registry — the reviewer makes exactly one kind of
    request and holds no conversation. It still fingerprints that request the
    way every agent fingerprints its own, so an artifact records the model,
    effort, prompt version, and output budget the judgement was made under.
    """

    name: ClassVar[str] = REPORT_REVIEWER_ROLE
    description: ClassVar[str] = (
        "Judge the finished report against the findings it rests on."
    )
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    prompt_version: ClassVar[str] = REPORT_REVIEW_PROMPT_VERSION

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker | None = None,
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
    ) -> None:
        self._provider = provider
        self._tracker = tracker
        self._config = config or AgentRuntimeConfig()
        self._model_profile = model_profile
        self._call_fingerprints: dict[str, str] = {}
        self._review_records: list[ResearchError] = []

    @property
    def provider(self) -> StructuredCompleter:
        return self._provider

    @property
    def config(self) -> AgentRuntimeConfig:
        return self._config

    @property
    def model_profile(self) -> EffectiveModelConfig | None:
        return self._model_profile

    @property
    def call_fingerprints(self) -> dict[str, str]:
        """One configuration fingerprint per kind of request this reviewer made."""
        return dict(self._call_fingerprints)

    def fingerprint_call(
        self,
        label: str,
        *,
        output_limit: int | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Fingerprint one review request and record it, as an agent does.

        A per-call ``reasoning_effort`` override stands in for the profile's
        value when one is passed, exactly as it does for an agent's call.
        """
        profile = self._model_profile
        effort = (
            reasoning_effort
            if reasoning_effort is not None
            else ("unresolved" if profile is None else profile.reasoning_effort)
        )
        value = call_configuration_fingerprint(
            agent_name=self.name,
            model="unresolved" if profile is None else profile.model,
            thinking_mode=(
                "unresolved" if profile is None else profile.thinking_mode
            ),
            reasoning_effort=effort,
            output_limit=output_limit,
            context_limit=self._config.prompt_context_entries,
            schema_name=label,
            prompt_version=self.prompt_version,
        )
        self._call_fingerprints[label] = value
        return value

    async def review(
        self,
        packet: ReportReviewInput,
        *,
        previous: ReportReview | None = None,
    ) -> ReportReview:
        """Judge one packet, or reuse a scored review of the same material.

        Reuse is by fingerprint and nothing else: a stored review of different
        content is not a review of this report, and a stored review that is not
        ``scored`` is not a review at all. There is no second attempt loop
        here — one request per review, and the outcome is recorded — but a
        request the provider truncated is re-asked once, and ``review_records``
        reports what that cost.
        """
        self._review_records = []
        if (
            previous is not None
            and previous.status == "scored"
            and previous.input_fingerprint == packet.fingerprint
        ):
            return previous
        return await review_report(
            self._provider,
            packet,
            tracker=self._tracker,
            reviewer=self,
        )

    async def review_scoped(
        self,
        scoped: "ScopedReportReviewInput",
    ) -> ReportReview:
        """Re-judge a redraft's changed parts, carrying the rest forward.

        T5 addendum: the first review of a pass is always full; this is the
        one after a redraft, asked only when the redraft's carried-over parts
        verified byte-identical to what the previous review judged (baked
        into ``scoped`` by :func:`build_scoped_report_review_input`). Its own
        schema (``ScopedReportReviewDraft``) and prompt version fingerprint
        the call distinctly from a full review's.
        """
        self._review_records = []
        return await review_scoped_report(
            self._provider,
            scoped,
            tracker=self._tracker,
            reviewer=self,
        )

    @property
    def review_records(self) -> tuple[ResearchError, ...]:
        """The records the most recent review produced, provider-free.

        Empty for a review that needed no retry — including one this reviewer
        reused instead of making — so a consumer publishing these beside the
        review cannot report an operational fact about a call that never
        happened. A whole review produces at most one, the output-limit retry.
        """
        return tuple(self._review_records)

    async def _request(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
    ) -> Any:
        self.fingerprint_call(
            schema.__name__,
            output_limit=self._config.report_review_max_tokens,
        )
        try:
            reply = await self._structured_request(messages, schema)
        except ProviderOutputLimitError as error:
            reply = await self._re_ask_truncated(messages, schema, error)
        payload = (
            reply.model_dump(mode="python")
            if isinstance(reply, schema)
            else reply
        )
        # Re-validated whatever its Python type: a provider that returns a
        # payload returns a dict, and an adapter or test double can return an
        # object whose fields were never checked. One trustworthy validation
        # boundary means the *values* are checked, not just the transport.
        return schema.model_validate(payload)

    async def _structured_request(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        *,
        reasoning_effort: str | None = None,
    ) -> Any:
        """One structured request for this review, at this request's effort.

        ``reasoning_effort`` is the retry's own setting: ``None`` on the
        ordinary request, and the shared retry effort on the second attempt.
        The output budget is the operation's configured cap on both.
        """
        return await self._provider.complete_structured(
            messages,
            schema,
            agent_name=self.name,
            max_tokens=self._config.report_review_max_tokens,
            reasoning_effort=reasoning_effort,
        )

    async def _re_ask_truncated(
        self,
        messages: Sequence[ChatMessage],
        schema: type[Any],
        error: ProviderOutputLimitError,
    ) -> Any:
        """Re-ask a truncated review request once, or report it stayed truncated.

        A truncated reply is the one failure a different request can fix, so it
        is re-asked once under the same output budget at the effort that leaves
        more of that budget for the answer. A second truncation is re-raised to
        the caller, which is where this reviewer's non-fatal "no judgement
        exists" path lives — an unjudged report never ends the run.

        The raised copy is fresh telemetry rather than the caught object, so
        the traceback this reaches the ledger through does not carry the
        provider response the truncation was detected on.
        """
        try:
            reply = await self._structured_request(
                messages, schema, reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT
            )
        except ProviderOutputLimitError as retry_error:
            self._record_retry(error, schema, outcome="truncated")
            raise retry_error.redacted_copy(
                ProviderOutputLimitError.SAFE_MESSAGE
            ) from None
        except ProviderError as retry_error:
            # A retry that cannot produce a valid reply keeps its typed error:
            # schema failures and transport failures have different statuses
            # at the review boundary, and neither delivered a judgement.
            self._record_retry(error, schema, outcome="failed")
            raise retry_error.redacted_copy(str(retry_error)) from None
        self._record_retry(error, schema, outcome="answered")
        return reply

    def _record_retry(
        self,
        truncation: ProviderOutputLimitError,
        schema: type[Any],
        *,
        outcome: str,
    ) -> None:
        """Record this request's retry, under the outcome it had."""
        self._review_records.append(
            report_review_output_limit_retry(
                truncation,
                schema=schema.__name__,
                reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
                max_tokens=self._config.report_review_max_tokens,
                outcome=outcome,
            )
        )


async def review_report(
    provider: StructuredCompleter,
    packet: ReportReviewInput,
    *,
    tracker: Tracker | None = None,
    config: AgentRuntimeConfig | None = None,
    model_profile: EffectiveModelConfig | None = None,
    reviewer: ReportReviewer | None = None,
    reviewed_fingerprint: str | None = None,
) -> ReportReview:
    """Run one complete semantic review of ``packet`` and record its outcome.

    The review is one request carrying the complete report, every statement,
    the cited findings and the deterministic checks, plus one re-ask if the
    provider truncated it. The report is never re-requested and never clipped.

    A provider failure is ``provider_failed``; a reply that is malformed, that
    breaks the defect contract, or that cites a record this packet does not
    carry is ``incomplete``. Every one of those carries no dimension scores, so
    none of them can be averaged into an acceptance, and ``tracker`` is
    optional only so a unit test can call this without a session span.
    """
    owner = reviewer or ReportReviewer(
        provider=provider, tracker=tracker, config=config, model_profile=model_profile
    )

    async def _run() -> ReportReview:
        return await _review_packet(
            owner, packet, reviewed_fingerprint=reviewed_fingerprint
        )

    tracker_ = owner._tracker  # noqa: SLF001  (the reviewer's own tracker)
    if tracker_ is None:
        return await _run()
    async with tracker_.agent_span(owner.name):
        return await _run()


async def _review_packet(
    reviewer: ReportReviewer,
    packet: ReportReviewInput,
    *,
    reviewed_fingerprint: str | None,
) -> ReportReview:
    """The one review flow, separate so the span wraps all of it."""
    if not packet.reader_content.strip():
        return _merge_review(
            packet,
            dimension_scores=None,
            dispositions={},
            defects=[],
            derived_statements=[],
            rationale=(
                "There is no reader content to review, so no judgement of the "
                "report exists."
            ),
            status="incomplete",
        )
    if reviewed_fingerprint is not None and reviewed_fingerprint != packet.fingerprint:
        return _merge_review(
            packet,
            dimension_scores=None,
            dispositions={},
            defects=[],
            derived_statements=[],
            rationale=(
                "The packet changed under this review: the material judged is "
                f"not the material the review was opened on "
                f"({reviewed_fingerprint} != {packet.fingerprint})."
            ),
            status="incomplete",
        )

    try:
        reply = await reviewer._request(  # noqa: SLF001
            review_messages(packet), ReportReviewDraft
        )
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(
            packet, _schema_reason(error, ReportReviewDraft), status="incomplete"
        )
    except ProviderError as error:
        return _failed_review(packet, _provider_reason(error))
    except ReportReviewContractViolation as violation:
        return _failed_review(packet, str(violation), status="incomplete")

    try:
        limit = review_defect_limit(packet)
        if len(reply.defects) > limit:
            raise ReportReviewContractViolation(
                f"the reply returned {len(reply.defects)} defects, more than "
                f"the {limit} this request states; a reply beyond the bound is "
                "refused whole, never cut."
            )
        dispositions = _dispositions(reply.statement_dispositions, packet=packet)
        defects, notes = _defects(reply.defects, packet=packet)
    except ReportReviewContractViolation as violation:
        return _failed_review(packet, str(violation), status="incomplete")

    for statement_id in packet.expected_statement_ids:
        dispositions.setdefault(statement_id, UNREVIEWED_STATEMENT_DISPOSITION)

    derived, derived_statements = _derived_defects(packet, dispositions, defects)
    scores = reply.dimensions.as_dimensions()
    status = _status_for(packet, dimension_scores=scores, dispositions=dispositions)
    dimensions: dict[str, float] | None = scores
    rationale_parts = [
        reply.rationale.strip() or "The review returned no rationale.",
        *notes,
    ]
    if status != "scored":
        rationale_parts.append(
            _incomplete_reason(
                packet,
                unreviewed_statement_ids=[
                    statement_id
                    for statement_id in packet.expected_statement_ids
                    if dispositions[statement_id] == UNREVIEWED_STATEMENT_DISPOSITION
                ],
            )
        )
        dimensions = None
    rationale = " ".join(part for part in rationale_parts if part).strip()
    try:
        return _merge_review(
            packet,
            dimension_scores=dimensions,
            dispositions=dispositions,
            defects=[*defects, *derived],
            derived_statements=derived_statements,
            rationale=rationale,
            status=status,
        )
    except ValidationError as error:
        # The last boundary: assembling the record is the one remaining step
        # that can fail on the record contract, and this function is called
        # from a graph node with no exit code for it — a ``ValidationError``
        # raised here reached the CLI as an uncaught traceback, with the
        # finished report never published and neither exit 4 nor exit 3. A
        # judgement that cannot be recorded is recorded as *absent* instead:
        # ``incomplete``, no dimensions, the reason on the rationale, which is
        # the mandated quality-assessment failure and still routes nowhere.
        # The reply's own drafts are not the record's problem, so the review is
        # rebuilt from the packet rather than from the failed assembly.
        return _failed_review(
            packet,
            (
                "The review could not be recorded: the record contract refused "
                f"the assembled review ({type(error).__name__}). No judgement "
                "of this report is stored."
            ),
            status="incomplete",
        )


def _failed_review(
    packet: ReportReviewInput,
    reason: str,
    *,
    status: str = "provider_failed",
) -> ReportReview:
    """A review that could not be made: an explicit absence, never a score."""
    return _merge_review(
        packet,
        dimension_scores=None,
        dispositions={},
        defects=[],
        derived_statements=[],
        rationale=reason,
        status=status,
    )


def _provider_reason(error: Exception) -> str:
    return (
        "The review could not be made: the model provider failed "
        f"({type(error).__name__}). No judgement of this report exists."
    )


def _schema_reason(error: Exception, schema: type[BaseModel]) -> str:
    """Why the reply was refused: which field broke which constraint.

    Rendered from the typed diagnostics only -- schema-proven field paths,
    the category, and pydantic's error types -- so the record names the
    failing field without carrying a single character of the reply.
    """
    if isinstance(error, StructuredOutputError):
        diagnostics = error.diagnostics
    elif isinstance(error, ValidationError):
        diagnostics = (validation_diagnostic(error, attempt=1, schema=schema),)
    else:
        diagnostics = ()
    detail = "; ".join(diagnostic.render() for diagnostic in diagnostics)
    return (
        "The review could not be recorded: the reply did not satisfy the "
        f"{schema.__name__} contract ({type(error).__name__}"
        + (f": {detail}" if detail else "")
        + "). No judgement of this report exists."
    )


def review_defects_as_refinement_jobs(
    review: ReportReview | None,
) -> Iterator[ReviewDefect]:
    """The material defects of a scored review, in the order it returned them.

    Only a ``scored`` review routes: an incomplete or provider-failed review
    judged nothing, so acting on its (empty) defect list as "no defects" is the
    one reading that must never happen — and it cannot, because this yields
    nothing only for a review that found nothing.
    """
    if review is None or review.status != "scored":
        return iter(())
    return iter(review.material_defects)


# --- T5 addendum: scoped re-review after a redraft --------------------------
#
# The first review of a pass is always full. After a redraft (spec §6.9), a
# second full review would re-read and re-judge the whole report even though
# most of it did not change; this section builds a *scoped* re-review instead:
# the model still sees the whole report for context and coherence, but its
# disposition-judging and new-defect burden is limited to the parts the
# redraft actually touched, and the previous review's own defects are carried
# forward for it to resolve rather than re-discover.


class PreviousDefectView(ContractModel):
    """One defect the previous full review recorded, carried into a scoped
    re-review. ``coverage_ids`` names the part(s) it was routed to, so the
    reviewer can weigh a previous defect against the part its own redraft
    touched (or find it names none, when that part itself changed)."""

    defect_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    statement_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    coverage_ids: list[str] = Field(default_factory=list)
    problem: str = Field(min_length=1)


class ScopedReportReviewInput(ContractModel):
    """The packet a scoped re-review reads after a redraft.

    ``base`` is the ordinary full packet -- the same one a full review would
    read, built by :func:`build_report_review_input` -- so the reader content,
    the statements, the findings, the table and the deterministic checks are
    never duplicated in a second shape. This adds only what a *scoped* review
    needs on top: which statements changed, which carried over, the
    dispositions the previous review already settled for the carried-over
    ones, and the previous review's own defects for the model to resolve.

    Built only when every part the redrafted composition marks carried over
    verifies byte-identical to what the previous review judged
    (:func:`remap_review_for_redraft`); a caller that cannot build one falls
    back to a full review.
    """

    base: ReportReviewInput
    previous_defects: list[PreviousDefectView] = Field(default_factory=list)
    changed_statement_ids: list[str] = Field(default_factory=list)
    unchanged_statement_ids: list[str] = Field(default_factory=list)
    carried_dispositions: dict[str, StatementReviewDisposition] = Field(
        default_factory=dict
    )
    fingerprint: str = ""


def scoped_report_review_input_fingerprint(scoped: ScopedReportReviewInput) -> str:
    """The scoped packet's own fingerprint: distinct from
    ``scoped.base.fingerprint`` because the material a scoped re-review reads
    -- the changed/unchanged split and the carried previous defects -- is not
    the material a full review reads, even over an identical report."""
    payload = scoped.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _statement_shape(statement: ReportStatement | None) -> tuple[object, ...]:
    """A carried-over statement's own content, with its numbering excluded.

    Renumbering (``report_writer._renumber``) reassigns every statement id in
    render order on *every* pass, so a carried-over part's ids shift whenever
    an earlier part's point count changes even though its own words never
    moved. Comparing everything except the id is the honest "byte-identical"
    check the addendum asks for.
    """
    if statement is None:
        return ()
    return (
        statement.text,
        tuple(statement.finding_ids),
        tuple(statement.target_ids),
        tuple(
            (item.name, item.verdict, item.picked, item.source_url)
            for item in statement.items
        ),
    )


def _sections_content_equal(old: ReportSection, new: ReportSection) -> bool:
    """Whether ``new`` prints exactly what ``old`` printed, ids aside."""
    if old.title != new.title or len(old.points) != len(new.points):
        return False
    for old_point, new_point in zip(old.points, new.points):
        if old_point.text != new_point.text:
            return False
        if list(old_point.source_urls) != list(new_point.source_urls):
            return False
        if _statement_shape(old_point.statement) != _statement_shape(
            new_point.statement
        ):
            return False
    return True


def _redraft_diff(
    previous: ReportComposition, composition: ReportComposition
) -> tuple[list[str], list[str], dict[str, str]] | None:
    """Which statements changed, which carried over, and their id map.

    ``None`` when a part the redrafted composition marks ``carried_over`` is
    not byte-identical to what ``previous`` held for it: the re-review is not
    scoped, and the caller must fall back to a full review.
    """
    old_sections = {
        section.coverage_id: section
        for section in previous.sections
        if section.coverage_id
    }
    new_sections = {
        section.coverage_id: section
        for section in composition.sections
        if section.coverage_id
    }
    # The bottom line is always re-asked last (spec §6.9): always changed.
    changed: list[str] = [
        point.statement.statement_id
        for point in composition.summary
        if point.statement is not None
    ]
    unchanged: list[str] = []
    id_map: dict[str, str] = {}
    for part in composition.parts:
        new_section = new_sections.get(part.coverage_id)
        if part.status != "carried_over":
            if new_section is not None:
                changed.extend(
                    point.statement.statement_id
                    for point in new_section.points
                    if point.statement is not None
                )
            continue
        old_section = old_sections.get(part.coverage_id)
        if old_section is None or new_section is None:
            return None
        if not _sections_content_equal(old_section, new_section):
            return None
        for old_point, new_point in zip(old_section.points, new_section.points):
            if old_point.statement is not None and new_point.statement is not None:
                id_map[old_point.statement.statement_id] = (
                    new_point.statement.statement_id
                )
                unchanged.append(new_point.statement.statement_id)
    return changed, unchanged, id_map


def remap_review_for_redraft(
    previous_review: ReportReview,
    *,
    previous_composition: ReportComposition,
    composition: ReportComposition,
) -> ReportReview | None:
    """The prior review, carried onto the redrafted composition's own ids.

    ``None`` when nothing carried over, or a carried-over part is not
    byte-identical to what ``previous_review`` judged: the caller must ask a
    full review instead. The graph's writer node calls this right after
    composing a redraft, while both compositions are still local Python
    objects, so the reviewer node can later build a scoped packet without
    ever needing the prior composition itself.
    """
    if previous_review.status != "scored":
        return None
    diff = _redraft_diff(previous_composition, composition)
    if diff is None:
        return None
    _changed_ids, unchanged_ids, id_map = diff
    if not unchanged_ids:
        return None
    dispositions = {
        id_map[old_id]: disposition
        for old_id, disposition in previous_review.per_statement_dispositions.items()
        if old_id in id_map
    }
    if not dispositions:
        return None
    defects = [
        defect.model_copy(
            update={
                "statement_ids": [
                    id_map[statement_id]
                    for statement_id in defect.statement_ids
                    if statement_id in id_map
                ],
                "coverage_ids": _coverage_ids_for(defect, previous_composition),
            }
        )
        for defect in previous_review.defects
    ]
    return previous_review.model_copy(
        update={
            "per_statement_dispositions": dispositions,
            "defects": defects,
            "reviewed_statement_ids": sorted(dispositions),
            "unreviewed_statement_ids": [],
            "composition_fingerprint": composition_semantic_fingerprint(composition),
        }
    )


def _coverage_ids_for(defect: ReviewDefect, composition: ReportComposition) -> list[str]:
    """Which part(s) a defect named, from its statement and target ids."""
    coverage_by_statement = {
        point.statement.statement_id: section.coverage_id
        for section in composition.sections
        for point in section.points
        if point.statement is not None
    }
    coverage_by_target = {
        target.target_id: topic.coverage_id
        for topic in composition.sub_topics
        for target in topic.evidence_targets
    }
    ids = {coverage_by_statement.get(sid) for sid in defect.statement_ids}
    ids |= {coverage_by_target.get(tid) for tid in defect.target_ids}
    return sorted(coverage_id for coverage_id in ids if coverage_id)


def build_scoped_report_review_input(
    state: ResearchState,
    *,
    previous_review: ReportReview,
) -> ScopedReportReviewInput | None:
    """The scoped packet for a re-review after a redraft.

    ``previous_review`` must already be a carried-over review whose
    dispositions and defects were remapped onto the current composition's own
    statement ids by :func:`remap_review_for_redraft`: this function reads
    the ids that carried a disposition forward as the "unchanged" set, and
    every other statement in the packet as "changed". ``None`` when the
    composition carries no parts at all, or when nothing in fact changed.
    """
    composition = state.composition
    if composition is None or not composition.parts:
        return None
    unchanged_ids = set(previous_review.per_statement_dispositions)
    if not unchanged_ids:
        return None
    base = build_report_review_input(state, composition)
    unchanged_ordered = [
        statement_id
        for statement_id in base.expected_statement_ids
        if statement_id in unchanged_ids
    ]
    if not unchanged_ordered:
        # Every id ``previous_review`` carried a disposition for is foreign to
        # this packet -- nothing genuinely carried over, so a "scoped" review
        # would judge every statement anyway. Not scoped: fall back to full.
        return None
    changed_ids = [
        statement_id
        for statement_id in base.expected_statement_ids
        if statement_id not in unchanged_ids
    ]
    if not changed_ids:
        return None
    previous_defects = [
        PreviousDefectView(
            defect_id=defect.defect_id,
            kind=defect.kind,
            severity=defect.severity,
            statement_ids=list(defect.statement_ids),
            target_ids=list(defect.target_ids),
            coverage_ids=list(defect.coverage_ids),
            problem=defect.problem,
        )
        for defect in previous_review.defects
    ]
    scoped = ScopedReportReviewInput(
        base=base,
        previous_defects=previous_defects,
        changed_statement_ids=changed_ids,
        unchanged_statement_ids=unchanged_ordered,
        carried_dispositions={
            statement_id: previous_review.per_statement_dispositions[statement_id]
            for statement_id in unchanged_ordered
        },
    )
    return scoped.model_copy(
        update={"fingerprint": scoped_report_review_input_fingerprint(scoped)}
    )


# --- the scoped request ------------------------------------------------------


_SCOPED_REVIEW_ADDITION = (
    "\n"
    "\n"
    "This report was redrafted once, after your own previous review of it. "
    "The whole report is shown below, for context and cross-part coherence. "
    "Only some of it changed: the changed statement ids are named below, and "
    "so are the ids that did not change and are not being asked again.\n"
    "\n"
    "For each defect your previous review recorded, say whether the redraft "
    "resolved it or whether it remains. Judge every changed statement by "
    "exactly the standard above. Look for a new defect naming a statement id "
    "only among the changed ones, or in a contradiction a changed statement "
    "now introduces with an unchanged one; a defect that names no statement "
    "at all -- an unanswered obligation, a table or source problem -- is not "
    "restricted this way. Re-score the seven report-level dimensions for the "
    "whole report as it now stands."
)

SCOPED_REPORT_REVIEW_SYSTEM_PROMPT = REPORT_REVIEW_SYSTEM_PROMPT + _SCOPED_REVIEW_ADDITION
"""The scoped re-review's own developer message (T5 addendum).

Built as the full review's own system prompt plus a short scoped-rules
paragraph, rather than restated from scratch, so a changed statement is
judged by exactly the full review's standard -- what a disposition means,
the figure-label rules, the table mis-credit rule, "a fact that is not in
the findings you were shown is not established" -- and the scoped addition
states only what is different about this call.
"""

SCOPED_REPORT_REVIEW_INSTRUCTION = (
    "The reply carries these fields:\n"
    "- dimensions: seven re-scored scores in [0,1], one per named dimension, "
    "for the whole report as it now stands.\n"
    "- statement_dispositions: one entry per changed statement id you were "
    "shown, each with the statement id and its disposition (supported, "
    "unsupported, or not_reviewed).\n"
    "- previous_defect_resolutions: one entry per previous defect id you "
    "were shown, each with whether the redraft resolved it.\n"
    "- new_defects: any new typed defects you found, each naming the "
    "statement ids and target ids it affects; one naming only unchanged "
    "statement ids is refused, but a defect naming no statement at all -- an "
    "unanswered obligation, a table or source problem scoped only to a "
    "target or a fact row -- is never restricted this way.\n"
    "- rationale: why the report scores as it does now.\n"
    "Every id you cite must be one this request showed you. A changed "
    "statement you leave without a disposition is recorded not_reviewed. "
    "A disposition you return for an unchanged id is ignored."
)


_SCOPED_REVIEW_REPLY_EXAMPLES = (
    (
        "Example input: changed statement S004; unchanged statement S001; "
        "one previous defect review-01 against S001, since resolved because "
        "S004 now supplies the qualifier S001 was missing.",
        '{"dimensions":{"completeness":0.85,"prioritization":0.8,'
        '"evidence_quality":0.8,"attribution":0.8,"uncertainty":0.8,'
        '"readability":0.85,"actionability":0.8},'
        '"statement_dispositions":[{"statement_id":"S004","disposition":"supported",'
        '"problem":""}],'
        '"previous_defect_resolutions":[{"defect_id":"review-01","resolved":true,'
        '"note":"S004 now supplies the missing qualifier."}],'
        '"new_defects":[],'
        '"rationale":"The redraft closed review-01; nothing else changed."}',
    ),
)


def _render_scoped_defect_contract(scoped: ScopedReportReviewInput) -> str:
    return (
        f"{REVIEW_DEFECT_RULES}\n"
        "- A new defect naming only unchanged statement ids is refused; one "
        "naming no statement at all -- scoped only to a target, a fact row, "
        "or the report as a whole -- is never refused for that reason, and "
        "a coverage defect naming a required target is still treated as "
        "major or worse.\n"
        f"- Return at most {review_defect_limit(scoped.base)} new defects in "
        "total, on top of the previous defects you are resolving."
    )


def _render_previous_defects(scoped: ScopedReportReviewInput) -> str:
    lines: list[str] = []
    for defect in scoped.previous_defects:
        parts = [f"- {defect.defect_id} ({defect.kind}, {defect.severity})"]
        parts.append(f"  {defect.problem}")
        if defect.statement_ids:
            parts.append("  against: " + ", ".join(defect.statement_ids))
        if defect.coverage_ids:
            parts.append("  in part(s): " + ", ".join(defect.coverage_ids))
        lines.append("\n".join(parts))
    return "\n".join(lines) or "(the previous review recorded no defect)"


def scoped_review_messages(scoped: ScopedReportReviewInput) -> list[ChatMessage]:
    """The one request a scoped re-review makes after a redraft.

    The whole report and every statement are still shown whole, for context
    and cross-part coherence -- only the disposition-judging and new-defect
    burden is scoped to the changed statement ids. Every section a full
    review shows is reused verbatim here (P1: the scoped material must not
    drop the explanatory lines a full review's judgement rests on).
    """
    packet = scoped.base
    static = [
        f"# Response contract\n{SCOPED_REPORT_REVIEW_INSTRUCTION}\n\n"
        f"{_render_scoped_defect_contract(scoped)}",
        "# What each dimension means\n"
        "Score each dimension in [0,1] against its own definition:\n"
        + _render_dimension_guidance(),
        "# Reply format\n"
        + render_structured_reply_format(_SCOPED_REVIEW_REPLY_EXAMPLES),
    ]
    material = [
        f"# Research question\n{packet.question}",
        (
            "# Packet fingerprint\n"
            f"Packet fingerprint: {scoped.fingerprint}\n"
            "This is a scoped re-review of a redraft: the changed and "
            "unchanged statement ids and the previous defects below are the "
            "whole of what is scoped for this call; the report, statements "
            "and findings following them are shown for context."
        ),
        f"# Answer contract\n{_render_answer_contract(packet.answer_contract)}",
        (
            "# Reader content — the complete candidate\n"
            "The report is quoted in full below and nothing is removed from "
            "its end. This is the complete report a reader would receive, "
            "not a prefix.\n\n"
            f"{_report_fence(packet.reader_content)}{_REPORT_FENCE_INFO}\n"
            f"{packet.reader_content.rstrip()}\n"
            f"{_report_fence(packet.reader_content)}"
        ),
        (
            "# Changed statement ids\n"
            "Judge these, and look for any new contradiction they introduce "
            "with the rest of the report.\n"
            + (", ".join(scoped.changed_statement_ids) or "(none)")
        ),
        (
            "# Unchanged statement ids\n"
            "These carried over unchanged from the previous review and are "
            "not being asked again; raise a new defect against one only when "
            "a changed statement now contradicts it.\n"
            + (", ".join(scoped.unchanged_statement_ids) or "(none)")
        ),
        (
            "# Previous defects\n"
            "Say, for each one, whether the redraft resolved it.\n"
            + _render_previous_defects(scoped)
        ),
        _reader_statements_block(packet),
        _cited_findings_block(packet),
        _verified_figures_block(packet),
        _table_block(packet),
        _not_found_block(packet),
        _deterministic_block(packet),
        f"# Manifest of what you were shown\n{_render_manifest(packet)}",
    ]
    return [
        ChatMessage(role="developer", content=SCOPED_REPORT_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content=render_structured_request(static, material)),
    ]


# --- the scoped reply's own schema -------------------------------------------


class PreviousDefectResolutionDraft(ContractModel):
    """One provider-reported resolution of a previous defect."""

    defect_id: str = Field(min_length=1)
    resolved: bool
    note: str = ""


class ScopedReportReviewDraft(ContractModel):
    """One provider-reported scoped re-review, before local resolution.

    Its own schema name and fingerprint: a scoped re-review's reply shape is
    not a full review's, so the two can never be confused by a stored call
    fingerprint (``fingerprint_call`` keys on ``schema.__name__``).
    """

    dimensions: ReviewDimensionScores
    statement_dispositions: list[StatementDispositionDraft] = Field(
        default_factory=list
    )
    previous_defect_resolutions: list[PreviousDefectResolutionDraft] = Field(
        default_factory=list
    )
    new_defects: list[ReviewDefectDraft] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


# --- resolving a scoped reply into one review --------------------------------


def _merge_previous_defect_resolutions(
    previous_defects: Sequence[PreviousDefectView],
    resolutions: Sequence[PreviousDefectResolutionDraft],
    *,
    known_statement_ids: set[str],
) -> tuple[list[ReviewDefect], list[str], list[str]]:
    """Carry every previous defect forward, resolved or not.

    T5 addendum item 4: a resolved defect is recorded, not silently dropped
    -- ``ReviewDefect.resolution`` says which, and ``.material`` reads it, so
    a resolved defect no longer blocks acceptance but still shows in the
    merged review's own defect history. A previous defect the reply never
    mentions is kept unresolved: silence about a known problem is not the
    same as fixing it. A statement id a defect named that no longer exists
    in this packet (one of the parts the redraft itself rewrote) is dropped
    from its scope the same way an unknown id is dropped from a fresh
    defect -- the defect stays, addressed by whatever target and coverage
    ids it still carries.
    """
    resolved_by_id = {
        resolution.defect_id.strip(): resolution.resolved
        for resolution in resolutions
        if resolution.defect_id.strip()
    }
    carried: list[ReviewDefect] = []
    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []
    for defect in previous_defects:
        resolved = bool(resolved_by_id.get(defect.defect_id))
        if resolved:
            resolved_ids.append(defect.defect_id)
        else:
            unresolved_ids.append(defect.defect_id)
        carried.append(
            ReviewDefect(
                defect_id=defect.defect_id,
                kind=cast(GapKind, defect.kind),
                severity=cast(GapSeverity, defect.severity),
                statement_ids=[
                    statement_id
                    for statement_id in defect.statement_ids
                    if statement_id in known_statement_ids
                ],
                target_ids=list(defect.target_ids),
                coverage_ids=list(defect.coverage_ids),
                problem=defect.problem,
                resolution="resolved" if resolved else "unresolved",
            )
        )
    return carried, resolved_ids, unresolved_ids


def _scoped_new_defects(
    drafts: Sequence[ReviewDefectDraft],
    *,
    scoped: ScopedReportReviewInput,
    existing_ids: set[str],
) -> tuple[list[ReviewDefect], list[str]]:
    """New defects, refused only when every statement id they name is
    unchanged.

    T5 addendum: "new defects are accepted only in changed parts, or when
    they cite a contradiction with a changed part" -- a defect naming a
    changed id together with the unchanged one it contradicts still names a
    changed id, so this one rule covers both. A defect that names *no*
    statement at all -- a required-target coverage gap (D11), a table or
    source mis-credit scoped only to fact-row ids, a report-level problem --
    is not restricted by this rule at all: it goes through :func:`_defects`
    exactly as a full review's would, D11 floor included, because there is
    no "which statement" for the scoping rule to apply to.
    """
    defects, notes = _defects(drafts, packet=scoped.base)
    changed = set(scoped.changed_statement_ids)
    kept: list[ReviewDefect] = []
    next_index = 1
    for defect in defects:
        if defect.statement_ids and not (set(defect.statement_ids) & changed):
            notes.append(
                f"A new defect ({defect.kind}, naming "
                f"{', '.join(defect.statement_ids)}) was dropped: a scoped "
                "re-review may not raise a new defect naming only unchanged "
                "statements."
            )
            continue
        candidate = f"review-{next_index:02d}"
        while candidate in existing_ids:
            next_index += 1
            candidate = f"review-{next_index:02d}"
        existing_ids.add(candidate)
        next_index += 1
        kept.append(defect.model_copy(update={"defect_id": candidate}))
    return kept, notes


async def review_scoped_report(
    provider: StructuredCompleter,
    scoped: ScopedReportReviewInput,
    *,
    tracker: Tracker | None = None,
    config: AgentRuntimeConfig | None = None,
    model_profile: EffectiveModelConfig | None = None,
    reviewer: ReportReviewer | None = None,
) -> ReportReview:
    """Run one scoped re-review of ``scoped`` and record its outcome."""
    owner = reviewer or ReportReviewer(
        provider=provider, tracker=tracker, config=config, model_profile=model_profile
    )

    async def _run() -> ReportReview:
        return await _review_scoped_packet(owner, scoped)

    tracker_ = owner._tracker  # noqa: SLF001  (the reviewer's own tracker)
    if tracker_ is None:
        return await _run()
    async with tracker_.agent_span(owner.name):
        return await _run()


async def _review_scoped_packet(
    reviewer: ReportReviewer,
    scoped: ScopedReportReviewInput,
) -> ReportReview:
    """The scoped review flow, separate so the span wraps all of it."""
    packet = scoped.base
    if not packet.reader_content.strip():
        return _merge_review(
            packet,
            dimension_scores=None,
            dispositions={},
            defects=[],
            derived_statements=[],
            rationale=(
                "There is no reader content to review, so no judgement of "
                "the report exists."
            ),
            status="incomplete",
        )
    try:
        reply = await reviewer._request(  # noqa: SLF001
            scoped_review_messages(scoped), ScopedReportReviewDraft
        )
    except (StructuredOutputError, ValidationError) as error:
        return _failed_review(
            packet, _schema_reason(error, ScopedReportReviewDraft), status="incomplete"
        )
    except ProviderError as error:
        return _failed_review(packet, _provider_reason(error))

    try:
        limit = review_defect_limit(scoped.base)
        if len(reply.new_defects) > limit:
            raise ReportReviewContractViolation(
                f"the reply returned {len(reply.new_defects)} new defects, "
                f"more than the {limit} this request states; a reply beyond "
                "the bound is refused whole, never cut."
            )
        # A disposition the reply returns for an unchanged id is dropped
        # here, never allowed to overwrite the carried reading below: the
        # scoped prompt's own promise is that an unchanged id is "not being
        # asked again," and the base prompt's "leave no statement id out"
        # is a rule about the ids this call is judging, not about every id
        # the whole-report packet merely shows for context.
        changed = set(scoped.changed_statement_ids)
        changed_dispositions = {
            statement_id: disposition
            for statement_id, disposition in _dispositions(
                reply.statement_dispositions, packet=packet
            ).items()
            if statement_id in changed
        }
    except ReportReviewContractViolation as violation:
        return _failed_review(packet, str(violation), status="incomplete")

    carried_defects, resolved_ids, unresolved_ids = (
        _merge_previous_defect_resolutions(
            scoped.previous_defects,
            reply.previous_defect_resolutions,
            known_statement_ids=set(packet.expected_statement_ids),
        )
    )
    existing_ids = {defect.defect_id for defect in carried_defects}
    new_defects, defect_notes = _scoped_new_defects(
        reply.new_defects, scoped=scoped, existing_ids=existing_ids
    )

    dispositions: dict[str, StatementReviewDisposition] = dict(
        scoped.carried_dispositions
    )
    dispositions.update(changed_dispositions)
    for statement_id in packet.expected_statement_ids:
        dispositions.setdefault(statement_id, UNREVIEWED_STATEMENT_DISPOSITION)

    all_defects = [*carried_defects, *new_defects]
    derived, derived_statements = _derived_defects(packet, dispositions, all_defects)

    scores = reply.dimensions.as_dimensions()
    status = _status_for(packet, dimension_scores=scores, dispositions=dispositions)
    dimensions: dict[str, float] | None = scores
    rationale_parts = [
        reply.rationale.strip() or "The re-review returned no rationale.",
        *defect_notes,
    ]
    if resolved_ids:
        rationale_parts.append(
            "Resolved by the redraft: " + ", ".join(sorted(resolved_ids)) + "."
        )
    if unresolved_ids:
        rationale_parts.append(
            "Still unresolved: " + ", ".join(sorted(unresolved_ids)) + "."
        )
    if status != "scored":
        rationale_parts.append(
            _incomplete_reason(
                packet,
                unreviewed_statement_ids=[
                    statement_id
                    for statement_id in packet.expected_statement_ids
                    if dispositions[statement_id] == UNREVIEWED_STATEMENT_DISPOSITION
                ],
            )
        )
        dimensions = None
    rationale = " ".join(part for part in rationale_parts if part).strip()
    try:
        return _merge_review(
            packet,
            dimension_scores=dimensions,
            dispositions=dispositions,
            defects=[*all_defects, *derived],
            derived_statements=derived_statements,
            rationale=rationale,
            status=status,
        )
    except ValidationError as error:
        return _failed_review(
            packet,
            (
                "The review could not be recorded: the record contract "
                f"refused the assembled review ({type(error).__name__}). No "
                "judgement of this report is stored."
            ),
            status="incomplete",
        )

__all__ = [
    "DIMENSION_GUIDANCE",
    "MAX_REVIEW_DEFECTS",
    "PreviousDefectResolutionDraft",
    "PreviousDefectView",
    "REPORT_REVIEWER_ROLE",
    "REPORT_REVIEW_INSTRUCTION",
    "REPORT_REVIEW_OPERATION",
    "REPORT_REVIEW_PROMPT_VERSION",
    "REPORT_REVIEW_SYSTEM_PROMPT",
    "REVIEW_DIMENSIONS",
    "REVIEW_DEFECT_RULES",
    "REVIEW_RUBRIC_VERSION",
    "SCOPED_REPORT_REVIEW_INSTRUCTION",
    "SCOPED_REPORT_REVIEW_SYSTEM_PROMPT",
    "SEMANTIC_REVIEW_MEAN",
    "ReportReviewContractViolation",
    "ReportReviewDraft",
    "ReportReviewInput",
    "ReportReviewer",
    "ReviewDefectDraft",
    "ReviewDeterministic",
    "ReviewDimensionScores",
    "ReviewFindingView",
    "ReviewStatementView",
    "ScopedReportReviewDraft",
    "ScopedReportReviewInput",
    "StatementDispositionDraft",
    "build_report_review_input",
    "build_scoped_report_review_input",
    "composition_semantic_fingerprint",
    "remap_review_for_redraft",
    "report_review_input_fingerprint",
    "review_defect_limit",
    "review_defects_as_refinement_jobs",
    "review_messages",
    "review_report",
    "review_scoped_report",
    "scoped_report_review_input_fingerprint",
    "scoped_review_messages",
    "semantic_review_passes",
]
