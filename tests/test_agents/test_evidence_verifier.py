"""The Evidence Verifier (spec §5)."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from deep_research.agents.evidence_verifier import (
    _CONTEXT_CHECK_REPLY_EXAMPLES,
    CONTEXT_CHECK_BATCH_SIZE,
    CONTEXT_CHECK_CONCURRENCY,
    EVIDENCE_VERIFIER_NAME,
    ContextCheckDraft,
    ContextItem,
    EvidenceVerifierAgent,
    FigureCheckDraft,
    context_passage,
    evaluated_issuer,
    figure_match,
    resolve_attribution,
    verify_finding,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
)
from deep_research.utils.types import (
    Finding,
    FindingVerification,
    ResearchState,
    ScoredSource,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read

SNIPPET = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"


def test_figure_match_confirms_the_snippet_and_each_figure() -> None:
    read = make_read()
    finding = make_finding(
        read, SNIPPET,
        figures=[figure("10.4", "GW", "2024", "actual"), figure("19.6", "GW", "2025", "forecast")],
    )
    match = figure_match(finding, {read.read_id: read})
    assert match.read_found and match.snippet_on_page
    assert match.matched == (True, False)  # 19.6 GW is on the page, but not in this snippet


def test_a_snippet_that_is_not_on_the_page_matches_nothing() -> None:
    read = make_read()
    finding = make_finding(read, "EIA says 10.4 GW was added in 2024.",
                           figures=[figure("10.4", "GW")])
    match = figure_match(finding, {read.read_id: read})
    assert match.read_found and not match.snippet_on_page and match.matched == (False,)


def test_a_missing_read_is_reported() -> None:
    finding = make_finding(make_read(), SNIPPET, figures=[figure("10.4", "GW")])
    match = figure_match(finding, {})
    assert not match.read_found and match.matched == (False,)


def test_the_snippet_check_is_cosmetic() -> None:
    read = make_read()
    finding = make_finding(read, SNIPPET.upper(), figures=[figure("10,400", "MW")])
    assert figure_match(finding, {read.read_id: read}).matched == (True,)


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


def test_a_not_matched_figure_is_rescued_only_by_its_evidence_words() -> None:
    read, finding = _woodmac_finding()
    finding = finding.model_copy(update={"figures": [figure("13.3", "GW", "2025", "forecast")]})
    item = _item(read, finding)
    assert item.match.matched == (False,)
    kept = verify_finding(item, {1: _reply(
        kind="forecast", evidence_words="Grid-scale storage installations are forecasted to reach 13.3 GW in 2025")})
    assert kept.figure_results[0].kept
    dropped = verify_finding(item, {1: _reply(kind="forecast")})
    assert dropped.figure_results[0].dropped_reason == "figure_not_in_evidence"


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


def test_a_relay_needs_its_originator_named_on_the_page() -> None:
    read = make_read(RELAY_PAGE, url=RELAY_URL, title="Storage in 2025 | Utility Dive")
    finding = make_finding(read, "According to Wood Mackenzie, utility-scale installations reached 16 GW in 2025.")
    assert resolve_attribution(proposed="relayed", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=None) == ("relayed", "Wood Mackenzie")
    assert resolve_attribution(proposed="relayed", organisation="BloombergNEF",
                               finding=finding, read=read, issuer=None) == ("unattributed", "utilitydive.com")


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


def _evidence_verifier(tracker: Tracker, completer: ScriptedCompleter) -> EvidenceVerifierAgent:
    return EvidenceVerifierAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name=EVIDENCE_VERIFIER_NAME, max_entries=20,
        ),
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
    section = section.split("\n\n# Response contract", 1)[0]
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
async def test_findings_are_checked_fifteen_per_call(tracker: Tracker) -> None:
    read = make_read(_metrics_page(20), url="https://example.test/batch", title="Batch metrics")
    findings = [_metric_finding(read, i) for i in range(20)]
    completer = ScriptedCompleter(outputs=[_confirm_reply, _confirm_reply])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=findings, read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert [call[0] for call in completer.calls] == ["ContextCheckDraft", "ContextCheckDraft"]
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
async def test_a_not_matched_figure_in_a_failed_batch_is_dropped_as_context_unavailable(
    tracker: Tracker,
) -> None:
    read = make_read()
    # 19.6 GW is on the page, but not inside this snippet: Figure Match
    # leaves it not_matched rather than dropping the finding outright.
    finding = make_finding(read, SNIPPET, figures=[figure("19.6", "GW", "2025", "forecast")])
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _evidence_verifier(tracker, completer)
    state = _state(raw_findings=[finding], read_records={read.read_id: read})

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    [judged] = outcome.state_update["verified_findings"]
    assert judged.verification.status == "dropped"
    assert judged.verification.dropped_reason == "all_figures_dropped"
    [result] = judged.verification.figure_results
    assert not result.matched
    assert result.dropped_reason == "context_unavailable"


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
async def test_the_concurrency_cap_is_four(tracker: Tracker) -> None:
    """§5.2: batches run concurrently, at most 4 at once. Six batches racing
    for the same semaphore prove the cap holds rather than merely happening
    to fit."""
    batch_count = 6
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
