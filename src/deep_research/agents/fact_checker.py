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

from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.prompts import (
    CLAIM_EXTRACTION_INSTRUCTION,
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_VERIFICATION_INSTRUCTION,
    CLAIM_VERIFICATION_SYSTEM_PROMPT,
    FACT_CHECKER_SYSTEM_PROMPT,
    AgentTask,
    render_finding_digest,
    render_react_messages,
    render_source_quality,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.researcher import merge_react_runs, render_evidence
from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.agents.steps import (
    ReActDecision,
    ReActRun,
    ReActStep,
    summarize_text,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ClaimVerdict,
    ContractModel,
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
        details={"exception_type": type(error).__name__},
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
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.fact_check.completed",
        message="Fact checking complete.",
        metadata={
            "claim_count": len(claims),
            **counts,
            "contradiction_count": contradiction_count,
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
        provider: StructuredCompleter,
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
        """Narrow the run-level task down to one claim's loop."""
        claimed = claimed_domains_for(claim.source_urls)
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
        )

    async def extract_claims(
        self,
        state: ResearchState,
    ) -> tuple[list[ClaimDraft], list[ResearchError], bool]:
        """Turn the finished research pass into checkable claim drafts.

        Makes no provider call when there are no findings, so the
        extraction step can never invent a claim out of nothing.
        """
        if not state.raw_findings:
            return [], [no_findings_to_check_error()], False

        try:
            response = await self.provider.complete_structured(
                claim_extraction_messages(
                    state, max_findings=self._finding_digest
                ),
                ClaimsDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            return [], [claim_extraction_provider_error(error)], True

        claims, rejected = build_claim_drafts(
            response, known_urls=known_source_urls(state)
        )
        errors = [invalid_claim_error(rejected)] if rejected else []
        return claims[: self._max_claims], errors, False

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
            return insufficient_claim(task.claim, reason="loop_failed"), (
                "loop_failed"
            ), [], False

        independent = independent_domains(
            retrieved_source_urls(run), claimed_domains=task.claimed_domains
        )
        if not independent:
            return (
                insufficient_claim(task.claim, reason="no_independent_source"),
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
        except OpenAIProviderError as error:
            return (
                insufficient_claim(task.claim, reason="provider_unavailable"),
                "provider_unavailable",
                [claim_verification_provider_error(error)],
                True,
            )

        return (
            build_claim(task.claim, draft, independent=independent),
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
        """Verified claims and errors only. ``run`` adds the events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["verified_claims"] = list(result.claims)
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
            return await self.provider.complete_structured(
                render_react_messages(
                    system_prompt=self.system_prompt(task),
                    task=task,
                    descriptors=toolset.descriptors(),
                    scratchpad=self.scratchpad.recent(
                        self.config.prompt_context_entries
                    ),
                    iteration=iteration,
                    max_iterations=self.config.max_iterations,
                ),
                ReActDecision,
                agent_name=self.name,
            )

        react = await run_react_loop(
            agent_name=self.name,
            tracker=self.tracker,
            tools=toolset,
            decide=decide,
            max_iterations=self.config.max_iterations,
            tool_budget=self.config.tool_budget,
            on_step=self._record_step,
            is_sufficient=self.is_sufficient,
            summary_limit=self.config.observation_summary_chars,
        )
        return react.model_copy(
            update={"errors": [*react.errors, *self.scratchpad.drain_errors()]}
        )

    async def run(self, state: ResearchState) -> AgentRun[VerifiedClaims]:
        """Extract claims, then verify each in its own bounded loop."""
        base_task = self.build_task(state)
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
        events.append(
            fact_check_completed_event(claims, tool_calls=merged.tool_calls)
        )
        result = VerifiedClaims(claims=claims)
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
