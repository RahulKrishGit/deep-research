"""Agent-specific hard gates and every case metric implementation."""

from __future__ import annotations

from deep_research.agents.critic import fallback_critique
from deep_research.agents.report import build_citation_index, collapse_mirror_urls
from deep_research.agents.steps import ReActObservation, ReActStep
from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.cases.critic import (
    CALIBRATION_CLUSTER_IDS,
    CALIBRATION_TARGET_IDS,
    measure_critic_calibration,
)
from deep_research.evaluation.dependencies import (
    bounded_url_fingerprints,
    read_url_fingerprints,
)
from deep_research.evaluation.evaluators import (
    AGENT_GATE_IDS,
    METRIC_FUNCTIONS,
    code_evaluator,
    deterministic_metric_scores,
    deterministic_quality,
    evaluate_agent_gates,
    evaluate_general_gates,
    evaluate_target,
)
from deep_research.evaluation.models import (
    AGENT_NAMES,
    CaseExpectations,
    DependencyLedger,
    DeterministicMetric,
    EvaluationCase,
    JudgeRubric,
    TargetOutput,
)
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    ORIGINAL_QUESTION_OMISSION_REFERENCE,
    ResearchState,
)


def gate(results, gate_id):
    return next(item for item in results if item.gate_id == gate_id)


def metric_score(output, case, metric_id: str) -> float:
    """One case metric's unit score, resolved through ``METRIC_FUNCTIONS``."""
    return deterministic_metric_scores(
        output, case, metric_functions=METRIC_FUNCTIONS
    )[metric_id]


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
    recorded, _ = bounded_url_fingerprints([SEARCH_RESULT_URL, DOCUMENT_URL])

    assert complete is True
    assert set(fingerprints) == set(recorded)
    assert MEMORY_URL not in fingerprints


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


def test_a_memory_read_passage_url_fails_the_evidence_gate(
    fact_checker_case, fact_checker_output
) -> None:
    """A ``query_memory`` match is recall, not a read: it proves nothing.

    The match carries text, a source URL, and a previous "verified" label.
    None of that is a same-run read or a validated cache entry, so the
    artifact cannot prove the passage's provenance and the gate fails closed.
    """
    output = fact_checker_output.with_verification_passage_urls(
        [MEMORY_URL]
    ).with_read_steps([MEMORY_STEP])

    assert read_url_fingerprints([MEMORY_STEP])[0] == []
    assert _evidence_gate(output, fact_checker_case).passed is False


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


def test_the_critic_gate_rejects_a_typed_provider_fallback_stop(
    critic_case, critic_output
) -> None:
    """No review exists, so no routing decision can be consistent with one.

    This test previously asserted the opposite. The fallback's
    ``should_continue=False`` satisfied ``route_consistent`` — the same reason
    ``review_produced`` exists — and after Critical 2's fix the critique carries
    ``review_status="failed"``, which the gate now reads directly.
    """
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
    assert fallback.review_status == "failed"
    gates = {
        item.gate_id: item for item in evaluate_agent_gates(output, critic_case)
    }
    assert gates["route_consistent"].passed is False
    assert gates["review_produced"].passed is False


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


# --- Task 12: scoped evidence targets ---------------------------------------
#
# Section 2.1's scoping guarantees are metrics, not gates: each one states a
# property of a plan that a general gate cannot see, so each needs a positive
# proof and a mutation that must score zero.


def _scoped_topic(output):
    """The scoped-targets fixture's first sub-topic: the comparison."""
    return output.result["sub_topics"][0]


def test_a_scoped_plan_scores_its_metrics_one(
    scoped_targets_case, scoped_target_output
) -> None:
    for metric_id in (
        "targets_declared",
        "dimensions_are_checkable",
        "support_policy_not_downgraded",
        "no_vague_dimensions",
    ):
        assert (
            metric_score(scoped_target_output, scoped_targets_case, metric_id)
            == 1.0
        ), metric_id
    assert (
        deterministic_quality(
            scoped_target_output,
            scoped_targets_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        == 1.0
    )


def test_the_omission_marker_alone_is_not_a_counted_obligation(
    scoped_targets_case, scoped_target_output
) -> None:
    """A target carrying the reserved omission reference is a marker for a
    reviewed omission, not an evidence obligation: a plan whose only entry is
    the marker has declared nothing and cannot be executed."""
    topic = _scoped_topic(scoped_target_output)
    marker = {
        **topic["evidence_targets"][0],
        "question": ORIGINAL_QUESTION_OMISSION_REFERENCE,
    }
    output = scoped_target_output.with_evidence_targets(
        {str(topic["title"]): [marker]}
    )

    assert metric_score(output, scoped_targets_case, "targets_declared") == 0.0


def test_a_counted_obligation_beside_the_marker_still_counts(
    scoped_targets_case, scoped_target_output
) -> None:
    """The filter skips the marker; it does not fail the plan carrying it."""
    topic = _scoped_topic(scoped_target_output)
    targets = list(topic["evidence_targets"])
    marker = {
        **targets[0],
        "target_id": "topic-01-target-02",
        "question": ORIGINAL_QUESTION_OMISSION_REFERENCE,
    }
    output = scoped_target_output.with_evidence_targets(
        {str(topic["title"]): [*targets, marker]}
    )

    assert metric_score(output, scoped_targets_case, "targets_declared") == 1.0


def test_a_plan_with_no_evidence_targets_scores_targets_declared_zero(
    scoped_targets_case, scoped_target_output
) -> None:
    """A plan with an empty target list is a *legacy* plan — one that has to
    be replanned before it can be executed — never a plan with nothing
    required."""
    output = scoped_target_output.without_evidence_targets()

    assert metric_score(output, scoped_targets_case, "targets_declared") == 0.0


def test_a_plan_declaring_nothing_fails_the_scoping_metrics_closed(
    scoped_targets_case, scoped_target_output
) -> None:
    """No obligation means nothing is checkable and nothing is un-vague.

    Both metrics iterate the plan's targets and returned true when there were
    none — one because ``all()`` over an empty sequence is true, the other
    because its ``any()`` was false — so a plan that declared no obligation
    collected their weight. A metric that checks obligations cannot pass a
    plan that has none.
    """
    output = scoped_target_output.without_evidence_targets()

    assert (
        metric_score(output, scoped_targets_case, "dimensions_are_checkable")
        == 0.0
    )
    assert (
        metric_score(output, scoped_targets_case, "no_vague_dimensions") == 0.0
    )
    assert (
        deterministic_quality(
            output,
            scoped_targets_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        < 1.0
    )


def test_one_uncreditable_dimension_makes_the_obligation_uncheckable(
    scoped_targets_case, scoped_target_output
) -> None:
    """Every required dimension must be creditable, not merely one of them.

    Production ``target_is_answered`` requires ``required.issubset(answered)``
    and ``answered_dimensions`` can only hold dimensions this same helper
    credits, so an obligation carrying one uncreditable dimension beside a
    creditable one can never be answered by any statement. Reading the
    helper's list as a truthy/falsey whole called that plan checkable and
    handed it the metric's weight.
    """
    output = scoped_target_output.with_target_dimensions(
        ["period: the most recent year", "safety record"]
    )

    assert (
        metric_score(output, scoped_targets_case, "dimensions_are_checkable")
        == 0.0
    )
    assert (
        deterministic_quality(
            output,
            scoped_targets_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        < 1.0
    )


def test_an_explicit_year_and_unit_are_structurally_checkable(
    scoped_targets_case, scoped_target_output
) -> None:
    """A plan is valid even when a generic probe uses a different year and unit."""
    output = scoped_target_output.with_target_dimensions(
        [
            "measure: grid-scale battery storage capacity added, in MW",
            "period: calendar year 2024",
        ]
    )

    assert metric_score(
        output, scoped_targets_case, "dimensions_are_checkable"
    ) == 1.0


def test_a_vague_dimension_scores_checkability_and_vagueness_zero(
    scoped_targets_case, scoped_target_output
) -> None:
    """The scoping defect both metrics exist for: every obligation still
    carries "required dimensions", and not one of them names anything a
    recorded proposition can fill."""
    output = scoped_target_output.with_target_dimensions(["relevant information"])

    assert (
        metric_score(output, scoped_targets_case, "dimensions_are_checkable")
        == 0.0
    )
    assert (
        metric_score(output, scoped_targets_case, "no_vague_dimensions") == 0.0
    )


def test_a_policy_downgraded_under_its_own_question_scores_zero(
    scoped_targets_case, scoped_target_output
) -> None:
    """The comparative obligation keeps the wording that earns an independent
    pair and is recorded under a weaker policy."""
    target = _scoped_topic(scoped_target_output)["evidence_targets"][0]
    output = scoped_target_output.with_target_policy(
        str(target["target_id"]), "primary_attribution"
    )

    assert (
        metric_score(
            output, scoped_targets_case, "support_policy_not_downgraded"
        )
        == 0.0
    )


def test_a_single_issuer_obligation_keeps_the_policy_its_question_earns(
    scoped_targets_case, scoped_target_output
) -> None:
    """A descriptive quantity earns no policy, so the plan's own decides.

    ``support_policy_for`` falls back to ``independent_pair`` for any question
    whose form earns nothing, so reading *it* scored every plan that follows
    the planner's own instruction — which stamps ``primary_attribution`` on a
    figure one issuer publishes — as a downgrade. The metric now reads the
    earned policy, which this question does not earn at all.
    """
    topics = scoped_target_output.result["sub_topics"]
    single_issuer = topics[0]["evidence_targets"][0]["target_id"]
    measured = topics[1]["evidence_targets"][0]["target_id"]
    output = scoped_target_output.with_target_policy(
        str(single_issuer),
        "primary_attribution",
        question=(
            "How much grid-scale battery storage capacity was added in the "
            "United States in 2024?"
        ),
    ).with_target_policy(str(measured), "independent_pair")

    assert (
        metric_score(
            output, scoped_targets_case, "support_policy_not_downgraded"
        )
        == 1.0
    )


def test_a_comparative_obligation_lowered_to_one_issuer_still_scores_zero(
    scoped_targets_case, scoped_target_output
) -> None:
    """The control: what the question earns is still the floor."""
    target = _scoped_topic(scoped_target_output)["evidence_targets"][0]
    output = scoped_target_output.with_target_policy(
        str(target["target_id"]), "primary_attribution"
    )

    assert (
        metric_score(
            output, scoped_targets_case, "support_policy_not_downgraded"
        )
        == 0.0
    )


def test_a_plan_that_lost_its_comparison_scores_zero(
    scoped_targets_case, scoped_target_output
) -> None:
    """Every recorded policy agrees with its question and the comparison is
    simply gone: the declared policy set is the only clause that can see it."""
    target = _scoped_topic(scoped_target_output)["evidence_targets"][0]
    output = scoped_target_output.with_target_policy(
        str(target["target_id"]),
        "primary_attribution",
        question=(
            "What does the current federal interconnection rule require of "
            "utility-scale wind projects?"
        ),
    )

    assert (
        metric_score(
            output, scoped_targets_case, "support_policy_not_downgraded"
        )
        == 0.0
    )


def test_a_contract_only_confirmation_demand_is_seen_as_a_downgrade() -> None:
    """The metric reads the session's own question, exactly as the floor does.

    The planner always rewrites a target into an atomic sentence, so
    "Independently confirm X" survives only in the contract's original
    question. Before the metric threaded ``contract_question`` through, a
    plan that recorded such a target under ``primary_attribution`` could not
    be told apart from one that earned no policy at all (review rank 1).
    """
    case = EvaluationCase(
        case_id="confirmation-demand-probe",
        version=1,
        agent_name="planner",
        tier="controlled",
        title="Independent confirmation demanded by the original question",
        purpose="Probe _support_policy_not_downgraded_passes's contract reading.",
        state=ResearchState(
            session_id="evaluation-confirmation-demand-probe",
            original_question=(
                "Independently confirm how much grid-scale battery storage "
                "the US added in 2024."
            ),
        ),
        dependency_scenario="planner-clean-memory",
        expectations=CaseExpectations(
            required_output_fields=["sub_topics"],
            max_iterations=2,
            max_tool_calls=10,
            deterministic_metrics=[
                DeterministicMetric(
                    metric_id="support_policy_not_downgraded",
                    weight=1.0,
                    description="probe",
                )
            ],
        ),
        judge_rubric=JudgeRubric(
            rubric_id="confirmation-demand-probe",
            version=1,
        ),
    )
    output = TargetOutput(
        case_id=case.case_id,
        case_version=case.version,
        agent_name=case.agent_name,
        tier=case.tier,
        repetition=1,
        session_id="evaluation-confirmation-demand-probe",
        experiment_name="planner-controlled-probe",
        completed=True,
        result={
            "sub_topics": [
                {
                    "coverage_id": "topic-01",
                    "title": "Battery storage additions",
                    "rationale": "The measured 2024 addition.",
                    "search_queries": ["battery storage additions 2024"],
                    "success_criteria": ["The 2024 addition is stated."],
                    "priority": 1,
                    "evidence_targets": [
                        {
                            "target_id": "topic-01-target-01",
                            "coverage_id": "topic-01",
                            "question": (
                                "How much battery storage capacity was "
                                "added in the US in 2024?"
                            ),
                            "required_dimensions": [
                                "measure: battery storage capacity added, "
                                "in MW",
                                "source: the federal energy statistical "
                                "agency's published capacity data",
                            ],
                            "required": True,
                            "critical": True,
                            "support_policy": "primary_attribution",
                        }
                    ],
                }
            ]
        },
        target_model_requested="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )

    assert (
        metric_score(output, case, "support_policy_not_downgraded") == 0.0
    )


def test_the_dimension_probe_fills_every_signal_field() -> None:
    """``dimensions_are_checkable`` is only as good as the proposition it
    probes with.

    A signal field the probe leaves empty would report every dimension
    answerable only through that field as un-creditable, failing plans that
    are fine. Reflected over the private signal table deliberately: that
    table is the thing the probe must stay in step with.
    """
    from deep_research.evaluation.evaluators import _TARGET_DIMENSION_PROBE
    from deep_research.utils.types import (
        _DIMENSION_SIGNALS,
        answered_atom_dimensions,
    )

    expected = {field for _, fields in _DIMENSION_SIGNALS for field in fields}

    assert set(answered_atom_dimensions((_TARGET_DIMENSION_PROBE,))) == expected


# --- Task 12: read-bearing acquisition --------------------------------------


def _readable_urls(read_bearing_case) -> list[str]:
    return list(read_bearing_case.expectations.reference["readable_urls"])


def test_a_read_bearing_run_scores_its_metrics_one(
    read_bearing_case, read_bearing_output
) -> None:
    for metric_id in (
        "findings_are_read_bearing",
        "no_recall_only_source",
        "sub_topic_coverage",
        "budget_respected",
    ):
        assert (
            metric_score(read_bearing_output, read_bearing_case, metric_id)
            == 1.0
        ), metric_id
    assert (
        deterministic_quality(
            read_bearing_output,
            read_bearing_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        == 1.0
    )


def test_a_finding_citing_an_unread_page_scores_zero(
    read_bearing_case, read_bearing_output
) -> None:
    """The run read one of the two pages it cites; the other finding rests on
    a page its artifact cannot prove was ever opened."""
    readable = _readable_urls(read_bearing_case)
    output = read_bearing_output.with_read_urls([readable[0]])

    assert output.dependencies.read_url_fingerprints_complete is True
    assert (
        metric_score(output, read_bearing_case, "findings_are_read_bearing")
        == 0.0
    )


def test_a_recalled_lead_reported_as_a_finding_scores_both_metrics_zero(
    read_bearing_case, read_bearing_output
) -> None:
    """The case's whole risk, and why a general gate cannot see it.

    The remembered entry is not a read, so a finding citing it is not
    read-bearing — while ``citations_known`` passes, because the case
    deliberately declares that URL as one of its known sources.
    """
    recall_only = read_bearing_case.expectations.reference["recall_only_url"]
    output = read_bearing_output.with_finding_url(recall_only)

    assert gate(
        evaluate_general_gates(output, read_bearing_case, secrets=()),
        "citations_known",
    ).passed is True
    assert (
        metric_score(output, read_bearing_case, "findings_are_read_bearing")
        == 0.0
    )
    assert (
        metric_score(output, read_bearing_case, "no_recall_only_source") == 0.0
    )


def test_an_artifact_that_cannot_prove_its_reads_scores_zero(
    read_bearing_case, read_bearing_output
) -> None:
    """Fail closed, three ways: an incomplete ledger, a ledger that lost its
    read identities, and a complete ledger that recorded no read at all."""
    readable = _readable_urls(read_bearing_case)
    incomplete = read_bearing_output.with_read_urls(readable, complete=False)
    lost = read_bearing_output.model_copy(
        update={"dependencies": DependencyLedger()}
    )
    empty = read_bearing_output.with_read_urls([])

    assert incomplete.dependencies.read_url_fingerprints_complete is False
    for output in (incomplete, lost, empty):
        assert (
            metric_score(output, read_bearing_case, "findings_are_read_bearing")
            == 0.0
        )


def test_an_empty_finding_list_scores_zero(
    read_bearing_case, read_bearing_output
) -> None:
    """Reporting nothing is not read-bearing: a run that reads and then says
    nothing must not collect the metric's weight."""
    output = read_bearing_output.model_copy(update={"result": {"findings": []}})

    assert (
        metric_score(output, read_bearing_case, "findings_are_read_bearing")
        == 0.0
    )


# --- Task 8: the three calibration error rates, measured separately ----------
#
# False acceptance, false rejection, and missed defects are three different
# mistakes with three different denominators. Folding them into one accuracy
# number hides which one a change made worse, so each has its own count and
# its own rate. The denominator is always the cases that could make that
# mistake: only a case whose answer should be rejected can be falsely
# accepted, and only a case with a defect can have that defect missed.


def test_the_calibration_report_measures_the_three_rates_separately() -> None:
    report = measure_critic_calibration()

    assert report.false_acceptance_rate == 0.0
    assert report.false_rejection_rate == 0.0
    assert report.missed_defect_rate == 0.0
    assert report.cases_expecting_rejection >= 1
    assert report.cases_expecting_acceptance >= 1
    assert report.cases_expecting_a_defect >= 1
    # Three separate denominators, not one shared accuracy figure.
    assert report.cases_expecting_a_defect != report.cases_expecting_rejection


def test_false_acceptance_is_measured_against_the_rejections_only() -> None:
    """A review that waves a bad answer through is one specific error.

    The injected override is exactly that mistake, applied to one case: a
    review that names no defect and scores the candidate at the top of the
    scale. Only the false-acceptance count may move.
    """
    baseline = measure_critic_calibration()
    injected = measure_critic_calibration(
        gap_overrides={"calibration-missing-critical-topic": []},
        score_overrides={"calibration-missing-critical-topic": 9},
    )

    assert injected.false_acceptances == baseline.false_acceptances + 1
    assert injected.false_acceptance_rate > baseline.false_acceptance_rate
    # The other two rates have their own denominators and did not move.
    assert injected.false_rejections == baseline.false_rejections
    assert injected.false_rejection_rate == baseline.false_rejection_rate
    # Accepting a defective answer while naming no defect is both mistakes at
    # once, and each is counted once, against its own denominator.
    assert injected.missed_defects == baseline.missed_defects + 1
    assert injected.cases_expecting_rejection != injected.cases_expecting_a_defect


def test_false_rejection_is_measured_against_the_acceptances_only() -> None:
    """Thin-sourcing a sound answer is a different error from accepting one."""
    baseline = measure_critic_calibration()
    injected = measure_critic_calibration(
        score_overrides={"calibration-strong-answer": 3}
    )

    assert injected.false_rejections == baseline.false_rejections + 1
    assert injected.false_rejection_rate > baseline.false_rejection_rate
    assert injected.false_acceptances == baseline.false_acceptances
    assert injected.missed_defects == baseline.missed_defects


def test_missed_defects_are_measured_against_the_defective_cases_only() -> None:
    """A defect named nowhere is its own error, scored below the threshold.

    The override drops the gap list and scores the candidate low, so the
    defect exists, nothing names it, and the answer is still rejected: the
    missed-defect count moves while neither acceptance count does.
    """
    baseline = measure_critic_calibration()
    injected = measure_critic_calibration(
        gap_overrides={"calibration-missing-critical-topic": []},
        score_overrides={"calibration-missing-critical-topic": 4},
    )

    assert injected.missed_defects == baseline.missed_defects + 1
    assert injected.missed_defect_rate > baseline.missed_defect_rate
    assert injected.false_acceptances == baseline.false_acceptances
    assert injected.false_rejections == baseline.false_rejections
    assert injected.cases_expecting_a_defect != injected.cases_expecting_acceptance


def test_critic_self_agreement_is_recorded_as_not_ground_truth() -> None:
    """These rates measure the contract, not whether the report is good.

    The Critic scoring its own scripted review cannot establish that the
    review was right: independent report review and source checks remain
    required, and the report says so rather than implying a validation it did
    not perform.
    """
    report = measure_critic_calibration()

    assert report.self_agreement_is_ground_truth is False
    assert "not ground truth" in report.basis


# --- Task 12: work-role independence ----------------------------------------


def _work_role_urls(work_role_case) -> list[str]:
    reference = work_role_case.expectations.reference
    return [*reference["same_work_urls"], *reference["independent_work_urls"]]


def _evaluated_row(output, url: str) -> dict:
    return next(
        row for row in output.result["evaluated_sources"] if row["url"] == url
    )


def test_a_work_role_run_scores_its_metrics_one(
    work_role_case, work_role_output
) -> None:
    for metric_id in (
        "mirror_not_a_new_work",
        "independent_work_recognized",
        "one_evaluation_per_source",
        "bounded_scores",
    ):
        assert (
            metric_score(work_role_output, work_role_case, metric_id) == 1.0
        ), metric_id
    assert (
        deterministic_quality(
            work_role_output,
            work_role_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        == 1.0
    )


def test_a_repository_copy_labelled_the_original_is_a_new_work(
    work_role_case, work_role_output
) -> None:
    """The case's risk in its most literal form: the archive copy claims to
    be the original publication, published by someone else. One work has
    become two, and every downstream independence count inherits it."""
    mirror = work_role_case.expectations.reference["same_work_urls"][1]
    output = work_role_output.with_source_identity(
        mirror, transport_relation="original", publisher_id="example.org"
    )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 0.0
    assert (
        metric_score(output, work_role_case, "independent_work_recognized")
        == 1.0
    )


def test_an_independent_work_given_the_original_publisher_scores_zero(
    work_role_case, work_role_output
) -> None:
    """Collapsing the university's own study into the survey's publisher
    reports one publisher where the run retrieved two — the independence
    count downstream inherits that loss too."""
    reference = work_role_case.expectations.reference
    original = _evaluated_row(work_role_output, reference["original_url"])
    independent = reference["independent_work_urls"][0]
    output = work_role_output.with_source_identity(
        independent, publisher_id=original["publisher_id"]
    )

    assert (
        metric_score(output, work_role_case, "independent_work_recognized")
        == 0.0
    )
    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 1.0


def test_unknown_identity_asserts_nothing_and_recognizes_nothing(
    work_role_case, work_role_output
) -> None:
    """The asymmetry between the two metrics is the design.

    ``mirror_not_a_new_work`` asks whether the run asserted a false second
    work; a row that records no identity asserted nothing, so the metric
    passes. ``independent_work_recognized`` asks whether the run recognized
    the genuinely separate work; unknown identity cannot recognize it, so the
    metric fails. Both outcomes belong to the same artifact: labelling
    everything unknown is exactly the behaviour the second metric exists to
    refuse, and it is not something to paper over by making the first fail.
    """
    output = work_role_output
    for url in _work_role_urls(work_role_case):
        output = output.with_source_identity(
            url,
            transport_relation="unknown",
            source_role="unknown",
            publisher_id=None,
        )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 1.0
    assert (
        metric_score(output, work_role_case, "independent_work_recognized")
        == 0.0
    )


def test_a_reprint_with_an_unknown_relation_and_its_own_publisher_is_a_new_work(
    work_role_case, work_role_output
) -> None:
    """The shape production actually emits, scoring the case's declared risk.

    ``validated_transport_relation`` downgrades a claimed mirror or
    syndication to ``unknown`` whenever the read does not evidence the
    issuer, while ``publisher_id`` is assigned from the read identity
    regardless — so a reprint whose page names nobody arrives as a
    non-derivative relation carrying a distinct publisher. Three publishers
    for one report is verbatim this case's stated risk.
    """
    mirror = work_role_case.expectations.reference["same_work_urls"][1]
    output = work_role_output.with_source_identity(
        mirror,
        transport_relation="unknown",
        publisher_id="repository.example.org",
    )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 0.0


def test_a_mirror_row_stamping_its_own_publisher_is_a_new_work(
    work_role_case, work_role_output
) -> None:
    """The relation a copy records never licenses a publisher of its own.

    A row whose relation is ``mirror`` was skipped before its publisher was
    ever compared, so a repository record that stamped the *serving host* as
    the publisher of a page it merely copies passed this metric — which is
    the rubric's failure verbatim ("The serving host is recorded as the
    publisher"). No production consumer gives a copy the original's
    publisher by relation alone: a copy carrying a different evidenced
    issuer keeps its own publisher on record, and one work must not read as
    two however the relation is spelled.
    """
    reference = work_role_case.expectations.reference
    mirror = reference["same_work_urls"][1]
    assert "mirror" in reference["derivative_relations"]
    output = work_role_output.with_source_identity(
        mirror,
        transport_relation="mirror",
        source_role="original_report",
        publisher_id="repository.example.org",
    )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 0.0


def test_a_copy_recorded_as_an_original_publication_is_a_new_work(
    work_role_case, work_role_output
) -> None:
    """The rubric's second failure half: a copy presented as an original.

    ``transport_vs_publication`` names two failures — "The serving host is
    recorded as the publisher, or a copy is recorded as an original
    publication" — and the metric compared publishers only, so a declared
    same-work row that kept the survey's identity and publisher but labelled
    itself ``original_report`` scored full marks. A publisher is not the only
    way a page claims to be a work of its own: the role is that claim, and a
    copy asserting one is the second original this metric exists to refuse.
    """
    mirror = work_role_case.expectations.reference["same_work_urls"][1]
    output = work_role_output.with_source_identity(
        mirror,
        transport_relation="mirror",
        source_role="original_report",
        publisher_id=None,
    )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 0.0


def test_a_copy_claiming_nothing_and_naming_no_publisher_is_not_a_new_work(
    work_role_case, work_role_output
) -> None:
    """An unlabelled copy is not itself the defect; the claim is.

    A row that records no publisher asserted no new identity, and one whose
    role is ``derivative`` asserts no originality either — unknown identity
    can establish neither sameness nor independence, which is why refusing
    that shape is ``independent_work_recognized``'s job. What this metric
    refuses is the claim, whether it is made with a publisher or with a role,
    so a row making neither still passes.
    """
    mirror = work_role_case.expectations.reference["same_work_urls"][1]
    output = work_role_output.with_source_identity(
        mirror,
        transport_relation="mirror",
        source_role="derivative",
        publisher_id=None,
    )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 1.0


def test_an_unknown_relation_carrying_the_original_publisher_is_not_a_new_work(
    work_role_case, work_role_output
) -> None:
    """An unknown relation is not itself the defect; the publisher decides.

    The same downgraded row recording the institute as its publisher is the
    same work however it was served: the relation went unknown, the identity
    did not. Failing it would punish a run that correctly inherited the
    publisher across a page that happens not to evidence its issuer.
    """
    reference = work_role_case.expectations.reference
    original, mirror, _wire = reference["same_work_urls"]
    original_publisher = _evaluated_row(work_role_output, original)["publisher_id"]
    output = work_role_output.with_source_identity(
        mirror,
        transport_relation="unknown",
        publisher_id=original_publisher,
    )

    assert metric_score(output, work_role_case, "mirror_not_a_new_work") == 1.0


# --- Task 12: upstream independence -----------------------------------------


def test_an_upstream_independent_pair_run_scores_its_metrics_one(
    upstream_pair_case, upstream_pair_output
) -> None:
    for metric_id in (
        "verdict_correctness",
        "no_false_independent_pair",
        "independence_enforced",
        "sources_known",
    ):
        assert (
            metric_score(upstream_pair_output, upstream_pair_case, metric_id)
            == 1.0
        ), metric_id
    assert (
        deterministic_quality(
            upstream_pair_output,
            upstream_pair_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        == 1.0
    )


def test_one_publishers_two_accounts_are_not_an_independent_pair(
    upstream_pair_case, upstream_pair_output
) -> None:
    """Flavour 1 of the Bug 1 defect class: the agency's order and that same
    agency's press release. Both rules see it — no publisher may corroborate
    itself — while the ordinary citation gate passes, because both URLs were
    legitimately retrieved."""
    reference = upstream_pair_case.expectations.reference
    output = upstream_pair_output.with_claim_fields(
        0, verdict="verified", evidence_status="verified_pair"
    ).with_claim_passages(0, reference["same_publisher_urls"])

    assert (
        gate(
            evaluate_general_gates(output, upstream_pair_case, secrets=()),
            "citations_known",
        ).passed
        is True
    )
    assert (
        metric_score(output, upstream_pair_case, "no_false_independent_pair")
        == 0.0
    )
    assert (
        metric_score(output, upstream_pair_case, "independence_enforced")
        == 0.0
    )


def test_a_second_domain_vouching_only_for_attribution_is_not_a_pair(
    upstream_pair_case, upstream_pair_output
) -> None:
    """Flavour 2, and the reason ``no_false_independent_pair`` is not a
    second name for the domain count.

    The mutated trap claim rests on the commission's order and the policy
    lab's page: two genuinely distinct registrable domains, neither of them
    an origin the claim was taken from, so ``independence_enforced`` PASSES —
    domain arithmetic cannot see that the lab's page vouches only for who
    said it, carrying neither the value nor the period the claim needs.
    ``no_false_independent_pair`` FAILS, because that claim is one the case
    declares must not be verified however many domains its passages span.
    The two metrics disagree here by design; a metric that only ever agreed
    with the domain count would be a duplicate and this case would not need
    it.
    """
    reference = upstream_pair_case.expectations.reference
    agency = reference["same_publisher_urls"][0]
    lab = reference["dimension_only_urls"][0]
    output = upstream_pair_output.with_claim_fields(
        0, verdict="verified", evidence_status="verified_pair"
    ).with_claim_passages(0, [agency, lab])

    assert (
        metric_score(output, upstream_pair_case, "independence_enforced") == 1.0
    )
    assert (
        metric_score(output, upstream_pair_case, "no_false_independent_pair")
        == 0.0
    )


def test_abstaining_on_every_claim_scores_verdict_correctness_zero(
    upstream_pair_case, upstream_pair_output
) -> None:
    """The control claim is mandatory: a run that refuses to verify anything
    must not collect the case's weight by staying silent."""
    output = upstream_pair_output.with_claim_fields(
        0, verdict="insufficient_evidence", evidence_status=None, evidence=[]
    ).with_claim_fields(
        1, verdict="insufficient_evidence", evidence_status=None, evidence=[]
    )

    assert (
        metric_score(output, upstream_pair_case, "verdict_correctness") == 0.0
    )
    assert (
        metric_score(output, upstream_pair_case, "no_false_independent_pair")
        == 1.0
    )


def test_a_claim_the_case_requires_must_be_in_the_output(
    upstream_pair_case, upstream_pair_output
) -> None:
    """Abstention by omission is abstention, and a reworded claim is not it.

    The verdict map was read one way only: a claim named by the case was
    compared *if* the output carried it, and nothing failed when it did not.
    Dropping the required control claim — or returning no claims at all —
    left ``verdict_correctness``, ``no_false_independent_pair``,
    ``independence_enforced`` and ``sources_known`` all true on an empty
    list, so a run that answered nothing scored 1.0 on a case whose own text
    says such a run collects nothing. Rewording the claim is the same
    abstention wearing the claim's shape.
    """
    reference = upstream_pair_case.expectations.reference
    control = reference["required_verified_claims"][0]
    claims = list(upstream_pair_output.result["verified_claims"])
    control_index = next(
        index for index, claim in enumerate(claims) if claim["text"] == control
    )
    omitted = [
        claim for index, claim in enumerate(claims) if index != control_index
    ]
    reworded = [
        (
            {**claim, "text": f"{control} (as reported by the commission)"}
            if index == control_index
            else claim
        )
        for index, claim in enumerate(claims)
    ]

    assert (
        metric_score(
            upstream_pair_output, upstream_pair_case, "verdict_correctness"
        )
        == 1.0
    )
    for label, defective in (
        ("omitted", upstream_pair_output.with_claims(omitted)),
        ("empty", upstream_pair_output.with_claims([])),
        ("reworded", upstream_pair_output.with_claims(reworded)),
    ):
        assert (
            metric_score(defective, upstream_pair_case, "verdict_correctness")
            == 0.0
        ), label
        assert (
            deterministic_quality(
                defective,
                upstream_pair_case,
                metric_functions=METRIC_FUNCTIONS,
            )
            < 1.0
        ), label


# --- Task 12: canonical citation provenance ---------------------------------
#
# The synthesizer half of Task 7's risk: the report's references are composed
# by joining the evidence registry, so no URL reaches the reader that the run
# never held, and one work reprinted twice is one reference. The end-to-end
# proof of the same defect lives in ``test_real_agents``, where
# ``statement_source_urls`` is monkeypatched inside the full graph replay;
# these assert the property at the artifact level — the composed report itself
# never carries a URL the state cannot derive a citation from, and never
# prints two references for one work.


def _derived_reference_urls(case) -> list[str]:
    """The reference list production's collapse rule derives from the state."""
    derived = [
        citation.url
        for citation in build_citation_index(
            case.state.evaluated_sources, case.state.verified_claims
        )
    ]
    return collapse_mirror_urls(derived, case.state.evaluated_sources)


def test_a_canonically_cited_report_scores_its_metrics_one(
    canonical_report_case, canonical_report_output
) -> None:
    for metric_id in (
        "citations_locally_derived",
        "one_reference_per_work",
        "coverage",
        "limitations_present",
        "reader_markdown_present",
        "evidence_markdown_present",
    ):
        assert (
            metric_score(
                canonical_report_output, canonical_report_case, metric_id
            )
            == 1.0
        ), metric_id
    assert (
        deterministic_quality(
            canonical_report_output,
            canonical_report_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        == 1.0
    )


def test_an_invented_reference_url_is_not_locally_derived(
    canonical_report_case, canonical_report_output
) -> None:
    """A URL no assessed source and no checked claim carries.

    The known-source gate refuses it too, but for a different reason: that gate
    compares the report against the case's *declaration*, while this metric
    compares it against the records the run actually holds — which is the
    invariant Task 7's join enforces, since a reference is rendered from an
    evidence id and never from a URL the model supplied.
    """
    case = canonical_report_case
    invented = "https://journal.example/cover-crop-nitrate-reduction"
    output = canonical_report_output.with_references(
        [*_derived_reference_urls(case), invented]
    )

    assert metric_score(output, case, "citations_locally_derived") == 0.0
    assert metric_score(output, case, "one_reference_per_work") == 0.0
    assert (
        gate(
            evaluate_general_gates(output, case, secrets=()),
            "citations_known",
        ).passed
        is False
    )


def test_a_work_printed_twice_is_one_reference_not_two(
    canonical_report_case, canonical_report_output
) -> None:
    """The two new metrics disagree here by design.

    Both copies are URLs the run really retrieved, so both are locally derived
    and the provenance metric passes: nothing was invented. What is wrong is
    identity — one work printed as two references — and only the metric that
    applies production's collapse rule sees it. A metric that only ever agreed
    with ``citations_locally_derived`` would be a duplicate of it.
    """
    case = canonical_report_case
    canonical, reprint = case.expectations.reference["mirror_pairs"][0]
    listed = [source.url for source in case.state.evaluated_sources]
    output = canonical_report_output.with_references(listed)

    assert canonical in listed and reprint in listed
    assert len(listed) == len(_derived_reference_urls(case)) + 1
    assert metric_score(output, case, "citations_locally_derived") == 1.0
    assert metric_score(output, case, "one_reference_per_work") == 0.0


def test_printing_the_reprint_instead_of_the_original_is_not_canonical(
    canonical_report_case, canonical_report_output
) -> None:
    """The other direction, and the reason neither metric is a URL count.

    The reprint is locally derived — the run retrieved it — so provenance
    passes, and the reference list still is not the one the composition
    derives: the copy the work's own assessment identifies as the original is
    what a reader is owed, and printing the republished copy instead loses the
    reference to the work itself.
    """
    case = canonical_report_case
    canonical, reprint = case.expectations.reference["mirror_pairs"][0]
    listed = [
        reprint if url == canonical else url
        for url in _derived_reference_urls(case)
    ]
    output = canonical_report_output.with_references(listed)

    assert reprint in listed and canonical not in listed
    assert metric_score(output, case, "citations_locally_derived") == 1.0
    assert metric_score(output, case, "one_reference_per_work") == 0.0


# --- Task 12: typed gap calibration -----------------------------------------


def _fixture_gap(typed_gap_output) -> dict:
    return typed_gap_output.result["critique"]["gaps"][0]


def test_a_typed_identity_gap_scores_its_metrics_one(
    typed_gap_case, typed_gap_output
) -> None:
    for metric_id in (
        "gap_kind_correct",
        "repair_action_routed",
        "conservative_score",
    ):
        assert (
            metric_score(typed_gap_output, typed_gap_case, metric_id) == 1.0
        ), metric_id
    assert (
        deterministic_quality(
            typed_gap_output,
            typed_gap_case,
            metric_functions=METRIC_FUNCTIONS,
        )
        == 1.0
    )


def test_the_typed_gap_fixture_types_the_defect_its_candidate_carries(
    typed_gap_case, typed_gap_output
) -> None:
    """The scripted gap is the case's own defect.

    It names the claim cluster the candidate's composition really carries, and
    it is typed and routed the way the case's reference declares. A proof built
    on a gap naming nothing the candidate has would only measure the fixture.
    """
    case = typed_gap_case
    cluster_id = CALIBRATION_CLUSTER_IDS["emissions"]
    reference = case.expectations.reference
    gap = _fixture_gap(typed_gap_output)

    assert gap["claim_cluster_ids"] == [cluster_id]
    assert cluster_id in case.state.composition.claim_clusters
    assert gap["kind"] in reference["expected_gap_kinds"]
    assert gap["repair_action"] in reference["expected_repair_actions"]


def test_a_false_pair_typed_as_coverage_routes_the_run_the_wrong_way(
    typed_gap_case, typed_gap_output
) -> None:
    """The misroute in its most literal form.

    The same rejection, the same score, and the same cluster — but typed as a
    coverage hole and routed to acquisition, with a target and a query the run
    may search for. That route sends the Researcher to fetch pages for a pair
    that already sits in the state, and no number of pages closes a defect
    about what those pages *are*. The typing and the route are what this case
    scores apart from the score itself.
    """
    case = typed_gap_case
    mistyped = {
        **_fixture_gap(typed_gap_output),
        "kind": "coverage",
        "repair_action": "acquire",
        "claim_cluster_ids": [],
        "target_ids": [CALIBRATION_TARGET_IDS["emissions"]],
        "recommended_queries": ["clinker substitution independent verification"],
    }
    output = typed_gap_output.with_typed_gaps([mistyped])

    assert metric_score(output, case, "gap_kind_correct") == 0.0
    assert metric_score(output, case, "repair_action_routed") == 0.0
    # The rejection is still calibrated — only the diagnosis is wrong, and a
    # case that scored the two together could not say which happened.
    assert metric_score(output, case, "conservative_score") == 1.0


def test_approving_the_false_pair_scores_every_metric_zero(
    typed_gap_case, typed_gap_output
) -> None:
    """The anti-abstention proof, at the artifact the case must refuse.

    A confident approval that names no defect: no material gap for either
    metric to read, and a score above the band's ceiling. Missing the defect
    has to cost every point this case offers, or abstaining from a judgement
    would be the cheapest way to collect them.
    """
    output = typed_gap_output.with_typed_gaps([]).with_score(9).with_should_continue(
        False
    )

    for metric_id in (
        "gap_kind_correct",
        "repair_action_routed",
        "conservative_score",
    ):
        assert metric_score(output, typed_gap_case, metric_id) == 0.0, metric_id


def test_a_correctly_typed_gap_against_the_wrong_cluster_scores_zero(
    typed_gap_case, typed_gap_output
) -> None:
    """The gap has to name the defect, not merely be phrased like it.

    The case's stated purpose is that "the gap that answers it is therefore
    typed identity" — the identity defect is the false pair in the emissions
    cluster. Both typed metrics read only a gap's kind and its route, so a
    review that raised a correctly typed identity defect against an unrelated
    claim — and never mentioned the pair the case exists to carry — collected
    their full weight. The typing and the route are what the case scores, and
    both are answers *about* an obligation.
    """
    case = typed_gap_case
    misplaced = {
        **_fixture_gap(typed_gap_output),
        "claim_cluster_ids": [CALIBRATION_CLUSTER_IDS["cost"]],
    }
    output = typed_gap_output.with_typed_gaps([misplaced])

    assert metric_score(output, case, "gap_kind_correct") == 0.0
    assert metric_score(output, case, "repair_action_routed") == 0.0
    # The rejection is still calibrated — only its target is wrong, and the
    # case scores the score separately for exactly that reason.
    assert metric_score(output, case, "conservative_score") == 1.0


def test_answering_the_false_pair_at_the_floor_is_not_calibrated(
    typed_gap_case, typed_gap_output
) -> None:
    """The band's floor is load-bearing, and this is its proof.

    The defect is real and not fatal: it is a rejection, not a run that failed
    to produce a report, and a review that answers it by collapsing to the
    floor has stopped grading the report at all. That is a different failure
    from accepting it — one the ceiling alone cannot see — and the case
    declares both bounds so the two are told apart.
    """
    output = typed_gap_output.with_score(1)

    assert metric_score(output, typed_gap_case, "conservative_score") == 0.0
    assert metric_score(output, typed_gap_case, "gap_kind_correct") == 1.0
    assert metric_score(output, typed_gap_case, "repair_action_routed") == 1.0

