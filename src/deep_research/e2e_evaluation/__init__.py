"""Whole-report quality evaluation, kept separate from agent evaluation."""

from __future__ import annotations

from deep_research.e2e_evaluation.cases import (
    CASE_REGISTRY_VERSION,
    CONTROLLED_CASE_IDS,
    CONTROLLED_CASES,
    LIVE_CASE_IDS,
    LIVE_CASES,
    all_cases,
    case_by_id,
    controlled_cases,
    live_cases,
)
from deep_research.e2e_evaluation.models import (
    CAMPAIGN_SCHEMA_VERSION,
    CASE_SCHEMA_VERSION,
    QUALITY_GATE_VERSION,
    REPORT_SCHEMA_VERSION,
    CampaignResult,
    CaseCampaignResult,
    ControlledCase,
    DeterministicEvaluation,
    ExpectedResult,
    WholeReportJudgeInput,
    WholeReportJudgeScore,
    WholeReportRubric,
)
from deep_research.e2e_evaluation.runner import (
    main,
    run_case,
    run_controlled_suite,
    run_suite,
)

__all__ = [
    "CAMPAIGN_SCHEMA_VERSION",
    "CASE_REGISTRY_VERSION",
    "CASE_SCHEMA_VERSION",
    "CONTROLLED_CASE_IDS",
    "CONTROLLED_CASES",
    "LIVE_CASE_IDS",
    "LIVE_CASES",
    "CampaignResult",
    "CaseCampaignResult",
    "ControlledCase",
    "DeterministicEvaluation",
    "ExpectedResult",
    "QUALITY_GATE_VERSION",
    "REPORT_SCHEMA_VERSION",
    "WholeReportJudgeInput",
    "WholeReportJudgeScore",
    "WholeReportRubric",
    "all_cases",
    "case_by_id",
    "controlled_cases",
    "live_cases",
    "main",
    "run_case",
    "run_controlled_suite",
    "run_suite",
]
