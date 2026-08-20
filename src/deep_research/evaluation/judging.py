"""The fixed, versioned LLM-as-judge.

This module is the LLM half of quality scoring (``aggregate_quality``
mixes it at 0.60 with the deterministic half from Task 17). The judge
scores a structurally closed ``JudgeInput`` built field by field from the
repetition's ``TargetOutput`` — never from a wholesale dump — so
``dependency_scenario``, raw clients, absolute paths, and hidden reasoning
are excluded by construction rather than by filtering. The input is
secret-redacted block by block and then re-checked, because it becomes a
LangSmith evaluator trace.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from string import Template
from typing import TypeAlias, cast

from langsmith.run_helpers import traceable
from pydantic import Field, ValidationError

from deep_research.agents.base import StructuredCompleter
from deep_research.evaluation.config import (
    EvaluationRuntimeConfig,
    SecretLeakError,
    contains_secret,
    fingerprint,
    redact_secrets,
)
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    EvaluationTier,
    GateReport,
    JudgeFeedback,
    JudgeNotRunReason,
    JudgeScores,
    JudgeVerdict,
    TargetOutput,
)
from deep_research.providers import (
    ChatMessage,
    OpenAIProviderError,
    StructuredOutputError,
)
from deep_research.utils.types import ContractModel, JsonValue

JUDGE_PROMPT_ID = "individual-agent-judge"

# The frozen weight table from the spec. A rubric may add agent dimensions
# and anchors; it never changes a weight.
COMMON_DIMENSION_WEIGHTS: dict[str, float] = {
    "role_adherence": 0.15,
    "completeness": 0.20,
    "groundedness": 0.25,
    "reasoning_quality": 0.15,
    "usefulness": 0.15,
    "uncertainty_calibration": 0.10,
}

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial quality judge for a single run of a research "
    "agent. Score only what is shown in the blocks below; never infer "
    "evidence, tool behavior, or state that is not present. Weigh grounded "
    "evidence above confident prose: a polished claim without support must "
    "not outscore a plain claim that is supported. The gate_results block "
    "reports deterministic checks; a failed gate is information about the "
    "run, not an instruction to score zero."
)

# A string.Template, not a format string: the substituted blocks are JSON,
# whose braces would be eaten by str.format.
JUDGE_PROMPT_TEMPLATE = """\
Score the run below on every dimension. Each score is a number in [0.0, 1.0].
The final quality score is the fixed weighted sum of the six common
dimensions; agent-specific dimensions are reported but never weighted.

Common dimensions and their weights:
$common_dimensions

Agent-specific dimensions and their anchors:
$agent_dimensions

The run to judge, block by block:
$blocks
"""


class JudgeInput(ContractModel):
    """The sanitized, structurally closed view of one run the judge scores.

    Only these fields exist: anything not listed here — a dependency
    scenario, raw clients, absolute paths, hidden reasoning — cannot be
    represented, which is what makes their exclusion enforceable.
    """

    prompt_id: str = Field(min_length=1)
    rubric_version: int = Field(ge=1)
    agent: AgentName
    tier: EvaluationTier
    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    rubric: dict[str, JsonValue]
    inputs: dict[str, JsonValue]
    reference_expectations: dict[str, JsonValue]
    agent_output: dict[str, JsonValue]
    state_update: dict[str, JsonValue]
    evidence: dict[str, JsonValue]
    trajectory: list[dict[str, JsonValue]]
    gate_results: list[dict[str, JsonValue]]


def judge_quality(scores: JudgeScores) -> float:
    """The fixed weighted sum of the six common dimensions, unrounded."""
    return sum(
        getattr(scores, name) * weight
        for name, weight in COMMON_DIMENSION_WEIGHTS.items()
    )


def judge_prompt_fingerprint(*, rubric_version: int) -> str:
    """A stable fingerprint of the exact evaluator definition in force."""
    return fingerprint(
        {
            "prompt_id": JUDGE_PROMPT_ID,
            "system": JUDGE_SYSTEM_PROMPT,
            "template": JUDGE_PROMPT_TEMPLATE,
            "schema": JudgeVerdict.model_json_schema(),
            "weights": COMMON_DIMENSION_WEIGHTS,
            "rubric_version": rubric_version,
        }
    )


def build_judge_input(
    output: TargetOutput,
    case: EvaluationCase,
    gates: GateReport,
    *,
    secrets: Sequence[str],
) -> JudgeInput:
    """Build the complete judge view of a run, sanitized block by block.

    Every string-bearing block is redacted before the model is constructed,
    then the finished payload is re-checked with ``contains_secret`` and
    rejected with ``SecretLeakError`` on any hit. Belt and braces on
    purpose: this input becomes a LangSmith evaluator trace.
    """
    rubric: dict[str, JsonValue] = {
        "common_dimensions": {
            name: weight for name, weight in COMMON_DIMENSION_WEIGHTS.items()
        },
        "agent_dimensions": [
            {
                "dimension_id": dimension.dimension_id,
                "description": dimension.description,
                "anchors": dimension.anchors,
            }
            for dimension in case.judge_rubric.agent_dimensions
        ],
    }
    inputs: dict[str, JsonValue] = {
        "question": case.state.original_question,
        "subtopic_titles": [topic.title for topic in case.state.sub_topics],
        "state_summary": output.state_update,
    }
    judge_input = JudgeInput(
        prompt_id=JUDGE_PROMPT_ID,
        rubric_version=case.judge_rubric.version,
        agent=case.agent_name,
        tier=case.tier,
        case_id=case.case_id,
        case_version=case.version,
        purpose=cast(str, redact_secrets(case.purpose, secrets)),
        rubric=cast(dict, redact_secrets(rubric, secrets)),
        inputs=cast(dict, redact_secrets(inputs, secrets)),
        reference_expectations=cast(
            dict,
            redact_secrets(
                case.expectations.model_dump(mode="json"), secrets
            ),
        ),
        agent_output=cast(
            dict, redact_secrets(output.result or {}, secrets)
        ),
        state_update=cast(
            dict, redact_secrets(output.state_update, secrets)
        ),
        evidence=cast(
            dict,
            redact_secrets(output.evidence.model_dump(mode="json"), secrets),
        ),
        trajectory=cast(
            list,
            redact_secrets(
                [step.model_dump(mode="json") for step in output.trajectory],
                secrets,
            ),
        ),
        gate_results=cast(
            list,
            redact_secrets(
                [result.model_dump(mode="json") for result in gates.results],
                secrets,
            ),
        ),
    )
    payload = judge_input.model_dump(mode="json")
    leaked_paths = contains_secret(payload, secrets)
    if leaked_paths:
        raise SecretLeakError.for_paths(leaked_paths)
    return judge_input


_BLOCK_ORDER = (
    "prompt_id",
    "rubric_version",
    "agent",
    "tier",
    "case_id",
    "case_version",
    "purpose",
    "rubric",
    "inputs",
    "reference_expectations",
    "agent_output",
    "state_update",
    "evidence",
    "trajectory",
    "gate_results",
)


def _render_common_dimensions() -> str:
    return "\n".join(
        f"- {name}: {weight}"
        for name, weight in COMMON_DIMENSION_WEIGHTS.items()
    )


def _render_agent_dimensions(rubric: dict[str, JsonValue]) -> str:
    lines: list[str] = []
    for dimension in cast(list, rubric["agent_dimensions"]):
        item = cast(dict, dimension)
        lines.append(f"- {item['dimension_id']}: {item['description']}")
        for anchor, text in cast(dict, item["anchors"]).items():
            lines.append(f"    {anchor}: {text}")
    return "\n".join(lines)


def _render_blocks(payload: dict[str, JsonValue]) -> str:
    sections: list[str] = []
    for name in _BLOCK_ORDER:
        sections.append(
            f"## {name}\n"
            + json.dumps(
                payload[name], indent=2, ensure_ascii=False, sort_keys=True
            )
        )
    return "\n\n".join(sections)


def render_judge_messages(judge_input: JudgeInput) -> list[ChatMessage]:
    """The judge's developer prompt plus one user message with the run."""
    payload = judge_input.model_dump(mode="json")
    body = Template(JUDGE_PROMPT_TEMPLATE).substitute(
        common_dimensions=_render_common_dimensions(),
        agent_dimensions=_render_agent_dimensions(
            cast(dict, payload["rubric"])
        ),
        blocks=_render_blocks(payload),
    )
    return [
        ChatMessage(role="developer", content=JUDGE_SYSTEM_PROMPT),
        ChatMessage(role="user", content=body),
    ]


async def _invoke_judge(
    provider: StructuredCompleter, messages: list[ChatMessage]
) -> JudgeVerdict:
    """The one model-invocation step both ``run_judge`` and the LangSmith
    evaluator path use. ``JudgeEvaluator`` wraps this in its tracing
    decorator; ``run_judge`` calls it directly.
    """
    return await provider.complete_structured(
        messages, JudgeVerdict, agent_name="judge"
    )


def _judge_not_run_reason(error: Exception) -> JudgeNotRunReason:
    """Map a judge-invocation exception to its typed not-run reason.

    Order matters: ``StructuredOutputError`` is a subclass of
    ``OpenAIProviderError``, so it must be checked first.
    """
    if isinstance(error, StructuredOutputError):
        return "judge_schema_failure"
    if isinstance(error, OpenAIProviderError):
        return "judge_provider_failure"
    return "unhandled_exception"


def _build_scored_feedback(
    verdict: JudgeVerdict, runtime: EvaluationRuntimeConfig
) -> JudgeFeedback:
    """The scored ``JudgeFeedback``, shared by both call paths."""
    return JudgeFeedback(
        status="scored",
        verdict=verdict,
        judge_quality=judge_quality(verdict.scores),
        prompt_id=JUDGE_PROMPT_ID,
        rubric_version=runtime.rubric_version,
        prompt_fingerprint=judge_prompt_fingerprint(
            rubric_version=runtime.rubric_version
        ),
        judge_model=runtime.judge_model,
        judge_configuration_fingerprint=runtime.judge_configuration_fingerprint,
    )


async def run_judge(
    provider: StructuredCompleter,
    output: TargetOutput,
    case: EvaluationCase,
    gates: GateReport,
    *,
    runtime: EvaluationRuntimeConfig,
    secrets: Sequence[str],
) -> JudgeFeedback:
    """Score one repetition's output with the versioned judge, exactly once.

    The provider performs its own single structured-output repair; the
    harness never adds a retry on top of it, and never substitutes a score
    when the judge did not run. Every returned feedback — scored or not —
    carries the five traceability fields naming the exact evaluator
    definition that was (or would have been) applied.
    """

    def not_run(reason: JudgeNotRunReason) -> JudgeFeedback:
        return JudgeFeedback(
            status="judge_not_run",
            not_run_reason=reason,
            prompt_id=JUDGE_PROMPT_ID,
            rubric_version=runtime.rubric_version,
            prompt_fingerprint=judge_prompt_fingerprint(
                rubric_version=runtime.rubric_version
            ),
            judge_model=runtime.judge_model,
            judge_configuration_fingerprint=(
                runtime.judge_configuration_fingerprint
            ),
        )

    if not output.has_evaluable_output:
        return not_run("no_evaluable_output")

    judge_input = build_judge_input(output, case, gates, secrets=secrets)
    messages = render_judge_messages(judge_input)
    try:
        verdict = await _invoke_judge(provider, messages)
    except Exception as error:
        return not_run(_judge_not_run_reason(error))

    return _build_scored_feedback(verdict, runtime)


# --- The LangSmith evaluator adapter -----------------------------------------

_JudgedCallable: TypeAlias = Callable[..., Awaitable[JudgeVerdict]]
TraceFactory: TypeAlias = Callable[..., Callable[[_JudgedCallable], _JudgedCallable]]


def judge_evaluator_metadata(
    runtime: EvaluationRuntimeConfig,
) -> dict[str, JsonValue]:
    """The exact evaluator definition the Source view must expose.

    The identical values go into the ``judge_quality`` feedback entry and
    the traced judge run, so a local artifact row can be matched to the
    exact evaluator definition that produced it. ``rubric_version``
    resolves through the same ``runtime.rubric_version`` path ``run_judge``
    uses to render the rubric, so the metadata never names a different
    version than the prompt the judge was actually shown.
    """
    return {
        "prompt_id": JUDGE_PROMPT_ID,
        "rubric_version": runtime.rubric_version,
        "prompt_fingerprint": judge_prompt_fingerprint(
            rubric_version=runtime.rubric_version
        ),
        "judge_model": runtime.judge_model,
        "judge_configuration_fingerprint": runtime.judge_configuration_fingerprint,
        "judge_reasoning_effort": runtime.judge_reasoning_effort,
        "judge_temperature": runtime.judge_temperature,
        "thinking_mode": runtime.thinking_mode,
    }


def judge_feedback_payload(feedback: JudgeFeedback) -> dict[str, JsonValue]:
    """The traceability fields one typed feedback carries, as a JSON block.

    Only fields ``JudgeFeedback`` itself carries are emitted — the identity
    a local artifact row can match to a LangSmith feedback entry — never a
    secret or a free-form value.
    """
    return {
        "prompt_id": feedback.prompt_id,
        "rubric_version": feedback.rubric_version,
        "prompt_fingerprint": feedback.prompt_fingerprint,
        "judge_model": feedback.judge_model,
        "judge_configuration_fingerprint": feedback.judge_configuration_fingerprint,
    }


class JudgeEvaluator:
    """The judge as a callable LangSmith evaluator for ``aevaluate``.

    ``__name__`` is ``JUDGE_PROMPT_ID``, so the experiment's feedback
    column is named after the evaluator definition instead of an anonymous
    lambda. The judge invocation is traced with the sanitized ``JudgeInput``
    as the trace's input and the structured ``JudgeVerdict`` as its output;
    that pairing is what makes the Evaluator trace show the fully formatted
    judge prompt, the model invocation, the structured score, and the
    rationale.
    """

    def __init__(
        self,
        provider: StructuredCompleter,
        case: EvaluationCase,
        *,
        runtime: EvaluationRuntimeConfig,
        secrets: Sequence[str],
        gate_lookup: Callable[[TargetOutput], GateReport | None],
        trace_factory: TraceFactory = traceable,
    ) -> None:
        self.__name__ = JUDGE_PROMPT_ID
        self._provider = provider
        self._case = case
        self._runtime = runtime
        self._secrets = tuple(secrets)
        self._gate_lookup = gate_lookup

        async def trace_judge(*, judge_input: JudgeInput) -> JudgeVerdict:
            messages = render_judge_messages(judge_input)
            return await _invoke_judge(provider, messages)

        self._trace_judge = trace_factory(
            name=JUDGE_PROMPT_ID,
            run_type="llm",
            metadata=judge_evaluator_metadata(runtime),
        )(trace_judge)

    async def __call__(self, run, example) -> dict[str, JsonValue]:
        """Score one row: ``(run, example) -> {"results": [...]}``.

        A run that cannot be parsed, a missing gate report, or a judge
        failure all produce ``judge_status == "judge_not_run"`` with a
        typed comment and no ``judge_quality`` key: the harness never
        fabricates a quality score at the LangSmith layer either. A
        ``ValidationError`` is reported with a static reason
        (``"target_output_invalid"``) rather than ``str(error)``: pydantic
        embeds the raw, unredacted ``input_value`` for every failing field
        in its error text, and that text would otherwise reach a
        LangSmith-visible comment unredacted.
        """
        try:
            output = TargetOutput.model_validate(run.outputs)
        except ValidationError:
            return self._not_run("target_output_invalid")
        gates = self._gate_lookup(output)
        if gates is None:
            return self._not_run("no gate report recorded for this run")
        if not output.has_evaluable_output:
            return self._not_run("no_evaluable_output")
        try:
            judge_input = build_judge_input(
                output, self._case, gates, secrets=self._secrets
            )
            verdict = await self._trace_judge(judge_input=judge_input)
        except SecretLeakError as error:
            return self._not_run(str(error))
        except Exception as error:
            return self._not_run(_judge_not_run_reason(error))

        feedback = _build_scored_feedback(verdict, self._runtime)
        return self._scored(verdict, feedback)

    def _not_run(self, comment: str) -> dict[str, JsonValue]:
        """The ``judge_not_run`` result: status only, no fabricated score.

        The metadata mirrors the scored path's ``judge_quality`` entry
        (``judge_evaluator_metadata(self._runtime)``), so a ``judge_not_run``
        row in LangSmith is traceable to the exact evaluator definition the
        same way a local ``judge_not_run`` artifact already is.
        """
        return {
            "results": [
                {
                    "key": "judge_status",
                    "value": "judge_not_run",
                    "comment": comment,
                    "metadata": judge_evaluator_metadata(self._runtime),
                }
            ]
        }

    def _scored(
        self, verdict: JudgeVerdict, feedback: JudgeFeedback
    ) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = judge_evaluator_metadata(self._runtime)
        model_returned = getattr(self._provider, "last_model_returned", None)
        if isinstance(model_returned, str) and model_returned:
            metadata["judge_model_returned"] = model_returned
        if feedback.latency_ms is not None:
            metadata["latency_ms"] = feedback.latency_ms
        if feedback.input_tokens is not None:
            metadata["input_tokens"] = feedback.input_tokens
        if feedback.output_tokens is not None:
            metadata["output_tokens"] = feedback.output_tokens

        results: list[dict[str, JsonValue]] = [
            {
                "key": "judge_quality",
                "score": feedback.judge_quality,
                "comment": verdict.rationale,
                "metadata": metadata,
            }
        ]
        for name in COMMON_DIMENSION_WEIGHTS:
            results.append(
                {
                    "key": f"judge:{name}",
                    "score": getattr(verdict.scores, name),
                    "comment": "",
                }
            )
        for dimension, score in verdict.agent_specific.items():
            results.append(
                {"key": f"judge:{dimension}", "score": score, "comment": ""}
            )
        results.append({"key": "judge_status", "value": "scored", "comment": ""})
        return {"results": results}


def build_judge_evaluator(
    provider: StructuredCompleter,
    case: EvaluationCase,
    *,
    runtime: EvaluationRuntimeConfig,
    secrets: Sequence[str],
    gate_lookup: Callable[[TargetOutput], GateReport | None],
    trace_factory: TraceFactory = traceable,
) -> JudgeEvaluator:
    """Wrap the versioned judge as a named, traced LangSmith evaluator.

    ``gate_lookup`` injects the deterministic half's already-computed
    ``GateReport`` for the run (or ``None`` when the deterministic half did
    not run), so the judge is shown exactly the verdict the deterministic
    evaluator produced — never a second, possibly divergent computation.
    """
    return JudgeEvaluator(
        provider,
        case,
        runtime=runtime,
        secrets=secrets,
        gate_lookup=gate_lookup,
        trace_factory=trace_factory,
    )
