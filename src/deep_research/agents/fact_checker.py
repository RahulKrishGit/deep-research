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

from collections.abc import Mapping, Sequence
from typing import Literal
from urllib.parse import urlsplit

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.identity import (
    claim_fingerprint,
    deduplicate_findings,
    finding_fingerprint,
    merge_claim_snapshot,
)
from deep_research.agents.prompts import (
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
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    MAX_CONSUMED_COVERAGE_IDS,
    MAX_CONSUMED_FINDING_FINGERPRINTS,
    Claim,
    ClaimVerdict,
    ContractModel,
    EvidencePassage,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
)

FACT_CHECKER_NAME = "fact_checker"
DEFAULT_MAX_CLAIMS = 5
DEFAULT_FINDING_DIGEST = 40
# Named distinctly from researcher.DEFAULT_EVIDENCE_CHARS: both are
# re-exported from deep_research.agents, so the names must not collide.
FACT_CHECK_EVIDENCE_CHARS = 4000
MAX_PASSAGE_EXCERPT_CHARS = 1000
MAX_PASSAGE_LOCATOR_CHARS = 200


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
    records the evidence it consumed.
    """

    claim: ClaimDraft
    claimed_domains: list[str] = []
    consumed_finding_fingerprints: list[str] = []
    consumed_coverage_ids: list[str] = []


class VerifiedClaims(ContractModel):
    """The validated claims ``FactCheckerAgent`` produces.

    Never sent to the provider — ``ClaimsDraft`` and ``ClaimVerdictDraft``
    are. Do not route this agent through ``complete_output``.
    """

    claims: list[Claim] = []


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
    cited = {normalize_source_url(url) for url in draft.source_urls}
    attributed: dict[str, Finding] = {}
    for finding in findings:
        url = normalize_source_url(finding.source_url)
        if url in cited and url not in attributed:
            attributed[url] = finding
    fingerprints: list[str] = []
    consumed_coverage: list[str] = []
    for finding in attributed.values():
        _append_unique(fingerprints, finding_fingerprint(finding))
        coverage_id = coverage_ids.get(_collapsed(finding.related_sub_topic))
        if coverage_id:
            _append_unique(consumed_coverage, coverage_id)
    return (
        fingerprints[:MAX_CONSUMED_FINDING_FINGERPRINTS],
        consumed_coverage[:MAX_CONSUMED_COVERAGE_IDS],
    )


def coverage_ids_by_title(state: ResearchState) -> dict[str, str]:
    """Map each planned sub-topic's collapsed title to its coverage id."""
    coverage_ids: dict[str, str] = {}
    for topic in state.sub_topics:
        coverage_ids.setdefault(_collapsed(topic.title), topic.coverage_id)
    return coverage_ids


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


def build_claim_drafts(
    draft: ClaimsDraft,
    *,
    known_urls: Sequence[str],
    prior_claims: Sequence[Claim] = (),
    new_source_urls: Sequence[str] = (),
    critique_texts: Sequence[str] = (),
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
    checked unless one of its own sources carries such new evidence, or the
    Critic explicitly asked for re-verification — the Critic's free-text
    override stays separate from provenance-based idempotence.
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
}

class EvidencePassageDraft(ContractModel):
    """One provider-reported passage before local provenance validation."""

    source_url: str
    source_title: str
    locator: str
    excerpt: str
    stance: Literal["supports", "contradicts"]


class ClaimVerdictDraft(ContractModel):
    """One model verdict for one claim, before domain validation.

    ``verdict`` is a plain ``str`` rather than a ``Literal``: a value the
    model invents must become ``insufficient_evidence`` locally, not a
    validation failure that discards the whole verification pass.
    """

    verdict: str
    confidence: float
    passages: list[EvidencePassageDraft]


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
    draft: ClaimVerdictDraft,
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
    draft: ClaimVerdictDraft,
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
        if verdict == "verified":
            return "verified", confidence
        return "unverified", confidence
    return "unverified", confidence


def build_claim(
    claim: ClaimDraft,
    draft: ClaimVerdictDraft,
    *,
    independent: Sequence[str] = (),
    retrieved_urls: Sequence[str] = (),
    upstream_read_urls: Sequence[str] = (),
    claimed_publishers: Sequence[str] | None = None,
    consumed_finding_fingerprints: Sequence[str] = (),
    consumed_coverage_ids: Sequence[str] = (),
) -> Claim:
    """Stamp one model verdict into a validated ``Claim`` record.

    ``retrieved_urls`` is the verification loop's own read set and
    ``upstream_read_urls`` the URLs this run's findings came from; the
    passages the verdict is resolved from may cite either.
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
        consumed_finding_fingerprints=list(consumed_finding_fingerprints),
        consumed_coverage_ids=list(consumed_coverage_ids),
    )


def insufficient_claim(
    claim: ClaimDraft,
    *,
    reason: str,
    consumed_finding_fingerprints: Sequence[str] = (),
    consumed_coverage_ids: Sequence[str] = (),
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
        insufficient_reason=reason,
        consumed_finding_fingerprints=list(consumed_finding_fingerprints),
        consumed_coverage_ids=list(consumed_coverage_ids),
    )


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


def claim_checked_event(
    claim: Claim,
    run: ReActRun,
    *,
    index: int,
    independent_sources: int,
    reason: str | None,
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
) -> ResearchEvent:
    """Report the whole fact-checking pass.

    Verdict counts are zero-filled by ``verdict_counts``, so a consumer
    never has to guess whether a missing key means zero.
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
            "tool_calls": tool_calls,
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
        max_claims: int = DEFAULT_MAX_CLAIMS,
        finding_digest: int = DEFAULT_FINDING_DIGEST,
        evidence_chars: int = FACT_CHECK_EVIDENCE_CHARS,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if max_claims < 1:
            raise ValueError("max_claims must be at least 1")
        if finding_digest < 1:
            raise ValueError("finding_digest must be at least 1")
        if evidence_chars < 1:
            raise ValueError("evidence_chars must be at least 1")
        self._max_claims = max_claims
        self._finding_digest = finding_digest
        self._evidence_chars = evidence_chars
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
        self._pending_provenance: dict[str, tuple[list[str], list[str]]] = {}

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

    def claim_task(self, base: AgentTask, claim: ClaimDraft) -> ClaimTask:
        """Narrow the run-level task down to one claim's loop.

        The provenance this claim's extraction pass attributed to it travels
        on the task, so the verdict it produces records the evidence it
        consumed without the agent consulting mutable state mid-loop.
        """
        claimed = claimed_domains_for(claim.source_urls)
        fingerprints, coverage_ids = self._pending_provenance.get(
            claim_fingerprint(claim.text), ([], [])
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
            consumed_finding_fingerprints=fingerprints,
            consumed_coverage_ids=coverage_ids,
        )

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
        draft, which findings and coverage topics it consumed.
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
        has_reverification_request = any(
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
            self._pending_provenance = {}
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
            self._pending_provenance = {}
            return [], [claim_extraction_provider_error(error)], True

        claims, rejected = build_claim_drafts(
            response,
            known_urls=known_source_urls(state),
            prior_claims=self._prior_claims,
            new_source_urls=new_evidence_urls,
            critique_texts=critique_texts,
        )
        errors = [invalid_claim_error(rejected)] if rejected else []
        accepted = claims[: self._max_claims]
        coverage_ids = coverage_ids_by_title(state)
        self._pending_provenance = {
            claim_fingerprint(item.text): consumed_provenance(
                item, findings=visible_findings, coverage_ids=coverage_ids
            )
            for item in accepted
        }
        return accepted, errors, False

    async def verify_claim(
        self,
        task: ClaimTask,
        run: ReActRun,
    ) -> tuple[Claim, str | None, list[ResearchError], bool]:
        """Judge one claim from one finished loop.

        Returns ``(claim, reason, errors, provider_failed)``. ``reason`` is
        an ``INSUFFICIENT_REASONS`` key when the claim could not be judged
        and ``None`` otherwise. No provider call is made when the loop
        failed or retrieved nothing independent, so a verdict can never be
        invented over an empty evidence section.
        """
        if not run.succeeded:
            # No model ever looked at this finding: the loop died before the
            # verdict was requested. Recording provenance here would make
            # ``_finding_is_new`` call it consumed and let a transient outage
            # suppress its re-extraction for the rest of the run, so the two
            # lists are deliberately left empty.
            return insufficient_claim(
                task.claim,
                reason="loop_failed",
            ), (
                "loop_failed"
            ), [], False

        retrieved_urls = retrieved_source_urls(run)
        independent = independent_domains(
            retrieved_urls, claimed_domains=task.claimed_domains
        )
        if not independent:
            return (
                insufficient_claim(
                    task.claim,
                    reason="no_independent_source",
                    consumed_finding_fingerprints=(
                        task.consumed_finding_fingerprints
                    ),
                    consumed_coverage_ids=task.consumed_coverage_ids,
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
                ClaimVerdictDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            # Same reasoning as ``loop_failed``: the verdict was never read,
            # so nothing was judged and nothing may look consumed.
            return (
                insufficient_claim(
                    task.claim,
                    reason="provider_unavailable",
                ),
                "provider_unavailable",
                [claim_verification_provider_error(error)],
                True,
            )

        return (
            build_claim(
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
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["verified_claims"] = merge_claim_snapshot(
                self._prior_claims,
                union_claim_provenance(self._prior_claims, result.claims),
            )
        return update

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
            propagate_provider_errors=False,
        )
        return react.model_copy(
            update={"errors": [*react.errors, *self.scratchpad.drain_errors()]}
        )

    async def run(self, state: ResearchState) -> AgentRun[VerifiedClaims]:
        """Extract claims, then verify each in its own bounded loop."""
        base_task = self.build_task(state)
        self._prior_claims = list(state.verified_claims)
        # What the researcher read upstream travels on the agent's state the
        # same way the prior snapshot does, because ``verify_claim`` is
        # handed only the claim's loop. The claims to verify are the
        # findings' own, so the findings' URLs are exactly the upstream read
        # set.
        self._upstream_read_urls = known_source_urls(state)
        # Provenance belongs to the extraction pass about to run; anything
        # left from an earlier run on this instance must not leak into it.
        self._pending_provenance = {}
        events: list[ResearchEvent] = []
        errors: list[ResearchError] = []
        claims: list[Claim] = []
        runs: list[ReActRun] = []

        async with self.tracker.agent_span(self.name) as span:
            drafts, extraction_errors, extraction_failed = (
                await self.extract_claims(state)
            )
            errors.extend(extraction_errors)
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "phase": "extraction",
                    "claim_count": len(drafts),
                    "provider_failed": extraction_failed,
                }
            )
        events.append(
            claims_extracted_event(
                claim_count=len(drafts),
                findings_considered=len(state.raw_findings),
                sources_considered=len(state.evaluated_sources),
            )
        )

        for index, draft in enumerate(drafts, start=1):
            task = self.claim_task(base_task, draft)
            async with self.tracker.agent_span(self.name) as span:
                react = await self._check_claim(task)
                claim, reason, verify_errors, verify_failed = (
                    await self.verify_claim(task, react)
                )
                if verify_failed:
                    # Mirror the loop-level provider_error path so the
                    # merged run never claims "finished" over an abort that
                    # actually happened during verification.
                    react = react.model_copy(
                        update={"stop_reason": "provider_error"}
                    )
                independent = len(
                    independent_domains(
                        retrieved_source_urls(react),
                        claimed_domains=task.claimed_domains,
                    )
                )
                span.set_outputs(
                    {
                        "agent_name": self.name,
                        "claim_index": index,
                        "verdict": claim.verdict,
                        "independent_sources": independent,
                        "tool_calls": react.tool_calls,
                        "stop_reason": react.stop_reason,
                    }
                )

            runs.append(react)
            claims.append(claim)
            errors.extend(react.errors)
            errors.extend(verify_errors)
            events.append(
                claim_checked_event(
                    claim,
                    react,
                    index=index,
                    independent_sources=independent,
                    reason=reason,
                )
            )
            if not react.succeeded:
                # A provider failure — from the loop or from verification —
                # is non-recoverable; the next claim would almost certainly
                # repeat it at cost. Claims already judged are kept.
                break

        merged = merge_react_runs(self.name, runs).model_copy(
            update={"errors": errors}
        )
        canonical_claims = merge_claim_snapshot(
            self._prior_claims,
            union_claim_provenance(self._prior_claims, claims),
        )
        events.append(
            fact_check_completed_event(
                canonical_claims, tool_calls=merged.tool_calls
            )
        )
        result = VerifiedClaims(claims=canonical_claims)
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=merged,
            errors=errors,
            state_update={
                **self.state_update(result, merged),
                "events": events,
            },
        )
