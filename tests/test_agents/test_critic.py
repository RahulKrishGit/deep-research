"""Tests for the Critic's score clamping, routing maths, and prompts."""

from __future__ import annotations

import json

import pytest

from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    MAX_CRITIC_SCORE,
    MIN_CRITIC_SCORE,
    ROUTING_REASONS,
    CriticAgent,
    CritiqueDraft,
    CritiqueTask,
    _render_spot_check_guidance,
    build_critique,
    clamp_score,
    critique_messages,
    fallback_critique,
    normalize_notes,
    route_decision,
)
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import (
    CRITIC_REVIEW_SYSTEM_PROMPT,
    AgentTask,
)
from deep_research.agents.steps import ReActDecision, ReActRun
from deep_research.evaluation.cases.critic import LIVE_CASES
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    Critique,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import FakeSearchClient, critic_tools

CRITIC_SOURCE_URL = "https://example.org/a"


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


def _source(*, low_confidence: bool = False) -> ScoredSource:
    return ScoredSource(
        url=CRITIC_SOURCE_URL,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(*, verdict: str = "verified") -> Claim:
    return Claim(
        text="Logical error rates fell below break-even in 2025.",
        source_urls=[CRITIC_SOURCE_URL],
        verdict=verdict,
        confidence=0.8,
        evidence=[],
        contradictions=[],
    )


def _draft(
    *,
    score: int = 8,
    gaps: list[str] | None = None,
    unsupported: list[str] | None = None,
    queries: list[str] | None = None,
    rationale: str = "Well sourced and complete.",
) -> CritiqueDraft:
    return CritiqueDraft(
        score=score,
        gaps=gaps or [],
        unsupported_claims=unsupported or [],
        recommended_queries=queries or [],
        rationale=rationale,
    )


def _task(**overrides: object) -> CritiqueTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "report": "# Research report: How mature is quantum error correction?",
        "iteration": 0,
        "max_iterations": 3,
        "claims": [_claim()],
        "sources": [_source()],
        "sub_topics": ["Alpha"],
        "error_count": 2,
    }
    payload.update(overrides)
    return CritiqueTask.model_validate(payload)


# A sentence that exists only inside the registered live case's report body, so
# these tests measure the report's presence rather than a nearby label.
_LIVE_REPORT_PROBE = "Low-carbon cement technologies have moved from pilot"


def _live_report() -> str:
    """The registered live critic case's fixed report."""
    report = (
        next(
            case for case in LIVE_CASES if case.case_id == "critic-live-review"
        )
        .fresh_state()
        .report
    )
    assert report is not None
    assert _LIVE_REPORT_PROBE in report
    return report


def test_spot_check_guidance_carries_the_report_under_review() -> None:
    guidance = _render_spot_check_guidance(
        _live_report(), [], report_chars=6000
    )

    assert _LIVE_REPORT_PROBE in guidance
    assert "Report under review:" in guidance


def test_spot_check_guidance_keeps_the_planned_queries_beside_the_report() -> None:
    guidance = _render_spot_check_guidance(
        _live_report(),
        [
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        report_chars=6000,
    )

    assert _LIVE_REPORT_PROBE in guidance
    assert "- Alpha" in guidance
    assert "  - alpha 2025" in guidance


def test_spot_check_guidance_clamps_the_report_like_the_review_prompt() -> None:
    report = "R" * 5000

    guidance = _render_spot_check_guidance(report, [], report_chars=100)

    assert report not in guidance
    assert "R" * 97 + "..." in guidance


def test_spot_check_guidance_omits_the_report_section_when_there_is_none() -> None:
    guidance = _render_spot_check_guidance("", [], report_chars=6000)

    assert "Report under review" not in guidance


@pytest.mark.asyncio
async def test_the_spot_check_prompt_renders_the_report(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Enough context.", "No spot check needed.")],
        outputs=[_draft(score=9)],
    )
    agent = _critic(tracker, completer, tool_budget=1)
    state = _critic_state(report="# Research report: report-body-marker")

    async with tracker.session_span("session-1", "question"):
        await agent.run(state)

    first_call = completer.calls[0]
    assert first_call[0] == "ReActDecision"
    assert "report-body-marker" in first_call[2][1].content


def test_the_review_call_uses_a_prompt_that_names_no_tools() -> None:
    """The review request offers no tools, so its prompt must not name any.

    Measured root cause: the review payload carries no ``tools`` and no
    ``tool_choice``, yet the shared system prompt announced ``web_search`` and
    ``query_memory``. The model obeyed and emitted DeepSeek tool-invocation
    markup into the message text, where local JSON validation rejected it — 16
    of 30 first attempts. The tool-aware prompt still belongs to the ReAct
    spot-check loop, which does offer the tools.
    """
    system = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[0].content

    assert system == CRITIC_REVIEW_SYSTEM_PROMPT
    lowered = system.lower()
    for forbidden in ("web_search", "query_memory", "tool"):
        assert forbidden not in lowered, forbidden


def _fence_bounds(body: str) -> tuple[int, int, str]:
    """Return the line indices of the report fence, plus its backtick run.

    The closing fence is matched by exact tick count, because a report may
    itself contain a shorter backtick run that is not the closing fence.
    """
    lines = body.splitlines()
    opening = next(
        index for index, line in enumerate(lines) if line.startswith("```")
    )
    tick = lines[opening].removesuffix("report")
    closing = next(
        index for index in range(opening + 1, len(lines)) if lines[index] == tick
    )
    return opening, closing, tick


def test_the_review_request_fences_the_report() -> None:
    """The report is quoted in a Markdown fence, not between angle-bracket tags.

    A fence is the one Markdown construct that delimits a verbatim region, and
    fenced content is not parsed as Markdown, so the report's own headings cannot
    be read as sections of this request. The opening fence is the begin marker
    and the closing fence is the end marker; its info string names the block.
    """
    body = _review_body()
    lines = body.splitlines()
    opening, closing, _ = _fence_bounds(body)

    assert lines[opening] == "```report"
    assert "\n".join(lines[opening + 1 : closing]) == _task().report
    assert "<<<REPORT BEGIN>>>" not in body
    assert "<<<REPORT END>>>" not in body
    # Supporting context follows the closing fence, not inside the report.
    assert lines.index("# Sub-topics planned") > closing


def test_no_request_line_uses_angle_bracket_markers() -> None:
    """Nothing outside the report may be tagged with angle brackets.

    The report is arbitrary provider-written Markdown, so this constrains the
    request's own envelope only, never the report's content.
    """
    body = _review_body()
    lines = body.splitlines()
    opening, closing, _ = _fence_bounds(body)
    envelope = "\n".join([*lines[:opening], *lines[closing + 1 :]])

    assert "<" not in envelope
    assert ">" not in envelope


def test_a_report_containing_a_fence_cannot_close_the_enclosing_fence() -> None:
    """The enclosing fence must outlast any backtick run in the report.

    A synthesised report can quote a fenced block of its own. A fixed three-
    backtick fence would be closed early by that content, exposing the rest of
    the report as if it were request text.
    """
    report = "# Title\n\n```\nquoted code\n```\n\n## Limitations\nNone."
    body = critique_messages(
        _task(report=report),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content
    lines = body.splitlines()
    opening, closing, tick = _fence_bounds(body)

    assert tick == "````"
    assert lines[opening] == "````report"
    assert "\n".join(lines[opening + 1 : closing]) == report


def _review_body() -> str:
    return critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content


def test_no_request_heading_can_be_confused_with_a_report_heading() -> None:
    """Request sections are H1; the report's own sections are H2.

    The canonical report is ``REPORT_SECTIONS`` — H2 body sections with H3
    sub-groups — and this request previously used H2 as well, so
    ``## Recorded problems`` sat at the same visual level as the report's own
    ``## Limitations``, separated only by the markers. At H1 every request
    heading outranks the report, so the report reads as content nested inside
    ``# Report under review``.
    """
    body = _review_body()
    lines = body.splitlines()
    opening, closing, _ = _fence_bounds(body)
    envelope = "\n".join([*lines[:opening], *lines[closing + 1 :]])

    collisions = [line for line in envelope.splitlines() if line.startswith("## ")]
    assert not collisions, f"request sections at H2: {collisions}"
    for section in (
        "# Research question",
        "# Report under review",
        "# Sub-topics planned",
        "# Claim verdicts",
        "# Source quality",
        "# Recorded problems",
        "# Spot checks",
        "# Response contract",
        "# How to choose the score",
        "# Reply format",
    ):
        assert section in envelope, section


def test_the_report_section_says_whose_headings_are_whose() -> None:
    """The model must know the report's headings belong to the report.

    The report carries its own H2 sections, so the request has to say so rather
    than relying on heading level alone to carry the boundary.
    """
    body = _review_body()

    assert (
        "its own headings belong to the report rather than to this request" in body
    )


def test_the_review_request_names_json_and_shows_its_shape() -> None:
    """The request must say JSON, and show the object it wants.

    Measured on the live-tested commit: the review prompt contained zero JSON
    lines and never used the word JSON, while the judge prompt — which has never
    recorded a ``json_invalid`` failure on the same model, transport, and effort
    — contained 147 JSON lines. DeepSeek's JSON Output guide requires the word
    "json" in the prompt and an example of the desired JSON format.

    A 50-request baseline measured a 22% failure rate for this call, and all
    failures were non-empty text that was not valid JSON, which is the mode this
    contract targets.
    """
    body = _review_body()

    assert "JSON" in body
    assert "# Reply format" in body
    for field in (
        "score",
        "gaps",
        "unsupported_claims",
        "recommended_queries",
        "rationale",
    ):
        assert f'"{field}"' in body


def test_the_reply_examples_are_valid_json_instances() -> None:
    """Both examples must be real JSON instances, not placeholder skeletons.

    The earlier skeleton used angle-bracket placeholders like
    ``<integer 1-10>``, which is not valid JSON, so the model was shown
    something that was neither a schema nor an example.
    """
    body = _review_body()
    examples = [
        json.loads(line) for line in body.splitlines() if line.startswith('{"score"')
    ]

    assert len(examples) == 2
    for payload in examples:
        assert sorted(payload) == [
            "gaps",
            "rationale",
            "recommended_queries",
            "score",
            "unsupported_claims",
        ]
        assert isinstance(payload["score"], int)
        assert 1 <= payload["score"] <= 10
        assert isinstance(payload["gaps"], list)
        assert isinstance(payload["unsupported_claims"], list)
        assert isinstance(payload["recommended_queries"], list)
        assert payload["rationale"].strip()
    # No angle-bracket placeholder may remain anywhere in the request.
    assert "<integer" not in body
    assert "<string>" not in body


def test_the_examples_bracket_the_outcome_threshold() -> None:
    """One example sits below the acceptance score and one clearly above.

    Two examples alone invite the model to split the difference. The pair must
    actually exercise both sides of the threshold the system computes routing
    from, so the scale in use is unambiguous.
    """
    body = _review_body()
    scores = [
        json.loads(line)["score"]
        for line in body.splitlines()
        if line.startswith('{"score"')
    ]

    assert min(scores) < ACCEPTANCE_SCORE
    assert max(scores) > ACCEPTANCE_SCORE
    assert "Weak report" in body
    assert "Strong report" in body


def test_the_request_gives_explicit_guidance_for_choosing_a_score() -> None:
    """Both examples need a stated rule for choosing between them.

    Before this, the only calibration was the endpoints "1 is unusable" and "10
    answers completely", which says nothing about the middle of the scale.
    """
    body = _review_body()

    assert "# How to choose the score" in body
    for band in ("1-3:", "4-6:", "7-8:", "9-10:"):
        assert band in body, band
    # The rule must be anchored on evidence, not on prose quality.
    assert "weakest load-bearing element" in body
    assert "not from its overall polish" in body


def test_each_example_demonstrates_the_band_it_is_labelled_with() -> None:
    """The anchors must show their own band, not merely claim a number.

    A labelled pair is worthless if the low example reads like a 4-6 and the
    high example reads like a 7-8, because the model calibrates against what
    the examples *show*. Band 1-3 turns on claims with no cited source, and
    band 9-10 turns on completeness with narrow gaps, so the examples have to
    differ on exactly those signals.
    """
    body = _review_body()
    examples = [
        json.loads(line) for line in body.splitlines() if line.startswith('{"score"')
    ]
    low, high = sorted(examples, key=lambda payload: payload["score"])

    assert 1 <= low["score"] <= 3
    assert low["unsupported_claims"], "band 1-3 is about unsourced central claims"
    assert 9 <= high["score"] <= 10
    assert high["unsupported_claims"] == []
    assert len(high["gaps"]) <= 1, "band 9-10 allows at most narrow gaps"
    assert len(low["gaps"]) > len(high["gaps"])


def test_the_score_guidance_does_not_leak_routing_policy() -> None:
    """Routing is computed locally; the prompt must not state the threshold.

    `CRITIQUE_INSTRUCTION` tells the model not to decide continuation, so the
    band table must describe report quality rather than publish the number the
    router compares against.
    """
    body = _review_body()

    assert "Do not decide whether research continues" in body
    assert f"score {ACCEPTANCE_SCORE}" not in body
    assert f"at least {ACCEPTANCE_SCORE}" not in body
    assert "acceptance" not in body.lower()


def test_the_request_states_its_json_requirement_once() -> None:
    """One format requirement, not three competing renditions.

    The request previously carried the response-contract prose, a skeleton and
    the provider's trailing schema message, each phrasing the JSON demand
    differently. The prose no longer repeats it; the example is the single
    agent-side statement, and the provider message remains the schema.
    """
    body = _review_body()

    assert body.count("# Reply format") == 1
    assert "Reply with a single JSON object" not in body
    assert "Do not wrap it in Markdown code fences" not in body


def test_the_json_demand_preserves_the_scoring_contract() -> None:
    """The JSON contract is additive; the scoring contract is unchanged."""
    body = _review_body()

    for requirement in (
        "an integer from 1 to 10",
        "list a gap only when closing it would materially change the",
        "an empty list when the report is materially complete",
        "Do not decide whether research continues",
        "Never restate the score alone",
    ):
        assert requirement in body


@pytest.mark.asyncio
async def test_only_the_review_call_gets_the_operation_output_budget(
    tracker: Tracker,
) -> None:
    """The report review is the one call that may exceed the global cap.

    The Critic's review renders the report, claims, source scores, and
    spot-check evidence and then asks for a score plus three lists plus a
    rationale in one JSON object. At the global cap it returned non-JSON text
    on both the initial attempt and the single repair in three consecutive
    live canaries, so it carries an operation-specific budget. ReAct decisions
    must stay at the global cap: widening them would change every agent's
    loop, not this one call.
    """
    completer = ScriptedCompleter(
        decisions=[finish("Enough context.", "No spot check needed.")],
        outputs=[_draft(score=9)],
    )
    agent = _critic(tracker, completer, tool_budget=1)

    async with tracker.session_span("session-1", "question"):
        await agent.run(_critic_state())

    budgets = dict(
        zip((call[0] for call in completer.calls), completer.budgets, strict=True)
    )
    assert budgets["ReActDecision"] == (
        AgentRuntimeConfig().react_decision_max_tokens
    )
    assert budgets["CritiqueDraft"] == AgentRuntimeConfig().critic_review_max_tokens


@pytest.mark.asyncio
async def test_the_review_budget_follows_the_agent_configuration(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Enough context.", "No spot check needed.")],
        outputs=[_draft(score=9)],
    )
    agent = _critic(
        tracker,
        completer,
        tool_budget=1,
        config=AgentRuntimeConfig(
            max_iterations=2, tool_budget=1, critic_review_max_tokens=16384
        ),
    )

    async with tracker.session_span("session-1", "question"):
        await agent.run(_critic_state())

    review_index = next(
        index
        for index, call in enumerate(completer.calls)
        if call[0] == "CritiqueDraft"
    )
    assert completer.budgets[review_index] == 16384


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(-4, 1), (0, 1), (1, 1), (7, 7), (10, 10), (99, 10)],
)
def test_scores_are_pinned_into_the_critic_score_range(
    raw: int, expected: int
) -> None:
    assert clamp_score(raw) == expected
    assert MIN_CRITIC_SCORE <= clamp_score(raw) <= MAX_CRITIC_SCORE


def test_notes_are_collapsed_deduplicated_and_capped() -> None:
    notes = normalize_notes(
        ["  No  cost data. ", "No cost data.", "", "   ", "No vendor audit."],
        limit=5,
    )

    assert notes == ["No cost data.", "No vendor audit."]
    assert normalize_notes(["a", "b", "c"], limit=2) == ["a", "b"]
    with pytest.raises(ValueError, match="limit"):
        normalize_notes(["a"], limit=0)


def test_an_acceptable_report_ends_the_run() -> None:
    assert route_decision(
        score=ACCEPTANCE_SCORE,
        gaps=[],
        unsupported_claims=[],
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (False, "accepted_quality")


@pytest.mark.parametrize(
    ("score", "gaps", "unsupported", "reason"),
    [
        (6, [], [], "low_score"),
        (9, ["No cost data."], [], "critical_gaps"),
        (9, [], ["Costs fell tenfold."], "unsupported_claims"),
    ],
)
def test_a_weak_report_continues_with_the_reason_that_applies(
    score: int, gaps: list[str], unsupported: list[str], reason: str
) -> None:
    assert route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (True, reason)


def test_the_iteration_bound_beats_every_quality_signal() -> None:
    assert route_decision(
        score=1,
        gaps=["Everything is missing."],
        unsupported_claims=["All of it."],
        iteration=3,
        max_iterations=3,
        has_report=True,
    ) == (False, "max_iterations_reached")


def test_a_missing_report_continues_while_budget_remains() -> None:
    assert route_decision(
        score=10,
        gaps=[],
        unsupported_claims=[],
        iteration=1,
        max_iterations=3,
        has_report=False,
    ) == (True, "missing_report")


def test_a_critique_is_validated_clamped_and_routed() -> None:
    critique, reason = build_critique(
        _draft(
            score=99,
            gaps=["  No cost data. ", "No cost data."],
            queries=["qec cost 2025"],
        ),
        iteration=0,
        max_iterations=3,
    )

    assert isinstance(critique, Critique)
    assert critique.score == MAX_CRITIC_SCORE
    assert critique.gaps == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]
    assert critique.should_continue is True
    assert reason == "critical_gaps"
    assert critique.rationale.startswith("Well sourced and complete.")
    assert ROUTING_REASONS["critical_gaps"] in critique.rationale


def test_a_blank_model_rationale_still_yields_a_usable_one() -> None:
    critique, reason = build_critique(
        _draft(rationale="   "), iteration=0, max_iterations=3
    )

    assert critique.rationale == ROUTING_REASONS["accepted_quality"]
    assert reason == "accepted_quality"
    assert critique.should_continue is False


def test_the_last_iteration_stops_even_on_a_scathing_critique() -> None:
    critique, reason = build_critique(
        _draft(score=2, gaps=["No cost data."]),
        iteration=3,
        max_iterations=3,
    )

    assert critique.should_continue is False
    assert reason == "max_iterations_reached"
    assert critique.gaps == ["No cost data."]
    assert ROUTING_REASONS["max_iterations_reached"] in critique.rationale


def test_a_provider_outage_never_buys_another_research_cycle() -> None:
    critique, reason = fallback_critique(
        reason="provider_unavailable", iteration=0, max_iterations=3
    )

    assert critique.score == MIN_CRITIC_SCORE
    assert critique.should_continue is False
    assert reason == "provider_unavailable"
    assert critique.rationale == ROUTING_REASONS["provider_unavailable"]


def test_a_missing_report_is_worth_one_more_cycle() -> None:
    critique, reason = fallback_critique(
        reason="missing_report", iteration=0, max_iterations=3
    )

    assert critique.should_continue is True
    assert reason == "missing_report"
    assert critique.gaps == ["No report was available to review."]

    exhausted, exhausted_reason = fallback_critique(
        reason="missing_report", iteration=3, max_iterations=3
    )
    assert exhausted.should_continue is False
    assert exhausted_reason == "max_iterations_reached"


def test_fallback_critique_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        fallback_critique(reason="because", iteration=0, max_iterations=3)


def test_critique_messages_carry_the_report_and_every_quality_signal() -> None:
    messages = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "# Research question" in body
    assert "# Report under review" in body
    assert "# Research report:" in body
    assert "# Sub-topics planned" in body
    assert "- Alpha" in body
    assert "# Claim verdicts" in body
    assert "[verified 0.80]" in body
    assert "# Source quality" in body
    assert "# Recorded problems" in body
    assert "2 error(s)" in body
    assert "# Spot checks" in body
    assert "# Response contract" in body


def test_critique_messages_clamp_a_long_report_without_flattening_it() -> None:
    report = "# Title\n\n" + ("x" * 500)
    body = critique_messages(
        _task(report=report),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=80,
        claim_digest=10,
    )[1].content

    assert "# Title\n" in body
    assert "x" * 500 not in body
    assert "..." in body


def test_critique_messages_say_so_when_there_is_no_report() -> None:
    body = critique_messages(
        _task(report="   "),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=80,
        claim_digest=10,
    )[1].content

    assert "(no report)" in body


def _critic(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    tools: list[BaseTool] | None = None,
    tool_budget: int = 0,
    config: AgentRuntimeConfig | None = None,
) -> CriticAgent:
    return CriticAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="critic", max_entries=20
        ),
        tools=tools if tools is not None else critic_tools(tracker),
        config=(
            config
            if config is not None
            else AgentRuntimeConfig(max_iterations=2, tool_budget=tool_budget)
        ),
    )


def _critic_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "sub_topics": [
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        "evaluated_sources": [_source()],
        "verified_claims": [_claim()],
        "report": "# Research report: How mature is quantum error correction?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def test_build_task_carries_the_report_budget_and_quality_signals(
    tracker: Tracker,
) -> None:
    agent = _critic(tracker, ScriptedCompleter())
    state = _critic_state(iteration=2, max_iterations=3)

    task = agent.build_task(state)

    assert task.instruction == state.original_question
    assert task.report == state.report
    assert task.iteration == 2
    assert task.max_iterations == 3
    assert task.sub_topics == ["Alpha"]
    assert len(task.claims) == 1
    assert len(task.sources) == 1
    assert task.error_count == 0


@pytest.mark.asyncio
async def test_first_spot_check_receives_planned_search_query_guidance(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Enough context.", "No spot check needed.")],
        outputs=[_draft(score=9)],
    )
    agent = _critic(tracker, completer, tool_budget=1)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert outcome.react.stop_reason == "finished"
    first_call = completer.calls[0]
    assert first_call[0] == "ReActDecision"
    user_prompt = first_call[2][1].content
    assert "Alpha" in user_prompt
    assert "alpha 2025" in user_prompt
    assert "use an applicable planned search query verbatim" in user_prompt


@pytest.mark.asyncio
async def test_an_acceptable_report_ends_the_graph(tracker: Tracker) -> None:
    agent = _critic(
        tracker, ScriptedCompleter(decisions=[], outputs=[_draft(score=9)])
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.score == 9
    assert outcome.state_update["critique"] == critique
    assert outcome.errors == []


@pytest.mark.asyncio
async def test_a_low_quality_report_asks_for_another_pass(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                _draft(
                    score=4,
                    gaps=["No cost data."],
                    unsupported=["Costs fell tenfold."],
                    queries=["qec cost 2025"],
                    rationale="One source carries the whole argument.",
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is True
    assert critique.gaps == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]


@pytest.mark.asyncio
async def test_the_final_iteration_forces_a_stop(tracker: Tracker) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(outputs=[_draft(score=2, gaps=["No cost data."])]),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state(iteration=3, max_iterations=3))

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.gaps == ["No cost data."]
    event = outcome.state_update["events"][-1]
    assert event.metadata["reason"] == "max_iterations_reached"
    assert event.metadata["should_continue"] is False


@pytest.mark.asyncio
async def test_a_run_emits_the_routing_counts_the_spec_requires(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                _draft(
                    score=5,
                    gaps=["No cost data.", "No vendor audit."],
                    unsupported=["Costs fell tenfold."],
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "critic.critique.started",
        "critic.critique.completed",
    ]
    completed = events[-1].metadata
    assert completed["score"] == 5
    assert completed["gap_count"] == 2
    assert completed["unsupported_claim_count"] == 1
    assert completed["should_continue"] is True
    assert completed["reason"] == "low_score"
    assert completed["iteration"] == 0
    assert completed["max_iterations"] == 3


@pytest.mark.asyncio
async def test_a_missing_report_is_recorded_without_a_provider_call(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state(report=None))

    assert completer.calls == []
    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is True
    assert critique.score == MIN_CRITIC_SCORE
    assert [error.error_type for error in outcome.errors] == [
        "critic_missing_report"
    ]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_still_routes_and_stops(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker, ScriptedCompleter(outputs=[_output_limit_error()])
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.score == MIN_CRITIC_SCORE
    assert outcome.react.stop_reason == "provider_error"
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_provider_error"
    )
    assert error.recoverable is False
    assert error.details["operation"] == "critic_report_review"
    provider = error.details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1


@pytest.mark.asyncio
async def test_an_http_provider_failure_still_routes_and_stops(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                ProviderResponseError(
                    "provider returned an HTTP error",
                    retryable=True,
                    failure_category="http",
                    http_status_code=503,
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert outcome.react.stop_reason == "provider_error"
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_provider_error"
    )
    provider = error.details["provider_failure"]
    assert provider["kind"] == "provider_http"
    assert provider["http_status_code"] == 503


@pytest.mark.asyncio
async def test_a_spot_check_reaches_the_review_prompt(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Check the cost figure.",
                "web_search",
                '{"query": "qec cost 2025"}',
            ),
            finish("Enough to judge.", "The cost figure checks out."),
        ],
        outputs=[_draft(score=9)],
    )
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(tracker),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert outcome.react.tool_calls == 1
    review_call = next(
        call for call in completer.calls if call[0] == "CritiqueDraft"
    )
    assert "[web_search]" in review_call[2][1].content
    event = outcome.state_update["events"][-1]
    assert event.metadata["tool_calls"] == 1


@pytest.mark.asyncio
async def test_critic_react_handles_empty_unused_final_answer(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            ReActDecision(
                thought="Check the cost figure.",
                action="use_tool",
                tool_name="web_search",
                tool_input_json='{"query": "qec cost 2025"}',
                final_answer="",
            ),
            finish("Enough to judge.", "The cost figure checks out."),
        ],
        outputs=[_draft(score=9)],
    )
    agent = _critic(tracker, completer, tool_budget=2)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert outcome.react.stop_reason == "finished"
    assert outcome.react.steps[0].final_answer is None


@pytest.mark.asyncio
async def test_a_failing_spot_check_never_stops_the_review(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search first.", "web_search", '{"query": "qec"}'),
            finish("Judge without it.", "The search failed."),
        ],
        outputs=[_draft(score=8)],
    )
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(
            tracker, search=FakeSearchClient([RuntimeError("tavily down")])
        ),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.score == 8
    assert outcome.react.stop_reason == "finished"


@pytest.mark.asyncio
async def test_finalize_requires_a_critique_task(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(outputs=[_draft()]))

    with pytest.raises(AgentConfigurationError, match="CritiqueTask"):
        await agent.finalize(
            AgentTask(instruction="anything"),
            ReActRun(agent_name="critic", stop_reason="finished"),
        )


def test_the_critic_declares_its_spot_check_tools(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(), tools=critic_tools(tracker))

    assert CriticAgent.name == "critic"
    assert CriticAgent.allowed_tools == ("web_search", "query_memory")
    assert agent.output_schema is Critique
