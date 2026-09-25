"""The Report Writer's evaluation cases (spec §6.1-6.2, D8).

Each controlled case is driven through the real ``ReportWriterAgent`` (the
``report_writer_output_for`` fixture) with a ``ScriptedCompleter`` answering
both structured calls the writer makes — its draft and, after drafting, the
Statement Check — so a case's declared behaviour is what the agent does. The
writer's five agent gates are then exercised on the reference output and on a
mutated copy, and its metrics on the states they are supposed to separate.
"""

from __future__ import annotations

import re

import pytest

from deep_research.agents.evidence_verifier import (
    StatementCheckDraft,
    StatementVerdictDraft,
)
from deep_research.agents.report import collapse_mirror_urls
from deep_research.agents.report_writer import (
    ReportWriterDraft,
    WriterPointDraft,
)
from deep_research.agents.sources import normalize_source_url
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
from deep_research.evaluation.models import TargetOutput

CONTROLLED = (
    "complete-cited-report",
    "conflict-and-limitations",
    "composition-no-publication",
    "canonical-evidence-report",
)
LIVE = "report-writer-live-report"

GATES = (
    "valid_report",
    "citations_known_only",
    "refusals_logged",
    "no_persistence_calls",
    "no_false_publication_claim",
)

_READER_URL_PATTERN = re.compile(r"^(\d+)\.\s+.*?\s+—\s+(\S+)\s*$", re.M)


def _case(case_id: str, tier: str = "controlled"):
    return case_by_id("report_writer", tier, case_id)


def _gates(output: TargetOutput, case) -> dict[str, bool]:
    return {
        result.gate_id: result.passed
        for result in evaluate_agent_gates(output, case)
    }


def _scores(output: TargetOutput, case) -> dict[str, float]:
    return dict(
        deterministic_metric_scores(
            output, case, metric_functions=METRIC_FUNCTIONS
        )
    )


def _composition(output: TargetOutput) -> dict:
    return dict((output.result or {}).get("composition") or {})


def _listed_reference_urls(output: TargetOutput) -> set[str]:
    return {
        normalize_source_url(url)
        for _, url in _READER_URL_PATTERN.findall(
            str((output.result or {}).get("markdown") or "")
        )
    }


# --- the registry's declared inventory --------------------------------------


def test_the_declared_inventory_matches_the_module() -> None:
    assert (
        tuple(case.case_id for case in cases_for("report_writer", "controlled"))
        == EXPECTED_CONTROLLED_CASE_IDS["report_writer"]
    )
    assert (
        tuple(case.case_id for case in cases_for("report_writer", "live"))
        == EXPECTED_LIVE_CASE_IDS["report_writer"]
    )
    assert EXPECTED_CONTROLLED_CASE_IDS["report_writer"] == CONTROLLED
    assert EXPECTED_LIVE_CASE_IDS["report_writer"] == (LIVE,)


def test_every_case_is_a_report_writer_case() -> None:
    for case_id in CONTROLLED:
        case = _case(case_id)
        assert case.agent_name == "report_writer"
        assert case.tier == "controlled"
    live = _case(LIVE, "live")
    assert live.agent_name == "report_writer"
    assert live.tier == "live"


def test_every_controlled_scenario_scripts_no_service() -> None:
    """The writer publishes nothing in a controlled pass, so nothing is scripted."""
    for case_id in CONTROLLED:
        case = _case(case_id)
        script = SCENARIOS[case.dependency_scenario]
        assert script.search_responses == {}, case_id
        assert script.http_pages == {}, case_id
        assert script.failures == {}, case_id
    assert _case(LIVE, "live").dependency_scenario == "live"


def test_every_case_declares_the_writer_gates_and_metrics() -> None:
    assert AGENT_GATE_IDS["report_writer"] == GATES
    for case in cases_for("report_writer", "controlled") + cases_for(
        "report_writer", "live"
    ):
        declared = {
            metric.metric_id for metric in case.expectations.deterministic_metrics
        }
        assert declared <= set(METRIC_FUNCTIONS), case.case_id
        assert sum(
            metric.weight for metric in case.expectations.deterministic_metrics
        ) == pytest.approx(1.0), case.case_id
        assert case.expectations.required_output_fields == [
            "markdown",
            "evidence_markdown",
        ]


def test_every_seeded_finding_carries_its_verification() -> None:
    """The writer may only cite verified findings, so every fixture is one."""
    for case in cases_for("report_writer", "controlled") + cases_for(
        "report_writer", "live"
    ):
        assert case.state.verified_findings, case.case_id
        for finding in case.state.verified_findings:
            verification = finding.verification
            assert verification is not None, case.case_id
            if verification.status == "dropped":
                assert verification.dropped_reason, case.case_id
                continue
            assert any(result.kept for result in verification.figure_results) or (
                not verification.figure_results
            ), case.case_id


def test_every_seeded_figure_is_consistent_with_its_read() -> None:
    for case in cases_for("report_writer", "controlled"):
        for finding in case.state.verified_findings:
            verification = finding.verification
            assert verification is not None
            for result in verification.figure_results:
                if not result.kept:
                    continue
                assert result.context is not None, case.case_id
                if result.evidence_words and finding.read_id:
                    read = case.state.read_records[finding.read_id]
                    assert result.evidence_words in " ".join(
                        read.passages.values()
                    ), case.case_id


# --- the controlled cases, driven through the real agent --------------------


def test_a_complete_case_composes_both_artifacts_and_passes_every_gate(
    report_writer_output_for,
) -> None:
    case = _case("complete-cited-report")
    output = report_writer_output_for(case)

    assert output.completed is True
    assert output.result["markdown"].strip()
    assert output.result["evidence_markdown"].strip()
    assert _gates(output, case) == {gate: True for gate in GATES}
    scores = _scores(output, case)
    assert all(score == 1.0 for score in scores.values()), scores


def test_the_complete_case_names_its_unanswered_target(
    report_writer_output_for,
) -> None:
    """§6.1 item 5: a required target nothing answers is listed, not dropped."""
    case = _case("complete-cited-report")
    unanswered = case.expectations.reference["unanswered_target_id"]
    output = report_writer_output_for(case)

    composition = _composition(output)
    assert [row["target_id"] for row in composition["not_found"]] == [unanswered]
    assert "## Not found" in output.result["markdown"]
    assert unanswered in str(composition)

    mutated = output.model_copy(
        update={
            "result": {
                **dict(output.result),
                "composition": {**composition, "not_found": []},
            }
        }
    )
    assert _gates(mutated, case)["valid_report"] is False


def test_the_reference_list_names_only_pages_the_findings_carry(
    report_writer_output_for,
) -> None:
    case = _case("complete-cited-report")
    output = report_writer_output_for(case)

    cited = _listed_reference_urls(output)
    allowed = {
        normalize_source_url(finding.source_url)
        for finding in case.state.verified_findings
    }
    assert cited
    assert cited <= allowed
    assert _scores(output, case)["citations_locally_derived"] == 1.0

    mutated = output.model_copy(
        update={
            "result": {
                **dict(output.result),
                "markdown": output.result["markdown"]
                + "\n\nSee https://invented.example/report for details.",
            }
        }
    )
    assert _gates(mutated, case)["citations_known_only"] is False


def test_a_dropped_finding_is_published_with_its_reason(
    report_writer_output_for,
) -> None:
    """The evidence log carries every drop, and the header counts it."""
    case = _case("complete-cited-report")
    expected_reason = case.expectations.reference["dropped_finding_reason"]
    output = report_writer_output_for(case)

    evidence = output.result["evidence_markdown"]
    assert f"dropped ({expected_reason})" in evidence
    assert "1 dropped" in output.result["markdown"]
    assert _gates(output, case)["refusals_logged"] is True


def test_a_refused_sentence_is_published_in_full_with_its_reason(
    report_writer_output_for,
) -> None:
    """§6.1 item 7 and D8: the check's refusal reaches the reader's log."""
    case = _case("conflict-and-limitations")
    conflated = (
        "Both forecasters expect 19.6 GW of battery storage additions in 2025."
    )
    refused_reason = "states one forecaster's figure as both forecasters'"

    def checker(messages, schema):
        del schema
        statements = []
        for label, sentence in re.findall(
            r"## (S\d+)\nsentence: (.*)", messages[-1].content
        ):
            verdict = (
                "inconsistent"
                if sentence.strip() == conflated
                else "consistent"
            )
            statements.append(
                StatementVerdictDraft(
                    label=label,
                    verdict=verdict,
                    reason=refused_reason
                    if verdict == "inconsistent"
                    else "consistent with its findings",
                )
            )
        return StatementCheckDraft(statements=statements)

    draft = ReportWriterDraft(
        executive_summary=[
            WriterPointDraft(
                text="The EIA expects 19.6 GW of utility-scale additions in 2025.",
                finding_labels=["F01"],
            ),
            WriterPointDraft(
                text=(
                    "Wood Mackenzie forecasts 13.3 GW of grid-scale "
                    "installations in 2025."
                ),
                finding_labels=["F02"],
            ),
            WriterPointDraft(text=conflated, finding_labels=["F01", "F02"]),
        ],
        sections=[],
    )
    output = report_writer_output_for(case, draft=draft, checker=checker)

    composition = _composition(output)
    [refused] = composition["rejected_points"]
    assert refused["text"] == conflated
    assert refused["reason"] == refused_reason
    evidence = output.result["evidence_markdown"]
    assert "## Refused sentences" in evidence
    assert conflated in " ".join(evidence.split())
    assert refused_reason in evidence
    # The refused sentence never reaches the reader's report, while both
    # forecaster's figures stay cited: the disagreement is kept, not smoothed.
    assert conflated not in output.result["markdown"]
    assert _listed_reference_urls(output) == {
        normalize_source_url(url)
        for url in case.expectations.reference["conflicting_urls"]
    }
    assert _scores(output, case)["conflicting_figures_published"] == 1.0
    assert all(score == 1.0 for score in _scores(output, case).values()), _scores(
        output, case
    )

    # A log that dropped the refusal fails the gate: an unexplained refusal is
    # a fact quietly removed.
    assert _gates(output, case)["refusals_logged"] is True
    without = output.without_refused_sentences()
    assert _gates(without, case)["refusals_logged"] is False


def test_a_composition_pass_claims_no_publication(report_writer_output_for) -> None:
    case = _case("composition-no-publication")
    output = report_writer_output_for(case)

    assert _gates(output, case) == {gate: True for gate in GATES}
    assert _scores(output, case)["no_persistence_calls"] == 1.0

    published = output.with_publication_path("report-composition-1.md")
    assert _gates(published, case)["no_false_publication_claim"] is False

    writing = output.with_persistence_call("write_document")
    assert _gates(writing, case)["no_persistence_calls"] is False


def test_the_canonical_case_prints_one_reference_per_work(
    report_writer_output_for,
) -> None:
    case = _case("canonical-evidence-report")
    mirror = case.expectations.reference["mirror_url"]
    output = report_writer_output_for(case)

    listed = _listed_reference_urls(output)
    derived = [
        normalize_source_url(source.url) for source in case.state.evaluated_sources
    ]
    canonical = {
        normalize_source_url(url)
        for url in collapse_mirror_urls(derived, case.state.evaluated_sources)
    }
    assert normalize_source_url(mirror) not in listed
    assert listed == canonical
    assert _scores(output, case)["one_reference_per_work"] == 1.0

    # Printing the mirror as a second reference is the failure this case
    # exists to catch.
    printing_both = output.with_references([*sorted(listed), mirror])
    assert _scores(printing_both, case)["one_reference_per_work"] == 0.0
    # And an invented URL is not locally derived even before it is collapsed.
    assert _scores(
        output.with_references([*sorted(listed), "https://invented.example/x"]),
        case,
    )["citations_locally_derived"] == 0.0


def test_the_reader_report_renders_its_structural_sections(
    report_writer_output_for,
) -> None:
    """§6.1 items 1-6 are structure, so the writer renders them itself."""
    for case_id in CONTROLLED:
        case = _case(case_id)
        output = report_writer_output_for(case)
        report = output.result["markdown"]
        assert report.startswith(f"# {case.state.original_question}"), case_id
        for heading in (
            "## Executive summary",
            "## Key facts",
            "## Sources",
        ):
            assert heading in report, case_id
        assert "As of" in report, case_id


def test_every_printed_statement_cites_a_known_label(
    report_writer_output_for,
) -> None:
    """§6.2's rule, read back from the artifact."""
    case = _case("conflict-and-limitations")
    output = report_writer_output_for(case)

    composition = _composition(output)
    labels = composition["finding_labels"]
    statements = [
        point["statement"]
        for point in [*composition["summary"]]
        + [
            point
            for section in composition["sections"]
            for point in section["points"]
        ]
    ]
    assert statements
    for statement in statements:
        assert statement["finding_ids"]
        assert set(statement["finding_ids"]) <= set(labels.values())
    assert _scores(output, case)["statements_labelled"] == 1.0

    unlabelled = output.model_copy(
        update={
            "result": {
                **dict(output.result),
                "composition": {
                    **composition,
                    "summary": [
                        {
                            **point,
                            "statement": None,
                        }
                        for point in composition["summary"]
                    ],
                },
            }
        }
    )
    assert _scores(unlabelled, case)["statements_labelled"] == 0.0


def test_the_live_case_is_a_report_writer_live_case() -> None:
    case = _case(LIVE, "live")
    assert case.expectations.reference["known_citation_urls"] == list(
        case.expectations.known_source_urls
    )
    assert len(case.state.verified_findings) == 3
    assert all(
        finding.verification is not None
        for finding in case.state.verified_findings
    )
