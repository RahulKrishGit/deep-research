"""Offline doubles for the evaluation harness.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


class FakeDataset:
    def __init__(self, name: str, dataset_id: str) -> None:
        self.name = name
        self.id = dataset_id
        self.url = f"https://smith.langchain.test/datasets/{dataset_id}"


class FakeExample:
    def __init__(self, example_id, inputs, outputs, metadata):
        self.id = example_id
        self.inputs = dict(inputs)
        self.outputs = dict(outputs)
        self.metadata = dict(metadata)


class FakeLangSmithClient:
    """Records every dataset call; deletion methods raise on sight."""

    def __init__(self, *, datasets: Sequence[FakeDataset] = ()) -> None:
        self._datasets = {dataset.name: dataset for dataset in datasets}
        self._examples: dict[str, list[FakeExample]] = {
            dataset.name: [] for dataset in datasets
        }
        self.created_datasets: list[str] = []
        self.created_examples: list[dict[str, Any]] = []
        self.updated_examples: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        return dataset_name in self._datasets

    def read_dataset(self, *, dataset_name: str) -> FakeDataset:
        if dataset_name not in self._datasets:
            raise LookupError(dataset_name)
        return self._datasets[dataset_name]

    def create_dataset(self, dataset_name: str, **kwargs: Any) -> FakeDataset:
        dataset = FakeDataset(dataset_name, uuid4().hex[:8])
        self._datasets[dataset_name] = dataset
        self._examples[dataset_name] = []
        self.created_datasets.append(dataset_name)
        return dataset

    def list_examples(self, *, dataset_name: str, **kwargs: Any):
        return list(self._examples.get(dataset_name, []))

    def create_examples(self, *, dataset_id: str, examples: Sequence[Mapping]):
        name = next(
            key
            for key, dataset in self._datasets.items()
            if dataset.id == dataset_id
        )
        for payload in examples:
            self.created_examples.append(dict(payload))
            self._examples[name].append(
                FakeExample(
                    uuid4().hex[:8],
                    payload.get("inputs", {}),
                    payload.get("outputs", {}),
                    payload.get("metadata", {}),
                )
            )

    def update_examples(self, *, updates: Sequence[Mapping]):
        for payload in updates:
            self.updated_examples.append(dict(payload))

    def create_feedback(self, run_id, key, **kwargs: Any) -> None:
        self.feedback.append({"run_id": run_id, "key": key, **kwargs})

    def delete_dataset(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("evaluation must never delete a dataset")

    def delete_example(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("evaluation must never delete an example")

    def delete_feedback(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("evaluation must never delete feedback")


@dataclass
class FakeExperimentResults:
    experiment_name: str
    url: str | None
    comparison_url: str | None = None
    comparison_error: Exception | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def get_comparison_url(self) -> str | None:
        if self.comparison_error is not None:
            raise self.comparison_error
        return self.comparison_url


def _example_payload(example: Any) -> dict[str, Any]:
    if isinstance(example, Mapping):
        return dict(example)
    return {
        "inputs": dict(example.inputs),
        "outputs": dict(example.outputs),
        "metadata": dict(example.metadata),
    }


class FakeEvaluateRunner:
    """A stand-in for ``langsmith.evaluation.aevaluate``.

    Records its keyword arguments, then drives the target and every
    evaluator ``num_repetitions`` times per example, sequentially.
    """

    def __init__(
        self,
        *,
        examples: Sequence[Any] = (),
        results: FakeExperimentResults | None = None,
    ) -> None:
        self.examples = list(examples)
        self.results = results
        self.calls: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.summary_feedback: list[dict[str, Any]] = []

    async def __call__(self, target, /, **kwargs: Any):
        self.calls.append(dict(kwargs))
        evaluators = kwargs.get("evaluators") or []
        summary_evaluators = kwargs.get("summary_evaluators") or []
        data = kwargs.get("data")
        raw_examples = self.examples if isinstance(data, str) else list(data)
        examples = [_example_payload(example) for example in raw_examples]
        runs: list[FakeRun] = []
        example_rows: list[FakeExampleRow] = []
        for _ in range(kwargs.get("num_repetitions", 1)):
            for example in examples:
                outputs = await target(example["inputs"])
                run = FakeRun(outputs=outputs)
                example_row = FakeExampleRow(example)
                feedback = []
                for evaluator in evaluators:
                    result = evaluator(run, example_row)
                    if hasattr(result, "__await__"):
                        result = await result
                    feedback.append(result)
                self.rows.append({"outputs": outputs, "feedback": feedback})
                runs.append(run)
                example_rows.append(example_row)
        for evaluator in summary_evaluators:
            try:
                result = evaluator(runs, example_rows)
                self.summary_feedback.extend(list(result.get("results", [])))
            except Exception:
                # LangSmith 0.10.11 logs and swallows summary-evaluator failures.
                continue
        return self.results or FakeExperimentResults(
            experiment_name=kwargs.get("experiment_prefix", "experiment"),
            url="https://smith.langchain.test/experiments/1",
            rows=self.rows,
        )


@dataclass
class FakeRun:
    outputs: dict[str, Any]
    id: str = "run-1"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeExampleRow:
    payload: Mapping[str, Any]

    @property
    def inputs(self) -> dict[str, Any]:
        return dict(self.payload.get("inputs", {}))

    @property
    def outputs(self) -> dict[str, Any]:
        return dict(self.payload.get("outputs", {}))

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self.payload.get("metadata", {}))


class FakeStructuredProvider:
    """Serves queued structured responses; never touches the network."""

    def __init__(self, responses: Sequence[Any] = ()) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Any, Any, str | None]] = []
        self.last_model_returned: str | None = "deepseek-v4-flash-fake"

    async def complete_structured(
        self, messages, schema, *, agent_name=None
    ):
        self.calls.append((list(messages), schema, agent_name))
        if not self.responses:
            raise AssertionError(f"no scripted response left for {schema}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
