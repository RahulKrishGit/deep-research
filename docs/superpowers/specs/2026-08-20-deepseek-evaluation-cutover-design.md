# DeepSeek Evaluation Cutover Design

**Status:** Approved

**Date:** 2026-08-20

## Goal

`OPENAI_API_KEY` is no longer available in any form (chat or embeddings). Move
the whole codebase — production runtime and the individual-agent-evaluation
harness — off OpenAI: DeepSeek V4 Flash becomes the chat provider (target
agents and judge alike), and a local embedding model replaces OpenAI
embeddings for long-term memory. This is a provider swap and a rewire of
existing, already-approved work, not a new subsystem.

This spec builds directly on two already-approved specs:

- `docs/superpowers/specs/2026-08-03-deepseek-provider-and-reasoning-design.md`
  (branch `codex/deepseek-provider`, not yet merged into `main`) — adds the
  DeepSeek chat adapter, provider factory, and capability-table validation.
- `docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md`
  (merged into `main`) — the six-agent evaluation harness, currently
  OpenAI-only.

Nothing about the evaluation harness's dataset/experiment/judging/gate
architecture changes. Only the provider it targets, the models/efforts it
uses, and the pieces of its runner that are hardcoded to OpenAI change.

## Decisions

- Merge `codex/deepseek-provider` into `main` first; build the rest of this
  work on top of the merged result.
- Evaluation baseline model becomes `deepseek-v4-flash` for both target agents
  and the judge (same known cost/correlated-weakness caveat the original spec
  already flagged for using one model as both target and judge).
- Reasoning effort mapping from the original low/medium/high baseline onto
  DeepSeek's high/max-only capability table: agents previously at `low`
  (Researcher, Source Evaluator) move to `high`; every agent previously at
  `medium` (Planner, Fact Checker, Synthesizer, Critic) and the judge
  (previously `high`) move to `max`. Thinking mode is always `enabled` for
  every case — no case needs `disabled`.
- Long-term memory's embedding provider becomes a local, offline model
  (chromadb's built-in default ONNX embedding function) instead of OpenAI's
  `text-embedding-3-small`. No API key, no per-call cost, no network call
  after the model's one-time local download.
- No existing persisted long-term-memory collection exists yet, so the vector
  width change (1536 -> 384 dimensions) is a free cutover, not a migration.
- `OpenAIChatProvider` and `OpenAIEmbeddingProvider` remain in the codebase as
  selectable alternatives (config-driven); they are simply no longer the
  default and are not exercised by the evaluation baseline.
- The evaluation runner's OpenAI-specific preflight (a live "list accessible
  models" call against the OpenAI API) is replaced with the DeepSeek design's
  local capability-table validation: fail-closed on an unsupported
  model/effort combination before any request, no live-availability network
  call for either provider.
- `OPENAI_API_KEY` is no longer a required secret anywhere in the evaluation
  harness; `DEEPSEEK_API_KEY` replaces it. `LANGSMITH_*` and `TAVILY_API_KEY`
  requirements are unchanged.

## Scope

### In Scope

- Merging `codex/deepseek-provider` into `main`, resolving conflicts in favor
  of keeping both the evaluation harness (from `main`) and the provider
  adapter code (from the branch).
- A new `LocalEmbeddingProvider` implementing the existing
  `embed_query`/`embed_documents` protocol, backed by chromadb's default
  embedding function.
- An `embedding_provider` config field (default `local`, alternative
  `openai`), independent of the chat `provider` field, wired into
  `runtime/assembly.py`'s `build_runtime`.
- Evaluation `config.yaml` defaults updated to DeepSeek model/effort/embedding
  values per the mapping above.
- Reworking `evaluation/config.py`'s `target_llm_config`/`judge_llm_config` to
  set `provider`/`thinking_mode` instead of the now-removed `reasoning_mode`
  field.
- Reworking `evaluation/runner.py` to build target/judge providers through the
  merged provider factory instead of hardcoded `OpenAIChatProvider`/
  `AsyncOpenAI`, and to drop the live OpenAI model-access preflight call in
  favor of capability-table validation.
- Updating `_SECRET_ENVIRONMENT_VARIABLES` and the credential-requirements
  table (evaluation design spec and the live-verification runbook) to require
  `DEEPSEEK_API_KEY` instead of `OPENAI_API_KEY`.
- Updating the evaluation design spec's Model and Cost Policy section with
  DeepSeek V4 Flash pricing.
- Updating offline tests across `tests/test_evaluation/*`, relevant
  `tests/test_agents/*`, and adding `LocalEmbeddingProvider` tests.
- Updating README/setup docs per the DeepSeek spec's own migration checklist.

### Out of Scope

- Any change to the DeepSeek provider branch's own adapter architecture,
  error hierarchy, or structured-output repair behavior — that work is
  approved and merges as-is.
- Any change to the evaluation harness's dataset/experiment/gate/scoring
  architecture beyond the provider/model/effort/embedding/secret rewiring
  described here.
- Embedding-quality tuning or evaluating alternate local embedding models
  beyond chromadb's default.
- End-to-end graph evaluation (still deferred per the original evaluation
  spec's follow-up section).
- Re-resolving the four open unknowns in the live-verification runbook
  (judge Source/Evaluator-trace visibility, trace nesting, `temperature` on a
  reasoning model, the `none` reasoning-effort value) — these get re-run
  against DeepSeek when verification is actually performed, not decided here.
- New CLI flags or configuration-loading paths beyond what the two source
  specs already define.

## Branch Merge

`git merge codex/deepseek-provider` into `main`. Expected conflicts:

- `src/deep_research/utils/config.py` — `LLMConfig` gains `provider` and
  `thinking_mode`, drops `reasoning_mode`. Take the branch's `LLMConfig`
  shape; re-derive any evaluation-harness code that referenced
  `reasoning_mode`.
- `src/deep_research/providers/__init__.py` — take the branch's exports
  (provider-neutral contracts, `DeepSeekChatProvider`, provider factory)
  alongside the existing `OpenAIEmbeddingProvider` export.
- `src/deep_research/runtime/assembly.py` — take the branch's chat-provider
  selection logic; add the embedding-provider selection logic described below
  on top of it.

Resolution rule: evaluation-harness code (currently only on `main`) wins where
it does not depend on the removed `reasoning_mode` field; provider-adapter
code from the branch wins everywhere else. The branch's own test suite
(`tests/test_deepseek_provider.py`, `tests/test_provider_*.py`) comes in
as-is. The evaluation harness's test suite — deleted on the branch because it
forked before that harness existed — is restored from `main`'s side of the
merge and then updated per this spec.

After the merge, `LLMConfig` defaults are: `provider: deepseek`,
`model: deepseek-v4-flash`, `thinking_mode: enabled`,
`reasoning_effort: high` — matching the DeepSeek spec's approved production
defaults. Production runtime is DeepSeek-by-default with no additional
configuration required.

## Embeddings

Add `LocalEmbeddingProvider` in `src/deep_research/providers/embeddings.py`,
implementing the same `embed_query(text) -> list[float]` /
`embed_documents(texts) -> list[list[float]]` protocol as
`OpenAIEmbeddingProvider`, backed by
`chromadb.utils.embedding_functions.DefaultEmbeddingFunction()`. `dimension`
is fixed at that model's known width (384) rather than looked up from a
per-model-name table.

`OpenAIEmbeddingProvider` stays in the codebase, unused by default. Add an
`embedding_provider` field to the relevant config (default `local`,
alternative `openai`), independent of the chat `provider` field since chat and
embeddings can differ. `build_runtime` in `runtime/assembly.py` selects the
embedding provider the same way it selects the chat provider.

No persisted long-term-memory Chroma collection exists yet, so there is no
migration to perform for the 1536-to-384-dimension change.

## Evaluation Config Changes

`config.yaml`:

```yaml
llm:
  provider: deepseek
  model: deepseek-v4-flash
  embedding_provider: local

evaluation:
  target_model: deepseek-v4-flash
  judge_model: deepseek-v4-flash
  target_reasoning_effort: max
  target_reasoning_effort_overrides:
    researcher: high
    source_evaluator: high
  judge_reasoning_effort: max
  embedding_model: local
```

`EvaluationConfig.reasoning_mode: Literal["standard"]` is removed.
`evaluation/config.py`'s `target_llm_config` and `judge_llm_config` functions
(building the effective `LLMConfig` for a run) are reworked to set `provider`
(the configured chat provider — `deepseek` in the baseline) and
`thinking_mode` (always `enabled`) on the copied `LLMConfig`, instead of the
removed `reasoning_mode` field. `EvaluationRuntimeConfig.reasoning_mode`
becomes `EvaluationRuntimeConfig.thinking_mode: Literal["enabled"]`, since no
evaluation case uses `disabled`.

## Evaluation Runner Provider Rework

`src/deep_research/evaluation/runner.py` currently hardcodes OpenAI:

- imports `OpenAIChatProvider` directly;
- `preflight(..., openai_client: Any, ...)` takes an OpenAI-specific client;
- `verify_model_access(client, model_ids)` calls OpenAI's model-retrieve
  endpoint as a live preflight check;
- lazily imports and constructs `AsyncOpenAI`, then builds
  `OpenAIChatProvider` for both the target and judge provider.

Rework:

- Replace direct `OpenAIChatProvider` construction with the merged branch's
  provider factory, selecting `DeepSeekChatProvider` or `OpenAIChatProvider`
  from the resolved `LLMConfig.provider`.
- Remove `verify_model_access`'s live "list models" OpenAI call. Preflight
  instead validates the configured target/judge model and resolved effort
  against the selected provider's local capability table — fail-closed on an
  unsupported combination, no network call, consistent with the DeepSeek
  design's stated philosophy that this registry does not attempt to discover
  live account entitlements.
- The `openai_client` preflight parameter becomes provider-generic (or is
  removed); the provider factory constructs whatever client the configured
  provider needs internally, mirroring how production `build_runtime` already
  does this after the merge.

## Secrets

`_SECRET_ENVIRONMENT_VARIABLES` in `evaluation/config.py` drops
`OPENAI_API_KEY` as an always-required secret and adds `DEEPSEEK_API_KEY`.
`LANGSMITH_API_KEY`, `LANGSMITH_WORKSPACE_ID`, and `TAVILY_API_KEY`
requirements are unchanged. The same table in the evaluation design spec and
the live-verification runbook's prerequisites section is updated to match:
`DEEPSEEK_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, optionally
`LANGSMITH_ENDPOINT`/`LANGSMITH_WORKSPACE_ID`, and `TAVILY_API_KEY` for live
cases that exercise search. `OPENAI_API_KEY` is no longer required anywhere
in the evaluation harness.

## Model and Cost Policy

Replaces the original evaluation spec's OpenAI GPT-5.6 Luna cost table.
Looked up 2026-08-20 against DeepSeek's pricing docs
(`https://api-docs.deepseek.com/quick_start/pricing`) — informational,
subject to the same recheck-before-implementation requirement the project
already applies to OpenAI model pricing, since rates and model IDs can move:

| | Cache hit | Cache miss | Output |
|---|---|---|---|
| Off-peak | $0.007 / M tokens | $0.22 / M tokens | $0.66 / M tokens |
| Peak (01:00-04:00, 06:00-10:00 UTC) | $0.014 / M tokens | $0.44 / M tokens | $1.32 / M tokens |

The dated model ID observed on the pricing page is
`DeepSeek-V4-Flash-0731`, distinct from the bare `deepseek-v4-flash` alias
used in requests per the DeepSeek provider design — the same alias-vs-snapshot
ambiguity the project's OpenAI runbook already encountered with the Luna
alias. Verify at implementation time whether the DeepSeek API accepts the
bare alias or requires the dated ID; record whichever identifier the provider
actually returns in experiment metadata, with no silent fallback.

The embeddings pricing row is removed entirely — the local embedding provider
has no API key and no per-call cost.

## Testing Strategy

All repository tests remain offline and fake-driven; no pytest invocation
makes a real DeepSeek, OpenAI, Tavily, web, Chroma-cloud, or LangSmith call.

- `tests/test_evaluation/*`: update fakes/fixtures currently assuming
  `OpenAIChatProvider` and the OpenAI-specific preflight to use the provider
  factory and capability-table validation instead.
- `tests/test_agents/*` and other suites already touched by the
  `codex/deepseek-provider` branch's own diff (e.g. `test_sources.py`,
  `test_synthesizer.py`, `test_config.py`, `test_entrypoint.py`) are checked
  for provider assumptions after the merge.
- New tests for `LocalEmbeddingProvider`: fixed dimension, an injectable
  embedding function for offline tests, no network call.
- Full offline suite and Ruff must pass after the merge and rewiring, same
  bar both source specs already require.

## Documentation

- This spec supersedes the evaluation design spec's Model and Cost Policy and
  credential-requirements sections; the original 2026-08-16 spec is left
  unedited as a historical record, with this spec as the current source of
  truth for those sections.
- Update the live-verification runbook's prerequisites section
  (`DEEPSEEK_API_KEY` instead of `OPENAI_API_KEY`).
- Update README setup/provider/secret-requirements sections per the DeepSeek
  provider design's own migration checklist.

## Acceptance Criteria

- `codex/deepseek-provider` is merged into `main` with the evaluation
  harness's test suite intact and passing.
- Production `build_runtime` constructs a DeepSeek chat provider and a local
  embedding provider by default, with no required OpenAI credentials.
- `python -m deep_research.evaluation agent <name>` runs target agents and the
  judge against `deepseek-v4-flash`, with Researcher and Source Evaluator at
  `high` effort and every other agent plus the judge at `max` effort, thinking
  always enabled.
- Evaluation preflight fails closed on an unsupported model/effort
  combination without any live network call, for either provider.
- Evaluation credential requirements list `DEEPSEEK_API_KEY` and no longer
  require `OPENAI_API_KEY`.
- No persisted long-term-memory collection needs migration.
- Offline test suite and Ruff pass with no real provider calls.
- No known secret appears in outputs, errors, artifacts, evaluator inputs, or
  trace metadata.

## Follow-Up

Once this cutover is implemented and merged, resume the individual-agent
live-verification runbook (`docs/superpowers/plans/
2026-08-16-individual-agent-evaluation-live-verification.md`) against
DeepSeek, re-resolving its four open unknowns for the new provider.
