"""Tests for the Critic's score clamping, routing maths, and prompts."""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from deep_research.agents.critic import (
    _CRITIQUE_HIGH_EXAMPLE_JSON,
    _CRITIQUE_LOW_EXAMPLE_JSON,
    _CRITIQUE_SCORE_BANDS,
    _HIGH_EXAMPLE_SCORE,
    _LOW_EXAMPLE_SCORE,
    ACCEPTANCE_SCORE,
    CRITIC_EVIDENCE_BATCH_CHARS,
    CRITIC_REPORT_CHARS,
    CRITIQUE_INSTRUCTION,
    DEFAULT_MAX_NOTES,
    MAX_CRITIC_SCORE,
    MIN_CRITIC_SCORE,
    QUESTION_TARGET_ID,
    ROUTING_REASONS,
    CriticAgent,
    CriticPacket,
    CritiqueContractViolation,
    CritiqueDraft,
    CritiqueGapDraft,
    CritiqueRepairRefused,
    CritiqueTask,
    build_critic_packet,
    build_critique,
    critic_packet_fingerprint,
    critique_messages,
    fallback_critique,
    normalize_gaps,
    normalize_notes,
    repair_target,
    route_decision,
)
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.identity import claim_fingerprint
from deep_research.agents.prompts import (
    CRITIC_REVIEW_SYSTEM_PROMPT,
    AgentTask,
)
from deep_research.agents.steps import ReActRun
from deep_research.evaluation.cases.critic import LIVE_CASES
from deep_research.graph.nodes import REPAIR_NODES
from deep_research.graph.state import HALTING_ERROR_TYPES
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    EVIDENCE_BADGE_LABELS,
    REPAIR_ACTIONS,
    AtomicProposition,
    Claim,
    ClaimCluster,
    Critique,
    CritiqueGap,
    EvidenceTarget,
    EvidenceUnit,
    ReportComposition,
    ReportPoint,
    ReportQualitySnapshot,
    ReportSection,
    ReportStatement,
    ResearchError,
    ResearchState,
    target_is_answered,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
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
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(*, verdict: str = "verified") -> Claim:
    return Claim(
        claim_id=claim_fingerprint(
            "Logical error rates fell below break-even in 2025."
        ),
        text="Logical error rates fell below break-even in 2025.",
        source_urls=[CRITIC_SOURCE_URL],
        verdict=verdict,
        evidence_status=(
            "verified_pair"
            if verdict == "verified"
            else "source_supported"
            if verdict == "insufficient_evidence"
            else None
        ),
        confidence=0.8,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
    )


def _draft(
    *,
    score: int = 8,
    gaps: list[str] | None = None,
    unsupported: list[str] | None = None,
    queries: list[str] | None = None,
    rationale: str = "Well sourced and complete.",
) -> CritiqueDraft:
    """One valid draft, with a string gap spelled as the typed provider shape.

    ``CritiqueDraft`` refuses a bare string gap (the provider contract is
    typed); the shorthand survives here because these tests are about routing
    and normalization, not about the reply grammar.
    """
    return CritiqueDraft(
        score=score,
        gaps=[_typed_gap(problem) for problem in (gaps or [])],
        unsupported_claims=unsupported or [],
        recommended_queries=queries or [],
        rationale=rationale,
    )


def _typed_gap(problem: str) -> CritiqueGapDraft:
    """The pre-Task-8 string-gap shape, written out as the provider sends it."""
    return CritiqueGapDraft(
        target_ids=[QUESTION_TARGET_ID],
        kind="coverage",
        severity="major",
        repair_action="acquire",
        problem=problem,
        recommended_queries=[],
    )


def _alpha() -> SubTopic:
    """The one planned sub-topic every Critic request test renders."""
    return SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="Alpha is load-bearing.",
        search_queries=["alpha 2025"],
        success_criteria=["A named source about Alpha."],
        priority=1,
    )


def _task(**overrides: object) -> CritiqueTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "report": "# Research report: How mature is quantum error correction?",
        "iteration": 0,
        "max_iterations": 3,
        "claims": [_claim()],
        "sources": [_source()],
        "sub_topics": [_alpha()],
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


@pytest.mark.asyncio
async def test_the_review_request_renders_the_report_without_any_tool_turn(
    tracker: Tracker,
) -> None:
    """The review is the Critic's only request, and it carries the report.

    Task 8 removed the spot-check loop: the Critic reviews the exact candidate
    and performs no discovery call at all, so the first provider request is the
    structured review and there is no ReAct turn in front of it.
    """
    completer = ScriptedCompleter(outputs=[_draft(score=9)])
    agent = _critic(tracker, completer, tool_budget=1)
    state = _critic_state(report="# Research report: report-body-marker")

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert completer.react_calls == []
    assert [call[0] for call in completer.calls] == ["CritiqueDraft"]
    assert "report-body-marker" in completer.calls[0][2][1].content
    assert outcome.react.tool_calls == 0
    assert outcome.react.stop_reason == "finished"


def test_the_critic_weak_and_strong_examples_are_valid_and_in_band() -> None:
    """Preservation test for the Critic's already-specialized example pair.

    Both examples must stay valid JSON that validates as ``CritiqueDraft``,
    and each must sit inside the band its own label claims: 1-3 for the weak
    example and 9-10 for the strong one. A malformed or out-of-band example
    would teach the model the wrong scale.
    """
    weak = json.loads(_CRITIQUE_LOW_EXAMPLE_JSON)
    strong = json.loads(_CRITIQUE_HIGH_EXAMPLE_JSON)

    assert CritiqueDraft.model_validate(weak).score == _LOW_EXAMPLE_SCORE
    assert CritiqueDraft.model_validate(strong).score == _HIGH_EXAMPLE_SCORE
    assert 1 <= weak["score"] <= 3
    assert 9 <= strong["score"] <= 10
    # "Negative" is weak semantics, never malformed JSON: the weak example
    # still carries populated lists.
    assert weak["gaps"] and weak["unsupported_claims"]
    assert strong["gaps"] is not None and strong["unsupported_claims"] == []


def test_the_review_call_uses_a_prompt_that_names_no_tools() -> None:
    """The review request offers no tools, so its prompt must not name any.

    Measured root cause: the review payload carries no ``tools`` and no
    ``tool_choice``, yet the shared system prompt announced ``web_search`` and
    ``query_memory``. The model obeyed and emitted DeepSeek tool-invocation
    markup into the message text, where local JSON validation rejected it — 16
    of 30 first attempts. Task 8 removed the tool path entirely, so the prompt
    states the absence of tools instead of advertising one.
    """
    system = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
    )[0].content

    assert system == CRITIC_REVIEW_SYSTEM_PROMPT
    lowered = system.lower()
    for forbidden in (
        "web_search",
        "query_memory",
        "web_scraper",
        "document_reader",
        "save_to_memory",
        "write_document",
    ):
        assert forbidden not in lowered, forbidden
    assert "no tools" in lowered


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
        "# Answer contract",
        "# Reader content",
        "# Sub-topics planned",
        "# Reader statements",
        "# Evidence targets",
        "# Evidence — batched read excerpts",
        "# Hard checks",
        "# Claim verdicts",
        "# Source quality",
        "# Recorded problems",
        "# Response contract",
        "# How to choose the score",
        "# Reply format",
        "# Packet fingerprint",
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


def _collapsed(text: str) -> str:
    """Whitespace runs collapsed: the prompt is wrapped, the meaning is not."""
    return " ".join(text.split())


def _critique_bands() -> tuple[tuple[int, int, str], ...]:
    """Parse the rendered band table into ``(low, high, guidance)`` rows.

    The table opens with one line saying how to choose a score, then one line per
    band, so only the band lines are parsed.
    """
    bands: list[tuple[int, int, str]] = []
    for line in _CRITIQUE_SCORE_BANDS.splitlines():
        match = re.fullmatch(r"(\d+)-(\d+): (.+)", line)
        if match is not None:
            bands.append((int(match[1]), int(match[2]), match[3]))
    assert bands, "the band table must state at least one band"
    return tuple(bands)


def test_the_score_bands_cover_the_declared_range_without_overlap_or_gaps() -> None:
    """Step 3: every score the router can see has exactly one band.

    Routing compares the score against ``ACCEPTANCE_SCORE``, so a range the band
    table leaves unstated is a range the model has no instruction for. The
    earlier prompt anchored only 1 and 10 and said nothing about the middle. The
    top band is worded as a reservation, which is what keeps the best scores from
    becoming the default.
    """
    bands = _critique_bands()

    assert bands[0][0] == MIN_CRITIC_SCORE
    assert bands[-1][1] == MAX_CRITIC_SCORE
    covered = [score for low, high, _ in bands for score in range(low, high + 1)]
    assert covered == list(range(MIN_CRITIC_SCORE, MAX_CRITIC_SCORE + 1))
    # Contradictory endpoint language: the top band reserves its own range, and
    # no band names a score that belongs to a different band.
    assert "reserve" in bands[-1][2].lower()
    for low, high, guidance in bands:
        for value in (int(item) for item in re.findall(r"\d+", guidance)):
            assert low <= value <= high, (low, high, guidance)


def test_each_example_sits_inside_its_band_and_brackets_the_threshold() -> None:
    """Step 3: the labelled pair straddles the score routing turns on.

    Both examples must be schema-valid and semantically opposite — a weak report
    and a strong one — and each must land inside the band its own label claims,
    or the pair teaches a scale the band table contradicts.
    """
    bands = _critique_bands()
    weak = CritiqueDraft.model_validate_json(_CRITIQUE_LOW_EXAMPLE_JSON)
    strong = CritiqueDraft.model_validate_json(_CRITIQUE_HIGH_EXAMPLE_JSON)

    assert weak.score == _LOW_EXAMPLE_SCORE
    assert strong.score == _HIGH_EXAMPLE_SCORE
    weak_band = next(band for band in bands if band[0] <= weak.score <= band[1])
    strong_band = next(band for band in bands if band[0] <= strong.score <= band[1])
    assert weak_band != strong_band
    assert weak.score < ACCEPTANCE_SCORE < strong.score
    # Opposite semantic cases, never one case at two values.
    assert weak.unsupported_claims and strong.unsupported_claims == []
    assert len(weak.gaps) > len(strong.gaps)
    body = _review_body()
    assert f"Weak report, score {weak.score}:" in body
    assert f"Strong report, score {strong.score}:" in body


def test_the_labelled_pair_is_introduced_as_a_scale_not_a_target() -> None:
    """Step 3: the pair is an illustration of the scale, stated once.

    Both examples are labelled by the case they show, and the contract presents
    them as examples of the scale in use. There is exactly one reply contract,
    so the pair cannot be read as a second, competing protocol.
    """
    body = _review_body()
    contract = body[body.index("# Reply format") :]
    prose = _collapsed(contract)

    assert "Two complete examples" in prose
    assert "one for a weak report and one for a strong one" in prose
    assert "showing the scale in use" in prose
    assert f"Weak report, score {_LOW_EXAMPLE_SCORE}:" in contract
    assert f"Strong report, score {_HIGH_EXAMPLE_SCORE}:" in contract
    assert contract.count("JSON object") == 1


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
        "list a gap only when it is a real, material defect",
        "an empty gap",
        "Do not decide whether research continues",
        "Never restate the score alone",
    ):
        assert requirement in body


@pytest.mark.asyncio
async def test_only_the_review_call_gets_the_operation_output_budget(
    tracker: Tracker,
) -> None:
    """The report review is the one call the Critic makes.

    The review renders the report, the claim verdicts, the source quality
    signals, and the packet's evidence and then asks for a score plus three
    lists plus a rationale in one JSON object. At the global cap it returned
    non-JSON text on both the initial attempt and the single repair in three
    consecutive live canaries, so it carries an operation-specific budget.
    Task 8 removed the ReAct spot-check loop, so no decision request shares it.
    """
    completer = ScriptedCompleter(outputs=[_draft(score=9)])
    agent = _critic(tracker, completer, tool_budget=1)

    async with tracker.session_span("session-1", "question"):
        await agent.run(_critic_state())

    assert completer.react_budgets == []
    budgets = dict(
        zip((call[0] for call in completer.calls), completer.budgets, strict=True)
    )
    assert budgets["CritiqueDraft"] == AgentRuntimeConfig().critic_review_max_tokens


@pytest.mark.asyncio
async def test_the_review_budget_follows_the_agent_configuration(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[_draft(score=9)])
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


@pytest.mark.parametrize("score", (-4, 0, 11, 99))
def test_a_provider_score_outside_the_critic_range_is_invalid(
    score: int,
) -> None:
    """C3: a score outside 1-10 is a malformed reply, not a value to pin.

    ``clamp_score`` turned ``{"score": 99}`` into 10, so a number the model
    never wrote could carry a review to ``accepted_quality``. The range is part
    of the reply contract now, and an out-of-range reply takes the same one
    repair as any other malformed one.
    """
    with pytest.raises(ValidationError):
        CritiqueDraft.model_validate(
            {
                "score": score,
                "gaps": [],
                "unsupported_claims": [],
                "recommended_queries": [],
                "rationale": "Review.",
            }
        )


@pytest.mark.parametrize("score", (MIN_CRITIC_SCORE, 7, MAX_CRITIC_SCORE))
def test_a_provider_score_inside_the_critic_range_is_kept(score: int) -> None:
    """The other side of C3's bound: an in-band score is the score used."""
    draft = CritiqueDraft.model_validate(
        {
            "score": score,
            "gaps": [],
            "unsupported_claims": [],
            "recommended_queries": [],
            "rationale": "Review.",
        }
    )

    assert draft.score == score


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


def test_a_critique_is_validated_and_routed() -> None:
    """An in-band score reaches the critique verbatim, with the route it implies.

    ``score=99`` used to be this test's subject: the draft accepted any integer
    and ``build_critique`` pinned it into the band. That bound is part of the
    reply contract now (see the boundary tests above), so what is left here is
    that a legal score survives into the critique unchanged.
    """
    critique, reason = build_critique(
        _draft(
            score=9,
            gaps=["  No cost data. ", "No cost data."],
            queries=["qec cost 2025"],
        ),
        iteration=0,
        max_iterations=3,
    )

    assert isinstance(critique, Critique)
    assert critique.score == 9
    assert [gap.problem for gap in critique.gaps] == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]
    assert critique.should_continue is True
    assert reason == "critical_gaps"
    assert critique.rationale.startswith("Well sourced and complete.")
    assert ROUTING_REASONS["critical_gaps"] in critique.rationale


def test_a_known_coverage_id_is_kept_and_an_unknown_one_is_refused() -> None:
    """An id the plan cannot answer is not obeyed *or* invented around.

    Task 7 globalized an unknown id into a whole-answer gap. That turned a
    hallucinated scope into a material obligation about the question, and the
    ``question`` sentinel means one specific thing — an original-question
    omission, which the prompt pairs with ``extend_plan``. A gap that names a
    record nothing can resolve is now refused, so the reply is repaired rather
    than recorded as something the model never said.
    """
    known = CritiqueGapDraft(
        coverage_id="topic-01",
        kind="coverage",
        severity="major",
        repair_action="acquire",
        problem="Alpha lacks cost evidence.",
        recommended_queries=["alpha cost 2025"],
    )
    unknown = CritiqueGapDraft(
        coverage_id="topic-999",
        kind="coverage",
        severity="major",
        repair_action="acquire",
        problem="The provider invented this plan id.",
        recommended_queries=["invented topic evidence"],
    )
    draft = CritiqueDraft(
        score=4,
        gaps=[known],
        unsupported_claims=[],
        recommended_queries=[],
        rationale="The report needs targeted evidence.",
    )

    critique, reason = build_critique(
        draft,
        iteration=0,
        max_iterations=3,
        known_coverage_ids={"topic-01"},
    )

    assert reason == "low_score"
    assert critique.gaps == [
        # A gap that named a known planned sub-topic keeps that id. No target
        # id is invented for it: without a packet there is no target registry
        # to resolve the sub-topic against, and the coverage id is a real,
        # routable scope on its own.
        CritiqueGap(
            gap_id="gap-01",
            coverage_id="topic-01",
            target_ids=[],
            problem="Alpha lacks cost evidence.",
            recommended_queries=["alpha cost 2025"],
        ),
    ]

    with pytest.raises(CritiqueContractViolation) as caught:
        build_critique(
            CritiqueDraft(
                score=4,
                gaps=[unknown],
                unsupported_claims=[],
                recommended_queries=[],
                rationale="The report needs targeted evidence.",
            ),
            iteration=0,
            max_iterations=3,
            known_coverage_ids={"topic-01"},
        )
    assert caught.value.field_paths() == ("gaps.0",)
    assert "do not resolve" in caught.value.problem


def test_normalize_gaps_accepts_a_legacy_string_gap() -> None:
    """The one gap normalizer reads the pre-Task-7 free-text shape too.

    The state-facing ``Critique`` hands a legacy string list to this function,
    which is the single place the old shape is understood. A legacy gap named no
    plan id and no statement, so its honest scope is the whole answer — never a
    fabricated topic id, and never *no* scope at all, because Task 9 routes
    ``acquire`` by the target it names.
    """
    assert normalize_gaps(["No cost data."]) == [
        CritiqueGap(
            gap_id="gap-01",
            coverage_id=None,
            target_ids=["question"],
            problem="No cost data.",
            recommended_queries=[],
        )
    ]


def test_only_the_state_boundary_reads_the_legacy_gap_shape(monkeypatch) -> None:
    """One normalizer implementation, and exactly one boundary that calls it.

    Two verbatim copies would drift the moment the gap shape changes, so the
    rule lives in one function. The *boundaries* differ deliberately: the
    state-facing ``Critique`` calls it, so an old persisted artifact stays
    readable, while the provider-facing ``CritiqueDraft`` does not — a live
    reply that ignores the typed response contract is repaired rather than
    rewritten into a material whole-answer gap.
    """
    import deep_research.agents.critic as critic_module

    real = critic_module.normalize_gap_drafts
    recorded_payloads: list[dict] = []

    def recorded(values: object) -> object:
        assert isinstance(values, dict)
        recorded_payloads.append(values)
        return real(values)

    monkeypatch.setattr(critic_module, "normalize_gap_drafts", recorded)

    with pytest.raises(ValidationError):
        CritiqueDraft.model_validate(
            {
                "score": 4,
                "gaps": ["No cost data."],
                "unsupported_claims": [],
                "recommended_queries": [],
                "rationale": "Thin sourcing.",
            }
        )
    critique = Critique.model_validate(
        {
            "score": 4,
            "gaps": ["No cost data."],
            "unsupported_claims": [],
            "recommended_queries": [],
            "should_continue": True,
            "rationale": "Thin sourcing.",
        }
    )

    # Only the state boundary handed the legacy list to the shared function.
    assert [payload["gaps"] for payload in recorded_payloads] == [
        ["No cost data."]
    ]
    assert critique.gaps == [
        CritiqueGap(
            gap_id="gap-01",
            coverage_id=None,
            target_ids=["question"],
            problem="No cost data.",
            recommended_queries=[],
        )
    ]


def test_title_and_problem_text_never_decide_a_gaps_target() -> None:
    """Only ids decide a target, and prose decides nothing at all.

    The Task 7 invariant, kept: a gap whose *problem text* names a topic
    targets nothing, and a legacy string gap targets the whole answer. An id
    that does not resolve is refused (the sibling test below), so neither the
    plan a model meant nor the title it wrote can become a target.
    """
    gaps = normalize_gaps(
        [
            # The legacy free-text shape, which predates ids entirely.
            "The report misses Beta evidence.",
        ],
        known_coverage_ids={"topic-01", "topic-02"},
    )

    assert [gap.coverage_id for gap in gaps] == [None]
    assert [gap.target_ids for gap in gaps] == [["question"]]
    assert [gap.problem for gap in gaps] == ["The report misses Beta evidence."]

    # Problem text naming a planned topic targets nothing, and with no id at
    # all the gap is refused rather than scoped by what it mentions.
    with pytest.raises(CritiqueContractViolation, match="do not resolve"):
        normalize_gaps(
            [
                CritiqueGapDraft(
                    coverage_id="topic-999",
                    kind="coverage",
                    severity="major",
                    repair_action="acquire",
                    problem=(
                        "Alpha appears only as a title in this problem, and "
                        "Beta likewise."
                    ),
                    recommended_queries=["alpha evidence"],
                ),
            ],
            known_coverage_ids={"topic-01", "topic-02"},
        )


def test_a_blank_scope_is_refused_like_any_other_unresolved_one() -> None:
    """A blank id names nothing, so it is refused like an unknown one.

    Narrowed deliberately from Task 7, where both became a global gap. The typed
    contract added the scope requirement, and a whitespace placeholder satisfies
    no part of it: refusing it costs one repair and buys a gap whose scope is
    real. A blank *list* entry is the same defect through a sibling field, and
    is refused for the same reason — ``target_ids=[""]`` used to satisfy "must
    name what it affects" and was then dropped downstream.
    """
    with pytest.raises(ValidationError, match="affects"):
        CritiqueGapDraft(
            coverage_id="   ",
            kind="coverage",
            severity="major",
            repair_action="acquire",
            problem="The report misses Beta evidence.",
            recommended_queries=["beta evidence"],
        )
    with pytest.raises(ValidationError, match="affects"):
        CritiqueGapDraft(
            target_ids=[""],
            kind="coverage",
            severity="major",
            repair_action="acquire",
            problem="The report is not good enough.",
            recommended_queries=[],
        )
    with pytest.raises(ValidationError, match="affects"):
        CritiqueGapDraft(
            statement_ids=["  "],
            claim_cluster_ids=[""],
            kind="coverage",
            severity="major",
            repair_action="acquire",
            problem="The report is not good enough.",
            recommended_queries=[],
        )


def test_a_vague_provider_gap_is_refused_not_scoped() -> None:
    """The important finding: "not good enough" is not an actionable defect.

    The reviewer's probe P2: an unscoped typed gap was silently recorded as a
    material whole-answer obligation because ``normalize_gaps`` pre-filled the
    fallback before the validator ever saw it. A gap that declared no scope at
    all now reaches the contract unchanged and is refused, which is the
    agent's cue to repair the reply; only a scope that was *declared* and did
    not resolve falls back to the whole answer.
    """
    vague = {
        "kind": "coverage",
        "severity": "major",
        "repair_action": "acquire",
        "problem": "The report is not good enough.",
        "recommended_queries": [],
        "target_ids": [],
        "statement_ids": [],
        "claim_cluster_ids": [],
    }

    with pytest.raises(ValidationError, match="affects"):
        CritiqueGapDraft.model_validate(vague)
    with pytest.raises(ValidationError, match="affects"):
        CritiqueDraft.model_validate(
            {
                "score": 5,
                "gaps": [vague],
                "unsupported_claims": [],
                "recommended_queries": [],
                "rationale": "Thin.",
            }
        )

    # A gap that never went through the schema is refused by the contract seam
    # with a typed error instead of a bare ValueError.
    unvalidated = CritiqueGapDraft.model_construct(
        gap_id="gap-01",
        coverage_id=None,
        target_ids=[],
        claim_cluster_ids=[],
        statement_ids=[],
        kind="coverage",
        severity="major",
        repair_action="acquire",
        problem="The report is not good enough.",
        recommended_queries=[],
    )
    draft = CritiqueDraft.model_construct(
        score=5,
        gaps=[unvalidated],
        unsupported_claims=[],
        recommended_queries=[],
        rationale="Thin.",
    )
    with pytest.raises(CritiqueContractViolation) as caught:
        build_critique(draft, iteration=0, max_iterations=3)
    assert caught.value.field_paths() == ("gaps.0",)
    assert "affects" in caught.value.problem


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
    assert [gap.problem for gap in critique.gaps] == ["No cost data."]
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
    assert [gap.problem for gap in critique.gaps] == [
        "No report was available to review."
    ]

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
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "# Research question" in body
    assert "# Reader content" in body
    assert "# Research report:" in body
    assert "# Sub-topics planned" in body
    # ``CRITIQUE_INSTRUCTION`` tells the model to copy an id exactly from the
    # statement, target, or cluster lists, so the planned sub-topic's id has to
    # appear next to its title or every gap it writes is nulled locally. The
    # exact line pins both values and their order.
    assert "- topic-01: Alpha" in body
    assert "# Reader statements" in body
    assert "# Evidence targets" in body
    assert "# Claim verdicts" in body
    assert "[verified 0.80]" in body
    assert "# Source quality" in body
    assert "# Recorded problems" in body
    assert "2 error(s)" in body
    assert "# Evidence — batched read excerpts" in body
    assert "# Hard checks" in body
    assert "# Response contract" in body


def test_critique_messages_keep_each_reader_section_and_typed_quality_context() -> None:
    report = "\n\n".join(
        [
            "# Research report: balanced input",
            "## Executive summary\nSUMMARY-MARKER",
            "## Constraint ranking\nCONSTRAINT-MARKER",
            "## Findings\nFINDINGS-MARKER",
            "## Uncertainty and conflicting evidence\nUNCERTAINTY-MARKER",
            "## Methodology\nMETHODOLOGY-MARKER",
            "## References\nREFERENCES-MARKER",
        ]
    )
    quality = ReportQualitySnapshot(
        coverage_ratio=0.8,
        planned_topics=5,
        covered_topics=4,
        unresolved_topic_ids=["topic-05"],
        unique_findings=6,
        unique_sources=4,
        cited_sources=3,
        scored_cited_source_ratio=1.0,
        verified_claims=4,
        contradicted_claims=1,
        duplicate_claims=0,
        duplicate_source_rows=0,
        uncited_settled_points=0,
        hard_failures=["broad_plan_coverage_below_0.80"],
    )
    task = _task(
        report=report,
        quality=quality,
        errors=[
            ResearchError(
                error_type="researcher_sub_topic_skipped",
                source="agent.researcher",
                message="A planned sub-topic was skipped.",
                details={"coverage_id": "topic-05"},
            )
        ],
    )

    body = critique_messages(
        task,
        ReActRun(agent_name="critic", stop_reason="finished"),
    )[1].content

    for marker in (
        "SUMMARY-MARKER",
        "CONSTRAINT-MARKER",
        "FINDINGS-MARKER",
        "UNCERTAINTY-MARKER",
        "METHODOLOGY-MARKER",
        "REFERENCES-MARKER",
    ):
        assert marker in body
    assert '"coverage_ratio": 0.8' in body
    assert "agent.researcher" in body
    assert "researcher_sub_topic_skipped" in body


def test_a_long_reader_section_is_carried_whole() -> None:
    """No section cap may cut the end off the candidate.

    The reviewer's probe: a ~1,100-word single ``## Findings`` section sits
    well inside the report bound and still lost its last contradiction to the
    old 6,000-character per-section cap. The request carries every section
    whole and says how large a large one is, so nothing is silently dropped.
    """
    tail = "The final contradiction is that NOAA reports the opposite."
    long_section = ("x" * 7000) + "\n" + tail
    report = (
        "# Research report: a long one\n\n## Findings\n\n" + long_section
    )
    assert len(long_section) > CRITIC_REPORT_CHARS
    body = critique_messages(
        _task(report=report),
        ReActRun(agent_name="critic", stop_reason="finished"),
    )[1].content

    assert "# Research report: a long one" in body
    assert long_section in body
    assert tail in body
    # The section is called out as large rather than cut.
    assert f"({len(long_section)} characters, carried whole)" in body


def test_critique_messages_say_so_when_there_is_no_report() -> None:
    body = critique_messages(
        _task(report="   "),
        ReActRun(agent_name="critic", stop_reason="finished"),
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
                coverage_id="topic-01",
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
    assert [(topic.coverage_id, topic.title) for topic in task.sub_topics] == [
        ("topic-01", "Alpha")
    ]
    assert len(task.claims) == 1
    assert len(task.sources) == 1
    assert task.error_count == 0


@pytest.mark.asyncio
async def test_the_review_request_carries_the_planned_topics_and_targets(
    tracker: Tracker,
) -> None:
    """The review is where a gap learns which ids it may name.

    ``CRITIQUE_INSTRUCTION`` requires every gap to name the target or
    statement it affects, so the request has to render both inventories: the
    planned sub-topics with their coverage ids and the evidence targets with
    their required dimensions.
    """
    completer = ScriptedCompleter(outputs=[_draft(score=9)])
    agent = _critic(tracker, completer, tool_budget=1)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.react.stop_reason == "finished"
    body = completer.calls[0][2][1].content
    assert "- topic-01: Alpha" in body
    assert _PACKET_TARGET_ID in body
    assert "target-01" in body
    # Every statement id the packet review may name is printed beside its text.
    assert "S001" in body
    assert "Alpha lacks cost evidence" not in body


@pytest.mark.asyncio
async def test_a_zero_critic_budget_leaves_no_tool_path_at_all(
    tracker: Tracker,
) -> None:
    """``critic: 0`` is the shipped value, and the gate in front of the loop
    is gone rather than merely closed.

    A gate that still read the global ``tool_budget`` would run a whole ReAct
    loop whenever the override was missing; Task 8 removed the loop, so the
    configuration and the code agree by construction.
    """
    completer = ScriptedCompleter(outputs=[_draft(score=9)])
    agent = _critic(
        tracker,
        completer,
        config=AgentRuntimeConfig(
            max_iterations=2,
            tool_budget=2,
            tool_budget_overrides={"critic": 0},
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert completer.react_calls == []
    assert outcome.result is not None
    assert outcome.result.score == 9
    assert outcome.react.tool_calls == 0


@pytest.mark.asyncio
async def test_a_generous_tool_budget_still_runs_no_spot_check(
    tracker: Tracker,
) -> None:
    """The Critic is tool-free whatever its budget says.

    The historical Critic spent ten search/memory calls per pass and could
    open no page, so its searches could not establish missing support. A
    budget override of four no longer buys four calls, and the injected tools
    are never offered to the provider.
    """
    completer = ScriptedCompleter(outputs=[_draft(score=9)])
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(tracker),
        config=AgentRuntimeConfig(
            max_iterations=4,
            tool_budget=4,
            tool_budget_overrides={"critic": 4},
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert completer.react_calls == []
    assert outcome.react.tool_calls == 0
    assert outcome.react.stop_reason == "finished"
    assert agent.toolset.names == ()


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
    assert [gap.problem for gap in critique.gaps] == ["No cost data."]
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
    assert [gap.problem for gap in critique.gaps] == ["No cost data."]
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
async def test_an_http_provider_failure_still_routes_and_stops(
    tracker: Tracker,
) -> None:
    """An outage is not an output limit: it is never re-asked.

    The retry rule is specific to a truncated reply, which is the one failure
    a different request can fix. A provider that is down says nothing about
    the request, so this path keeps its single attempt, its failed review, and
    the provider snapshot on the record.
    """
    completer = ScriptedCompleter(
        outputs=[
            ProviderResponseError(
                "provider returned an HTTP error",
                retryable=True,
                failure_category="http",
                http_status_code=503,
                failure_origin="sdk",
            )
        ]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"]
    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.react.stop_reason == "provider_error"
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_provider_error"
    )
    assert error.recoverable is False
    assert error.details["operation"] == "critic_report_review"
    provider = error.details["provider_failure"]
    assert provider["kind"] == "provider_http"
    assert provider["http_status_code"] == 503


@pytest.mark.asyncio
async def test_a_request_rejected_for_size_fails_closed(
    tracker: Tracker,
) -> None:
    """The unbounded request's fallback is the provider-error path, not a cut.

    Fix round 4 removed every bound on the review material — the passage cut,
    the evidence-unit ceiling, the claim count and length slices, and the source
    slice — so a long enough run can produce a request a provider refuses for
    size. What must not happen is a quiet degradation into a partial view: the
    refusal is a provider error, so the review is recorded ``failed``, the run
    does not continue, and the graph turns that into ``critique_failed`` →
    status ``failed`` → quality ``partial`` → no memory write → CLI exit 3,
    which `test_a_real_critic_provider_outage_fails_closed_through_cli` pins end
    to end (``graph_route`` reads ``review_status``, not the failure category).
    """
    provider = ScriptedCompleter(
        outputs=[
            ProviderResponseError(
                "request exceeds the model's context window",
                failure_category="http",
                http_status_code=413,
                failure_origin="sdk",
            )
        ]
    )
    agent = _critic(tracker, provider)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in provider.calls] == ["CritiqueDraft"]
    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.should_continue is False
    assert outcome.result.gaps == []
    assert outcome.react.stop_reason == "provider_error"
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_provider_error"
    ]


@pytest.mark.asyncio
async def test_a_truncated_review_is_re_asked_once_at_a_high_effort(
    tracker: Tracker,
) -> None:
    """The output-limit rule's critic half: one retry, then the review stands.

    A truncated reply is the one failure that a *different* request can fix:
    the same packet under the same output budget, with the effort that leaves
    more of that budget for the answer. The run must proceed on the retry's
    review, and the retry must be recorded — the effort used, the budget it
    kept, and the outcome — because otherwise a paid second call is invisible.
    """
    completer = ScriptedCompleter(outputs=[_output_limit_error(), _draft(score=8)])
    agent = _critic(tracker, completer)
    cap = agent.config.critic_review_max_tokens

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"] * 2
    assert completer.budgets == [cap, cap]
    assert completer.efforts == [None, "high"]
    critique = outcome.result
    assert critique is not None
    assert critique.review_status == "reviewed"
    assert critique.score == 8
    assert outcome.react.stop_reason == "finished"
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_output_limit_retry"
    ]
    retry = outcome.errors[0]
    assert retry.recoverable is True
    assert retry.error_type not in HALTING_ERROR_TYPES


@pytest.mark.asyncio
async def test_two_truncations_leave_the_report_unjudged_without_ending_the_run(
    tracker: Tracker,
) -> None:
    """Two truncations are not worth a run: they are worth a record.

    Nothing about the report changes between two truncated calls, so a third
    would truncate too, and the run's only truthful options are "no critique
    exists" and "say so". It must not end: the report is complete and
    publishable, and the graph has a state for exactly this — no critique, no
    acceptance, and the reason on the record. Other provider failures keep
    their own path; only the output limit degrades.
    """
    completer = ScriptedCompleter(
        outputs=[_output_limit_error(), _output_limit_error()]
    )
    agent = _critic(tracker, completer)
    cap = agent.config.critic_review_max_tokens

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert completer.budgets == [cap, cap]
    assert completer.efforts == [None, "high"]
    # No critique was made, so the state carries none: an unavailable review
    # must not arrive as a floor score with no gaps, which is what an accepted
    # one looks like. The key is written with ``None`` rather than omitted,
    # because an omitted key would leave an *earlier* pass's judgement in place
    # and the router reads that judgement.
    assert outcome.result is None
    assert outcome.state_update["critique"] is None
    assert outcome.react.stop_reason == "provider_error"
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_output_limit_retry",
        "critic_review_unavailable",
    ]
    assert all(error.recoverable for error in outcome.errors)
    assert not any(
        error.error_type in HALTING_ERROR_TYPES for error in outcome.errors
    )
    retry = outcome.errors[0]
    assert retry.details["outcome"] == "truncated"
    assert retry.details["reasoning_effort"] == "high"
    assert retry.details["max_tokens"] == cap


@pytest.mark.asyncio
async def test_a_repaired_truncation_retry_keeps_the_repair_allowance(
    tracker: Tracker,
) -> None:
    """A malformed reply *after* the retry still gets its one repair.

    The retry is one more review attempt, not a replacement for the repair
    flow: an unparseable answer is the failure the repair exists for, whatever
    attempt produced it. The retry is still recorded — it happened, and its own
    outcome is that the reply it returned was not usable.
    """
    completer = ScriptedCompleter(
        outputs=[_output_limit_error(), {"not": "a draft"}, _draft(score=7)]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"] * 3
    assert completer.efforts == [None, "high", None]
    critique = outcome.result
    assert critique is not None
    assert critique.score == 7
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_output_limit_retry",
        "critic_review_repaired",
    ]
    assert outcome.errors[0].details["outcome"] == "answered"


@pytest.mark.asyncio
async def test_a_truncated_repair_is_re_asked_once_at_a_high_effort(
    tracker: Tracker,
) -> None:
    """The output-limit rule one call deeper: the repair gets the same retry.

    The repair request is the longest review request the run makes — it carries
    the packet *and* the findings — so a truncation there is at least as likely
    as the one that ended the live run. It is re-asked once under the same
    output budget at the higher-answer effort, and the review then stands.
    """
    completer = ScriptedCompleter(
        outputs=[_schema_error("missing"), _output_limit_error(), _draft(score=6)]
    )
    agent = _critic(tracker, completer)
    cap = agent.config.critic_review_max_tokens

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"] * 3
    assert completer.budgets == [cap, cap, cap]
    assert completer.efforts == [None, None, "high"]
    critique = outcome.result
    assert critique is not None
    assert critique.review_status == "reviewed"
    assert critique.score == 6
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_output_limit_retry",
        "critic_review_repaired",
    ]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_twice_truncated_repair_leaves_the_report_unjudged(
    tracker: Tracker,
) -> None:
    """A repair that truncates twice degrades exactly as the review does.

    No critique exists after it, so the run publishes its report unscored
    rather than ending: the same absence, the same records, and the same
    refusal to accept an unreviewed report.
    """
    completer = ScriptedCompleter(
        outputs=[
            _schema_error("missing"),
            _output_limit_error(),
            _output_limit_error(),
        ]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"] * 3
    assert completer.efforts == [None, None, "high"]
    assert outcome.result is None
    assert outcome.state_update["critique"] is None
    assert outcome.react.stop_reason == "provider_error"
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_output_limit_retry",
        "critic_review_unavailable",
    ]
    assert all(error.recoverable for error in outcome.errors)
    assert outcome.errors[0].details["outcome"] == "truncated"


@pytest.mark.asyncio
async def test_a_truncated_review_repair_is_re_asked_before_it_degrades(
    tracker: Tracker,
) -> None:
    """A truncated review whose retry is malformed still gets its repair.

    The two allowances are independent: the output-limit retry is one more
    attempt at the reply, and a reply the contract refuses is what the repair
    exists for. Four calls, in that order, and the review still stands.
    """
    completer = ScriptedCompleter(
        outputs=[
            _output_limit_error(),
            {"not": "a draft"},
            _output_limit_error(),
            _draft(score=7),
        ]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"] * 4
    assert completer.efforts == [None, "high", None, "high"]
    critique = outcome.result
    assert critique is not None
    assert critique.score == 7
    assert [error.error_type for error in outcome.errors] == [
        "critic_review_output_limit_retry",
        "critic_review_output_limit_retry",
        "critic_review_repaired",
    ]
    assert [error.details["outcome"] for error in outcome.errors[:2]] == [
        "answered",
        "answered",
    ]


@pytest.mark.asyncio
async def test_a_search_snippet_can_never_appear_as_verification_evidence(
    tracker: Tracker,
) -> None:
    """Only a registered read excerpt is evidence; a search hit never is.

    Section 2.1: search results and snippets are not read-bearing evidence.
    The historical Critic could only search, so its snippets were offered to
    the review as if they were checks. The tools are still injected here and
    the search client still returns a snippet, and the snippet must reach
    neither the review request nor the packet.
    """
    sentinel = "SEARCH-SNIPPET-SENTINEL"
    completer = ScriptedCompleter(outputs=[_draft(score=8)])
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(
            tracker,
            search=FakeSearchClient(
                responses=[
                    [
                        {
                            "title": "A snippet",
                            "url": CRITIC_SOURCE_URL,
                            "content": sentinel,
                        }
                    ]
                ]
            ),
        ),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    body = completer.calls[0][2][1].content
    assert sentinel not in body
    assert sentinel not in json.dumps(
        outcome.state_update["critique"].model_dump(mode="json")
    )
    # The packet's evidence is the registered read excerpt, by id.
    assert _PACKET_EVIDENCE_ID in body
    assert "Break-even was reached in 2025." in body
    assert outcome.react.tool_calls == 0


@pytest.mark.asyncio
async def test_a_failing_tool_cannot_stop_a_review_that_never_calls_one(
    tracker: Tracker,
) -> None:
    """A provider outage on the tool side is unreachable from this path."""
    completer = ScriptedCompleter(outputs=[_draft(score=8)])
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
    assert outcome.react.tool_calls == 0


@pytest.mark.asyncio
async def test_finalize_requires_a_critique_task(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(outputs=[_draft()]))

    with pytest.raises(AgentConfigurationError, match="CritiqueTask"):
        await agent.finalize(
            AgentTask(instruction="anything"),
            ReActRun(agent_name="critic", stop_reason="finished"),
        )


def test_the_critic_declares_no_tools(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(), tools=critic_tools(tracker))

    assert CriticAgent.name == "critic"
    assert CriticAgent.allowed_tools == ()
    assert agent.toolset.names == ()
    assert agent.output_schema is Critique


# --- Task 8: the complete packet, typed gaps, and typed repair actions -------

QUESTION = "How mature is quantum error correction?"
_PACKET_CLAIM = "Logical error rates fell below break-even in 2025."
_PACKET_CLUSTER_ID = "cluster-01"
_PACKET_EVIDENCE_ID = "ev-01"
_PACKET_TARGET_ID = "target-01"
_PACKET_REPORT = (
    "# Research report: How mature is quantum error correction?\n\n"
    "## Summary\n\nLogical error rates fell below break-even in 2025. [1]"
)


def _packet_evidence(
    evidence_id: str = _PACKET_EVIDENCE_ID,
    *,
    excerpt: str = "Break-even was reached in 2025.",
    target_id: str = _PACKET_TARGET_ID,
) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=evidence_id,
        read_id="read-1",
        source_url=CRITIC_SOURCE_URL,
        source_title="QEC 2025",
        locator=f"section-{evidence_id}",
        excerpt=excerpt,
        target_ids=[target_id],
        origin="researcher",
    )


def test_a_critic_target_is_open_exactly_when_it_is_not_answered() -> None:
    """``open`` is the negation of the gate's verdict, not a second rule."""


def test_the_critic_target_view_is_the_coverage_gate_view() -> None:
    """The packet's ``answered`` is the gate's answer, target for target.

    The pooled view this replaced could close a target the deterministic gate
    calls open — a ``context`` statement, or one failing the support policy,
    covering the last required dimension — so the Critic was told an obligation
    was met that the run would go on to report as open.
    """
    state = _packet_state()
    packet = build_critic_packet(state, state.composition)

    assert packet.statements, "the packet must carry the statements it judges"
    assert packet.targets
    planned = [
        target
        for topic in state.sub_topics
        for target in topic.evidence_targets
    ]
    answered = [target_is_answered(state, target) for target in planned]

    assert [reported.answered for reported in packet.targets] == answered
    assert [reported.open for reported in packet.targets] == [
        not value for value in answered
    ]
    # And the property itself, on the model the packet just handed out.
    sample = packet.targets[0]
    assert sample.model_copy(update={"answered": True}).open is False
    assert sample.model_copy(update={"answered": False}).open is True


def _packet_topic(
    *, required_dimensions: Sequence[str] | None = None
) -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="Alpha is load-bearing.",
        search_queries=["alpha 2025"],
        success_criteria=["A named source about Alpha."],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id=_PACKET_TARGET_ID,
                coverage_id="topic-01",
                question="What is the measured logical error rate?",
                required_dimensions=list(required_dimensions or ["attribution"]),
                required=True,
                critical=True,
                support_policy="independent_pair",
            )
        ],
    )


def _packet_composition(
    *,
    evidence: Sequence[EvidenceUnit] | None = None,
    target_dimensions: Sequence[str] | None = None,
) -> ReportComposition:
    units = list(evidence) if evidence is not None else [_packet_evidence()]
    claim = _claim()
    cluster = ClaimCluster(
        cluster_id=_PACKET_CLUSTER_ID,
        proposition=AtomicProposition(text=_PACKET_CLAIM, attribution="QEC 2025"),
        evidence_ids=[unit.evidence_id for unit in units],
        member_claim_ids=[claim.claim_id],
        target_ids=[_PACKET_TARGET_ID],
        source_urls=[CRITIC_SOURCE_URL],
        verdicts=["verified"],
        verdict_evidence_status={"verified": "verified_pair"},
    )
    return ReportComposition(
        question=QUESTION,
        session_id="session-1",
        sub_topics=[_packet_topic(required_dimensions=target_dimensions)],
        claims=[claim],
        sources=[_source()],
        claim_clusters={cluster.cluster_id: cluster},
        evidence_units={unit.evidence_id: unit for unit in units},
        summary=[
            ReportPoint(
                text=_PACKET_CLAIM,
                claim_ids=[claim.claim_id],
                source_urls=[CRITIC_SOURCE_URL],
            )
        ],
        sections=[
            ReportSection(
                title="Findings",
                points=[
                    ReportPoint(
                        text="Two mechanisms dominate the measured effect.",
                        claim_ids=[claim.claim_id],
                        source_urls=[CRITIC_SOURCE_URL],
                    )
                ],
            )
        ],
    )


def _packet_state(
    *,
    report: str = _PACKET_REPORT,
    evidence: Sequence[EvidenceUnit] | None = None,
    target_dimensions: Sequence[str] | None = None,
    quality: ReportQualitySnapshot | None = None,
    with_composition: bool = True,
    claims: Sequence[Claim] | None = None,
    sources: Sequence[ScoredSource] | None = None,
) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": QUESTION,
        "sub_topics": [_packet_topic(required_dimensions=target_dimensions)],
        "evaluated_sources": list(sources) if sources is not None else [_source()],
        "verified_claims": list(claims) if claims is not None else [_claim()],
        "report": report,
    }
    if with_composition:
        payload["composition"] = _packet_composition(
            evidence=evidence, target_dimensions=target_dimensions
        )
    if quality is not None:
        payload["quality"] = quality
    return ResearchState.model_validate(payload)


def _schema_error(*categories: str) -> StructuredOutputError:
    return StructuredOutputError(
        "structured output remained invalid after one repair request",
        diagnostics=[
            StructuredValidationDiagnostic(
                attempt=1,
                field_paths=("gaps.0.severity",),
                category=category,
            )
            for category in (categories or ("other_schema",))
        ],
    )


def _typed_draft(
    *,
    score: int = 8,
    gaps: list[CritiqueGapDraft] | None = None,
    rationale: str = "Well sourced and complete.",
) -> CritiqueDraft:
    return CritiqueDraft(
        score=score,
        gaps=gaps or [],
        unsupported_claims=[],
        recommended_queries=[],
        rationale=rationale,
    )


def test_presentation_gap_does_not_request_search():
    gap = CritiqueGap(
        gap_id="g1", target_ids=["t1"], claim_cluster_ids=[],
        statement_ids=["s1"], kind="presentation", severity="major",
        repair_action="synthesize", problem="The answer is repeated in three lists.",
        recommended_queries=[])
    assert gap.repair_action == "synthesize"
    assert not gap.recommended_queries


def test_a_major_gap_must_name_what_it_affects() -> None:
    """A major defect names a target, a statement, or a cluster.

    “Improve quality” and a vague “more sources” are not actionable, and Task
    9 routes by the ids a gap carries. A minor gap is exempt: it is allowed to
    be a wording-level observation with no owner.
    """
    with pytest.raises(ValidationError, match="affects"):
        CritiqueGap(
            gap_id="g1",
            kind="coverage",
            severity="major",
            repair_action="adjudicate",
            problem="The report is not good enough.",
        )

    minor = CritiqueGap(
        gap_id="g2",
        kind="presentation",
        severity="minor",
        repair_action="synthesize",
        problem="The same figure is restated in three consecutive bullets.",
    )
    assert minor.severity == "minor"
    assert minor.claim_cluster_ids == []


def test_an_acquire_gap_must_name_the_target_whose_obligation_is_missing() -> None:
    """Acquisition is per obligation, so the gap has to name the obligation.

    A statement-only reference says which sentence is thin; it does not say
    what evidence is owed, and ``acquire`` is the action that goes and gets
    it. The whole-answer sentinel is a legitimate name here — that is how an
    original-question omission is expressed.
    """
    with pytest.raises(ValidationError, match="acquire"):
        CritiqueGap(
            gap_id="g1",
            statement_ids=["S001"],
            kind="missing_support",
            severity="major",
            repair_action="acquire",
            problem="This sentence is not backed by any read.",
            recommended_queries=["quantum error correction break-even"],
        )

    obligation = CritiqueGap(
        gap_id="g2",
        target_ids=["target-01"],
        kind="missing_support",
        severity="major",
        repair_action="acquire",
        problem="The measured logical error rate is owed but unsupported.",
        recommended_queries=["quantum error correction break-even"],
    )
    assert obligation.recommended_queries


def test_queries_belong_to_acquisition_gaps_only() -> None:
    """A rewrite, an adjudication, or a consolidation runs no search."""
    with pytest.raises(ValidationError, match="acquisition"):
        CritiqueGap(
            gap_id="g1",
            target_ids=["target-01"],
            kind="contradiction",
            severity="major",
            repair_action="adjudicate",
            problem="Two reads disagree about the rate.",
            recommended_queries=["quantum error correction rate 2025"],
        )

    with pytest.raises(ValidationError, match="acquisition"):
        CritiqueGap(
            gap_id="g2",
            target_ids=["question"],
            kind="coverage",
            severity="major",
            repair_action="extend_plan",
            problem="The question asks for a cost the plan never targeted.",
            recommended_queries=["low-carbon cement cost premium"],
        )


def test_gaps_differing_only_in_action_are_two_defects() -> None:
    """The dedupe identity carries the action, because the action is the route.

    Two gaps with the same scope and the same words that repair differently
    are two nodes' work. Collapsing them kept only the more severe reading,
    which silently adopted one node's repair for the other's defect.
    """
    shared = {
        "coverage_id": "topic-01",
        "target_ids": ["target-01"],
        "problem": "The cost obligation is not met.",
    }
    gaps = normalize_gaps(
        [
            CritiqueGapDraft(
                **shared,
                kind="coverage",
                severity="major",
                repair_action="acquire",
                recommended_queries=["cement cost 2026"],
            ),
            CritiqueGapDraft(
                **shared,
                kind="missing_support",
                severity="critical",
                repair_action="adjudicate",
            ),
        ],
        known_coverage_ids=["topic-01"],
    )

    assert [gap.repair_action for gap in gaps] == ["acquire", "adjudicate"]
    assert [gap.kind for gap in gaps] == ["coverage", "missing_support"]


def test_the_same_defect_restated_with_a_different_label_is_still_one() -> None:
    """One action and one scope: the more severe, better-queried reading wins."""
    shared = {
        "coverage_id": "topic-01",
        "target_ids": ["target-01"],
        "kind": "coverage",
        "repair_action": "acquire",
        "problem": "The cost obligation is not met.",
    }
    gaps = normalize_gaps(
        [
            CritiqueGapDraft(**shared, severity="minor"),
            CritiqueGapDraft(
                **shared,
                severity="critical",
                recommended_queries=["cement cost 2026"],
            ),
        ],
        known_coverage_ids=["topic-01"],
    )

    assert len(gaps) == 1
    assert gaps[0].severity == "critical"
    assert gaps[0].recommended_queries == ["cement cost 2026"]


def test_the_repair_actions_are_exactly_the_router_keys() -> None:
    """``REPAIR_NODES`` keys are the literals, and the graph routes on them.

    The route table itself lives in ``graph.nodes`` — one table, keyed by the
    contract — so the key equality is asserted against the table the router
    actually reads.
    """
    assert REPAIR_ACTIONS == (
        "extend_plan",
        "acquire",
        "assess_source",
        "adjudicate",
        "consolidate",
        "synthesize",
    )
    assert tuple(REPAIR_NODES) == REPAIR_ACTIONS
    for action in REPAIR_ACTIONS:
        gap = CritiqueGap(
            gap_id=f"g-{action}",
            target_ids=["target-01"],
            kind="coverage",
            severity="major",
            repair_action=action,
            problem="One obligation is unmet.",
        )
        assert gap.repair_action == action

    with pytest.raises(ValidationError):
        CritiqueGap(
            gap_id="g-unknown",
            target_ids=["target-01"],
            kind="coverage",
            severity="major",
            repair_action="search_more",
            problem="One obligation is unmet.",
        )


def test_a_minor_gap_does_not_force_another_research_pass() -> None:
    """An otherwise sound answer with one minor defect is accepted.

    The historical rule made every listed gap a reason to research again, so a
    wording-level observation bought a whole pass. Severity now decides
    materiality, while a legacy gap — which carries no severity at all — still
    counts, because the pre-Task-8 contract only asked for material gaps.
    """
    minor = CritiqueGap(
        gap_id="g1",
        statement_ids=["S001"],
        kind="presentation",
        severity="minor",
        repair_action="synthesize",
        problem="The same figure is restated in three consecutive bullets.",
    )
    assert route_decision(
        score=8,
        gaps=[minor],
        unsupported_claims=[],
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (False, "accepted_quality")

    major = minor.model_copy(update={"severity": "major"})
    assert route_decision(
        score=8,
        gaps=[major],
        unsupported_claims=[],
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (True, "critical_gaps")

    assert route_decision(
        score=8,
        gaps=["No cost data."],
        unsupported_claims=[],
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (True, "critical_gaps")


def test_the_packet_carries_the_full_reader_content_and_every_statement() -> None:
    state = _packet_state()

    packet = build_critic_packet(state, state.composition)

    assert packet.question == QUESTION
    assert packet.reader_content == _PACKET_REPORT
    assert packet.answer_contract is None
    assert [statement.statement_id for statement in packet.statements] == [
        "S001",
        "F001",
    ]
    assert packet.statements[0].target_ids == [_PACKET_TARGET_ID]
    assert packet.statements[0].evidence_ids == [_PACKET_EVIDENCE_ID]
    assert [batch.items[0].evidence_id for batch in packet.evidence_batches] == [
        _PACKET_EVIDENCE_ID
    ]
    assert packet.evidence_batches[0].items[0].badge == "verified_pair"
    assert packet.evidence_batches[0].items[0].badge_label == (
        EVIDENCE_BADGE_LABELS["verified_pair"]
    )
    assert packet.omitted_evidence_ids == []
    assert [target.target_id for target in packet.targets] == [_PACKET_TARGET_ID]
    # The gate refuses this fixture's statement (it is the pooled view's
    # idea of answered, not the gate's), so the ids are now empty and the
    # target is open - the disagreement item 1 closes.
    assert packet.targets[0].answered is False
    assert packet.targets[0].open is True
    assert packet.targets[0].answered_dimension_ids == []
    # The pooled view closed this target (its statement carries the required
    # dimension in an answering mode); the gate refuses it because the fixture's
    # claim is not the independent pair its support policy requires. The packet
    # now carries the gate's reading, so the target is listed as open.
    assert packet.open_targets == [packet.targets[0]]
    # The fingerprint covers the exact text and ids one review was opened on.
    assert re.fullmatch(r"[0-9a-f]{12}", packet.fingerprint)
    assert build_critic_packet(state).fingerprint == packet.fingerprint


def test_a_target_with_an_unanswered_dimension_stays_open() -> None:
    state = _packet_state(target_dimensions=["attribution", "geography"])

    packet = build_critic_packet(state)

    assert packet.targets[0].required_dimensions == ["attribution", "geography"]
    # The gate refuses this fixture's statement (it is the pooled view's
    # idea of answered, not the gate's), so the ids are now empty and the
    # target is open - the disagreement item 1 closes.
    assert packet.targets[0].answered is False
    assert packet.targets[0].open is True
    assert packet.targets[0].answered_dimension_ids == []
    assert [target.target_id for target in packet.open_targets] == [
        _PACKET_TARGET_ID
    ]


def test_the_fingerprint_changes_with_the_report_and_with_the_evidence() -> None:
    packet = build_critic_packet(_packet_state())

    other_report = build_critic_packet(
        _packet_state(report=_PACKET_REPORT + "\nOne more sentence. [1]\n")
    )
    other_evidence = build_critic_packet(
        _packet_state(
            evidence=[
                _packet_evidence(excerpt="A different exact excerpt about 2025.")
            ]
        )
    )

    assert other_report.fingerprint != packet.fingerprint
    assert other_evidence.fingerprint != packet.fingerprint


def test_the_packet_reports_the_deterministic_hard_checks() -> None:
    quality = ReportQualitySnapshot(
        coverage_ratio=0.5,
        planned_topics=2,
        covered_topics=1,
        unresolved_topic_ids=["topic-02"],
        unique_findings=1,
        unique_sources=1,
        cited_sources=1,
        scored_cited_source_ratio=1.0,
        verified_claims=1,
        contradicted_claims=0,
        duplicate_claims=0,
        duplicate_source_rows=0,
        uncited_settled_points=0,
        hard_failures=["broad_plan_coverage_below_0.80"],
    )

    packet = build_critic_packet(_packet_state(quality=quality))

    assert "broad_plan_coverage_below_0.80" in packet.hard_checks


def test_a_gap_cannot_be_raised_against_prose_with_no_statement_record() -> None:
    """Where there is no statement record, that absence is itself the defect.

    A composition-less report cannot resolve a cited statement id, so the
    packet says so as a hard check and a gap that cites one is refused: it is a
    claim about a record nobody can look up. The absence is reported, not
    silently turned into an obligation about the whole answer.
    """
    state = _packet_state(with_composition=False)

    packet = build_critic_packet(state)

    assert packet.statements == []
    assert any("statement record" in check for check in packet.hard_checks)

    with pytest.raises(CritiqueContractViolation, match="do not resolve"):
        build_critique(
            _typed_draft(
                score=6,
                gaps=[
                    CritiqueGapDraft(
                        coverage_id=None,
                        statement_ids=["S999"],
                        kind="contradiction",
                        severity="major",
                        repair_action="adjudicate",
                        problem="The report contradicts a source it cites.",
                    )
                ],
                rationale="One contradiction is unresolved.",
            ),
            iteration=0,
            max_iterations=3,
            packet=packet,
            known_coverage_ids={"topic-01"},
        )


def test_a_gap_may_name_the_record_ids_the_packet_carries() -> None:
    state = _packet_state()
    packet = build_critic_packet(state)

    critique, _ = build_critique(
        _typed_draft(
            score=5,
            gaps=[
                CritiqueGapDraft(
                    coverage_id="topic-01",
                    target_ids=[_PACKET_TARGET_ID],
                    statement_ids=["S001"],
                    claim_cluster_ids=[_PACKET_CLUSTER_ID],
                    kind="missing_support",
                    severity="major",
                    repair_action="acquire",
                    problem="The measured rate is attributed but never corroborated.",
                    recommended_queries=["quantum error correction rate 2026"],
                )
            ],
        ),
        iteration=0,
        max_iterations=3,
        packet=packet,
        known_coverage_ids={"topic-01"},
    )

    gap = critique.gaps[0]
    assert gap.target_ids == [_PACKET_TARGET_ID]
    assert gap.statement_ids == ["S001"]
    assert gap.claim_cluster_ids == [_PACKET_CLUSTER_ID]
    assert gap.repair_action == "acquire"
    assert critique.recommended_queries == ["quantum error correction rate 2026"]


@pytest.mark.asyncio
async def test_a_late_contradiction_limitation_and_citation_stay_visible(
    tracker: Tracker,
) -> None:
    """Nothing at the end of a long report may fall outside the review.

    The historical failure was a late contradiction, a fabricated limitation,
    and a citation near the report's end falling outside old prefix
    boundaries. All three markers sit beyond the old 6,000-character prefix
    here and must still reach the review request.
    """
    contradiction = "EPA reports a fall while NOAA reports a rise."
    limitation = "replication has not been attempted by anyone else"
    citation = "https://late.example/end-of-report-citation"
    report = (
        "# Research report: How mature is quantum error correction?\n\n"
        "## Findings\n\n"
        + ("Filler sentence about logical error rates and hardware. " * 110)
        + "\n\n## Uncertainty and conflicting evidence\n\n"
        + contradiction
        + "\n\n## Limitations\n\nThe headline figure rests on one vendor blog and "
        + limitation
        + f" ({citation}).\n"
    )
    assert len(report) > 6000
    assert report.index(contradiction) > 6000
    assert report.index(limitation) > 6000
    assert report.index(citation) > 6000
    completer = ScriptedCompleter(outputs=[_draft(score=6)])
    agent = _critic(tracker, completer)
    state = _packet_state(report=report)

    async with tracker.session_span("session-1", "question"):
        await agent.run(state)

    body = completer.calls[0][2][1].content
    assert contradiction in body
    assert limitation in body
    assert citation in body


@pytest.mark.asyncio
async def test_a_malformed_draft_is_repaired_against_the_same_fingerprint(
    tracker: Tracker,
) -> None:
    """The one repair re-asks the same model about the same packet.

    The repair request carries the fingerprint of the packet the review was
    opened on, so a repaired critique can never be a second review of
    different text wearing the first one's authority.
    """
    completer = ScriptedCompleter(outputs=[_schema_error("missing"), _draft(score=8)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    first = completer.calls[0][2][1].content
    second = completer.calls[1][2][1].content
    fingerprint = re.search(r"Packet fingerprint: ([0-9a-f]{12})", first)
    assert fingerprint is not None
    assert f"Packet fingerprint: {fingerprint.group(1)}" in second
    assert outcome.result is not None
    assert outcome.result.score == 8
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    assert repaired.recoverable is True
    assert repaired.details["attempts"] == 2
    assert repaired.details["schema_categories"] == ["missing"]
    assert repaired.details["schema_field_paths"] == ["gaps.0.severity"]


def test_a_changed_packet_is_refused_as_a_repair_target() -> None:
    reviewed = build_critic_packet(_packet_state())
    changed = build_critic_packet(
        _packet_state(report=_PACKET_REPORT + "\nA sentence added after review.\n")
    )
    assert changed.fingerprint != reviewed.fingerprint

    assert repair_target(reviewed, reviewed_fingerprint=reviewed.fingerprint) is (
        reviewed
    )
    with pytest.raises(CritiqueRepairRefused, match="changed"):
        repair_target(changed, reviewed_fingerprint=reviewed.fingerprint)


@pytest.mark.asyncio
async def test_an_exhausted_repair_returns_an_explicit_failed_review(
    tracker: Tracker,
) -> None:
    """Two malformed replies are a failed review, never a guessed score.

    Nothing about the report was judged, so no score is invented and no
    acceptance is recorded: the routing stops the run with an enumerated
    reason, the failure is non-recoverable, and the diagnostics that are
    recorded are bounded field paths and categories, never provider text.
    """
    completer = ScriptedCompleter(
        outputs=[_schema_error("missing"), _schema_error("type_mismatch")]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    critique = outcome.result
    assert critique is not None
    assert critique.review_status == "failed"
    assert critique.score == MIN_CRITIC_SCORE
    assert critique.score < ACCEPTANCE_SCORE
    assert critique.should_continue is False
    assert critique.gaps == []
    assert critique.rationale == ROUTING_REASONS["review_failed"]
    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    event = outcome.state_update["events"][-1]
    assert event.metadata["reason"] == "review_failed"
    assert event.metadata["should_continue"] is False
    error = next(
        item
        for item in outcome.errors
        if item.error_type == "critic_review_schema_error"
    )
    assert error.recoverable is False
    assert error.details["operation"] == "critic_report_review"
    assert error.details["attempts"] == 2
    assert error.details["schema_categories"] == ["missing", "type_mismatch"]
    assert error.details["schema_field_paths"] == ["gaps.0.severity"]
    # No provider payload, report text, or excerpt is retained.
    assert _PACKET_CLAIM not in json.dumps(error.details)
    assert _PACKET_REPORT not in json.dumps(error.details)
    assert outcome.react.stop_reason == "provider_error"


@pytest.mark.asyncio
async def test_a_repair_refusal_is_reported_as_a_failed_review(
    tracker: Tracker, monkeypatch
) -> None:
    """A refused repair is a failed review, not a silent retry."""
    import deep_research.agents.critic as critic_module

    def refuse(*args: object, **kwargs: object) -> CriticPacket:
        raise CritiqueRepairRefused(
            "the report/evidence packet changed between the review and its repair"
        )

    monkeypatch.setattr(critic_module, "repair_target", refuse)
    completer = ScriptedCompleter(outputs=[_schema_error(), _draft(score=9)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == ["CritiqueDraft"]
    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.should_continue is False
    assert outcome.state_update["events"][-1].metadata["reason"] == "review_failed"


@pytest.mark.asyncio
async def test_a_provider_failure_during_the_repair_is_not_a_schema_failure(
    tracker: Tracker,
) -> None:
    """The ledger must not blame the reply for an outage.

    The first reply was malformed and the repair never arrived, so the review
    failed for two distinct reasons. Both are recorded, and the provider
    failure is the one that says why the second attempt produced nothing.
    """
    completer = ScriptedCompleter(
        outputs=[
            _schema_error("missing"),
            ProviderResponseError(
                "provider returned an HTTP error",
                retryable=True,
                failure_category="http",
                http_status_code=503,
                failure_origin="sdk",
            ),
        ]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    kinds = {error.error_type for error in outcome.errors}
    assert kinds == {"critic_review_schema_error", "critic_review_provider_error"}
    schema = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_schema_error"
    )
    assert schema.details["attempts"] == 2
    provider = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_provider_error"
    )
    assert provider.details["provider_failure"]["http_status_code"] == 503


@pytest.mark.asyncio
async def test_a_provider_shaped_reply_outside_the_contract_is_repaired(
    tracker: Tracker,
) -> None:
    """Critical 1: a reply the schema accepts and the contract rejects.

    The reported gap shape is the one the *previous* prompt taught — every gap
    carrying ``recommended_queries`` — so this is the habit the model brings,
    not a hypothetical. It used to abort ``agent.run`` with an unhandled
    ``ValidationError``, bypassing both the repair and the failed review. It
    now takes the same route a malformed reply takes: one repair, against the
    same packet fingerprint.
    """
    violating = {
        "score": 6,
        "gaps": [
            {
                "gap_id": "gap-01",
                "target_ids": ["target-01"],
                "claim_cluster_ids": [],
                "statement_ids": [],
                "kind": "coverage",
                "severity": "major",
                "repair_action": "synthesize",
                "problem": "The cost section has to be rewritten.",
                "recommended_queries": ["cement cost premium"],
            }
        ],
        "unsupported_claims": [],
        "recommended_queries": ["cement cost premium"],
        "rationale": "The cost section is thin.",
    }
    completer = ScriptedCompleter(
        outputs=[lambda messages, schema: violating, _draft(score=8)]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.score == 8
    assert outcome.result.review_status == "reviewed"
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    assert repaired.details["schema_field_paths"]
    assert repaired.details["attempts"] == 2

    # The repair re-reads the same packet: same fingerprint, both requests.
    first = completer.calls[0][2][1].content
    second = completer.calls[1][2][1].content
    fingerprint = re.search(r"Packet fingerprint: ([0-9a-f]{12})", first)
    assert fingerprint is not None
    assert f"Packet fingerprint: {fingerprint.group(1)}" in second


@pytest.mark.asyncio
async def test_a_contract_violating_reply_exhausts_into_a_failed_review(
    tracker: Tracker,
) -> None:
    """Two replies outside the contract are a failed review, never a crash."""
    violating = {
        "score": 6,
        "gaps": [
            {
                "gap_id": "gap-01",
                "target_ids": [],
                "claim_cluster_ids": [],
                "statement_ids": ["S001"],
                "kind": "missing_support",
                "severity": "major",
                "repair_action": "acquire",
                "problem": "This sentence is unsupported.",
                "recommended_queries": ["qec break-even"],
            }
        ],
        "unsupported_claims": [],
        "recommended_queries": ["qec break-even"],
        "rationale": "One sentence is unsupported.",
    }
    completer = ScriptedCompleter(
        outputs=[lambda messages, schema: violating] * 2
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.score == MIN_CRITIC_SCORE
    assert outcome.result.should_continue is False
    assert outcome.result.gaps == []
    assert outcome.state_update["events"][-1].metadata["reason"] == "review_failed"
    error = next(
        item
        for item in outcome.errors
        if item.error_type == "critic_review_schema_error"
    )
    assert error.recoverable is False
    assert error.details["attempts"] == 2
    assert error.details["schema_field_paths"]
    # No provider text, report text, or excerpt is retained.
    assert _PACKET_CLAIM not in json.dumps(error.details)


@pytest.mark.asyncio
async def test_a_draft_that_bypassed_the_schema_is_repaired_too(
    tracker: Tracker,
) -> None:
    """The defensive seam: a draft built without validation is not fatal.

    A transport that hands back objects it never validated never reaches
    ``CritiqueGapDraft``'s validator, so the agent re-validates the reply in
    ``_complete_review`` before anything reads it; the contract check inside
    ``normalize_gaps`` remains as the second line. Either way the typed failure
    is repaired, never a bare ``ValueError`` from inside a model validator.
    """
    unvalidated = CritiqueGapDraft.model_construct(
        gap_id="gap-01",
        coverage_id=None,
        target_ids=[],
        claim_cluster_ids=[],
        statement_ids=[],
        kind="coverage",
        severity="major",
        repair_action="acquire",
        problem="The report is not good enough.",
        recommended_queries=[],
    )
    bypassing = CritiqueDraft.model_construct(
        score=5,
        gaps=[unvalidated],
        unsupported_claims=[],
        recommended_queries=[],
        rationale="Thin.",
    )
    completer = ScriptedCompleter(outputs=[bypassing, _draft(score=7)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.score == 7
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    assert repaired.details["schema_field_paths"] == ["gaps"]


@pytest.mark.asyncio
async def test_a_report_that_changes_before_the_repair_refuses_it(
    tracker: Tracker,
) -> None:
    """I3: the fingerprint guard is real, and needs no monkeypatch to fire.

    The packet the review was opened on travels with the task, so comparing
    *that* object against its own fingerprint could never fail. The repair now
    rebuilds the packet from the state the run is holding; a report edited
    between the two attempts produces a different fingerprint, the repair is
    refused before any second request, and the review fails explicitly rather
    than silently reviewing a different report.
    """
    state = _packet_state()
    opened = build_critic_packet(state).fingerprint

    def edit_the_report_then_fail(
        messages: object, schema: object
    ) -> StructuredOutputError:
        del messages, schema
        state.report = state.report + "\n\nA sentence added while reviewing.\n"
        return _schema_error()

    completer = ScriptedCompleter(outputs=[edit_the_report_then_fail, _draft(score=8)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    # The report really did change, so the guard had something to catch.
    assert build_critic_packet(state).fingerprint != opened
    # One request only: the repair was refused before it was sent.
    assert [call[0] for call in completer.calls] == ["CritiqueDraft"]
    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert {error.error_type for error in outcome.errors} == {
        "critic_review_schema_error",
        "critic_review_repair_refused",
    }
    refused = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repair_refused"
    )
    assert refused.recoverable is False
    # A local refusal is not a provider failure, so the run summary says so.
    assert outcome.react.stop_reason == "finished"


@pytest.mark.asyncio
async def test_a_blank_problem_is_repaired_and_never_deletes_the_gap(
    tracker: Tracker,
) -> None:
    """Critical 2: a scoped major gap with a blank problem.

    The gap's ids were valid, so the scope rule had nothing to say, and
    ``problem: "   "`` was stripped to ``""`` and dropped by the normalizer —
    a material defect that vanished, leaving a high score with an empty gap list
    and an acceptance. It is now a malformed reply: one repair, and the
    repaired review keeps its gap.
    """
    blank = {
        "score": 8,
        "gaps": [
            {
                "gap_id": "gap-01",
                "target_ids": [_PACKET_TARGET_ID],
                "claim_cluster_ids": [],
                "statement_ids": [],
                "kind": "coverage",
                "severity": "major",
                "repair_action": "acquire",
                "problem": "   ",
                "recommended_queries": ["qec break-even corroboration"],
            }
        ],
        "unsupported_claims": [],
        "recommended_queries": ["qec break-even corroboration"],
        "rationale": "One obligation is unsupported.",
    }
    completer = ScriptedCompleter(outputs=[lambda m, s: blank, _draft(score=6)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    assert repaired.details["schema_field_paths"] == ["gaps.problem"]


@pytest.mark.asyncio
async def test_a_blank_problem_repeated_exhausts_into_a_failed_review(
    tracker: Tracker,
) -> None:
    """Two blank problems are no review at all, never a silent acceptance."""
    blank = {
        "score": 9,
        "gaps": [
            {
                "gap_id": "gap-01",
                "target_ids": [_PACKET_TARGET_ID],
                "claim_cluster_ids": [],
                "statement_ids": [],
                "kind": "coverage",
                "severity": "major",
                "repair_action": "acquire",
                "problem": "",
                "recommended_queries": ["qec break-even corroboration"],
            }
        ],
        "unsupported_claims": [],
        "recommended_queries": ["qec break-even corroboration"],
        "rationale": "One obligation is unsupported.",
    }
    completer = ScriptedCompleter(outputs=[lambda m, s: blank] * 2)
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.gaps == []
    assert outcome.result.should_continue is False
    assert outcome.state_update["events"][-1].metadata["reason"] == "review_failed"


def _impossible_score_review(score: int) -> dict[str, object]:
    """A reply whose only defect is a score outside the declared 1-10 range."""
    return {
        "score": score,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "rationale": "Complete.",
    }


@pytest.mark.asyncio
async def test_an_out_of_range_provider_score_is_repaired(
    tracker: Tracker,
) -> None:
    """C3: ``{"score": 99}`` is refused and re-asked, never pinned to 10.

    The reply is otherwise well formed and the report is fine, so the old
    ``clamp_score`` produced a clean review scoring ``MAX_CRITIC_SCORE`` — an
    acceptance built on a number nobody wrote. The range is a schema bound now,
    so this is one repair against the same packet, and the repaired review is
    the review that counts.
    """
    completer = ScriptedCompleter(
        outputs=[_impossible_score_review(99), _draft(score=8)]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"
    assert outcome.result.score == 8
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    assert repaired.details["schema_field_paths"] == ["score"]


@pytest.mark.asyncio
async def test_an_out_of_range_score_repeated_exhausts_into_a_failed_review(
    tracker: Tracker,
) -> None:
    """Two impossible scores are no review at all, never a pinned acceptance.

    The second reply scores below the range rather than above it, so the run
    fails on the bound itself and not on one repeated literal.
    """
    completer = ScriptedCompleter(
        outputs=[
            _impossible_score_review(99),
            _impossible_score_review(-4),
        ]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.gaps == []
    assert outcome.result.should_continue is False
    assert outcome.state_update["events"][-1].metadata["reason"] == "review_failed"


def _overflowing_gaps() -> list[dict[str, object]]:
    """Ten minor editorial notes in front of one material acquisition gap.

    Every gap is contract-valid and distinct, so the only thing wrong with a
    reply built from them is the count.
    """
    minor = [
        {
            "statement_ids": ["S001"],
            "kind": "presentation",
            "severity": "minor",
            "repair_action": "synthesize",
            "problem": f"Editorial issue {index}.",
            "recommended_queries": [],
        }
        for index in range(DEFAULT_MAX_NOTES)
    ]
    major = {
        "target_ids": [_PACKET_TARGET_ID],
        "kind": "acquisition",
        "severity": "major",
        "repair_action": "acquire",
        "problem": "The central target lacks required evidence.",
        "recommended_queries": ["qec independent evidence"],
    }
    return [*minor, major]


def _overflowing_review() -> dict[str, object]:
    """The provider-shaped reply that carries those gaps."""
    return {
        "score": 9,
        "gaps": _overflowing_gaps(),
        "unsupported_claims": [],
        "recommended_queries": [],
        "rationale": "Mostly strong.",
    }


@pytest.mark.asyncio
async def test_an_overflowing_gap_list_is_repaired_not_truncated(
    tracker: Tracker,
) -> None:
    """C4: an eleventh gap is refused, never dropped before routing.

    ``normalize_gaps`` ended with ``gaps[:DEFAULT_MAX_NOTES]``, so ten minor
    editorial notes in front of one major acquisition gap silently lost the
    material defect — and the cut left no trace in the critique, so a score of
    9 accepted a report whose central obligation was unmet. The bound is part
    of the reply contract now, and the overflow takes the repair path.
    """
    completer = ScriptedCompleter(
        outputs=[_overflowing_review(), _draft(score=8)]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    assert repaired.details["schema_field_paths"] == ["gaps"]


@pytest.mark.asyncio
async def test_an_overflowing_gap_list_repeated_exhausts_into_a_failed_review(
    tracker: Tracker,
) -> None:
    """Two overflowing replies are no review at all, never a truncated one."""
    completer = ScriptedCompleter(outputs=[_overflowing_review()] * 2)
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.gaps == []
    assert outcome.result.should_continue is False
    assert outcome.state_update["events"][-1].metadata["reason"] == "review_failed"


def test_a_draft_that_bypassed_the_schema_cannot_hide_an_overflow() -> None:
    """C4's second boundary: the normalizer refuses what the schema would have.

    A transport that hands over constructed objects, or a caller that built a
    draft without validation, never runs the ``max_length`` check — so the bound
    is enforced again where the gaps are actually made. Each gap here is valid
    on its own; the reply is refused for its count, and refused rather than cut,
    because the tail of the list is where the material defect sits.
    """
    drafts = [
        CritiqueGapDraft.model_validate(gap) for gap in _overflowing_gaps()
    ]
    draft = CritiqueDraft.model_construct(
        score=9,
        gaps=drafts,
        unsupported_claims=[],
        recommended_queries=[],
        rationale="Mostly strong.",
    )

    with pytest.raises(CritiqueContractViolation, match="more than 10"):
        build_critique(draft, iteration=0, max_iterations=3)


@pytest.mark.asyncio
async def test_an_unknown_scope_id_is_repaired_not_rewritten(
    tracker: Tracker,
) -> None:
    """Important 3: a hallucinated id does not become the question sentinel."""
    hallucinated = {
        "score": 6,
        "gaps": [
            {
                "gap_id": "gap-01",
                "coverage_id": "hallucinated-topic-999",
                "target_ids": [],
                "claim_cluster_ids": [],
                "statement_ids": [],
                "kind": "coverage",
                "severity": "major",
                "repair_action": "acquire",
                "problem": "The report misses the deployment evidence.",
                "recommended_queries": ["qec deployment evidence"],
            }
        ],
        "unsupported_claims": [],
        "recommended_queries": ["qec deployment evidence"],
        "rationale": "One topic is uncovered.",
    }
    completer = ScriptedCompleter(outputs=[lambda m, s: hallucinated, _draft(score=7)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.score == 7
    assert outcome.result.review_status == "reviewed"


def test_the_generic_gap_forms_are_refused_by_the_contract() -> None:
    """The two prohibited generic forms, as the contract actually judges them.

    The prompt names "improve the quality" and "find more sources" as
    non-actionable, and this pins what the *contract* does with them rather
    than pretending to judge arbitrary prose: a generic complaint that names no
    record is refused, and a generic acquisition that names no obligation is
    refused. There is deliberately no keyword blacklist — a contract that
    rejected any problem text containing a forbidden phrase would prove nothing
    about the rest of the prose.
    """
    generic_feedback = {
        "kind": "coverage",
        "severity": "major",
        "repair_action": "synthesize",
        "problem": "Improve the quality of the report.",
        "target_ids": [],
        "statement_ids": [],
        "claim_cluster_ids": [],
        "recommended_queries": [],
    }
    generic_acquisition = {
        "kind": "acquisition",
        "severity": "major",
        "repair_action": "acquire",
        "problem": "Find more sources.",
        "target_ids": [],
        "statement_ids": ["S001"],
        "claim_cluster_ids": [],
        "recommended_queries": ["clinker substitution sources"],
    }

    with pytest.raises(ValidationError, match="affects"):
        CritiqueGapDraft.model_validate(generic_feedback)
    with pytest.raises(ValidationError, match="acquire"):
        CritiqueGapDraft.model_validate(generic_acquisition)

    # The prompt names both forms, so a model is told before it answers.
    instruction = " ".join(CRITIQUE_INSTRUCTION.split())
    assert '"Improve the quality" and "find more sources" are not actionable' in (
        instruction
    )


def test_the_fingerprint_covers_the_fields_the_review_actually_reads() -> None:
    """Important 2: the fingerprint is the packet, not a subset of it.

    The previous digest hashed ids and excerpts only, so editing an evidence
    item's ``source_url`` — the independence signal a critic checks — or a
    target's support policy, or the dimensions a statement is recorded as
    answering, left the identity unchanged and the repair guard blind.
    """
    state = _packet_state()
    packet = build_critic_packet(state)
    baseline = packet.fingerprint

    def mutated(packet: CriticPacket) -> str:
        return critic_packet_fingerprint(packet)

    # An evidence item's source URL becomes the same publisher's other page.
    batches = [
        batch.model_copy(
            update={
                "items": [
                    batch.items[0].model_copy(
                        update={"source_url": "https://other.example/same-work"}
                    ),
                    *batch.items[1:],
                ]
            }
        )
        for batch in packet.evidence_batches
    ]
    assert mutated(
        packet.model_copy(update={"evidence_batches": batches})
    ) != baseline

    # A target's support policy: the obligation's own rule, rendered into the
    # request.
    targets = [
        target.model_copy(update={"support_policy": "primary_attribution"})
        for target in packet.targets
    ]
    assert mutated(packet.model_copy(update={"targets": targets})) != baseline

    # The dimensions a statement is recorded as answering.
    statements = [
        statement.model_copy(update={"answered_dimensions": []})
        for statement in packet.statements
    ]
    assert (
        mutated(packet.model_copy(update={"statements": statements})) != baseline
    )

    # A deterministic hard check.
    assert (
        mutated(
            packet.model_copy(update={"hard_checks": ["a new hard failure"]})
        )
        != baseline
    )

    # And the digest is stable: the packet's own fingerprint field is excluded
    # from its payload, so re-computing it changes nothing.
    assert critic_packet_fingerprint(packet) == baseline
    assert build_critic_packet(state).fingerprint == baseline


def test_the_critic_prompt_version_is_repinned() -> None:
    """The prompt and reply schema both changed, so the version changed.

    The call fingerprint includes the prompt version; leaving it at ``"1"``
    would let a Task 7-era request compare equal to a Task 8 one whose reply
    contract is a different shape.
    """
    assert CriticAgent.prompt_version == "critic-2"


@pytest.mark.asyncio
async def test_the_registered_live_report_is_reviewed_in_full(
    tracker: Tracker,
) -> None:
    """The registered live case's own report is carried whole, end included.

    Its limitations paragraph is the report's last section and the reason the
    case exists: a review that cannot see it cannot judge the disclosure.
    """
    report = _live_report()
    completer = ScriptedCompleter(outputs=[_draft(score=7)])
    agent = _critic(tracker, completer)
    state = _packet_state(report=report)

    async with tracker.session_span("session-1", "question"):
        await agent.run(state)

    body = completer.calls[0][2][1].content
    collapsed = " ".join(body.split())
    assert report in build_critic_packet(state).reader_content
    assert (
        "the durability and long-term performance data under field exposure"
        in collapsed
    )
    assert "rely on assumptions about clinker substitution rates" in collapsed


# --- fix round 4: the provider boundary, swept --------------------------------
#
# One class, ten instances: a provider value the schema accepted and
# normalization then transformed — clamped, truncated, dropped, defaulted,
# coerced, widened or forward-filled — in a way that can move a route, a
# terminal state, a quality status or an acceptance. Each test below was run
# against the unfixed tree first; the captures are in the report's fix-round-4
# section.


def _blank_unsupported_reply() -> dict[str, object]:
    """A score-9 reply whose one defect is a blank unsupported claim."""
    return {
        "score": 9,
        "gaps": [],
        "unsupported_claims": ["   "],
        "recommended_queries": [],
        "rationale": "Otherwise complete.",
    }


@pytest.mark.asyncio
async def test_a_blank_unsupported_claim_is_repaired_not_dropped(
    tracker: Tracker,
) -> None:
    """Sweep 1: ``["   "]`` is a defect the provider reported, not nothing.

    ``unsupported_claims`` is ``list[str]``, so a blank entry validated;
    ``normalize_notes`` then dropped it, ``route_decision`` saw an empty list,
    and a reply that named an unsupported claim was accepted as though it had
    named none.
    """
    completer = ScriptedCompleter(
        outputs=[_blank_unsupported_reply(), _draft(score=8)]
    )
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"
    repaired = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_repaired"
    )
    # The bounded path names the field the reply broke, not the list index:
    # ``schema_field_path`` keeps only field names the requested schema proves.
    assert repaired.details["schema_field_paths"] == ["unsupported_claims"]


@pytest.mark.asyncio
async def test_a_blank_unsupported_claim_repeated_exhausts_into_a_failed_review(
    tracker: Tracker,
) -> None:
    """Two blank claims are no review at all, never an empty claim list."""
    completer = ScriptedCompleter(outputs=[_blank_unsupported_reply()] * 2)
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert outcome.result is not None
    assert outcome.result.review_status == "failed"
    assert outcome.result.unsupported_claims == []
    assert outcome.result.should_continue is False
    assert outcome.state_update["events"][-1].metadata["reason"] == "review_failed"


@pytest.mark.parametrize("coerced", ("9", "9.0", 9.0, True))
def test_a_coerced_provider_score_is_refused(coerced: object) -> None:
    """Sweep 2: ``"9"``, ``"9.0"``, ``9.0`` and ``true`` are not the score 9.

    A non-strict ``int`` field coerces all four, so a provider that never sent
    a number could still hand over a judgement — and ``true`` became the score
    ``1``, a value no model wrote.
    """
    with pytest.raises(ValidationError):
        CritiqueDraft.model_validate(
            {
                "score": coerced,
                "gaps": [],
                "unsupported_claims": [],
                "recommended_queries": [],
                "rationale": "Review.",
            }
        )


def test_the_provider_schema_refuses_a_legacy_string_gap() -> None:
    """Sweep 3: the live reply contract is typed, so a string gap is malformed.

    The before-validator turned ``["Missing cost evidence"]`` into a material
    ``target_ids=["question"]`` / ``severity="major"`` / ``acquire`` gap, which
    rewarded a reply that ignored the typed contract with ``critical_gaps``
    instead of sending it to repair. The legacy rule stays on the state-facing
    ``Critique``, where old snapshots are read.
    """
    with pytest.raises(ValidationError):
        CritiqueDraft.model_validate(
            {
                "score": 9,
                "gaps": ["Missing cost evidence"],
                "unsupported_claims": [],
                "recommended_queries": [],
                "rationale": "One gap.",
            }
        )


def test_the_state_facing_critique_still_reads_a_legacy_string_gap() -> None:
    """The compatibility rule moved; it did not disappear."""
    critique = Critique.model_validate(
        {
            "score": 5,
            "gaps": ["Missing cost evidence"],
            "unsupported_claims": [],
            "recommended_queries": [],
            "should_continue": True,
            "rationale": "One gap.",
            "review_status": "reviewed",
        }
    )

    assert [gap.problem for gap in critique.gaps] == ["Missing cost evidence"]
    assert critique.gaps[0].target_ids == [QUESTION_TARGET_ID]


@pytest.mark.asyncio
async def test_a_transport_supplied_draft_is_revalidated_not_trusted(
    tracker: Tracker,
) -> None:
    """Sweep 4: ``model_construct`` gives the right type and no validation.

    ``_complete_review`` returned any object whose Python type was
    ``CritiqueDraft``, so a transport — or a fake — could hand over a draft
    that had never passed a single constraint. The reply is re-validated now,
    and this forged blank claim takes the repair path like any other malformed
    one.
    """
    forged = CritiqueDraft.model_construct(
        score=9,
        gaps=[],
        unsupported_claims=["   "],
        recommended_queries=[],
        rationale="Otherwise complete.",
    )
    completer = ScriptedCompleter(outputs=[forged, _draft(score=8)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"
    assert outcome.result.score == 8


@pytest.mark.asyncio
async def test_a_forged_non_integer_score_is_repaired_not_routed(
    tracker: Tracker,
) -> None:
    """A forged string score used to travel past the schema into routing."""
    forged = CritiqueDraft.model_construct(
        score="9",
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        rationale="Complete.",
    )
    completer = ScriptedCompleter(outputs=[forged, _draft(score=8)])
    agent = _critic(tracker, completer)

    # Dumping a value that never matched its schema warns, which is exactly what
    # this forged object is; the warning is not the assertion.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        async with tracker.session_span("session-1", "question"):
            outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"
    assert outcome.result.score == 8


@pytest.mark.parametrize("field", ("kind", "severity", "repair_action"))
def test_a_gap_draft_requires_the_fields_that_decide_its_route(
    field: str,
) -> None:
    """Sweep 5: a default must not become a judgement the model never made."""
    gap: dict[str, object] = {
        "target_ids": [_PACKET_TARGET_ID],
        "kind": "coverage",
        "severity": "major",
        "repair_action": "acquire",
        "problem": "One obligation is unmet.",
        "recommended_queries": ["qec independent evidence"],
    }
    del gap[field]

    with pytest.raises(ValidationError):
        CritiqueDraft.model_validate(
            {
                "score": 5,
                "gaps": [gap],
                "unsupported_claims": [],
                "recommended_queries": [],
                "rationale": "Thin.",
            }
        )


@pytest.mark.asyncio
async def test_an_omitted_severity_is_not_a_routing_judgement(
    tracker: Tracker,
) -> None:
    """Sweep 5, at the agent: a score-9 reply routed on a local default.

    ``severity: GapSeverity = "major"`` on the provider-facing draft meant the
    run bought another research pass on a field the model never sent.
    """
    omitted = {
        "score": 9,
        "gaps": [
            {
                "target_ids": [_PACKET_TARGET_ID],
                "problem": "The measured rate is not corroborated.",
                "recommended_queries": ["qec break-even corroboration"],
            }
        ],
        "unsupported_claims": [],
        "recommended_queries": [],
        "rationale": "One obligation is thin.",
    }
    completer = ScriptedCompleter(outputs=[omitted, _draft(score=8)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_packet_state())

    assert [call[0] for call in completer.calls] == [
        "CritiqueDraft",
        "CritiqueDraft",
    ]
    assert outcome.result is not None
    assert outcome.result.review_status == "reviewed"


_OLD_EVIDENCE_UNIT_CHARS = 1200
_OLD_MAX_EVIDENCE_UNITS = 24
_OLD_CLAIM_DIGEST = 40
"""The bounds fix round 4 deleted, named here because the tests still need
"more than the old bound" as input. They live in the test module rather than in
``agents.critic`` on purpose: a constant there would assert a limit the code no
longer applies, which is the exact confusion this round removed.
"""


def test_critic_carries_a_long_evidence_passage_whole() -> None:
    """Sweep 6: the qualifier at the end of a passage is review material.

    ``from_unit`` fed every excerpt through a 1,200-character cut, so a
    sentence like "However, this estimate excludes process emissions" — the
    one that turns an acceptance into a contradiction — was removed before the
    critic could read it.
    """
    tail = "TAIL-CONTRADICTION: this estimate excludes process emissions."
    excerpt = ("supporting context " * 100) + tail
    assert len(excerpt) > _OLD_EVIDENCE_UNIT_CHARS

    packet = build_critic_packet(
        _packet_state(evidence=[_packet_evidence(excerpt=excerpt)])
    )

    item = packet.evidence_batches[0].items[0]
    assert item.excerpt == excerpt
    assert tail in item.excerpt


def test_oversized_evidence_is_batched_and_never_omitted() -> None:
    """Sweep 7: batching is the bound; a 25th passage is not lost material.

    The ceiling kept 24 ids and named the rest as omitted, so the request told
    the critic that more evidence existed and not what it said — a
    contradiction in passage 25 was unjudgeable. Every registered unit is
    carried now, in order, and the batch list bounds the request instead.
    """
    units = [
        _packet_evidence(
            f"ev-{index:02d}",
            excerpt=f"Excerpt {index} about the measured logical error rate. " * 20,
        )
        for index in range(1, _OLD_MAX_EVIDENCE_UNITS + 7)
    ]

    packet = build_critic_packet(_packet_state(evidence=units))

    assert len(packet.evidence_batches) > 1
    for batch in packet.evidence_batches:
        assert batch.chars <= CRITIC_EVIDENCE_BATCH_CHARS or len(batch.items) == 1
    rendered = [
        item.evidence_id
        for batch in packet.evidence_batches
        for item in batch.items
    ]
    assert rendered == [unit.evidence_id for unit in units]
    assert packet.omitted_evidence_ids == []
    assert len(rendered) == _OLD_MAX_EVIDENCE_UNITS + 6
    assert [statement.statement_id for statement in packet.statements] == [
        "S001",
        "F001",
    ]


def _many_claims(count: int, *, long_first: bool = False) -> list[Claim]:
    """Distinct checked claims, the first optionally longer than a digest line."""
    claims: list[Claim] = []
    for index in range(1, count + 1):
        text = f"Checked claim {index} carries its own distinctive marker {index}."
        if index == 1 and long_first:
            text = ("Measured detail about the reported rate. " * 20) + (
                "CLAIM-TAIL-MARKER"
            )
        claims.append(
            _claim().model_copy(
                update={"claim_id": f"claim-{index:02d}", "text": text}
            )
        )
    return claims


def _many_sources(count: int) -> list[ScoredSource]:
    """Distinct scored sources, in citation order."""
    return [
        _source().model_copy(
            update={
                "url": f"https://s{index:02d}.example.org/a",
                "title": f"Source {index}",
            }
        )
        for index in range(1, count + 1)
    ]


@pytest.mark.asyncio
async def test_the_claim_section_renders_every_checked_claim_whole(
    tracker: Tracker,
) -> None:
    """Sweep 8: the request rendered 40 claims, each cut at 240 characters.

    The packet fingerprint covers every checked claim, so a claim the request
    never rendered sat inside the review's authority and outside its view; and
    a claim whose own text was cut could hide the half that contradicts the
    report.
    """
    claims = _many_claims(_OLD_CLAIM_DIGEST + 1, long_first=True)
    completer = ScriptedCompleter(outputs=[_draft(score=8)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        await agent.run(_packet_state(claims=claims))

    collapsed = " ".join(completer.calls[0][2][1].content.split())
    assert f"distinctive marker {_OLD_CLAIM_DIGEST + 1}" in collapsed
    assert "CLAIM-TAIL-MARKER" in collapsed


@pytest.mark.asyncio
async def test_the_source_section_renders_every_cited_source(
    tracker: Tracker,
) -> None:
    """Sweep 9: ``max_sources=36`` dropped source 37 without a marker.

    The dropped row can be the weak or unscored source behind a load-bearing
    statement, which is exactly what a citation review is for.
    """
    sources = _many_sources(37)
    completer = ScriptedCompleter(outputs=[_draft(score=8)])
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        await agent.run(_packet_state(sources=sources))

    collapsed = " ".join(completer.calls[0][2][1].content.split())
    assert "s01.example.org" in collapsed
    assert "s37.example.org" in collapsed


def _badge_state(order: Sequence[str]) -> ResearchState:
    """One evidence unit cited by a ``verified_pair`` and a ``contested`` statement."""
    composition = _packet_composition()
    pair = composition.claim_clusters[_PACKET_CLUSTER_ID]
    contested = pair.model_copy(
        update={
            "cluster_id": "cluster-contested",
            "verdicts": ["contested"],
            "verdict_evidence_status": {"contested": "contested"},
        }
    )
    composition.claim_clusters[contested.cluster_id] = contested
    statements = {
        "pair": ReportStatement(
            statement_id="S001",
            text="The measured rate reached break-even. [1]",
            claim_cluster_ids=[_PACKET_CLUSTER_ID],
            evidence_ids=[_PACKET_EVIDENCE_ID],
            target_ids=[_PACKET_TARGET_ID],
            answered_dimensions=["attribution"],
        ),
        "contested": ReportStatement(
            statement_id="S002",
            text="A second reading disputes the measured rate. [1]",
            claim_cluster_ids=["cluster-contested"],
            evidence_ids=[_PACKET_EVIDENCE_ID],
            target_ids=[_PACKET_TARGET_ID],
            answered_dimensions=["attribution"],
        ),
    }
    composition.summary[0].statement = statements[order[0]]
    composition.sections[0].points[0].statement = statements[order[1]]
    return _packet_state(with_composition=False).model_copy(
        update={"composition": composition}
    )


def test_the_evidence_badge_is_conservative_and_order_independent() -> None:
    """Sweep 10: first-wins badge aggregation widened optimistically.

    One physical passage can corroborate one statement and contest another.
    Taking the first badge in statement order made the review's view of that
    passage depend on order, and a ``verified_pair`` badge could be printed
    beside a cluster that contests it. The conservative rule already used one
    level up applies here: disagreeing contributors report no badge.
    """
    forward = build_critic_packet(_badge_state(("pair", "contested")))
    backward = build_critic_packet(_badge_state(("contested", "pair")))

    item = forward.evidence_batches[0].items[0]
    assert item.badge == ""
    assert item.badge_label == EVIDENCE_BADGE_LABELS[""]
    assert backward.evidence_batches[0].items[0].badge == item.badge
