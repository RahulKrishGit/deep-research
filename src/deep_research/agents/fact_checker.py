"""The Fact Checker: extract major claims and verify them independently.

The provider is asked for ``ClaimsDraft`` and ``ClaimVerdictDraft``, never
for ``Claim`` itself: ``Claim`` declares ``Field(min_length=1)`` and
``UnitScore`` constraints that strict structured outputs reject. Local code
stamps the parts the model must not be trusted with — which source URLs
actually exist, which retrieved domains are independent of the claim, and
the final verdict when the model's answer conflicts with the evidence it
reported.

Verdict convention: the four ``ClaimVerdict`` strings, and only those.
``insufficient_evidence`` is the honest default whenever nothing
independent was retrieved, and is never a way of expressing "probably
true".
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from deep_research.agents.acquisition import (
    AcquisitionState,
    ToolPolicyDecision,
    admit_read_result,
    build_acquisition_context,
    build_boundary_audit,
    select_relevant_passages,
)
from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.claim_clusters import (
    LEGACY_COVERAGE_DIMENSION,
    ClaimConsolidation,
    _VALUE_UNIT_PATTERN,
    _canonical_unit,
    _claim_issuer,
    _split_clauses,
    atom_answers_target,
    claim_cluster_id,
    claim_meets_support_policy,
    consolidate_claims,
    critical_target_ids,
    extract_text_atoms,
    resolved_verdict_and_status,
    select_claim_batch_indices,
    target_order_for,
)
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import (
    EvidenceEligibility,
    _identity_words,
    canonical_read_text,
    eligible_independent_pair,
    resolve_source_identities,
    shares_lineage,
    source_origin_id,
)
from deep_research.agents.identity import (
    claim_fingerprint,
    deduplicate_findings,
    finding_fingerprint,
    merge_claim_snapshot,
)
from deep_research.agents.prompts import (
    ADJUDICATION_DEPENDENCE_INSTRUCTION,
    ADJUDICATION_INSTRUCTION,
    CLAIM_EXTRACTION_INSTRUCTION,
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_VERIFICATION_INSTRUCTION,
    CLAIM_VERIFICATION_SYSTEM_PROMPT,
    FACT_CHECKER_SYSTEM_PROMPT,
    AgentTask,
    render_finding_digest,
    render_source_quality,
    render_structured_reply_format,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.researcher import merge_react_runs, render_evidence
from deep_research.agents.source_evaluator import assess_new_sources
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import (
    ReActDecision,
    ReActRun,
    ReActStep,
    read_evidence_urls,
    summarize_text,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderError
from deep_research.providers.contracts import (
    StructuredOutputError,
    StructuredRepairRecord,
)
from deep_research.tools.base import BaseTool
from deep_research.tools.passage_selection import (
    _expanded_terms,
    _score,
    _tokens,
)
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    MAX_CONSUMED_COVERAGE_IDS,
    MAX_CONSUMED_FINDING_FINGERPRINTS,
    BoundaryAudit,
    Claim,
    ClaimCluster,
    ClaimProvenance,
    ClaimVerdict,
    ConflictAssessment,
    ContractModel,
    EvidenceDisposition,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    ReadRecord,
    RefinementTarget,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    _ENERGY_UNIT,
    _POWER_UNIT,
)

FACT_CHECKER_NAME = "fact_checker"
# The legacy name for the claim batch bound. It used to be a hidden prefix —
# the sixth claim an extraction returned simply did not exist, and every
# measured extraction batch accepted exactly five — and it is now
# ``agents.claim_batch_size``. The constant is kept, and kept equal to that
# setting's default, so existing callers and snapshots name the same number.
DEFAULT_MAX_CLAIMS = 5
DEFAULT_CLAIM_BATCH_SIZE = DEFAULT_MAX_CLAIMS
# How many batches one pass runs. The pass's total allowance is the product of
# the two bounds; everything beyond it stays pending and is reported.
DEFAULT_CLAIM_BATCHES_PER_PASS = 6
# How much unadjudicated claim work one pass keeps in its resume window. The
# bound is explicit because ``ClaimsDraft.claims`` is not: nothing about the
# provider's reply limits how many claims it may carry. Work past the window is
# *not* dropped — it is persisted as deferred and resumed once the window
# drains, so the bound never deletes anything.
MAX_PENDING_CLAIMS = 64
DEFAULT_FINDING_DIGEST = 40
# How many pending claim identities one event reports. The count is exact; the
# identities are bounded so an event never grows with the queue.
MAX_REPORTED_PENDING_CLAIMS = 16
# Named distinctly from researcher.DEFAULT_EVIDENCE_CHARS: both are
# re-exported from deep_research.agents, so the names must not collide.
#
# How much evidence one adjudication request carries, in characters. Raised
# from 4000 once the claim-specific pool was narrowed to the passages that bear
# on the claim: the audited run's linked passages are ~900 characters each, so
# 4000 held five of them and deferred the rest — and a deferred candidate keeps
# a pair from settling, which made ``verified_pair`` unreachable for exactly the
# obligations the narrowing was meant to serve. Replaying the rank-4 acceptance
# state (the finalize_report run's input) at 8000: claims 1, 2 and 11 carry
# their whole pool — 9, 6 and 6 units, zero deferred — and settle verified,
# where 4000 deferred 4, 1 and 1. The bounded candidate list is what keeps the
# cost of the larger request from growing with the registry.
FACT_CHECK_EVIDENCE_CHARS = 8000
MAX_PASSAGE_EXCERPT_CHARS = 1000

# How long a candidate's locator may be in a rendered block. Locators are
# project-minted (``chunk-3``, ``page-12-chunk-0``), so this is a shape bound,
# not a content one.
MAX_PASSAGE_LOCATOR_CHARS = 200
# How many distinct schema field paths one repaired-reply event reports. The
# diagnostic count is exact; the paths are bounded so an event never grows with
# a malformed reply.
MAX_REPORTED_FIELD_PATHS = 8
# How many exact passages one verifier read contributes to its packet, matching
# the Researcher's own default selection bound.
DEFAULT_PASSAGES_PER_READ = 4
# How many omitted ids and handoff ids one persisted adjudication manifest
# carries. The counts are exact; the id lists are bounded so a 300-document
# registry cannot write a 300-row manifest per claim, and the overflow is
# summarized rather than dropped.
MAX_PACKET_OMISSIONS = 16

# The tools that read a body. A refusal is remembered per URL for the whole
# pass, whichever reader met it.
_READ_TOOLS = frozenset({"web_scraper", "document_reader"})


class ClaimDraft(ContractModel):
    """One model-extracted claim, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``verdict``, ``confidence``, ``evidence``, and
    ``contradictions`` are deliberately absent — extraction proposes
    claims, verification judges them.
    """

    text: str
    source_urls: list[str]


class ClaimsDraft(ContractModel):
    """The provider-facing claim-extraction schema."""

    claims: list[ClaimDraft]


# One example: the extraction contract above already states the empty-list
# case, and an empty list is not the opposite end of a scale.
_CLAIM_EXTRACTION_REPLY_EXAMPLES = (
    (
        "Example input: a finding from "
        "https://evidence.example.test/report states that the measured "
        "reduction was 12 percent.",
        '{"claims":[{"text":"The example report measured a 12 percent '
        'reduction.","source_urls":["https://evidence.example.test/report"]}]}',
    ),
)


class ClaimTask(AgentTask):
    """An ``AgentTask`` bound to the claim its loop verifies.

    Carrying the claim on the task is what lets ``finalize(task, run)``
    know which claim it is finalizing without the agent holding mutable
    state across await points. The two provenance fields carry what this
    claim's extraction pass attributed to it, so the verified ``Claim``
    records the evidence it consumed, and ``target_ids`` carries the planned
    obligations it answers.
    """

    claim: ClaimDraft
    claimed_domains: list[str] = []
    consumed_finding_fingerprints: list[str] = []
    consumed_coverage_ids: list[str] = []
    target_ids: list[str] = []
    target_policies: dict[str, str] = Field(default_factory=dict)
    """The support policy of each target this claim reached for.

    Carried so the policy is enforced *after* adjudication, when the evidence
    the claim actually rests on exists.
    """

    packet: AdjudicationPacket | None = None
    """The claim-specific evidence packet this claim is adjudicated against.

    ``None`` for a run with no read registry behind it at all, which keeps the
    legacy passage path total. A packet with no units is *not* the same thing:
    it is a claim whose claim-specific union is empty, and it is adjudicated as
    ``no_candidate`` rather than by re-opening a global URL pool.
    """

    retrieval_needed: bool = True
    """False when the packet already carries a qualifying independent pair.

    A sufficient upstream pair needs no new retrieval (Section 2.1), so the
    loop is skipped rather than spending tool calls to re-read what the run
    already has.
    """


class ClaimAttribution(ContractModel):
    """What one extraction pass attributed to one accepted claim draft."""

    consumed_finding_fingerprints: list[str] = []
    consumed_coverage_ids: list[str] = []
    target_ids: list[str] = []
    target_policies: dict[str, str] = Field(default_factory=dict)
    provenance: ClaimProvenance = Field(default_factory=ClaimProvenance)
    """Where this claim's figure comes from, read from the finding it states.

    Carried here rather than re-derived at adjudication: the pass that read
    the findings is the pass that knows which one this claim's figure came
    from, and the verdict must record the same attribution the extraction
    made.
    """


class PendingClaim(ContractModel):
    """One extracted claim a pass did not adjudicate.

    Reported so a caller can see the work that exists rather than infer it
    from a count: a claim only ever *placed in a prompt* has consumed
    nothing, and its absence from the verified snapshot must read as pending,
    not as done. ``deferred`` distinguishes the claims in the resume window
    from the overflow past it — both are pending, and neither is deleted.
    """

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    deferred: bool = False


class VerifiedClaims(ContractModel):
    """The validated claims ``FactCheckerAgent`` produces.

    Never sent to the provider — ``ClaimsDraft`` and ``ClaimVerdictDraft``
    are. Do not route this agent through ``complete_output``.
    """

    claims: list[Claim] = []
    pending_claims: list[PendingClaim] = []
    """The continuation queue: extracted, not adjudicated, and not consumed."""


def known_source_urls(state: ResearchState) -> list[str]:
    """Every canonical source URL the findings actually used, in order."""
    seen: list[str] = []
    for finding in state.raw_findings:
        url = normalize_source_url(finding.source_url)
        if url not in seen:
            seen.append(url)
    return seen


def _collapsed(value: str) -> str:
    return " ".join(value.split()).casefold()


def _critique_texts(state: ResearchState) -> tuple[str, ...]:
    critique = state.critique
    if critique is None:
        return ()
    return tuple(
        text
        for text in (
            *(gap.problem for gap in critique.gaps),
            *critique.unsupported_claims,
            *critique.recommended_queries,
        )
        if isinstance(text, str) and text.strip()
    )


def _critique_requests_reverification(
    claim_text: str, critique_texts: Sequence[str]
) -> bool:
    """Recognize an explicit Critic request tied to this claim."""
    claim = _collapsed(claim_text)
    if not claim:
        return False
    markers = ("reverify", "re-verify", "re verify", "recheck", "re-check")
    return any(
        marker in _collapsed(text) and claim in _collapsed(text)
        for text in critique_texts
        for marker in markers
    )


def _finding_is_new(finding: Finding, prior_claims: Sequence[Claim]) -> bool:
    """True unless a prior claim recorded consuming this exact finding.

    Persisted provenance is the only authority. Claim extraction is
    explicitly allowed to rewrite and merge findings into self-contained
    claims, so comparing a raw finding's prose with a claim's text, evidence,
    contradictions, or verification excerpts could never tell an unchanged
    origin finding from one that arrived with changed content. A prior claim
    carrying no recorded provenance — a fixture, or a snapshot written
    before provenance existed — proves nothing and therefore suppresses
    nothing: the failure direction is extra work, never skipped evidence.
    """
    return (
        finding_fingerprint(finding)
        not in _recorded_finding_fingerprints(prior_claims)
    )


def _recorded_finding_fingerprints(prior_claims: Sequence[Claim]) -> set[str]:
    """Every origin-finding identity the prior snapshot recorded consuming."""
    return {
        fingerprint
        for claim in prior_claims
        for fingerprint in claim.consumed_finding_fingerprints
    }


def _recorded_coverage_ids(prior_claims: Sequence[Claim]) -> set[str]:
    """Every planned coverage id the prior snapshot recorded consuming."""
    return {
        coverage_id
        for claim in prior_claims
        for coverage_id in claim.consumed_coverage_ids
    }


def _append_unique(values: list[str], value: str) -> None:
    """Keep first-seen order, so provenance is a deterministic function."""
    if value and value not in values:
        values.append(value)


def coverage_ids_by_title(state: ResearchState) -> dict[str, str]:
    """Map each planned sub-topic's collapsed title to its coverage id."""
    coverage_ids: dict[str, str] = {}
    for topic in state.sub_topics:
        coverage_ids.setdefault(_collapsed(topic.title), topic.coverage_id)
    return coverage_ids


def evidence_targets_by_title(
    state: ResearchState,
) -> dict[str, list[EvidenceTarget]]:
    """Map each planned sub-topic's collapsed title to its evidence targets.

    The claim scheduler and the attribution check both read these, so a claim
    can be credited with an obligation only when its own prose answers that
    obligation's required dimensions and support policy. A plan written before
    the target inventory existed states its obligations as coverage ids, and
    gets one synthesized target per topic carrying a dimension this contract
    cannot check — which is exactly what keeps such a plan schedulable without
    inventing a dimension the model never declared.
    """
    targets: dict[str, list[EvidenceTarget]] = {}
    for topic in state.sub_topics:
        key = _collapsed(topic.title)
        if key in targets:
            continue
        if topic.evidence_targets:
            targets[key] = list(topic.evidence_targets)
            continue
        targets[key] = [
            EvidenceTarget(
                target_id=topic.coverage_id,
                coverage_id=topic.coverage_id,
                question=topic.title,
                required_dimensions=[LEGACY_COVERAGE_DIMENSION],
                required=True,
                critical=False,
                support_policy="independent_pair",
            )
        ]
    return targets


def target_ids_by_title(state: ResearchState) -> dict[str, list[str]]:
    """The target ids behind each planned sub-topic, in plan order."""
    return {
        key: [target.target_id for target in targets]
        for key, targets in evidence_targets_by_title(state).items()
    }


def obligation_question(state: ResearchState) -> str:
    """The question a target's dimensions are judged against.

    The frozen answer contract's question when a plan has been produced —
    Section 2.3 freezes it, and a later refinement may not re-anchor what the
    obligations mean — and the run's own question before that.
    """
    contract = state.answer_contract
    if contract is not None:
        return contract.question
    return state.original_question


def _attributed_findings(
    draft: ClaimDraft,
    *,
    findings: Sequence[Finding],
) -> dict[str, Finding]:
    """The one highest-priority finding each cited URL is attributed to.

    A URL alone cannot tell two findings apart, so the first candidate in the
    pass's extraction order consumes it and the rest stay unconsumed — which
    is what keeps an untouched second coverage topic sharing one source URL
    reported as uncovered instead of covered by URL overlap.
    """
    cited = {normalize_source_url(url) for url in draft.source_urls}
    attributed: dict[str, Finding] = {}
    for finding in findings:
        url = normalize_source_url(finding.source_url)
        if url in cited and url not in attributed:
            attributed[url] = finding
    return attributed


def consumed_provenance(
    draft: ClaimDraft,
    *,
    findings: Sequence[Finding],
    coverage_ids: Mapping[str, str],
) -> tuple[list[str], list[str]]:
    """The origin findings and coverage ids one extracted claim consumed.

    Attribution is per consumed FINDING, never per cited URL, because a URL
    alone cannot tell two findings apart: when several candidates in this
    pass carry the same URL, the single highest-priority candidate in the
    pass's extraction order consumes it and the rest stay unconsumed. That is
    what keeps an untouched second coverage topic sharing one source URL
    reported as uncovered instead of being covered by URL overlap.

    ``coverage_ids`` maps a collapsed sub-topic title to the coverage id the
    Planner stamped for it. A finding whose title matches no planned topic
    contributes its fingerprint but no coverage id, so it can never mark an
    unrelated topic covered.

    Both lists are bounded, and an identity that does not fit is treated as
    not consumed — extra work, never skipped evidence.
    """
    fingerprints: list[str] = []
    consumed_coverage: list[str] = []
    for finding in _attributed_findings(draft, findings=findings).values():
        _append_unique(fingerprints, finding_fingerprint(finding))
        coverage_id = coverage_ids.get(_collapsed(finding.related_sub_topic))
        if coverage_id:
            _append_unique(consumed_coverage, coverage_id)
    return (
        fingerprints[:MAX_CONSUMED_FINDING_FINGERPRINTS],
        consumed_coverage[:MAX_CONSUMED_COVERAGE_IDS],
    )


def claim_provenance_for(
    draft: ClaimDraft, *, findings: Sequence[Finding]
) -> ClaimProvenance:
    """The provenance of the one finding this claim's figure came from.

    A page can state several figures — EIA's March 2025 release carries both
    the 2024 additions and the 2025 forecast — and each has its own period,
    edition and release date, so provenance is read from the finding whose own
    text carries a figure this claim states and from no other. Where two
    findings carry the same figure the attribution is ambiguous and nothing is
    copied: a guess here would print an edition and a release date beside a
    figure that did not come from it.
    """
    cited = _cited_findings(draft, findings=findings)
    if not cited:
        return ClaimProvenance()
    wanted = set(_NUMBER.findall(draft.text))
    scored = [
        (len(wanted.intersection(_NUMBER.findall(finding.content))), position)
        for position, finding in enumerate(cited)
    ]
    best = max(score for score, _ in scored)
    if best == 0:
        # No figure to choose by: one cited finding is still unambiguous.
        chosen = cited[0] if len(cited) == 1 else None
    else:
        winners = [position for score, position in scored if score == best]
        chosen = cited[winners[0]] if len(winners) == 1 else None
    if chosen is None:
        return ClaimProvenance()
    return ClaimProvenance(
        attributed_issuer=chosen.attributed_issuer,
        measure_scope=chosen.measure_scope,
        vintage=chosen.vintage,
        statement_date=chosen.statement_date,
        release_date=chosen.release_date,
        data_period=chosen.data_period,
    )


def claim_attribution(
    draft: ClaimDraft,
    *,
    findings: Sequence[Finding],
    coverage_ids: Mapping[str, str],
    targets: Mapping[str, Sequence[EvidenceTarget]],
    question: str,
) -> ClaimAttribution:
    """Everything one extraction pass attributes to one accepted claim.

    ``consumed_provenance`` answers what the claim already consumed;
    ``target_ids`` answers which planned obligations it actually answers, which
    is what the claim scheduler reads. An obligation is earned, not inherited
    from the topic a finding came from: the claim's own prose has to satisfy
    that target's required dimensions and support policy (Section 2.3), so a
    claim can never be credited with an obligation it does not meet. Both are
    derived from the same consumed findings, so a claim can never answer a
    topic it cited nothing from either.

    The candidates a claim's prose is checked against start with the planned
    targets its findings named — the researcher's extraction keeps that
    binding, and it is what lets a read fetched for one topic answer another
    topic's target. Every finding of a cited URL contributes, not only the one
    attribution consumes: a page can state several findings bound to different
    targets, and the claim's prose may be answered by any of them. The
    findings' own sub-topics stay a candidate source too, so a finding
    extracted before the binding existed (one carrying no target ids) is
    judged exactly as it was, and a named target the plan does not contain
    contributes nothing: only the plan can issue an obligation.
    """
    fingerprints, consumed_coverage = consumed_provenance(
        draft, findings=findings, coverage_ids=coverage_ids
    )
    atoms = extract_text_atoms(draft.text)
    obligations: list[str] = []
    policies: dict[str, str] = {}
    planned = {
        target.target_id: target
        for group in targets.values()
        for target in group
    }
    candidates = _candidate_targets(
        _cited_findings(draft, findings=findings),
        targets=targets,
        planned=planned,
    )
    for target in candidates:
        if any(
            atom_answers_target(atom, target, question=question)
            for atom in atoms
        ):
            _append_unique(obligations, target.target_id)
            policies.setdefault(target.target_id, target.support_policy)
    return ClaimAttribution(
        consumed_finding_fingerprints=fingerprints,
        consumed_coverage_ids=consumed_coverage,
        target_ids=obligations,
        target_policies={
            target_id: policies[target_id] for target_id in obligations
        },
        provenance=claim_provenance_for(draft, findings=findings),
    )


def _cited_findings(
    draft: ClaimDraft, *, findings: Sequence[Finding]
) -> list[Finding]:
    """Every finding whose URL the claim cites, in the order given.

    Deliberately not one per URL. ``_attributed_findings`` keeps one finding
    per cited URL for *provenance*, which is what stops URL overlap from
    marking a second topic covered, but a page can state several findings: EIA
    64705 carries both the 2024 addition and the 19.6 GW forecast, and a claim
    about the forecast is answered by the second of them. Seeding candidates
    from the first alone left the forecast target out of the very set the
    claim's prose is checked against, which is the audited case this slice
    exists for.
    """
    cited = {normalize_source_url(url) for url in draft.source_urls}
    return [
        finding
        for finding in findings
        if normalize_source_url(finding.source_url) in cited
    ]


def _candidate_targets(
    cited_findings: Sequence[Finding],
    *,
    targets: Mapping[str, Sequence[EvidenceTarget]],
    planned: Mapping[str, EvidenceTarget],
) -> list[EvidenceTarget]:
    """The plan targets one claim's cited findings make it eligible for.

    The findings' named targets lead, because they are the specific
    obligations their content answers; the targets of the sub-topics that
    fetched their reads follow, which is the whole candidate set before
    findings carried a binding. Ids resolve through the plan, so a target id
    no plan issued is simply absent rather than invented.
    """
    candidates: list[EvidenceTarget] = []
    seen: set[str] = set()
    for finding in cited_findings:
        for target_id in finding.target_ids:
            target = planned.get(target_id)
            if target is None or target.target_id in seen:
                continue
            seen.add(target.target_id)
            candidates.append(target)
    for finding in cited_findings:
        for target in targets.get(_collapsed(finding.related_sub_topic), ()):
            if target.target_id in seen:
                continue
            seen.add(target.target_id)
            candidates.append(target)
    return candidates


def ordered_findings_for_extraction(
    state: ResearchState,
    *,
    prior_claims: Sequence[Claim] = (),
) -> list[Finding]:
    """Return unique findings in coverage- and verification-aware order."""
    findings = deduplicate_findings(state.raw_findings)
    topic_ranks: dict[str, int] = {}
    topic_ids: dict[str, str] = {}
    for position, topic in enumerate(state.sub_topics):
        key = _collapsed(topic.title)
        topic_ranks.setdefault(key, position)
        topic_ids.setdefault(key, topic.coverage_id)

    source_risk = {
        normalize_source_url(source.url)
        for source in state.evaluated_sources
        if source.low_confidence
    }
    prior_unsettled_sources = {
        normalize_source_url(url)
        for claim in prior_claims
        if claim.verdict in {"unverified", "insufficient_evidence"}
        for url in claim.source_urls
    }
    # A topic is covered iff a prior claim RECORDED consuming its coverage id.
    # URL overlap is deliberately not coverage: two planned topics can share
    # one source URL, and checking one of them must not silently cover the
    # other and strip it of its priority.
    covered_keys = {
        key
        for key, coverage_id in topic_ids.items()
        if coverage_id in _recorded_coverage_ids(prior_claims)
    }
    indexed = list(enumerate(findings))

    def ordering(item: tuple[int, Finding]) -> tuple[int, int, int]:
        index, finding = item
        key = _collapsed(finding.related_sub_topic)
        topic_rank = topic_ranks.get(key, len(topic_ranks))
        if key not in covered_keys:
            bucket = 0
        elif _finding_is_new(finding, prior_claims):
            bucket = 1
        elif (
            finding.confidence < 0.5
            or normalize_source_url(finding.source_url) in source_risk
            or normalize_source_url(finding.source_url)
            in prior_unsettled_sources
        ):
            bucket = 2
        else:
            bucket = 3
        return bucket, topic_rank, index

    indexed.sort(key=ordering)
    return [finding for _, finding in indexed]


def _job_requests_adjudication(
    claim: Claim,
    jobs: Sequence[RefinementTarget],
) -> bool:
    """True when a typed repair job asks *this* claim to be adjudicated again.

    The typed equivalent of "reverify this claim", and deliberately
    scope-aware: the job names the clusters, claims, or targets it is about, so
    a contradiction routed at one cluster re-opens that cluster's claims and
    leaves the rest of the ledger alone. A job that names no scope at all —
    an assertion the Synthesizer returned, which has no claim link by
    construction — re-opens nothing here; it is what the pass-level check
    reads.
    """
    scope: set[str] = set()
    for job in jobs:
        if job.action not in ("adjudicate", "consolidate"):
            continue
        scope.update(job.claim_cluster_ids)
        scope.update(job.target_ids)
    if not scope:
        return False
    owned = {
        claim.claim_id,
        *claim.cluster_aliases,
        *claim.target_ids,
    }
    if claim.cluster_id:
        owned.add(claim.cluster_id)
    return bool(scope.intersection(owned))


def build_claim_drafts(
    draft: ClaimsDraft,
    *,
    known_urls: Sequence[str],
    prior_claims: Sequence[Claim] = (),
    new_source_urls: Sequence[str] = (),
    critique_texts: Sequence[str] = (),
    refinement_targets: Sequence[RefinementTarget] = (),
) -> tuple[list[ClaimDraft], list[str]]:
    """Keep the claims whose sources exist, naming the ones dropped.

    A model that attaches a URL nobody retrieved has invented a citation,
    which is exactly the failure this project refuses to pass downstream.
    Rejection reasons are generated here and never copied from provider
    output, so they are safe to record in ``ResearchError.details``.

    ``new_source_urls`` is the caller's provenance-derived answer to "which
    URLs carry a finding no prior claim recorded consuming"
    (``extract_claims`` computes it with ``_finding_is_new``). A claim whose
    fingerprint is already in the snapshot is therefore dropped as already
    checked unless one of its own sources carries such new evidence, or a
    repair asked for it: a typed ``adjudicate``/``consolidate`` job whose scope
    names this claim, or the Critic's free-text override, which stays separate
    from provenance-based idempotence.
    """
    allowed = {normalize_source_url(url) for url in known_urls}
    new_urls = {normalize_source_url(url) for url in new_source_urls}
    prior_by_id = {
        claim_fingerprint(claim.text): claim for claim in prior_claims
    }
    seen_ids: set[str] = set()
    claims: list[ClaimDraft] = []
    rejected: list[str] = []
    for index, item in enumerate(draft.claims, start=1):
        text = " ".join(item.text.split())
        if not text:
            rejected.append(f"claim {index}: blank claim text")
            continue
        urls: list[str] = []
        for raw in item.source_urls:
            url = normalize_source_url(raw)
            if url in allowed and url not in urls:
                urls.append(url)
        if not urls:
            rejected.append(
                f"claim {index}: no source url from the collected findings"
            )
            continue
        fingerprint = claim_fingerprint(text)
        previous = prior_by_id.get(fingerprint)
        if previous is not None:
            if not (
                set(urls) & new_urls
                or _critique_requests_reverification(text, critique_texts)
                or _job_requests_adjudication(previous, refinement_targets)
            ):
                rejected.append(f"claim {index}: already checked")
                continue
        if fingerprint in seen_ids:
            rejected.append(f"claim {index}: duplicate claim")
            continue
        seen_ids.add(fingerprint)
        claims.append(ClaimDraft(text=text, source_urls=urls))
    return claims, rejected


def claim_extraction_messages(
    state: ResearchState,
    *,
    max_findings: int,
    prior_claims: Sequence[Claim] = (),
) -> list[ChatMessage]:
    """Build the messages that request one structured claim draft."""
    findings = ordered_findings_for_extraction(
        state, prior_claims=prior_claims
    )[:max_findings]
    coverage_lines = [
        f"- {topic.coverage_id}: {topic.title}"
        for topic in state.sub_topics
    ]
    coverage = "\n".join(coverage_lines) or "(no planned coverage topics)"
    sections = [
        f"# Research question\n{state.original_question}",
        f"# Coverage order\n{coverage}",
        f"# Retrieved findings\n{render_finding_digest(findings)}",
        (
            "# Source quality\n"
            f"{render_source_quality(state.evaluated_sources)}"
        ),
        f"# Response contract\n{CLAIM_EXTRACTION_INSTRUCTION}",
        (
            "# Reply format\n"
            f"{render_structured_reply_format(_CLAIM_EXTRACTION_REPLY_EXAMPLES)}"
        ),
    ]
    return [
        ChatMessage(role="developer", content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def claim_extraction_provider_error(error: Exception) -> ResearchError:
    """Record that claim extraction could not reach the provider.

    Non-recoverable: with no claims there is nothing to verify, so the
    pass ends. ``details`` carries a static operation and safe provider
    snapshot, matching the redaction discipline in ``react.py`` and
    ``researcher.py``.
    """
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_extraction_provider_error",
        message=(
            "The model provider failed while claims were extracted; no "
            "claim was verified."
        ),
        recoverable=False,
        details=agent_provider_failure_details(
            "fact_checker_claim_extraction", error
        ),
    )


def invalid_claim_error(rejected: Sequence[str]) -> ResearchError:
    """Warn that some extracted claims were malformed and were dropped."""
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_invalid_claim",
        message="Some extracted claims were malformed and were dropped.",
        details={"rejected": list(rejected)},
    )


def claim_consolidation_degraded_error(
    diagnostics: Sequence[str],
) -> ResearchError:
    """Record that the one equivalence proposal could not be obtained.

    Recoverable, and deliberately not a stop. The cluster ids are minted from
    ``claim_cluster_id`` — a pure function of the assertion, folded by local
    identity — so a pass whose provider failed still publishes clusters that
    are *under-merged*, never wrongly merged, and a later pass with a working
    provider merges them. Publishing nothing instead would reproduce exactly
    the defect the wiring removes. ``diagnostics`` carries only
    project-generated strings, never provider text.
    """
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_claim_consolidation_degraded",
        message=(
            "The equivalence proposal could not be obtained; this pass's "
            "claims are published as separate clusters and a later pass may "
            "merge them."
        ),
        details={"diagnostics": list(diagnostics)},
    )


def no_findings_to_check_error() -> ResearchError:
    """Warn that there was nothing to fact check at all."""
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_no_findings",
        message="No findings were available to extract claims from.",
    )


# The verdict vocabulary, in report order. Pinned against ClaimVerdict by
# test_verdict_values_match_the_shared_claim_verdict_type so the two can
# never drift.
VERDICT_VALUES: tuple[ClaimVerdict, ...] = (
    "verified",
    "unverified",
    "contradicted",
    "insufficient_evidence",
)

# Enumerated, project-generated reasons a claim could not be judged.
# Never provider text: these reach ResearchEvent.metadata.
INSUFFICIENT_REASONS = {
    "no_independent_source": (
        "No source independent of the claim's own publisher was retrieved."
    ),
    "loop_failed": "The verification loop stopped on a provider failure.",
    "provider_unavailable": (
        "The model provider failed while the verdict was requested."
    ),
    "unrecognized_verdict": "The model returned no usable verdict.",
    # Task 6's claim-specific adjudication reasons. Each names a *local*
    # condition, so an insufficient claim is diagnosable without re-reading the
    # prompt: which boundary lost the evidence, which identity test failed, or
    # which part of the support the model could not complete.
    "evidence_not_admitted": (
        "The model selected an evidence id the local test could not admit as "
        "evidence."
    ),
    "handoff_loss": (
        "A passage or read this claim linked to never reached the registry."
    ),
    "packet_incomplete": (
        "The packet omitted candidates by an explicit bound or disposition."
    ),
    "acquisition_failed": "Targeted retrieval attempted and acquired nothing usable.",
    "no_candidate": "No claim-specific candidate evidence existed to adjudicate.",
    "capacity_deferred": "The claim's evidence work was deferred past this pass.",
    "identity_unknown": (
        "A supporting source's publisher, work, or origin is not established."
    ),
    "same_work": "Every complete support came from one work.",
    "same_publisher": "Every complete support came from one publisher.",
    "shared_origin": "The complete supports share one claim-specific origin.",
    "no_complete_support": (
        "No selected passage supported the complete atomic claim."
    ),
    "partially_shown": (
        "The passage that may support the claim was shown only in part, so "
        "the supporting text may lie beyond what was read."
    ),
    "relay_source": (
        "The supporting passage repeats the claim's issuer rather than being "
        "the issuer's own account of it."
    ),
    "single_primary_only": (
        "Primary-source attribution: independent corroboration not established."
    ),
    "schema_failed": (
        "The provider's structured reply could not be validated; nothing was "
        "adjudicated."
    ),
    "model_disagreement": (
        "The model's own assessments of the packet contradict its selections."
    ),
}

# The identities behind one support passage, for the strict pair test.
ADJUDICATION_OPERATION = "adjudication_packet"

class EvidencePassageDraft(ContractModel):
    """One provider-reported passage before local provenance validation."""

    source_url: str
    source_title: str
    locator: str
    excerpt: str
    stance: Literal["supports", "contradicts"]


class PassageVerdictDraft(ContractModel):
    """One model verdict for one claim, before domain validation.

    ``verdict`` is a plain ``str`` rather than a ``Literal``: a value the
    model invents must become ``insufficient_evidence`` locally, not a
    validation failure that discards the whole verification pass.

    This is the *legacy* shape, used only for a claim with no claim-specific
    packet to adjudicate — a run with no read registry behind it. It cites
    URLs and quotes text, which is exactly what the packet path forbids: a
    citation the model writes is not a read this run performed.
    """

    verdict: str
    confidence: float
    passages: list[EvidencePassageDraft]


# How a passage stands to the origin of the figure it carries (Section 2.2
# rules 5-7). Only the first two can be half of a corroborating pair.
DEPENDENCE_LEVELS = ("primary", "independent_analysis", "derivative", "unknown")
CORROBORATING_DEPENDENCE = frozenset({"primary", "independent_analysis"})


class SupportAssessment(ContractModel):
    """The model's per-id assessment of ONE passage it was shown.

    The model is asked about ids and nothing else — never to quote a document
    again, because a passage the model retypes is not evidence. Every field is
    a plain bool/str so an answer this contract cannot use becomes a local
    disagreement rather than a validation failure.
    """

    evidence_id: str
    stance: str
    """``supports`` / ``contradicts`` / ``unrelated``; anything else is local."""
    complete_support: bool = False
    """True only when the passage supports the WHOLE atomic claim, not a part."""
    scope_compatible: bool | None = None
    """True when period, unit, and scope match the claim, False when they do not.

    ``None`` means the model did not say, and it is deliberately not the same
    as ``False``: reading silence as "a different period" dismissed the
    conflict and let a refuted claim settle as verified. An unstated scope
    never admits a support and never resolves a disagreement.
    """
    dependence: str = "unknown"
    """``primary`` / ``independent_analysis`` / ``derivative`` / ``unknown``.

    Anything outside that vocabulary is ``unknown``, and only ``primary`` and
    ``independent_analysis`` may corroborate: an omitted or unreadable
    judgement can never complete a pair.
    """
    origin_group_id: str = ""
    """The origin this passage's figure comes from, when not its own.

    Empty keeps the passage's locally resolved origin. Anything else must be
    one of the origins the packet printed beside its candidates; a value the
    model was not shown refuses the passage for corroboration.
    """
    rationale: str = ""

    @field_validator("dependence")
    @classmethod
    def _known_dependence(cls, value: str) -> str:
        folded = value.casefold()
        return folded if folded in DEPENDENCE_LEVELS else "unknown"


class ClaimVerdictDraft(ContractModel):
    """One model verdict over the ids of one claim-specific evidence packet.

    The model proposes a verdict and selects ids; local code owns the identity,
    the read membership, the strict pair test, and the final verdict. It may
    not invent an excerpt or cite a URL — the packet already carries the exact
    text of every candidate, so a retyped quote would be model output standing
    where a read should be.
    """

    verdict: str
    confidence: float
    assessments: list[SupportAssessment]
    support_ids: list[str]
    contradiction_ids: list[str]
    rationale: str = ""


# Two examples, because the verdict set has opposite populated/empty shapes:
# ``verified`` carries evidence, ``insufficient_evidence`` carries none. Both
# are schema-valid; "negative" means weak evidence, never malformed JSON.
_CLAIM_VERIFICATION_REPLY_EXAMPLES = (
    (
        "Verified example input: an independent study reports the same "
        "measured reduction.",
        '{"verdict":"verified","confidence":0.9,"passages":[{"source_url":"https://third-party.example.test/report",'
        '"source_title":"Independent study","locator":"p. 4",'
        '"excerpt":"An independent study reports the same measured reduction.",'
        '"stance":"supports"}]}',
    ),
    (
        "Insufficient-evidence example input: no independent material was "
        "retrieved.",
        '{"verdict":"insufficient_evidence","confidence":0.0,"passages":[]}',
    ),
)


# The packet reply shapes. ``verified`` selects two ids and says why; the
# insufficient shape selects none. Neither carries a URL or a quote: the packet
# the model was shown already carries the exact text of every candidate.
_ADJUDICATION_REPLY_EXAMPLES = (
    (
        "Verified example input: ev-1 and ev-2 each state the same measured "
        "reduction for the same period, each from its own measurement.",
        '{"verdict":"verified","confidence":0.9,"assessments":['
        '{"evidence_id":"ev-1","stance":"supports","complete_support":true,'
        '"scope_compatible":true,"dependence":"primary","origin_group_id":"",'
        '"rationale":"The operator reports its own measurement."},'
        '{"evidence_id":"ev-2","stance":"supports","complete_support":true,'
        '"scope_compatible":true,"dependence":"primary","origin_group_id":"",'
        '"rationale":"The audit measured the reduction itself."}],'
        '"support_ids":["ev-1","ev-2"],"contradiction_ids":[],'
        '"rationale":"Two independent reports state the same figure."}',
    ),
    (
        "Insufficient-evidence example input: ev-3 is about a different "
        "period.",
        '{"verdict":"insufficient_evidence","confidence":0.0,"assessments":['
        '{"evidence_id":"ev-3","stance":"supports","complete_support":false,'
        '"scope_compatible":false,"dependence":"primary","origin_group_id":"",'
        '"rationale":"It measures a different year."}],'
        '"support_ids":[],"contradiction_ids":[],'
        '"rationale":"The only candidate measures a different period."}',
    ),
)


class AdjudicationPacket(ContractModel):
    """The claim-specific evidence one adjudication request is built from.

    Every candidate is an admitted :class:`EvidenceUnit` — an exact excerpt of
    a successful same-run read — with the identity local code resolved for it.
    The packet, not a global URL list, decides what may be selected: a passage
    about another claim is not in it, so it cannot be cited for this one.
    """

    claim_id: str
    claim_text: str
    claim_source_urls: list[str] = Field(default_factory=list)
    claim_target_ids: list[str] = Field(default_factory=list)
    """The obligations this claim answers, so the packet path attributes them
    exactly as the extracted path does. A support-policy gate that filters an
    empty list reads as enforcement while gating nothing."""
    claim_cluster_id: str = ""
    claimed_issuer: str = ""
    """The body this claim attributes its figure to, in the claim's own words.

    Read from the finding the claim was extracted from (the attributed issuer
    the page names) and otherwise from the claim's own prose. Empty means the
    claim names no issuer, and then nothing here can be *that* body's account
    either — the badge reports a named issuer's own publication.
    """
    passage_issuers: dict[str, str] = Field(default_factory=dict)
    """Each candidate's issuing body, as the Source Evaluator evidenced it.

    ``evidence_id -> the ``issuer`` anchor accepted for that passage's
    source``, and nothing for a candidate whose source establishes no issuing
    body: a copy, a relay, an unattributed page, or a source nobody assessed.
    This and only this is the passage side of the primary badge — the claim's
    own cited host is not evidence about who published anything, which is how
    a House.gov printout of an EIA article was read as EIA's own account.
    """
    claim_provenance: ClaimProvenance = Field(default_factory=ClaimProvenance)
    """Where this claim's figure comes from, as its finding recorded it.

    Copied onto the verdict, so the composition, the reader report and the
    ledger state an issuer, an edition and a release date per figure. An
    empty record means nothing was recorded, never that the figure has no
    origin.
    """
    units: list[EvidenceUnit] = Field(default_factory=list)
    eligibility: dict[str, EvidenceEligibility] = Field(default_factory=dict)
    omitted: list[EvidenceDisposition] = Field(default_factory=list)
    omitted_count: int = 0
    """How many registry units were left out, exact even when ``omitted`` is
    bounded. The bound is on the list, never on the fact of the omission."""
    missing_read_ids: list[str] = Field(default_factory=list)
    """Reads this claim is linked to that contributed no passage to the packet.

    The exact ids the passage-selection boundary lost, so ``handoff_loss`` names
    what disappeared instead of describing it.
    """
    missing_read_count: int = 0
    unrendered_ids: list[str] = Field(default_factory=list)
    """Candidates this packet holds that the request did not carry.

    The one record of what the model was *not* shown. It is deliberately not
    read back out of ``omitted``: that list is bounded and shared with other
    claims' units, so a busy registry could truncate away the entry that says a
    candidate was never carried — and a verdict would then be validated against
    a passage nobody saw. Bounded by the packet's own size, so it needs no cap.
    """
    partially_shown_ids: list[str] = Field(default_factory=list)
    """Candidates this packet carried only in part.

    A candidate the request could not print in full was shown as a prefix, so
    the model judged a fragment: whether the rest of the passage would have
    supported the claim is unanswerable from what it saw. Such a candidate is
    recorded here — it may still be selected, and it may never stand as a
    *complete* support, exactly like the deferral ``deferred_capacity`` records.
    """
    fingerprint: str = ""


def _claim_target_scope(state: ResearchState, target_ids: set[str]) -> set[str]:
    """Every id a unit may carry and still belong to this claim's work.

    A claim's ``target_ids`` are the evidence targets the plan minted
    (``topic-01-target-01``). Units do not carry those alone: the Researcher
    registers a unit against the sub-topic its read was taken for
    (``topic-01``), while the Fact Checker's own retrieval for a claim
    registers against the obligation it was reading for. Two id spaces, so a
    link that intersects the obligation ids directly admits only the second
    kind and misses every read the Researcher already made for this exact
    sub-topic. The scope is therefore the obligations themselves plus the
    sub-topics they live in — never another topic's.
    """
    return _target_scope(state.sub_topics, target_ids)


def _target_scope(
    sub_topics: Sequence[SubTopic], target_ids: set[str]
) -> set[str]:
    """The same expansion, from the plan alone, for callers without a state."""
    scope = set(target_ids)
    for topic in sub_topics:
        if topic.coverage_id in target_ids:
            scope.add(topic.coverage_id)
        for target in topic.evidence_targets:
            if target.target_id in target_ids:
                scope.add(target.coverage_id)
    return scope


# A figure a claim or a question states, as it is written: ``10.3 GW``, ``26%``,
# ``1 Megawatt``. ``_YEAR`` names the figures that are not measurements: a year
# is shared by every account of a period, so it can never tell a passage about
# this claim from a passage about another one.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_YEAR = re.compile(r"(?:19|20)\d{2}")

# The floor an obligation-linked passage clears for a claim that states no
# figure. One shared term is not a floor: "capacity" is in every passage of an
# energy sub-topic, so the link admitted the sub-topic again the moment the
# claim had no figure to match on.
_RELEVANCE_FLOOR_TERMS = 2

# How many of the claim's measurand terms a *rival's* own figure phrase shares
# before it is read as a measurement of the same quantity. One is not enough:
# on the audited data a single shared "storage" admitted six passages of a
# market-research vendor's prose to a claim about battery storage additions.
_MEASURAND_TERMS_REQUIRED = 2
# How many tokens after a figure name it. "of new battery storage capacity" is
# four; six leaves room for the restatement in parentheses a claim writes.
_MEASURAND_SPAN = 6
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'.-]*")


def claim_evidence_pool(
    state: ResearchState,
    claim: Claim | ClaimDraft,
    *,
    target_ids: Sequence[str] | None = None,
) -> list[EvidenceUnit]:
    """The evidence units this *claim* may be adjudicated against.

    ``target_ids`` is the obligations the *caller* recorded for this claim: a
    draft the provider just returned carries none of its own, so the agent
    passes the attribution it built for the draft, while a recorded ``Claim``
    already carries them. A caller that passes a bare draft and no target ids
    therefore gets the *narrower* pool — only the claim's own citations and its
    recorded passages — and must do so deliberately.

    Three links, and only these: a unit whose read is one of the claim's own
    citations, a unit whose locator and excerpt are the passage the claim
    already recorded, and a unit covering one of the claim's obligations — the
    sub-topic an evidence target of this claim lives in. Everything else in the
    registry belongs to other claims — admitting it would let a document about
    topic A settle a claim about topic B on the strength of having been read in
    the same run.

    The obligation link admits a unit that *bears* on this claim, not every
    unit of the sub-topic the obligation lives in. A sub-topic is a research
    branch, not a fact: on the audited run's data the link admitted 47 of the
    48 passages registered against the claim about the 2024 additions, because
    any passage about batteries shares "battery"/"storage"/"capacity" with it —
    and 47 candidates then deferred most of themselves, which is the state a
    claim can never settle out of. A linked passage is this claim's evidence
    when it states one of the claim's own figures, or another value of the
    same quantity (:func:`_linked_bears_on`) — and a claim that states no
    figure at all keeps a lexical floor of two shared terms, stronger than the
    single shared word every passage of a sub-topic has.
    """
    obligations = (
        set(target_ids)
        if target_ids is not None
        else set(getattr(claim, "target_ids", ()) or ())
    )
    scope = _claim_target_scope(state, obligations)
    cited = {
        normalize_source_url(url) for url in getattr(claim, "source_urls", ()) or ()
    }
    recorded = {
        (normalize_source_url(passage.source_url), passage.locator, passage.excerpt)
        for passage in getattr(claim, "verification_evidence", ()) or ()
    }
    reads = {
        read.read_id
        for read in state.read_records.values()
        if normalize_source_url(read.resolved_url) in cited
        or normalize_source_url(read.requested_url) in cited
    }
    claim_text = getattr(claim, "text", "") or ""
    figures = _non_year_numbers(claim_text)
    families = {_unit_family(unit) for _, unit in _measured_pairs(claim_text)}
    claim_years = _years(claim_text)
    measurand = _measurand_terms(claim_text)
    linked = [
        unit
        for unit in state.evidence_units.values()
        if scope and scope.intersection(unit.target_ids)
    ]
    linked_ids = {unit.evidence_id for unit in linked}
    floor = set() if figures else _lexical_floor_ids(linked, claim_text)
    pool: list[EvidenceUnit] = []
    for unit in state.evidence_units.values():
        by_citation = unit.read_id in reads
        by_passage = (
            normalize_source_url(unit.source_url),
            unit.locator,
            unit.excerpt,
        ) in recorded
        by_target = unit.evidence_id in linked_ids and (
            _linked_bears_on(
                unit.excerpt,
                figures=figures,
                families=families,
                years=claim_years,
                measurand=measurand,
            )
            if figures
            else unit.evidence_id in floor
        )
        if by_citation or by_target or by_passage:
            pool.append(unit)
    return pool


def _measured_pairs(text: str) -> list[tuple[str, str]]:
    """Every ``value + unit`` a text states, as written and as folded.

    A year is never a measurement — "in 2024" is a period, and the unit is what
    makes the two different — and the unit is folded by the same rule the claim
    parser folds it with, so one spelling never reads as two units.
    """
    pairs: list[tuple[str, str]] = []
    for match in _VALUE_UNIT_PATTERN.finditer(text):
        value = " ".join(match.group("value").split())
        if _YEAR.fullmatch(value):
            continue
        pairs.append((value, _canonical_unit(match.group("unit"))))
    return pairs


def _years(text: str) -> set[str]:
    """The four-digit years a text states, as periods rather than figures."""
    return {
        number for number in _NUMBER.findall(text) if _YEAR.fullmatch(number)
    }


def _unit_family(unit: str) -> str:
    """The quantity a unit measures: power, energy, or the unit itself.

    Scale is not a family: ``10.4 GW`` and ``12,314 MW`` are two measurements
    of one quantity, and a rival figure at another scale is exactly what an
    adjudication has to be able to see. ``GWh`` against ``GW`` is a different
    quantity, and the two never stand for each other. The classification is the
    one the target qualifier already uses, so a claim and a passage cannot
    disagree about what a unit is.
    """
    if _POWER_UNIT.search(unit):
        return "power"
    if _ENERGY_UNIT.search(unit):
        return "energy"
    return unit


def _linked_bears_on(
    excerpt: str,
    *,
    figures: set[str],
    families: set[str],
    years: set[str],
    measurand: set[str],
) -> bool:
    """True when a linked passage is about this claim's own measurement.

    Two ways, and only these. It writes one of the claim's figures as the claim
    writes it — unconditional, because the figure itself is the proof that the
    passage measures this claim's quantity. Or the clause that states *another*
    value in the claim's own unit family is about the same measurand and a
    compatible period, which makes it a rival figure or an independent
    measurement of it — the case an adjudication has to be able to see.

    The measurand is what keeps the second way from admitting every power
    figure in the sub-topic: "30 GW of solar in 2024" states a value in the
    claim's unit and shares nothing of what the claim measures, so it is a
    figure about something else. A single shared word is not enough either: on
    the audited data one shared "storage" admitted six passages of a
    market-research vendor's prose, which is why the rival's own figure phrase
    has to share the measurand the claim states beside its figure.
    """
    if figures.intersection(_non_year_numbers(excerpt)):
        return True
    if not families or not measurand:
        return False
    for clause in _split_clauses(excerpt):
        stated_years = _years(clause)
        if years and stated_years and not years.intersection(stated_years):
            continue
        for match in _VALUE_UNIT_PATTERN.finditer(clause):
            unit = _canonical_unit(match.group("unit"))
            if _unit_family(unit) not in families:
                continue
            shared = measurand.intersection(_figure_terms(clause, match))
            if len(shared) < _MEASURAND_TERMS_REQUIRED:
                continue
            return True
    return False


def _phrase_after(clause: str, match: re.Match[str]) -> str:
    """The words a figure is written with, and no more.

    Six tokens is the longest measurand the audited claims state — "of new
    battery storage capacity" — and a longer run reaches the rest of the
    sentence, which qualifies the measurement rather than naming it.
    """
    return " ".join(_WORD.findall(clause[match.end() :])[:_MEASURAND_SPAN])


# The words a clause is written with that state no measurand: connectives,
# auxiliaries, and reporting verbs the selection stopword list does not carry.
# A rival sharing one of these shares the sentence, not the quantity.
_MEASURE_NOISE = frozenset(
    {
        "that", "this", "these", "those", "than", "then", "they", "them",
        "their", "there", "were", "was", "are", "has", "have", "had", "will",
        "would", "could", "should", "said", "says", "also", "into", "onto",
        "over", "under", "per", "about", "while", "during", "each", "both",
    }
)


def _content_terms(text: str) -> set[str]:
    """The measurand words of a phrase: what is measured, not how much or when.

    Figures, periods, and the words a clause is merely written with are what
    the caller matched or filtered already, so what is left is the measurand's
    own vocabulary. The measure's own name stays: "battery storage capacity"
    and "battery storage" are one measurand, and the two-term requirement below
    is what keeps a generic "capacity" from admitting a figure by itself.
    """
    return {
        token
        for token in _tokens(text)
        if len(token) >= 4
        and not token.isdigit()
        and not _YEAR.fullmatch(token)
        and token not in _MEASURE_NOISE
    }


def _measurand_terms(text: str) -> set[str]:
    """The claim's own terms for what it measures, beside its figures.

    "generators added 10.4 GW of new battery storage capacity" measures battery
    storage capacity, and the words right after the figure are where the claim
    names it. A claim whose figure ends its clause states its measurand earlier
    in that same clause — "with operators planning to add 19.6 GW" — which is
    what :func:`_figure_terms` falls back to. A claim that names no measurand
    at all admits no rival figures, which is the honest reading of one that
    never says what its number counts.
    """
    terms: set[str] = set()
    for clause in _split_clauses(text):
        for match in _VALUE_UNIT_PATTERN.finditer(clause):
            terms.update(_figure_terms(clause, match))
    return terms


def _figure_terms(clause: str, match: re.Match[str]) -> set[str]:
    """One figure's measurand words, or its clause's when the figure ends it.

    "…planning to add 19.6 GW" states its measurand earlier in the same clause,
    and both sides of the comparison are read this way — so a claim and the
    rival it admits cannot disagree about where a measurand is stated.
    """
    phrase = _content_terms(_phrase_after(clause, match))
    return phrase or _content_terms(clause)


def _non_year_numbers(text: str) -> set[str]:
    """The figures a text states, with four-digit years left out.

    A year is shared by every account of the period, so it can never tell a
    unit about this claim from a unit about another one. What does is the
    measurement, written the way the claim writes it.
    """
    return {
        number
        for number in _NUMBER.findall(text)
        if not _YEAR.fullmatch(number)
    }


def _lexical_floor_ids(
    units: Sequence[EvidenceUnit], claim_text: str
) -> set[str]:
    """The units that clear the floor for a claim that states no figure.

    One shared term is not a floor: "capacity" appears in every passage of an
    energy sub-topic, so a figure-less claim — a definition, a method, a
    comparison — admitted the whole sub-topic again the moment it was linked.
    The floor is two shared terms of the same expanded query the request ranks
    by, so what may be a candidate and what is offered first stay one rule. A
    claim with no text has nothing to be irrelevant to, so the floor admits:
    the failure direction is extra work, never skipped evidence.
    """
    if not claim_text.strip():
        return {unit.evidence_id for unit in units}
    if not units:
        return set()
    terms = _expanded_terms(claim_text)
    tokens = _tokens(claim_text)
    return {
        unit.evidence_id
        for unit in units
        if _score(unit.excerpt, terms, tokens)[0] >= _RELEVANCE_FLOOR_TERMS
    }


def claim_pool_dispositions(
    state: ResearchState,
    pool: Sequence[EvidenceUnit],
) -> list[EvidenceDisposition]:
    """Why each registry unit that is not in the packet was left out.

    An explicit omission with a reason, never a silent drop: a passage the run
    did read and this adjudication did not consider has to be distinguishable
    from one that was never read (Section 2.4).
    """
    admitted = {unit.evidence_id for unit in pool}
    return [
        EvidenceDisposition(
            item_id=unit.evidence_id,
            stage="adjudication-packet",
            reason="out_of_scope",
            target_ids=list(unit.target_ids),
        )
        for unit in state.evidence_units.values()
        if unit.evidence_id not in admitted
    ] or []


def claim_missing_read_ids(
    reads: Mapping[str, ReadRecord],
    claim: Claim | ClaimDraft,
    pool: Sequence[EvidenceUnit],
    *,
    target_ids: Sequence[str] | None = None,
    sub_topics: Sequence[SubTopic] = (),
) -> list[str]:
    """Reads this claim is linked to that contributed no passage to its packet.

    The passage-selection boundary is where a read stops being evidence: a
    document the run read for this claim, whose body is in the registry, but
    from which no unit was admitted, is a *handoff loss*. Naming the exact read
    ids is what makes that distinguishable from a claim that simply has no
    source — the two produce the same thin packet and the same verdict.

    The link is read through the same expansion the pool uses
    (:func:`_claim_target_scope`): a read the Researcher registered against the
    sub-topic it was taken for is a read taken for this claim's obligations,
    and comparing the two id spaces directly reported no loss at all.
    """
    obligations = (
        set(target_ids)
        if target_ids is not None
        else set(getattr(claim, "target_ids", ()) or ())
    )
    scope = _target_scope(sub_topics, obligations)
    cited = {
        normalize_source_url(url)
        for url in getattr(claim, "source_urls", ()) or ()
    }
    present = {unit.read_id for unit in pool}
    missing: list[str] = []
    for read in reads.values():
        if read.read_id in present:
            continue
        if (
            normalize_source_url(read.resolved_url) in cited
            or normalize_source_url(read.requested_url) in cited
            or bool(scope and scope.intersection(read.target_ids))
        ):
            missing.append(read.read_id)
    return missing


def memory_candidate_count(
    state: ResearchState,
    claim: Claim | ClaimDraft,
) -> int:
    """How many *remembered items* were considered as candidates for this claim.

    One quantity, precisely: remembered findings whose source is one this claim
    cites. It counts recalled **items**, never recalls attempted - the number of
    memory lookups is ``memory_recall_count``, reported beside it - and never
    anything validated. A recall is discovery (TR-01), so this number is
    deliberately separate from ``independent_sources``, which counts validated
    read support only; a coverage metric that read one for the other is the
    confusion this pair of fields exists to prevent.
    """
    cited = {
        normalize_source_url(url)
        for url in getattr(claim, "source_urls", ()) or ()
    }
    return sum(
        1
        for finding in state.memory_context.similar_findings
        if normalize_source_url(finding.source_url) in cited
    )


def memory_recall_count(run: ReActRun | None) -> int:
    """How many successful ``query_memory`` lookups this claim's loop made.

    The other half of the memory diagnostic, and a different quantity from
    ``memory_candidate_count``: a lookup is an attempt, an item is a candidate
    it returned. Neither is read support.
    """
    return sum(
        1
        for step in (run.steps if run is not None else ())
        if step.tool_name == "query_memory"
        and step.tool_result is not None
        and step.tool_result.success
    )


def claim_relevant_order(
    pool: Sequence[EvidenceUnit], query: str
) -> list[EvidenceUnit]:
    """The pool ordered by how much each passage bears on the claim itself.

    The request shows only the candidates it can carry whole, so the order the
    pool hands them over in decides what the adjudicator reads. Registry order
    is the order reads were *admitted*, which is about the sub-topic a read was
    fetched for, not about this claim: a passage that shares none of the
    claim's words could be offered, and deferred, ahead of the sentence that
    states its figure. Passages that share no term with the claim — a rendering
    bound must still name them — keep their registry order behind the rest.
    """
    if not pool:
        return list(pool)
    ranked = select_relevant_passages(
        {unit.evidence_id: unit.excerpt for unit in pool},
        query,
        len(pool),
    )
    by_id = {unit.evidence_id: unit for unit in pool}
    # A passage that states one of the claim's own numbers is *about* it, and a
    # lexical score over prose can rank four passages that merely share its
    # wording above the one carrying the figure. The numeric overlap decides
    # first, and the lexical ranking orders everything within it, so a claim's
    # own figure leads and no rule has to name a URL or a number.
    wanted = set(_NUMBER.findall(query))
    if wanted:
        ranked.sort(
            key=lambda evidence_id: -len(
                wanted.intersection(_NUMBER.findall(by_id[evidence_id].excerpt))
            )
        )
    ordered = [by_id[evidence_id] for evidence_id in ranked]
    seen = set(ranked)
    ordered.extend(unit for unit in pool if unit.evidence_id not in seen)
    return ordered


def claim_eligibility(
    unit: EvidenceUnit,
    *,
    read: ReadRecord | None,
    source: ScoredSource | None,
    assessment: SupportAssessment | None,
) -> EvidenceEligibility:
    """The strict-pair inputs one candidate passage contributes.

    Publisher, work, and lineage are the Source Evaluator's resolved identity
    of ``source`` — the identity validated against the anchors it accepted and
    resolved across every read of the run — and never a re-derivation from
    ``read`` alone, which would forget the evidenced issuer and turn one
    report's mirror into a second publisher. ``read`` says only whether the
    passage is a valid read.

    ``corroboration_eligible`` is the Task 4 enforcement point: a passage whose
    source was never assessed may support a statement, but it may never be half
    of an independent pair, because nothing established what it is or where it
    came from. The model's ``dependence`` is necessary and not sufficient — the
    origin identity local code resolved has to exist too.
    """
    origin_group_id = source_origin_id(source) if source is not None else None
    identity = source.work_identity if source is not None else None
    return EvidenceEligibility(
        publisher_id=source.publisher_id if source is not None else None,
        work_id=source.work_id if source is not None else None,
        origin_group_id=origin_group_id,
        derives_from_work_ids=(
            list(identity.derives_from_work_ids) if identity is not None else []
        ),
        complete_support=bool(
            assessment is not None
            and assessment.complete_support
            and assessment.scope_compatible
        ),
        read_valid=read is not None,
        # The local half of the pair test, and the Task 4 enforcement point: a
        # passage whose source was never assessed carries no origin, so it may
        # support a statement but may never be half of an independent pair. The
        # model's ``dependence`` is folded in only where an assessment exists —
        # before adjudication this is what a candidate *could* contribute, and
        # the model decides what it does.
        corroboration_eligible=bool(
            source is not None
            and source.evaluation_status == "scored"
            and origin_group_id is not None
            and (
                assessment is None
                or assessment.dependence in CORROBORATING_DEPENDENCE
            )
        ),
    )


def adjudication_fingerprint(
    claim_text: str, units: Sequence[EvidenceUnit]
) -> str:
    """The packet identity: the claim and the exact text of every candidate.

    One formula for every packet, so two adjudications of the same claim over
    the same evidence share it and a changed body, an added passage, a
    re-worded claim, or a candidate deferred for capacity does not.
    """
    return hashlib.sha256(
        "\x1f".join(
            [
                claim_fingerprint(claim_text),
                *(
                    f"{unit.evidence_id}\x1e{canonical_read_text(unit.excerpt)}"
                    for unit in units
                ),
            ]
        ).encode("utf-8")
    ).hexdigest()


def build_adjudication_packet(
    claim: ClaimDraft,
    pool: Sequence[EvidenceUnit],
    *,
    eligibility: Mapping[str, EvidenceEligibility] | None = None,
    omitted: Sequence[EvidenceDisposition] = (),
    omitted_count: int | None = None,
    missing_read_ids: Sequence[str] = (),
    target_ids: Sequence[str] = (),
    provenance: ClaimProvenance | None = None,
    passage_issuers: Mapping[str, str] | None = None,
) -> AdjudicationPacket:
    """Assemble the packet, with the fingerprint that identifies it.

    The fingerprint covers the claim and the exact text of every candidate, so
    two adjudications of the same claim over the same evidence share it and a
    changed body, an added passage, or a re-worded claim does not.

    ``provenance`` is what the extraction recorded about this claim's figure
    and ``passage_issuers`` what each candidate's source is evidenced to be;
    both travel with the packet into the verdict, so a claim keeps the
    attribution that was read rather than re-deriving it from the page it
    happens to cite.
    """
    units = list(pool)
    fingerprint = adjudication_fingerprint(claim.text, units)
    atoms = extract_text_atoms(claim.text)
    recorded = provenance or ClaimProvenance()
    return AdjudicationPacket(
        claim_id=claim_fingerprint(claim.text),
        claim_text=claim.text,
        claim_source_urls=list(getattr(claim, "source_urls", ()) or ()),
        claim_target_ids=list(target_ids),
        claim_cluster_id=claim_cluster_id(atoms[0]) if atoms else "",
        claimed_issuer=claimed_issuer_for(claim.text, provenance=recorded),
        passage_issuers=dict(passage_issuers or {}),
        claim_provenance=recorded,
        units=units,
        eligibility=dict(eligibility or {}),
        omitted=list(omitted)[:MAX_PACKET_OMISSIONS],
        omitted_count=(
            len(omitted) if omitted_count is None else omitted_count
        ),
        missing_read_ids=list(missing_read_ids)[:MAX_PACKET_OMISSIONS],
        missing_read_count=len(missing_read_ids),
        fingerprint=fingerprint,
    )


def _stamp_cluster_verdict(claim: Claim, cluster: ClaimCluster) -> Claim:
    """One cluster member, carrying the verdict the cluster resolved.

    The cluster is the unit that settles: a member's own verdict is what *it*
    recorded, and publishing it beside a sibling's contradiction is how one
    fact is presented as both corroborated and refuted.
    """
    verdict, status = resolved_verdict_and_status(cluster)
    return claim.model_copy(
        update={
            "cluster_id": cluster.cluster_id,
            "cluster_aliases": list(cluster.cluster_aliases),
            "verdict": verdict,
            "evidence_status": status,
            "insufficient_reason": (
                claim.insufficient_reason
                if verdict == "insufficient_evidence"
                else None
            ),
        }
    )


def _cluster_for_stored_claim(
    claim: Claim, clusters: Iterable[ClaimCluster]
) -> ClaimCluster | None:
    """The cluster this claim belongs to, by its own id or by an absorbed one."""
    for cluster in clusters:
        if claim.cluster_id == cluster.cluster_id or (
            claim.cluster_id and claim.cluster_id in cluster.cluster_aliases
        ):
            return cluster
    return None


def refutes(stance: str) -> bool:
    """True when a stance says the passage disagrees with the claim.

    The vocabulary is closed — ``supports``, ``contradicts``, ``unrelated`` —
    and the local reading of anything else is conservative: a stance this
    contract cannot place is not read as support.
    """
    return stance.casefold() not in ("supports", "unrelated")


def _unclear_stance(stance: str) -> bool:
    """True when the model's stance is one this contract cannot classify.

    Distinct from :func:`refutes` on purpose: an unclear stance blocks
    settlement, but it never asserts that the passage is incompatible with the
    claim, so nothing is published as a contradiction over it.
    """
    return stance.casefold() not in ("supports", "contradicts", "unrelated")


def _row_credit(row: SupportAssessment) -> int:
    """How much credit one assessment gives the claim; lower is less credit.

    Used to choose between two judgements of one passage: the least creditable
    row stands, so the model's row order cannot decide how much credit a
    passage receives.
    """
    if row.stance.casefold() == "contradicts":
        return 0
    if _unclear_stance(row.stance):
        return 1
    if row.stance.casefold() != "supports":
        return 2
    if not row.complete_support:
        return 3
    if not row.scope_compatible:
        return 4
    if row.dependence not in CORROBORATING_DEPENDENCE:
        return 5
    return 6


def partially_shown_candidates(packet: AdjudicationPacket) -> set[str]:
    """The packet's own candidates the request could print only in part.

    Read from the packet's own record, exactly as :func:`unshown_candidates`
    reads the unrendered ones: the model saw a prefix of these passages, so a
    support selected over one is a judgement about the words that were shown,
    never about the passage.
    """
    return {unit.evidence_id for unit in packet.units}.intersection(
        packet.partially_shown_ids
    )


def _read_url(tool_input: Mapping[str, object]) -> str:
    """The URL a read tool call names, however the reader spells it."""
    for key in ("url", "source", "requested_url", "requested_source"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass
class RefusedReadPolicy:
    """The reads one pass already lost, so no later claim pays for them twice.

    Audit finding #10 (C15): ``utilitydive/…/750338`` was refused by its
    publisher and requested again by four different claims, the EIA form page
    three times — 9 of the run's 10 scraper failures, every one a repeat of a
    refusal an earlier claim had already recorded. A refusal is a fact about
    the pass, not about the claim that met it, so the second request is refused
    locally: the loop is told why, the budget is not charged for a call that
    would not have run, and the claim is judged on the evidence it has.

    Only *failed* reads are remembered, and they are remembered per reader:
    ``web_scraper`` fails a PDF with ``unsupported_content_type`` and the
    designed next step is ``document_reader`` on that same URL, so a refusal
    keyed by URL alone denied the fallback that the failure asks for. A
    successful read is never remembered either — it is cached and costs
    nothing to re-request.
    """

    refusals: dict[tuple[str, str], str] = field(default_factory=dict)

    def __call__(
        self, decision: ReActDecision, tool_input: Mapping[str, object] | None = None
    ) -> ToolPolicyDecision:
        return self.before_action(decision, tool_input or {})

    def before_action(
        self,
        decision: ReActDecision,
        tool_input: Mapping[str, object],
    ) -> ToolPolicyDecision:
        if decision.action != "use_tool" or decision.tool_name not in _READ_TOOLS:
            return ToolPolicyDecision()
        url = _read_url(tool_input or {})
        known = self.refusals.get(
            ((decision.tool_name or ""), normalize_source_url(url))
        )
        if not url or known is None:
            return ToolPolicyDecision()
        return ToolPolicyDecision(
            allowed=False,
            reason=(
                f"{url} was already refused this pass ({known}); it is not "
                "requested again for another claim."
            ),
        )

    def after_action(
        self, step: ReActStep, tool_input: Mapping[str, object]
    ) -> None:
        if step.tool_name not in _READ_TOOLS:
            return
        result = step.tool_result
        if result is not None and result.success:
            return
        url = _read_url(tool_input or {})
        if not url:
            return
        reason = "the read failed"
        if result is not None and result.error is not None:
            reason = result.error.type or reason
        self.refusals.setdefault(
            (step.tool_name or "", normalize_source_url(url)), reason
        )


def evidenced_issuer(source: ScoredSource | None) -> str:
    """The issuing body one assessed source is evidenced to be, or ``""``.

    The Source Evaluator's accepted ``issuer`` anchor is the only record that
    says a page *is* a named body's own publication: it is written only after
    ``evidence._issuer_evidenced`` accepted the read — an attribution phrase
    about this document, or the body's own institutionally served host under a
    title that names it. ``ScoredSource.publisher_id`` is not that record: with
    no accepted anchor it is the serving host's identity, which is how a
    House.gov printout of an EIA article came to read as EIA's own account.
    """
    if source is None or source.evaluation_status != "scored":
        return ""
    issuer = (source.identity_anchors or {}).get("issuer")
    return issuer.strip() if isinstance(issuer, str) else ""


def _initialism(words: str) -> str:
    """The acronym a name's own significant words spell, or ``""``.

    A dotted initial ("U.S." folds to ``u s``) is part of a name, not of its
    acronym: taking those letters would spell "useia" for the Energy
    Information Administration. A one-word name spells no acronym.
    """
    significant = [word for word in words.split() if len(word) > 1]
    if len(significant) < 2:
        return ""
    return "".join(word[0] for word in significant)


def _issuer_core(words: str) -> str:
    """One name's identity with its qualifier and its own acronym gloss folded away.

    A leading "the" or "u s" ("U.S." once punctuation folds) is a qualifier,
    not part of what the name identifies: "the department of energy" and
    "department of energy" name one body, and so do "u s energy information
    administration" and "energy information administration". A trailing word
    that spells the same acronym as the rest of the name is the parenthetical
    gloss a page writes beside its own name ("... Administration (EIA)"), not
    an added qualifier, so it folds away too. Folding never empties a
    non-empty name.
    """
    tokens = words.split()
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    elif len(tokens) > 2 and tokens[:2] == ["u", "s"]:
        tokens = tokens[2:]
    if len(tokens) > 1 and _initialism(" ".join(tokens[:-1])) == tokens[-1]:
        tokens = tokens[:-1]
    return " ".join(tokens)


def issuer_matches(claimed: str, evidenced: str) -> bool:
    """True when two spellings name one issuing body.

    Case, typography and prose punctuation fold, so "eia" and "EIA" are one
    name; a leading qualifier or a name's own acronym gloss folds away, so
    "EIA", "U.S. Energy Information Administration" and "U.S. Energy
    Information Administration (EIA)" share one core; and an acronym the
    longer core spells out is that body. Two names merge only when their
    cores are equal or one core is exactly the acronym the other spells —
    never because one spelling's words happen to appear inside the other's,
    which is the word-containment rule this replaces: it let "EIA" match
    "EIA News" and "Department of Energy" match "Texas Department of Energy".

    Decided, accepted limit: two different bodies whose full names spell the
    same acronym — "EIA" the Energy Information Administration and "EIA" the
    Environmental Investigation Agency — are indistinguishable here. Both
    pairs look identical to a two-name comparison (a bare acronym against the
    full name it spells), which is exactly the shape the "EIA" ==
    "U.S. Energy Information Administration" chain needs to hold at all.
    Telling the two real bodies apart needs a registry of known issuers, a
    different mechanism from comparing the two names in front of it.
    """
    claimed_words = _identity_words(claimed)
    evidenced_words = _identity_words(evidenced)
    if not claimed_words or not evidenced_words:
        return False
    claimed_core = _issuer_core(claimed_words)
    evidenced_core = _issuer_core(evidenced_words)
    if claimed_core == evidenced_core:
        return True
    return (
        _initialism(evidenced_core) == claimed_core.replace(" ", "")
        or _initialism(claimed_core) == evidenced_core.replace(" ", "")
    )


def claimed_issuer_for(
    text: str, *, provenance: ClaimProvenance | None = None
) -> str:
    """The body one claim attributes its figure to, or ``""`` when it names none.

    The finding's recorded attribution leads: it is the body the page itself
    attributes the figure to, validated against the read's own words. The
    claim's prose is the fallback for a claim extracted before that record
    existed. A claim that names nobody stays unnamed — the page it happened to
    be found on is not a body it attributes anything to.
    """
    recorded = (
        provenance.attributed_issuer if provenance is not None else ""
    ) or ""
    if recorded.strip():
        return recorded.strip()
    return _claim_issuer(text)


def _passage_issuer(packet: AdjudicationPacket, evidence_id: str) -> str:
    """The issuing body one candidate's source is evidenced to be, or ``""``."""
    return packet.passage_issuers.get(evidence_id, "")


def _issuer_passage_stands(
    packet: AdjudicationPacket,
    supports: Sequence[str],
    rows: Mapping[str, SupportAssessment],
) -> bool:
    """True when one complete support is the claiming body's own account.

    Two things have to hold, and neither is read from the URL the claim
    already cites. The passage's source must be *evidenced* to be a named
    issuing body's own publication, and — when the claim names the body its
    figure belongs to — it must be that body. Another publisher's page, a copy
    whose issuing body nothing establishes, and an unattributed page all
    support the claim without being its issuer's account of it, and none may
    earn a badge that reads as the agency's own word. A claim that names
    nobody is not thereby barred: the badge then reports the publisher the
    passage itself evidences.
    """
    for evidence_id in supports:
        row = rows.get(evidence_id)
        if row is None or row.dependence != "primary":
            continue
        evidenced = _passage_issuer(packet, evidence_id)
        if not evidenced:
            continue
        if not packet.claimed_issuer or issuer_matches(
            packet.claimed_issuer, evidenced
        ):
            return True
    return False


class PacketRendering(NamedTuple):
    """Which candidates one adjudication request carries, and how.

    ``rendered`` pairs each candidate with the exact text the request prints
    for it; ``partially_shown`` names the candidates whose printed text is a
    prefix of the passage rather than the passage; ``unrendered`` is what the
    budget left out, in packet order.
    """

    rendered: list[tuple[EvidenceUnit, str]]
    unrendered: list[EvidenceUnit]
    partially_shown: list[str] = []


def _passage_was_clipped(excerpt: str, text: str) -> bool:
    """True when ``text`` is a cut-down form of ``excerpt``.

    ``_bounded_passage_text`` returns the passage itself whenever it fits the
    bound, so equality after whitespace collapse is the exact test: a candidate
    that fails it printed only part of what the passage says.
    """
    return text != " ".join(excerpt.split())


def _render_order(packet: AdjudicationPacket) -> list[EvidenceUnit]:
    """The packet's candidates in the order one request offers them.

    The candidate that bears most on the claim leads, then the pair candidates,
    then the rest — each group in the claim's own relevance order. Pair
    candidates used to lead outright, so live claim #14's four pair-eligible
    candidates that shared nothing with the claim went first and the passage
    carrying the claim's own sentence was clipped into the last slot. The pair
    rule still promotes what follows the lead, because a budget must never hide
    the second member of a pair behind a passage that could corroborate
    nothing.
    """
    ranking = claim_relevant_order(list(packet.units), packet.claim_text)
    rank = {
        unit.evidence_id: position for position, unit in enumerate(ranking)
    }
    lead = ranking[0].evidence_id if ranking else None
    indices = sorted(
        range(len(packet.units)),
        key=lambda index: (
            0
            if packet.units[index].evidence_id == lead
            else (1 if _pair_candidate(packet, packet.units[index]) else 2),
            rank.get(packet.units[index].evidence_id, len(rank)),
            index,
        ),
    )
    return [packet.units[index] for index in indices]


def plan_packet_rendering(
    packet: AdjudicationPacket,
    *,
    evidence_chars: int,
) -> PacketRendering:
    """Split a packet's candidates into what one request carries, and how.

    The candidate that bears most on the claim is offered first, then the pair
    candidates (:func:`_render_order`), because a budget can never hide what a
    claim is about behind passages that are not about it. What follows is
    carried whole whenever it fits: a request that prints a prefix of a passage
    asks the model to judge text it cannot see, and a figure past the cut reads
    as an absent one. Only a passage longer than the whole request is ever cut,
    so a document page — which is what a ``document_reader`` passage is — is
    carried whole and may stand as a complete support. A longer passage is
    offered in part, and recorded in ``partially_shown``; the candidates behind
    a full request are returned rather than dropped, so the caller records them
    as explicit omissions. An unassessed passage is never read as an absent
    one.
    """
    if evidence_chars < 1:
        raise ValueError("evidence_chars must be at least 1")
    ordered = _render_order(packet)
    position = {
        unit.evidence_id: index for index, unit in enumerate(packet.units)
    }
    rendered: list[tuple[EvidenceUnit, str]] = []
    unrendered: list[int] = []
    partially_shown: list[str] = []
    used = 0
    for unit in ordered:
        # What this candidate may print: everything the request has left
        # once its own block's fixed text is paid for. The first candidate
        # is carried however little room is left, so a request is never
        # empty.
        room = evidence_chars - used - len(_candidate_block(packet, unit, ""))
        if room < 1 and rendered:
            unrendered.append(position[unit.evidence_id])
            continue
        text = _bounded_passage_text(unit.excerpt, limit=max(room, 1))
        rendered.append((unit, text))
        used += len(_candidate_block(packet, unit, text))
        if _passage_was_clipped(unit.excerpt, text):
            partially_shown.append(unit.evidence_id)
    return PacketRendering(
        rendered=rendered,
        unrendered=[packet.units[index] for index in sorted(unrendered)],
        partially_shown=partially_shown,
    )


def _pair_candidate(packet: AdjudicationPacket, unit: EvidenceUnit) -> bool:
    """True when this candidate could be half of an independent pair."""
    eligibility = packet.eligibility.get(unit.evidence_id)
    return bool(eligibility is not None and eligibility.corroboration_eligible)


def _candidate_block(
    packet: AdjudicationPacket, unit: EvidenceUnit, text: str
) -> str:
    eligibility = packet.eligibility.get(unit.evidence_id)
    origin = eligibility.origin_group_id if eligibility is not None else None
    return (
        f"- id: {unit.evidence_id}\n"
        f"  source: {unit.source_title} ({unit.source_url})\n"
        f"  origin: {origin or '(none resolved)'}\n"
        f"  locator: {unit.locator}\n"
        f"  exact text: {text}"
    )


def with_render_boundaries(
    packet: AdjudicationPacket,
    *,
    evidence_chars: int,
) -> AdjudicationPacket:
    """The same packet, recording every candidate this request cannot show whole.

    Two records, because the two conditions differ. A candidate the request
    did not carry is *omitted*: an explicit disposition, deferred like
    ``deferred_capacity``, so nothing a run read disappears silently. A
    candidate the request carried only in part is *partially shown*: the model
    saw a prefix, so its text may say something the passage does not and the
    passage may say something its text does not — the validator may not read a
    complete support out of it. At the production budget only a passage longer
    than one candidate may carry is ever partially shown.
    """
    plan = plan_packet_rendering(
        packet, evidence_chars=evidence_chars
    )
    if not plan.unrendered and not plan.partially_shown:
        return packet
    unrendered = [unit.evidence_id for unit in plan.unrendered]
    omitted = [
        *(
            EvidenceDisposition(
                item_id=unit.evidence_id,
                stage="adjudication-packet",
                reason="deferred_capacity",
                target_ids=list(unit.target_ids),
            )
            for unit in plan.unrendered
        ),
        *packet.omitted,
    ]
    return packet.model_copy(
        update={
            # The new omissions come first: they are the ones that describe this
            # request, and the list is bounded.
            "omitted": omitted[:MAX_PACKET_OMISSIONS],
            "omitted_count": packet.omitted_count + len(unrendered),
            "unrendered_ids": unrendered,
            "partially_shown_ids": list(plan.partially_shown),
        }
    )


def adjudication_messages(
    packet: AdjudicationPacket,
    *,
    evidence_chars: int,
) -> list[ChatMessage]:
    """The messages that judge one claim from its own evidence packet.

    Every candidate the budget can carry is printed with the id the model must
    select, the local origin id it may name in ``origin_group_id``, and the
    text of the read it came from — clipped to its own bound, so one long page
    cannot fill the request by itself. Which candidates those are is
    :func:`plan_packet_rendering`'s answer, and the caller records the rest as
    omissions: the prompt and the packet the verdict is validated against are
    the same set of candidates, so nothing is judged over a passage the model
    never saw. Nothing else is offered: no URL the model could cite instead,
    and no invitation to quote.
    """
    plan = plan_packet_rendering(
        packet, evidence_chars=evidence_chars
    )
    rendered = [
        _candidate_block(packet, unit, text) for unit, text in plan.rendered
    ]
    sections = [
        f"# Claim\n{packet.claim_text}",
        (
            "# Candidate passages (select by id; the text below is the exact "
            "text of the read, do not retype or cite anything else)\n"
            + ("\n".join(rendered) if rendered else "(none)")
        ),
        f"# Response contract\n{ADJUDICATION_INSTRUCTION}",
        f"# Dependence contract\n{ADJUDICATION_DEPENDENCE_INSTRUCTION}",
        (
            "# Reply format\n"
            f"{render_structured_reply_format(_ADJUDICATION_REPLY_EXAMPLES)}"
        ),
    ]
    return [
        ChatMessage(
            role="developer", content=CLAIM_VERIFICATION_SYSTEM_PROMPT
        ),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]



def defer_beyond_budget(
    packet: AdjudicationPacket,
    *,
    evidence_chars: int,
) -> AdjudicationPacket:
    """The packet reduced to the candidates one request can carry whole.

    A packet larger than the request can print used to hold the candidates the
    budget could not reach, so the packet's own candidate list, the request
    that printed it, and the boundary audit described three different sets —
    and the verdict was then refused over candidates the model was never shown.
    What the request cannot carry is decided *here*, before the packet: the
    candidate is removed from ``units`` and recorded as this claim's own
    ``deferred_capacity`` disposition, so nothing the packet holds is
    "unrendered" and the deferral is a reason a reader can act on rather than a
    rendering accident.

    A candidate longer than the whole request is deferred too: the request can
    only ever print a prefix of it, and a prefix may never stand as a complete
    support. The one exception is a packet where nothing else would be carried
    — a request with no candidate could not be adjudicated at all, so the first
    candidate is printed in part exactly as ``plan_packet_rendering`` says, and
    recorded as partially shown.
    """
    if evidence_chars < 1:
        raise ValueError("evidence_chars must be at least 1")
    carried: list[EvidenceUnit] = []
    used = 0
    for unit in _render_order(packet):
        text = " ".join(unit.excerpt.split())
        block = len(_candidate_block(packet, unit, text))
        if carried and used + block > evidence_chars:
            # No room for this one whole. It is deferred by name, and the
            # candidates behind it — which may still fit — are considered:
            # stopping here is how one page-sized passage starved a packet of
            # its own short passages.
            continue
        carried.append(unit)
        used += block
        if len(carried) == 1 and block > evidence_chars:
            # A candidate longer than the whole request is the one exception:
            # it is carried in part rather than deferred, because a request
            # with no candidate could not be adjudicated at all.
            break
    carried_ids = {unit.evidence_id for unit in carried}
    if len(carried_ids) == len(packet.units):
        return packet
    # Packet order, not offer order: the packet keeps the registry's order and
    # the request renders in its own.
    kept = [
        unit for unit in packet.units if unit.evidence_id in carried_ids
    ]
    deferred = [
        EvidenceDisposition(
            item_id=unit.evidence_id,
            stage="adjudication-packet",
            reason="deferred_capacity",
            target_ids=list(unit.target_ids),
        )
        for unit in packet.units
        if unit.evidence_id not in carried_ids
    ]
    return packet.model_copy(
        update={
            "units": kept,
            "eligibility": {
                evidence_id: entry
                for evidence_id, entry in packet.eligibility.items()
                if evidence_id in carried_ids
            },
            # The new deferrals come first: they are the ones that describe
            # this request, and the list is bounded.
            "omitted": [*deferred, *packet.omitted][:MAX_PACKET_OMISSIONS],
            "omitted_count": packet.omitted_count + len(deferred),
            # Recomputed over the candidates the packet now holds, so the
            # fingerprint still identifies exactly the text an adjudication
            # was made over.
            "fingerprint": adjudication_fingerprint(
                packet.claim_text, kept
            ),
        }
    )


def deferred_candidates(packet: AdjudicationPacket) -> set[str]:
    """The packet's own candidates this request could not carry.

    Before the cap these were the units left in ``unrendered_ids``; now the
    deferral is decided when the packet is built, so the unit is no longer in
    ``units`` and its record is the disposition it was deferred with. The same
    two tests apply to both spellings: a candidate nobody saw cannot settle the
    claim it was deferred from. Read from the packet's own dispositions, which
    are kept ahead of the other omissions, so the entry that keeps a claim from
    settling can never be the one the bounded list truncates away.
    """
    return {
        item.item_id
        for item in packet.omitted
        if item.stage == "adjudication-packet"
        and item.reason == "deferred_capacity"
    }


def unshown_candidates(packet: AdjudicationPacket) -> set[str]:
    """The packet's own candidates that one request could not carry.

    A candidate here was in the packet the verdict is validated against and was
    not shown to the model, so it was never judged: an open question, never an
    absent one. Read from the packet's own record of what it could not carry,
    never back out of the bounded omission list — that list is shared with other
    claims' units, and truncating it must not be able to hide a candidate the
    request left behind.
    """
    return {unit.evidence_id for unit in packet.units}.intersection(
        packet.unrendered_ids
    )


def validate_adjudication(
    draft: ClaimVerdictDraft,
    packet: AdjudicationPacket,
    assessments: Sequence[SupportAssessment] | None = None,
) -> Claim:
    """Stamp one model adjudication into a validated ``Claim``.

    The model selected ids; everything that makes a selection *evidence* is
    decided here: the id must be one the model was shown, the passage must be
    the packet's own read, both sides of the strict pair test must hold before
    ``verified`` may be written, and a material unresolved contradiction stops
    it. ``assessments`` defaults to the draft's own rows and exists so a caller
    that re-assesses (a repaired reply, a re-run) validates the same packet
    against the rows that actually apply.

    A provider or schema failure never reaches this function: there is no draft
    to validate, so the caller keeps the claim pending instead of recording an
    evidence verdict about it.
    """
    rows = list(draft.assessments if assessments is None else assessments)
    # The ids the request actually carried: the packet minus the candidates the
    # budget left out. An id in the packet but not in the request was never
    # shown to the model, so a row for it is refused exactly as an id that is
    # not in the packet at all — otherwise the model is credited with a passage
    # it never saw, and that passage can be half of a pair. The candidates the
    # packet itself deferred for capacity count with them: the packet no longer
    # holds them, and the model still never saw them.
    unshown = unshown_candidates(packet) | deferred_candidates(packet)
    shown = {
        unit.evidence_id: unit
        for unit in packet.units
        if unit.evidence_id not in unshown
    }
    # The candidates the request carried only in part. They were shown, so the
    # model's selection of one is real and is recorded; but a prefix cannot
    # establish what the whole passage says, so none may stand as a complete
    # support and none may be read as an absent one.
    partial = partially_shown_candidates(packet)
    flags: list[str] = []
    # The flags a standing complete support earns, kept apart from the packet's
    # own: what kept the claim from settling is what the reason must say, and
    # the packet's rendering and admission flags are audit facts about the
    # request rather than the verdict's explanation.
    analysis: list[str] = []
    if packet.missing_read_ids:
        # A read this claim is linked to never reached the packet. That is the
        # first thing to say about the claim, because it explains a thin packet
        # that would otherwise read as "nothing supports this".
        flags.append("handoff_loss")
    if unshown:
        # An omission of one of this packet's own candidates: the judgement is
        # incomplete, which is what the flag means. Units of other claims, and
        # deferrals from elsewhere in the run, are not about this claim.
        flags.append("packet_incomplete")
    accepted: dict[str, SupportAssessment] = {}
    selected_support_candidates: list[str] = []
    refused: set[str] = set()
    for row in rows:
        if row.evidence_id not in shown:
            flags.append("evidence_not_admitted")
            refused.add(row.evidence_id)
            continue
        existing = accepted.get(row.evidence_id)
        if existing is not None:
            # Two judgements of one passage: the model disagreed with itself
            # about it, so the id is refused and the *least* creditable of its
            # rows stands — keeping the first one let the most permissive
            # judgement decide the claim, and made the verdict depend on the
            # order the model happened to write its rows in.
            flags.append("model_disagreement")
            refused.add(row.evidence_id)
            if _row_credit(row) < _row_credit(existing):
                accepted[row.evidence_id] = row
            continue
        accepted[row.evidence_id] = row

    selected_supports = list(dict.fromkeys(draft.support_ids))
    selected_contradictions = list(dict.fromkeys(draft.contradiction_ids))
    supports = [
        evidence_id
        for evidence_id in selected_supports
        if evidence_id in accepted
        and evidence_id not in refused
        and accepted[evidence_id].stance.casefold() == "supports"
    ]
    # Every candidate that disagrees, whether the model said so in a row or by
    # selecting the id: a selection it never assessed, and an assessment it
    # never selected, are each evidence that the passage refutes the claim.
    # Dropping either published a verdict over a refutation nobody weighed.
    contradicts = [
        unit.evidence_id
        for unit in packet.units
        if unit.evidence_id not in unshown
        and (
            unit.evidence_id in selected_contradictions
            or (
                unit.evidence_id in accepted
                and accepted[unit.evidence_id].stance.casefold() == "contradicts"
            )
        )
    ]
    # A row whose stance is none of ``supports``, ``contradicts``, or
    # ``unrelated`` is a judgement this contract cannot place: it blocks
    # settlement, because a passage nobody could classify is not an absent one,
    # but it never asserts incompatibility — publishing ``contradicted`` over
    # it would fail every support policy and dominate every cluster the claim
    # joins.
    unclear = [
        unit.evidence_id
        for unit in packet.units
        if unit.evidence_id not in unshown
        and unit.evidence_id in accepted
        and _unclear_stance(accepted[unit.evidence_id].stance)
    ]
    admitted = set(supports).union(contradicts)
    refused_now = {
        evidence_id
        for evidence_id in (*selected_supports, *selected_contradictions)
        if evidence_id not in admitted
    }
    if refused_now:
        flags.append("evidence_not_admitted")
        # A selection the local test refused is recorded as refused, so the
        # manifest can tell it apart from one the model never made.
        refused.update(refused_now)
    selected_support_candidates = list(supports)
    overlap = set(supports).intersection(contradicts)
    if overlap:
        # A passage cannot both support and refute one claim: the model
        # disagreed with itself, and the conservative reading is the refutation.
        flags.append("model_disagreement")
        refused.update(overlap)
        supports = [item for item in supports if item not in overlap]

    eligibility = dict(packet.eligibility)
    for evidence_id in shown:
        eligibility.setdefault(
            evidence_id,
            claim_eligibility(
                shown[evidence_id], read=None, source=None, assessment=None
            ),
        )
    # The origins the model was shown, and so the only ones it may name: the
    # local origin of each candidate in the packet.
    allowed_origins = {
        eligibility[evidence_id].origin_group_id
        for evidence_id in shown
        if eligibility[evidence_id].origin_group_id
    }
    for evidence_id, row in accepted.items():
        base = eligibility[evidence_id]
        origin = base.origin_group_id
        named_origin_valid = True
        if row.origin_group_id:
            if row.origin_group_id in allowed_origins:
                # "This passage's figure comes from that candidate's origin":
                # the passage then shares it, and cannot pair with it.
                origin = row.origin_group_id
            else:
                # An origin nobody printed is not evidence of anything, and it
                # is never trusted — the row cannot corroborate at all.
                named_origin_valid = False
                flags.append("model_disagreement")
        eligibility[evidence_id] = base.model_copy(
            update={
                # A candidate the request could only print in part was judged
                # on a prefix: the model's "complete support" is a claim about
                # the words it was shown, so the passage cannot stand as a
                # complete support for anything beyond them.
                "complete_support": bool(
                    row.complete_support
                    and row.scope_compatible
                    and evidence_id not in partial
                ),
                "origin_group_id": origin,
                # Section 2.2 rule 7: only a primary account or an independent
                # analysis can corroborate; derivative and unknown never do.
                "corroboration_eligible": bool(
                    base.corroboration_eligible
                    and named_origin_valid
                    and row.dependence in CORROBORATING_DEPENDENCE
                ),
            }
        )

    # Only a passage that supports the COMPLETE, in-scope claim may stand
    # behind it. A partial, off-scope, or reversed reading is still assessed
    # and still recorded as a conflict where it contradicts — but it is not
    # evidence for the claim, and publishing its text as ``evidence`` would put
    # an unsupported assertion in front of a reader.
    complete_supports = [
        evidence_id
        for evidence_id in supports
        if eligibility[evidence_id].complete_support
    ]
    supports = complete_supports
    # A candidate this request could not carry was never judged by the model,
    # so the claim cannot settle over it: what was not shown is an open
    # question, never an absent one.
    verified_pair: tuple[str, str] | None = None
    for index, left in enumerate(supports):
        for right in supports[index + 1 :]:
            if eligible_independent_pair(eligibility[left], eligibility[right]):
                verified_pair = (left, right)
                break
        if verified_pair is not None:
            break
    if unshown:
        verified_pair = None

    conflicts = _conflict_assessments(
        packet, accepted, supports, [*contradicts, *unclear]
    )
    asserted_ids = set(contradicts)
    asserted = any(
        conflict.material and conflict.resolution == "unresolved"
        for conflict in conflicts
        if any(
            evidence_id in asserted_ids
            for evidence_id in conflict.evidence_ids
        )
    )
    blocked = asserted or any(
        conflict.material and conflict.resolution == "unresolved"
        for conflict in conflicts
    )
    if asserted:
        # Both sides exist and cannot be reconciled by a scope difference this
        # contract can see, so the claim is disputed. The conflict rows carry
        # the analysis; a refutation that resolves into ``resolved`` (a
        # different period, unit, or population) or that nobody assessed into
        # materiality never reaches here, and settles as *unsettled* below
        # rather than as a forced "false".
        verdict: ClaimVerdict = "contradicted"
        status: str | None = "contested"
    elif blocked:
        # A judgement this contract cannot place, on a passage the model never
        # said was incompatible: the claim cannot settle over it, and nothing
        # is published as a contradiction for it.
        verdict = "insufficient_evidence"
        status: str | None = None
        if supports:
            # The badge is about a supporting passage, so it is only asked
            # for when one exists: a claim with no support at all is not
            # told its support was a relay.
            status, reasons = _support_reasons(packet, supports, accepted, eligibility)
            flags.extend(reasons)
            analysis.extend(reasons)
        else:
            flags.append(_no_support_reason(partial, unshown))
    elif verified_pair is not None:
        verdict = "verified"
        status = "verified_pair"
    elif supports:
        verdict = "insufficient_evidence"
        status, reasons = _support_reasons(packet, supports, accepted, eligibility)
        flags.extend(reasons)
        analysis.extend(reasons)
    elif selected_support_candidates:
        # The model selected a support and no complete support stands: the
        # passage said something, and not the whole claim. The primary badge is
        # not written for it — it is the reader's "this is the issuer's own
        # account of the fact" line, and no complete passage carries the claim.
        verdict = "insufficient_evidence"
        status = None
        flags.append(_no_support_reason(partial, unshown))
    else:
        verdict = "insufficient_evidence"
        status = None
        if not shown:
            flags.append("no_candidate")
        else:
            flags.append(_no_support_reason(partial, unshown))
    if normalize_verdict(draft.verdict) == "verified" and verdict != "verified":
        # The model proposed the strict badge and the local test refused it.
        # Kept as a flag, never as an override: the proposal is not evidence.
        flags.append("model_disagreement")

    selected = [*supports, *contradicts]
    passages = [
        EvidencePassage(
            source_url=shown[evidence_id].source_url,
            source_title=shown[evidence_id].source_title,
            locator=shown[evidence_id].locator,
            excerpt=shown[evidence_id].excerpt,
            stance=(
                "contradicts" if evidence_id in contradicts else "supports"
            ),
        )
        for evidence_id in selected
    ]
    # The published reason. When a complete support stood, the reason says what
    # kept it from settling the claim — identity_unknown, single_primary_only,
    # relay_source — because that is the only thing a reader can act on, and
    # `packet_incomplete` (the first flag the packet's own cut appends) said
    # nothing about the claim at all: it was the reason every claim whose
    # supporting passage was shown and accepted published. It stays an audit
    # flag, and every other flag keeps its precedence, because a lost read or a
    # refused id is a fact about this claim's own evidence.
    if verdict == "insufficient_evidence" and flags:
        candidates = (
            [flag for flag in flags if flag != "packet_incomplete"]
            if analysis
            else flags
        )
        reason = (candidates or flags)[0]
    else:
        reason = None
    claim = Claim(
        claim_id=packet.claim_id,
        text=packet.claim_text,
        source_urls=(
            list(packet.claim_source_urls)
            or sorted({unit.source_url for unit in packet.units})
            or [f"evidence-packet:{packet.fingerprint[:16]}"]
        ),
        verdict=verdict,
        confidence=_clamp_confidence(draft.confidence),
        evidence=[
            passage.excerpt
            for passage in passages
            if passage.stance == "supports"
        ],
        contradictions=[
            passage.excerpt
            for passage in passages
            if passage.stance == "contradicts"
        ],
        verification_evidence=passages,
        provenance=packet.claim_provenance,
        insufficient_reason=reason,
        cluster_id=packet.claim_cluster_id or None,
        evidence_status=status,
        conflict_assessments=conflicts,
        audit_flags=sorted(set(flags)),
        # The packet path attributes obligations exactly as the extracted path
        # does: an empty list here would make the support-policy gate filter
        # nothing, retire no target, and read as enforcement.
        target_ids=list(packet.claim_target_ids),
        evidence_selection={
            evidence_id: (
                "contradicts" if evidence_id in contradicts else "supports"
            )
            for evidence_id in selected
        },
        refused_evidence_ids=sorted(refused),
    )
    return claim


# Independence failures a pair test *proved* — the supports are one work, one
# publisher, or share one origin — outrank the badge's reason, which only says
# why no primary badge was written. A proof is the reason a reader acts on;
# "identity unknown" beside it would hide it.
_PROVEN_INDEPENDENCE_FAILURES = ("same_work", "same_publisher", "shared_origin")


def _support_reasons(
    packet: AdjudicationPacket,
    supports: Sequence[str],
    rows: Mapping[str, SupportAssessment],
    eligibility: Mapping[str, EvidenceEligibility],
) -> tuple[str | None, list[str]]:
    """The badge complete supports earn, and every flag, most specific first.

    Order: a proven independence failure, then the badge's own reason (relay
    or unknown identity), then the remaining pair flags. The first flag is the
    claim's published reason, so the order is the contract.
    """
    status, badge_flags = _settled_badge(packet, supports, rows)
    identity_flags = _insufficient_flags(eligibility, supports)
    proven = [flag for flag in identity_flags if flag in _PROVEN_INDEPENDENCE_FAILURES]
    rest = [flag for flag in identity_flags if flag not in _PROVEN_INDEPENDENCE_FAILURES]
    return status, list(dict.fromkeys([*proven, *badge_flags, *rest]))


def _settled_badge(
    packet: AdjudicationPacket,
    supports: Sequence[str],
    rows: Mapping[str, SupportAssessment],
) -> tuple[str | None, list[str]]:
    """The badge complete supports earn, and the flag when they earn none.

    ``source_supported`` is published as "primary-source attribution", so it is
    written only where a complete supporting passage is an evidenced
    publication of the body the claim attributes its figure to. A relay — a
    trade-press story repeating an agency's figure — still supports the claim
    and is still recorded as its evidence; it is not the issuer's account of
    it, and the badge says what the evidence is.
    """
    if _issuer_passage_stands(packet, supports, rows):
        return "source_supported", []
    # The reason says what is actually known. A passage the adjudicator called
    # *derivative* repeats another work's figure: that is a relay. A passage
    # judged its own account whose source establishes no issuing body is not a
    # relay — nothing says it repeats anyone — and the identity that is missing
    # is the one the badge would have named.
    if all(
        rows.get(evidence_id) is not None
        and rows[evidence_id].dependence == "unknown"
        for evidence_id in supports
    ):
        # Nobody said what this passage is, so the reason may not either.
        return None, []
    if any(
        rows.get(evidence_id) is not None
        and rows[evidence_id].dependence == "derivative"
        for evidence_id in supports
    ):
        # The adjudicator's own dependence judgement already says this
        # passage repeats another work's figure; that is what a relay is,
        # whether or not an issuer anchor names who it repeats.
        return None, ["relay_source"]
    if not any(_passage_issuer(packet, evidence_id) for evidence_id in supports):
        return None, ["identity_unknown"]
    return None, ["relay_source"]


def _no_support_reason(partial: set[str], unshown: set[str]) -> str:
    """Why no complete support stands behind a claim, without overclaiming.

    ``no_complete_support`` says nothing supported the claim. That is a
    statement about the evidence, so it is written only where the request
    showed everything the packet held. Where a candidate was deferred or
    carried in part, the supporting text may lie in what nobody saw, and the
    honest reason is that cut — ``packet_incomplete`` or ``partially_shown`` —
    never an absence.
    """
    if partial:
        return "partially_shown"
    if unshown:
        return "packet_incomplete"
    return "no_complete_support"


def _insufficient_flags(
    eligibility: Mapping[str, EvidenceEligibility],
    complete_supports: Sequence[str],
) -> list[str]:
    """Which identity test kept a completely supported claim unsettled.

    Every failing test is reported, not the first one: the audit classes may
    coexist, and naming one of three reasons would make a claim look
    diagnosable when it is not.
    """
    flags: list[str] = []
    identified = [
        evidence_id
        for evidence_id in complete_supports
        if eligibility[evidence_id].publisher_id
        and eligibility[evidence_id].work_id
    ]
    # The specific identity test is named whenever it is provable, before the
    # generic "only one source" answer: two complete supports that share one
    # work, one publisher, or one origin are a *known* failure of independence,
    # and calling that "identity_unknown" would hide the reason the ledger is
    # read for.
    if len(identified) >= 2:
        if len({eligibility[item].work_id for item in identified}) < 2:
            flags.append("same_work")
        if len({eligibility[item].publisher_id for item in identified}) < 2:
            flags.append("same_publisher")
        origins = {
            eligibility[item].origin_group_id for item in identified
        }
        # A story repeating a report, or two write-ups of one dataset, share
        # their origin through the lineage the sources state.
        lineage = any(
            shares_lineage(eligibility[left], eligibility[right])
            for index, left in enumerate(identified)
            for right in identified[index + 1 :]
        )
        if (None not in origins and len(origins) < 2) or lineage:
            flags.append("shared_origin")
    if not flags:
        eligible = [
            evidence_id
            for evidence_id in complete_supports
            if eligibility[evidence_id].corroboration_eligible
        ]
        if not eligible:
            flags.append("identity_unknown")
        elif len(eligible) < 2:
            flags.append("single_primary_only")
    if not flags:
        flags.append("single_primary_only")
    return flags


def _conflict_assessments(
    packet: AdjudicationPacket,
    accepted: Mapping[str, SupportAssessment],
    supports: Sequence[str],
    contradicts: Sequence[str],
) -> list[ConflictAssessment]:
    """One row for every candidate that disagrees with a selected support.

    Recorded rather than resolved away. Materiality is decided by scope:
    ``complete_support`` answers "does this passage support the WHOLE claim",
    which a passage that *refutes* the claim answers "no" by definition — and
    the schema's default when the model omits the field — so reading
    materiality from it dismissed every same-scope refutation. An in-scope
    refutation is therefore ``unresolved`` and material; a different period,
    unit, or population is ``resolved`` and not material; and a refutation
    nobody assessed at all is ``unresolved`` and material, because what was
    never examined cannot be dismissed.
    """
    rows: list[ConflictAssessment] = []
    for evidence_id in contradicts:
        row = accepted.get(evidence_id)
        if row is None:
            # Nobody examined the disagreement, and what was never examined
            # cannot be dismissed.
            same_scope, resolution, material = False, "unresolved", True
            rationale = "the contradicting passage was never assessed"
        elif row.scope_compatible is False:
            # The only stated difference this contract can see.
            same_scope, resolution, material = False, "resolved", False
            rationale = "the passages differ in period, unit, or scope"
        elif row.scope_compatible is None:
            # An unstated scope is not a claim that the scope differs: silence
            # cannot resolve a disagreement.
            same_scope, resolution, material = False, "unresolved", True
            rationale = "the contradicting passage's scope was never stated"
        else:
            same_scope, resolution, material = True, "unresolved", True
            rationale = "both passages claim to support the same scope"
        rows.append(
            ConflictAssessment(
                claim_cluster_id=packet.claim_cluster_id,
                evidence_ids=sorted({*supports, evidence_id}),
                same_scope=same_scope,
                material=material,
                resolution=resolution,  # type: ignore[arg-type]
                rationale=rationale,
            )
        )
    return rows



def _packet_has_pair(
    packet: AdjudicationPacket,
    *,
    shown: Sequence[str] | None = None,
) -> bool:
    """True when two of the packet's candidates already form the strict pair.

    A qualifying upstream pair needs no new retrieval (Section 2.1), so this is
    the test that lets the claim skip its loop entirely. It is a *necessary*
    condition for the badge, never the badge: the model still has to select the
    two passages and judge that each supports the whole claim.

    ``shown`` restricts the test to the candidates one request actually carries
    (``plan_packet_rendering``). A pair that the request cannot print is not a
    pair the model can certify, so retrieval is not skipped on its strength —
    and a candidate the request could only print in part is not one either: a
    passage nobody saw whole can never stand as a complete support, so a pair
    that needs it would leave the claim unsettled with no retrieval to repair
    it.
    """
    # ``complete_support`` is the model's judgement about what a passage means,
    # so before the adjudication it is not yet known. The local half of the
    # test is every other conjunct: a valid read, a contributor eligible to
    # corroborate at all, and known, pairwise-different publisher, work, and
    # origin. Passing it means a pair is *possible* and no retrieval is needed;
    # it never means the badge, which only ``validate_adjudication`` writes.
    carried = None if shown is None else set(shown) - set(packet.partially_shown_ids)
    possible = [
        packet.eligibility[unit.evidence_id].model_copy(
            update={"complete_support": True}
        )
        for unit in packet.units
        if unit.evidence_id in packet.eligibility
        and (carried is None or unit.evidence_id in carried)
    ]
    for index, left in enumerate(possible):
        for right in possible[index + 1 :]:
            if eligible_independent_pair(left, right):
                return True
    return False


def issuer_passage_carried(
    packet: AdjudicationPacket,
    *,
    shown: Sequence[str] | None = None,
) -> bool:
    """True when the request carries the claiming body's own account.

    The local half of :func:`_issuer_passage_stands`, asked before any model
    call: a candidate of the request is an evidenced publication of the body
    the claim attributes its figure to — the same two facts the badge reads,
    both about the passage's source rather than about the URL the claim
    already cites. Whether the passage supports the claim is the model's
    judgement; this only says the account an obligation on the claim's own
    issuer needs is already in hand.

    ``shown`` restricts the test to the candidates one request actually carries,
    and a candidate carried only in part never counts: a passage nobody saw
    whole may not stand as a complete support.
    """
    carried = (
        None
        if shown is None
        else set(shown) - set(packet.partially_shown_ids)
    )
    for unit in packet.units:
        if carried is not None and unit.evidence_id not in carried:
            continue
        eligibility = packet.eligibility.get(unit.evidence_id)
        if eligibility is None or not eligibility.read_valid:
            continue
        evidenced = _passage_issuer(packet, unit.evidence_id)
        if not evidenced:
            continue
        if not packet.claimed_issuer or issuer_matches(
            packet.claimed_issuer, evidenced
        ):
            return True
    return False


def single_source_suffices(
    packet: AdjudicationPacket,
    policies: Mapping[str, str] | None,
    *,
    shown: Sequence[str] | None = None,
) -> bool:
    """True when no obligation needs a second source and the issuer is carried.

    An obligation whose policy is answered by one primary-source attributed
    reading — ``primary_attribution``, or ``derivation`` — is answered by the
    claim's own issuer's passage, and nothing retrieval finds can answer it
    instead: a passage from another publisher is not that issuer's account. So
    when *every* obligation of the claim is one of those and the request
    already carries the issuer's reading, no further retrieval can change the
    verdict and the loop's tool budget is spent for nothing. All 18 claim loops
    of the audited run ran to their limit this way, finding 0 independent
    sources.

    An obligation with no policy recorded, or any obligation that requires an
    independent pair, answers ``False``: retrieval stays the claim's only path
    to settlement, which is the failure direction that costs work rather than
    evidence. The claim's own adjudication is unchanged either way — a pair the
    packet happens to carry still verifies — and a claim whose support does not
    stand is not stranded: the one thing retrieval can still bring such an
    obligation is a *second page from the same issuer*, so
    :func:`repair_retrieval_warranted` spends one bounded retrieval on exactly
    that, and nothing at all when the carried passage earned the badge.
    """
    if not policies or not packet.claim_target_ids:
        return False
    if any(
        policy not in ("primary_attribution", "derivation")
        for policy in policies.values()
    ):
        return False
    return issuer_passage_carried(packet, shown=shown)


def repair_retrieval_warranted(
    packet: AdjudicationPacket,
    policies: Mapping[str, str] | None,
    claim: Claim,
) -> bool:
    """True when one bounded retrieval could still change this claim's verdict.

    The loop is skipped for one of two reasons, and each has its own repair. A
    pair was already possible, and the model could not turn two identities into
    two supporting passages: the claim is not settled on them, so one bounded
    retrieval runs over the enlarged union. Or no obligation needed a pair and
    the request already carried the issuer's own reading: nothing retrieval
    finds can answer such an obligation *except a second page from that same
    issuer*, so the retrieval runs exactly when the carried passage did not
    stand as the support — a claim that earned its badge is answered already and
    spends nothing. That second repair is why all 18 loops of the audited run
    ran to their tool limit: 17 of them had nothing to repair.

    A candidate nobody saw stops both, because neither can fix it: a rendering
    shortfall is not a retrieval problem, and a deferral would keep the pair
    refused after the retrieval had been paid for.
    """
    if claim.verdict != "insufficient_evidence":
        return False
    if unshown_candidates(packet) or deferred_candidates(packet):
        return False
    if _packet_has_pair(packet):
        return True
    return single_source_suffices(packet, policies) and (
        claim.evidence_status != "source_supported"
    )


def _packet_independent_publishers(
    packet: AdjudicationPacket | None, claim: Claim | None
) -> int:
    """How many eligible independent publishers stand behind a judged claim.

    The diagnostic counts *validated read support*: a passage counts only when
    its source carries a resolved publisher, work, and claim-specific origin and
    the model judged it a complete, in-scope, independent support. A remembered
    candidate, a search result, or a second URL on one publisher contributes
    nothing, so the number can never rise on repetition or on URL count.
    """
    if packet is None or claim is None:
        return 0
    # Only a published support counts, and a support is published only when it
    # completely supported the claim — so the completeness half of the test is
    # already applied. What remains to check locally is that the source behind
    # it was validated: a scored origin, not a URL.
    selected = {
        passage.excerpt
        for passage in claim.verification_evidence
        if passage.stance == "supports"
    }
    publishers = {
        packet.eligibility[unit.evidence_id].publisher_id
        for unit in packet.units
        if unit.excerpt in selected
        and unit.evidence_id in packet.eligibility
        and packet.eligibility[unit.evidence_id].corroboration_eligible
        and packet.eligibility[unit.evidence_id].publisher_id
    }
    return len(publishers)


def provider_failure_reason(error: Exception) -> str:
    """Which enumerated reason a provider failure records.

    A structured-output schema failure exhausted the provider's one repair, and
    an outage never reached it. Both are operational states, and neither is an
    evidence verdict — the caller keeps the claim pending either way — but a
    reviewer reading the ledger needs to tell a malformed reply from a
    transport failure.
    """
    return "schema_failed" if isinstance(error, StructuredOutputError) else (
        "provider_unavailable"
    )


def adjudication_repaired_event(
    record: StructuredRepairRecord,
    *,
    packet_fingerprint: str,
) -> ResearchEvent | None:
    """Report one malformed adjudication reply that the repair made valid.

    Bounded field paths and stable categories only, with the packet
    fingerprint: what was wrong and over which packet, never the rejected text
    and never a path inferred from the schema's name.
    """
    if not record.diagnostics:
        return None
    field_paths = sorted(
        {path for item in record.diagnostics for path in item.field_paths}
    )[: MAX_REPORTED_FIELD_PATHS]
    categories = sorted(
        {item.category or "unknown" for item in record.diagnostics}
    )
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.adjudication.repaired",
        message="A malformed adjudication reply was repaired and validated.",
        metadata={
            "schema": record.schema_name,
            "packet_fingerprint": packet_fingerprint or record.packet_fingerprint,
            "field_paths": field_paths,
            "categories": categories,
            "diagnostic_count": len(record.diagnostics),
        },
    )


def retrieved_source_urls(run: ReActRun) -> list[str]:
    """Canonical URLs the loop actually retrieved content from, in order.

    A tool can return ``success=True`` with nothing usable inside it — a
    search with no hits, an empty scrape, a memory miss — so a payload is
    only counted when it actually carries content. This doubles as the
    "did we retrieve anything at all" predicate: an empty list means the
    verification call must not be made.
    """
    found: list[str] = []
    for step in run.steps:
        for url in read_evidence_urls(step):
            if url not in found:
                found.append(url)
    return found


def supporting_publisher_count(claim: Claim) -> int:
    """How many distinct publishers support this claim's own verdict.

    One publisher is one source however many passages it supplies, so a pair
    of passages from the same domain is not the independent pair a
    ``independent_pair`` target requires.
    """
    return len(
        {
            publisher_identity(passage.source_url).casefold()
            for passage in claim.verification_evidence
            if passage.stance == "supports"
        }
    )


def admitted_target_ids(claim: Claim, policies: Mapping[str, str]) -> list[str]:
    """The target ids this *adjudicated* claim still answers.

    The support policy is a constraint on evidence that exists, so it is
    applied here rather than at extraction: a claim credited with an
    obligation before it was judged can be found insufficient and keep the
    obligation anyway. A target whose policy the claim does not meet loses the
    credit, which is the conservative direction — the obligation stays
    outstanding instead of being marked answered by evidence that never
    supported it.

    What each policy *is* stays ``claim_meets_support_policy``'s rule: the
    claim's own ``evidence_status`` travels with the verdict because the
    weaker policies answer on a ``source_supported`` reading, and a gate that
    cannot see the badge would have to guess it.
    """
    publishers = supporting_publisher_count(claim)
    return [
        target_id
        for target_id in claim.target_ids
        if claim_meets_support_policy(
            support_policy=policies.get(target_id, "independent_pair"),
            verdict=claim.verdict,
            supporting_publishers=publishers,
            evidence_status=claim.evidence_status,
        )
    ]


def claimed_domains_for(source_urls: Sequence[str]) -> list[str]:
    """The distinct publisher identities a claim's own sources live on."""
    domains: list[str] = []
    for url in source_urls:
        domain = publisher_identity(url).casefold()
        if domain not in domains:
            domains.append(domain)
    return domains


def independent_domains(
    urls: Sequence[str],
    *,
    claimed_domains: Sequence[str],
) -> list[str]:
    """Distinct retrieved publishers that are not the claim's own.

    A second page from the publisher that made the claim is not
    corroboration, which is the whole point of cross-referencing.
    """
    claimed = {domain.casefold() for domain in claimed_domains}
    found: list[str] = []
    for url in urls:
        domain = publisher_identity(url).casefold()
        if domain in claimed or domain in found:
            continue
        found.append(domain)
    return found


def normalize_verdict(raw: str) -> ClaimVerdict:
    """Map model text onto a ``ClaimVerdict``, defaulting to the honest one.

    Anything unrecognised becomes ``insufficient_evidence``: a verdict the
    system cannot interpret is not evidence of anything.
    """
    candidate = " ".join(raw.split()).casefold().replace("-", "_")
    candidate = candidate.replace(" ", "_")
    if candidate in VERDICT_VALUES:
        return candidate  # type: ignore[return-value]
    return "insufficient_evidence"


def _clamp_confidence(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _is_copied_example_url(url: str) -> bool:
    """Reject URLs copied from the compact provider examples."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if host is None:
        return False
    host = host.casefold()
    return "example" in host.split(".")


def _bounded_passage_text(value: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return summarize_text(value, limit=limit)


def valid_verification_passages(
    draft: PassageVerdictDraft,
    *,
    retrieved_urls: Sequence[str],
    upstream_read_urls: Sequence[str] = (),
    claimed_publishers: Sequence[str],
) -> list[EvidencePassage]:
    """Keep only bounded passages backed by read-bearing independent URLs.

    The admissible read set is the UNION of the verification loop's own
    reads and the URLs this run's findings came from: the evidence pool is
    the run's supporting upstream findings plus the verifier's own
    retrievals. Admitting only the second half discarded evidence the
    researcher had already read in the same run, and a discarded passage
    left ``resolve_verdict`` with nothing to judge — it answered
    ``insufficient_evidence`` with confidence 0. That was 9 of 12 failures
    in the last run. Only *which* read URLs are admissible widens here: the
    independence rule, the copied-example rejection, and the bounds are
    untouched.
    """
    reads = [*retrieved_urls, *upstream_read_urls]
    retrieved = {normalize_source_url(url) for url in reads}
    claimed = {publisher.casefold() for publisher in claimed_publishers}
    valid: list[EvidencePassage] = []
    seen: set[tuple[str, str, str, str]] = set()
    for passage in draft.passages:
        url = normalize_source_url(passage.source_url)
        if not url or url not in retrieved or _is_copied_example_url(url):
            continue
        publisher = publisher_identity(url).casefold()
        if publisher in claimed:
            continue
        source_title = _bounded_passage_text(passage.source_title, limit=300)
        locator = _bounded_passage_text(
            passage.locator, limit=MAX_PASSAGE_LOCATOR_CHARS
        )
        excerpt = _bounded_passage_text(
            passage.excerpt, limit=MAX_PASSAGE_EXCERPT_CHARS
        )
        if not source_title or not locator or not excerpt:
            continue
        key = (url, locator, excerpt, passage.stance)
        if key in seen:
            continue
        seen.add(key)
        valid.append(
            EvidencePassage(
                source_url=url,
                source_title=source_title,
                locator=locator,
                excerpt=excerpt,
                stance=passage.stance,
            )
        )
    return valid


def resolve_verdict(
    draft: PassageVerdictDraft,
    *,
    independent: Sequence[str] = (),
    passages: Sequence[EvidencePassage] = (),
) -> tuple[ClaimVerdict, float]:
    """Resolve a verdict from validated, independent passages in strict order."""
    del independent  # Publisher names alone must never satisfy this gate.
    if not passages:
        return "insufficient_evidence", 0.0
    verdict = normalize_verdict(draft.verdict)
    confidence = _clamp_confidence(draft.confidence)
    if any(passage.stance == "contradicts" for passage in passages):
        return "contradicted", confidence
    if any(passage.stance == "supports" for passage in passages):
        # This path has no evidence packet and therefore no pair test, so it
        # may never publish the strict badge: the old code returned whatever
        # the model proposed, which is how a single unpersisted read settled a
        # claim. ``unverified`` is the honest ceiling here.
        del verdict
        return "unverified", confidence
    return "unverified", confidence


def build_claim(
    claim: ClaimDraft,
    draft: PassageVerdictDraft,
    *,
    independent: Sequence[str] = (),
    retrieved_urls: Sequence[str] = (),
    upstream_read_urls: Sequence[str] = (),
    claimed_publishers: Sequence[str] | None = None,
    consumed_finding_fingerprints: Sequence[str] = (),
    consumed_coverage_ids: Sequence[str] = (),
    target_ids: Sequence[str] = (),
    provenance: ClaimProvenance | None = None,
) -> Claim:
    """Stamp one model verdict into a validated ``Claim`` record.

    ``retrieved_urls`` is the verification loop's own read set and
    ``upstream_read_urls`` the URLs this run's findings came from; the
    passages the verdict is resolved from may cite either. ``provenance`` is
    what the extraction recorded about this claim's figure, carried onto the
    verdict so the attribution survives adjudication.
    """
    claimed = list(
        claimed_publishers
        if claimed_publishers is not None
        else claimed_domains_for(claim.source_urls)
    )
    passages = valid_verification_passages(
        draft,
        retrieved_urls=retrieved_urls,
        upstream_read_urls=upstream_read_urls,
        claimed_publishers=claimed,
    )
    verdict, confidence = resolve_verdict(
        draft, independent=independent, passages=passages
    )
    return Claim(
        claim_id=claim_fingerprint(claim.text),
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict=verdict,
        confidence=confidence,
        evidence=[
            passage.excerpt
            for passage in passages
            if passage.stance == "supports"
        ],
        contradictions=[
            passage.excerpt
            for passage in passages
            if passage.stance == "contradicts"
        ],
        verification_evidence=passages,
        provenance=provenance or ClaimProvenance(),
        consumed_finding_fingerprints=list(consumed_finding_fingerprints),
        consumed_coverage_ids=list(consumed_coverage_ids),
        target_ids=list(target_ids),
        evidence_status=_legacy_evidence_status(verdict, passages),
    )


def _legacy_evidence_status(
    verdict: ClaimVerdict, passages: Sequence[EvidencePassage]
) -> str | None:
    """The badge a claim judged without a packet can honestly carry.

    ``contested`` when the passages disagree, ``source_supported`` when one
    stands behind the claim, and nothing at all when there is no evidence to
    classify - ``verified_pair`` is unreachable here by construction, because
    this path has no pair test to run.
    """
    if verdict == "contradicted":
        return "contested"
    return "source_supported" if passages else None


def insufficient_claim(
    claim: ClaimDraft,
    *,
    reason: str,
    consumed_finding_fingerprints: Sequence[str] = (),
    consumed_coverage_ids: Sequence[str] = (),
    target_ids: Sequence[str] = (),
    provenance: ClaimProvenance | None = None,
) -> Claim:
    """Record a claim that could not be judged, with no invented confidence.

    ``reason`` is one of ``INSUFFICIENT_REASONS``. It travels in the claim's
    event metadata and on the record itself, so the evidence ledger's claim
    registry can print it: without it an operator reading only the published
    artifacts cannot tell a claim that went unjudged because nothing
    independent was ever read from one whose verdict came back thin, and the
    reviewer's classification of every insufficient claim is not computable.
    The field carries the enumerated token, never the sentence it maps to —
    the token is what an audit groups on, and the explanation stays a
    rendering concern. This is the only construction path that sets it:
    ``build_claim`` reaches ``insufficient_evidence`` too, from a verdict
    nothing usable was read out of, and leaves the field ``None`` because
    nothing was unavailable there.

    Whether the two ``consumed_*`` lists are supplied is the caller's
    decision, and it is a decision about whether this finding was *judged*:
    a verdict that was read and found unsupported records what it consumed,
    while a provider failure that reached no verdict at all records nothing.
    Recording provenance for an unjudged finding would make it look already
    accounted for and suppress its re-extraction for the rest of the run —
    the one polarity this agent's "extra work, never skipped evidence" rule
    must not invert on a transient outage.
    """
    if reason not in INSUFFICIENT_REASONS:
        raise ValueError(f"unknown insufficient-evidence reason: {reason}")
    return Claim(
        claim_id=claim_fingerprint(claim.text),
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict="insufficient_evidence",
        confidence=0.0,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
        provenance=provenance or ClaimProvenance(),
        insufficient_reason=reason,
        consumed_finding_fingerprints=list(consumed_finding_fingerprints),
        consumed_coverage_ids=list(consumed_coverage_ids),
        target_ids=list(target_ids),
    )


def _unique_drafts(drafts: Sequence[ClaimDraft]) -> list[ClaimDraft]:
    """One draft per claim identity, in first-seen order.

    A claim the model restates is the same piece of work: without this a pass
    resumed from its continuation queue would verify it twice and count it
    twice.
    """
    unique: dict[str, ClaimDraft] = {}
    for draft in drafts:
        unique.setdefault(claim_fingerprint(draft.text), draft)
    return list(unique.values())


def _pending_claim(
    draft: ClaimDraft,
    target_ids: Sequence[str],
    *,
    deferred: bool = False,
) -> PendingClaim:
    """One unadjudicated draft as the caller sees it."""
    return PendingClaim(
        claim_id=claim_fingerprint(draft.text),
        text=draft.text,
        source_urls=list(draft.source_urls),
        target_ids=list(target_ids),
        deferred=deferred,
    )


def partition_pending_claims(
    drafts: Sequence[ClaimDraft],
) -> tuple[list[ClaimDraft], list[ClaimDraft]]:
    """Split pending work into the resume window and the deferred overflow.

    The window is bounded so a pass cannot hold unbounded unadjudicated work
    in one structure; the overflow is returned rather than discarded, and the
    next pass drains both. Order is preserved across the split, so a claim's
    position in the queue never depends on which half it landed in.
    """
    window = list(drafts[:MAX_PENDING_CLAIMS])
    deferred = list(drafts[MAX_PENDING_CLAIMS:])
    return window, deferred


def union_claim_provenance(
    previous: Sequence[Claim],
    current: Sequence[Claim],
) -> list[Claim]:
    """Give every re-checked claim everything its earlier record consumed.

    ``merge_claim_snapshot`` replaces a claim's WHOLE record with the latest
    verdict, so a re-verification that consumed only the newly arrived
    finding would otherwise forget the findings the earlier record consumed —
    and those would look new again on the pass after. The union is computed
    here, before the merge, which keeps ``merge_claim_snapshot`` the pure
    identity function it is. First-seen order is preserved and the bound is
    applied last, so an identity that does not fit costs extra work rather
    than silently suppressing evidence.
    """
    by_fingerprint = {
        claim_fingerprint(claim.text): claim for claim in previous
    }
    merged: list[Claim] = []
    for claim in current:
        before = by_fingerprint.get(claim_fingerprint(claim.text))
        if before is None:
            merged.append(claim)
            continue
        fingerprints = list(before.consumed_finding_fingerprints)
        for fingerprint in claim.consumed_finding_fingerprints:
            _append_unique(fingerprints, fingerprint)
        coverage_ids = list(before.consumed_coverage_ids)
        for coverage_id in claim.consumed_coverage_ids:
            _append_unique(coverage_ids, coverage_id)
        merged.append(
            claim.model_copy(
                update={
                    "consumed_finding_fingerprints": fingerprints[
                        :MAX_CONSUMED_FINDING_FINGERPRINTS
                    ],
                    "consumed_coverage_ids": coverage_ids[
                        :MAX_CONSUMED_COVERAGE_IDS
                    ],
                }
            )
        )
    return merged


def claim_verification_messages(
    task: ClaimTask,
    run: ReActRun,
    *,
    evidence_chars: int,
    independent: Sequence[str],
) -> list[ChatMessage]:
    """Build the messages that judge one claim from one finished loop."""
    claimed = "\n".join(f"- {url}" for url in task.claim.source_urls)
    domains = ", ".join(independent) or "(none)"
    sections = [
        f"# Claim\n{task.claim.text}",
        f"# Sources that made the claim\n{claimed}",
        f"# Independent domains retrieved\n{domains}",
        (
            "# Retrieved evidence\n"
            f"{render_evidence(run, limit=evidence_chars, discovery_payloads=False)}"
        ),
        f"# Response contract\n{CLAIM_VERIFICATION_INSTRUCTION}",
        (
            "# Reply format\n"
            f"{render_structured_reply_format(_CLAIM_VERIFICATION_REPLY_EXAMPLES)}"
        ),
    ]
    return [
        ChatMessage(
            role="developer", content=CLAIM_VERIFICATION_SYSTEM_PROMPT
        ),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def verdict_counts(claims: Sequence[Claim]) -> dict[str, int]:
    """Count every verdict value, including the ones that did not occur.

    Zero-filled so a consumer reading the event stream never has to guess
    whether a missing key means zero or means the agent forgot.
    """
    counts = {verdict: 0 for verdict in VERDICT_VALUES}
    for claim in claims:
        counts[claim.verdict] += 1
    return counts


def claim_verification_provider_error(error: Exception) -> ResearchError:
    """Record that one claim's verdict call could not reach the provider.

    Non-recoverable, and the claim is recorded as
    ``insufficient_evidence``: an outage is not evidence.
    """
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_verification_provider_error",
        message=(
            "The model provider failed while a claim's verdict was "
            "requested; the claim was recorded as insufficient evidence."
        ),
        recoverable=False,
        details=agent_provider_failure_details(
            "fact_checker_claim_verification", error
        ),
    )


def claims_extracted_event(
    *,
    claim_count: int,
    findings_considered: int,
    sources_considered: int,
) -> ResearchEvent:
    """Report how many checkable claims the findings yielded."""
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.claims.extracted",
        message="Claim extraction complete.",
        metadata={
            "claim_count": claim_count,
            "findings_considered": findings_considered,
            "sources_considered": sources_considered,
        },
    )


def claims_pending_event(
    pending: Sequence[ClaimDraft],
    *,
    batches_run: int,
    claim_batch_size: int,
    claim_batches_per_pass: int,
    deferred_claim_count: int = 0,
) -> ResearchEvent:
    """Report the claims this pass extracted and did not adjudicate.

    A pass bound is a continuation, not a deletion: the identities are listed
    so the next pass can resume them, and the count is exact even when the
    identity list is bounded. No claim text travels here — the identity is
    what a resumer needs, and an event is not a place to copy evidence.
    """
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.claims.pending",
        message="Some extracted claims were left pending for a later batch.",
        metadata={
            "pending_claim_count": len(pending),
            "pending_claim_ids": [
                claim_fingerprint(draft.text)[:16]
                for draft in pending[:MAX_REPORTED_PENDING_CLAIMS]
            ],
            "deferred_claim_count": deferred_claim_count,
            "batches_run": batches_run,
            "claim_batch_size": claim_batch_size,
            "claim_batches_per_pass": claim_batches_per_pass,
        },
    )


def claim_checked_event(
    claim: Claim,
    run: ReActRun,
    *,
    index: int,
    independent_sources: int,
    reason: str | None,
    memory_candidates: int = 0,
    memory_recalls: int = 0,
) -> ResearchEvent:
    """Report one claim's verdict and what it cost to reach it.

    ``tool_calls`` here is the spec's "tool calls per claim". ``reason`` is
    an ``INSUFFICIENT_REASONS`` key or ``None``; it is never provider
    text. The claim itself is summarized, never pasted, for the same
    reason.
    """
    support_passages = sum(
        passage.stance == "supports"
        for passage in claim.verification_evidence
    )
    contradiction_passages = sum(
        passage.stance == "contradicts"
        for passage in claim.verification_evidence
    )
    unique_publishers = len(
        {
            publisher_identity(passage.source_url).casefold()
            for passage in claim.verification_evidence
        }
    )
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.claim.checked",
        message=f"Claim {index} checked.",
        metadata={
            "claim": summarize_text(claim.text),
            "index": index,
            "verdict": claim.verdict,
            "confidence": round(claim.confidence, 4),
            "contradictions": len(claim.contradictions),
            "independent_sources": independent_sources,
            # Discovery only, and counted apart from the validated support
            # above so a recall can never be read as a read. Two distinct
            # quantities: remembered ITEMS considered, and memory LOOKUPS made.
            "memory_candidates": memory_candidates,
            "memory_recalls": memory_recalls,
            "support_passages": support_passages,
            "contradiction_passages": contradiction_passages,
            "unique_publishers": unique_publishers,
            "tool_calls": run.tool_calls,
            "iterations": run.iterations,
            "stop_reason": run.stop_reason,
            "reason": reason,
        },
    )


def fact_check_completed_event(
    claims: Sequence[Claim],
    *,
    tool_calls: int,
    claim_batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    claim_batches_per_pass: int = DEFAULT_CLAIM_BATCHES_PER_PASS,
    batches_run: int = 0,
    pending_claim_count: int = 0,
    deferred_claim_count: int = 0,
    memory_candidates: int = 0,
    memory_recalls: int = 0,
    cluster_count: int = 0,
    consolidation_diagnostics: Sequence[str] = (),
    cluster_aliases: Mapping[str, str] | None = None,
) -> ResearchEvent:
    """Report the whole fact-checking pass.

    Verdict counts are zero-filled by ``verdict_counts``, so a consumer
    never has to guess whether a missing key means zero. The batch bounds and
    the pending count travel here too, so a report can say how much claim work
    one pass was allowed and how much of it is still outstanding rather than
    leaving the prefix implicit.

    The consolidation travels as bounded counts and ids, never excerpts: the
    diagnostics are project-generated strings, and the aliases are cluster ids
    a reader resolves against the registry rather than text the provider
    wrote.
    """
    counts = verdict_counts(claims)
    contradiction_count = sum(1 for claim in claims if claim.contradictions)
    passages = [
        passage
        for claim in claims
        for passage in claim.verification_evidence
    ]
    support_passages = sum(passage.stance == "supports" for passage in passages)
    contradiction_passages = sum(
        passage.stance == "contradicts" for passage in passages
    )
    unique_publishers = len(
        {publisher_identity(passage.source_url).casefold() for passage in passages}
    )
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.fact_check.completed",
        message="Fact checking complete.",
        metadata={
            "claim_count": len(claims),
            **counts,
            "contradiction_count": contradiction_count,
            "support_passages": support_passages,
            "contradiction_passages": contradiction_passages,
            "unique_publishers": unique_publishers,
            "memory_candidates": memory_candidates,
            "memory_recalls": memory_recalls,
            "tool_calls": tool_calls,
            "claim_batch_size": claim_batch_size,
            "claim_batches_per_pass": claim_batches_per_pass,
            "batches_run": batches_run,
            "adjudicated_claim_count": len(claims),
            "pending_claim_count": pending_claim_count,
            "deferred_claim_count": deferred_claim_count,
            "cluster_count": cluster_count,
            "consolidation_diagnostics": list(consolidation_diagnostics),
            "cluster_aliases": dict(cluster_aliases or {}),
        },
    )


class FactCheckerAgent(BaseAgent[VerifiedClaims]):
    """Extract the major claims and verify each against independent sources.

    ``run`` is overridden because the spec requires a loop *per claim*,
    which the single-loop ``BaseAgent.run`` cannot express. Everything
    below ``run`` — bounds, tracing, tool execution, scratchpad writes —
    is still the shared runtime's.
    """

    name = FACT_CHECKER_NAME
    description = "Verify the major factual claims against independent sources."
    allowed_tools = (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    )

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
        max_claims: int = DEFAULT_MAX_CLAIMS,
        batches_per_pass: int = DEFAULT_CLAIM_BATCHES_PER_PASS,
        finding_digest: int = DEFAULT_FINDING_DIGEST,
        evidence_chars: int = FACT_CHECK_EVIDENCE_CHARS,
        passages_per_read: int = DEFAULT_PASSAGES_PER_READ,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
            model_profile=model_profile,
        )
        if max_claims < 1:
            raise ValueError("max_claims must be at least 1")
        if batches_per_pass < 1:
            raise ValueError("batches_per_pass must be at least 1")
        if finding_digest < 1:
            raise ValueError("finding_digest must be at least 1")
        if evidence_chars < 1:
            raise ValueError("evidence_chars must be at least 1")
        if passages_per_read < 1:
            raise ValueError("passages_per_read must be at least 1")
        self._max_claims = max_claims
        self._batches_per_pass = batches_per_pass
        self._finding_digest = finding_digest
        self._evidence_chars = evidence_chars
        self._passages_per_read = passages_per_read
        # The canonical snapshot of the state this run was handed, captured by
        # ``run`` so ``state_update`` can merge into it. Empty until a run
        # starts, which keeps a directly-invoked ``state_update`` total.
        self._prior_claims: list[Claim] = []
        # The canonical URLs the run's findings came from — evidence the
        # researcher already read upstream, which the claim's verification
        # pool admits alongside the loop's own reads. Captured by ``run``
        # from the state it was handed, like ``_prior_claims``; empty until
        # a run starts, which keeps a directly-invoked ``verify_claim``
        # total.
        self._upstream_read_urls: list[str] = []
        # What the last extraction pass attributed to each accepted draft,
        # keyed by claim fingerprint. ``claim_task`` reads it so a verified
        # claim records the evidence it consumed; it is per-run state and is
        # rewritten on every extraction.
        self._pending_provenance: dict[str, ClaimAttribution] = {}
        # Extracted, not adjudicated: the bounded continuation queue. A pass
        # that ran out of batch allowance leaves its remaining claims here,
        # and the next pass drains them before it extracts anything, so a
        # deferred claim resumes instead of disappearing.
        self._continuation: list[ClaimDraft] = []
        self._deferred: list[ClaimDraft] = []
        # The identities of the drafts this run drained from the queue. They
        # are work this run will do, so the provenance reset must keep their
        # attribution: a resumed claim that the model does not restate has no
        # other source for the obligations it answers.
        self._resumed_fingerprints: set[str] = set()
        # Task 6's claim-specific evidence acquisition state, captured by
        # ``run`` from the state it was handed. ``_run_reads`` is the shared
        # successful-read cache: the Researcher's own reads are in it, keyed by
        # read id, so a Fact Checker target that needs the same PDF reuses the
        # stored body instead of downloading it again.
        self._session_id = ""
        self._run_reads: dict[str, ReadRecord] = {}
        self._run_sources: list[ScoredSource] = []
        self._sub_topics: list[SubTopic] = []
        """The frozen plan, for expanding an obligation to its sub-topic scope."""
        self._run_acquisition_state: dict[str, AcquisitionState] = {}
        # What this pass newly admitted, so state_update can persist it for
        # synthesis and refinement rather than keeping it inside the loop.
        self._new_reads: dict[str, ReadRecord] = {}
        self._new_evidence: dict[str, EvidenceUnit] = {}
        # The clusters this pass's own adjudications minted, keyed by the
        # identity each was minted from. Empty means this pass consolidated
        # nothing, so ``state_update`` publishes no ``claim_clusters`` key at
        # all rather than an empty registry that would replace the stored one.
        self._new_clusters: dict[str, ClaimCluster] = {}
        self._new_dispositions: list[EvidenceDisposition] = []
        self._adjudication_flags: dict[str, list[str]] = {}
        self._adjudication_audits: dict[str, BoundaryAudit] = {}
        # How many manifests this *session* has minted. ``_adjudication_audits``
        # is cleared at every pass start, so a count taken from it restarts at
        # 1 and a second pass mints ids the first pass already used, with
        # different contents: the state merge refuses one id with two contents
        # and the whole update is rejected.
        self._audit_sequence = 0
        self._adjudicated_packets: set[str] = set()
        self._repair_events: list[ResearchEvent] = []
        # The pass's refusals. ``run`` replaces this at the start of every
        # pass; the default keeps a directly-invoked ``_check_claim`` total.
        self._refused_reads = RefusedReadPolicy()

    @property
    def claim_batch_size(self) -> int:
        """How many claims one adjudication batch takes."""
        return self._max_claims

    @property
    def claim_batches_per_pass(self) -> int:
        """How many adjudication batches one pass runs."""
        return self._batches_per_pass

    @property
    def pending_claims(self) -> list[PendingClaim]:
        """The claims this agent extracted and has not adjudicated yet.

        The resume window first, then the deferred overflow, both reported:
        the bound is on how much one structure holds, never on what the agent
        remembers.
        """
        return [
            *(
                _pending_claim(draft, self._obligations_for(draft))
                for draft in self._continuation
            ),
            *(
                _pending_claim(
                    draft, self._obligations_for(draft), deferred=True
                )
                for draft in self._deferred
            ),
        ]

    @property
    def output_schema(self) -> type[VerifiedClaims]:
        """The validated claims. Never sent to the provider.

        ``extract_claims`` asks for ``ClaimsDraft`` and ``verify_claim``
        asks for ``ClaimVerdictDraft``, because ``Claim`` carries ``Field``
        and ``UnitScore`` constraints that do not survive strict JSON
        schema conversion. Do not route this agent through
        ``complete_output``.
        """
        return VerifiedClaims

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return FACT_CHECKER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        """Describe the run as a whole. ``claim_task`` narrows it."""
        return AgentTask(instruction=state.original_question)

    def claim_task(
        self,
        base: AgentTask,
        claim: ClaimDraft,
        *,
        packet: AdjudicationPacket | None = None,
        retrieval_needed: bool = True,
    ) -> ClaimTask:
        """Narrow the run-level task down to one claim's loop.

        The provenance this claim's extraction pass attributed to it travels
        on the task, so the verdict it produces records the evidence it
        consumed without the agent consulting mutable state mid-loop.
        """
        claimed = claimed_domains_for(claim.source_urls)
        attribution = self._pending_provenance.get(
            claim_fingerprint(claim.text), ClaimAttribution()
        )
        sources = "\n".join(f"- {url}" for url in claim.source_urls)
        guidance = "\n".join(
            [
                "The claim was made by these sources:",
                sources,
                (
                    "Do not treat another page from "
                    f"{', '.join(claimed)} as independent corroboration."
                ),
            ]
        )
        sections = [
            section
            for section in (base.guidance.strip(), guidance)
            if section
        ]
        return ClaimTask(
            instruction=(
                f'Verify this claim against independent sources: "'
                f'{claim.text}"'
            ),
            guidance="\n\n".join(sections),
            claim=claim,
            claimed_domains=claimed,
            consumed_finding_fingerprints=(
                attribution.consumed_finding_fingerprints
            ),
            consumed_coverage_ids=attribution.consumed_coverage_ids,
            target_ids=attribution.target_ids,
            target_policies=attribution.target_policies,
            packet=packet,
            retrieval_needed=retrieval_needed,
        )

    def _obligations_for(self, draft: ClaimDraft) -> list[str]:
        """The planned obligations one draft answers, as the scheduler sees them."""
        return self._pending_provenance.get(
            claim_fingerprint(draft.text), ClaimAttribution()
        ).target_ids

    def _policies_for(self, draft: ClaimDraft) -> dict[str, str]:
        """The support policy of each obligation this draft reached for.

        Read beside ``_obligations_for``: the packet's sufficiency test needs
        the pair requirement before the loop starts, and the policy is what the
        plan assigned it.
        """
        return self._pending_provenance.get(
            claim_fingerprint(draft.text), ClaimAttribution()
        ).target_policies

    # --- Task 6: the claim-specific evidence union --------------------------

    def _provenance_for(self, draft: ClaimDraft) -> ClaimProvenance:
        """What the extraction recorded about this claim's figure.

        Read from the attribution `extract_claims` computed for this draft, so
        the verdict carries the issuer, edition and release date the pass read
        — never a second derivation from the page the claim happens to cite. A
        draft the extraction pass never attributed (a resumed queue, a
        fixture) has nothing recorded, which is what an empty record means.
        """
        return self._pending_provenance.get(
            claim_fingerprint(draft.text), ClaimAttribution()
        ).provenance

    def _passage_issuers(
        self, pool: Sequence[EvidenceUnit]
    ) -> dict[str, str]:
        """Each candidate's issuing body, as its source is evidenced to be.

        Empty for a candidate whose source establishes no issuing body — a
        copy, a relay, an unattributed page, or a source nobody assessed — so
        no such passage can stand as the account of the body the claim names.
        """
        return {
            unit.evidence_id: evidenced_issuer(self._claim_source(unit))
            for unit in pool
        }

    def _claim_source(self, unit: EvidenceUnit) -> ScoredSource | None:
        """The assessed source behind one candidate passage, if there is one."""
        read = self._run_reads.get(unit.read_id)
        if read is None:
            return None
        candidates = {
            normalize_source_url(read.resolved_url),
            normalize_source_url(read.requested_url),
        }
        for source in self._run_sources:
            if normalize_source_url(source.url) in candidates:
                return source
        return None

    def _claim_eligibility(
        self, pool: Sequence[EvidenceUnit]
    ) -> dict[str, EvidenceEligibility]:
        """The strict-pair inputs of every candidate, resolved locally.

        Publisher, work, and lineage are the assessed source's resolved
        identity, and the claim-specific origin comes from its *assessment* —
        so a passage whose source was never scored carries no origin and can
        never be half of an independent pair.
        """
        return {
            unit.evidence_id: claim_eligibility(
                unit,
                read=self._run_reads.get(unit.read_id),
                source=self._claim_source(unit),
                assessment=None,
            )
            for unit in pool
        }

    def _packet_for(
        self,
        state: ResearchState,
        draft: ClaimDraft,
        *,
        target_ids: Sequence[str] = (),
        policies: Mapping[str, str] | None = None,
    ) -> tuple[AdjudicationPacket | None, bool]:
        """The claim's packet and whether retrieval is still worth its budget.

        ``None`` means this run has no read registry at all, so the legacy
        passage path applies. A packet with no units is not ``None``: it is a
        claim whose claim-specific union is empty, and the honest answer to
        that is ``no_candidate``, not a global URL pool.

        ``policies`` is the support policy of each of the claim's obligations.
        Retrieval is skipped when the request already carries a pair (Section
        2.1) and also when nothing the claim answers needs one: a
        ``primary_attribution`` obligation is answered by the issuer's own
        reading, which is either in the request or cannot be fetched into it.
        """
        if not state.evidence_units and not self._run_reads:
            return None, True
        pool = claim_relevant_order(
            claim_evidence_pool(state, draft, target_ids=target_ids),
            draft.text,
        )
        eligibility = self._claim_eligibility(pool)
        omitted = claim_pool_dispositions(state, pool)
        packet = build_adjudication_packet(
            draft,
            pool,
            eligibility=eligibility,
            provenance=self._provenance_for(draft),
            passage_issuers=self._passage_issuers(pool),
            omitted=omitted,
            omitted_count=len(omitted),
            missing_read_ids=claim_missing_read_ids(
                state.read_records,
                draft,
                pool,
                target_ids=target_ids,
                sub_topics=list(state.sub_topics),
            ),
            target_ids=target_ids,
        )
        # A pair the request cannot show is not a pair the model can certify,
        # so sufficiency is judged over the candidates the budget carries.
        plan = plan_packet_rendering(
            packet, evidence_chars=self._evidence_chars
        )
        shown = [unit.evidence_id for unit, _ in plan.rendered]
        if _packet_has_pair(packet, shown=shown):
            return packet, False
        return packet, not single_source_suffices(
            packet, policies, shown=shown
        )

    async def _augment_packet(
        self, packet: AdjudicationPacket | None, run: ReActRun, task: ClaimTask
    ) -> AdjudicationPacket | None:
        """Fold the loop's own successful reads into the packet.

        Everything the loop read is admitted through Task 3's payload adapter
        and selected as target-bearing passages, so verifier-acquired evidence
        enters through exactly the same read contract as the Researcher's. Its
        sources are assessed through Task 4's service *before* they may carry a
        statement: an unassessed source keeps no origin and cannot pair.
        """
        if packet is None:
            return None
        units = {unit.evidence_id: unit for unit in packet.units}
        admitted_reads: list[ReadRecord] = []
        # This claim's own deferrals, kept apart from the run-wide list: the
        # packet describes this claim's evidence, and an earlier claim's
        # read-selection deferral is not an omission from this packet.
        claim_dispositions: list[EvidenceDisposition] = []
        for step in run.steps:
            result = step.tool_result
            if result is None or not result.success:
                continue
            target_id = task.target_ids[0] if task.target_ids else None
            admission = admit_read_result(
                result,
                session_id=self._session_id or "fact-checker-session",
                target_id=target_id,
                query=task.claim.text,
                origin="fact_checker",
                selected_limit=self._passages_per_read,
                # Verification selects by the claim and never by the page's
                # header. The lede rule exists for extraction, where a
                # release's opening passage is what the page is about; a
                # claim's packet holds only the passages that bear on the
                # claim, and an unrelated lede admitted here is noise that
                # turns a free ``no_candidate`` outcome into a paid
                # adjudication over a page the claim says nothing about.
                include_lede=False,
                # The run's registry stands for a body it already holds: this
                # loop's own URLs are the ones the state hands it, so a second
                # spelling (`www.`), a layout difference, or a reader's
                # URL-fallback label would otherwise restate one read identity
                # and have the whole state update refused.
                recorded_reads=self._run_reads,
            )
            if admission is None:
                continue
            self._new_reads[admission.read.read_id] = admission.read
            self._run_reads[admission.read.read_id] = admission.read
            claim_dispositions.extend(
                item for item in admission.dispositions if item
            )
            self._new_dispositions.extend(admission.dispositions)
            for evidence_id, unit in admission.evidence.items():
                self._new_evidence[evidence_id] = unit
                units[evidence_id] = unit
            admitted_reads.append(admission.read)
        if admitted_reads:
            # Resolved over every read of the run, not only the new ones: a
            # verifier-read copy of an upstream report is that report's work.
            # The claim's own words order each new read's dossier, so the
            # evaluator judges the passages this claim was read for.
            self._run_sources = await assess_new_sources(
                self.provider,
                admitted_reads,
                self._run_sources,
                # A claim is the obligation its reads were admitted for.
                obligation_queries={
                    read.resolved_url: [task.claim.text]
                    for read in admitted_reads
                },
                known_reads=self._run_reads.values(),
            )
        merged_dispositions = [*packet.omitted, *claim_dispositions]
        eligibility = dict(packet.eligibility)
        # Ranked again, over the enlarged pool: a passage this claim's own
        # retrieval read is evidence the claim paid for, and appending it
        # behind the ranked pool deferred it unseen.
        enlarged = claim_relevant_order(list(units.values()), task.claim.text)
        eligibility.update(self._claim_eligibility(enlarged))
        obligations = packet.claim_target_ids or list(task.target_ids)
        # Recomputed, never carried: when the bounded retrieval does the natural
        # thing and re-reads the document the packet had lost, that read is now
        # present and the loss is repaired. Publishing the stale loss would
        # teach a reader to distrust the audit.
        return build_adjudication_packet(
            task.claim,
            enlarged,
            eligibility=eligibility,
            # The attribution and the issuing bodies travel with the rebuild:
            # this packet replaces the one the task carried, and a rebuilt
            # packet that lost them would adjudicate the claim as if the
            # extraction had recorded nothing about its figure.
            provenance=self._provenance_for(task.claim),
            passage_issuers=self._passage_issuers(enlarged),
            omitted=merged_dispositions,
            omitted_count=packet.omitted_count + len(claim_dispositions),
            missing_read_ids=claim_missing_read_ids(
                self._run_reads,
                task.claim,
                enlarged,
                target_ids=obligations,
                sub_topics=self._sub_topics,
            ),
            target_ids=obligations,
        )

    def _final_packet(
        self, packet: AdjudicationPacket | None
    ) -> AdjudicationPacket | None:
        """The packet one request reads, once no more evidence will be added.

        The candidates this request can carry are decided before the packet:
        what does not fit the request whole is deferred by name with its
        reason, so the packet's owner list, the request that prints it, and the
        boundary audit describe one set and nothing the packet holds is
        "unrendered". A candidate longer than the whole request is the one
        exception, and is recorded as partially shown — so no passage is judged
        by a text it is not.
        """
        if packet is None:
            return None
        return with_render_boundaries(
            defer_beyond_budget(packet, evidence_chars=self._evidence_chars),
            evidence_chars=self._evidence_chars,
        )

    def _record_packet_audit(
        self,
        packet: AdjudicationPacket,
        claim: Claim | None,
        *,
        status: str,
        target_ids: Sequence[str] = (),
    ) -> None:
        """Persist the Section 2.4 boundary manifest for one adjudication.

        Every id list is joinable against the others — ``input_ids`` are the
        evidence ids the packet offered, ``selected_ids`` the ones the model
        selected, ``accepted_ids`` the subset standing behind the claim — and
        they are joined BY ID, never by excerpt: a mirror pair's two units
        carry identical text, and an excerpt join would report both for one
        selection. A selection the local test refused is recorded as
        ``refused:<id>``, so "not selected" and "selected and refused" stay
        distinguishable, and the handoff entries name exactly which reads never
        reached the packet.
        The lists are bounded (``MAX_PACKET_OMISSIONS``) and any overflow is
        summarized by count, so a 300-document registry cannot write a
        300-row manifest per claim while still recording that it overflowed.
        """
        selection = dict(claim.evidence_selection) if claim else {}
        refused = list(claim.refused_evidence_ids) if claim else []
        disposition_ids = [
            f"{item.item_id}:{item.reason}" for item in packet.omitted
        ]
        persisted_omissions = len(packet.omitted[:MAX_PACKET_OMISSIONS])
        if packet.omitted_count > persisted_omissions:
            disposition_ids.append(
                f"omitted_overflow:"
                f"{packet.omitted_count - persisted_omissions}"
            )
        disposition_ids.extend(
            f"read:{read_id}:handoff_loss"
            for read_id in packet.missing_read_ids
        )
        if packet.missing_read_count > len(packet.missing_read_ids):
            disposition_ids.append(
                "handoff_overflow:"
                f"{packet.missing_read_count - len(packet.missing_read_ids)}"
            )
        disposition_ids.extend(f"refused:{item}" for item in refused)
        # The candidates the request could not carry are the ones this packet
        # offered and the model was never shown: they belong in the manifest
        # whether or not the bounded omission list still has room for them.
        recorded = {item.item_id for item in packet.omitted}
        disposition_ids.extend(
            f"{item}:deferred_capacity"
            for item in packet.unrendered_ids
            if item not in recorded
        )
        audit = build_boundary_audit(
            operation=ADJUDICATION_OPERATION,
            job_id=self._session_id or "fact-checker-session",
            agent_name=self.name,
            sequence=getattr(self, "_audit_sequence", 0) + 1,
            target_ids=tuple(target_ids),
            claim_cluster_ids=(
                [packet.claim_cluster_id] if packet.claim_cluster_id else []
            ),
            input_ids=tuple(unit.evidence_id for unit in packet.units),
            selected_ids=tuple(
                unit.evidence_id
                for unit in packet.units
                if unit.evidence_id in selection
            ),
            accepted_ids=tuple(
                unit.evidence_id
                for unit in packet.units
                if selection.get(unit.evidence_id) == "supports"
            ),
            deferred_ids=tuple(
                dict.fromkeys(
                    [
                        *packet.unrendered_ids,
                        *(
                            item.item_id
                            for item in packet.omitted[:MAX_PACKET_OMISSIONS]
                        ),
                    ]
                )
            ),
            disposition_ids=tuple(disposition_ids),
            packet_fingerprint=packet.fingerprint,
            configuration_fingerprint=(
                f"evidence_chars={self._evidence_chars};"
                f"passages={self._passages_per_read}"
            ),
            status=status,  # type: ignore[arg-type]
        )
        self._adjudication_audits[audit.audit_id] = audit
        self._audit_sequence = getattr(self, "_audit_sequence", 0) + 1

    def _discard_stale_repairs(self) -> None:
        """Drop repairs the provider buffered before the call about to be made.

        The provider hook is deliberately dumb: it hands back everything
        recorded since the last drain. Only the packet path drains, so a repair
        from the legacy verdict path or from an earlier claim would otherwise be
        published against *this* packet's fingerprint.
        """
        drain = getattr(self.provider, "drain_structured_repairs", None)
        if callable(drain):
            drain()

    def _drain_repair_events(self, packet: AdjudicationPacket) -> None:
        """Record repaired structured replies, bounded and provider-text-free.

        The provider's one-repair flow returns the repaired parse and, until
        this hook, dropped the categories that describe what was wrong. The
        categories are read from the provider's own local validation — never
        inferred from the schema's name — and are recorded with the packet
        fingerprint so a repair is tied to the work it happened over.
        """
        drain = getattr(self.provider, "drain_structured_repairs", None)
        if not callable(drain):
            return
        for record in drain():
            if record.schema_name != ClaimVerdictDraft.__name__:
                # A record for any other schema cannot be about this packet.
                continue
            event = adjudication_repaired_event(
                record, packet_fingerprint=packet.fingerprint
            )
            if event is not None:
                self._repair_events.append(event)

    def _acquisition_key(self, target_id: str | None) -> str:
        """The key the run's acquisition state is written under for a target.

        ``acquisition_state_by_target`` is the Researcher's, keyed by the
        sub-topic's ``coverage_id`` — one acquisition loop runs per sub-topic
        — while a claim's obligation carries the namespaced target id
        (``topic-01-target-01``). Reading the queue by that id found nothing,
        so the resume context this claim's loop renders was always empty and
        the loop never saw what the run had already queued for its own
        sub-topic.

        A target id the plan does not name keeps the id it came with: a plan
        built before the planner namespaced its targets stamped the coverage
        id on the target itself, and that id is the key its entry was written
        under.
        """
        for sub_topic in self._sub_topics:
            if any(
                target.target_id == target_id
                for target in sub_topic.evidence_targets
            ):
                return sub_topic.coverage_id
        return target_id or ""

    def build_decision_context(
        self,
        task: AgentTask,
        *,
        iteration: int,
        steps: Sequence[ReActStep],
    ) -> str:
        """Render Task 3's acquisition context for this claim's next decision.

        The claim's loop gets the same complete decision packet the Researcher
        does: the active target, the candidate URLs this loop has discovered
        but not read, the reads and evidence already behind the claim, the
        remaining tool/model-turn capacity, and the next support type still
        required. A prefix of a search or PDF payload is never used instead.
        """
        if not isinstance(task, ClaimTask):
            return ""
        target_id = task.target_ids[0] if task.target_ids else None
        stored = self._run_acquisition_state.get(
            self._acquisition_key(target_id)
        )
        discovered: list[str] = []
        for step in steps:
            for url in read_evidence_urls(step):
                normalized = normalize_source_url(url)
                if normalized and normalized not in discovered:
                    discovered.append(normalized)
        state = AcquisitionState(
            target_id=target_id or "",
            remaining_calls=max(
                0, self.config.tool_budget_for(self.name) - sum(
                    1 for step in steps if step.tool_name
                )
            ),
            remaining_model_turns=max(0, self.config.max_iterations - iteration),
            candidate_urls=(
                list(stored.candidate_urls) if stored is not None else []
            ) or discovered,
            attempted_urls=(
                list(stored.attempted_urls) if stored is not None else []
            ),
            read_urls=(
                list(stored.read_urls) if stored is not None else []
            ),
            pending_extraction_ids=(
                list(stored.pending_extraction_ids) if stored is not None else []
            ),
            pending_passage_ids=(
                list(stored.pending_passage_ids) if stored is not None else []
            ),
            consecutive_searches=(
                stored.consecutive_searches if stored is not None else 0
            ),
            empty_searches=stored.empty_searches if stored is not None else 0,
            candidate_records=(
                dict(stored.candidate_records) if stored is not None else {}
            ),
        )
        packet = task.packet
        evidence = (
            {unit.evidence_id: unit for unit in packet.units}
            if packet is not None
            else {}
        )
        return build_acquisition_context(
            state,
            self._run_reads,
            evidence,
            limit=self._evidence_chars,
            target_id=target_id,
            dispositions=tuple(packet.omitted) if packet is not None else (),
        )

    def _reset_provenance(self) -> None:
        """Drop attribution for drafts this run will not process.

        Provenance belongs to the extraction pass about to run, so anything
        left over from an earlier run must not leak into it. A draft this run
        resumes is the exception: it is work this run will do, and its
        attribution was derived from this same run's findings. Both halves are
        needed — the queue before it is drained, and the drained identities
        afterwards, because ``extract_claims`` resets again once the queue is
        empty.
        """
        resumable = (
            {
                claim_fingerprint(draft.text)
                for draft in (*self._continuation, *self._deferred)
            }
            | self._resumed_fingerprints
        )
        self._pending_provenance = {
            fingerprint: attribution
            for fingerprint, attribution in self._pending_provenance.items()
            if fingerprint in resumable
        }

    def _drain_continuation(self) -> list[ClaimDraft]:
        """Admit at most one window of deferred work, and leave the rest deferred.

        The bound is on the pool a pass actively works, not on what the agent
        remembers: draining the whole deferred list would activate every claim
        at once and the window would bound nothing. The remainder keeps its
        order and stays persisted until a later pass admits it.
        """
        window, deferred = partition_pending_claims(
            [*self._continuation, *self._deferred]
        )
        self._continuation = []
        self._deferred = deferred
        self._resumed_fingerprints = {
            claim_fingerprint(draft.text) for draft in window
        }
        return window

    def _remember_pending(self, drafts: Sequence[ClaimDraft]) -> None:
        """Persist this pass's leftover work inside the explicit bound."""
        window, deferred = partition_pending_claims(drafts)
        self._continuation = window
        self._deferred = deferred

    async def extract_claims(
        self,
        state: ResearchState,
    ) -> tuple[list[ClaimDraft], list[ResearchError], bool]:
        """Turn the finished research pass into checkable claim drafts.

        Makes no provider call when there are no findings, so the
        extraction step can never invent a claim out of nothing — and none
        when every candidate finding's identity is already recorded as
        consumed by a prior claim, so stable evidence cannot keep buying
        extraction and verification passes. This also records, per accepted
        draft, which findings and coverage topics it consumed and which
        planned obligations it answers.

        No prefix is applied here. Every claim the findings support is kept,
        and how many of them one pass adjudicates is the scheduler's decision
        — which is what stops a later topic from being starved by the first
        five claims the model happened to return.
        """
        findings = ordered_findings_for_extraction(
            state, prior_claims=self._prior_claims
        )
        if not findings:
            return [], [no_findings_to_check_error()], False
        # The model is shown only the first ``_finding_digest`` candidates, so
        # attribution may only consider that same slice. Attributing against
        # the full list lets a claim citing a URL whose finding sits past the
        # cut record that finding's coverage id as consumed — over-counting
        # coverage on evidence the model never saw, not merely costing extra
        # work. ``claim_extraction_messages`` below applies the same bound to
        # the same ordered list.
        visible_findings = findings[: self._finding_digest]

        critique_texts = _critique_texts(state)
        # Provenance decides "new evidence": a finding is new iff no prior
        # claim recorded consuming its identity.
        new_evidence_urls = [
            normalize_source_url(finding.source_url)
            for finding in findings
            if _finding_is_new(finding, self._prior_claims)
        ]
        # Two ways a pass can be told to look again at evidence it already
        # holds. The typed one is the route: ``adjudicate`` and ``consolidate``
        # jobs are what the graph's refinement hop dispatched here, and reading
        # them is what keeps that route from landing on a node that does
        # nothing. The prose markers are the legacy signal a critique written
        # before the typed contract carries, and they stay as the fallback for
        # exactly that reason.
        has_reverification_request = any(
            job.action in ("adjudicate", "consolidate")
            for job in state.refinement_targets
        ) or any(
            marker in _collapsed(text)
            for text in critique_texts
            for marker in (
                "reverify",
                "re-verify",
                "re verify",
                "recheck",
                "re-check",
            )
        )
        if (
            self._prior_claims
            and not new_evidence_urls
            and not has_reverification_request
        ):
            self._reset_provenance()
            return [], [], False

        try:
            response = await self.provider.complete_structured(
                claim_extraction_messages(
                    state,
                    max_findings=self._finding_digest,
                    prior_claims=self._prior_claims,
                ),
                ClaimsDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            self._reset_provenance()
            return [], [claim_extraction_provider_error(error)], True

        claims, rejected = build_claim_drafts(
            response,
            known_urls=known_source_urls(state),
            prior_claims=self._prior_claims,
            new_source_urls=new_evidence_urls,
            critique_texts=critique_texts,
            refinement_targets=state.refinement_targets,
        )
        errors = [invalid_claim_error(rejected)] if rejected else []
        self._reset_provenance()
        coverage_ids = coverage_ids_by_title(state)
        targets = evidence_targets_by_title(state)
        question = obligation_question(state)
        self._pending_provenance.update(
            {
                claim_fingerprint(item.text): claim_attribution(
                    item,
                    findings=visible_findings,
                    coverage_ids=coverage_ids,
                    targets=targets,
                    question=question,
                )
                for item in claims
            }
        )
        return claims, errors, False

    async def verify_claim(
        self,
        task: ClaimTask,
        run: ReActRun,
    ) -> tuple[Claim | None, str | None, list[ResearchError], bool]:
        """Judge one claim from one finished loop.

        Returns ``(claim, reason, errors, provider_failed)``. ``reason`` is
        an ``INSUFFICIENT_REASONS`` key when the claim could not be judged
        and ``None`` otherwise.

        ``None`` for the claim means **nothing was adjudicated**. A provider or
        schema failure is not an evidence verdict: the model never answered, so
        recording ``insufficient_evidence`` would make an outage read as a
        finding about the claim, and would mark the claim's findings consumed
        on the strength of a failure. The claim stays outstanding instead, and
        the caller returns it to the continuation queue.

        A *definitive local* verdict is different and is still recorded: when
        the loop succeeded and read material, and none of what it read is
        independent of the claim's own publisher, that is a settled fact about
        the evidence this pass gathered.
        """
        if task.packet is not None:
            return await self._adjudicate_packet(task, run)
        return await self._verify_with_passages(task, run)

    async def _adjudicate_packet(
        self,
        task: ClaimTask,
        run: ReActRun,
    ) -> tuple[Claim | None, str | None, list[ResearchError], bool]:
        """Judge one claim over its claim-specific evidence packet.

        The packet decides what may be selected: every candidate is an admitted
        unit of a same-run read, so a URL the model writes cannot stand in for
        evidence this run never read. The local test, not the model's proposal,
        writes ``verified``.
        """
        packet = task.packet
        assert packet is not None
        if not run.succeeded:
            return None, "loop_failed", [], False
        if not packet.units:
            # Nothing claim-specific exists to adjudicate: no model call is
            # made, because there is nothing it could legitimately select from.
            claim = insufficient_claim(
                task.claim,
                reason="no_candidate",
                provenance=self._provenance_for(task.claim),
                consumed_finding_fingerprints=(
                    task.consumed_finding_fingerprints
                ),
                consumed_coverage_ids=task.consumed_coverage_ids,
                target_ids=task.target_ids,
            )
            return (
                claim.model_copy(
                    update={
                        "target_ids": admitted_target_ids(
                            claim, task.target_policies
                        )
                    }
                ),
                "no_candidate",
                [],
                False,
            )
        # Anything the provider buffered before this call belongs to other
        # work - the legacy verdict path never drains at all - and publishing it
        # under this packet's fingerprint would be a diagnostic about the wrong
        # claim. Discarded here, so what is drained afterwards is this call's.
        self._discard_stale_repairs()
        try:
            draft = await self.provider.complete_structured(
                adjudication_messages(
                    packet, evidence_chars=self._evidence_chars
                ),
                ClaimVerdictDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            # A provider failure, including a structured-output schema failure:
            # the adjudication was never read, so nothing was judged. Recording
            # insufficient_evidence here would make an outage read as a finding
            # about the claim and would mark its findings consumed.
            reason = provider_failure_reason(error)
            self._adjudication_failure[packet.claim_id] = (
                "schema_failed"
                if reason == "schema_failed"
                else "provider_failed"
            )
            return (
                None,
                reason,
                [claim_verification_provider_error(error)],
                True,
            )
        claim = validate_adjudication(draft, packet, None)
        self._adjudication_flags[packet.claim_id] = claim.audit_flags
        return (
            claim.model_copy(
                update={
                    "consumed_finding_fingerprints": list(
                        task.consumed_finding_fingerprints
                    ),
                    "consumed_coverage_ids": list(task.consumed_coverage_ids),
                    "target_ids": admitted_target_ids(
                        claim, task.target_policies
                    ),
                }
            ),
            claim.insufficient_reason,
            [],
            False,
        )

    async def _verify_with_passages(
        self,
        task: ClaimTask,
        run: ReActRun,
    ) -> tuple[Claim | None, str | None, list[ResearchError], bool]:
        """The legacy path: judge one claim from the loop's own retrieved text.

        Used only for a claim with no claim-specific packet, i.e. a run whose
        state carries no read registry. It admits upstream URLs globally, which
        is exactly the behaviour the packet path replaces.
        """
        if not run.succeeded:
            # No model ever looked at this finding: the loop died before the
            # verdict was requested. Nothing was judged and nothing may look
            # consumed, so no claim is produced at all.
            return None, "loop_failed", [], False

        retrieved_urls = retrieved_source_urls(run)
        independent = independent_domains(
            retrieved_urls, claimed_domains=task.claimed_domains
        )
        if not independent:
            claim = insufficient_claim(
                task.claim,
                reason="no_independent_source",
                provenance=self._provenance_for(task.claim),
                consumed_finding_fingerprints=(
                    task.consumed_finding_fingerprints
                ),
                consumed_coverage_ids=task.consumed_coverage_ids,
                target_ids=task.target_ids,
            )
            return (
                claim.model_copy(
                    update={
                        "target_ids": admitted_target_ids(
                            claim, task.target_policies
                        )
                    }
                ),
                "no_independent_source",
                [],
                False,
            )

        try:
            draft = await self.provider.complete_structured(
                claim_verification_messages(
                    task,
                    run,
                    evidence_chars=self._evidence_chars,
                    independent=independent,
                ),
                PassageVerdictDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            # A provider failure, including a structured-output schema
            # failure: the verdict was never read, so nothing was judged and
            # nothing may look consumed.
            return (
                None,
                provider_failure_reason(error),
                [claim_verification_provider_error(error)],
                True,
            )

        claim = build_claim(
            task.claim,
            draft,
            independent=independent,
            retrieved_urls=retrieved_urls,
            upstream_read_urls=self._upstream_read_urls,
            claimed_publishers=task.claimed_domains,
            consumed_finding_fingerprints=(
                task.consumed_finding_fingerprints
            ),
            consumed_coverage_ids=task.consumed_coverage_ids,
            target_ids=task.target_ids,
            provenance=self._provenance_for(task.claim),
        )
        return (
            claim.model_copy(
                update={
                    "target_ids": admitted_target_ids(
                        claim, task.target_policies
                    )
                }
            ),
            None,
            [],
            False,
        )

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> VerifiedClaims | None:
        """Adapt ``verify_claim`` to the ``BaseAgent`` hook.

        ``run`` calls ``verify_claim`` directly so it can keep the reason
        and the errors this hook signature has nowhere to return.
        """
        if not isinstance(task, ClaimTask):
            raise AgentConfigurationError(
                "FactCheckerAgent.finalize requires a ClaimTask"
            )
        claim, _, _, _ = await self.verify_claim(task, run)
        if claim is None:
            # Nothing was adjudicated, so this hook has no validated claim to
            # report. The run path keeps the claim in its continuation queue.
            return None
        return VerifiedClaims(claims=[claim])

    def state_update(
        self,
        result: VerifiedClaims | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The complete claim snapshot and errors. ``run`` adds the events.

        ``verified_claims`` replaces rather than appends, so this update
        carries every claim verified so far — the ones found on the state
        this run was handed, merged with the ones it just checked. A claim
        the latest pass contradicted therefore replaces its own earlier
        verified record instead of sitting beside it, and it keeps the
        provenance the earlier record consumed.

        ``evaluated_sources`` replaces too, and it is written for the same
        reason: a document this verification read carries a report statement,
        so its assessment has to be saved beside the read that produced it.
        Saving the read without the assessment leaves the report citing a
        source no record ever judged.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["verified_claims"] = merge_claim_snapshot(
                self._prior_claims,
                union_claim_provenance(self._prior_claims, result.claims),
            )
        # The evidence a verification acquired travels in the same update: its
        # reads, its exact evidence units, the dispositions that explain what
        # was not admitted, and the boundary manifests of every adjudication. A
        # passage the Fact Checker read is evidence for synthesis and
        # refinement, not work that disappears when the loop ends.
        if self._new_reads:
            update["read_records"] = dict(self._new_reads)
        if self._new_evidence:
            update["evidence_units"] = dict(self._new_evidence)
        if self._new_clusters:
            update["claim_clusters"] = dict(self._new_clusters)
        if self._new_dispositions:
            update["evidence_dispositions"] = list(self._new_dispositions)
        if self._adjudication_audits:
            update["boundary_audits"] = dict(self._adjudication_audits)
        if self._new_reads:
            # Cumulative and resolved over every read of the run, never per
            # read: the snapshot is what the pair test itself read its
            # publisher, work, and origin from, so publishing anything less
            # would record a verdict against an identity no later step can
            # reproduce — and would drop the earlier sources this update
            # replaces.
            update["evaluated_sources"] = list(self._run_sources)
        return update

    def _sufficient_run(self) -> ReActRun:
        """The empty loop a sufficient upstream packet needs.

        A qualifying upstream pair needs no new retrieval, so the claim's loop
        runs zero iterations and spends zero tool calls. The run is a real
        ``ReActRun`` rather than ``None`` so every downstream consumer — the
        tool-call total, the stop reason, the merged run — keeps its shape.
        """
        return ReActRun(
            agent_name=self.name,
            stop_reason="sufficient",
            max_loop_iterations=self.config.max_iterations,
            max_loop_tool_calls=self.config.tool_budget_for(self.name),
        )

    async def _check_claim(self, task: ClaimTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes about the previous claim are
        noise in this one's prompt. Context that genuinely carries over
        travels in ``task.guidance`` instead.
        """
        self.scratchpad.clear()
        toolset = self.toolset

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> ReActDecision:
            del steps
            return await self._complete_react_decision(task, iteration=iteration)

        react = await run_react_loop(
            agent_name=self.name,
            tracker=self.tracker,
            tools=toolset,
            decide=decide,
            max_iterations=self.config.max_iterations,
            tool_budget=self.config.tool_budget_for(self.name),
            on_step=self._record_step,
            is_sufficient=self.is_sufficient,
            summary_limit=self.config.observation_summary_chars,
            tool_policy=self._refused_reads,
            propagate_provider_errors=False,
        )
        return react.model_copy(
            update={"errors": [*react.errors, *self.scratchpad.drain_errors()]}
        )

    async def run(self, state: ResearchState) -> AgentRun[VerifiedClaims]:
        """Extract claims, then adjudicate them in fair, bounded batches.

        Each batch takes one outstanding obligation per planned target before
        it takes an extra claim about a topic it has already served, so a pass
        cannot spend its whole allowance on the first topic. The pass runs at
        most ``claim_batches_per_pass`` batches of at most ``claim_batch_size``
        claims; everything beyond that stays pending, is reported as pending,
        and is resumed by the next pass. Nothing is marked consumed by merely
        reaching a prompt: a claim consumes its findings only when a verdict
        was actually reached for it.
        """
        base_task = self.build_task(state)
        self._prior_claims = list(state.verified_claims)
        # What the researcher read upstream travels on the agent's state the
        # same way the prior snapshot does, because ``verify_claim`` is
        # handed only the claim's loop. The claims to verify are the
        # findings' own, so the findings' URLs are exactly the upstream read
        # set.
        self._upstream_read_urls = known_source_urls(state)
        # Task 6's claim-specific acquisition state. The run's read registry is
        # the shared successful-read cache: it holds the Researcher's own
        # reads, so a Fact Checker target that needs the same document reuses
        # the stored body instead of downloading it again.
        self._session_id = state.session_id
        self._run_reads = dict(state.read_records)
        # Resolved once over the registry, so a snapshot written before batch
        # resolution existed is identified by the same rule as a fresh one.
        self._run_sources = resolve_source_identities(
            state.evaluated_sources, self._run_reads.values()
        )
        self._sub_topics = list(state.sub_topics)
        self._run_acquisition_state = dict(state.acquisition_state_by_target)
        self._new_reads = {}
        self._new_evidence = {}
        self._new_clusters = {}
        self._new_dispositions = []
        self._adjudication_flags = {}
        self._adjudication_audits = {}
        # ``merge_boundary_audits`` refuses one id with two different
        # contents, so any prior instance that wrote into
        # ``state.boundary_audits`` minted at most ``len(state.boundary_audits)``
        # distinct audits total across every agent — meaning this instance's
        # own highest-used sequence is strictly less than that count. A fresh
        # instance (e.g. a checkpoint restore reconstructing this agent
        # against already-populated state) would otherwise re-seed at 0 and
        # remint ids an earlier instance already claimed. This only ever
        # raises the counter, matching the same-instance, later-pass
        # invariant just above.
        self._audit_sequence = max(
            self._audit_sequence, len(state.boundary_audits)
        )
        self._adjudicated_packets = set()
        self._repair_events = []
        self._adjudication_failure = {}
        # One pass's refusals: a URL that could not be read for one claim is
        # not requested again for another in this pass. Replaced here, at the
        # start of every pass, so a recovered publisher is retried next pass.
        self._refused_reads = RefusedReadPolicy()
        # Provenance belongs to the extraction pass about to run; anything
        # left from an earlier run must not leak into it, except the
        # attribution of a claim this run is about to resume. The queue is
        # drained first so the reset can see which drafts it is keeping.
        resumed = self._drain_continuation()
        # What the drain could not admit stays deferred. It is captured here
        # because the end of this pass **replaces** the queue, and a parked
        # claim that is not carried into that replacement is work destroyed:
        # neither published, nor pending, nor reported.
        parked = list(self._deferred)
        self._reset_provenance()
        target_order = target_order_for(state)
        events: list[ResearchEvent] = []
        errors: list[ResearchError] = []
        claims: list[Claim] = []
        runs: list[ReActRun] = []

        async with self.tracker.agent_span(self.name) as span:
            drafts, extraction_errors, extraction_failed = (
                await self.extract_claims(state)
            )
            errors.extend(extraction_errors)
            # The pool a pass activates is bounded. ``ClaimsDraft.claims`` is
            # not, so the extracted set can be any size; what a pass takes on
            # is at most one window, and the rest is persisted as deferred for
            # a later pass rather than admitted all at once.
            pool, deferred_now = partition_pending_claims(
                _unique_drafts([*resumed, *drafts])
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "phase": "extraction",
                    "claim_count": len(pool) + len(deferred_now),
                    "admitted_claim_count": len(pool),
                    "resumed_claim_count": len(resumed),
                    "provider_failed": extraction_failed,
                }
            )
        events.append(
            claims_extracted_event(
                claim_count=len(pool) + len(deferred_now),
                findings_considered=len(state.raw_findings),
                sources_considered=len(state.evaluated_sources),
            )
        )

        adjudicated: set[str] = set()
        answered_targets: set[str] = set()
        critical_targets = critical_target_ids(state)
        cursor = 0
        batches_run = 0
        index = 0
        memory_candidates_seen = 0
        memory_recalls_seen = 0
        for batch_number in range(1, self._batches_per_pass + 1):
            outstanding = [
                draft
                for draft in pool
                if claim_fingerprint(draft.text) not in adjudicated
            ]
            if not outstanding:
                break
            picked, cursor = select_claim_batch_indices(
                [self._obligations_for(draft) for draft in outstanding],
                target_order,
                self._max_claims,
                cursor=cursor,
                priority=[
                    target
                    for target in critical_targets
                    if target not in answered_targets
                ],
            )
            batches_run = batch_number
            stopped = False
            for draft in (outstanding[position] for position in picked):
                index += 1
                packet, retrieval_needed = self._packet_for(
                    state,
                    draft,
                    target_ids=self._obligations_for(draft),
                    policies=self._policies_for(draft),
                )
                task = self.claim_task(
                    base_task,
                    draft,
                    packet=packet,
                    retrieval_needed=retrieval_needed,
                )
                async with self.tracker.agent_span(self.name) as span:
                    if retrieval_needed:
                        react = await self._check_claim(task)
                    else:
                        # The claim-specific union already carries a qualifying
                        # independent pair, so no retrieval is needed and none
                        # is performed: zero tool calls, no body download.
                        react = self._sufficient_run()
                    task = task.model_copy(
                        update={
                            "packet": self._final_packet(
                                await self._augment_packet(
                                    task.packet, react, task
                                )
                            )
                        }
                    )
                    claim, reason, verify_errors, verify_failed = (
                        await self.verify_claim(task, react)
                    )
                    if (
                        claim is not None
                        and not retrieval_needed
                        and task.packet is not None
                        and repair_retrieval_warranted(
                            task.packet, task.target_policies, claim
                        )
                    ):
                        # The skipped loop is still repairable: a pair the model
                        # could not use, or a single-source obligation whose
                        # carried issuer passage did not stand. ONE bounded
                        # targeted retrieval runs, its read is admitted and
                        # assessed like any other, and the claim is
                        # re-adjudicated over the enlarged union. Reusing the
                        # loop's own bounded budget rather than nesting another
                        # retry is the point, and the predicate is where the
                        # cases that no retrieval can help are kept out.
                        react = await self._check_claim(task)
                        task = task.model_copy(
                            update={
                                "packet": self._final_packet(
                                    await self._augment_packet(
                                        task.packet, react, task
                                    )
                                )
                            }
                        )
                        claim, reason, verify_errors, verify_failed = (
                            await self.verify_claim(task, react)
                        )
                    if task.packet is not None:
                        self._drain_repair_events(task.packet)
                        self._record_packet_audit(
                            task.packet,
                            claim,
                            status=(
                                self._adjudication_failure.get(
                                    task.packet.claim_id, "provider_failed"
                                )
                                if verify_failed
                                else "completed"
                            ),
                            target_ids=task.target_ids,
                        )
                        self._adjudicated_packets.add(task.packet.fingerprint)
                    if verify_failed:
                        # Mirror the loop-level provider_error path so the
                        # merged run never claims "finished" over an abort that
                        # actually happened during verification.
                        react = react.model_copy(
                            update={"stop_reason": "provider_error"}
                        )
                    memory_candidates = memory_candidate_count(
                        state, task.claim
                    )
                    memory_recalls = memory_recall_count(react)
                    memory_candidates_seen += memory_candidates
                    memory_recalls_seen += memory_recalls
                    # In the packet path the diagnostic counts VALIDATED
                    # read support; a URL count can never raise it.
                    independent = (
                        _packet_independent_publishers(task.packet, claim)
                        if task.packet is not None
                        else len(
                            independent_domains(
                                retrieved_source_urls(react),
                                claimed_domains=task.claimed_domains,
                            )
                        )
                    )
                    span.set_outputs(
                        {
                            "agent_name": self.name,
                            "claim_index": index,
                            "batch_index": batch_number,
                            "adjudicated": claim is not None,
                            "verdict": claim.verdict if claim is not None else None,
                            "evidence_status": (
                                claim.evidence_status if claim is not None else None
                            ),
                            "insufficient_reason": reason,
                            "claim_flags": self._adjudication_flags.get(
                                task.packet.claim_id if task.packet else "", []
                            ),
                            "retrieval_needed": retrieval_needed,
                            "packet_fingerprint": (
                                task.packet.fingerprint if task.packet else None
                            ),
                            "packet_ids": (
                                [unit.evidence_id for unit in task.packet.units]
                                if task.packet is not None
                                else []
                            ),
                            "differences": [
                                item.item_id
                                for item in (task.packet.omitted if task.packet else [])
                            ][:MAX_REPORTED_PENDING_CLAIMS],
                            "cluster_ids": (
                                [task.packet.claim_cluster_id]
                                if task.packet is not None
                                and task.packet.claim_cluster_id
                                else []
                            ),
                            "independent_sources": independent,
                            "memory_candidates": memory_candidates,
                            "memory_recalls": memory_recalls,
                            "tool_calls": react.tool_calls,
                            "stop_reason": react.stop_reason,
                        }
                    )

                runs.append(react)
                errors.extend(react.errors)
                errors.extend(verify_errors)
                if claim is None:
                    # A provider or schema failure is not an evidence verdict.
                    # The claim consumed nothing, is not published, and stays
                    # in the continuation queue for the next pass.
                    stopped = True
                    break
                claims.append(claim)
                # Adjudicated, and only now: the verdict exists, so this
                # claim's findings are genuinely consumed.
                adjudicated.add(claim_fingerprint(draft.text))
                # What the *published* claim answers, not what it reached for
                # before it was judged: an obligation the support policy
                # refused is still outstanding, and the critical-target
                # priority list has to see it that way.
                answered_targets.update(claim.target_ids)
                events.append(
                    claim_checked_event(
                        claim,
                        react,
                        index=index,
                        independent_sources=independent,
                        reason=reason,
                        memory_candidates=memory_candidate_count(
                            state, task.claim
                        ),
                        memory_recalls=memory_recall_count(react),
                    )
                )
                if not react.succeeded:
                    # A provider failure — from the loop or from
                    # verification — is non-recoverable; the next claim would
                    # almost certainly repeat it at cost. Claims already
                    # judged are kept, and every claim this pass did not
                    # reach stays in the continuation queue.
                    stopped = True
                    break
            if stopped:
                break

        pending = [
            draft
            for draft in pool
            if claim_fingerprint(draft.text) not in adjudicated
        ]
        # Everything this pass did not adjudicate is persisted, oldest work
        # first: what an earlier drain parked, then what this pass admitted and
        # did not reach, then this pass's never-admitted overflow. Deduplicated
        # because a claim can be both parked and extracted again.
        remaining = _unique_drafts([*parked, *pending, *deferred_now])
        self._remember_pending(remaining)
        deferred_count = len(self._deferred)
        if remaining:
            events.append(
                claims_pending_event(
                    remaining,
                    batches_run=batches_run,
                    claim_batch_size=self._max_claims,
                    claim_batches_per_pass=self._batches_per_pass,
                    deferred_claim_count=deferred_count,
                )
            )

        # A repaired structured reply is a diagnostic the run must be able to
        # read: the response's own finish reason is ``stop``, exactly like a
        # clean first attempt, so without this the categories and field paths
        # of the rejected reply are collected and then dropped.
        events.extend(self._repair_events)
        # One bounded provider call per pass, and only when this pass
        # adjudicated something: with no drafts the candidate list is the
        # stored propositions alone, so the call could only re-merge stored
        # identities against each other with no new evidence. The call is a
        # model call, not a tool call, so it is not gated by
        # ``tool_budget_for`` and ``merged.tool_calls`` is unmoved by it.
        consolidation: ClaimConsolidation | None = None
        consolidation_diagnostics: list[str] = []
        if claims:
            async with self.tracker.agent_span(self.name) as span:
                consolidation = await consolidate_claims(
                    self.provider,
                    # This pass's own adjudications, never the whole published
                    # snapshot: a stored claim is already represented by its
                    # cluster in ``existing``, and resubmitting it would give
                    # one fact two candidates and two identities.
                    claims,
                    existing=list(state.claim_clusters.values()),
                    # The run's whole known registry, a plain overlay rather
                    # than the conflict-detecting reducer: a claim may cite a
                    # passage an earlier pass admitted, and a read-only view
                    # assembled for one provider call is not the place to fail
                    # a run.
                    evidence=list(
                        {**state.evidence_units, **self._new_evidence}.values()
                    ),
                )
                span.set_outputs(
                    {
                        "agent_name": self.name,
                        "phase": "consolidation",
                        "cluster_count": len(consolidation.clusters),
                        "provider_failed": consolidation.provider_failed,
                    }
                )
            self._new_clusters = {
                cluster.cluster_id: cluster
                for cluster in consolidation.clusters
            }
            # The claim-to-cluster link exists on the cluster side already:
            # ``extract_atoms`` stamps each atom with its claim id and
            # ``cluster_for_atom`` folds those into ``member_claim_ids``. The
            # published claim has to carry it too — ``statement_claims``
            # resolves a cluster-addressed statement only through
            # ``claim.cluster_id`` / ``claim.cluster_aliases``, and a cluster
            # id is not a claim id.
            cluster_for_claim = {
                claim_id: cluster
                for cluster in consolidation.clusters
                for claim_id in cluster.member_claim_ids
            }
            claims = [
                _stamp_cluster_verdict(claim, cluster)
                if (cluster := cluster_for_claim.get(claim.claim_id))
                is not None
                else claim
                for claim in claims
            ]
            # A claim no cluster claims is left unstamped rather than given a
            # fabricated identity, and is named here so the omission has a
            # recorded reason.
            consolidation_diagnostics = sorted(
                {
                    *consolidation.diagnostics,
                    *(
                        f"unclustered_claim:{claim.claim_id}"
                        for claim in claims
                        if claim.cluster_id is None
                    ),
                }
            )
            if consolidation.provider_failed:
                # R7: a degraded consolidation is non-halting. The clusters are
                # still published and the claims are still stamped, because
                # their ids are locally derived; the pass is under-merged, and
                # the next pass with a working provider merges what is left.
                errors.append(
                    claim_consolidation_degraded_error(
                        consolidation_diagnostics
                    )
                )
        merged = merge_react_runs(self.name, runs).model_copy(
            update={"errors": errors}
        )
        canonical_claims = merge_claim_snapshot(
            self._prior_claims,
            union_claim_provenance(self._prior_claims, claims),
        )
        # A cluster publishes one verdict, and every member carries it —
        # including a stored member this pass never resubmitted. Re-stamping
        # only this pass's claims left a first-pass ``verified`` claim sitting
        # in the cluster that a later pass contradicted, so the reader was
        # shown the contradicted fact as independently corroborated.
        canonical_claims = [
            _stamp_cluster_verdict(claim, cluster)
            if (
                cluster := _cluster_for_stored_claim(
                    claim, self._new_clusters.values()
                )
            )
            is not None
            else claim
            for claim in canonical_claims
        ]
        events.append(
            fact_check_completed_event(
                canonical_claims,
                tool_calls=merged.tool_calls,
                claim_batch_size=self._max_claims,
                claim_batches_per_pass=self._batches_per_pass,
                batches_run=batches_run,
                pending_claim_count=len(remaining),
                deferred_claim_count=deferred_count,
                memory_candidates=memory_candidates_seen,
                memory_recalls=memory_recalls_seen,
                cluster_count=len(self._new_clusters),
                consolidation_diagnostics=consolidation_diagnostics,
                cluster_aliases=(
                    consolidation.aliases if consolidation is not None else {}
                ),
            )
        )
        result = VerifiedClaims(
            claims=canonical_claims,
            pending_claims=self.pending_claims,
        )
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=merged,
            errors=errors,
            state_update={
                **self.state_update(result, merged),
                "events": events,
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
