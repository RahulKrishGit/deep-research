"""The Evidence Verifier's evaluation cases (spec §5.1-5.2, D8).

Every controlled case's fixture is driven through the real
``EvidenceVerifierAgent`` with a ``ScriptedCompleter`` answering the Context
Check the verifier actually requests (``ContextCheckDraft``) — the
``evidence_verifier_output_for`` fixture in ``conftest`` is that run — so a
case's declared outcome is the run's outcome rather than a claim about it. The
three agent gates are then exercised on the reference output and on a mutated
copy of it, because a gate that cannot fail is not a gate.
"""

from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.evidence import excerpt_matches
from deep_research.agents.evidence_verifier import (
    CONTEXT_CHECK_BATCH_SIZE,
    EVIDENCE_VERIFIER_NAME,
    ContextCheckDraft,
    EvidenceVerifierAgent,
    FigureCheckDraft,
)
from deep_research.evaluation.cases import (
    EXPECTED_CONTROLLED_CASE_IDS,
    EXPECTED_LIVE_CASE_IDS,
    case_by_id,
    cases_for,
)
from deep_research.evaluation.dependencies import SCENARIOS
from deep_research.evaluation.evaluators import (
    AGENT_GATE_IDS,
    METRIC_FUNCTIONS,
    deterministic_metric_scores,
    evaluate_agent_gates,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.evaluation.models import TargetOutput
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from tests.agent_fakes import ScriptedCompleter

CONTROLLED = (
    "scope-corrected-to-all-segments",
    "relay-labelled-as-relay",
    "invented-evidence-words-rejected",
)
LIVE = "evidence-verifier-live-benchmark"

GATES = ("verification_recorded", "no_invented_evidence", "drop_reasons_named")

METRICS = {
    "verification_recorded",
    "no_invented_evidence",
    "drop_reasons_named",
    "expected_outcome",
}


def _case(case_id: str, tier: str = "controlled"):
    return case_by_id("evidence_verifier", tier, case_id)


def _read_of(case, finding):
    return case.state.read_records[finding.read_id or ""]


def _reference_words(case) -> str:
    """The words the case's own reference says its Context Check answers with."""
    words = case.expectations.reference.get("context_check_evidence_words")
    assert isinstance(words, str), case.case_id
    return words


def _reply(case, **overrides: object) -> ContextCheckDraft:
    """The Context Check the case's own reference declares.

    The reply is built from the case's fixtures and reference rather than from
    literals here, so a case cannot be edited without moving what its scripted
    run answers.
    """
    finding = case.state.raw_findings[0]
    item = finding.figures[0]
    fields: dict[str, object] = dict(
        finding="F01",
        figure=1,
        period=item.period,
        scope=finding.measure_scope,
        attribution="own",
        organisation="",
        kind=item.kind,
        evidence_words=_reference_words(case),
        verdict="confirm",
        reason="As stated on the page.",
    )
    fields.update(overrides)
    return ContextCheckDraft(figures=[FigureCheckDraft(**fields)])


def _expected(case) -> dict:
    [outcome] = case.expectations.reference["expected_outcomes"]
    return dict(outcome)


def _scores(output: TargetOutput, case) -> dict[str, float]:
    """Every declared metric's unit score, through the real registry."""
    return dict(
        deterministic_metric_scores(
            output, case, metric_functions=METRIC_FUNCTIONS
        )
    )


def _gates(output: TargetOutput, case) -> dict[str, bool]:
    return {
        result.gate_id: result.passed
        for result in evaluate_agent_gates(output, case)
    }


# --- the registry's declared inventory --------------------------------------


def test_the_declared_inventory_matches_the_module() -> None:
    assert (
        tuple(case.case_id for case in cases_for("evidence_verifier", "controlled"))
        == EXPECTED_CONTROLLED_CASE_IDS["evidence_verifier"]
    )
    assert (
        tuple(case.case_id for case in cases_for("evidence_verifier", "live"))
        == EXPECTED_LIVE_CASE_IDS["evidence_verifier"]
    )
    assert EXPECTED_CONTROLLED_CASE_IDS["evidence_verifier"] == CONTROLLED
    assert EXPECTED_LIVE_CASE_IDS["evidence_verifier"] == (LIVE,)


def test_every_case_is_an_evidence_verifier_case() -> None:
    for case_id in CONTROLLED:
        case = _case(case_id)
        assert case.agent_name == "evidence_verifier"
        assert case.tier == "controlled"
    live = _case(LIVE, "live")
    assert live.agent_name == "evidence_verifier"
    assert live.tier == "live"


def test_every_controlled_scenario_is_scripted_and_tool_free() -> None:
    """The verifier declares no tools, so its scenario scripts no service."""
    for case_id in CONTROLLED:
        case = _case(case_id)
        script = SCENARIOS[case.dependency_scenario]
        assert script.search_responses == {}, case_id
        assert script.http_pages == {}, case_id
        assert script.reputations == {}, case_id
        assert script.failures == {}, case_id
    # A live case names the live tier, not a scripted scenario: the live
    # bundle builds real, injected dependencies (see build_live_dependencies).
    assert _case(LIVE, "live").dependency_scenario == "live"
    assert "live" not in SCENARIOS


def test_every_case_declares_the_verifier_gates_and_metrics() -> None:
    assert AGENT_GATE_IDS["evidence_verifier"] == GATES
    for case_id in CONTROLLED:
        case = _case(case_id)
        declared = {
            metric.metric_id for metric in case.expectations.deterministic_metrics
        }
        assert declared == METRICS, case_id
        assert declared <= set(METRIC_FUNCTIONS), case_id
        assert sum(
            metric.weight for metric in case.expectations.deterministic_metrics
        ) == pytest.approx(1.0)
    live = _case(LIVE, "live")
    assert {
        metric.metric_id for metric in live.expectations.deterministic_metrics
    } == {"verification_recorded", "no_invented_evidence", "drop_reasons_named"}


def test_every_seeded_finding_is_bound_to_its_own_seeded_read() -> None:
    """A fixture that fails Figure Match would grade nothing (§5.1 step 1)."""
    for case in cases_for("evidence_verifier", "controlled") + cases_for(
        "evidence_verifier", "live"
    ):
        assert case.state.raw_findings, case.case_id
        for finding in case.state.raw_findings:
            read = _read_of(case, finding)
            assert read is not None, case.case_id
            assert finding.snippet, case.case_id
            assert excerpt_matches(
                " ".join(read.passages.values()), finding.snippet
            ), case.case_id
            assert finding.verification is None, "a seeded finding starts unverified"


def test_the_live_case_cites_the_benchmark_s_own_addresses() -> None:
    """R4: the live case's pages are the benchmark's, down to the address.

    The EIA address is the one the benchmark itself declares
    (``tests.evidence_fakes.EIA_URL``), compared through production's own
    normalizer so the two fixtures cannot drift apart.
    """
    from tests.evidence_fakes import EIA_URL

    case = _case(LIVE, "live")
    urls = {finding.source_url for finding in case.state.raw_findings}

    assert normalize_source_url(EIA_URL) in urls
    assert normalize_source_url(EIA_URL) == (
        "https://eia.gov/todayinenergy/detail.php?id=64705"
    )
    assert "https://woodmac.com/press-releases/2025-us-energy-storage" in urls
    assert "https://utilitydive.com/news/storage-2025" in urls


def test_the_live_case_uses_the_benchmark_pages() -> None:
    case = _case(LIVE, "live")
    pages = case.expectations.reference["benchmark_pages"]
    assert len(pages) == 3
    assert any("eia.gov" in url for url in pages)
    assert any("woodmac.com" in url for url in pages)
    urls = {finding.source_url for finding in case.state.raw_findings}
    assert urls <= set(pages)
    assert len(case.state.raw_findings) == 4


# --- the controlled cases, driven through the real agent --------------------


def test_a_scope_the_page_corrects_is_recorded_corrected(
    evidence_verifier_output_for,
) -> None:
    case = _case("scope-corrected-to-all-segments")
    expected = _expected(case)
    output = evidence_verifier_output_for(
        case, [_reply(case, scope=expected["scope"], verdict="correct")]
    )

    [finding] = output.result["findings"]
    verification = finding["verification"]
    assert verification["status"] == expected["status"] == "verified_corrected"
    [result] = verification["figure_results"]
    assert result["dropped_reason"] is None
    assert result["context"]["scope"] == "all segments"
    assert result["corrected"] is True
    # The recorded scope is the defect the case grades, and the run replaced
    # it rather than keeping both.
    assert case.state.raw_findings[0].measure_scope == "Grid-scale"
    assert result["context"]["scope"] != case.state.raw_findings[0].measure_scope
    assert result["context"]["period"] == "2025"
    assert result["context"]["kind"] == "actual"

    assert _gates(output, case) == {gate: True for gate in GATES}
    scores = _scores(output, case)
    assert all(score == 1.0 for score in scores.values()), scores


def test_a_relayed_figure_is_credited_to_the_page_s_originator(
    evidence_verifier_output_for,
) -> None:
    case = _case("relay-labelled-as-relay")
    expected = _expected(case)
    output = evidence_verifier_output_for(
        case,
        [
            _reply(
                case,
                attribution=expected["attribution"],
                organisation=expected["organisation"],
            )
        ],
    )

    [finding] = output.result["findings"]
    verification = finding["verification"]
    assert verification["status"] == expected["status"] == "verified"
    [result] = verification["figure_results"]
    assert result["dropped_reason"] is None
    assert result["context"]["attribution"] == "relayed"
    assert result["context"]["organisation"] == "Wood Mackenzie"
    assert result["context"]["organisation"] != case.expectations.reference[
        "relay_host"
    ]
    assert _gates(output, case) == {gate: True for gate in GATES}
    assert all(score == 1.0 for score in _scores(output, case).values())


def test_evidence_words_the_page_does_not_carry_drop_the_figure(
    evidence_verifier_output_for,
) -> None:
    case = _case("invented-evidence-words-rejected")
    expected = _expected(case)
    output = evidence_verifier_output_for(case, [_reply(case)])

    [finding] = output.result["findings"]
    verification = finding["verification"]
    assert verification["status"] == expected["status"] == "dropped"
    assert verification["dropped_reason"] == expected["finding_drop_reason"]
    [result] = verification["figure_results"]
    assert result["dropped_reason"] == expected["figure_drop_reason"]
    assert result["context"] is None
    # The words the checker answered with are nowhere on the page: that is
    # what makes this the invented-evidence case rather than a wording test.
    read = _read_of(case, case.state.raw_findings[0])
    assert not excerpt_matches(" ".join(read.passages.values()), _reference_words(case))
    assert _gates(output, case) == {gate: True for gate in GATES}
    assert _scores(output, case)["expected_outcome"] == 1.0


def test_the_live_case_is_judged_in_one_batch_covering_every_finding(
    evidence_verifier_live_case, evidence_verifier_live_output
) -> None:
    """One Context Check batch, labelled F01..F0n, judges every finding.

    ``context_unchecked`` is the fixture's own witness: it is set only when a
    figure had no reply and code had to fall back, so a run whose reply
    covered every finding leaves it False everywhere. A reply that answered
    one label would light it up on the rest.
    """
    findings = evidence_verifier_live_output.result["findings"]

    assert len(findings) == len(evidence_verifier_live_case.state.raw_findings)
    for finding in findings:
        verification = finding["verification"]
        assert verification is not None
        assert verification["context_unchecked"] is False
        for result in verification["figure_results"]:
            assert result["dropped_reason"] is None
            assert result["context"] is not None


def test_a_repetition_without_a_result_is_judged_from_its_state_update(
    evidence_verifier_output_for,
) -> None:
    """The verifier's snapshot has two artifact homes, and both are read.

    ``VerifiedFindings.findings`` in the result and ``verified_findings`` on
    the state update carry the same judgement; a repetition whose result was
    not recorded is still judged on the snapshot the run wrote into state,
    rather than failing every gate as if it had judged nothing.
    """
    case = _case("relay-labelled-as-relay")
    output = evidence_verifier_output_for(case, [_reply(case)])
    from_state = output.model_copy(
        update={
            "result": None,
            "state_update": {
                **(output.state_update or {}),
                "verified_findings": output.result["findings"],
            },
        }
    )

    assert _gates(from_state, case) == {gate: True for gate in GATES}
    assert from_state.result is None


def test_the_context_check_is_asked_once_per_batch_of_findings() -> None:
    """The verifier's batching is its own contract, and one case is one batch."""
    case = _case("relay-labelled-as-relay")
    completer = ScriptedCompleter(outputs=[_reply(case)])
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="evidence-verifier-case-tests",
            api_key=None,
        )
    )
    agent = EvidenceVerifierAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="evaluation-relay-labelled-as-relay",
            agent_name=EVIDENCE_VERIFIER_NAME,
            max_entries=20,
        ),
    )
    state = case.fresh_state()

    async def run():
        async with tracker.session_span(state.session_id, state.original_question):
            return await agent.run(state)

    asyncio.run(run())

    assert [schema for schema, _, _ in completer.calls] == ["ContextCheckDraft"]
    assert CONTEXT_CHECK_BATCH_SIZE >= 1


# --- the gates fail on a mutated reference output ---------------------------


def test_a_finding_without_verification_fails_the_gate(
    evidence_verifier_output_for,
) -> None:
    case = _case("relay-labelled-as-relay")
    output = evidence_verifier_output_for(case, [_reply(case)])
    mutated = output.without_verification()

    gates = _gates(mutated, case)

    assert gates["verification_recorded"] is False
    assert _scores(mutated, case)[
        "verification_recorded"
    ] == 0.0
    # Nothing else moved: the same mutation leaves the finding on its page.
    assert gates["no_invented_evidence"] is True


def test_invented_evidence_words_fail_the_gate(
    evidence_verifier_output_for,
) -> None:
    case = _case("relay-labelled-as-relay")
    output = evidence_verifier_output_for(case, [_reply(case)])
    invented = "installations of 16 GW of grid-scale batteries"
    mutated = output.with_invented_evidence_words(invented)

    gates = _gates(mutated, case)

    assert gates["no_invented_evidence"] is False
    assert _scores(mutated, case)["no_invented_evidence"] == 0.0
    # The words are not on the page the finding cites, which is what the
    # predicate re-derives; the case's own page carries the real sentence.
    read = _read_of(case, case.state.raw_findings[0])
    assert not excerpt_matches(" ".join(read.passages.values()), invented)
    assert gates["verification_recorded"] is True


def test_a_dropped_figure_without_a_reason_fails_the_gate(
    evidence_verifier_output_for,
) -> None:
    """A drop with no reason is not a record any reader can act on.

    ``FigureResult`` itself refuses the shape — a figure with no drop reason
    and no confirmed context is neither kept nor explained — so the mutation
    makes the snapshot unreadable rather than merely incomplete, and every
    verifier gate fails closed on it. That is the contract: an artifact whose
    drops are unexplained proves nothing, so it is not scored as if it did.
    """
    case = _case("invented-evidence-words-rejected")
    output = evidence_verifier_output_for(case, [_reply(case)])
    mutated = output.without_drop_reason()

    gates = _gates(mutated, case)

    assert gates == {gate: False for gate in GATES}
    assert _scores(mutated, case)["drop_reasons_named"] == 0.0


def test_a_kept_figure_the_page_does_not_state_costs_the_case_metric(
    evidence_verifier_output_for,
) -> None:
    """The declared outcome pins the corrected scope, not just the status."""
    case = _case("scope-corrected-to-all-segments")
    output = evidence_verifier_output_for(
        case, [_reply(case, scope="grid-scale", verdict="correct")]
    )

    scores = _scores(output, case)

    assert scores["expected_outcome"] == 0.0, scores
    # The run still recorded a verification of a page-carrying figure, so only
    # the case-specific metric moves: a near miss costs weight rather than
    # failing the gates.
    assert _gates(output, case) == {gate: True for gate in GATES}


def test_the_expected_outcome_reference_matches_the_seeded_fixture() -> None:
    """Every declared outcome names a page this case actually seeds."""
    for case_id in CONTROLLED:
        case = _case(case_id)
        urls = {finding.source_url for finding in case.state.raw_findings}
        for outcome in case.expectations.reference["expected_outcomes"]:
            assert outcome["source_url"] in urls, case_id


def test_the_replies_are_built_from_the_cases_own_reference() -> None:
    """A case cannot answer its Context Check with words it does not declare."""
    for case_id in CONTROLLED:
        case = _case(case_id)
        [figure_reply] = _reply(case).figures
        assert figure_reply.evidence_words == _reference_words(case)
        assert figure_reply.finding == "F01"
        assert figure_reply.figure == 1
