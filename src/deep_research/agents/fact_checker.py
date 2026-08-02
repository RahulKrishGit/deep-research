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

from collections.abc import Sequence

from deep_research.agents.errors import agent_error
from deep_research.agents.prompts import (
    CLAIM_EXTRACTION_INSTRUCTION,
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_VERIFICATION_INSTRUCTION,
    CLAIM_VERIFICATION_SYSTEM_PROMPT,
    AgentTask,
    render_finding_digest,
    render_source_quality,
)
from deep_research.agents.researcher import render_evidence
from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.agents.steps import ReActRun
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ClaimVerdict,
    ContractModel,
    ResearchError,
    ResearchState,
)

FACT_CHECKER_NAME = "fact_checker"
DEFAULT_MAX_CLAIMS = 5
DEFAULT_FINDING_DIGEST = 40
# Named distinctly from researcher.DEFAULT_EVIDENCE_CHARS: both are
# re-exported from deep_research.agents, so the names must not collide.
FACT_CHECK_EVIDENCE_CHARS = 4000


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


class ClaimTask(AgentTask):
    """An ``AgentTask`` bound to the claim its loop verifies.

    Carrying the claim on the task is what lets ``finalize(task, run)``
    know which claim it is finalizing without the agent holding mutable
    state across await points.
    """

    claim: ClaimDraft
    claimed_domains: list[str] = []


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


def build_claim_drafts(
    draft: ClaimsDraft,
    *,
    known_urls: Sequence[str],
) -> tuple[list[ClaimDraft], list[str]]:
    """Keep the claims whose sources exist, naming the ones dropped.

    A model that attaches a URL nobody retrieved has invented a citation,
    which is exactly the failure this project refuses to pass downstream.
    Rejection reasons are generated here and never copied from provider
    output, so they are safe to record in ``ResearchError.details``.
    """
    allowed = set(known_urls)
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
        claims.append(ClaimDraft(text=text, source_urls=urls))
    return claims, rejected


def claim_extraction_messages(
    state: ResearchState,
    *,
    max_findings: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured claim draft."""
    findings = list(state.raw_findings)[:max_findings]
    sections = [
        f"## Research question\n{state.original_question}",
        f"## Retrieved findings\n{render_finding_digest(findings)}",
        (
            "## Source quality\n"
            f"{render_source_quality(state.evaluated_sources)}"
        ),
        f"## Response contract\n{CLAIM_EXTRACTION_INSTRUCTION}",
    ]
    return [
        ChatMessage(role="developer", content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def claim_extraction_provider_error(error: Exception) -> ResearchError:
    """Record that claim extraction could not reach the provider.

    Non-recoverable: with no claims there is nothing to verify, so the
    pass ends. ``details`` carries ``exception_type`` only, matching the
    redaction discipline in ``react.py`` and ``researcher.py``.
    """
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_extraction_provider_error",
        message=(
            "The model provider failed while claims were extracted; no "
            "claim was verified."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
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

# Read tools that can carry evidence, mapped to the payload key holding the
# source identifier. save_to_memory is absent by construction: this agent
# never writes.
_EVIDENCE_URL_KEYS = {
    "web_search": "results",
    "web_scraper": "url",
    "document_reader": "source",
    "query_memory": "matches",
}


class ClaimVerdictDraft(ContractModel):
    """One model verdict for one claim, before domain validation.

    ``verdict`` is a plain ``str`` rather than a ``Literal``: a value the
    model invents must become ``insufficient_evidence`` locally, not a
    validation failure that discards the whole verification pass.
    """

    verdict: str
    confidence: float
    evidence: list[str]
    contradictions: list[str]


def _search_urls(data: dict[str, object]) -> list[str]:
    results = data.get("results")
    if not isinstance(results, list):
        return []
    urls: list[str] = []
    for item in results:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(str(item["url"]))
    return urls


def _memory_urls(data: dict[str, object]) -> list[str]:
    matches = data.get("matches")
    if not isinstance(matches, list):
        return []
    urls: list[str] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        candidate = match.get("source_url")
        if not isinstance(candidate, str):
            metadata = match.get("metadata")
            candidate = (
                metadata.get("source_url") if isinstance(metadata, dict) else None
            )
        if isinstance(candidate, str) and candidate.strip():
            urls.append(candidate)
    return urls


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
        result = step.tool_result
        if result is None or not result.success:
            continue
        data = result.data
        if not isinstance(data, dict):
            continue
        if result.tool_name not in _EVIDENCE_URL_KEYS:
            continue
        if result.tool_name == "web_search":
            candidates = _search_urls(data)
        elif result.tool_name == "query_memory":
            candidates = _memory_urls(data)
        elif result.tool_name == "web_scraper":
            url = data.get("url")
            text = data.get("text")
            candidates = (
                [url]
                if isinstance(url, str) and isinstance(text, str) and text.strip()
                else []
            )
        else:
            source = data.get("source")
            candidates = (
                [source]
                if isinstance(source, str) and data.get("chunks")
                else []
            )
        for candidate in candidates:
            url = normalize_source_url(candidate)
            if url and url not in found:
                found.append(url)
    return found


def claimed_domains_for(source_urls: Sequence[str]) -> list[str]:
    """The distinct domains a claim's own sources live on."""
    domains: list[str] = []
    for url in source_urls:
        domain = source_domain(url).casefold()
        if domain not in domains:
            domains.append(domain)
    return domains


def independent_domains(
    urls: Sequence[str],
    *,
    claimed_domains: Sequence[str],
) -> list[str]:
    """Distinct retrieved domains that are not the claim's own.

    A second page from the publisher that made the claim is not
    corroboration, which is the whole point of cross-referencing.
    """
    claimed = {domain.casefold() for domain in claimed_domains}
    found: list[str] = []
    for url in urls:
        domain = source_domain(url).casefold()
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


def resolve_verdict(
    draft: ClaimVerdictDraft,
    *,
    independent: Sequence[str],
) -> tuple[ClaimVerdict, float]:
    """Decide the recorded verdict from the model's answer and the evidence.

    Three local rules override the model, in this order:
    nothing independent was retrieved -> ``insufficient_evidence``;
    the model itself reported contradictions -> ``contradicted``, whatever
    it called the verdict; ``insufficient_evidence`` carries no confidence.
    """
    if not independent:
        return "insufficient_evidence", 0.0
    verdict = normalize_verdict(draft.verdict)
    confidence = _clamp_confidence(draft.confidence)
    if draft.contradictions:
        return "contradicted", confidence
    if verdict == "insufficient_evidence":
        return "insufficient_evidence", 0.0
    return verdict, confidence


def build_claim(
    claim: ClaimDraft,
    draft: ClaimVerdictDraft,
    *,
    independent: Sequence[str],
) -> Claim:
    """Stamp one model verdict into a validated ``Claim`` record."""
    verdict, confidence = resolve_verdict(draft, independent=independent)
    return Claim(
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict=verdict,
        confidence=confidence,
        evidence=list(draft.evidence),
        contradictions=list(draft.contradictions),
    )


def insufficient_claim(claim: ClaimDraft, *, reason: str) -> Claim:
    """Record a claim that could not be judged, with no invented confidence.

    ``reason`` is one of ``INSUFFICIENT_REASONS``; it travels in the
    claim's event metadata rather than on the record, because ``Claim``
    has no field for it and this project does not widen a shared contract
    for one agent's bookkeeping.
    """
    if reason not in INSUFFICIENT_REASONS:
        raise ValueError(f"unknown insufficient-evidence reason: {reason}")
    return Claim(
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict="insufficient_evidence",
        confidence=0.0,
        evidence=[],
        contradictions=[],
    )


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
        f"## Claim\n{task.claim.text}",
        f"## Sources that made the claim\n{claimed}",
        f"## Independent domains retrieved\n{domains}",
        (
            "## Retrieved evidence\n"
            f"{render_evidence(run, limit=evidence_chars)}"
        ),
        f"## Response contract\n{CLAIM_VERIFICATION_INSTRUCTION}",
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
