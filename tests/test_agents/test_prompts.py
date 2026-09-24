"""Tests for the pure prompt rendering boundary."""

from __future__ import annotations

import re

import pytest

from deep_research.agents.identity import claim_fingerprint
from deep_research.agents.prompts import (
    CLAIM_EXTRACTION_INSTRUCTION,
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_VERIFICATION_INSTRUCTION,
    CLAIM_VERIFICATION_SYSTEM_PROMPT,
    CRITIC_REVIEW_SYSTEM_PROMPT,
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    FACT_CHECKER_SYSTEM_PROMPT,
    NATIVE_REACT_RESPONSE_CONTRACT,
    REPORT_INSTRUCTION,
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    STRUCTURED_EXAMPLE_NOTICE,
    STRUCTURED_REPLY_FORMAT,
    SYNTHESIZER_SYSTEM_PROMPT,
    AgentTask,
    render_claim_digest,
    render_finding_digest,
    render_react_messages,
    render_scratchpad,
    render_source_dossier,
    render_source_quality,
    render_structured_reply_format,
)
from deep_research.agents.sources import SourceGroup
from deep_research.memory.entries import ScratchpadEntry
from deep_research.utils.types import Claim, Finding, ScoredSource


def _entry(content: str, kind: str = "thought") -> ScratchpadEntry:
    return ScratchpadEntry.model_validate(
        {"agent_name": "researcher", "kind": kind, "content": content}
    )


def test_scratchpad_renders_kind_prefixed_lines_oldest_first() -> None:
    rendered = render_scratchpad(
        [_entry("Search for benchmarks."), _entry("Found 5 results.", "observation")]
    )

    assert rendered == (
        "- [thought] Search for benchmarks.\n- [observation] Found 5 results."
    )


def test_scratchpad_says_so_when_empty() -> None:
    assert render_scratchpad([]) == "(no notes yet)"


def test_scratchpad_collapses_multiline_entry_content_onto_one_line() -> None:
    rendered = render_scratchpad(
        [
            _entry(
                "## Summary\nFirst I need X.\n\nThen Y.",
                kind="summary",
            )
        ]
    )

    assert rendered == "- [summary] ## Summary First I need X. Then Y."
    assert "\n" not in rendered.split("] ", 1)[1]


def test_the_native_contract_asks_for_provider_native_tool_calling() -> None:
    assert "provider-native tool calling" in NATIVE_REACT_RESPONSE_CONTRACT
    assert (
        "never write or imitate a tool call in text, JSON, XML, DSML, or a "
        "Markdown fence" in NATIVE_REACT_RESPONSE_CONTRACT
    )
    assert (
        "return the final answer directly" in NATIVE_REACT_RESPONSE_CONTRACT
    )


def test_the_native_contract_asks_for_one_or_more_tools() -> None:
    """The transport accepts several calls per turn, so the text must too.

    ``_native_response_outcome`` accepts every ``function_call`` item in one
    response, so a contract capping the model at one call throws away
    lookups the transport would have executed.
    """
    assert "one or more tools" in NATIVE_REACT_RESPONSE_CONTRACT
    assert "at most one" not in NATIVE_REACT_RESPONSE_CONTRACT
    assert (
        "never write or imitate a tool call in text, JSON, XML, DSML, or a "
        "Markdown fence" in NATIVE_REACT_RESPONSE_CONTRACT
    )


def test_the_native_contract_never_mentions_the_simulated_protocol() -> None:
    for forbidden in (
        "## Tools",
        "tool_input_json",
        "tool_name",
        "ReActDecision",
        "leave final_answer empty",
    ):
        assert forbidden not in NATIVE_REACT_RESPONSE_CONTRACT


def test_react_messages_open_with_the_agent_system_prompt() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        scratchpad=[],
        iteration=1,
        max_iterations=3,
    )

    assert len(messages) == 2
    assert messages[0].role == "developer"
    assert messages[0].content == "You are a researcher."
    assert messages[1].role == "user"


def test_react_messages_carry_task_notes_and_the_iteration_budget() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(
            instruction="Summarize QEC progress.",
            guidance="Prefer 2025 sources.",
        ),
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )
    body = messages[1].content

    assert "Summarize QEC progress." in body
    assert "Prefer 2025 sources." in body
    assert "- [thought] Search for benchmarks." in body
    assert "Iteration 2 of 3." in body
    assert NATIVE_REACT_RESPONSE_CONTRACT in body


def test_react_messages_advertise_no_tool_catalogue_and_no_action_envelope() -> None:
    """The tools ride on the request itself, so the text must name none."""
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        scratchpad=[],
        iteration=1,
        max_iterations=3,
    )
    body = messages[1].content

    for forbidden in (
        "## Tools",
        "tool_input_json",
        "tool_name",
        "ReActDecision",
        "Arguments:",
        "no tools available",
    ):
        assert forbidden not in body


def test_react_messages_omit_the_guidance_section_when_it_is_blank() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(instruction="Summarize QEC progress."),
        scratchpad=[],
        iteration=1,
        max_iterations=1,
    )

    assert "## Guidance" not in messages[1].content


def test_react_messages_are_deterministic() -> None:
    def _render() -> list[str]:
        return [
            message.content
            for message in render_react_messages(
                system_prompt="You are a researcher.",
                task=AgentTask(instruction="Summarize QEC progress."),
                scratchpad=[_entry("note")],
                iteration=1,
                max_iterations=3,
            )
        ]

    assert _render() == _render()


@pytest.mark.parametrize(
    ("iteration", "max_iterations", "match"),
    [
        (0, 3, r"^iteration must be at least 1$"),
        (4, 3, "iteration must not exceed max_iterations"),
        (1, 0, "max_iterations must be at least 1"),
    ],
)
def test_react_messages_reject_an_impossible_iteration_budget(
    iteration: int, max_iterations: int, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        render_react_messages(
            system_prompt="You are a researcher.",
            task=AgentTask(instruction="Summarize QEC progress."),
            scratchpad=[],
            iteration=iteration,
            max_iterations=max_iterations,
        )


def test_react_messages_render_the_full_body_verbatim() -> None:
    messages = render_react_messages(
        system_prompt="You are a researcher.",
        task=AgentTask(
            instruction="Summarize QEC progress.",
            guidance="Prefer 2025 sources.",
        ),
        scratchpad=[_entry("Search for benchmarks.")],
        iteration=2,
        max_iterations=3,
    )

    assert messages[1].content == (
        "## Task\n"
        "Summarize QEC progress.\n\n"
        "## Guidance\n"
        "Prefer 2025 sources.\n\n"
        "## Notes so far\n"
        "- [thought] Search for benchmarks.\n\n"
        "## Budget\n"
        "Iteration 2 of 3.\n\n"
        "## How to respond\n"
        f"{NATIVE_REACT_RESPONSE_CONTRACT}"
    )


def test_react_messages_reject_a_blank_system_prompt() -> None:
    with pytest.raises(ValueError, match="system_prompt must not be blank"):
        render_react_messages(
            system_prompt="   ",
            task=AgentTask(instruction="Summarize QEC progress."),
            scratchpad=[],
            iteration=1,
            max_iterations=1,
        )


def test_memory_guidance_lists_recalled_findings_and_strategies() -> None:
    from deep_research.agents.prompts import render_memory_guidance
    from deep_research.utils.types import Finding, MemorySnapshot

    guidance = render_memory_guidance(
        MemorySnapshot(
            similar_findings=[
                Finding(
                    content="Logical error rates fell below break-even.",
                    source_url="https://example.test/qec",
                    source_title="QEC 2025",
                    extracted_at="2026-01-01T00:00:00+00:00",
                    confidence=0.8,
                    related_sub_topic="Error correction",
                )
            ],
            suggested_strategies=["Prefer peer-reviewed sources."],
        )
    )

    assert "1 finding(s) recalled from previous sessions:" in guidance
    assert "Logical error rates fell below break-even." in guidance
    assert "https://example.test/qec" in guidance
    assert "Strategies that worked before:" in guidance
    assert "- Prefer peer-reviewed sources." in guidance


def test_memory_guidance_is_empty_when_nothing_was_recalled() -> None:
    from deep_research.agents.prompts import render_memory_guidance
    from deep_research.utils.types import MemorySnapshot

    assert render_memory_guidance(MemorySnapshot()) == ""


PROMPT_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _prompt_finding(
    *,
    content: str = "Logical error rates fell below break-even.",
    url: str = "https://example.org/a",
    source_title: str = "QEC 2025",
    sub_topic: str = "Alpha",
    attributed_issuer: str | None = None,
    attribution_quote: str | None = None,
    measure_scope: str | None = None,
    vintage: str | None = None,
    release_date: str | None = None,
    data_period: str | None = None,
    statement_date: str | None = None,
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title=source_title,
        extracted_at=PROMPT_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
        attributed_issuer=attributed_issuer,
        attribution_quote=attribution_quote,
        measure_scope=measure_scope,
        vintage=vintage,
        release_date=release_date,
        data_period=data_period,
        statement_date=statement_date,
    )


def test_source_dossier_renders_every_scoring_input() -> None:
    group = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="QEC 2025",
        sub_topics=["Alpha"],
        findings=[_prompt_finding()],
    )

    rendered = render_source_dossier(group, index=2, reputation=0.9)

    assert "Source 2: https://example.org/a" in rendered
    assert "Title: QEC 2025" in rendered
    assert "Cited for: Alpha" in rendered
    assert "Corroboration" not in rendered
    assert "Known reputation: 0.90" in rendered
    assert "Logical error rates fell below break-even." in rendered


def test_source_dossier_says_so_when_no_reputation_is_known() -> None:
    group = SourceGroup(
        url="https://example.org/a", domain="example.org", title="A"
    )

    rendered = render_source_dossier(group, index=1, reputation=None)

    assert "Known reputation: none on record" in rendered
    assert "(no findings)" in rendered


def test_source_dossier_clamps_long_finding_text() -> None:
    group = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="A",
        sub_topics=["Alpha"],
        findings=[_prompt_finding(content="x" * 500)],
    )

    rendered = render_source_dossier(
        group, index=1, reputation=None, excerpt_chars=50
    )

    assert "x" * 500 not in rendered
    assert "..." in rendered


def test_finding_digest_numbers_findings_and_names_their_sources() -> None:
    rendered = render_finding_digest(
        [
            _prompt_finding(sub_topic="Alpha"),
            _prompt_finding(content="Second claim.", sub_topic="Beta"),
        ]
    )

    assert "1. [Alpha] Logical error rates fell below break-even." in rendered
    assert "(https://example.org/a)" in rendered
    assert "2. [Beta] Second claim." in rendered


def test_finding_digest_handles_an_empty_list() -> None:
    assert render_finding_digest([]) == "(no findings)"


def test_finding_digest_carries_issuer_vintage_and_release_date() -> None:
    """The fact checker judges a figure by whose it is and how current it is.

    The audited run's claim stage read "Clean Edge reported 10.3 GW" from a
    digest that carried the host and nothing else: not that the page
    attributes both figures to EIA, not the inventory vintage behind them, and
    not the release date that tells the December-2024 inventory from the
    January-2025 one. Every fact the finding recorded about the figure's
    provenance has to reach the model that adjudicates it.
    """
    rendered = render_finding_digest(
        [
            _prompt_finding(
                content=(
                    "U.S. utility-scale capacity additions in 2024, by fuel "
                    "type."
                ),
                url=(
                    "https://cleanedge.com/data-dive/"
                    "u-s-electric-utility-scale-capacity-additions-by-fuel-type-2"
                ),
                attributed_issuer="U.S. Energy Information Administration (EIA)",
                attribution_quote=(
                    "according to the U.S. Energy Information Administration "
                    "(EIA)"
                ),
                measure_scope="utility-scale, projects larger than 1 MW",
                vintage="January 2025 Preliminary Monthly Electric Generator Inventory",
                release_date="2025-03-12",
                data_period="2024",
            )
        ]
    )

    assert "attributed to U.S. Energy Information Administration (EIA)" in rendered
    assert "as relayed by cleanedge.com" in rendered
    assert "scope: utility-scale, projects larger than 1 MW" in rendered
    assert (
        "vintage: January 2025 Preliminary Monthly Electric Generator Inventory"
        in rendered
    )
    assert "released: 2025-03-12" in rendered
    assert "period: 2024" in rendered
    assert "(https://cleanedge.com/data-dive/" in rendered


def test_finding_digest_does_not_call_an_institutional_body_a_relay() -> None:
    """First party is established the way evidence.py establishes it for a read.

    eia.gov's own "Today in Energy" page names the agency and prints its
    bracketed acronym, and ``.gov`` is a suffix only the agency's registry
    controls, so printing "as relayed by eia.gov" beside the agency's own
    name would describe a relay where there is one body. Wood Mackenzie's
    press release sits on a plain commercial domain no registry reserves for
    it, so it keeps the neutral clause even though it is the monitor's own
    report — the safe direction this rule never silently drops a host it
    cannot verify as the issuer's own.
    """
    rendered = render_finding_digest(
        [
            _prompt_finding(
                content="The agency's own count is 10.4 GW in 2024.",
                url="https://eia.gov/todayinenergy/detail.php?id=64705",
                source_title=(
                    "Today in Energy - U.S. Energy Information Administration "
                    "(EIA)"
                ),
                attributed_issuer="U.S. Energy Information Administration (EIA)",
                attribution_quote=(
                    "according to our January 2025 preliminary electric "
                    "generator inventory data"
                ),
            ),
            _prompt_finding(
                content="The monitor's 2024 total is 12,314 MW.",
                url="https://woodmac.com/press-releases/energy-storages-rise",
                source_title="Energy Storage's Meteoric Rise | Wood Mackenzie",
                attributed_issuer="Wood Mackenzie",
                attribution_quote=(
                    "released today by the American Clean Power Association "
                    "(ACP) and Wood Mackenzie"
                ),
                measure_scope="all segments",
            ),
        ]
    )

    assert (
        "attributed to U.S. Energy Information Administration (EIA)) "
        "(https://eia.gov/todayinenergy/detail.php?id=64705)"
    ) in rendered
    assert "attributed to Wood Mackenzie" in rendered
    assert "as relayed by woodmac.com" in rendered
    assert "as relayed by eia.gov" not in rendered


def test_finding_digest_keeps_the_relay_clause_for_a_lookalike_host() -> None:
    """A letters substring on the label is not a first party.

    eia.news spells the agency's acronym exactly as eia.gov does, and
    energy.gov's label is inside the agency's own name too, but neither
    domain is the agency's: a news site's suffix carries no registry
    guarantee, and DOE's own department site is not the sub-agency it
    reports on. Both keep the relay clause.
    """
    rendered = render_finding_digest(
        [
            _prompt_finding(
                content="Battery installations reached 12 GW in 2024.",
                url="https://eia.news/batteries",
                source_title="Energy News - Battery Storage Roundup",
                attributed_issuer="U.S. Energy Information Administration (EIA)",
                attribution_quote=(
                    "according to the U.S. Energy Information Administration "
                    "(EIA)"
                ),
            ),
            _prompt_finding(
                content="DOE announced a new loan guarantee program.",
                url="https://www.energy.gov/articles/loan-guarantee",
                source_title="DOE Announces New Loan Guarantee Program",
                attributed_issuer="U.S. Energy Information Administration (EIA)",
                attribution_quote=(
                    "data from the U.S. Energy Information Administration (EIA)"
                ),
            ),
        ]
    )

    assert "as relayed by eia.news" in rendered
    assert "as relayed by energy.gov" in rendered


def test_finding_digest_leaves_a_self_published_figure_unattributed() -> None:
    """No relay claim where the page states its own figure, and no blank labels.

    The digest is read as a description of evidence: printing "attributed to"
    or "relayed by" beside a body's own figure, or an empty label where the
    source states no date, would describe provenance the finding does not
    carry.
    """
    rendered = render_finding_digest(
        [
            _prompt_finding(
                url="https://eia.gov/todayinenergy/detail.php?id=64705",
                vintage="January 2025 preliminary inventory",
            )
        ]
    )

    assert "attributed to" not in rendered
    assert "relayed by" not in rendered
    assert "vintage: January 2025 preliminary inventory" in rendered
    assert "released:" not in rendered
    assert "scope:" not in rendered


def test_source_quality_marks_low_confidence_sources() -> None:
    rendered = render_source_quality(
        [
            ScoredSource(
                url="https://example.org/a",
                title="A",
                authority_score=0.9,
                recency_score=0.8,
                relevance_score=0.9,
                overall_score=0.9,
                rationale="Strong.",
            ),
            ScoredSource(
                url="https://weak.test/b",
                title="B",
                authority_score=0.1,
                recency_score=0.1,
                relevance_score=0.1,
                overall_score=0.08,
                rationale="Weak.",
                low_confidence=True,
            ),
        ]
    )

    assert "https://example.org/a: score=0.90 status=scored" in rendered
    assert (
        "https://weak.test/b: score=0.08 status=scored low_confidence=true"
        in rendered
    )


def test_source_quality_handles_an_empty_list() -> None:
    assert render_source_quality([]) == "(no sources scored)"


def test_source_quality_renders_unscored_status_without_a_numeric_placeholder() -> None:
    rendered = render_source_quality(
        [
            ScoredSource(
                url="https://capped.test/source",
                title="Capped",
                authority_score=None,
                recency_score=None,
                relevance_score=None,
                overall_score=None,
                rationale="Past the source cap.",
                evaluation_status="unscored_cap",
            )
        ]
    )

    assert rendered == "- https://capped.test/source: status=unscored_cap"
    assert "0.20" not in rendered


def test_source_quality_deduplicates_and_caps_by_relevance() -> None:
    rendered = render_source_quality(
        [
            ScoredSource(
                url="https://example.test/a",
                title="A old",
                authority_score=0.8,
                recency_score=0.8,
                relevance_score=0.2,
                overall_score=0.3,
                rationale="Old assessment.",
            ),
            ScoredSource(
                url="https://EXAMPLE.test/a/",
                title="A current",
                authority_score=0.9,
                recency_score=0.9,
                relevance_score=0.95,
                overall_score=0.92,
                rationale="Current assessment.",
            ),
            ScoredSource(
                url="https://other.test/b",
                title="B",
                authority_score=0.3,
                recency_score=0.4,
                relevance_score=0.1,
                overall_score=0.2,
                rationale="Low relevance.",
            ),
            ScoredSource(
                url="https://capped.test/c",
                title="C",
                authority_score=None,
                recency_score=None,
                relevance_score=None,
                overall_score=None,
                rationale="Past the cap.",
                evaluation_status="unscored_cap",
            ),
        ],
        max_sources=2,
    )

    assert rendered.splitlines() == [
        "- https://example.test/a: score=0.92 status=scored",
        "- https://other.test/b: score=0.20 status=scored",
    ]
    assert rendered.count("https://example.test/a") == 1


def test_new_prompt_constants_state_their_contracts() -> None:
    # The scoring call must never be asked for a combined score: this
    # project computes overall_score from the three source dimensions.
    assert "overall" not in SOURCE_SCORING_INSTRUCTION
    assert "authority" in SOURCE_SCORING_INSTRUCTION
    assert "between 0 and 1" in SOURCE_SCORING_INSTRUCTION
    assert "exact url" in SOURCE_EVALUATOR_SYSTEM_PROMPT


def test_the_source_evaluator_keeps_a_written_date_at_its_own_precision() -> None:
    """A dateline written in words dates the document by that day.

    The instruction used to ask for "the year, for 'January 15, 2026'",
    because the value had to appear in the quote. Every EIA page in the
    audited run dates itself that way, so two releases of one series were
    recorded as the same "2026" and could not be ranked — the reduction is
    what the instruction must not ask for, and the reader now accepts the
    full date a spelled quote states.
    """
    instruction = SOURCE_SCORING_INSTRUCTION

    assert "return the full date the words name" in instruction
    assert "the year, for" not in instruction
    # The value still has to be one the quote states: precision is the
    # document's, never the model's.
    assert "the date the words name and no finer one" in instruction


def test_constraint_cells_state_how_they_are_checked() -> None:
    """Mechanism/geography have no structured provenance, so they are checked.

    Task 6 could only tell the model to be careful. Task 7 checks the cell
    against the evidence its row cites and repairs it, which is a different
    contract and has to be stated as one: the prompt must say the wording
    comes from the evidence and that an unbacked cell is replaced.
    """
    instruction = REPORT_INSTRUCTION.casefold()

    assert "checked against the evidence the row cites" in instruction
    assert "the wording of a cell must come from that evidence" in instruction
    assert "replaced with 'not stated'" in instruction


def test_claim_ids_are_copied_from_the_one_evidence_packet() -> None:
    """The writer is shown one addressable claim block, not two whose labels
    could disagree: the instruction must name that packet, never a second
    'checked-claims' block the composer no longer sends.
    """
    instruction = REPORT_INSTRUCTION.casefold()

    assert "copy the labels exactly as printed in the evidence packet" in instruction
    assert "checked-claims packet" not in instruction


def test_source_consumers_distinguish_quality_scores_from_statuses() -> None:
    assert "quality score of every source" not in SYNTHESIZER_SYSTEM_PROMPT
    assert "quality score when scored" in SYNTHESIZER_SYSTEM_PROMPT
    assert "explicit evaluation status otherwise" in SYNTHESIZER_SYSTEM_PROMPT
    assert "quality score when scored" in CRITIC_REVIEW_SYSTEM_PROMPT
    assert "explicit evaluation status otherwise" in CRITIC_REVIEW_SYSTEM_PROMPT
    assert "source scores" not in CRITIC_SYSTEM_PROMPT
    assert "quality score when scored" in CRITIC_SYSTEM_PROMPT
    assert "explicit evaluation status otherwise" in CRITIC_SYSTEM_PROMPT
    assert "independent" in FACT_CHECKER_SYSTEM_PROMPT
    assert "retrieved findings" in CLAIM_EXTRACTION_SYSTEM_PROMPT
    assert "empty list" in CLAIM_EXTRACTION_INSTRUCTION
    assert "invent" in CLAIM_VERIFICATION_SYSTEM_PROMPT
    for verdict in ("verified", "unverified", "contradicted",
                    "insufficient_evidence"):
        assert verdict in CLAIM_VERIFICATION_INSTRUCTION


def _digest_claim(
    *,
    text: str = "Logical error rates fell below break-even.",
    verdict: str = "verified",
    confidence: float = 0.8,
    urls: list[str] | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=urls or ["https://example.org/a"],
        verdict=verdict,
        evidence_status=(
            "verified_pair"
            if verdict == "verified"
            else "source_supported"
            if verdict == "insufficient_evidence"
            else None
        ),
        confidence=confidence,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
    )


def test_claim_digest_shows_verdict_confidence_and_sources() -> None:
    rendered = render_claim_digest(
        [
            _digest_claim(),
            _digest_claim(
                text="Adoption is broad.",
                verdict="insufficient_evidence",
                confidence=0.0,
                urls=["https://other.test/b", "https://third.test/c"],
            ),
        ]
    )

    assert "1. [verified 0.80] Logical error rates fell below break-even." in rendered
    assert "(https://example.org/a)" in rendered
    assert "2. [insufficient_evidence 0.00] Adoption is broad." in rendered
    assert "(https://other.test/b, https://third.test/c)" in rendered


def test_claim_digest_clamps_long_claims_and_handles_an_empty_list() -> None:
    rendered = render_claim_digest([_digest_claim(text="x" * 500)], limit=50)

    assert "x" * 500 not in rendered
    assert "..." in rendered
    assert render_claim_digest([]) == "(no claims were checked)"


def test_the_synthesizer_prompt_forbids_inventing_evidence() -> None:
    assert "verified" in SYNTHESIZER_SYSTEM_PROMPT
    assert "invent" in SYNTHESIZER_SYSTEM_PROMPT
    # The skeleton is rendered locally; asking the model for it would let
    # the two disagree.
    assert "citation" in REPORT_INSTRUCTION
    assert "exactly" in REPORT_INSTRUCTION
    assert "executive summary" in REPORT_INSTRUCTION
    assert "uncertainty" in REPORT_INSTRUCTION


def test_the_critic_prompt_states_the_gap_and_score_contracts() -> None:
    assert "no tools" in CRITIC_SYSTEM_PROMPT
    assert "1 to 10" in CRITIQUE_INSTRUCTION
    # Materiality is now declared rather than implied: a gap carries a
    # severity, and only critical and major gaps block acceptance.
    assert "material defect" in CRITIQUE_INSTRUCTION
    assert "severity" in CRITIQUE_INSTRUCTION
    assert "minor" in CRITIQUE_INSTRUCTION
    assert "routing" not in CRITIQUE_INSTRUCTION
    assert "recommended" in CRITIQUE_INSTRUCTION


TOOL_FREE_SENTENCE = re.compile(r"[^.]*\bno tools\b[^.]*\.")


def test_the_review_system_prompt_names_no_tools() -> None:
    """Neither Critic prompt offers tools, so neither may name one.

    Measured: the review payload carries no ``tools`` and no ``tool_choice``,
    yet the prompt announced ``web_search`` and ``query_memory``. The model
    obeyed and emitted DeepSeek tool-invocation markup into the message text,
    which local JSON validation rejected — 16 of 30 first attempts. Task 8
    went further and removed the tool path, so the shared prompt is tool-free
    too and its "no tools" sentence says so.

    The word "tool" is checked by removal, not by exemption: every sentence
    that states the *absence* of tools is struck out, and the word may not
    appear in any other sentence. An earlier version skipped the check
    whenever the word occurred at all, which constrained nothing.
    """
    for prompt in (CRITIC_REVIEW_SYSTEM_PROMPT, CRITIC_SYSTEM_PROMPT):
        lowered = prompt.lower()
        for forbidden in ("web_search", "query_memory", "spot-check"):
            assert forbidden not in lowered, forbidden
        remainder = TOOL_FREE_SENTENCE.sub("", lowered)
        assert "tool" not in remainder, remainder

    # Both prompts state the absence explicitly, so a model cannot read either
    # as an invitation to call something.
    assert "no tools" in CRITIC_REVIEW_SYSTEM_PROMPT.lower()
    assert "no tools" in CRITIC_SYSTEM_PROMPT.lower()


def test_the_review_prompt_describes_the_report_boundaries() -> None:
    assert "fenced block" in CRITIC_REVIEW_SYSTEM_PROMPT
    # No angle-bracket marker vocabulary may survive anywhere in the prompt.
    assert "BEGIN" not in CRITIC_REVIEW_SYSTEM_PROMPT
    assert "END marker" not in CRITIC_REVIEW_SYSTEM_PROMPT
    assert "<" not in CRITIC_REVIEW_SYSTEM_PROMPT
    # The old tool-aware prompt announced a spot-check loop; the Critic has
    # none, so no prompt of this agent may advertise a tool again.
    assert "web_search" not in CRITIC_SYSTEM_PROMPT
    assert "query_memory" not in CRITIC_SYSTEM_PROMPT
    assert "exact excerpt of a successful read" in CRITIC_REVIEW_SYSTEM_PROMPT


def test_unsupported_claims_are_defined_leniently_with_an_override() -> None:
    """Attribution counts as support, unless evidence contradicts it.

    Measured: the earlier wording — "statements the report makes that no cited
    source or verified claim backs" — admitted two readings, and the Critic took
    the strict one. Live canary judge rationales report 4, 8 and 5 unsupported
    claims, reasoned from "outside the two verified claims" while the report
    attributes those statements inline to named sources.

    Strictness is self-defeating here for a structural reason: claim extraction
    is deliberately partial (it selects only the most load-bearing claims), so
    absence from the digest would function as evidence of unsupportedness, and
    `route_decision` consults `unsupported_claims` after the score and gaps
    checks, so over-reporting forces refinement on runs that would otherwise be
    accepted.

    The override matters as much as the leniency: a bare citation must not
    survive a claim verdict or spot-check evidence that contradicts it.
    """
    prose = " ".join(CRITIQUE_INSTRUCTION.split())

    # Lenient by default.
    assert "neither clearly attributed to one of the report's cited sources" in prose
    assert "nor backed by a verified claim" in prose
    # Absence from the digest is explicitly not evidence of unsupportedness.
    assert "the claim digest is deliberately partial" in prose
    assert "absence from it is not evidence of unsupportedness" in prose
    assert (
        "Do not mark a cited statement unsupported solely because it lacks a "
        "separate verified-claim entry" in prose
    )
    # …and the override keeps the lenient reading from laundering citations.
    # Task 8 replaced "spot-check evidence" with the packet's read excerpts:
    # the Critic no longer searches, so a snippet can no longer stand in for a
    # contradiction.
    assert "A contrary " in prose
    assert "claim verdict or a read excerpt that disagrees still makes a " in prose
    assert "statement unsupported, however it is cited" in prose
    # The superseded strict wording must be gone.
    assert "that no cited source or verified claim backs" not in prose


# --- the shared structured reply format --------------------------------------

def test_the_shared_reply_format_names_json_and_forbids_a_fence() -> None:
    assert "JSON object" in STRUCTURED_REPLY_FORMAT
    assert "no Markdown fence" in STRUCTURED_REPLY_FORMAT


def test_render_structured_reply_format_normalizes_each_example() -> None:
    rendered = render_structured_reply_format(
        (
            (
                "Example input: an isolated case.",
                '{ "b": 2,\n "a": 1 }',
            ),
        )
    )

    assert STRUCTURED_REPLY_FORMAT in rendered
    assert STRUCTURED_EXAMPLE_NOTICE in rendered
    assert "Example input: an isolated case.\nExample JSON output:\n" in rendered
    # Compact, sorted, single-line, and never fenced.
    assert '{"a":1,"b":2}' in rendered
    assert "```" not in rendered


def test_render_structured_reply_format_accepts_two_examples() -> None:
    rendered = render_structured_reply_format(
        (
            ("Weak example input: a.", '{"score":1}'),
            ("Strong example input: b.", '{"score":9}'),
        )
    )

    assert rendered.count("Example JSON output:") == 2


@pytest.mark.parametrize(
    "examples",
    [
        pytest.param((), id="zero-examples"),
        pytest.param(
            (
                ("One.", '{"a":1}'),
                ("Two.", '{"a":2}'),
                ("Three.", '{"a":3}'),
            ),
            id="three-examples",
        ),
        pytest.param((("Example input: x.", "{not json}"),), id="malformed-json"),
        pytest.param((("Example input: x.", "[1, 2]"),), id="array-payload"),
        pytest.param((("Example input: x.", '"scalar"'),), id="scalar-payload"),
        pytest.param((("   ", '{"a":1}'),), id="blank-label"),
        pytest.param((("Line one\nLine two", '{"a":1}'),), id="multi-line-label"),
    ],
)
def test_render_structured_reply_format_fails_closed(examples) -> None:
    with pytest.raises(ValueError):
        render_structured_reply_format(examples)
