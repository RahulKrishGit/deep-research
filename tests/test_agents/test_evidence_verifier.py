"""The Evidence Verifier (spec §5)."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from deep_research.agents.evidence_verifier import (
    _CONTEXT_CHECK_REPLY_EXAMPLES,
    _checked,
    _owns_page,
    _page_date_basis,
    CONTEXT_CHECK_BATCH_SIZE,
    CONTEXT_CHECK_CONCURRENCY,
    EVIDENCE_VERIFIER_NAME,
    ContextCheckDraft,
    ContextItem,
    EvidenceVerifierAgent,
    FigureCheckDraft,
    StatementCheckDraft,
    StatementCheckItem,
    StatementVerdictDraft,
    VerifiedFindings,
    check_statements,
    context_check_messages,
    context_passage,
    evaluated_issuer,
    evaluated_page_date,
    figure_match,
    page_owner,
    resolve_attribution,
    statement_check_messages,
    unchecked_context,
    verify_finding,
)
from deep_research.agents.prompts import STRUCTURED_REQUEST_END
from deep_research.agents.steps import ReActRun
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderConfigurationError,
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
)
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    ResearchState,
    ScoredSource,
    SourceTemporal,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read

SNIPPET = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"


def test_figure_match_confirms_the_snippet_is_on_the_page() -> None:
    read = make_read()
    finding = make_finding(
        read, SNIPPET,
        figures=[figure("10.4", "GW", "2024", "actual"), figure("19.6", "GW", "2025", "forecast")],
    )
    match = figure_match(finding, {read.read_id: read})
    assert match.read_found and match.snippet_on_page


def test_a_snippet_that_is_not_on_the_page_is_reported() -> None:
    read = make_read()
    finding = make_finding(read, "EIA says 10.4 GW was added in 2024.",
                           figures=[figure("10.4", "GW")])
    match = figure_match(finding, {read.read_id: read})
    assert match.read_found and not match.snippet_on_page


def test_a_missing_read_is_reported() -> None:
    finding = make_finding(make_read(), SNIPPET, figures=[figure("10.4", "GW")])
    match = figure_match(finding, {})
    assert not match.read_found


def test_the_snippet_check_is_cosmetic() -> None:
    read = make_read()
    finding = make_finding(read, SNIPPET.upper(), figures=[figure("10,400", "MW")])
    assert figure_match(finding, {read.read_id: read}).snippet_on_page


# ---------------------------------------------------------------------------
# the Context Check's enforcement (spec §5.2)
# ---------------------------------------------------------------------------


def test_the_reply_examples_correction_appears_in_its_own_evidence_words() -> None:
    """The example shown to the model must obey the same rule code enforces
    (§5.2): a correction is kept only when it appears in evidence_words
    itself, not merely somewhere else in the passage."""
    _, payload = _CONTEXT_CHECK_REPLY_EXAMPLES[0]
    reply_figure = json.loads(payload)["figures"][0]
    assert reply_figure["scope"] in reply_figure["evidence_words"]


WOODMAC_URL = "https://www.woodmac.com/press-releases/2025-us-energy-storage"
WOODMAC_PAGE = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery energy "
    "storage system installations in 2025, a 52% increase over 2024, across all "
    "segments. Grid-scale storage installations are forecasted to reach 13.3 GW in 2025."
)
RELAY_URL = "https://www.utilitydive.com/news/storage-2025"
RELAY_PAGE = (
    "According to Wood Mackenzie, utility-scale installations reached 16 GW in 2025. "
    "Analysts expect further growth."
)
SNIPPET_189 = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery energy "
    "storage system installations in 2025"
)


def _item(read, finding) -> ContextItem:
    return ContextItem(
        label="F01", finding=finding, read=read,
        passage=context_passage(read, finding.locator, finding.snippet),
        match=figure_match(finding, {read.read_id: read}),
    )


def _reply(**overrides: object) -> FigureCheckDraft:
    fields = dict(finding="F01", figure=1, period="2025", scope=None, attribution="own",
                  organisation="Wood Mackenzie", kind="actual",
                  evidence_words=SNIPPET_189, verdict="confirm", reason="As stated.")
    fields.update(overrides)
    return FigureCheckDraft(**fields)


def _woodmac_finding(**fields: object):
    read = make_read(WOODMAC_PAGE, url=WOODMAC_URL, title="2025 storage record | Wood Mackenzie")
    finding = make_finding(read, SNIPPET_189,
                           figures=[figure("18.9", "gigawatts", "2025", "actual")], **fields)
    return read, finding


def test_a_scope_correction_the_page_carries_is_applied() -> None:
    read, finding = _woodmac_finding(measure_scope="Grid-scale")
    words = SNIPPET_189 + ", a 52% increase over 2024, across all segments"
    result = verify_finding(_item(read, finding), {1: _reply(
        scope="all segments", evidence_words=words, verdict="correct")})
    assert result.status == "verified_corrected"
    [figure_result] = result.figure_results
    assert figure_result.kept and figure_result.context.scope == "all segments"


def test_invented_evidence_words_are_rejected() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), {1: _reply(
        evidence_words="installations of 18.9 GW of grid-scale batteries in 2025")})
    assert result.status == "dropped"
    assert result.figure_results[0].dropped_reason == "evidence_not_on_page"


def test_a_correction_the_page_does_not_carry_drops_the_figure() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), {1: _reply(period="2026", verdict="correct")})
    assert result.figure_results[0].dropped_reason == "correction_not_on_page"


def test_a_correction_found_only_elsewhere_in_the_passage_is_refused() -> None:
    """evidence_words is where THIS figure states its period and scope; the
    passage's other sentences may not stand in for it. SNIPPET_189 states the
    2025 figure; the wider passage separately mentions "2024" in its very
    next clause ("a 52% increase over 2024"), which must not license
    correcting THIS figure's period to 2024.
    """
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), {1: _reply(
        period="2024", evidence_words=SNIPPET_189, verdict="correct")})
    assert result.figure_results[0].dropped_reason == "correction_not_on_page"


def test_a_scope_correction_not_on_the_page_is_dropped() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), {1: _reply(
        scope="residential only", verdict="correct")})
    assert result.figure_results[0].dropped_reason == "correction_not_on_page"


def test_a_rejected_figure_drops_and_the_rest_survive() -> None:
    read, finding = _woodmac_finding()
    finding = finding.model_copy(update={"figures": [
        figure("18.9", "gigawatts", "2025", "actual"), figure("52", "%", "2025", "actual")]})
    snippet = SNIPPET_189 + ", a 52% increase over 2024"
    finding = finding.model_copy(update={"snippet": snippet})
    result = verify_finding(_item(read, finding), {
        1: _reply(evidence_words=snippet),
        2: _reply(figure=2, verdict="reject", evidence_words=snippet, reason="A growth rate, not a capacity."),
    })
    assert result.status == "verified_corrected"
    assert [r.kept for r in result.figure_results] == [True, False]


def test_a_missing_reply_leaves_a_matched_figure_unchecked() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), None)
    assert result.status == "verified" and result.context_unchecked
    assert result.figure_results[0].context.attribution == "own"


def test_a_reply_that_omits_a_figure_drops_it_when_the_snippet_lacks_it() -> None:
    """P1-2: a reply that answers its batch but leaves a figure out is no
    evidence about that figure -- the model may have left it out exactly
    because it cannot find it stated. Deterministic code keeps an unjudged
    figure only when the snippet itself states it (``figure_in_text``):
    the omitted 52% is not in the snippet, so it is dropped with
    ``context_unavailable``, while the confirmed 18.9 GW stays checked.
    """
    read, finding = _woodmac_finding()
    finding = finding.model_copy(update={"figures": [
        figure("18.9", "gigawatts", "2025", "actual"),
        figure("52", "%", "2025", "actual"),
    ]})
    result = verify_finding(_item(read, finding), {1: _reply()})
    assert [r.kept for r in result.figure_results] == [True, False]
    assert result.figure_results[1].dropped_reason == "context_unavailable"
    assert result.figure_results[0].context.scope is None
    assert result.status == "verified_corrected" and not result.context_unchecked


def test_a_relay_needs_its_originator_named_on_the_page() -> None:
    read = make_read(RELAY_PAGE, url=RELAY_URL, title="Storage in 2025 | Utility Dive")
    finding = make_finding(read, "According to Wood Mackenzie, utility-scale installations reached 16 GW in 2025.")
    assert resolve_attribution(proposed="relayed", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=None) == ("relayed", "Wood Mackenzie")
    # The page's own title names it "Utility Dive", matching its host
    # (verified_facts.same_organisation): page_owner now reports that name
    # rather than the bare "utilitydive.com" label (this round's own fix).
    assert resolve_attribution(proposed="relayed", organisation="BloombergNEF",
                               finding=finding, read=read, issuer=None) == ("unattributed", "Utility Dive")


def test_an_admitted_attribution_makes_a_relay() -> None:
    read = make_read(RELAY_PAGE, url=RELAY_URL, title="Storage in 2025 | Utility Dive")
    finding = make_finding(read, "utility-scale installations reached 16 GW in 2025.",
                           attributed_issuer="Wood Mackenzie",
                           attribution_quote="According to Wood Mackenzie")
    assert resolve_attribution(proposed="unattributed", organisation=None,
                               finding=finding, read=read, issuer=None) == ("relayed", "Wood Mackenzie")


def test_the_source_evaluators_issuer_names_the_pages_own_organisation() -> None:
    # PD-25: a .com page with no copyright line is Wood Mackenzie's own when the
    # Source Evaluator validated that issuer for the read; without it, the host.
    read = make_read("The U.S. storage market will install 15 GW in 2025, a record year.",
                     url="https://www.woodmac.com/press-releases/q1-2025", title="US storage outlook")
    finding = make_finding(read, "The U.S. storage market will install 15 GW in 2025, a record year.")
    source = ScoredSource(url=read.resolved_url, title=read.title, rationale="Validated issuer.",
                          authority_score=0.8, recency_score=0.8, relevance_score=0.8,
                          overall_score=0.8, identity_anchors={"issuer": "Wood Mackenzie"})
    issuer = evaluated_issuer([source], read)
    assert issuer == "Wood Mackenzie"
    assert resolve_attribution(proposed="own", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=issuer) == ("own", "Wood Mackenzie")
    assert resolve_attribution(proposed=None, organisation=None,
                               finding=finding, read=read, issuer=issuer) == ("own", "Wood Mackenzie")
    assert resolve_attribution(proposed="own", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=None) == ("own", "woodmac.com")


# ---------------------------------------------------------------------------
# the Figure Match consumers Task 1.2's review flagged for this task, plus
# PD-26: a context_unchecked finding keeps its Figure Match status and flag.
# ---------------------------------------------------------------------------


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How much battery storage capacity was added?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _evidence_verifier(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    config: AgentRuntimeConfig | None = None,
) -> EvidenceVerifierAgent:
    return EvidenceVerifierAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name=EVIDENCE_VERIFIER_NAME, max_entries=20,
        ),
        config=config,
    )


def _bare_finding(content: str, **fields: object) -> Finding:
    values: dict[str, object] = dict(
        content=content, source_url="https://example.test/f1",
        source_title="Example", extracted_at="2026-09-24T00:00:00+00:00",
        confidence=0.9, related_sub_topic="Battery storage",
    )
    values.update(fields)
    return Finding(**values)


def _metric_sentence(index: int) -> str:
    return f"Site {index:03d} added {10 + index} GW of capacity in 2025."


def _metrics_page(count: int) -> str:
    return " ".join(_metric_sentence(i) for i in range(count))


def _metric_finding(read, index: int) -> Finding:
    return make_finding(
        read, _metric_sentence(index),
        figures=[figure(str(10 + index), "GW", "2025", "actual")],
        content=f"Site {index:03d} capacity finding",
    )


def _confirm_reply(messages: list, schema: type) -> ContextCheckDraft:
    """Answer every listed figure with a plain confirmation.

    Reads the batch's own labels and snippets back out of the request body,
    so it answers correctly whichever batch (and whichever relabelling) it is
    handed -- the same way a real reply is keyed to the request it answers.
    """
    del schema
    section = messages[1].content.split("# Figures to check\n", 1)[1]
    section = section.split("\n\n" + STRUCTURED_REQUEST_END, 1)[0]
    figures = []
    for block in re.split(r"\n\n(?=## )", section):
        label = re.match(r"## (F\d+)", block).group(1)
        snippet = re.search(r"snippet: (.*)", block).group(1)
        figures.append(FigureCheckDraft(
            finding=label, figure=1, attribution="own", kind="actual",
            evidence_words=snippet, verdict="confirm", reason="As stated.",
        ))
    return ContextCheckDraft(figures=figures)


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


@pytest.mark.asyncio
async def test_the_context_check_call_is_fingerprinted(tracker: Tracker) -> None:
    """A run's ``call_fingerprints`` names every kind of call it made, so a
    caller can tell which model/effort configuration produced it -- the same
    bookkeeping every other structured call in this codebase performs."""
    read = make_read()
    finding = make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")])
    completer = ScriptedCompleter(outputs=[_confirm_reply])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert "ContextCheckDraft" in outcome.call_fingerprints


@pytest.mark.asyncio
async def test_findings_are_checked_five_per_call(tracker: Tracker) -> None:
    read = make_read(_metrics_page(20), url="https://example.test/batch", title="Batch metrics")
    findings = [_metric_finding(read, i) for i in range(20)]
    completer = ScriptedCompleter(outputs=[_confirm_reply] * 4)
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert [call[0] for call in completer.calls] == ["ContextCheckDraft"] * 4
    judged = outcome.state_update["verified_findings"]
    assert len(judged) == 20
    assert all(
        f.verification is not None and f.verification.status == "verified"
        for f in judged
    )


@pytest.mark.asyncio
async def test_failed_batch_marks_findings_context_unchecked(tracker: Tracker) -> None:
    read = make_read()
    findings = [
        make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")],
                    content=f"Finding {i}")
        for i in range(3)
    ]
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    judged = outcome.state_update["verified_findings"]
    assert len(judged) == 3
    for finding in judged:
        assert finding.verification.status == "verified"
        assert finding.verification.context_unchecked is True
        [result] = finding.verification.figure_results
        assert result.kept
    error_types = [error.error_type for error in outcome.state_update["errors"]]
    assert error_types.count("evidence_verifier_context_check_failed") == 1


@pytest.mark.asyncio
async def test_a_figure_the_snippet_lacks_is_dropped_when_the_batch_fails(
    tracker: Tracker,
) -> None:
    """P1-2: a failed batch keeps a figure only when the snippet itself
    states it. 19.6 GW is on the page but not in this finding's snippet, so
    deterministic code cannot confirm it: the figure is dropped with
    ``context_unavailable`` and the finding with it.
    """
    read = make_read()
    finding = make_finding(read, SNIPPET, figures=[figure("19.6", "GW", "2025", "forecast")])
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    [judged] = outcome.state_update["verified_findings"]
    assert judged.verification.status == "dropped"
    assert judged.verification.dropped_reason == "all_figures_dropped"
    assert judged.verification.context_unchecked is False
    [result] = judged.verification.figure_results
    assert not result.kept and result.dropped_reason == "context_unavailable"
    error_types = [error.error_type for error in outcome.state_update["errors"]]
    assert error_types.count("evidence_verifier_context_check_failed") == 1


@pytest.mark.asyncio
async def test_a_truncated_batch_is_asked_again_once_in_halves(tracker: Tracker) -> None:
    read = make_read(_metrics_page(4), url="https://example.test/halves", title="Halves")
    findings = [_metric_finding(read, i) for i in range(4)]
    completer = ScriptedCompleter(
        outputs=[_output_limit_error(), _confirm_reply, _confirm_reply]
    )
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert [call[0] for call in completer.calls] == ["ContextCheckDraft"] * 3
    judged = outcome.state_update["verified_findings"]
    assert len(judged) == 4
    assert all(f.verification.status == "verified" for f in judged)
    assert outcome.state_update["errors"] == []


@pytest.mark.asyncio
async def test_only_new_findings_are_verified(tracker: Tracker) -> None:
    f1 = _bare_finding("A prior finding with no figures.")
    f1_verified = f1.model_copy(
        update={"verification": FindingVerification(status="verified", figure_results=[])}
    )
    read = make_read()
    f2 = make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")],
                      content="A new finding")
    completer = ScriptedCompleter(outputs=[_confirm_reply])
    agent = _evidence_verifier(tracker, completer)
    state = _state(
        raw_findings=[f1, f2], verified_findings=[f1_verified],
        read_records={read.read_id: read},
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert len(completer.calls) == 1
    body = completer.calls[0][2][1].content
    assert body.count("## F") == 1
    assert SNIPPET in body
    judged = outcome.state_update["verified_findings"]
    assert judged[0] == f1_verified
    assert len(judged) == 2
    assert judged[1].verification.status == "verified"


@pytest.mark.asyncio
async def test_a_read_not_found_drops_the_finding_at_the_agent_level(tracker: Tracker) -> None:
    finding = make_finding(make_read(), SNIPPET, figures=[figure("10.4", "GW")])
    completer = ScriptedCompleter()
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    [judged] = outcome.state_update["verified_findings"]
    assert judged.verification.status == "dropped"
    assert judged.verification.dropped_reason == "read_not_found"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_snippet_not_on_the_page_drops_the_finding_at_the_agent_level(
    tracker: Tracker,
) -> None:
    read = make_read()
    finding = make_finding(read, "EIA says 10.4 GW was added in 2024.", figures=[figure("10.4", "GW")])
    completer = ScriptedCompleter()
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    [judged] = outcome.state_update["verified_findings"]
    assert judged.verification.status == "dropped"
    assert judged.verification.dropped_reason == "snippet_not_on_page"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_batch_truncated_twice_gives_context_unchecked_and_two_errors(
    tracker: Tracker,
) -> None:
    """The first attempt truncates and splits in half; when both halves also
    truncate, each half's own failure is recorded (2 errors), and every
    finding keeps its Figure Match result as unchecked context rather than
    being dropped or silently retried a third time."""
    read = make_read(_metrics_page(4), url="https://example.test/twice", title="Twice")
    findings = [_metric_finding(read, i) for i in range(4)]
    completer = ScriptedCompleter(outputs=[
        _output_limit_error(), _output_limit_error(), _output_limit_error(),
    ])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert len(completer.calls) == 3
    judged = outcome.state_update["verified_findings"]
    assert len(judged) == 4
    assert all(
        f.verification.status == "verified" and f.verification.context_unchecked
        for f in judged
    )
    error_types = [e.error_type for e in outcome.state_update["errors"]]
    assert error_types.count("evidence_verifier_context_check_failed") == 2


class _ConcurrencyProbe:
    """A completer that records the peak number of concurrent calls in flight.

    Real overlap requires a genuine await point inside the call, which
    ``ScriptedCompleter`` never yields on; this fake sleeps so several batches
    can actually be in flight together, the only way to observe the cap.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, list]] = []
        self._in_flight = 0
        self.max_in_flight = 0

    async def complete_structured(self, messages, schema, *, agent_name=None,
                                  max_tokens=None, reasoning_effort=None):
        self.calls.append((schema.__name__, agent_name, list(messages)))
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0.01)
            return _confirm_reply(list(messages), schema)
        finally:
            self._in_flight -= 1

    async def complete_react(self, messages, tools, *, agent_name=None, max_tokens=None):
        raise AssertionError("the Context Check never uses complete_react")


@pytest.mark.asyncio
async def test_the_concurrency_cap_is_eight(tracker: Tracker) -> None:
    """§5.2/D8: batches run concurrently, at most 8 at once. More batches
    than the cap, all racing for the same semaphore, prove the cap holds
    rather than merely happening to fit."""
    batch_count = 10
    total = batch_count * CONTEXT_CHECK_BATCH_SIZE
    read = make_read(_metrics_page(total), url="https://example.test/cap", title="Cap test")
    findings = [_metric_finding(read, i) for i in range(total)]
    probe = _ConcurrencyProbe()
    agent = _evidence_verifier(tracker, probe)
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert len(probe.calls) == batch_count
    assert probe.max_in_flight == CONTEXT_CHECK_CONCURRENCY
    assert len(outcome.state_update["verified_findings"]) == total


def test_a_relayed_organisation_that_owns_the_page_resolves_to_own() -> None:
    """PD-8 row 1: when the Context Check proposes 'relayed' for the
    organisation whose own page this actually is, code overrides it to
    'own' rather than mislabelling an issuer's own figure as a relay of
    itself."""
    text = (
        "U.S. battery capacity increased 66% in 2024. Generators added 10.4 "
        "GW of new battery storage capacity in 2024, according to the U.S. "
        "Energy Information Administration."
    )
    read = make_read(text)  # eia.gov, titled "U.S. battery capacity increased 66% in 2024"
    finding = make_finding(read, text)
    assert resolve_attribution(
        proposed="relayed", organisation="U.S. Energy Information Administration",
        finding=finding, read=read, issuer=None,
    ) == ("own", "U.S. Energy Information Administration")


def test_a_cover_credited_organisation_resolves_to_relayed_far_from_the_figure() -> None:
    """Task 3.6's audit-2 shape (spec §5, PD-8 row 2): a mirrored PDF credits
    its originator only on its cover/masthead, many passages before the
    figure itself, with no attribution cue anywhere near the figure's own
    locator. The document's opening still identifies the whole document's
    author, so the relay resolves correctly rather than falling through to
    'unattributed, the relaying host'.
    """
    cover = "Short-Term Energy\nOutlook\nSTEO\nJanuary 2025"
    masthead = (
        "The U.S. Energy Information Administration (EIA), the statistical "
        "and analytical agency within the U.S. Department of Energy (DOE), "
        "prepared this report."
    )
    figure_text = "battery storage capacity growing by 47% (14 GW) in 2025"
    passages = {
        "page-1-chunk-0": cover,
        "page-2-chunk-1": masthead,
        "page-3-chunk-2": "Macroeconomic assumptions are a key driver in the forecast.",
        "page-13-chunk-13": "Retail sales of electricity into the industrial sector increase.",
        "page-14-chunk-14": f"integrate onto the power grid, with {figure_text} and 25% in 2026.",
        "page-15-chunk-15": "Generation from nuclear will increase in 2025 and 2026.",
    }
    read = make_read(
        " ".join(passages.values()),
        url="https://ent.news/2025/1/940.pdf", title="Short-Term Energy Outlook - ENT News",
        passages=passages,
    )
    finding = make_finding(read, figure_text, target_ids=())
    assert resolve_attribution(
        proposed="relayed", organisation="U.S. Energy Information Administration",
        finding=finding, read=read, issuer=None,
    ) == ("relayed", "U.S. Energy Information Administration")


def test_an_opening_mention_with_no_authorship_cue_stays_unattributed() -> None:
    """The same shape, but the opening only mentions the proposed
    organisation ("Unlike BloombergNEF") with no authorship cue: it must not
    be credited with the document, and the figure still has no local
    attribution cue either, so the finding stays unattributed to the host.
    """
    cover = "Short-Term Energy Outlook. Unlike BloombergNEF, this report uses a different methodology."
    figure_text = "battery storage capacity growing by 47% (14 GW) in 2025"
    passages = {
        "page-1-chunk-0": cover,
        "page-3-chunk-2": "Macroeconomic assumptions are a key driver in the forecast.",
        "page-13-chunk-13": "Retail sales of electricity into the industrial sector increase.",
        "page-14-chunk-14": f"integrate onto the power grid, with {figure_text} and 25% in 2026.",
        "page-15-chunk-15": "Generation from nuclear will increase in 2025 and 2026.",
    }
    read = make_read(
        " ".join(passages.values()),
        url="https://ent.news/2025/1/940.pdf", title="x",
        passages=passages,
    )
    finding = make_finding(read, figure_text, target_ids=())
    assert resolve_attribution(
        proposed="relayed", organisation="BloombergNEF",
        finding=finding, read=read, issuer=None,
    ) == ("unattributed", "ent.news")


def test_page_owner_uses_the_pages_own_name_when_it_matches_the_host() -> None:
    """A woodmac.com page whose own title states 'Wood Mackenzie' is shown
    to the reader as Wood Mackenzie's own page, not the bare host label."""
    read = make_read(
        "The U.S. energy storage market hit a record 18.9 GW in 2025.",
        url="https://www.woodmac.com/press-releases/2025-us-energy-storage",
        title="2025 U.S. Energy Storage Installations Set New Record | Wood Mackenzie",
    )
    assert page_owner(read) == "Wood Mackenzie"


def test_page_owner_keeps_the_host_label_when_nothing_matches() -> None:
    """A generic title and body naming no organisation that matches the
    host: the bare registrable host label is kept, never invented."""
    read = make_read(
        "Storage market update: installations continue to grow.",
        url="https://www.utilitydive.com/news/storage-update",
        title="Storage market update",
    )
    assert page_owner(read) == "utilitydive.com"


def test_page_owner_never_credits_a_merely_similar_name_on_a_gov_host() -> None:
    """verified_facts.same_organisation is strict: energy.gov (the
    Department of Energy's own host) is never credited as "EIA" merely
    because the page's own title names EIA -- the two are not the same
    organisation, however similar the first word of each looks."""
    read = make_read(
        "The Department of Energy oversees EIA, an independent statistical agency.",
        url="https://www.energy.gov/articles/eia-overview",
        title="DOE Newsroom | EIA",
    )
    assert page_owner(read) == "energy.gov"


def test_page_owner_stops_a_cued_run_at_the_sentence_end() -> None:
    """P1-1: a footer's own full stop ends the name it states -- "Utility
    Dive. All rights reserved" credits Utility Dive, never "Utility Dive.
    All"."""
    read = make_read(
        "(c) 2025 Utility Dive. All rights reserved.",
        url="https://www.utilitydive.com/news/storage-update",
        title="Storage market update",
    )
    assert page_owner(read) == "Utility Dive"


def test_page_owner_returns_the_shortest_name_prefix_the_host_matches() -> None:
    """P1-1: a colon headline is not a title-credit separator, so the whole
    headline is one candidate; the name is the shortest word prefix that
    ``same_organisation`` confirms against the host, not the headline."""
    read = make_read(
        "The US energy storage market hit a record in 2025.",
        url="https://www.woodmac.com/press-releases/2025-us-energy-storage",
        title="Wood Mackenzie: US energy storage market hits record",
    )
    assert page_owner(read) == "Wood Mackenzie"


def test_page_owner_trims_a_cued_name_to_the_organisation_the_host_matches() -> None:
    """P1-1: a cue can introduce a longer run than the name ("Published by X
    Research Team"); the shortest matching prefix is what the page states as
    the organisation."""
    read = make_read(
        "Published by Wood Mackenzie Research Team. Storage capacity hit a record.",
        url="https://www.woodmac.com/press-releases/2025-us-energy-storage",
        title="US energy storage market hits record",
    )
    assert page_owner(read) == "Wood Mackenzie"


def test_page_owner_never_shortens_a_name_to_the_bare_host_label() -> None:
    """P1-1's prefix search must not turn a page's own word into a name:
    "Energy" is one word of the Department of Energy's name, not a
    stand-in for it (the same reading ``_single_token`` records), so an
    energy.gov page whose headline starts with it keeps the host label it
    is served under."""
    read = make_read(
        "Reports on storage.",
        url="https://www.energy.gov/topics/energy-storage",
        title="Energy storage reports | Department of Energy",
    )
    assert page_owner(read) == "energy.gov"


# ---------------------------------------------------------------------------
# check_statements (spec §6.2, D8): the Report Writer's sibling check
# ---------------------------------------------------------------------------


def _statement_finding(
    value: str, unit: str = "GW", *, period: str | None = "2025",
    organisation: str = "Wood Mackenzie", evidence_words: str = "",
) -> Finding:
    """A verified finding carrying one kept figure, for a StatementCheckItem."""
    finding = _bare_finding(
        f"{organisation} states {value} {unit}.",
        figures=[figure(value, unit, period, "actual")],
    )
    context = FigureContext(period=period, scope=None, attribution="own",
                            organisation=organisation, kind="actual")
    result = FigureResult(figure=finding.figures[0], matched=True,
                          evidence_words=evidence_words or f"{value} {unit}", context=context)
    verification = FindingVerification(status="verified", figure_results=[result])
    return finding.model_copy(update={"verification": verification})


def _statement_item(label: str, text: str, *findings: Finding) -> StatementCheckItem:
    return StatementCheckItem(
        label=label, text=text, findings=list(findings),
        labels=[f"F{i:02d}" for i in range(1, len(findings) + 1)],
    )


def _confirm_statement_reply(messages: list, schema: type) -> StatementCheckDraft:
    """Answer every listed statement with a plain 'consistent' verdict.

    Reads the batch's own labels back out of the request body, so it
    answers correctly whichever batch it is handed.
    """
    del schema
    body = messages[1].content
    labels = re.findall(r"## (S\d+)", body)
    return StatementCheckDraft(statements=[
        StatementVerdictDraft(label=label, verdict="consistent", reason="Matches the findings.")
        for label in labels
    ])


@pytest.mark.asyncio
async def test_statements_are_checked_five_per_call() -> None:
    finding = _statement_finding("18.9", "GW")
    items = [
        _statement_item(f"S{i:02d}", f"Wood Mackenzie states {i} GW.", finding)
        for i in range(12)
    ]
    completer = ScriptedCompleter(outputs=[_confirm_statement_reply] * 3)

    results, errors = await check_statements(completer, items, question="How much storage?")

    assert [call[0] for call in completer.calls] == ["StatementCheckDraft"] * 3
    assert errors == []
    assert len(results) == 12
    assert all(verdict is not None and verdict.verdict == "consistent" for verdict in results.values())


class _StatementConcurrencyProbe:
    """A completer that records the peak number of concurrent calls in flight."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._in_flight = 0
        self.max_in_flight = 0

    async def complete_structured(self, messages, schema, *, agent_name=None,
                                  max_tokens=None, reasoning_effort=None):
        self.calls.append(schema.__name__)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0.01)
            return _confirm_statement_reply(list(messages), schema)
        finally:
            self._in_flight -= 1


@pytest.mark.asyncio
async def test_statement_check_concurrency_cap_is_eight() -> None:
    finding = _statement_finding("18.9", "GW")
    batch_count = 10
    total = batch_count * CONTEXT_CHECK_BATCH_SIZE
    items = [
        _statement_item(f"S{i:03d}", f"Wood Mackenzie states {i} GW.", finding)
        for i in range(total)
    ]
    probe = _StatementConcurrencyProbe()

    results, errors = await check_statements(probe, items, question="How much storage?")

    assert len(probe.calls) == batch_count
    assert probe.max_in_flight == CONTEXT_CHECK_CONCURRENCY
    assert len(results) == total
    assert errors == []


@pytest.mark.asyncio
async def test_consistent_corrected_and_inconsistent_verdicts_pass_through() -> None:
    finding = _statement_finding("18.9", "GW", evidence_words="18.9 GW in 2025")

    def reply(messages: list, schema: type) -> StatementCheckDraft:
        del messages, schema
        return StatementCheckDraft(statements=[
            StatementVerdictDraft(label="S01", verdict="consistent", reason="Matches."),
            StatementVerdictDraft(label="S02", verdict="corrected",
                                  corrected_text="Wood Mackenzie reported 18.9 GW.",
                                  reason="Softened to reported."),
            StatementVerdictDraft(label="S03", verdict="inconsistent", reason="Invents a date."),
        ])

    items = [
        _statement_item("S01", "Wood Mackenzie states 18.9 GW.", finding),
        _statement_item("S02", "Wood Mackenzie confirms 18.9 GW.", finding),
        _statement_item("S03", "Wood Mackenzie states 18.9 GW on March 1.", finding),
    ]
    fingerprinted: list[str] = []
    completer = ScriptedCompleter(outputs=[reply])

    results, errors = await check_statements(
        completer, items, question="How much storage?", fingerprint=fingerprinted.append
    )

    assert errors == []
    assert results["S01"].verdict == "consistent"
    assert results["S02"].verdict == "corrected"
    assert results["S02"].corrected_text == "Wood Mackenzie reported 18.9 GW."
    assert results["S03"].verdict == "inconsistent"
    assert fingerprinted == ["StatementCheckDraft"]


@pytest.mark.asyncio
async def test_a_blank_corrected_text_is_treated_as_inconsistent() -> None:
    finding = _statement_finding("18.9", "GW")

    def reply(messages: list, schema: type) -> StatementCheckDraft:
        del messages, schema
        return StatementCheckDraft(statements=[
            StatementVerdictDraft(label="S01", verdict="corrected", corrected_text="   ",
                                  reason="Tried to correct but found nothing to change."),
        ])

    items = [_statement_item("S01", "Wood Mackenzie states 18.9 GW.", finding)]
    completer = ScriptedCompleter(outputs=[reply])

    results, errors = await check_statements(completer, items, question="How much storage?")

    assert errors == []
    assert results["S01"].verdict == "inconsistent"


@pytest.mark.asyncio
async def test_a_failed_statement_batch_gives_none_and_an_error() -> None:
    finding = _statement_finding("18.9", "GW")
    items = [
        _statement_item("S01", "Wood Mackenzie states 18.9 GW.", finding),
        _statement_item("S02", "Wood Mackenzie states 18.9 GW too.", finding),
    ]
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])

    results, errors = await check_statements(completer, items, question="How much storage?")

    assert results == {"S01": None, "S02": None}
    assert len(errors) == 1
    assert errors[0].error_type == "evidence_verifier_statement_check_failed"


@pytest.mark.asyncio
async def test_a_missing_label_in_the_reply_gives_none_and_records_an_error() -> None:
    """A reply that answers some of its batch's labels leaves the rest
    unjudged; the caller keeps those sentences as drafted, so the omission
    is recorded exactly as a failed batch is."""
    finding = _statement_finding("18.9", "GW")

    def reply(messages: list, schema: type) -> StatementCheckDraft:
        del messages, schema
        return StatementCheckDraft(statements=[
            StatementVerdictDraft(label="S01", verdict="consistent", reason="Matches."),
            StatementVerdictDraft(label="S02", verdict="consistent", reason="Matches."),
        ])

    items = [
        _statement_item("S01", "Wood Mackenzie states 18.9 GW.", finding),
        _statement_item("S02", "Wood Mackenzie states 18.9 GW too.", finding),
        _statement_item("S03", "Wood Mackenzie states 18.9 GW as well.", finding),
    ]
    completer = ScriptedCompleter(outputs=[reply])

    results, errors = await check_statements(completer, items, question="How much storage?")

    assert results["S01"].verdict == "consistent"
    assert results["S02"].verdict == "consistent"
    assert results["S03"] is None
    assert len(errors) == 1
    assert errors[0].error_type == "evidence_verifier_statement_check_failed"
    assert errors[0].details["reason"] == "label omitted from the reply"


# ---------------------------------------------------------------------------
# The two bounds are config, not module constants (PD-12, PD-27, D9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_configured_batch_size_splits_five_statements_into_three_calls() -> None:
    """`check_statements` takes the batch size from its caller, so the Report
    Writer's own `agents.verifier_batch_size` bounds the Statement Check the
    same way it bounds the Context Check (§5.4)."""
    finding = _statement_finding("18.9", "GW")
    items = [
        _statement_item(f"S{i:02d}", f"Wood Mackenzie states {i} GW.", finding)
        for i in range(5)
    ]
    completer = ScriptedCompleter(outputs=[_confirm_statement_reply] * 3)

    results, errors = await check_statements(
        completer, items, question="How much storage?", batch_size=2
    )

    assert [call[0] for call in completer.calls] == ["StatementCheckDraft"] * 3
    assert errors == []
    assert len(results) == 5
    assert all(
        verdict is not None and verdict.verdict == "consistent"
        for verdict in results.values()
    )


@pytest.mark.asyncio
async def test_a_configured_concurrency_bounds_the_statement_check() -> None:
    """`check_statements` takes the concurrency from its caller: at most two of
    its batches are ever in flight when it is configured with two."""
    finding = _statement_finding("18.9", "GW")
    batch_count = 6
    total = batch_count * CONTEXT_CHECK_BATCH_SIZE
    items = [
        _statement_item(f"S{i:03d}", f"Wood Mackenzie states {i} GW.", finding)
        for i in range(total)
    ]
    probe = _StatementConcurrencyProbe()

    results, errors = await check_statements(
        probe, items, question="How much storage?", concurrency=2
    )

    assert len(probe.calls) == batch_count
    assert probe.max_in_flight == 2
    assert len(results) == total
    assert errors == []


@pytest.mark.asyncio
async def test_the_context_check_bounds_come_from_the_agent_config(
    tracker: Tracker,
) -> None:
    """PD-12: the batch size and the concurrency the Context Check runs under are
    `agents.verifier_batch_size` and `agents.verifier_concurrency`, read from
    the verifier's own `AgentRuntimeConfig` — the module constants stay only
    as the defaults."""
    batch_size = 2
    concurrency = 3
    total = 12
    read = make_read(
        _metrics_page(total), url="https://example.test/config", title="Config test"
    )
    findings = [_metric_finding(read, i) for i in range(total)]
    probe = _ConcurrencyProbe()
    agent = _evidence_verifier(
        tracker,
        probe,  # type: ignore[arg-type]
        config=AgentRuntimeConfig(
            verifier_batch_size=batch_size, verifier_concurrency=concurrency
        ),
    )
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert len(probe.calls) == total // batch_size
    assert probe.max_in_flight == concurrency
    assert len(outcome.state_update["verified_findings"]) == total


def test_the_statement_check_shows_each_cited_findings_snippet_and_attribution() -> None:
    """D10 gap 3: a finding with no figure is judged against its verified words."""
    text = "Rising rents pushed households out of the centre, according to the Example Institute."
    finding = make_finding(
        make_read(text), text,
        attributed_issuer="Example Institute",
        attribution_quote="according to the Example Institute",
    ).model_copy(update={"verification": FindingVerification(status="verified")})
    body = statement_check_messages(
        [StatementCheckItem(label="S001", text="Rising rents pushed households out of the centre.",
                            findings=[finding], labels=["F01"])],
        question="Why did households leave the centre?",
    )[1].content
    assert "  F01: (no kept figures)\n" in body
    assert f'    snippet: "{text}"' in body
    assert '    attributed to: Example Institute ("according to the Example Institute")' in body


def test_an_own_page_finding_is_attributed_to_its_publisher() -> None:
    """D10: with no admitted issuer, the Statement Check still sees whose words a snippet is."""
    text = "Of the five kettles we tested, Model B was the quietest."
    finding = make_finding(
        make_read(text, url="https://lab.example.test/kettles", title="Kettles"), text,
    ).model_copy(update={"verification": FindingVerification(status="verified")})
    body = statement_check_messages(
        [StatementCheckItem(label="S001", text="Model B is the quietest kettle.",
                            findings=[finding], labels=["F01"])],
        question="Which kettle is the quietest?",
    )[1].content
    assert "    attributed to: lab.example.test" in body.splitlines()


def test_a_kept_figure_states_its_attribution_on_its_own_line_only() -> None:
    """Fix round 1: a figure line and a second line must not credit different bodies.

    The figure line carries the Context Check's own verdict (``relayed
    (Example Institute)``); a separate ``attributed to:`` line built from the
    extraction-time issuer, which is empty here, would credit the relay site
    instead and contradict it.
    """
    text = "Rents rose 7 percent in 2025, the Example Institute said."
    wanted = figure("7", "percent", "2025", "actual")
    finding = make_finding(
        make_read(text, url="https://gazette.example.test/rents", title="Rents"), text,
        figures=[wanted],
    ).model_copy(update={"verification": FindingVerification(
        status="verified",
        figure_results=[FigureResult(
            figure=wanted, matched=True, evidence_words=text,
            context=FigureContext(period="2025", attribution="relayed",
                                  organisation="Example Institute", kind="actual"),
        )],
    )})
    body = statement_check_messages(
        [StatementCheckItem(label="S001", text="Rents rose 7 percent in 2025.",
                            findings=[finding], labels=["F01"])],
        question="How much did rents rise?",
    )[1].content
    lines = body.splitlines()

    assert f'    snippet: "{text}"' in lines
    assert not [line for line in lines if line.startswith("    attributed to:")]
    assert any(
        line.startswith(
            "  F01: 7 percent | period 2025 | scope none | subject none | kind actual | "
            "relayed (Example Institute) | evidence: "
        )
        for line in lines
    )


# ---------------------------------------------------------------------------
# D11/D12: the figure subject, and relative periods resolved from the page date
#
# ``_figure_item`` is named apart from ``_item(read, finding)`` above because
# this one takes the page's own text and the figure under it -- the shape the
# subject and relative-period rules read.
# ---------------------------------------------------------------------------


def _figure_item(text, figure_, *, page_date=None, **finding_fields) -> ContextItem:
    read = make_read(text)
    finding = make_finding(read, text, figures=[figure_], target_ids=["topic-01-target-01"],
                           **finding_fields)
    return ContextItem(label="F01", finding=finding, read=read, passage=text,
                       match=figure_match(finding, {read.read_id: read}), page_date=page_date)


def _check(item, **reply):
    fields = {"finding": "F01", "figure": 1, "period": None, "scope": None,
              "attribution": "own", "organisation": None, "kind": "actual",
              "evidence_words": item.finding.snippet, "verdict": "confirm",
              "reason": "Stated."} | reply
    return _checked(item, item.finding.figures[0], FigureCheckDraft(**fields))


def test_a_relative_period_is_resolved_from_the_page_date() -> None:
    """Spec §5.2, D11 (Gate G4 finding (a)): 'this year' on a page dated 2026-02-20."""
    text = "Operators installed 4 GW this year."
    kept = _check(_figure_item(text, figure("4", "GW"), page_date="2026-02-20"),
                  period="2026", kind="forecast", verdict="correct")
    assert kept.kept and (kept.context.period, kept.context.period_resolved_from) == ("2026", "2026-02-20")
    undated = _check(_figure_item(text, figure("4", "GW")), period="2026", kind="forecast",
                     verdict="correct")
    assert undated.dropped_reason == "correction_not_on_page"


def test_a_subject_is_adopted_only_as_the_page_names_it() -> None:
    """Ruling N2: an off-page subject never drops a figure unless it disputes a recorded one.

    A real dispute needs neither subject on the page (fix round 1); that case
    has its own test below.
    """
    text = "Model B scored 4.5 out of 5 for noise."
    named = _check(_figure_item(text, figure("4.5", "out of 5")), subject="Model B")
    assert named.kept and named.context.subject == "Model B"
    unverified = _check(_figure_item(text, figure("4.5", "out of 5")), subject="Model C", verdict="correct")
    assert unverified.kept and unverified.context.subject is None
    recorded = figure("4.5", "out of 5").model_copy(update={"subject": "Model B"})
    misread = figure("4.5", "out of 5").model_copy(update={"subject": "Model A"})
    corrected = _check(_figure_item(text, misread), subject="Model B", verdict="correct")
    assert corrected.kept and corrected.context.subject == "Model B"
    us = _check(_figure_item("U.S. operators installed 4 GW in 2024.",
                             figure("4", "GW", "2024", "actual")),
                subject="United States", period="2024")
    assert us.kept and us.context.subject is None
    item = _figure_item(text, recorded)
    assert unchecked_context(item.finding, item.finding.figures[0], item.read, None).subject == "Model B"


def test_the_context_check_block_shows_the_page_date_and_the_recorded_subject() -> None:
    item = _figure_item("Model B scored 4.5 out of 5 this year.",
                        figure("4.5", "out of 5").model_copy(update={"subject": "Model B"}),
                        page_date="2026-02-20")
    body = context_check_messages([item])[1].content
    assert "page date: 2026-02-20 (from the Source Evaluator)" in body.splitlines()
    assert "| recorded subject Model B" in body


def test_a_block_with_no_page_date_says_so() -> None:
    """§8.7-D12: every block states its resolution basis, and says when it has none."""
    body = context_check_messages(
        [_figure_item("Operators installed 4 GW in 2024.", figure("4", "GW", "2024", "actual"))]
    )[1].content
    assert "page date: not stated" in body.splitlines()


def _dated_source(read, publication_date: str | None) -> ScoredSource:
    return ScoredSource(url=read.resolved_url, title=read.title, rationale="Dated.",
                        authority_score=0.8, recency_score=0.8, relevance_score=0.8,
                        overall_score=0.8,
                        temporal=SourceTemporal(publication_date=publication_date))


def test_the_page_date_comes_from_the_evaluated_source() -> None:
    """D11: the one date a relative period may resolve against is the read's validated one."""
    read = make_read()
    assert evaluated_page_date([_dated_source(read, "2026-02-20")], read) == "2026-02-20"
    assert evaluated_page_date([_dated_source(read, None)], read) is None
    elsewhere = _dated_source(read, "2026-02-20").model_copy(update={"url": "https://other.example.test/x"})
    assert evaluated_page_date([elsewhere], read) is None


def _relative_period_reply(messages: list, schema: type) -> ContextCheckDraft:
    """Answer the one figure with the period its page's stated date resolves "this year" to."""
    del schema
    snippet = re.search(r"snippet: (.*)", messages[1].content).group(1)
    return ContextCheckDraft(figures=[FigureCheckDraft(
        finding="F01", figure=1, period="2026", attribution="own", kind="actual",
        evidence_words=snippet, verdict="correct", reason="The page's own date resolves the year.",
    )])


@pytest.mark.asyncio
async def test_a_relative_period_resolves_against_the_evaluated_page_date(tracker: Tracker) -> None:
    """D11/D12 wire-up: the verifier carries the Source Evaluator's date to the figure's check."""
    text = "Operators installed 4 GW this year."
    read = make_read(text, url="https://operators.example.test/report", title="Operators")
    finding = make_finding(read, text, figures=[figure("4", "GW")])
    completer = ScriptedCompleter(outputs=[_relative_period_reply])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read},
                   evaluated_sources=[_dated_source(read, "2026-02-20")])

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    [judged] = outcome.state_update["verified_findings"]
    [result] = judged.verification.figure_results
    assert result.kept
    assert (result.context.period, result.context.period_resolved_from) == ("2026", "2026-02-20")


def test_a_statement_check_figure_line_names_its_subject() -> None:
    """D11: a sentence about one subject is judged against the subject its figure is about."""
    text = "Model B scored 4.5 out of 5 for noise."
    wanted = figure("4.5", "out of 5")
    finding = make_finding(
        make_read(text, url="https://lab.example.test/kettles", title="Kettles"), text,
        figures=[wanted],
    ).model_copy(update={"verification": FindingVerification(
        status="verified",
        figure_results=[FigureResult(
            figure=wanted, matched=True, evidence_words=text,
            context=FigureContext(attribution="own", organisation="Example Test Lab",
                                  kind="actual", subject="Model B"),
        )],
    )})
    body = statement_check_messages(
        [StatementCheckItem(label="S001", text="Model B scored 4.5 out of 5.",
                            findings=[finding], labels=["F01"])],
        question="Which kettle is the quietest?",
    )[1].content
    assert any(
        line.startswith(
            "  F01: 4.5 out of 5 | period none | scope none | subject Model B | kind actual | "
            "own (Example Test Lab) | evidence: "
        )
        for line in body.splitlines()
    )


# ---------------------------------------------------------------------------
# Fix round 1: explicit periods beat relative ones, a restated or page-backed
# subject is kept, and the block shows the date the check resolves against.
# ---------------------------------------------------------------------------


def test_an_explicitly_dated_figure_is_never_given_a_relative_period() -> None:
    """Fix 1: the words date the figure 2024, so "this year" is another clause."""
    text = "Operators installed 4 GW in 2024; this year they plan more."
    corrected = _check(
        _figure_item(text, figure("4", "GW", "2024", "actual"), page_date="2026-02-20"),
        period="2026", kind="actual", verdict="correct")
    assert corrected.dropped_reason == "correction_not_on_page"


def test_a_restated_subject_never_disputes_the_recorded_one() -> None:
    """Fix 2a: "X200" against a recorded "Acme X200" name one thing."""
    text = "The Acme X200 scored 4.5 out of 5 for noise."
    recorded = figure("4.5", "out of 5").model_copy(update={"subject": "Acme X200"})
    kept = _check(_figure_item(text, recorded), subject="ACME X200 Inc.", verdict="correct")
    assert kept.kept and kept.context.subject == "Acme X200"


def test_a_page_backed_recorded_subject_is_never_overridden() -> None:
    """Fix 2b: the page says "U.S.", so an unbacked "United States" cannot displace it."""
    text = "U.S. operators installed 4 GW in 2024."
    recorded = figure("4", "GW", "2024", "actual").model_copy(update={"subject": "U.S."})
    kept = _check(_figure_item(text, recorded), subject="United States", period="2024")
    assert kept.kept and kept.context.subject == "U.S."


def test_a_subject_neither_side_backs_still_drops_the_figure() -> None:
    """Fix 2c: neither subject is on the page, so the figure's own one is unusable."""
    text = "The device scored 4.5 out of 5 for noise."
    recorded = figure("4.5", "out of 5").model_copy(update={"subject": "Model A"})
    dropped = _check(_figure_item(text, recorded), subject="Model B", verdict="correct")
    assert dropped.dropped_reason == "correction_not_on_page"


def test_the_block_names_the_date_the_check_resolves_against() -> None:
    """Fix 3: one basis, printed, so a batch reads the date code will use."""
    text = "Operators installed 4 GW this year."
    item = _figure_item(text, figure("4", "GW"), release_date="2026-02-20")
    body = context_check_messages([item])[1].content
    assert "page date: 2026-02-20 (the finding's release date)" in body.splitlines()
    kept = _check(item, period="2026", kind="forecast", verdict="correct")
    assert (kept.context.period, kept.context.period_resolved_from) == ("2026", "2026-02-20")


def test_the_block_labels_where_its_page_date_came_from() -> None:
    """Fix 3: the Source Evaluator first, then the finding's own two dates, then none."""
    text = "Operators installed 4 GW in 2024, the firm's 2025-12-01 update said."
    figure_ = figure("4", "GW", "2024", "actual")

    def lines(**dates) -> list[str]:
        body = context_check_messages([_figure_item(text, figure_, **dates)])[1].content
        return body.splitlines()

    assert "page date: 2026-02-20 (from the Source Evaluator)" in lines(page_date="2026-02-20")
    assert "page date: 2025-11-30 (the finding's release date)" in lines(release_date="2025-11-30")
    assert "page date: 2025-12-01 (the finding's statement date)" in lines(statement_date="2025-12-01")
    assert "page date: 2025-11-30 (the finding's release date)" in lines(
        release_date="2025-11-30", statement_date="2025-12-01")
    assert "page date: not stated" in lines()


def test_a_recorded_subject_must_be_in_the_figures_own_evidence_words() -> None:
    """Fix 2: a passage naming another figure's subject never backs this figure's one.

    The passage carries both kettles; the words that state the 4.5 figure name
    the C kettle, and the recorded subject belongs to the 4.2 figure. A
    proposal names neither, so nothing backs either subject and the figure is
    dropped rather than kept carrying the other figure's subject.
    """
    passage = ("The Model B kettle scored 4.2 out of 5 for noise. "
               "The Model C kettle scored 4.5 out of 5 for noise.")
    words = "The Model C kettle scored 4.5 out of 5 for noise."
    recorded = figure("4.5", "out of 5").model_copy(update={"subject": "Model B"})
    read = make_read(passage)
    finding = make_finding(read, words, figures=[recorded], target_ids=["topic-01-target-01"])
    item = ContextItem(label="F01", finding=finding, read=read, passage=passage,
                       match=figure_match(finding, {read.read_id: read}))

    miscredited = _check(item, subject="the C-series kettle", verdict="correct")

    assert miscredited.dropped_reason == "correction_not_on_page"


# ---------------------------------------------------------------------------
# Task FF1 (final review, slice 1): the relay a multi-source finding states,
# the null proposal, the off-page statement date, the grown binding, the
# BaseAgent hook's own answer, and a configuration fault.
# ---------------------------------------------------------------------------


def test_a_relayed_figure_is_credited_to_the_body_the_page_credits() -> None:
    """Task FF1 (review C1): one finding states two bodies' figures and the
    extraction admitted one issuer for the whole finding; the Context Check's
    relay of the *other* body must survive, or the report prints a figure the
    admitted body never issued as its relay."""
    text = ("According to the EIA, developers plan to add 18.2 GW in 2025, while "
            "Wood Mackenzie projects 15 GW in 2025.")
    read = make_read(text, url="https://www.utilitydive.com/news/x", title="Storage outlook")
    finding = make_finding(read, text,
                           figures=[figure("18.2", "GW", "2025", "forecast"),
                                    figure("15", "GW", "2025", "forecast")],
                           attributed_issuer="EIA")

    assert resolve_attribution(
        proposed="relayed", organisation="Wood Mackenzie", finding=finding,
        read=read, issuer=None,
    ) == ("relayed", "Wood Mackenzie")


def test_a_relay_the_page_does_not_credit_is_never_the_findings_other_issuer() -> None:
    """Task FF1 (review C1): with no cue beside the proposed body, the finding's
    admitted issuer is not the answer -- the figure is unattributed to the site
    that carried it rather than credited to a body that did not issue it."""
    text = ("U.S. developers plan to add 18.2 GW in 2025. A second body, "
            "Wood Mackenzie, is named here with no attribution at all.")
    read = make_read(text, url="https://www.utilitydive.com/news/x", title="Storage outlook")
    finding = make_finding(read, text,
                           figures=[figure("18.2", "GW", "2025", "forecast"),
                                    figure("15", "GW", "2025", "forecast")],
                           attributed_issuer="EIA")

    assert resolve_attribution(
        proposed="relayed", organisation="Wood Mackenzie", finding=finding,
        read=read, issuer=None,
    ) == ("unattributed", "utilitydive.com")


def test_a_null_period_proposal_clears_a_period_the_page_does_not_state() -> None:
    """Task FF1 (review I1): the extraction recorded a period the page never
    dates; the Context Check answers null, as its prompt instructs, and the
    recorded value must not stand as a verified period."""
    text = ("Our testers rated the kettle highly, and the review says nothing about "
            "when they did.")
    kept = _check(_figure_item(text, figure("4.5", "out of 5", "2026", "actual")),
                  verdict="correct")

    assert kept.kept and kept.context.period is None and kept.corrected


def test_a_period_the_words_state_survives_a_null_proposal() -> None:
    """The bound on the rule above: the words the check was given state the
    recorded period, so the figure's own evidence backs it and it stands."""
    text = "The K1 scored 4.5 out of 5 in 2026."
    kept = _check(_figure_item(text, figure("4.5", "out of 5", "2026", "actual")),
                  verdict="correct")

    assert kept.kept and kept.context.period == "2026"


def test_a_null_subject_proposal_clears_a_subject_the_words_do_not_state() -> None:
    """Task FF1 (review I1): the same rule for the recorded subject."""
    text = "The X200 scored 4.5 out of 5."
    recorded = figure("4.5", "out of 5").model_copy(update={"subject": "Acme X300"})
    kept = _check(_figure_item(text, recorded), verdict="correct")

    assert kept.kept and kept.context.subject is None and kept.corrected


def test_an_explicit_period_in_the_words_beats_a_relative_reading() -> None:
    """Task FF1 (review P2-2): fix round 1 guarded the *recorded* period only;
    with nothing recorded, the words' own explicit year still beats a relative
    reading, so a 2024 figure is never kept under 2026."""
    text = "Firms added 4 GW in 2024; this year they plan more."
    item = _figure_item(text, figure("4", "GW", None, "forecast"), page_date="2026-02-20")
    dropped = _check(item, period="2026", kind="forecast", verdict="correct")

    assert dropped.dropped_reason == "correction_not_on_page"


def test_a_statement_date_the_page_does_not_state_is_no_basis() -> None:
    """Task FF1 (review I7): the finding's statement date is admitted the way
    its quote is, so a relative period is never resolved against a date the
    page cannot support."""
    text = "Operators plan to add 4 GW this year, the firm said."
    item = _figure_item(text, figure("4", "GW", None, "forecast"), statement_date="2031-05-01")

    assert _page_date_basis(item) == (None, "")
    assert "page date: not stated" in context_check_messages([item])[1].content.splitlines()
    dropped = _check(item, period="2031", kind="forecast", verdict="correct")
    assert dropped.dropped_reason == "correction_not_on_page"


@pytest.mark.asyncio
async def test_a_finding_already_verified_is_judged_again_when_its_targets_grew(
    tracker: Tracker,
) -> None:
    """Task FF1 (review P2-4): a re-extraction of the same content that binds a
    target the verified record lacked is judged again, so the obligation it now
    answers is read from a verified record instead of staying Not found."""
    read = make_read()
    finding = make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")],
                           target_ids=["topic-01-target-01"])
    verified_before = finding.model_copy(
        update={"verification": FindingVerification(status="verified")})
    rebound = finding.model_copy(
        update={"target_ids": ["topic-01-target-01", "topic-02-target-01"]})
    completer = ScriptedCompleter(outputs=[_confirm_reply])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[rebound], read_records={read.read_id: read},
                   verified_findings=[verified_before])

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert [call[0] for call in completer.calls] == ["ContextCheckDraft"]
    snapshot = outcome.state_update["verified_findings"]
    assert [f.target_ids for f in snapshot] == [["topic-01-target-01", "topic-02-target-01"]]
    assert snapshot[0].verification.status == "verified"


@pytest.mark.asyncio
async def test_a_finding_already_verified_with_the_same_bindings_is_not_judged_again(
    tracker: Tracker,
) -> None:
    """The bound on the rule above: a pass costs nothing the snapshot covers."""
    read = make_read()
    finding = make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")],
                           target_ids=["topic-01-target-01"])
    verified_before = finding.model_copy(
        update={"verification": FindingVerification(status="verified")})
    completer = ScriptedCompleter(outputs=[])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read},
                   verified_findings=[verified_before])

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert completer.calls == []
    assert [f.target_ids for f in outcome.state_update["verified_findings"]] == [
        ["topic-01-target-01"]]


def test_the_state_update_hook_reports_errors_only(tracker: Tracker) -> None:
    """Task FF1 (review P3-1): ``run`` builds the snapshot PD-4 accumulates, so
    the documented ``BaseAgent`` hook must not answer with one pass's findings
    in its place -- a caller relying on it would drop every earlier finding."""
    agent = _evidence_verifier(tracker, ScriptedCompleter(outputs=[]))
    finding = make_finding(make_read(), SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")])

    update = agent.state_update(
        VerifiedFindings(findings=[finding]),
        ReActRun(agent_name=EVIDENCE_VERIFIER_NAME, stop_reason="finished"),
    )

    assert "verified_findings" not in update


@pytest.mark.asyncio
async def test_a_provider_configuration_error_halts_the_context_check(tracker: Tracker) -> None:
    """Task FF1 (review P3-5): a rejected model or effort is a configuration
    fault the run halts on, not a batch that silently publishes every figure as
    unchecked context."""
    read = make_read()
    finding = make_finding(read, SNIPPET, figures=[figure("10.4", "GW", "2024", "actual")])
    completer = ScriptedCompleter(
        outputs=[ProviderConfigurationError("unsupported reasoning effort")])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read})

    with pytest.raises(ProviderConfigurationError):
        async with tracker.session_span("session-1", "question"):
            await agent.run(state)


def test_an_organisation_merely_mentioned_does_not_own_the_page() -> None:
    """Slice-3 review: ``_owns_page`` asks whether the page *is* the
    organisation's, not whether the organisation appears on it.

    The Source Evaluator's validated issuer (PD-25) is a judgement about the
    read, so an identity-words match with it counts only beside the page's own
    credit of itself: a news page quoting "the Example Statistical Agency" is
    not the agency's own page, and the agency's own .gov page is.
    """
    text = ("Battery storage grew 66% in 2024. The Example Statistical Agency said demand "
            "will double by 2026.")
    news = make_read(text, url="https://www.utilitydive.com/news/storage", title="Storage grew")
    own = make_read(text, url="https://www.esa.gov/data", title="Storage data")

    assert not _owns_page(news, "Example Statistical Agency", "Example Statistical Agency")
    assert _owns_page(own, "Example Statistical Agency", None)


def test_the_pages_own_masthead_still_evidences_the_validated_issuer() -> None:
    """The bound on the rule above: a page that credits itself with the name the
    Source Evaluator validated is still its own page."""
    text = "Grid Storage Outlook. Published by Example Lab, March 2026. Capacity grew 66%."
    read = make_read(text, url="https://www.examplelab.com/outlook", title="Grid Storage Outlook")

    assert _owns_page(read, "Example Lab", "Example Lab")


# ---------------------------------------------------------------------------
# The live pre-flight's Defect A (review-01) end to end: the Context Check left
# the relay unresolved and the label contradicted the page's own sentence.
# ---------------------------------------------------------------------------

PREFLIGHT_WORDS = (
    "U.S. developers and power plant owners plan to significantly increase utility-scale "
    "battery storage over the next three years, reaching 30 GW by the end of 2025, based on "
    "the latest reporting from the U.S. Energy Information Administration (EIA)."
)
PREFLIGHT_URL = ("https://www.power-eng.com/energy-storage/batteries/"
                 "eia-utility-scale-battery-storage-capacity-to-reach-30-gw-by-2026")


def test_a_relay_the_context_check_leaves_unresolved_is_read_from_its_own_words() -> None:
    """Defect A: the Context Check answered that the source does not attribute
    the figure, while the words it quoted credit the U.S. Energy Information
    Administration. Code reads the credit back out of those words, so the label
    agrees with the sentence the writer quotes from the page (review-01)."""
    read = make_read(PREFLIGHT_WORDS, url=PREFLIGHT_URL,
                     title="EIA: utility-scale battery storage capacity to reach 30 GW by 2026")
    finding = make_finding(read, PREFLIGHT_WORDS,
                           figures=[figure("30", "GW", "2025", "forecast")])

    assert resolve_attribution(proposed="unattributed", organisation=None, finding=finding,
                               read=read, issuer=None, words=PREFLIGHT_WORDS) == (
        "relayed", "U.S. Energy Information Administration")


def test_a_body_merely_mentioned_leaves_the_figure_unattributed() -> None:
    """The bound: the fallback needs a cue beside the name, so a page that only
    mentions a body elsewhere stays unattributed to the site that carried it."""
    words = ("U.S. developers plan to add 30 GW of utility-scale battery storage by the end "
             "of 2025.")
    read = make_read(f"{words} Analysts at the U.S. Energy Information Administration track the "
                      "market each quarter, and this sentence is about something else.",
                     url=PREFLIGHT_URL, title="Storage to reach 30 GW")
    finding = make_finding(read, words, figures=[figure("30", "GW", "2025", "forecast")])

    assert resolve_attribution(proposed="unattributed", organisation=None, finding=finding,
                               read=read, issuer=None, words=words) == (
        "unattributed", "power-eng.com")
