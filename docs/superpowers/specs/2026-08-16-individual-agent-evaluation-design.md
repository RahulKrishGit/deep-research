# Individual Agent Evaluation Harness Design

**Status:** Approved

**Date:** 2026-08-16

**Branch:** `plan/individual-agent-evaluation`

## Goal

Provide a repeatable, LangSmith-backed way to evaluate each of the six research
agents independently before running end-to-end graph evaluations. The harness
must make it easy to identify a weak agent, rerun only that agent or one of its
cases, inspect its traces, compare experiments, improve the implementation, and
verify the improvement against the same inputs and rubrics.

The six agents are:

- Planner
- Researcher
- Source Evaluator
- Fact Checker
- Synthesizer
- Critic

## Approved Decisions

- Expose a dedicated CLI at `python -m deep_research.evaluation`.
- Keep evaluation orchestration separate from the existing end-to-end research
  CLI.
- Use a two-tier framework:
  - controlled evaluation with real OpenAI and deterministic external
    collaborators;
  - live evaluation with real OpenAI and applicable real external services.
- Define three controlled cases per agent: normal, challenging, and
  failure-recovery.
- Run every controlled case three times.
- Define one live case per agent and run it once.
- Run an LLM-as-judge evaluator on every completed run that has evaluable
  output, including controlled and live runs.
- Combine deterministic hard gates, automated quality scoring, and final
  manual trace review.
- Store the cases as visible LangSmith datasets and each invocation as a
  LangSmith experiment.
- Store a durable local JSON result alongside the LangSmith experiment.
- Defer end-to-end graph evaluation to a follow-up specification after the six
  agents pass individual evaluation.

## Scope

### In Scope

- A reusable evaluation package under `src/deep_research/evaluation/`.
- A dedicated evaluation CLI.
- Typed, versioned case definitions for all six agents.
- Controlled and live dependency construction.
- A production-parity single-agent factory.
- LangSmith dataset synchronization and experiment execution.
- Three controlled repetitions and one live repetition.
- Deterministic hard gates and agent-specific code evaluators.
- A fixed, versioned LLM-as-judge rubric applied to every evaluable run.
- Aggregate scoring and pass/fail decisions.
- Terminal summaries and local JSON artifacts.
- Links to LangSmith experiments and representative traces.
- Offline tests for the evaluation framework itself.

### Out of Scope

- End-to-end graph datasets, evaluators, and experiment commands.
- Continuous production or online evaluation.
- Automatic prompt rewriting or agent self-improvement.
- Automatically changing models or selecting a winning model.
- Automatically approving an agent without human review.
- A notebook, web dashboard, or new API surface for evaluations.
- Persisting evaluation data into normal research memory or report locations.

## Current Repository Fit

All agents already expose the common asynchronous contract:

```python
async def run(self, state: ResearchState) -> AgentRun[Any]: ...
```

The production runtime already assembles all six agents in
`deep_research.runtime.assembly.build_agents`. The graph already runs agents
inside a `Tracker.session_span`, and each agent opens its own `agent_span` plus
any nested LLM, ReAct, tool, and memory spans.

The evaluation harness will reuse those contracts and tracing primitives. It
will not create a second agent implementation or a second observability model.

## Architecture

Add this package:

```text
src/deep_research/evaluation/
|-- __init__.py
|-- __main__.py
|-- cli.py
|-- models.py
|-- config.py
|-- factory.py
|-- dependencies.py
|-- datasets.py
|-- targets.py
|-- evaluators.py
|-- judging.py
|-- runner.py
|-- reporting.py
`-- cases/
    |-- __init__.py
    |-- planner.py
    |-- researcher.py
    |-- source_evaluator.py
    |-- fact_checker.py
    |-- synthesizer.py
    `-- critic.py
```

### Module Responsibilities

`models.py`
: Defines strict models for agent names, tiers, cases, expectations, hard-gate
  results, rubric scores, repetition results, experiment summaries, and local
  artifacts.

`config.py`
: Resolves evaluation settings from `ConfigSettings`, CLI arguments, and
  environment variables, including the effective per-agent and judge reasoning
  efforts. It never serializes provider secrets.

`factory.py`
: Constructs exactly one production-configured agent. Production
  `build_agents()` and evaluation construction must share the same constructor
  mapping so the harness cannot drift from production wiring.

`dependencies.py`
: Builds fresh controlled or live dependencies for one case repetition.

`datasets.py`
: Validates the local case registry and synchronizes secret-free examples to
  LangSmith datasets by stable case identity.

`targets.py`
: Adapts one `EvaluationCase` into a LangSmith target function. It opens the
  existing session trace, runs the selected agent, and returns only typed,
  redacted evaluation outputs.

`evaluators.py`
: Implements general and agent-specific deterministic checks and scores.

`judging.py`
: Builds the fixed LLM-as-judge input, invokes the configured judge model with
  structured output, validates the response, and reports evaluator metadata.

`runner.py`
: Runs one agent or the suite, requests LangSmith repetitions, aggregates
  scores, applies thresholds, and writes the final artifact.

`reporting.py`
: Produces concise terminal output, experiment and trace links, and stable JSON.

`cases/`
: Holds the source-of-truth case registry. Cases are code-backed typed fixtures
  because `ResearchState` and dependency behavior are richer than a safe YAML
  representation.

## Production-Parity Agent Factory

Introduce a shared internal constructor mapping keyed by the six canonical
agent names. It receives the same settings, provider, tracker, tools,
scratchpad/session identity, and optional reputation source that production
uses.

Production `build_agents()` calls this mapping six times. Evaluation calls it
once. This targeted refactor is required to avoid either of these failures:

- assembling all six agents merely to test one;
- duplicating constructor settings in evaluation code and silently drifting
  from production.

The factory must fail at construction when an agent's declared tool is absent,
matching existing `AgentToolset` behavior.

## Evaluation Case Contract

Each case includes:

```python
class EvaluationCase(ContractModel):
    case_id: str
    version: int
    agent_name: AgentName
    tier: EvaluationTier
    title: str
    purpose: str
    state: ResearchState
    dependency_scenario: str
    expectations: CaseExpectations
    judge_rubric: JudgeRubric
    metadata: dict[str, JsonValue]
```

Case identifiers are stable and kebab-case. A repetition receives a deep copy
of `state` and a newly constructed dependency bundle. No mutable collaborator
is shared between repetitions.

## Controlled Case Matrix

### Planner

1. `focused-decomposition`
   - Decompose a focused research question into 3-7 distinct, prioritized
     subtopics.
   - Check coverage, non-overlap, ordering, and useful search framing.
2. `ambiguous-scope`
   - Scope a broad or ambiguous question without inventing constraints.
   - Check that ambiguity is handled through explicit, balanced subtopics.
3. `planning-tool-failure`
   - Inject a failed memory lookup or search followed by deterministic recovery
     evidence.
   - Check that the plan is still valid and the recoverable failure is recorded.

### Researcher

1. `multi-source-coverage`
   - Research planned subtopics using deterministic search and page content.
   - Check per-subtopic coverage, source diversity, and grounded findings.
2. `conflicting-evidence`
   - Provide sources that disagree or offer incomplete evidence.
   - Check that findings preserve uncertainty and do not collapse disagreement.
3. `partial-search-failure`
   - Fail selected search or scraping calls while keeping alternative evidence
     available.
   - Check bounded recovery, useful partial results, and recorded errors.

### Source Evaluator

1. `strong-and-weak-sources`
   - Mix authoritative, low-authority, and obviously weak sources.
   - Check score ordering, rationales, and low-confidence flags.
2. `corroboration-recency-reputation`
   - Make authority, corroboration, recency, and reputation point in different
     directions.
   - Check balanced scoring rather than reliance on a single signal.
3. `reputation-provider-failure`
   - Fail reputation lookup or model scoring for part of the source set.
   - Check deterministic fallback scores, continued evaluation, and errors.

### Fact Checker

1. `mixed-verdicts`
   - Include supported, refuted, and insufficient-evidence claims.
   - Check verdict correctness, confidence, rationale, and evidence linkage.
2. `independent-domain-evidence`
   - Include apparent corroboration from dependent or duplicate domains.
   - Check that independence requirements are enforced.
3. `verification-search-failure`
   - Fail selected verification searches while leaving some evidence available.
   - Check conservative verdicts, recoverable errors, and bounded execution.

### Synthesizer

1. `complete-cited-report`
   - Provide a complete set of findings, evaluated sources, and verified claims.
   - Check structure, citation validity, coverage, and useful synthesis.
2. `conflict-and-limitations`
   - Provide conflicting evidence and explicit research limitations.
   - Check uncertainty, caveats, and absence of overstatement.
3. `write-or-memory-failure`
   - Fail document writing or selected memory saves.
   - Check that the report remains truthful in state, failures are recorded, and
     no false persistence claim is made.

### Critic

1. `approve-strong-report`
   - Review a well-supported, complete report.
   - Check scoring and an approval route consistent with the rubric.
2. `request-more-research`
   - Review a report with material evidence and coverage gaps.
   - Check actionable critique and a route back to research.
3. `missing-evidence-or-budget-exhausted`
   - Exercise missing evidence and the final allowed macro iteration.
   - Check conservative scoring and deterministic route discipline.

## Live Case Matrix

Each agent has one live case:

- `planner-live-scope`
- `researcher-live-evidence`
- `source-evaluator-live-ranking`
- `fact-checker-live-verification`
- `synthesizer-live-report`
- `critic-live-review`

Live cases still start from curated state so only the selected agent is under
test. They use the applicable real dependencies:

- OpenAI for every agent;
- Tavily and HTTP content retrieval for agents that declare those tools;
- Chroma-backed evaluation memory where the agent reads or writes memory;
- real document writes for Synthesizer;
- real LangSmith tracing for every agent.

## Dependency Tiers

| Dependency | Controlled | Live |
|---|---|---|
| OpenAI target model | Real | Real |
| LangSmith | Real | Real |
| Starting `ResearchState` | Curated and fixed | Curated and fixed |
| Tool responses | Deterministic | Real where applicable |
| Tavily | Prohibited | Real where applicable |
| HTTP scraping | Prohibited | Real where applicable |
| Long-term memory | Isolated deterministic double | Isolated Chroma collection |
| Procedural memory | Isolated deterministic double | Isolated evaluation registry |
| Document output | Isolated deterministic sink | Real evaluation-only directory |
| Clock | Fixed when relevant | Real |

Controlled evaluation is not an offline unit test: it deliberately makes real
OpenAI and LangSmith calls. Only non-model application dependencies are made
deterministic.

Live evaluation must never use the normal research collection, procedural
registry, or report directory. It receives an experiment-specific namespace
under the evaluation output root.

## Model and Cost Policy

> **Superseded 2026-08-20** by
> `docs/superpowers/specs/2026-08-20-deepseek-evaluation-cutover-design.md`.
> This section records the current policy after that cutover; the rest of
> this document is unchanged and remains the architectural record.

The evaluation baseline uses DeepSeek V4 Flash for both the target agents and
the judge, and a local, offline embedding model for live-tier memory.

| Role | Model |
|---|---|
| All six target agents | `deepseek-v4-flash` |
| LLM-as-judge evaluator | `deepseek-v4-flash` |
| Live evaluation embeddings | local (chromadb default ONNX model, 384 dimensions) |

Pricing, looked up against `https://api-docs.deepseek.com/quick_start/pricing`
— informational, and subject to a recheck before any run that spends money:

| | Cache hit | Cache miss | Output |
|---|---|---|---|
| Off-peak | $0.007 / M tokens | $0.22 / M tokens | $0.66 / M tokens |
| Peak (01:00-04:00, 06:00-10:00 UTC) | $0.014 / M tokens | $0.44 / M tokens | $1.32 / M tokens |

There is no embeddings pricing row: the local embedding provider has no API
key and no per-call cost.

The dated model identifier observed on the pricing page is
`DeepSeek-V4-Flash-0731`, distinct from the bare `deepseek-v4-flash` alias
used in requests. Whichever identifier the provider actually returns is
recorded in experiment metadata as `target_model_returned`; there is no
silent fallback between the two.

### Reasoning-Effort Policy

Reasoning effort is explicit and independently configurable for every target
agent and for the judge. The approved initial baseline is:

| Component | Effective effort |
|---|---|
| Planner | `max` |
| Researcher | `high` |
| Source Evaluator | `high` |
| Fact Checker | `max` |
| Synthesizer | `max` |
| Critic | `max` |
| LLM-as-judge evaluator | `max` |

Thinking mode is `enabled` for every call; no evaluation case uses `disabled`,
and the typed runtime configuration makes `disabled` unrepresentable. DeepSeek
V4 Flash supports exactly `high` and `max` with thinking enabled, which is why
the original `low`/`medium` levels map onto them as above.

Target effort resolves in this order:

1. an invocation-level `--reasoning-effort` override for the selected agent;
2. that agent's `target_reasoning_effort_overrides` entry;
3. the global `target_reasoning_effort` default.

Judge effort resolves from an invocation-level `--judge-reasoning-effort`
override, then `judge_reasoning_effort`. The effective target and judge efforts
are frozen before experiment creation. One experiment never mixes reasoning
profiles across repetitions. Changing either effort produces a distinct
configuration fingerprint and experiment, while reusing the same dataset.

The baseline deliberately assigns `low` to the repeated, tool-heavy Researcher
and structured Source Evaluator; it assigns `medium` to the agents that perform
decomposition, reconciliation, synthesis, or holistic review. The judge uses
`high` because it supplies most of the numerical quality score and otherwise
uses the same model as the target. `xhigh`, `max`, and Pro mode are not baseline
settings; they require a measured quality gain in focused experiments before
adoption.

The target provider applies the resolved agent effort to both ordinary and
structured-output calls, including a structured-output repair attempt. The
separate judge provider applies only the resolved judge effort. No prompt asks a
model to simulate a reasoning level; the value is sent through the Responses
API reasoning configuration.

OpenAI currently lists only the `gpt-5.6-luna` alias and no dated Luna snapshot.
Every experiment therefore records the requested alias and provider-returned
model identifier, along with the complete model configuration. This preserves
the strongest available comparison evidence but cannot guarantee an unchanged
backend if OpenAI moves the alias. If a dated Luna snapshot becomes available,
adopting it requires an explicit configuration and evaluation-version change.
No agent-specific model overrides are enabled in the initial baseline.
Controlled and live tiers use the same target and judge models so the tier
comparison does not introduce a model change.

At the time of this design, OpenAI lists GPT-5.6 Luna at $0.20 per million input
tokens, $0.02 per million cached input tokens, and $1.20 per million output
tokens. It lists `text-embedding-3-small` at $0.02 per million input tokens.
These prices are informational and must be rechecked before implementation or
when a model change is proposed.

Using the same small model as both target and judge minimizes cost but can
produce correlated weaknesses and less reliable grading than a stronger judge.
The deterministic gates, fixed rubric, three controlled repetitions, score
floors, and mandatory manual trace review remain required safeguards. A future
experiment may explicitly override the judge model for comparison, but a model
override never changes the stored baseline result.

Preflight verifies that the configured model identifiers are accessible to the
current OpenAI project. If `gpt-5.6-luna` is unavailable, evaluation fails with
a configuration error and does not silently fall back to another model.
Selecting a replacement requires an explicit configuration and rubric-version
change so comparisons remain interpretable.

## LangSmith Datasets

Create two versioned datasets per agent:

```text
deep-research-planner-controlled-v1
deep-research-planner-live-v1
deep-research-researcher-controlled-v1
deep-research-researcher-live-v1
...
```

The datasets are visible in the active LangSmith workspace under **Datasets &
Experiments**, including the EU LangSmith website when
`LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`.

### Dataset Contents

Each example contains:

- secret-free inputs sufficient to reconstruct the case;
- reference expectations used only by evaluators;
- agent, tier, case ID, case version, and rubric version metadata;
- no API keys, clients, local absolute paths, or production memory content.

### Synchronization Rules

- The code registry is the source of truth.
- Dataset names include the dataset schema version.
- Examples are matched by stable case ID and case version.
- An unchanged dataset is reused.
- A semantic case or reference change increments its case version.
- A breaking dataset-contract change creates a new dataset version.
- Synchronization never deletes unrelated remote examples.
- Duplicate case IDs or conflicting versions fail before model execution.

## LangSmith Experiments

Use the installed LangSmith SDK's asynchronous evaluation API. Controlled
experiments use:

```python
await aevaluate(
    target,
    data=dataset_name,
    evaluators=evaluators,
    experiment_prefix=experiment_prefix,
    num_repetitions=3,
    max_concurrency=1,
    metadata=experiment_metadata,
)
```

Live experiments use `num_repetitions=1` and `max_concurrency=1`.

Sequential execution is the default to reduce rate-limit noise, keep logs
readable, and make live side effects easier to isolate.

Experiment names use this shape:

```text
<agent>-<tier>-<UTC timestamp>-<short Git SHA>
```

Experiment metadata includes:

- agent and tier;
- Git commit and dirty-worktree flag;
- target model, effective reasoning effort, and effective model configuration;
- judge model, effective reasoning effort, and effective judge configuration;
- case registry version;
- rubric version;
- tool/dependency mode;
- application configuration fingerprint;
- target prompt fingerprints;
- evaluation package version.

LangSmith is the primary interactive surface for outputs, feedback, averages,
standard deviation across repetitions, traces, and experiment comparisons.

## Trace Structure

The LangSmith experiment target wraps the existing project tracker rather than
reimplementing spans. A successful run contains:

```text
LangSmith experiment example
`-- research.session
    `-- selected agent
        |-- ReAct iterations, when applicable
        |-- LLM spans
        |-- tool spans
        `-- memory spans, when applicable
```

The target returns the project `trace_url` and records it in the local artifact.
If the SDK's experiment wrapper and the existing tracker create sibling roots
instead of a nested tree, implementation must correlate them through stable
experiment, case, repetition, and session metadata and expose both links. A
focused compatibility test decides the exact nesting behavior before the full
runner is implemented.

Every trace is tagged with:

- `evaluation`;
- agent;
- tier;
- case ID and version;
- repetition;
- experiment name;
- Git SHA;
- model and rubric versions.

## Deterministic Hard Gates

Hard gates cannot be offset by a high quality score.

### General Gates

- Agent construction succeeds using production-parity wiring.
- The run completes without an unhandled exception.
- `AgentRun`, result, and `ResearchStateUpdate` validate.
- Required output fields and artifacts are present.
- Iteration and tool budgets are respected.
- Errors use typed project contracts.
- Citations reference evidence provided to or retrieved by the case.
- No known secret appears in outputs, errors, artifacts, evaluator inputs, or
  trace metadata.
- Controlled runs make no prohibited external tool or persistent-memory calls.
- A valid LangSmith trace is created.
- The tracker records no LangSmith transport failure.

### Agent-Specific Gates

Planner
: Produces 3-7 distinct, prioritized, valid subtopics and preserves the original
  question.

Researcher
: Covers the selected subtopics or records an explicit reason, produces sourced
  findings, and does not invent source URLs.

Source Evaluator
: Produces one valid evaluation per canonical source, finite bounded scores,
  and required low-confidence flags.

Fact Checker
: Produces valid verdicts, links evidence, respects independent-domain rules,
  and uses insufficient evidence conservatively.

Synthesizer
: Produces a valid report, uses only known citations, represents limitations,
  and reports persistence failures truthfully.

Critic
: Produces bounded component scores, actionable critique, and a routing decision
  consistent with score thresholds and remaining iteration budget.

## LLM-as-Judge Evaluation

The judge runs for every controlled and live repetition that produces evaluable
output, even when a deterministic hard gate fails. A setup failure or unhandled
exception that produces no output is marked `judge_not_run` with a typed reason;
the harness never fabricates a quality score.

The judge receives only:

- the case purpose and rubric;
- safe input and reference expectations;
- the typed agent output and state update;
- source/evidence context made available to the agent;
- safe tool-trajectory summaries;
- deterministic gate results.

It does not receive API keys, raw clients, local production paths, or hidden
chain-of-thought.

### Judge Configuration

- `evaluation.judge_model` is configurable.
- Its baseline value is the model from the Model and Cost Policy.
- An explicit override is recorded in experiment metadata and the local
  artifact; there is no implicit fallback to another model.
- `evaluation.judge_reasoning_effort` is independent from every target-agent
  effort and defaults to the approved `high` baseline.
- Judge temperature is fixed at `0.0`.
- Judge output uses a strict structured schema.
- The rubric prompt and schema are versioned and fingerprinted.
- Judge model, configuration, rubric version, raw structured score, and concise
  rationale are recorded as LangSmith feedback and in the local artifact.

### LangSmith Judge Visibility

Each judge is registered as a named LangSmith evaluator attached to the
experiment. For every completed judge evaluation, the LangSmith experiment UI
must provide:

- a **Source** link that exposes the versioned judge prompt template, rubric,
  output schema, and evaluator definition;
- an **Evaluator trace** link that exposes the sanitized, fully formatted judge
  input, judge model invocation, structured score, and concise rationale;
- latency and token-usage data when the judge provider reports them; and
- evaluator metadata containing the prompt identifier, rubric version, prompt
  fingerprint, judge model, and judge configuration fingerprint.

The same prompt identifier, rubric version, and fingerprints are stored in the
local JSON artifact so a local result can be matched to the exact evaluator
source shown in LangSmith. Prompt and evaluator traces must pass the same
redaction rules as target traces: they contain no API keys, raw clients, local
production paths, production memory, or hidden chain-of-thought.

If a judge is expected to run but its evaluator source or evaluator trace cannot
be opened in LangSmith, the repetition fails the trace-availability requirement.
Runs correctly classified as `judge_not_run` because no evaluable target output
exists retain their typed reason and are exempt from creating a nonexistent
judge trace.

### Judge Dimensions

All agents are scored for:

- role adherence;
- completeness;
- groundedness;
- reasoning quality visible in the output and trajectory summary;
- usefulness;
- uncertainty calibration.

Agent-specific rubrics add dimensions such as decomposition quality, source
diversity, verdict discipline, citation faithfulness, or critique actionability.

The common judge score uses these fixed weights:

| Dimension | Weight |
|---|---:|
| Role adherence | 0.15 |
| Completeness | 0.20 |
| Groundedness | 0.25 |
| Visible reasoning and trajectory quality | 0.15 |
| Usefulness | 0.15 |
| Uncertainty calibration | 0.10 |

Agent-specific rubrics define the observable anchors for these dimensions; they
do not silently change the weights. A later rubric version may deliberately
change weights, but experiment metadata must make that version difference
explicit.

### Score Composition

Every case defines deterministic quality metrics and weights that sum to `1.0`.
The deterministic evaluator returns their weighted score in `[0.0, 1.0]`. The
judge returns the weighted common-dimension score in the same range.

The repetition aggregate is:

```text
aggregate_quality = (0.40 * deterministic_quality) + (0.60 * judge_quality)
```

The runner calculates this value without intermediate rounding and renders it
to two decimals only for display. Hard gates remain separate and cannot be
offset by either component. If judge feedback is required but unavailable, the
repetition has no aggregate quality score and fails.

## Pass and Approval Rules

### Controlled Experiment

An agent receives `AUTOMATED PASS` only when:

- all hard gates pass in all nine repetitions;
- every repetition's aggregate quality score is at least `0.65`;
- each case's three-repetition average is at least `0.80`;
- no severe error or secret-redaction failure occurs;
- every evaluable repetition has judge feedback.

One failed repetition fails the controlled experiment but does not stop the
remaining cases or erase completed results.

### Manual Review

After automated passage, status is `REVIEW REQUIRED`. The CLI links the
lowest-scoring repetition for each of the three cases. The user reviews those
three traces in LangSmith before considering the agent approved.

Manual review checks:

- whether the visible reasoning trajectory is sensible;
- whether tool choices are justified;
- whether output quality matches the numeric score;
- whether errors and uncertainty are represented honestly;
- whether prompts or tool responses expose unexpected data.

The initial implementation reports this gate but does not invent an automatic
human-approval state.

### Live Experiment

The live run passes only when:

- all applicable hard gates pass;
- the LangSmith trace is available without tracker errors;
- required real dependencies were actually exercised;
- aggregate quality score is at least `0.75`;
- judge feedback is present.

The live run is manually invoked after controlled review; it is never launched
automatically by the controlled command.

## CLI

### Commands

```powershell
# List agents, tiers, cases, repetitions, and dataset names.
python -m deep_research.evaluation list

# Run all three controlled cases for one agent, three times each.
python -m deep_research.evaluation agent researcher

# Run one controlled case, still with three repetitions.
python -m deep_research.evaluation agent researcher `
  --case conflicting-evidence

# Run the selected agent's single live case once.
python -m deep_research.evaluation agent researcher --tier live

# Compare one agent at a different effort without editing the baseline config.
python -m deep_research.evaluation agent researcher `
  --reasoning-effort medium

# Run controlled experiments for all six agents.
python -m deep_research.evaluation suite
```

### Shared Options

- `--config <path>`: configuration file, default `config.yaml`.
- `--tier controlled|live`: default `controlled`.
- `--case <case-id>`: only valid for `agent`.
- `--output-directory <path>`: override evaluation artifact root.
- `--experiment-prefix <text>`: optional human-readable prefix.
- `--reasoning-effort <level>`: target-agent override for `agent`; invalid for
  `suite` so a suite cannot silently mix the approved per-agent profile.
- `--judge-reasoning-effort <level>`: judge override for `agent` or `suite`; a
  suite override applies uniformly to every judge call in that invocation.
- `--verbose`: print per-repetition gate and evaluator summaries without
  printing secrets or complete model payloads.

The fixed controlled repetition count and live repetition count are config
defaults. CLI overrides for those counts are deliberately omitted initially so
the canonical workflow stays repeatable.

### Agent Names

User-facing names are:

- `planner`
- `researcher`
- `source-evaluator`
- `fact-checker`
- `synthesizer`
- `critic`

Unknown or ambiguous names fail with exit code `2` and list valid values.

### Terminal Summary

```text
Researcher - controlled
Cases:       3/3 passed
Repetitions: 9/9 completed
Hard gates:  9/9 passed
Mean score:  0.86
Status:      REVIEW REQUIRED

Experiment:  <LangSmith experiment URL>
Review:
  multi-source-coverage    <lowest-scoring trace URL>
  conflicting-evidence     <lowest-scoring trace URL>
  partial-search-failure   <lowest-scoring trace URL>
Results: output/evaluations/researcher/<experiment-id>/results.json
```

### Exit Codes

- `0`: requested experiments achieved automated pass.
- `1`: one or more requested experiments completed but failed gates or scores.
- `2`: invalid CLI usage, unknown agent/case, or invalid local case registry.
- `3`: configuration, credential, dataset synchronization, or LangSmith trace
  infrastructure failure prevented a valid experiment.
- `130`: interrupted by the user.

## Local Artifacts

Write artifacts under:

```text
output/evaluations/<agent>/<experiment-id>/results.json
```

For the suite command, also write:

```text
output/evaluations/suite/<suite-id>/summary.json
```

The artifact contains:

- schema version;
- experiment and dataset identifiers and URLs;
- Git and configuration metadata;
- requested and provider-returned model identifiers, effective target and judge
  reasoning efforts, reasoning mode, and configuration fingerprints;
- case and repetition results;
- hard-gate results;
- deterministic and judge scores;
- evaluator comments;
- trace URLs;
- aggregate statistics;
- final automated status;
- redacted error records.

Artifacts must round-trip through strict Pydantic models and JSON. They never
contain raw API keys, client objects, or unredacted provider exceptions.

## Configuration

Add non-secret evaluation defaults to `config.yaml` and typed configuration:

```yaml
evaluation:
  controlled_repetitions: 3
  controlled_case_average_threshold: 0.80
  controlled_repetition_floor: 0.65
  live_repetitions: 1
  live_threshold: 0.75
  target_model: deepseek-v4-flash
  target_reasoning_effort: max
  target_reasoning_effort_overrides:
    researcher: high
    source_evaluator: high
  judge_model: deepseek-v4-flash
  judge_reasoning_effort: max
  embedding_model: local
  judge_temperature: 0.0
  max_concurrency: 1
  output_directory: output/evaluations/
  dataset_version: 1
  rubric_version: 1
```

Runtime secrets remain environment-only:

- `DEEPSEEK_API_KEY`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `LANGSMITH_ENDPOINT`, including the EU endpoint when applicable
- optional `LANGSMITH_WORKSPACE_ID`
- `TAVILY_API_KEY` for live cases that require search

Controlled mode requires the selected chat provider's key and LangSmith
credentials. Live mode also requires only the external credentials applicable
to the selected agent. `OPENAI_API_KEY` is no longer required anywhere in the
evaluation harness.

## Error Handling

### Preflight Failures

Fail before creating an experiment when:

- the case registry is invalid or duplicated;
- a requested agent or case does not exist;
- required credentials are absent;
- the configured target, judge, or applicable embedding model is inaccessible;
- a reasoning effort is invalid, an override key is unknown, or Pro mode is
  requested by the initial harness;
- the agent cannot be built with production-parity wiring;
- the dataset cannot be resolved or safely synchronized;
- the output root cannot be created;
- controlled dependency guards cannot be installed.

### Repetition Failures

- Capture typed setup, provider, tool, validation, judging, trace, and artifact
  failures independently.
- Continue remaining repetitions and cases unless shared infrastructure is no
  longer trustworthy.
- Do not retry target agent runs beyond the provider and agent policies already
  configured by production.
- Do not silently retry a judge response beyond the judge provider's one
  structured-output repair attempt.
- Preserve partial results and experiment links.

### LangSmith Failure

Normal research tolerates LangSmith failure and continues locally. Evaluation
has a stricter contract: trace availability is a hard requirement. A missing
trace or LangSmith transport error makes the affected evaluation fail even when
the agent output is otherwise usable.

## Security and Isolation

- Never serialize or print secret values.
- Reuse existing tracker redaction and add artifact/evaluator redaction tests.
- Scan outgoing dataset examples and experiment metadata for known secret
  values before upload.
- Store only curated, non-sensitive examples in LangSmith datasets.
- Use unique evaluation session IDs.
- Use evaluation-specific Chroma collections and procedural registries.
- Use evaluation-specific document directories.
- Never load normal research memory into a controlled case.
- Never delete remote datasets, examples, feedback, or experiments.
- Keep `.env` ignored and out of worktrees, commits, outputs, and traces.

## Testing Strategy

All repository tests remain offline and fake-driven. No pytest invocation makes
a real OpenAI, Tavily, web, Chroma-cloud, or LangSmith call.

### Unit Tests

- Strict model validation and JSON round trips.
- Reasoning-effort enum validation, known agent-key validation, precedence,
  and immutable per-experiment resolution.
- Canonical agent and tier parsing.
- Case-registry uniqueness and version rules.
- Three controlled and one live case per agent.
- Fresh state and dependency construction per repetition.
- Hard-gate behavior and score normalization.
- Agent-specific evaluators.
- Judge prompt construction, structured output, and failure handling.
- Aggregate thresholds and lowest-score selection.
- Secret detection and redaction.
- Exit-code mapping and terminal rendering.

### Factory and Dependency Tests

- Single-agent factory settings match production `build_agents()` settings.
- Controlled dependencies prohibit external application service calls.
- Live dependency resolution selects only applicable credentials and services.
- Evaluation memory and output paths are isolated.
- Missing declared tools fail during construction.

### LangSmith Adapter Tests

- Use a fake LangSmith client and fake evaluation runner.
- Dataset creation, reuse, version conflict, and non-deletion behavior.
- `num_repetitions=3` for controlled and `1` for live.
- `max_concurrency=1`.
- Experiment metadata and links.
- Effective target and judge reasoning efforts and configuration fingerprints.
- Code evaluator and judge feedback attachment.
- Judge evaluator identity, prompt version, rubric version, and fingerprints.
- Judge inputs and outputs are sanitized before evaluator tracing.
- Trace correlation with the existing project tracker.
- LangSmith failure produces exit code `3` or a failed repetition as appropriate.

### CLI Tests

- `list`, `agent`, and `suite` happy paths.
- Focused case selection.
- Controlled and live selection.
- Target and judge reasoning overrides, including target-suite rejection and
  uniform judge-suite application.
- Unknown agent, case, or tier.
- Missing credentials and invalid configuration.
- Partial experiment failure.
- JSON artifact writing and summary rendering.
- No secret leakage in stdout or stderr.

### Explicit Live Verification

Real evaluation commands are manual, opt-in verification steps after offline
tests pass. Verification order is:

1. one controlled case for one agent;
2. the selected agent's full controlled dataset;
3. one live case for that agent;
4. the six-agent controlled suite only after focused runs are sound.

The first manual controlled run also verifies in the LangSmith UI that the
judge score exposes both its **Source** and **Evaluator trace**, and that neither
view contains a known secret or hidden chain-of-thought.

## User Workflow

When one agent appears weak:

1. Run its controlled experiment.
2. Open the lowest-scoring trace for each case.
3. Identify prompt, reasoning, tool-selection, grounding, or output problems.
4. Change only the relevant agent, its reasoning effort, or a shared contract.
5. Rerun the same case and compare experiments in LangSmith.
6. Run all three controlled cases for the agent.
7. Manually review the lowest-scoring trace for each case.
8. Run the agent's one live case.
9. Consider the agent ready for the later end-to-end evaluation only after
   controlled and live evidence is satisfactory.

## Acceptance Criteria

- `python -m deep_research.evaluation list` shows all six agents, three
  controlled cases per agent, and one live case per agent.
- An agent controlled command creates or reuses its visible LangSmith dataset.
- Controlled execution produces nine target runs and nine judge evaluations.
- A focused controlled case produces three target runs and three judge
  evaluations.
- A live command produces one target run and one judge evaluation.
- The default target and judge models are `gpt-5.6-luna`; live
  embeddings use `text-embedding-3-small` when applicable.
- The effective baseline efforts are Planner `medium`, Researcher `low`, Source
  Evaluator `low`, Fact Checker `medium`, Synthesizer `medium`, Critic `medium`,
  and judge `high`, all in standard mode.
- Every agent and the judge can be configured independently, and reasoning
  overrides create a separately fingerprinted experiment without changing the
  dataset or mixing profiles across repetitions.
- Model access is checked before experiment execution, and model identifiers are
  recorded in LangSmith metadata and local JSON without silent fallback.
- Every evaluable run has deterministic feedback, judge feedback, and a trace.
- Every completed judge evaluation has an openable LangSmith **Source** and
  **Evaluator trace** showing the versioned, sanitized prompt and structured
  result.
- LangSmith feedback and the local JSON artifact identify the same judge prompt,
  rubric version, and fingerprints.
- Dataset and experiment metadata contain no known secret.
- Repetitions do not share mutable state, memory, output paths, or tools.
- Controlled runs cannot reach prohibited external application services.
- Pass/fail rules match the approved floors and averages.
- CLI summaries link the experiment and lowest-scoring traces.
- Local JSON artifacts validate and contain every repetition result.
- Production and evaluation use the same single-agent construction mapping.
- Offline test, Ruff, secret-redaction, and diff checks pass.
- No end-to-end graph evaluation is included in this implementation.

## Follow-Up Specification

After all six agents are individually approved, create a separate end-to-end
evaluation design. It will reuse the case, dataset, evaluator, experiment,
artifact, and reporting foundations from this harness while adding graph
routing, cross-agent state seams, critic loop-back behavior, checkpointing, and
whole-report quality criteria.

## LangSmith References

- Evaluation concepts:
  <https://docs.langchain.com/langsmith/evaluation-concepts>
- Evaluation workflow:
  <https://docs.langchain.com/langsmith/evaluation>
- Experiment configuration and repetitions:
  <https://docs.langchain.com/langsmith/experiment-configuration>
- Repetition analysis:
  <https://docs.langchain.com/langsmith/repetition>
- Comparing experiments:
  <https://docs.langchain.com/langsmith/compare-experiment-results>
- Trace metadata and tags:
  <https://docs.langchain.com/langsmith/add-metadata-tags>
- Analyze an experiment, including evaluator source and traces:
  <https://docs.langchain.com/langsmith/analyze-an-experiment>
- Manage evaluators:
  <https://docs.langchain.com/langsmith/evaluators>
- GPT-5.6 Luna model capabilities and pricing:
  <https://developers.openai.com/api/docs/models/gpt-5.6-luna>
- GPT-5.6 model and reasoning-effort guidance:
  <https://developers.openai.com/api/docs/guides/latest-model>
- `text-embedding-3-small` capabilities and pricing:
  <https://developers.openai.com/api/docs/models/text-embedding-3-small>
