"""Agent-specific hard gates and every case metric implementation."""

from __future__ import annotations

from deep_research.agents.critic import fallback_critique
from deep_research.agents.steps import ReActObservation, ReActStep
from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.dependencies import (
    bounded_url_fingerprints,
    read_url_fingerprints,
)
from deep_research.evaluation.evaluators import (
    AGENT_GATE_IDS,
    METRIC_FUNCTIONS,
    code_evaluator,
    evaluate_agent_gates,
    evaluate_target,
)
from deep_research.evaluation.models import AGENT_NAMES
from deep_research.tools.base import ToolResult


def gate(results, gate_id):
    return next(item for item in results if item.gate_id == gate_id)


def test_every_case_metric_has_an_implementation() -> None:
    """A case naming a metric nobody implemented is a plan-time bug, and
    ``deterministic_quality`` raises rather than silently scoring zero."""
    missing = sorted(
        {
            metric.metric_id
            for case in all_cases()
            for metric in case.expectations.deterministic_metrics
        }
        - set(METRIC_FUNCTIONS)
    )

    assert missing == []


def test_every_agent_declares_its_specific_gates() -> None:
    for agent_name in AGENT_NAMES:
        assert AGENT_GATE_IDS[agent_name], agent_name


# --- Planner ---------------------------------------------------------------


def test_the_planner_gate_rejects_fewer_than_three_subtopics(
    planner_case, planner_output
) -> None:
    output = planner_output.with_sub_topics(2)

    assert gate(
        evaluate_agent_gates(output, planner_case), "subtopic_count"
    ).passed is False


def test_the_planner_gate_rejects_more_than_seven_subtopics(
    planner_case, planner_output
) -> None:
    output = planner_output.with_sub_topics(8)

    assert gate(
        evaluate_agent_gates(output, planner_case), "subtopic_count"
    ).passed is False


def test_the_planner_gate_rejects_duplicate_titles(
    planner_case, planner_output
) -> None:
    output = planner_output.with_titles(["Alpha", "alpha ", "Beta"])

    assert gate(
        evaluate_agent_gates(output, planner_case), "distinct_subtopics"
    ).passed is False


def test_the_planner_gate_requires_the_question_to_survive(
    planner_case, planner_output
) -> None:
    output = planner_output.model_copy(
        update={"state_update": {"original_question": "a different question"}}
    )

    assert gate(
        evaluate_agent_gates(output, planner_case), "question_preserved"
    ).passed is False


# --- Researcher ------------------------------------------------------------


def test_the_researcher_gate_requires_coverage_or_a_recorded_reason(
    researcher_case, researcher_output
) -> None:
    output = researcher_output.with_findings_for(
        ["Coefficient of performance below freezing"]
    )

    result = gate(
        evaluate_agent_gates(output, researcher_case), "sub_topic_covered"
    )

    assert result.passed is False
    assert "Cold-climate field trial outcomes" in result.detail


def test_an_uncovered_subtopic_passes_when_a_skip_error_was_recorded(
    researcher_case, researcher_output
) -> None:
    output = researcher_output.with_findings_for(
        ["Coefficient of performance below freezing"]
    ).model_copy(
        update={
            "errors": [
                {
                    "error_type": "sub_topic_skipped",
                    "source": "researcher",
                    "message": "no evidence was available",
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "details": {
                        "sub_topic": "Cold-climate field trial outcomes"
                    },
                }
            ]
        }
    )

    assert gate(
        evaluate_agent_gates(output, researcher_case), "sub_topic_covered"
    ).passed is True


def test_the_researcher_gate_rejects_an_invented_source_url(
    researcher_case, researcher_output
) -> None:
    output = researcher_output.with_finding_url("https://invented.example/x")

    assert gate(
        evaluate_agent_gates(output, researcher_case), "no_invented_sources"
    ).passed is False


def test_the_researcher_gate_rejects_a_malformed_source_url_without_raising(
    researcher_case, researcher_output
) -> None:
    """Finding 16: this is the reviewer-found path itself --
    ``_gate_no_invented_sources`` -> ``_no_invented_sources_passes`` ->
    ``agents/sources.py``'s unguarded ``normalize_source_url`` call, for
    the Researcher agent, reached directly through ``evaluate_agent_gates``
    with no local fallback in the way (unlike ``_gate_citations_known``'s
    round-2 ``_normalized`` helper). Pre-root-cause-fix this raised
    ``ValueError: Port out of range 0-65535`` here; post-fix it must
    return a clean failing ``GateResult`` instead.
    """
    output = researcher_output.with_finding_url("https://ex.com:99999/page")

    result = gate(
        evaluate_agent_gates(output, researcher_case), "no_invented_sources"
    )

    assert result.passed is False


def test_evaluate_target_never_raises_on_a_malformed_researcher_source_url(
    researcher_case, researcher_target_output
) -> None:
    """The same malformed URL, driven through ``evaluate_target`` (general
    gates + agent gates + deterministic quality together) -- the actual
    call shape ``runner.py``'s ``_dispatch_code`` uses in production.
    """
    result = dict(researcher_target_output.result)
    findings = [dict(item) for item in result["findings"]]
    findings[0]["source_url"] = "https://ex.com:99999/page"
    output = researcher_target_output.model_copy(
        update={"result": {**result, "findings": findings}}
    )

    report, quality = evaluate_target(output, researcher_case, secrets=())

    assert gate(report.results, "no_invented_sources").passed is False
    assert 0.0 <= quality <= 1.0


# --- Source Evaluator ------------------------------------------------------


def test_the_source_evaluator_gate_requires_one_evaluation_per_source(
    source_evaluator_case, source_evaluator_output
) -> None:
    output = source_evaluator_output.drop_one_source()

    assert gate(
        evaluate_agent_gates(output, source_evaluator_case),
        "one_evaluation_per_source",
    ).passed is False


def test_the_source_evaluator_gate_rejects_a_non_finite_score(
    source_evaluator_case, source_evaluator_output
) -> None:
    output = source_evaluator_output.with_score("overall_score", 1.5)

    assert gate(
        evaluate_agent_gates(output, source_evaluator_case),
        "bounded_scores",
    ).passed is False


def test_the_source_evaluator_gate_requires_the_expected_low_confidence_flag(
    source_evaluator_case, source_evaluator_output
) -> None:
    output = source_evaluator_output.clear_low_confidence()

    assert gate(
        evaluate_agent_gates(output, source_evaluator_case),
        "low_confidence_flagged",
    ).passed is False


# --- Fact Checker ----------------------------------------------------------


def test_the_fact_checker_gate_enforces_independent_domains(
    fact_checker_dependent_case, fact_checker_dependent_output
) -> None:
    """The dependent case declares minimum_independent_domains: a claim
    whose corroboration is only same-family stays rejected even when the
    evidence strings paraphrase without pasting URLs — the scripted
    same-family results the trajectory recorded still resolve to the
    claim's own family."""
    assert gate(
        evaluate_agent_gates(
            fact_checker_dependent_output, fact_checker_dependent_case
        ),
        "independent_domains",
    ).passed is False


def test_two_genuinely_independent_domains_pass_the_gate(
    fact_checker_dependent_case, fact_checker_dependent_output
) -> None:
    """Independent publisher identities must be present on passages."""
    output = fact_checker_dependent_output.with_verification_passage_urls(
        [
            "https://cern.org/outage-audit",
            "https://eia.gov/outage-bulletin",
        ]
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_dependent_case),
        "independent_domains",
    ).passed is True


def test_the_mixed_verdicts_case_passes_without_a_declared_minimum(
    fact_checker_case, fact_checker_output
) -> None:
    """The ordinary case never declares minimum_independent_domains, so the
    gate cannot fail a well-supported verified claim whose evidence
    paraphrases instead of pasting URLs."""
    output = fact_checker_output.with_evidence_texts(
        [
            "The IAEA framework covers SMR designs and the NRC applies the "
            "same review."
        ]
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_case),
        "independent_domains",
    ).passed is True


def test_the_fact_checker_gate_requires_evidence_on_a_verified_claim(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_empty_evidence()

    assert gate(
        evaluate_agent_gates(output, fact_checker_case), "evidence_linked"
    ).passed is False


def test_the_fact_checker_evidence_gate_rejects_a_claims_own_publisher(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_verification_passage_urls(
        ["https://iaea.org/another-safety-page"]
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_case), "evidence_linked"
    ).passed is False


def test_the_fact_checker_evidence_gate_accepts_contradiction_passages(
    fact_checker_case, fact_checker_output
) -> None:
    result = dict(fact_checker_output.result or {})
    claims = [dict(item) for item in (result.get("verified_claims") or [])]
    claim = claims[0]
    claim["verdict"] = "contradicted"
    claim["evidence"] = []
    claim["contradictions"] = ["An independent source disputes the result."]
    claim["verification_evidence"] = [
        {
            **passage,
            "stance": "contradicts",
            "excerpt": "An independent source disputes the result.",
        }
        for passage in claim["verification_evidence"]
    ]
    output = fact_checker_output.model_copy(
        update={"result": {**result, "verified_claims": claims}}
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_case), "evidence_linked"
    ).passed is True


def test_insufficient_evidence_must_stay_low_confidence(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_claim_verdict(
        "insufficient_evidence", confidence=0.95
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_case),
        "conservative_insufficiency",
    ).passed is False


# --- Fact Checker: read-bearing passage provenance -------------------------
#
# Task 5 review, Important 2: the evidence checks validated passage fields and
# publisher independence but never bound a passage URL to the run's
# read-bearing tool results, so a search-only or invented URL could still pass
# the quality gates. These tests drive the REAL classifier over typed steps —
# the same one ``targets._success_output`` records the artifact with — and
# require the gate to agree.

SEARCH_RESULT_URL = "https://third.test/x"
DOCUMENT_URL = "https://fourth.test/d.csv"
MEMORY_URL = "https://fifth.test/m"


def _typed_step(
    iteration: int, tool_name: str, data: dict[str, object]
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=f"Call {tool_name}.",
        action="use_tool",
        tool_name=tool_name,
        observation=ReActObservation(
            tool_name=tool_name, success=True, summary=f"{tool_name} ran"
        ),
        tool_result=ToolResult(
            tool_name=tool_name, success=True, data=data, latency_ms=1.0
        ),
    )


SEARCH_STEP = _typed_step(
    1,
    "web_search",
    {"results": [{"title": "T", "url": SEARCH_RESULT_URL}]},
)
SCRAPE_STEP = _typed_step(
    2, "web_scraper", {"url": SEARCH_RESULT_URL, "text": "Body."}
)
DOCUMENT_STEP = _typed_step(
    3, "document_reader", {"source": DOCUMENT_URL, "chunks": ["a"]}
)
MEMORY_STEP = _typed_step(
    4,
    "query_memory",
    {"matches": [{"content": "A remembered passage.", "source_url": MEMORY_URL}]},
)


def _evidence_gate(output, case):
    return gate(evaluate_agent_gates(output, case), "evidence_linked")


def test_the_read_provenance_classifier_excludes_search_only_hits() -> None:
    """A search result list is discovery: it proves no read."""
    fingerprints, complete = read_url_fingerprints([SEARCH_STEP])

    assert (fingerprints, complete) == ([], True)


def test_the_read_provenance_classifier_keeps_every_read_bearing_tool() -> None:
    fingerprints, complete = read_url_fingerprints(
        [SEARCH_STEP, SCRAPE_STEP, DOCUMENT_STEP, MEMORY_STEP]
    )
    recorded, _ = bounded_url_fingerprints(
        [SEARCH_RESULT_URL, DOCUMENT_URL, MEMORY_URL]
    )

    assert complete is True
    assert set(fingerprints) == set(recorded)


def test_a_search_only_passage_url_fails_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    """Passages built from a search hit alone: the searched URL was never
    read, so the classifier records no identity for it."""
    output = fact_checker_output.with_verification_passage_urls(
        [SEARCH_RESULT_URL]
    ).with_read_steps([SEARCH_STEP])

    assert output.dependencies.read_url_fingerprints == []
    assert _evidence_gate(output, fact_checker_case).passed is False


def test_a_scraped_passage_url_passes_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_verification_passage_urls(
        [SEARCH_RESULT_URL]
    ).with_read_steps([SEARCH_STEP, SCRAPE_STEP])

    assert read_url_fingerprints([SEARCH_STEP, SCRAPE_STEP])[0] == list(
        output.dependencies.read_url_fingerprints
    )
    assert _evidence_gate(output, fact_checker_case).passed is True


def test_a_document_read_passage_url_passes_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_verification_passage_urls(
        [DOCUMENT_URL]
    ).with_read_steps([DOCUMENT_STEP])

    assert _evidence_gate(output, fact_checker_case).passed is True


def test_a_memory_read_passage_url_passes_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    """A provenance-bearing ``query_memory`` match is a read, and counts."""
    output = fact_checker_output.with_verification_passage_urls(
        [MEMORY_URL]
    ).with_read_steps([MEMORY_STEP])

    assert read_url_fingerprints([MEMORY_STEP])[0] == list(
        output.dependencies.read_url_fingerprints
    )
    assert _evidence_gate(output, fact_checker_case).passed is True


def test_an_artifact_that_cannot_prove_its_reads_fails_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    """Fail closed: an empty provenance field proves nothing."""
    output = fact_checker_output.with_verification_passage_urls(
        [SEARCH_RESULT_URL]
    ).with_read_urls([])

    assert output.dependencies.read_url_fingerprints == []
    assert _evidence_gate(output, fact_checker_case).passed is False


def test_an_incomplete_read_provenance_ledger_fails_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    """A truncated identity list may be missing exactly the passage's URL."""
    output = fact_checker_output.with_verification_passage_urls(
        [SEARCH_RESULT_URL]
    ).with_read_urls([SEARCH_RESULT_URL], complete=False)

    assert output.dependencies.read_url_fingerprints_complete is False
    assert _evidence_gate(output, fact_checker_case).passed is False


def test_a_passage_at_an_unread_independent_domain_fails_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    """The old fixture's exact defect: a CERN URL absent from the run.

    Publisher independence is not provenance: an invented independent domain
    used to satisfy the evidence gate outright.
    """
    output = (
        fact_checker_output.with_verification_passage_urls(
            ["https://cern.org/outage-audit"]
        ).with_read_steps([SEARCH_STEP, SCRAPE_STEP])
    )

    assert _evidence_gate(output, fact_checker_case).passed is False


# --- Synthesizer -----------------------------------------------------------


def test_the_synthesizer_gate_rejects_an_unknown_citation(
    synthesizer_case, synthesizer_output
) -> None:
    output = synthesizer_output.with_report_citing(
        "https://invented.example.com/page"
    )

    assert gate(
        evaluate_agent_gates(output, synthesizer_case), "citations_known_only"
    ).passed is False


def test_the_synthesizer_gate_requires_limitations_to_be_represented(
    synthesizer_case, synthesizer_output
) -> None:
    output = synthesizer_output.without_limitations()

    assert gate(
        evaluate_agent_gates(output, synthesizer_case),
        "limitations_represented",
    ).passed is False


def test_the_synthesizer_gate_rejects_a_false_publication_claim(
    synthesizer_composition_case, synthesizer_composition_output
) -> None:
    """Task 6 composes artifacts; it must not claim they were published."""
    output = synthesizer_composition_output.with_report_text(
        "The full report was published to the output directory."
    )

    assert gate(
        evaluate_agent_gates(output, synthesizer_composition_case),
        "no_false_publication_claim",
    ).passed is False


def test_the_synthesizer_gate_checks_publication_claims_in_both_artifacts(
    synthesizer_composition_case, synthesizer_composition_output
) -> None:
    result = dict(synthesizer_composition_output.result or {})
    output = synthesizer_composition_output.model_copy(
        update={
            "result": {
                **result,
                "evidence_markdown": "The evidence ledger was saved to disk.",
            }
        }
    )

    assert gate(
        evaluate_agent_gates(output, synthesizer_composition_case),
        "no_false_publication_claim",
    ).passed is False


def test_the_synthesizer_gate_rejects_a_persistence_call(
    synthesizer_case, synthesizer_output
) -> None:
    output = synthesizer_output.model_copy(
        update={
            "dependencies": synthesizer_output.dependencies.model_copy(
                update={"document_writes": 1}
            )
        }
    )

    assert gate(
        evaluate_agent_gates(output, synthesizer_case),
        "no_persistence_calls",
    ).passed is False


# --- Critic ----------------------------------------------------------------


def test_the_critic_gate_requires_a_bounded_score(
    critic_case, critic_output
) -> None:
    output = critic_output.with_score(0)

    assert gate(
        evaluate_agent_gates(output, critic_case), "bounded_component_scores"
    ).passed is False


def test_the_critic_gate_uses_the_production_routing_rule(
    critic_case, critic_output
) -> None:
    """The gate calls ``agents.critic.route_decision`` rather than
    re-deriving the threshold, so the two can never disagree."""
    output = critic_output.with_score(9).with_should_continue(True)

    assert gate(
        evaluate_agent_gates(output, critic_case), "route_consistent"
    ).passed is False


def test_the_critic_gate_accepts_a_typed_provider_fallback_stop(
    critic_case, critic_output
) -> None:
    fallback, _ = fallback_critique(
        reason="provider_unavailable",
        iteration=critic_case.state.iteration,
        max_iterations=critic_case.state.max_iterations,
    )
    output = critic_output.model_copy(
        update={
            "result": {"critique": fallback.model_dump(mode="json")},
            "errors": [
                {
                    "error_type": "critic_review_provider_error",
                    "source": "agent.critic",
                    "message": "provider review fallback used",
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "recoverable": False,
                    "details": {
                        "operation": "critic_report_review",
                        "provider_failure": {
                            "kind": "provider_failure",
                            "type": "ProviderError",
                            "retryable": False,
                            "status_code": None,
                        },
                    },
                }
            ],
        }
    )

    assert fallback.should_continue is False
    assert gate(
        evaluate_agent_gates(output, critic_case), "route_consistent"
    ).passed is True


def test_the_critic_gate_forbids_continuing_with_no_budget_left(
    critic_budget_case, critic_budget_output
) -> None:
    output = critic_budget_output.with_should_continue(True)

    assert gate(
        evaluate_agent_gates(output, critic_budget_case), "route_consistent"
    ).passed is False


def test_the_critic_gate_requires_actionable_critique(
    critic_gap_case, critic_gap_output
) -> None:
    output = critic_gap_output.with_gaps([]).with_recommended_queries([])

    assert gate(
        evaluate_agent_gates(output, critic_gap_case), "critique_actionable"
    ).passed is False


# --- The LangSmith adapter -------------------------------------------------


def test_the_code_evaluator_emits_gate_and_quality_feedback(
    planner_case, clean_target_output
) -> None:
    from tests.evaluation_fakes import FakeExampleRow, FakeRun

    evaluator = code_evaluator(planner_case, secrets=())
    results = evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    keys = {item["key"] for item in results["results"]}
    assert "hard_gates_passed" in keys
    assert "deterministic_quality" in keys
    assert any(key.startswith("gate:") for key in keys)


def test_the_code_evaluator_never_raises_on_a_malformed_run(
    planner_case,
) -> None:
    """A broken run must fail its gates, not abort the whole experiment."""
    from tests.evaluation_fakes import FakeExampleRow, FakeRun

    evaluator = code_evaluator(planner_case, secrets=())
    results = evaluator(FakeRun(outputs={"nonsense": True}), FakeExampleRow({}))

    scores = {item["key"]: item["score"] for item in results["results"]}
    assert scores["hard_gates_passed"] == 0
    assert scores["deterministic_quality"] == 0.0


def test_the_code_evaluator_never_raises_on_a_malformed_cited_url(
    planner_case, clean_target_output
) -> None:
    """Finding 14, traced through the actual runner dispatch path.

    ``code_evaluator``'s inner ``evaluate`` closure is exactly what
    ``runner.py``'s ``_dispatch_code`` wraps in a LangSmith evaluator call,
    and ``runner.py`` only catches ``ValidationError`` around that
    dispatch — never ``ValueError``. Pre-fix, a malformed cited URL (e.g.
    an out-of-range port) made ``_gate_citations_known`` raise
    ``ValueError`` from ``normalize_source_url``'s ``urlsplit(...).port``,
    which would escape here uncaught, and in the real runner that means
    ``pending_gates[key]`` is never set and the whole repetition silently
    vanishes from ``repetitions_by_case`` instead of failing its gates.
    This must now return a normal, fully populated gate report with
    ``citations_known`` scored 0 -- not raise.

    Uses the planner (no agent-specific gate touches
    ``normalize_source_url``) so this test isolates ``citations_known``'s
    own resilience rather than the separately-scoped, pre-existing
    ``normalize_source_url`` non-totality reachable from other gates
    (e.g. ``no_invented_sources``'s ``source_domain`` call, out of scope
    for this fix per the finding).
    """
    from tests.evaluation_fakes import FakeExampleRow, FakeRun

    result = dict(clean_target_output.result)
    result["sub_topics"] = [
        {
            **result["sub_topics"][0],
            "rationale": "See https://ex.com:99999/page for details.",
        },
        *result["sub_topics"][1:],
    ]
    output = clean_target_output.model_copy(update={"result": result})

    evaluator = code_evaluator(planner_case, secrets=())
    results = evaluator(
        FakeRun(outputs=output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    scores = {item["key"]: item["score"] for item in results["results"]}
    assert scores["gate:citations_known"] == 0
    assert scores["hard_gates_passed"] == 0
    # The repetition was scored, not silently dropped: every other gate
    # still ran and reported a real result.
    assert len(results["results"]) > 2


def test_evaluate_target_combines_general_and_agent_gates(
    planner_case, clean_target_output
) -> None:
    report, quality = evaluate_target(
        clean_target_output, planner_case, secrets=()
    )

    ids = {item.gate_id for item in report.results}
    assert "run_completed" in ids
    assert "subtopic_count" in ids
    assert 0.0 <= quality <= 1.0

