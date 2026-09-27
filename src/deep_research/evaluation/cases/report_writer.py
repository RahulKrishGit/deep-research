"""Report Writer evaluation cases: cited prose over verified findings.

Every case states the same thing in two artifacts — the reader report and the
evidence log — from one composition. The findings are already verified (the
Evidence Verifier's judgement is part of the fixture, because the writer never
judges a figure itself), the targets are the obligations the report has to
answer or list as not found, and the graded behaviour is what the writer
composes from them: prose citing known labels only, a reference list derived
from its own records, every refusal published with its reason, and no
publication claim about artifacts no run wrote.

The Statement Check's verdicts are scripted by the tests that drive these
cases: the writer calls the checker after drafting (D8), so a controlled run
supplies both replies.
"""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    context,
    dropped,
    evaluation_state,
    figure,
    finding,
    kept,
    metrics,
    read_record,
    rubric,
    scored_source,
    sub_topic,
    target,
    verified,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase
from deep_research.utils.types import MemorySnapshot

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would let
# one case's mutations leak into the others. The report-writing dimensions are
# shared as plain tuples — rubric() copies them into a fresh JudgeRubric per
# call — so the dimension text is written once, not verbatim in all five
# rubrics.

_REPORT_DIMENSIONS = (
    (
        "evidence_fidelity",
        "The report says only what the verified findings support.",
        "Sections and sentences stay within the verified findings and their "
        "confirmed figure contexts; nothing is invented.",
        "The report asserts material no verified finding supports.",
    ),
    (
        "citation_faithfulness",
        "Every citation resolves to a page the report's own findings cite.",
        "Every cited URL belongs to a verified finding, and the reference "
        "list prints each work once.",
        "Citations are invented, mismatched, or outside the run's evidence.",
    ),
    (
        "refusal_honesty",
        "A drafted sentence the check refused, and every dropped figure, is "
        "published with its reason.",
        "The evidence log prints every refused sentence and every dropped "
        "figure with the reason recorded for it.",
        "A refusal or a drop is hidden, or published without a reason.",
    ),
)

_STATEMENT_DIMENSIONS = (
    (
        "statement_discipline",
        "Every printed sentence states only what its cited findings state.",
        "Each sentence's numbers, dates, scope, organisation and forecast or "
        "actual match the findings it cites.",
        "A sentence overstates, conflates two findings, or drops a caveat.",
    ),
)

_COMPLETE_RUBRIC = rubric("report-writer-complete-report", *_REPORT_DIMENSIONS)

_CONFLICT_RUBRIC = rubric(
    "report-writer-conflict-limitations",
    *_REPORT_DIMENSIONS,
    *_STATEMENT_DIMENSIONS,
)

_COMPOSITION_RUBRIC = rubric(
    "report-writer-composition-only",
    *_REPORT_DIMENSIONS,
)

_CANONICAL_RUBRIC = rubric(
    "report-writer-canonical-evidence",
    *_REPORT_DIMENSIONS,
)

_LIVE_RUBRIC = rubric("report-writer-live-report", *_REPORT_DIMENSIONS)

# All URLs are written in the normalized form the agent records (no ``www.``,
# no trailing slash), so a gate comparison can never byte-mismatch.

_EIA_URL = "https://eia.gov/todayinenergy/detail.php?id=64705"
_WOODMAC_URL = "https://woodmac.com/press-releases/2025-us-energy-storage"
_RELAY_URL = "https://utilitydive.com/news/storage-2025"
_COMPLETE_URLS = (_EIA_URL, _WOODMAC_URL, _RELAY_URL)

_EIA_TITLE = (
    "U.S. battery capacity increased 66% in 2024 | U.S. Energy "
    "Information Administration"
)
_EIA_PAGE = (
    "U.S. battery capacity increased 66% in 2024. Generators added 10.4 "
    "gigawatts (GW) of new battery storage capacity in 2024, the second-largest "
    "generating capacity addition after solar, according to our January 2025 "
    "Preliminary Monthly Electric Generator Inventory. In 2025, capacity growth "
    "from battery storage could set a record as operators report plans to add "
    "19.6 GW of utility-scale battery storage to the grid."
)
_EIA_ACTUAL_SNIPPET = (
    "Generators added 10.4 gigawatts (GW) of new battery storage capacity in "
    "2024"
)
_EIA_FORECAST_SNIPPET = (
    "In 2025, capacity growth from battery storage could set a record as "
    "operators report plans to add 19.6 GW of utility-scale battery storage to "
    "the grid"
)

_WOODMAC_TITLE = "2025 storage record | Wood Mackenzie"
_WOODMAC_PAGE = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery "
    "energy storage system installations in 2025, a 52% increase over 2024, "
    "across all segments. Grid-scale storage installations are forecasted to "
    "reach 13.3 GW in 2025."
)
_WOODMAC_SNIPPET = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery "
    "energy storage system installations in 2025"
)
_WOODMAC_SCOPE_SNIPPET = (
    "Grid-scale storage installations are forecasted to reach 13.3 GW in 2025."
)

_RELAY_TITLE = "Storage in 2025 | Utility Dive"
_RELAY_PAGE = (
    "According to Wood Mackenzie, utility-scale installations reached 16 GW "
    "in 2025. Analysts expect further growth."
)
_RELAY_SNIPPET = (
    "According to Wood Mackenzie, utility-scale installations reached 16 GW "
    "in 2025."
)

_EIA_ORGANISATION = "U.S. Energy Information Administration"
_WOODMAC_ORGANISATION = "Wood Mackenzie"

# --- Case 1: a complete, cited report --------------------------------------


_COMPLETE_EIA_READ = read_record(
    _EIA_URL,
    case_id="complete-cited-report",
    title=_EIA_TITLE,
    text=_EIA_PAGE,
)
_COMPLETE_WOODMAC_READ = read_record(
    _WOODMAC_URL,
    case_id="complete-cited-report",
    title=_WOODMAC_TITLE,
    text=_WOODMAC_PAGE,
)
_COMPLETE_RELAY_READ = read_record(
    _RELAY_URL,
    case_id="complete-cited-report",
    title=_RELAY_TITLE,
    text=_RELAY_PAGE,
)

_COMPLETE = build_case(
    case_id="complete-cited-report",
    agent_name="report_writer",
    tier="controlled",
    title="Compose a cited report over verified findings",
    purpose=(
        "Three verified findings answer three required targets — the EIA's "
        "2024 actual, its 2025 forecast as relayed by Utility Dive, and Wood "
        "Mackenzie's market-wide 2025 figure — and a fourth finding was "
        "dropped, so the report has a real header count to carry. Every "
        "printed sentence cites a label the registry built, the reference "
        "list is derived from the findings' own URLs, and no persistence tool "
        "is called: publication is the terminal pass's job."
    ),
    state=evaluation_state(
        case_id="complete-cited-report",
        question=(
            "How much battery storage capacity was added in the United States "
            "in 2024, and what is forecast for 2025?"
        ),
        sub_topics=(
            sub_topic(
                "U.S. battery storage capacity additions",
                rationale=(
                    "The EIA's own inventory states the 2024 actual, which is "
                    "the question's first half."
                ),
                queries=["us battery storage capacity additions 2024"],
                criteria=["An actual installation figure for 2024"],
                priority=1,
                targets=(
                    target(
                        "topic-01-target-01",
                        question="How much battery storage capacity was added in 2024?",
                        measure="battery storage power capacity added",
                        unit_dimension="power",
                        period="2024",
                        kind="actual",
                        geography="United States",
                    ),
                ),
            ),
            sub_topic(
                "Battery storage forecasts for 2025",
                rationale="The question's second half asks for the 2025 outlook.",
                queries=["us battery storage forecast 2025"],
                criteria=["A forecast figure for 2025"],
                priority=2,
                targets=(
                    target(
                        "topic-02-target-01",
                        question="What battery storage capacity is forecast for 2025?",
                        measure="battery storage power capacity forecast",
                        unit_dimension="power",
                        period="2025",
                        kind="forecast",
                        geography="United States",
                        organisation=_EIA_ORGANISATION,
                    ),
                ),
            ),
            sub_topic(
                "Market-wide storage figures",
                rationale=(
                    "The market total covers every segment, so it is a "
                    "different basis from the utility-scale forecast."
                ),
                queries=["us energy storage market total 2025"],
                criteria=["A market-wide installation figure"],
                priority=3,
                targets=(
                    target(
                        "topic-03-target-01",
                        question=(
                            "What did the whole U.S. storage market "
                            "install in 2025?"
                        ),
                        measure="battery energy storage system installations",
                        unit_dimension="power",
                        period="2025",
                        kind="actual",
                        geography="United States",
                        organisation=_WOODMAC_ORGANISATION,
                    ),
                    # No seeded finding answers this obligation, so an honest
                    # report has to list it under Not found: §6.1 item 5 either
                    # answers a required target or says it could not.
                    target(
                        "topic-03-target-02",
                        question=(
                            "How much did grid-scale installations add on "
                            "their own in 2025?"
                        ),
                        measure="grid-scale battery storage installations",
                        unit_dimension="power",
                        period="2025",
                        kind="actual",
                        geography="United States",
                        organisation=_WOODMAC_ORGANISATION,
                    ),
                ),
            ),
        ),
        reads=(_COMPLETE_EIA_READ, _COMPLETE_WOODMAC_READ, _COMPLETE_RELAY_READ),
        verified_findings=(
            verified(
                finding(
                    _EIA_ACTUAL_SNIPPET,
                    url=_EIA_URL,
                    title=_EIA_TITLE,
                    sub_topic_title="U.S. battery storage capacity additions",
                    snippet=_EIA_ACTUAL_SNIPPET,
                    read_id=_COMPLETE_EIA_READ.read_id,
                    locator="body",
                    figures=(figure("10.4", "GW", period="2024", kind="actual"),),
                    target_ids=("topic-01-target-01",),
                    data_period="2024",
                    statement_date="March 12, 2025",
                    release_date="January 2025",
                ),
                (
                    kept(
                        figure("10.4", "GW", period="2024", kind="actual"),
                        context(
                            organisation=_EIA_ORGANISATION,
                            kind="actual",
                            period="2024",
                        ),
                        evidence_words=(
                            "Generators added 10.4 gigawatts (GW) of new "
                            "battery storage capacity in 2024"
                        ),
                    ),
                ),
                status="verified",
            ),
            verified(
                finding(
                    _EIA_FORECAST_SNIPPET,
                    url=_RELAY_URL,
                    title=_RELAY_TITLE,
                    sub_topic_title="Battery storage forecasts for 2025",
                    snippet=_RELAY_SNIPPET,
                    read_id=_COMPLETE_RELAY_READ.read_id,
                    locator="body",
                    figures=(figure("16", "GW", period="2025", kind="forecast"),),
                    target_ids=("topic-02-target-01",),
                    data_period="2025",
                    attributed_issuer=_EIA_ORGANISATION,
                ),
                (
                    kept(
                        figure("16", "GW", period="2025", kind="forecast"),
                        context(
                            organisation=_EIA_ORGANISATION,
                            kind="forecast",
                            attribution="relayed",
                            period="2025",
                        ),
                        evidence_words=(
                            "According to Wood Mackenzie, utility-scale "
                            "installations reached 16 GW in 2025."
                        ),
                    ),
                ),
                status="verified",
            ),
            verified(
                finding(
                    _WOODMAC_SNIPPET,
                    url=_WOODMAC_URL,
                    title=_WOODMAC_TITLE,
                    sub_topic_title="Market-wide storage figures",
                    snippet=_WOODMAC_SNIPPET,
                    read_id=_COMPLETE_WOODMAC_READ.read_id,
                    locator="body",
                    figures=(
                        figure("18.9", "gigawatts", period="2025", kind="actual"),
                    ),
                    target_ids=("topic-03-target-01",),
                    data_period="2025",
                    measure_scope="all segments",
                ),
                (
                    kept(
                        figure("18.9", "gigawatts", period="2025", kind="actual"),
                        context(
                            organisation=_WOODMAC_ORGANISATION,
                            kind="actual",
                            period="2025",
                            scope="all segments",
                        ),
                        evidence_words=_WOODMAC_SNIPPET,
                    ),
                ),
                status="verified",
            ),
            # A dropped finding stays in the snapshot and is printed as
            # dropped: the header counts it, and no sentence may cite it.
            verified(
                finding(
                    "U.S. storage installations reached 24 GW in 2025.",
                    url=_WOODMAC_URL,
                    title=_WOODMAC_TITLE,
                    sub_topic_title="Market-wide storage figures",
                    snippet="U.S. storage installations reached 24 GW in 2025.",
                    read_id=_COMPLETE_WOODMAC_READ.read_id,
                    locator="body",
                    figures=(figure("24", "GW", period="2025", kind="actual"),),
                    data_period="2025",
                ),
                (
                    dropped(
                        figure("24", "GW", period="2025", kind="actual"),
                        "evidence_not_on_page",
                        text="The passage does not state 24 GW.",
                    ),
                ),
                status="dropped",
                dropped_reason="all_figures_dropped",
            ),
        ),
        sources=(
            scored_source(
                _EIA_URL,
                title=_EIA_TITLE,
                authority=0.95,
                recency=0.90,
                relevance=0.95,
                overall=0.93,
                rationale="Federal statistical agency inventory page.",
                serving_host="eia.gov",
                publisher_id=_EIA_ORGANISATION,
                work_id=_EIA_URL,
                transport_relation="original",
            ),
            scored_source(
                _WOODMAC_URL,
                title=_WOODMAC_TITLE,
                authority=0.88,
                recency=0.90,
                relevance=0.90,
                overall=0.89,
                rationale="Market analyst's own release, market-wide basis.",
                serving_host="woodmac.com",
                publisher_id=_WOODMAC_ORGANISATION,
                work_id=_WOODMAC_URL,
                transport_relation="original",
            ),
            scored_source(
                _RELAY_URL,
                title=_RELAY_TITLE,
                authority=0.70,
                recency=0.88,
                relevance=0.85,
                overall=0.80,
                rationale=(
                    "Trade press relaying the EIA's forecast, not the "
                    "forecaster's own publication."
                ),
                serving_host="utilitydive.com",
                publisher_id="Utility Dive",
                work_id=_RELAY_URL,
                transport_relation="unknown",
            ),
        ),
        memory_context=MemorySnapshot(suggested_strategies=["cite every figure"]),
    ),
    dependency_scenario="report-writer-complete",
    expectations=CaseExpectations(
        required_output_fields=["markdown", "evidence_markdown"],
        reference={
            "known_citation_urls": list(_COMPLETE_URLS),
            "dropped_finding_reason": "all_figures_dropped",
            # The one required obligation nothing answers, which the report
            # has to name under Not found.
            "unanswered_target_id": "topic-03-target-02",
            "forbidden_publication_claims": [
                "saved to",
                "written to",
                "stored at",
                "published to",
            ],
        },
        known_source_urls=list(_COMPLETE_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "reader_markdown_present",
                0.15,
                "The result carries non-empty reader `markdown`.",
            ),
            (
                "evidence_markdown_present",
                0.15,
                "The result carries non-empty `evidence_markdown`.",
            ),
            (
                "citations_locally_derived",
                0.20,
                "Every reference in the report is a page the run's own "
                "verified findings cite.",
            ),
            (
                "statements_labelled",
                0.20,
                "Every printed statement cites at least one known finding "
                "label.",
            ),
            (
                "no_persistence_calls",
                0.20,
                "Neither persistence tool is called during composition.",
            ),
            (
                "no_false_publication_claim",
                0.10,
                "The composition does not claim either artifact was "
                "published.",
            ),
        ),
    ),
    judge_rubric=_COMPLETE_RUBRIC,
    metadata={"scenario": "normal"},
)


# --- Case 2: a conflict the report must not smooth -------------------------


_CONFLICT_EIA_READ = read_record(
    _EIA_URL,
    case_id="conflict-and-limitations",
    title=_EIA_TITLE,
    text=_EIA_PAGE,
)
_CONFLICT_WOODMAC_READ = read_record(
    _WOODMAC_URL,
    case_id="conflict-and-limitations",
    title=_WOODMAC_TITLE,
    text=_WOODMAC_PAGE,
)

_CONFLICT = build_case(
    case_id="conflict-and-limitations",
    agent_name="report_writer",
    tier="controlled",
    title="Represent disagreement without overstating it",
    purpose=(
        "Two verified forecasts disagree: the EIA projects 19.6 GW of "
        "utility-scale additions in 2025 and Wood Mackenzie 13.3 GW of "
        "grid-scale installations. A sentence that states one figure for "
        "both organisations is refused by the Statement Check, and an "
        "honest report keeps the two figures apart. The graded behaviour "
        "is that the refusal is published with its reason and the "
        "conflicting figures both stay citable."
    ),
    state=evaluation_state(
        case_id="conflict-and-limitations",
        question=(
            "What do forecasters expect for U.S. battery storage "
            "additions in 2025?"
        ),
        sub_topics=(
            sub_topic(
                "2025 battery storage forecasts by forecaster",
                rationale=(
                    "Two forecasters publish different bases, and the report "
                    "has to keep them apart rather than average them."
                ),
                queries=["us battery storage forecast 2025 by forecaster"],
                criteria=["Each forecaster's own figure, cited separately"],
                priority=1,
                targets=(
                    target(
                        "topic-01-target-01",
                        question="What does the EIA forecast for 2025?",
                        measure="utility-scale battery storage capacity additions",
                        unit_dimension="power",
                        period="2025",
                        kind="forecast",
                        geography="United States",
                        organisation=_EIA_ORGANISATION,
                    ),
                    target(
                        "topic-01-target-02",
                        question="What does Wood Mackenzie forecast for 2025?",
                        measure="grid-scale battery storage installations",
                        unit_dimension="power",
                        period="2025",
                        kind="forecast",
                        geography="United States",
                        organisation=_WOODMAC_ORGANISATION,
                    ),
                ),
            ),
        ),
        reads=(_CONFLICT_EIA_READ, _CONFLICT_WOODMAC_READ),
        verified_findings=(
            verified(
                finding(
                    _EIA_FORECAST_SNIPPET,
                    url=_EIA_URL,
                    title=_EIA_TITLE,
                    sub_topic_title="2025 battery storage forecasts by forecaster",
                    snippet=_EIA_FORECAST_SNIPPET,
                    read_id=_CONFLICT_EIA_READ.read_id,
                    locator="body",
                    figures=(figure("19.6", "GW", period="2025", kind="forecast"),),
                    target_ids=("topic-01-target-01",),
                    data_period="2025",
                    measure_scope="utility-scale",
                    statement_date="March 12, 2025",
                ),
                (
                    kept(
                        figure("19.6", "GW", period="2025", kind="forecast"),
                        context(
                            organisation=_EIA_ORGANISATION,
                            kind="forecast",
                            period="2025",
                            scope="utility-scale",
                        ),
                        evidence_words=_EIA_FORECAST_SNIPPET,
                    ),
                ),
                status="verified",
            ),
            verified(
                finding(
                    _WOODMAC_SCOPE_SNIPPET,
                    url=_WOODMAC_URL,
                    title=_WOODMAC_TITLE,
                    sub_topic_title="2025 battery storage forecasts by forecaster",
                    snippet=_WOODMAC_SCOPE_SNIPPET,
                    read_id=_CONFLICT_WOODMAC_READ.read_id,
                    locator="body",
                    figures=(figure("13.3", "GW", period="2025", kind="forecast"),),
                    target_ids=("topic-01-target-02",),
                    data_period="2025",
                    measure_scope="grid-scale",
                ),
                (
                    kept(
                        figure("13.3", "GW", period="2025", kind="forecast"),
                        context(
                            organisation=_WOODMAC_ORGANISATION,
                            kind="forecast",
                            period="2025",
                            scope="grid-scale",
                        ),
                        evidence_words=_WOODMAC_SCOPE_SNIPPET,
                    ),
                ),
                status="verified",
            ),
        ),
        sources=(
            scored_source(
                _EIA_URL,
                title=_EIA_TITLE,
                authority=0.95,
                recency=0.90,
                relevance=0.95,
                overall=0.93,
                rationale="Federal statistical agency inventory page.",
                serving_host="eia.gov",
                publisher_id=_EIA_ORGANISATION,
                work_id=_EIA_URL,
                transport_relation="original",
            ),
            scored_source(
                _WOODMAC_URL,
                title=_WOODMAC_TITLE,
                authority=0.88,
                recency=0.90,
                relevance=0.90,
                overall=0.89,
                rationale="Market analyst's own release.",
                serving_host="woodmac.com",
                publisher_id=_WOODMAC_ORGANISATION,
                work_id=_WOODMAC_URL,
                transport_relation="original",
            ),
        ),
        memory_context=MemorySnapshot(
            suggested_strategies=["state disagreements explicitly"]
        ),
    ),
    dependency_scenario="report-writer-conflicted",
    expectations=CaseExpectations(
        required_output_fields=["markdown", "evidence_markdown"],
        reference={
            "known_citation_urls": [_EIA_URL, _WOODMAC_URL],
            "conflicting_urls": [_EIA_URL, _WOODMAC_URL],
            "forbidden_publication_claims": [
                "saved to",
                "written to",
                "stored at",
                "published to",
            ],
        },
        known_source_urls=[_EIA_URL, _WOODMAC_URL],
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "reader_markdown_present",
                0.10,
                "The result carries non-empty reader `markdown`.",
            ),
            (
                "evidence_markdown_present",
                0.10,
                "The result carries non-empty `evidence_markdown`.",
            ),
            (
                "refusals_logged",
                0.25,
                "Every refused sentence and every dropped figure is published "
                "with its reason in the evidence log.",
            ),
            (
                "conflicting_figures_published",
                0.20,
                "Both conflicting forecasters' pages appear in the report's "
                "reference list.",
            ),
            (
                "statements_labelled",
                0.15,
                "Every printed statement cites at least one known finding "
                "label.",
            ),
            (
                "no_persistence_calls",
                0.10,
                "Neither persistence tool is called during composition.",
            ),
            (
                "no_false_publication_claim",
                0.10,
                "The composition does not claim either artifact was "
                "published.",
            ),
        ),
    ),
    judge_rubric=_CONFLICT_RUBRIC,
    metadata={"scenario": "challenging"},
)


# --- Case 3: composition only, never publication ---------------------------


_COMPOSITION_EIA_READ = read_record(
    _EIA_URL,
    case_id="composition-no-publication",
    title=_EIA_TITLE,
    text=_EIA_PAGE,
)

_COMPOSITION = build_case(
    case_id="composition-no-publication",
    agent_name="report_writer",
    tier="controlled",
    title="Compose both report artifacts without publishing them",
    purpose=(
        "One verified finding, one required target, and a writer that "
        "declares both persistence tools but calls neither: the run composes "
        "the reader report and the evidence log and writes no file and no "
        "memory entry. The output must not claim either artifact was "
        "published, because no path was written."
    ),
    state=evaluation_state(
        case_id="composition-no-publication",
        question=(
            "How much battery storage capacity was added in the United "
            "States in 2024?"
        ),
        sub_topics=(
            sub_topic(
                "U.S. battery storage capacity additions",
                rationale="One question, one obligation.",
                queries=["us battery storage capacity additions 2024"],
                criteria=["An actual installation figure for 2024"],
                priority=1,
                targets=(
                    target(
                        "topic-01-target-01",
                        question="How much battery storage capacity was added in 2024?",
                        measure="battery storage power capacity added",
                        unit_dimension="power",
                        period="2024",
                        kind="actual",
                        geography="United States",
                    ),
                ),
            ),
        ),
        reads=(_COMPOSITION_EIA_READ,),
        verified_findings=(
            verified(
                finding(
                    "The EIA reports that generators added 10.4 gigawatts "
                    "(GW) of new battery storage capacity in 2024",
                    url=_EIA_URL,
                    title=_EIA_TITLE,
                    sub_topic_title="U.S. battery storage capacity additions",
                    snippet=(
                        "The EIA reports that generators added 10.4 "
                        "gigawatts (GW) of new battery storage capacity in "
                        "2024"
                    ),
                    read_id=_COMPOSITION_EIA_READ.read_id,
                    locator="body",
                    figures=(figure("10.4", "GW", period="2024", kind="actual"),),
                    target_ids=("topic-01-target-01",),
                    data_period="2024",
                ),
                (
                    kept(
                        figure("10.4", "GW", period="2024", kind="actual"),
                        context(
                            organisation=_EIA_ORGANISATION,
                            kind="actual",
                            period="2024",
                        ),
                        evidence_words=(
                            "Generators added 10.4 gigawatts (GW) of new "
                            "battery storage capacity in 2024"
                        ),
                    ),
                ),
                status="verified",
            ),
        ),
        sources=(
            scored_source(
                _EIA_URL,
                title=_EIA_TITLE,
                authority=0.95,
                recency=0.90,
                relevance=0.95,
                overall=0.93,
                rationale="Federal statistical agency inventory page.",
                serving_host="eia.gov",
                publisher_id=_EIA_ORGANISATION,
                work_id=_EIA_URL,
                transport_relation="original",
            ),
        ),
    ),
    dependency_scenario="report-writer-composition",
    expectations=CaseExpectations(
        required_output_fields=["markdown", "evidence_markdown"],
        reference={
            "known_citation_urls": [_EIA_URL],
            "forbidden_publication_claims": [
                "saved to",
                "written to",
                "stored at",
                "published to",
            ],
        },
        known_source_urls=[_EIA_URL],
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "reader_markdown_present",
                0.15,
                "The result carries non-empty reader `markdown`.",
            ),
            (
                "evidence_markdown_present",
                0.15,
                "The result carries non-empty `evidence_markdown`.",
            ),
            (
                "no_persistence_calls",
                0.30,
                "Neither persistence tool is called during composition.",
            ),
            (
                "no_false_publication_claim",
                0.25,
                "The composition does not claim either artifact was "
                "published.",
            ),
            (
                "citations_locally_derived",
                0.15,
                "Every reference in the report is a page the run's own "
                "verified findings cite.",
            ),
        ),
    ),
    judge_rubric=_COMPOSITION_RUBRIC,
    metadata={"scenario": "composition-only"},
)


# --- Case 4: one work, one reference ---------------------------------------


_CANONICAL_TRIALS_URL = "https://fieldstation.example/cover-crop-nitrate-trials"
_CANONICAL_REPRINT_URL = "https://agmirror.example/trials/cover-crop-nitrate"
_CANONICAL_META_URL = "https://agmetaanalysis.example/cover-crop-nitrate-meta-analysis"
_CANONICAL_MONITOR_URL = "https://waterauthority.example/cover-crop-nitrate-monitoring"
_CANONICAL_GUIDE_URL = "https://farminputs.example/cover-crop-establishment-guide"
_CANONICAL_URLS = (
    _CANONICAL_TRIALS_URL,
    _CANONICAL_REPRINT_URL,
    _CANONICAL_META_URL,
    _CANONICAL_MONITOR_URL,
    _CANONICAL_GUIDE_URL,
)
# Every host is a ``.example`` host, whose full name is its publisher identity,
# so the four works stay four publishers.
_CANONICAL_WORK = "cover-crop-nitrate-trials-2025"
_CANONICAL_TRIALS_PAGE = (
    "The 2025 cover-crop nitrate trials across twelve field stations found "
    "median nitrate leaching reductions of 31 percent against bare fallow."
)
_CANONICAL_REPRINT_PAGE = (
    "The 2025 cover-crop nitrate trials across twelve field stations found "
    "median nitrate leaching reductions of 31 percent against bare fallow. "
    "Reprinted from the trials network."
)
_CANONICAL_META_PAGE = (
    "AgMeta Analysis estimates a mean nitrate leaching reduction of 24 "
    "percent from cover crops, from a meta-analysis of 48 trials."
)
_CANONICAL_MONITOR_PAGE = (
    "Water Authority recorded a 19 percent fall in nitrate concentration "
    "after cover crops were established, from its catchment monitoring."
)
_CANONICAL_GUIDE_PAGE = (
    "Farm Inputs states that establishment timing dominates cover-crop "
    "performance; late sowing loses roughly a third of the potential "
    "nitrogen uptake."
)

_CANONICAL_TRIALS_READ = read_record(
    _CANONICAL_TRIALS_URL,
    case_id="canonical-evidence-report",
    title="Cover-crop nitrate trials 2025 | Field Station Network",
    text=_CANONICAL_TRIALS_PAGE,
)
_CANONICAL_REPRINT_READ = read_record(
    _CANONICAL_REPRINT_URL,
    case_id="canonical-evidence-report",
    title="Cover-crop nitrate trials 2025 | Agricultural Mirror",
    text=_CANONICAL_REPRINT_PAGE,
)
_CANONICAL_META_READ = read_record(
    _CANONICAL_META_URL,
    case_id="canonical-evidence-report",
    title="Cover-crop nitrate meta-analysis | AgMeta Analysis",
    text=_CANONICAL_META_PAGE,
)
_CANONICAL_MONITOR_READ = read_record(
    _CANONICAL_MONITOR_URL,
    case_id="canonical-evidence-report",
    title="Cover-crop nitrate catchment monitoring | Water Authority",
    text=_CANONICAL_MONITOR_PAGE,
)
_CANONICAL_GUIDE_READ = read_record(
    _CANONICAL_GUIDE_URL,
    case_id="canonical-evidence-report",
    title="Cover-crop establishment guide | Farm Inputs",
    text=_CANONICAL_GUIDE_PAGE,
)

_CANONICAL = build_case(
    case_id="canonical-evidence-report",
    agent_name="report_writer",
    tier="controlled",
    title="Print one reference per work, under the readable copy",
    purpose=(
        "The trials network's report and a reprint of it are one work served "
        "twice: the assessed rows record one work id with one original and "
        "one mirror, and the findings cite the original. The reference list "
        "prints each work once — the mirror does not become a second source "
        "— and every URL it prints is one the run's own records carry."
    ),
    state=evaluation_state(
        case_id="canonical-evidence-report",
        question="How much do cover crops reduce nitrate leaching?",
        sub_topics=(
            sub_topic(
                "Cover-crop nitrate reduction",
                rationale="The question turns on a measurable reduction.",
                queries=["cover crop nitrate leaching reduction"],
                criteria=["A measured reduction from a named study"],
                priority=1,
                targets=(
                    target(
                        "topic-01-target-01",
                        question="What reduction has been measured?",
                        measure="nitrate leaching reduction",
                        unit_dimension="percent",
                        geography="field stations",
                    ),
                ),
            ),
        ),
        reads=(
            _CANONICAL_TRIALS_READ,
            _CANONICAL_REPRINT_READ,
            _CANONICAL_META_READ,
            _CANONICAL_MONITOR_READ,
            _CANONICAL_GUIDE_READ,
        ),
        verified_findings=(
            verified(
                finding(
                    _CANONICAL_TRIALS_PAGE,
                    url=_CANONICAL_TRIALS_URL,
                    title="Cover-crop nitrate trials 2025 | Field Station Network",
                    sub_topic_title="Cover-crop nitrate reduction",
                    snippet=_CANONICAL_TRIALS_PAGE,
                    read_id=_CANONICAL_TRIALS_READ.read_id,
                    locator="body",
                    figures=(figure("31", "%", kind="actual"),),
                    target_ids=("topic-01-target-01",),
                ),
                (
                    kept(
                        figure("31", "%", kind="actual"),
                        context(
                            organisation="fieldstation.example",
                            kind="actual",
                        ),
                        evidence_words=_CANONICAL_TRIALS_PAGE,
                    ),
                ),
                status="verified",
            ),
            verified(
                finding(
                    _CANONICAL_META_PAGE,
                    url=_CANONICAL_META_URL,
                    title="Cover-crop nitrate meta-analysis | AgMeta Analysis",
                    sub_topic_title="Cover-crop nitrate reduction",
                    snippet=_CANONICAL_META_PAGE,
                    read_id=_CANONICAL_META_READ.read_id,
                    locator="body",
                    figures=(figure("24", "%", kind="actual"),),
                ),
                (
                    kept(
                        figure("24", "%", kind="actual"),
                        context(
                            organisation="agmetaanalysis.example",
                            kind="actual",
                        ),
                        evidence_words=_CANONICAL_META_PAGE,
                    ),
                ),
                status="verified",
            ),
            verified(
                finding(
                    _CANONICAL_MONITOR_PAGE,
                    url=_CANONICAL_MONITOR_URL,
                    title="Cover-crop nitrate catchment monitoring | Water Authority",
                    sub_topic_title="Cover-crop nitrate reduction",
                    snippet=_CANONICAL_MONITOR_PAGE,
                    read_id=_CANONICAL_MONITOR_READ.read_id,
                    locator="body",
                    figures=(figure("19", "%", kind="actual"),),
                ),
                (
                    kept(
                        figure("19", "%", kind="actual"),
                        context(
                            organisation="waterauthority.example",
                            kind="actual",
                        ),
                        evidence_words=_CANONICAL_MONITOR_PAGE,
                    ),
                ),
                status="verified",
            ),
            verified(
                finding(
                    _CANONICAL_GUIDE_PAGE,
                    url=_CANONICAL_GUIDE_URL,
                    title="Cover-crop establishment guide | Farm Inputs",
                    sub_topic_title="Cover-crop nitrate reduction",
                    snippet=_CANONICAL_GUIDE_PAGE,
                    read_id=_CANONICAL_GUIDE_READ.read_id,
                    locator="body",
                ),
                (),
                status="verified",
            ),
        ),
        sources=(
            scored_source(
                _CANONICAL_TRIALS_URL,
                title="Cover-crop nitrate trials 2025 | Field Station Network",
                authority=0.85,
                recency=0.80,
                relevance=0.90,
                overall=0.85,
                rationale="The trials network's own report.",
                serving_host="fieldstation.example",
                publisher_id="Field Station Network",
                work_id=_CANONICAL_WORK,
                transport_relation="original",
            ),
            scored_source(
                _CANONICAL_REPRINT_URL,
                title="Cover-crop nitrate trials 2025 | Agricultural Mirror",
                authority=0.45,
                recency=0.80,
                relevance=0.85,
                overall=0.65,
                rationale=(
                    "A reprint of the trials report, served by an aggregator: "
                    "the same work, not a second one."
                ),
                serving_host="agmirror.example",
                publisher_id="Agricultural Mirror",
                work_id=_CANONICAL_WORK,
                transport_relation="mirror",
            ),
            scored_source(
                _CANONICAL_META_URL,
                title="Cover-crop nitrate meta-analysis | AgMeta Analysis",
                authority=0.80,
                recency=0.78,
                relevance=0.88,
                overall=0.82,
                rationale="Independent meta-analysis of 48 trials.",
                serving_host="agmetaanalysis.example",
                publisher_id="AgMeta Analysis",
                work_id="cover-crop-nitrate-meta-analysis",
                transport_relation="original",
            ),
            scored_source(
                _CANONICAL_MONITOR_URL,
                title="Cover-crop nitrate catchment monitoring | Water Authority",
                authority=0.82,
                recency=0.85,
                relevance=0.80,
                overall=0.82,
                rationale="Regulator's own catchment monitoring.",
                serving_host="waterauthority.example",
                publisher_id="Water Authority",
                work_id="cover-crop-nitrate-monitoring",
                transport_relation="original",
            ),
            scored_source(
                _CANONICAL_GUIDE_URL,
                title="Cover-crop establishment guide | Farm Inputs",
                authority=0.60,
                recency=0.70,
                relevance=0.65,
                overall=0.65,
                rationale="A vendor guide: usable for practice, not for effect size.",
                serving_host="farminputs.example",
                publisher_id="Farm Inputs",
                work_id="cover-crop-establishment-guide",
                transport_relation="original",
            ),
        ),
    ),
    dependency_scenario="report-writer-canonical-evidence",
    expectations=CaseExpectations(
        required_output_fields=["markdown", "evidence_markdown"],
        reference={
            "known_citation_urls": [
                _CANONICAL_TRIALS_URL,
                _CANONICAL_META_URL,
                _CANONICAL_MONITOR_URL,
                _CANONICAL_GUIDE_URL,
            ],
            "mirror_url": _CANONICAL_REPRINT_URL,
            "work_id": _CANONICAL_WORK,
        },
        known_source_urls=list(_CANONICAL_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "citations_locally_derived",
                0.30,
                "Every reference in the report is a page the run's own "
                "verified findings cite.",
            ),
            (
                "one_reference_per_work",
                0.30,
                "The reference list prints one entry per work, under the copy "
                "the assessments identify as the original.",
            ),
            (
                "statements_labelled",
                0.20,
                "Every printed statement cites at least one known finding "
                "label.",
            ),
            (
                "reader_markdown_present",
                0.10,
                "The result carries non-empty reader `markdown`.",
            ),
            (
                "evidence_markdown_present",
                0.10,
                "The result carries non-empty `evidence_markdown`.",
            ),
        ),
    ),
    judge_rubric=_CANONICAL_RUBRIC,
    metadata={"scenario": "canonical-evidence"},
)


# --- The live case ---------------------------------------------------------


_DOE_URL = "https://energy.gov/eere/buildings/heat-pump-systems"
_NREL_URL = "https://nrel.gov/research/buildings/heat-pump-retrofit-costs"
_IEA_URL = "https://iea.org/energy-system/buildings/heating"
_LIVE_URLS = (_DOE_URL, _NREL_URL, _IEA_URL)

_DOE_PAGE = (
    "Heat-pump retrofits in temperate climates now cost about the same as "
    "furnace replacements when installation incentives are counted, with "
    "operating costs below those of fossil-fuel systems."
)
_NREL_PAGE = (
    "Field studies of heat-pump retrofits in temperate U.S. climates find "
    "median installed costs between $8,000 and $12,000 for ducted systems."
)
_IEA_PAGE = (
    "Heat-pump retrofit costs in temperate markets have fallen over the past "
    "five years, and policy incentives shorten payback periods to seven years "
    "or less."
)

_LIVE_DOE_READ = read_record(
    _DOE_URL,
    case_id="report-writer-live-report",
    title="Heat pump systems | Department of Energy",
    text=_DOE_PAGE,
)
_LIVE_NREL_READ = read_record(
    _NREL_URL,
    case_id="report-writer-live-report",
    title="Heat pump retrofit costs | NREL",
    text=_NREL_PAGE,
)
_LIVE_IEA_READ = read_record(
    _IEA_URL,
    case_id="report-writer-live-report",
    title="Heat pumps in buildings | IEA",
    text=_IEA_PAGE,
)

_LIVE = build_case(
    case_id="report-writer-live-report",
    agent_name="report_writer",
    tier="live",
    title="Compose a cited report on heat-pump retrofit costs, live",
    purpose=(
        "Three fixed, current findings on heat-pump retrofit costs in "
        "temperate climates, composed by a live model into the same shape as "
        "the controlled cases. The citations come from the findings' own "
        "pages, so citations_locally_derived is gradable, and the writer "
        "still publishes no artifact: composition is not publication."
    ),
    state=evaluation_state(
        case_id="report-writer-live-report",
        question=(
            "What does recent evidence say about heat-pump retrofit costs "
            "in temperate climates?"
        ),
        sub_topics=(
            sub_topic(
                "Heat-pump retrofit cost benchmarks",
                rationale="Stable cost benchmarks let a reader price a retrofit.",
                queries=["heat pump retrofit installed cost"],
                criteria=["Installed cost data for temperate climates"],
                priority=1,
                targets=(
                    target(
                        "topic-01-target-01",
                        question="What does a ducted heat-pump retrofit cost?",
                        measure="installed retrofit cost",
                        unit_dimension=None,
                        organisation="NREL",
                    ),
                ),
            ),
            sub_topic(
                "Incentives and payback periods",
                rationale="Incentives are the main lever on effective cost.",
                queries=["heat pump retrofit incentives payback"],
                criteria=["At least one incentive or payback figure"],
                priority=2,
            ),
            sub_topic(
                "Cost comparisons with incumbent systems",
                rationale="Retrofit costs matter relative to furnace replacement.",
                queries=["heat pump versus furnace replacement cost"],
                criteria=["A comparison with an incumbent heating system"],
                priority=3,
            ),
        ),
        reads=(_LIVE_DOE_READ, _LIVE_NREL_READ, _LIVE_IEA_READ),
        verified_findings=(
            verified(
                finding(
                    _DOE_PAGE,
                    url=_DOE_URL,
                    title="Heat pump systems | Department of Energy",
                    sub_topic_title="Cost comparisons with incumbent systems",
                    snippet=_DOE_PAGE,
                    read_id=_LIVE_DOE_READ.read_id,
                    locator="body",
                ),
                (),
                status="verified",
            ),
            verified(
                finding(
                    _NREL_PAGE,
                    url=_NREL_URL,
                    title="Heat pump retrofit costs | NREL",
                    sub_topic_title="Heat-pump retrofit cost benchmarks",
                    snippet=_NREL_PAGE,
                    read_id=_LIVE_NREL_READ.read_id,
                    locator="body",
                    target_ids=("topic-01-target-01",),
                ),
                (),
                status="verified",
            ),
            verified(
                finding(
                    _IEA_PAGE,
                    url=_IEA_URL,
                    title="Heat pumps in buildings | IEA",
                    sub_topic_title="Incentives and payback periods",
                    snippet=_IEA_PAGE,
                    read_id=_LIVE_IEA_READ.read_id,
                    locator="body",
                ),
                (),
                status="verified",
            ),
        ),
        sources=(
            scored_source(
                _DOE_URL,
                title="Heat pump systems | Department of Energy",
                authority=0.90,
                recency=0.88,
                relevance=0.90,
                overall=0.88,
                rationale="Federal program page with current cost guidance.",
                serving_host="energy.gov",
                publisher_id="Department of Energy",
                work_id=_DOE_URL,
                transport_relation="original",
            ),
            scored_source(
                _NREL_URL,
                title="Heat pump retrofit costs | NREL",
                authority=0.92,
                recency=0.88,
                relevance=0.92,
                overall=0.90,
                rationale="National laboratory field studies of installed costs.",
                serving_host="nrel.gov",
                publisher_id="NREL",
                work_id=_NREL_URL,
                transport_relation="original",
            ),
            scored_source(
                _IEA_URL,
                title="Heat pumps in buildings | IEA",
                authority=0.90,
                recency=0.86,
                relevance=0.88,
                overall=0.86,
                rationale="International agency analysis of costs and policy.",
                serving_host="iea.org",
                publisher_id="IEA",
                work_id=_IEA_URL,
                transport_relation="original",
            ),
        ),
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["markdown", "evidence_markdown"],
        reference={
            "known_citation_urls": list(_LIVE_URLS),
            "forbidden_publication_claims": [
                "saved to",
                "written to",
                "stored at",
                "published to",
            ],
        },
        known_source_urls=list(_LIVE_URLS),
        max_iterations=1,
        max_tool_calls=5,
        deterministic_metrics=metrics(
            (
                "reader_markdown_present",
                0.20,
                "The result carries non-empty reader `markdown`.",
            ),
            (
                "evidence_markdown_present",
                0.20,
                "The result carries non-empty `evidence_markdown`.",
            ),
            (
                "citations_locally_derived",
                0.25,
                "Every reference in the report is a page the run's own "
                "verified findings cite.",
            ),
            (
                "statements_labelled",
                0.15,
                "Every printed statement cites at least one known finding "
                "label.",
            ),
            (
                "no_persistence_calls",
                0.10,
                "Neither persistence tool is called during composition.",
            ),
            (
                "no_false_publication_claim",
                0.10,
                "The composition does not claim either artifact was "
                "published.",
            ),
        ),
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

# Append new cases; never prepend. ``conftest.controlled_case_for`` takes
# ``cases_for(agent, "controlled")[0]``, so the first case here is the one
# every conftest-driven gate test exercises.
CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _COMPLETE,
    _CONFLICT,
    _COMPOSITION,
    _CANONICAL,
)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
