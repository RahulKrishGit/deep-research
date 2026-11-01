"""Stage replay on frozen state.

A live run made with a stage capture bound (``observability.stage_capture``)
leaves the evidence verifier's input state, every Statement Check call's
items and every page extraction's request on disk. ``run`` asks those same
stages again, once, under one arm's settings -- the production config plus
that arm's overrides -- and writes what each figure, each sentence and each
extraction target was judged; ``summarize`` compares two arms the way the
pass rule reads: the treatment must agree with the control at least as often
as the control agrees with itself, and drop no more.

    python -m deep_research.experiments.stage_replay run --capture DIR \\
        --arm control --repetition 1 --override '{"agents": {"verifier_batch_size": 5}}' \\
        --stages context_check,statement_check,page_extraction --out DIR
    python -m deep_research.experiments.stage_replay summarize --out DIR

``--stages`` names the stages to ask (all three by default); the output file
lists them, and ``summarize`` judges exactly the stages every repetition
lists, refusing outputs whose repetitions list different ones. An output
with no list was written before ``--stages`` existed and counts as listing
the Context Check and the Statement Check.

Only model calls are made: no search, no page read, no memory write.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from time import perf_counter
from typing import Any

from deep_research.agents.base import effort_kwargs
from deep_research.agents.evidence_verifier import (
    StatementCheckItem,
    StatementVerdictDraft,
    check_statements,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.researcher import SubTopicFindingsDraft, build_findings
from deep_research.experiments.live_runs import peak_ahead
from deep_research.observability import call_label
from deep_research.providers import ChatMessage, ProviderError
from deep_research.utils.types import (
    Finding,
    ReadRecord,
    ResearchError,
    ResearchState,
    SubTopic,
)

CONTEXT_CHECK = "context_check"
STATEMENT_CHECK = "statement_check"
PAGE_EXTRACTION = "page_extraction"
STAGES = (CONTEXT_CHECK, STATEMENT_CHECK, PAGE_EXTRACTION)
LEGACY_STAGES = (CONTEXT_CHECK, STATEMENT_CHECK)
"""The stages an output written before ``--stages`` existed was judged on."""
_FIGURE_MATCH_DROPS = frozenset({"read_not_found", "snippet_not_on_page"})
_REPLAYED_AT = "1970-01-01T00:00:00+00:00"
"""What a replayed finding is stamped ``extracted_at``: ``Finding`` requires a
timezone-aware ISO time, nothing here stores the finding, and a fixed value
reads no clock."""


@dataclass(frozen=True, slots=True)
class StageResult:
    """One stage, asked once, on one capture file."""

    stage: str
    source: str
    seconds: float
    verdicts: dict[str, Any]
    counts: dict[str, int]


def figure_verdicts(findings: Sequence[Finding]) -> dict[str, Any]:
    """What the Context Check decided for each figure it judged.

    Keyed ``<finding fingerprint>#<figure number>``. A verdict is everything the
    report can use of the figure: why it was dropped (``None`` when kept),
    whether it was corrected, whether its batch went unchecked, and the period,
    scope, subject, attribution and kind it was kept with. A finding Figure
    Match dropped, or one with no figures, never reached the check and is
    left out.
    """
    verdicts: dict[str, Any] = {}
    for finding in findings:
        verification = finding.verification
        if verification is None or not finding.figures:
            continue
        if verification.dropped_reason in _FIGURE_MATCH_DROPS:
            continue
        key = finding_fingerprint(finding)
        for number, result in enumerate(verification.figure_results, 1):
            context = result.context
            verdicts[f"{key}#{number}"] = [
                result.dropped_reason,
                result.corrected,
                verification.context_unchecked,
                None if context is None else context.period,
                None if context is None else context.scope,
                None if context is None else context.subject,
                None if context is None else context.attribution,
                None if context is None else context.kind,
            ]
    return verdicts


def context_counts(findings: Sequence[Finding], errors: Sequence[ResearchError]) -> dict[str, int]:
    judged = figure_verdicts(findings)
    statuses = [
        finding.verification.status
        for finding in findings
        if finding.verification is not None
    ]
    return {
        "figures_judged": len(judged),
        "figures_dropped": sum(1 for verdict in judged.values() if verdict[0] is not None),
        "figures_corrected": sum(1 for verdict in judged.values() if verdict[1]),
        "figures_unchecked": sum(1 for verdict in judged.values() if verdict[2]),
        "findings_verified": statuses.count("verified"),
        "findings_corrected": statuses.count("verified_corrected"),
        "findings_dropped": statuses.count("dropped"),
        "errors": len(errors),
    }


def statement_verdicts(results: Mapping[str, StatementVerdictDraft | None]) -> dict[str, Any]:
    """Each sentence's verdict, ``None`` when it went unjudged."""
    return {
        label: None if verdict is None else verdict.verdict
        for label, verdict in results.items()
    }


def statement_counts(
    results: Mapping[str, StatementVerdictDraft | None], errors: Sequence[ResearchError]
) -> dict[str, int]:
    verdicts = list(statement_verdicts(results).values())
    return {
        "statements": len(verdicts),
        "consistent": verdicts.count("consistent"),
        "corrected": verdicts.count("corrected"),
        "inconsistent": verdicts.count("inconsistent"),
        "unjudged": verdicts.count(None),
        "errors": len(errors),
    }


async def _replay_page_extractions(
    pages: Sequence[Path], *, settings: Any, provider: Any, tracker: Any
) -> StageResult:
    """Ask every captured page extraction once, all under one shared gate.

    Each file is the request a run sent for one page, with the sub-topic, the
    reads and the target ids its reply was admitted against. The reply goes
    through ``build_findings`` as the researcher sends it, so a finding is kept
    or rejected exactly as in the run. A target is bound when a kept finding
    names it; a provider failure leaves its page with no verdicts.
    """
    gate = asyncio.Semaphore(settings.agents.extraction_concurrency)
    effort = effort_kwargs(settings.agents.effort_for(PAGE_EXTRACTION))

    async def one(path: Path) -> tuple[str, list[Finding] | None, int]:
        payload = json.loads(path.read_text("utf-8"))
        messages = [ChatMessage.model_validate(message) for message in payload["messages"]]
        known_reads = {
            read_id: ReadRecord.model_validate(read)
            for read_id, read in payload["known_reads"].items()
        }
        async with gate:
            try:
                with call_label(PAGE_EXTRACTION):
                    draft = await provider.complete_structured(
                        messages,
                        SubTopicFindingsDraft,
                        agent_name="researcher",
                        **effort,
                    )
            except ProviderError:
                return path.name, None, 0
        findings, rejected = build_findings(
            draft,
            sub_topic=SubTopic.model_validate(payload["sub_topic"]),
            extracted_at=_REPLAYED_AT,
            known_urls=[read.resolved_url for read in known_reads.values()],
            known_reads=known_reads,
            valid_target_ids=payload["valid_target_ids"],
        )
        return path.name, findings, len(rejected)

    started = perf_counter()
    async with tracker.session_span("stage-replay-page-extraction", "page extraction"):
        answered = await asyncio.gather(*(one(path) for path in pages))
    seconds = perf_counter() - started
    verdicts: dict[str, Any] = {}
    kept = rejected_total = provider_errors = 0
    for name, findings, rejected in answered:
        if findings is None:
            provider_errors += 1
            continue
        kept += len(findings)
        rejected_total += rejected
        for finding in findings:
            for target_id in finding.target_ids:
                verdicts[f"{name}#{target_id}"] = True
    return StageResult(
        stage=PAGE_EXTRACTION,
        source=f"{len(pages)} calls",
        seconds=round(seconds, 3),
        verdicts=verdicts,
        counts={
            "findings_kept": kept,
            "findings_rejected": rejected_total,
            "targets_bound": len(verdicts),
            "provider_errors": provider_errors,
        },
    )


async def replay_capture(
    capture: Path,
    *,
    settings: Any,
    provider: Any,
    tracker: Any,
    build_agent: Any,
    stages: Collection[str] = STAGES,
) -> list[StageResult]:
    """Ask every captured stage in ``stages`` once under ``settings``.

    The verifier's captured inputs run one after another, as a run's passes
    do; every captured Statement Check call, from every pass, then runs
    together under one shared gate of ``agents.verifier_concurrency``, as the
    writer's parts do, and their result carries the time the whole set took.
    Every captured page extraction likewise runs together, under one gate of
    ``agents.extraction_concurrency``. A stage not in ``stages`` is not asked,
    and a stage with nothing captured gives no result.
    """
    results: list[StageResult] = []
    verifier_inputs = sorted(capture.glob("evidence_verifier-*.json")) if CONTEXT_CHECK in stages else []
    for path in verifier_inputs:
        state = ResearchState.model_validate(json.loads(path.read_text("utf-8"))["state"])
        agent = build_agent(
            "evidence_verifier",
            settings,
            tracker=tracker,
            provider=provider,
            tools=(),
            session_id=state.session_id,
            reputation=None,
        )
        started = perf_counter()
        async with tracker.session_span(f"replay-{state.session_id}", state.original_question):
            run = await agent.run(state)
        seconds = perf_counter() - started
        judged = [] if run.result is None else run.result.findings
        results.append(
            StageResult(
                stage=CONTEXT_CHECK,
                source=path.name,
                seconds=round(seconds, 3),
                verdicts=figure_verdicts(judged),
                counts=context_counts(judged, run.errors),
            )
        )
    calls = sorted(capture.glob("statement_check-*.json")) if STATEMENT_CHECK in stages else []
    if calls:
        gate = asyncio.Semaphore(settings.agents.verifier_concurrency)

        async def one(path: Path) -> tuple[str, dict[str, Any], list[ResearchError]]:
            payload = json.loads(path.read_text("utf-8"))
            items = [StatementCheckItem.model_validate(item) for item in payload["items"]]
            verdicts, errors = await check_statements(
                provider,
                items,
                question=payload["question"],
                batch_size=settings.agents.verifier_batch_size,
                gate=gate,
                **effort_kwargs(settings.agents.effort_for(STATEMENT_CHECK)),
            )
            return path.name, verdicts, errors

        started = perf_counter()
        async with tracker.session_span("stage-replay-statement-check", "statement check"):
            answered = await asyncio.gather(*(one(path) for path in calls))
        seconds = perf_counter() - started
        verdicts: dict[str, Any] = {}
        merged: dict[str, StatementVerdictDraft | None] = {}
        errors: list[ResearchError] = []
        for name, call_verdicts, call_errors in answered:
            for label, verdict in call_verdicts.items():
                verdicts[f"{name}#{label}"] = None if verdict is None else verdict.verdict
                merged[f"{name}#{label}"] = verdict
            errors.extend(call_errors)
        results.append(
            StageResult(
                stage=STATEMENT_CHECK,
                source=f"{len(calls)} calls",
                seconds=round(seconds, 3),
                verdicts=verdicts,
                counts=statement_counts(merged, errors),
            )
        )
    pages = sorted(capture.glob("page_extraction-*.json")) if PAGE_EXTRACTION in stages else []
    if pages:
        results.append(
            await _replay_page_extractions(
                pages, settings=settings, provider=provider, tracker=tracker
            )
        )
    return results


def agreement(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    """The share of keys, across both, whose verdicts are equal."""
    keys = set(first) | set(second)
    if not keys:
        return 1.0
    same = sum(1 for key in keys if key in first and key in second and first[key] == second[key])
    return same / len(keys)


def _repetitions(out: Path, arm: str) -> list[dict[str, Any]]:
    paths = sorted(out.glob(f"{arm}-*.json"), key=lambda path: int(path.stem.rsplit("-", 1)[1]))
    return [json.loads(path.read_text("utf-8")) for path in paths]


def _listed_stages(repetition: Mapping[str, Any]) -> tuple[str, ...]:
    """The stages one repetition lists, in report order.

    An output with no ``stages`` key was written before ``--stages`` existed,
    when every replay asked the Context Check and the Statement Check, so it
    lists those two and never the page extraction.
    """
    listed = set(repetition.get("stages", LEGACY_STAGES))
    return tuple(stage for stage in STAGES if stage in listed)


def _one_stage_list(
    out: Path, arms: Sequence[tuple[str, Sequence[Mapping[str, Any]]]]
) -> list[str]:
    """The stages every repetition of every arm lists, or a ``ValueError``.

    A repetition that was not asked a stage has no verdicts for it. One such
    control repetition pulls the control's agreement floor and every minimum
    count for that stage down to 0, where a treatment that bound no target or
    judged no statement passes, so repetitions that list different stages are
    not summarized at all.
    """
    lists = {
        (arm, _listed_stages(repetition))
        for arm, repetitions in arms
        for repetition in repetitions
    }
    if len({stages for _, stages in lists}) > 1:
        found = "; ".join(f"{arm}: {','.join(stages)}" for arm, stages in sorted(lists))
        raise ValueError(
            f"{out}: every repetition must list the same stages, found {found}"
        )
    return list(next(iter(lists))[1])


def _reasoning_tokens(repetition: Mapping[str, Any]) -> int | None:
    """The reasoning tokens one repetition's whole replay reported, if recorded."""
    return repetition.get("telemetry", {}).get("reasoning_tokens")


def _stage(repetition: list[dict[str, Any]], stage: str) -> tuple[dict[str, Any], dict[str, int], float]:
    verdicts: dict[str, Any] = {}
    counts: dict[str, int] = {}
    seconds = 0.0
    for result in repetition:
        if result["stage"] != stage:
            continue
        verdicts.update({f"{result['source']}|{key}": value for key, value in result["verdicts"].items()})
        for name, value in result["counts"].items():
            counts[name] = counts.get(name, 0) + value
        seconds += result["seconds"]
    return verdicts, counts, seconds


def summarize(out: Path, *, control: str = "control", treatment: str = "treatment") -> dict[str, Any]:
    """The verdict for one capture's replays.

    Per stage: ``agreement_control_floor`` is the lowest agreement between two
    control repetitions (the control's own noise floor) and
    ``agreement_treatment`` the mean agreement of each treatment repetition
    with each control one. Every accuracy check must hold for every stage
    judged; the time check is the Context Check's. The stages judged are the
    ones every repetition lists (``_one_stage_list``): repetitions that list
    different stages raise ``ValueError``, since one that skipped a stage
    would lower the control's floor for it. A listed stage the control has no
    verdicts for fails ``<stage>.present``: a replay that judged nothing for a
    stage it was asked about is no pass on the others.
    ``control_reasoning_tokens`` and ``treatment_reasoning_tokens`` give each
    repetition's reasoning tokens, over every stage it asked, for reading
    beside the checks; no check depends on them.
    """
    controls, treatments = _repetitions(out, control), _repetitions(out, treatment)
    if len(controls) < 2 or not treatments:
        raise ValueError(
            f"{out}: need at least two {control} and one {treatment} repetitions, "
            f"found {len(controls)} and {len(treatments)}"
        )
    stages = _one_stage_list(out, ((control, controls), (treatment, treatments)))
    summary: dict[str, Any] = {"control_repetitions": len(controls),
                               "treatment_repetitions": len(treatments),
                               "stages": stages}
    checks: dict[str, bool] = {}
    for stage in stages:
        control_runs = [_stage(repetition["results"], stage) for repetition in controls]
        treatment_runs = [_stage(repetition["results"], stage) for repetition in treatments]
        if not any(verdicts for verdicts, _, _ in control_runs):
            checks[f"{stage}.present"] = False
            summary[stage] = None
            continue
        floor = min(agreement(a[0], b[0]) for a, b in combinations(control_runs, 2))
        across = statistics.mean(
            agreement(c[0], t[0]) for c in control_runs for t in treatment_runs
        )

        def counts(runs: list[tuple[dict[str, Any], dict[str, int], float]], name: str) -> list[int]:
            return [run[1].get(name, 0) for run in runs]

        stage_summary: dict[str, Any] = {
            "agreement_control_floor": round(floor, 4),
            "agreement_treatment": round(across, 4),
            "control_seconds_median": round(statistics.median(run[2] for run in control_runs), 3),
            "treatment_seconds_median": round(statistics.median(run[2] for run in treatment_runs), 3),
            "control_counts": [run[1] for run in control_runs],
            "treatment_counts": [run[1] for run in treatment_runs],
            "control_reasoning_tokens": [_reasoning_tokens(r) for r in controls],
            "treatment_reasoning_tokens": [_reasoning_tokens(r) for r in treatments],
        }
        checks[f"{stage}.agreement"] = across >= floor
        if stage == CONTEXT_CHECK:
            checks[f"{stage}.figures_dropped"] = statistics.mean(
                counts(treatment_runs, "figures_dropped")
            ) <= max(counts(control_runs, "figures_dropped"))
            kept = [a + b for a, b in zip(counts(control_runs, "findings_verified"),
                                          counts(control_runs, "findings_corrected"))]
            kept_treatment = [a + b for a, b in zip(counts(treatment_runs, "findings_verified"),
                                                    counts(treatment_runs, "findings_corrected"))]
            checks[f"{stage}.findings_kept"] = statistics.mean(kept_treatment) >= min(kept)
            checks[f"{stage}.figures_unchecked"] = max(
                counts(treatment_runs, "figures_unchecked")
            ) <= max(counts(control_runs, "figures_unchecked"))
            checks[f"{stage}.faster"] = (
                stage_summary["treatment_seconds_median"] < stage_summary["control_seconds_median"]
            )
        elif stage == STATEMENT_CHECK:
            checks[f"{stage}.inconsistent"] = statistics.mean(
                counts(treatment_runs, "inconsistent")
            ) <= max(counts(control_runs, "inconsistent"))
            checks[f"{stage}.unjudged"] = max(
                counts(treatment_runs, "unjudged")
            ) <= max(counts(control_runs, "unjudged"))
        else:
            checks[f"{stage}.findings_kept"] = statistics.mean(
                counts(treatment_runs, "findings_kept")
            ) >= min(counts(control_runs, "findings_kept"))
            checks[f"{stage}.targets_bound"] = statistics.mean(
                counts(treatment_runs, "targets_bound")
            ) >= min(counts(control_runs, "targets_bound"))
            checks[f"{stage}.provider_errors"] = max(
                counts(treatment_runs, "provider_errors")
            ) <= max(counts(control_runs, "provider_errors"))
        summary[stage] = stage_summary
    summary["checks"] = checks
    summary["passed"] = bool(checks) and all(checks.values())
    return summary


def _settings(config: str, override: str) -> Any:
    from deep_research.main import load_settings

    return load_settings(config, config_overrides=json.loads(override))


def _stages(value: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(name.strip() for name in value.split(",") if name.strip()))
    if not names or any(name not in STAGES for name in names):
        raise argparse.ArgumentTypeError(
            f"expected some of {','.join(STAGES)}, comma-separated; got {value!r}"
        )
    return names


async def _run(arguments: argparse.Namespace) -> Path:
    from deep_research.observability import RunTelemetryCollector, Tracker
    from deep_research.providers import build_chat_provider
    from deep_research.runtime.assembly import build_agent

    settings = _settings(arguments.config, arguments.override)
    tracker = Tracker.from_config(settings.langsmith)
    telemetry = RunTelemetryCollector()
    provider = build_chat_provider(settings.llm, tracker, telemetry=telemetry)
    results = await replay_capture(
        Path(arguments.capture),
        settings=settings,
        provider=provider,
        tracker=tracker,
        build_agent=build_agent,
        stages=arguments.stages,
    )
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{arguments.arm}-{arguments.repetition}.json"
    path.write_text(
        json.dumps(
            {
                "arm": arguments.arm,
                "repetition": arguments.repetition,
                "override": json.loads(arguments.override),
                "stages": list(arguments.stages),
                "results": [asdict(result) for result in results],
                "telemetry": telemetry.snapshot().model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def main(
    argv: Sequence[str] | None = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    parser = argparse.ArgumentParser(prog="python -m deep_research.experiments.stage_replay")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="ask the captured stages once under one arm")
    run.add_argument("--capture", required=True)
    run.add_argument("--arm", required=True, choices=("control", "treatment"))
    run.add_argument("--repetition", required=True, type=int)
    run.add_argument("--override", required=True, help="config overrides, as a JSON object")
    run.add_argument("--out", required=True)
    run.add_argument("--config", default="config.yaml")
    run.add_argument(
        "--stages",
        type=_stages,
        default=STAGES,
        help=f"comma-separated stages to ask, of {','.join(STAGES)} (default: all)",
    )
    summary = commands.add_parser("summarize", help="compare the arms written to --out")
    summary.add_argument("--out", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        if peak_ahead(now()):
            print("REFUSE: DeepSeek peak hours start within 50 minutes; nothing was run.")
            return 2
        print(asyncio.run(_run(arguments)))
        return 0
    result = summarize(Path(arguments.out))
    print(json.dumps(result, indent=1))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
