# DeepSeek Evaluation Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the whole codebase — production runtime and the individual-agent evaluation harness — off OpenAI: merge the approved DeepSeek chat provider, make `deepseek-v4-flash` the target and judge model, replace OpenAI embeddings with a local chromadb-backed embedding provider, and replace the runner's live OpenAI model-access preflight with fail-closed local capability-table validation.

**Architecture:** Task 1 merges the unmerged branch `codex/deepseek-provider` into `main`, which brings in `providers/contracts.py`, `providers/capabilities.py`, `providers/deepseek_provider.py`, and `providers/factory.py`, and reshapes `LLMConfig` (`provider` + `thinking_mode` + non-optional `reasoning_effort`, no `reasoning_mode`). Everything after Task 1 is rewiring on top of that merged shape: a new `LocalEmbeddingProvider` and `build_embedding_provider` selector alongside the existing `build_chat_provider`; an `embedding_provider` config field independent of the chat `provider` field; the evaluation harness's config, secrets, preflight, and production wiring re-pointed at the provider factory and the local capability table. No dataset, experiment, gate, judging, scoring, or reporting architecture changes.

**Tech Stack:** Python 3.11+, Pydantic v2, chromadb (already a dependency — its bundled `DefaultEmbeddingFunction` is the local embedding model), the `openai` SDK (still used as the transport for both OpenAI Responses and DeepSeek Chat Completions), LangSmith, pytest + pytest-asyncio, Ruff. **No new third-party dependency.**

## Global Constraints

- **Every test in this repository is offline and fake-driven.** No test added or modified by this plan may make a real DeepSeek, OpenAI, Tavily, web, Chroma-cloud, or LangSmith call. In particular, no test may let `chromadb`'s `DefaultEmbeddingFunction` download or run its ONNX model — the embedding function is always injected in tests.
- **The full offline suite and Ruff must pass at the end of every task**, before its commit. The two commands, verbatim, are `python -m pytest` and `python -m ruff check src/ tests/`. Both currently report a clean run on `main`; a task is not done until they do again.
- Effort mapping is fixed by the spec and is not a judgement call: Researcher and Source Evaluator at `high`; Planner, Fact Checker, Synthesizer, Critic, **and the judge** at `max`. Thinking mode is `enabled` everywhere; no evaluation case uses `disabled`.
- Baseline model for both target agents and the judge is `deepseek-v4-flash`.
- `OpenAIChatProvider` and `OpenAIEmbeddingProvider` stay in the codebase as selectable, config-driven alternatives. Do not delete either, and do not delete their tests.
- `OPENAI_API_KEY` must not be a required secret anywhere by default — not in `load_config(strict=True)`, not in evaluation preflight, not in `required_credentials`. It becomes required only when a config explicitly selects `embedding_provider: openai` or `provider: openai`.
- Do **not** change the DeepSeek branch's adapter architecture, error hierarchy, or structured-output repair behaviour. It is approved and merges as-is. The only additions to `DeepSeekChatProvider` in this plan are the `last_model_returned` property in Task 4.
- Do **not** add CLI flags or configuration-loading paths beyond what this plan names.
- Python 3.11 union syntax (`X | None`), `from __future__ import annotations` at the top of every new module, Ruff lint rules `E`, `F`, `I` (import sorting).
- Tests use explicit `@pytest.mark.asyncio` markers. `asyncio_mode` is **not** `auto`; an async test without the marker silently does not run.
- No secret value may appear in outputs, errors, artifacts, evaluator inputs, or trace metadata.

---

## File Structure

**Merged in by Task 1 (from `codex/deepseek-provider`, do not author these):**

| File | Responsibility |
| --- | --- |
| `src/deep_research/providers/contracts.py` | Provider-neutral `ChatMessage` / `ChatResult` and the `ProviderError` hierarchy. |
| `src/deep_research/providers/capabilities.py` | The anchored, fail-closed model capability registry; `capability_for`, `resolve_request_settings`. |
| `src/deep_research/providers/deepseek_provider.py` | `DeepSeekChatProvider` — Chat Completions against `https://api.deepseek.com`. |
| `src/deep_research/providers/factory.py` | `build_chat_provider`, `validate_agent_model_configs`. |

**Created or modified by this plan:**

| File | Change |
| --- | --- |
| `src/deep_research/providers/embeddings.py` | Add `LocalEmbeddingProvider`, `LOCAL_EMBEDDING_PROVIDER`, `LOCAL_EMBEDDING_DIMENSION`. `OpenAIEmbeddingProvider` untouched. |
| `src/deep_research/providers/factory.py` | Add `build_embedding_provider` and the `EmbeddingAdapter` alias next to the chat factory; give `build_chat_provider` an optional `api_key` passthrough. |
| `src/deep_research/providers/__init__.py` | Export the new embedding symbols. |
| `src/deep_research/providers/deepseek_provider.py` | Add `last_model_returned`, mirroring `OpenAIChatProvider`. |
| `src/deep_research/utils/config.py` | Post-merge cleanup; `EmbeddingProviderName`; `LLMConfig.embedding_provider`; strict-secret rules; `EvaluationConfig` DeepSeek baseline; drop `EvaluationConfig.reasoning_mode`. |
| `src/deep_research/runtime/assembly.py` | `build_runtime` selects the embedding provider from config. |
| `src/deep_research/evaluation/config.py` | `thinking_mode` replaces `reasoning_mode`; `target_llm_config` / `judge_llm_config` set `provider` + `thinking_mode`; `_SECRET_ENVIRONMENT_VARIABLES` swap. |
| `src/deep_research/evaluation/models.py` | `TargetOutput.thinking_mode` replaces `TargetOutput.reasoning_mode`. |
| `src/deep_research/evaluation/judging.py` | Judge evaluator metadata emits `thinking_mode`. |
| `src/deep_research/evaluation/dependencies.py` | `required_credentials` becomes provider-aware; live bundle selects its embedding provider. |
| `src/deep_research/evaluation/runner.py` | Capability-table preflight replaces `verify_model_access`; `openai_client` parameter removed; suite wiring via `build_chat_provider`. |
| `src/deep_research/evaluation/cli.py` | Production agent pipeline wired through `build_chat_provider`; effort choices gain `minimal`. |
| `config.yaml` | Post-merge cleanup of the `llm` block; DeepSeek `evaluation` block. |
| `.env.example` | `DEEPSEEK_API_KEY` first, `OPENAI_API_KEY` optional. |
| `README.md` | Setup, providers, secret matrix, evaluation environment-variable table. |
| `docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md` | Model and Cost Policy + credential list superseded in place, with a pointer to this cutover spec. |
| `docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md` | Prerequisites section. |

**Tests modified or extended:**

| File | Covers |
| --- | --- |
| `tests/test_providers_embeddings.py` | Task 2 (`LocalEmbeddingProvider`) |
| `tests/test_provider_factory.py` | Tasks 3 (`build_embedding_provider`), 9 (`api_key` passthrough) |
| `tests/test_imports.py` | Tasks 2, 3 (public export surface) |
| `tests/test_config.py` | Tasks 1, 3, 5 |
| `tests/test_runtime/test_assembly.py` | Tasks 1, 3 |
| `tests/test_deepseek_provider.py` | Task 4 |
| `tests/test_evaluation/test_config.py` | Tasks 5, 6, 8 |
| `tests/test_evaluation/test_judging.py` | Task 6 |
| `tests/test_evaluation/test_models.py`, `tests/test_evaluation/test_reporting.py`, `tests/test_evaluation/conftest.py` | Task 7 |
| `tests/test_evaluation/test_dependencies_live.py` | Tasks 8, 12 |
| `tests/test_evaluation/test_runner_preflight.py` | Task 9 |
| `tests/test_evaluation/test_suite.py`, `tests/test_evaluation/test_cli.py` | Task 10 |
| `tests/evaluation_fakes.py` | Tasks 7, 9 |

---

## Read this before Task 1: what the merge actually does

`git merge-tree` was run read-only against `main` and `codex/deepseek-provider` while writing this plan. Merge base is `cf150ed`. **Exactly four files conflict:**

```
src/deep_research/providers/openai_provider.py
src/deep_research/utils/config.py
tests/test_config.py
tests/test_runtime/test_assembly.py
```

`src/deep_research/providers/__init__.py` and `src/deep_research/runtime/assembly.py` **auto-merge cleanly** — the spec predicted conflicts there, but Git resolves both. Verify them anyway (Task 1 Step 6): the branch's `build_chat_provider` / `validate_agent_model_configs` wiring must be present and `OpenAIEmbeddingProvider` must still be exported.

Two files auto-merge into **silently wrong** content and must be hand-repaired inside the merge, before the merge commit:

1. **`config.yaml`** gets the branch's `llm` block *and* main's now-removed `reasoning_effort:` / `reasoning_mode: standard` keys appended under `llm`. YAML duplicate keys mean last-one-wins, so `reasoning_effort` silently becomes `null`.
2. **`src/deep_research/utils/config.py`**'s `LLMConfig`: after you resolve the visible conflict marker, main's trailing field block is still auto-merged *inside the branch's class body*, re-declaring `temperature: float | None`, re-declaring `reasoning_effort: ReasoningEffort | None = None` (clobbering the branch's `= "high"`), adding back `reasoning_mode`, and adding back `request_options()`. There is no conflict marker around any of it.

Task 1 gives the exact final text for both.

---

## Task 1: Merge `codex/deepseek-provider` into `main`

**Files:**
- Modify (conflict resolution): `src/deep_research/providers/openai_provider.py`, `src/deep_research/utils/config.py`, `tests/test_config.py`, `tests/test_runtime/test_assembly.py`
- Modify (silent bad auto-merge, repair inside the merge): `config.yaml`
- Verify only: `src/deep_research/providers/__init__.py`, `src/deep_research/runtime/assembly.py`
- Test: the whole suite — `python -m pytest`

**Interfaces:**
- Produces, for every later task:
  - `deep_research.utils.config.ProviderName = Literal["deepseek", "openai"]`
  - `deep_research.utils.config.ThinkingMode = Literal["enabled", "disabled"]`
  - `deep_research.utils.config.ReasoningEffort: TypeAlias = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]`
  - `deep_research.utils.config.AgentModelOverride`, `EffectiveModelConfig(model: str, thinking_mode: ThinkingMode, reasoning_effort: ReasoningEffort)`
  - `LLMConfig(provider="deepseek", model="deepseek-v4-flash", thinking_mode="enabled", reasoning_effort="high", temperature: float = 0.7, ...)` with `resolve_for(agent_name: str | None) -> EffectiveModelConfig` and `model_for(agent_name: str | None) -> str`. **`LLMConfig.reasoning_mode` and `LLMConfig.request_options()` no longer exist.**
  - `deep_research.providers.build_chat_provider(config: LLMConfig, tracker: Tracker) -> ChatAdapter`
  - `deep_research.providers.validate_agent_model_configs(config: LLMConfig, agent_names: Sequence[str]) -> dict[str, ResolvedRequestSettings]`
  - `deep_research.providers.resolve_request_settings(provider: ProviderName, effective: EffectiveModelConfig) -> ResolvedRequestSettings`
  - `deep_research.providers.ProviderConfigurationError`
  - `DeepSeekChatProvider(config: LLMConfig, tracker: Tracker, *, api_key: str | None = None, client: Any | None = None)`
  - `OpenAIChatProvider.last_model_returned -> str | None` (preserved through the merge)
  - `pyproject.toml` gains `addopts = "-m 'not live'"` and a `live` marker — `python -m pytest` excludes `tests/live/` automatically.

- [ ] **Step 1: Start the merge on a branch off `main`**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/deepseek-evaluation-cutover
git merge --no-commit --no-ff codex/deepseek-provider
git status --short
```

Expected: `git status --short` lists `UU` for exactly the four conflicted files above. Do not commit yet — `--no-commit` is deliberate so the repairs in Steps 2–6 land inside the merge commit.

- [ ] **Step 2: Resolve `src/deep_research/utils/config.py` — the type aliases and `LLMConfig`**

Replace everything from the first `<<<<<<< main` marker through the end of `LLMConfig`'s field list (i.e. the whole region containing both conflict hunks, the duplicated `class LLMConfig`, and main's re-declared trailing fields) with exactly this. Keep `AgentModelOverride` and `EffectiveModelConfig` between the aliases and `LLMConfig`, keep the branch's `resolve_for` / `model_for` bodies that follow, and delete main's `request_options()` method entirely.

```python
ProviderName = Literal["deepseek", "openai"]
ThinkingMode = Literal["enabled", "disabled"]
ReasoningEffort: TypeAlias = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]


class AgentModelOverride(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )
    model: str | None = Field(default=None, min_length=1)
    thinking_mode: ThinkingMode | None = None
    reasoning_effort: ReasoningEffort | None = None


class EffectiveModelConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )
    model: str = Field(min_length=1)
    thinking_mode: ThinkingMode
    reasoning_effort: ReasoningEffort


class LLMConfig(BaseModel):
    """Chat and embedding model and request settings."""

    provider: ProviderName = "deepseek"
    model: str = Field(default="deepseek-v4-flash", min_length=1)
    embedding_model: str = Field(
        default="text-embedding-3-small",
        min_length=1,
    )
    thinking_mode: ThinkingMode = "enabled"
    reasoning_effort: ReasoningEffort = "high"
    model_overrides: dict[str, str | AgentModelOverride] = Field(
        default_factory=dict
    )
    timeout: float = Field(default=60.0, gt=0)
    retry_count: int = Field(default=2, ge=0)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
```

Rules applied here, so a reviewer can check them: `TypeAlias` is kept from `main` (evaluation code annotates with `ReasoningEffort`), the *values* are the branch's (they add `minimal`); `temperature` is the branch's non-optional `float` (the branch's capability registry, not `None`, decides whether temperature is sent); `reasoning_effort` is the branch's required `"high"`, not main's optional `None`; `reasoning_mode` and `request_options()` are gone.

Everything else in this file auto-merged correctly and must be left alone — in particular `_ENVIRONMENT_OVERRIDES` (which correctly contains both the branch's `LLM_THINKING_MODE`/`LLM_REASONING_EFFORT` and main's `EVALUATION_*` entries), `EVALUATION_AGENT_KEYS`, `_DEFAULT_TARGET_EFFORTS`, `EvaluationConfig`, `ConfigSettings.evaluation`, and the branch's `_COMMON_REQUIRED_ENVIRONMENT_VARIABLES` / `_CHAT_PROVIDER_ENVIRONMENT_VARIABLES` / `_validate_runtime_secrets(provider=..., tracing_enabled=...)`. `EvaluationConfig.reasoning_mode` stays for now; Task 5 removes it.

- [ ] **Step 3: Resolve `src/deep_research/providers/openai_provider.py` — three hunks**

The branch replaced the ad-hoc request kwargs with a validated `request` dict; `main` added `last_model_returned` for the evaluation harness. Take the branch's request construction and keep main's recording, rebound from the removed local `model` to `effective.model`.

Hunk 1, in `complete()`:

```python
                    response = await self._client.responses.create(
                        **{**request, "input": payload}
                    )
```

Hunk 2, also in `complete()` (the `ChatResult` return):

```python
                self._last_model_returned = (
                    getattr(response, "model", None) or effective.model
                )
                return ChatResult(text=text, model=effective.model, usage=usage)
```

Hunk 3, in `_structured_attempt()`:

```python
                response = await self._client.responses.parse(
                    **{**request, "input": payload, "text_format": schema}
                )
```

Leave the already-auto-merged `self._last_model_returned: str | None = None` in `__init__`, the `last_model_returned` property, and the assignment inside `_structured_attempt` (that one references the method's own `model: str` parameter and is correct as merged) untouched.

- [ ] **Step 4: Resolve the two test conflicts — keep both sides**

`tests/test_config.py`, import block: keep both names, alphabetically ordered so Ruff's `I` rule is satisfied.

```python
from deep_research.utils.config import (
    ConfigSettings,
    EffectiveModelConfig,
    EvaluationConfig,
    LLMConfig,
    MissingSecretsError,
    apply_config_overrides,
    load_config,
)
```

`tests/test_runtime/test_assembly.py`: the conflict is one large "main added tests / branch added tests" block. Delete the three marker lines (`<<<<<<< main`, `=======`, `>>>>>>> codex/deepseek-provider`) and keep **both** blocks of test functions in place, main's first. There are no overlapping test names.

- [ ] **Step 5: Repair the silently-bad `config.yaml` auto-merge**

`config.yaml`'s `llm` block auto-merged into a block with duplicate keys. Replace the whole `llm:` block with exactly:

```yaml
llm:
  provider: deepseek
  model: deepseek-v4-flash
  embedding_model: text-embedding-3-small
  thinking_mode: enabled
  reasoning_effort: high
  model_overrides: {}
  timeout: 60.0
  retry_count: 2
  temperature: 0.7
  max_tokens: 4096
```

Leave the `evaluation:` block exactly as `main` had it — Task 5 rewrites it. Confirm the repair parses and validates:

```bash
python -c "import yaml; d=yaml.safe_load(open('config.yaml',encoding='utf-8'))['llm']; print(sorted(d)); assert d['reasoning_effort']=='high'"
```

Expected: the key list prints with no `reasoning_mode`, and the assert passes.

- [ ] **Step 6: Verify the two files that auto-merged cleanly**

```bash
python -c "import deep_research.providers as p; print(p.build_chat_provider, p.OpenAIEmbeddingProvider, p.DeepSeekChatProvider)"
grep -n "validate_agent_model_configs\|build_chat_provider" src/deep_research/runtime/assembly.py
```

Expected: the import prints three objects with no error; the grep shows `validate_agent_model_configs` called at the top of `build_runtime` and `build_chat_provider` used for `provider = chat_provider or ...`.

- [ ] **Step 7: Run the full offline suite**

Run: `python -m pytest`
Expected: PASS. If anything fails, it is a merge-resolution bug in Steps 2–5, not a test to weaken — in particular a failure mentioning `request_options`, `reasoning_mode` on `LLMConfig`, or `NoneType` reasoning effort means Step 2 was not applied completely.

- [ ] **Step 8: Run Ruff**

Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 9: Commit the merge**

```bash
git add -A
git commit -m "merge: bring the DeepSeek provider branch into the evaluation harness"
```

---

## Task 2: `LocalEmbeddingProvider`

**Files:**
- Modify: `src/deep_research/providers/embeddings.py` (append after `OpenAIEmbeddingProvider`; module docstring updated)
- Modify: `src/deep_research/providers/__init__.py`
- Test: `tests/test_providers_embeddings.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `deep_research.providers.embeddings.LOCAL_EMBEDDING_PROVIDER: str = "local"`
  - `deep_research.providers.embeddings.LOCAL_EMBEDDING_DIMENSION: int = 384`
  - `deep_research.providers.embeddings.LocalEmbeddingProvider(*, embedding_function: Any | None = None)` with `dimension -> int`, `embed_query(text: str) -> list[float]`, `embed_documents(texts: Sequence[str]) -> list[list[float]]` — the same synchronous protocol `LongTermMemory` calls through `asyncio.to_thread`.
  - Both names re-exported from `deep_research.providers`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers_embeddings.py`:

```python
class FakeEmbeddingFunction:
    """Stands in for chromadb's DefaultEmbeddingFunction. Never downloads."""

    def __init__(self, vectors=None) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def __call__(self, input):  # noqa: A002 - chromadb's parameter name
        payload = list(input)
        self.calls.append(payload)
        if self.vectors is not None:
            return self.vectors
        return [[0.5, 0.25] for _ in payload]


def test_local_embedding_provider_reports_the_models_fixed_dimension() -> None:
    from deep_research.providers import (
        LOCAL_EMBEDDING_DIMENSION,
        LocalEmbeddingProvider,
    )

    assert LOCAL_EMBEDDING_DIMENSION == 384
    assert LocalEmbeddingProvider().dimension == 384


def test_local_embedding_provider_embeds_documents_through_the_injected_function() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction()
    provider = LocalEmbeddingProvider(embedding_function=function)

    assert provider.embed_documents(["alpha", "beta"]) == [
        [0.5, 0.25],
        [0.5, 0.25],
    ]
    assert function.calls == [["alpha", "beta"]]


def test_local_embedding_provider_embeds_a_query_as_one_document() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction(vectors=[[1.0, 2.0]])
    provider = LocalEmbeddingProvider(embedding_function=function)

    assert provider.embed_query("alpha") == [1.0, 2.0]
    assert function.calls == [["alpha"]]


def test_local_embedding_provider_coerces_numpy_style_rows_to_floats() -> None:
    """chromadb returns numpy arrays; the memory layer requires plain floats."""
    import array

    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction(vectors=[array.array("f", [0.5, 0.25])])
    provider = LocalEmbeddingProvider(embedding_function=function)

    vectors = provider.embed_documents(["alpha"])

    assert vectors == [[0.5, 0.25]]
    assert all(type(value) is float for value in vectors[0])


def test_local_embedding_provider_returns_nothing_for_no_texts() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction()

    assert LocalEmbeddingProvider(embedding_function=function).embed_documents([]) == []
    assert function.calls == []


def test_local_embedding_provider_rejects_blank_input() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    provider = LocalEmbeddingProvider(embedding_function=FakeEmbeddingFunction())

    with pytest.raises(ValueError, match="must not be blank"):
        provider.embed_documents(["alpha", "   "])


def test_local_embedding_provider_rejects_a_short_result() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction(vectors=[[0.1, 0.2]])
    provider = LocalEmbeddingProvider(embedding_function=function)

    with pytest.raises(ValueError, match="unexpected number of embeddings"):
        provider.embed_documents(["alpha", "beta"])


def test_local_embedding_provider_never_builds_the_real_model_when_injected(
    monkeypatch,
) -> None:
    """The default function is what would download an ONNX model; an
    injected function must make that path unreachable, which is what keeps
    this suite offline."""
    import chromadb.utils.embedding_functions as chroma_embedding_functions

    from deep_research.providers import LocalEmbeddingProvider

    def exploding(*args, **kwargs):
        raise AssertionError("offline tests must not build the local model")

    monkeypatch.setattr(
        chroma_embedding_functions, "DefaultEmbeddingFunction", exploding
    )
    provider = LocalEmbeddingProvider(embedding_function=FakeEmbeddingFunction())

    assert provider.embed_query("alpha") == [0.5, 0.25]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_providers_embeddings.py -v -k local`
Expected: FAIL with `ImportError: cannot import name 'LocalEmbeddingProvider' from 'deep_research.providers'`

- [ ] **Step 3: Write the implementation**

Append to `src/deep_research/providers/embeddings.py`:

```python
LOCAL_EMBEDDING_PROVIDER = "local"
# chromadb's bundled all-MiniLM-L6-v2 ONNX model. Fixed here rather than
# looked up from a per-model-name table: there is exactly one local model.
LOCAL_EMBEDDING_DIMENSION = 384


class LocalEmbeddingProvider:
    """Embed research text with chromadb's bundled default ONNX model.

    Implements the same ``embed_query``/``embed_documents`` protocol as
    ``OpenAIEmbeddingProvider``, so ``LongTermMemory`` cannot tell the two
    apart. There is no API key and no per-call cost: the model is fetched
    once into chromadb's local cache and every embedding after that is
    computed in-process.

    ``embedding_function`` is injectable so tests never construct the real
    function, which is the object that would download the model.
    """

    def __init__(self, *, embedding_function: Any | None = None) -> None:
        self._embedding_function = embedding_function

    @property
    def dimension(self) -> int:
        """The model's fixed vector width, known without any call."""
        return LOCAL_EMBEDDING_DIMENSION

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        payload = list(texts)
        if not payload:
            return []
        if any(not text.strip() for text in payload):
            raise ValueError("embedding input must not be blank")
        vectors = self._get_embedding_function()(payload)
        rows = list(vectors)
        if len(rows) != len(payload):
            raise ValueError(
                "the local embedding model returned an unexpected number "
                "of embeddings"
            )
        # chromadb hands back numpy float32 rows; the Chroma collection and
        # every artifact downstream want plain Python floats.
        return [[float(value) for value in row] for row in rows]

    def _get_embedding_function(self) -> Any:
        if self._embedding_function is None:
            try:
                from chromadb.utils.embedding_functions import (
                    DefaultEmbeddingFunction,
                )
            except ImportError as error:
                raise RuntimeError(
                    "the chromadb package is required for local embeddings; "
                    'install the project with pip install -e ".[dev]"'
                ) from error
            self._embedding_function = DefaultEmbeddingFunction()
        return self._embedding_function
```

Update the module docstring's first line to `"""OpenAI and local embedding providers."""` and its body to mention that both implement the one protocol long-term memory depends on.

- [ ] **Step 4: Export the new names**

In `src/deep_research/providers/__init__.py`, extend the existing `embeddings` import and `__all__`:

```python
from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    LOCAL_EMBEDDING_DIMENSION,
    LOCAL_EMBEDDING_PROVIDER,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
```

and add `"LOCAL_EMBEDDING_DIMENSION"`, `"LOCAL_EMBEDDING_PROVIDER"`, `"LocalEmbeddingProvider"` to `__all__` in its existing sorted position.

Also add the three names to the import list inside `tests/test_imports.py::test_provider_public_api_imports`, plus one assertion in that test's assertion block:

```python
    assert LocalEmbeddingProvider.__name__ == "LocalEmbeddingProvider"
    assert LOCAL_EMBEDDING_DIMENSION == 384
    assert LOCAL_EMBEDDING_PROVIDER == "local"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_providers_embeddings.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/providers/embeddings.py src/deep_research/providers/__init__.py tests/test_providers_embeddings.py tests/test_imports.py
git commit -m "feat: add a local, offline embedding provider"
```

---

## Task 3: `embedding_provider` config field, selector, and runtime wiring

**Files:**
- Modify: `src/deep_research/utils/config.py` (`EmbeddingProviderName`, `LLMConfig.embedding_provider`, `_ENVIRONMENT_OVERRIDES`, `_COMMON_REQUIRED_ENVIRONMENT_VARIABLES`, `_EMBEDDING_PROVIDER_ENVIRONMENT_VARIABLES`, `_validate_runtime_secrets`, `load_config`)
- Modify: `src/deep_research/providers/factory.py`, `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/runtime/assembly.py` (`build_runtime`)
- Modify: `src/deep_research/runtime/errors.py` (the `missing_secrets` hint)
- Modify: `config.yaml`, `.env.example`
- Test: `tests/test_provider_factory.py`, `tests/test_config.py`, `tests/test_runtime/test_assembly.py`

**Interfaces:**
- Consumes: `LocalEmbeddingProvider`, `LOCAL_EMBEDDING_PROVIDER`, `OpenAIEmbeddingProvider`, `DEFAULT_EMBEDDING_MODEL` (Task 2); `ProviderConfigurationError`, `build_chat_provider` (Task 1).
- Produces:
  - `deep_research.utils.config.EmbeddingProviderName = Literal["local", "openai"]`
  - `LLMConfig.embedding_provider: EmbeddingProviderName = "local"` — independent of `LLMConfig.provider`
  - `deep_research.providers.build_embedding_provider(provider: EmbeddingProviderName, *, model: str = DEFAULT_EMBEDDING_MODEL) -> EmbeddingAdapter`
  - `deep_research.providers.EmbeddingAdapter: TypeAlias = LocalEmbeddingProvider | OpenAIEmbeddingProvider`
  - Environment override `LLM_EMBEDDING_PROVIDER -> ("llm", "embedding_provider")`
  - Strict-mode secrets = chat-provider key + embedding-provider key + `TAVILY_API_KEY` (+ LangSmith pair when tracing).

- [ ] **Step 1: Write the failing selector tests**

Append to `tests/test_provider_factory.py`:

```python
def test_build_embedding_provider_selects_the_local_model() -> None:
    from deep_research.providers import (
        LocalEmbeddingProvider,
        build_embedding_provider,
    )

    provider = build_embedding_provider("local")

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.dimension == 384


def test_build_embedding_provider_selects_openai_with_the_configured_model() -> None:
    from deep_research.providers import (
        OpenAIEmbeddingProvider,
        build_embedding_provider,
    )

    provider = build_embedding_provider("openai", model="text-embedding-3-large")

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.model == "text-embedding-3-large"


def test_build_embedding_provider_rejects_an_unknown_name_without_falling_back() -> None:
    from deep_research.providers import (
        ProviderConfigurationError,
        build_embedding_provider,
    )

    with pytest.raises(ProviderConfigurationError) as caught:
        build_embedding_provider("cohere")

    assert "local, openai" in str(caught.value)
```

If `pytest` is not already imported at the top of `tests/test_provider_factory.py`, add `import pytest`.

- [ ] **Step 2: Write the failing config and assembly tests**

Append to `tests/test_config.py`:

```python
def test_the_embedding_provider_defaults_to_local_and_is_independent_of_chat() -> None:
    from deep_research.utils.config import LLMConfig

    assert LLMConfig().embedding_provider == "local"
    assert LLMConfig(provider="openai").embedding_provider == "local"
    assert LLMConfig(embedding_provider="openai").provider == "deepseek"


def test_strict_mode_requires_no_openai_key_for_the_default_stack(
    monkeypatch, config_path
) -> None:
    """DeepSeek chat plus local embeddings needs no OpenAI credential."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")

    settings = load_config(str(config_path), strict=True)

    assert settings.llm.embedding_provider == "local"


def test_strict_mode_requires_the_openai_key_only_for_openai_embeddings(
    monkeypatch, config_path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    monkeypatch.setenv("LLM_EMBEDDING_PROVIDER", "openai")

    with pytest.raises(MissingSecretsError, match="OPENAI_API_KEY"):
        load_config(str(config_path), strict=True)
```

Append to `tests/test_runtime/test_assembly.py`:

```python
@pytest.mark.asyncio
async def test_build_runtime_uses_the_local_embedding_provider_by_default(
    tracker, tmp_path, monkeypatch
) -> None:
    from deep_research.providers import LocalEmbeddingProvider

    captured: list[object] = []

    def recording_from_config(config, *, embeddings, tracker):
        captured.append(embeddings)
        return LongTermMemory(collection=FakeCollection(), embeddings=FakeEmbeddings())

    monkeypatch.setattr(
        "deep_research.runtime.assembly.LongTermMemory.from_config",
        recording_from_config,
    )
    settings = ConfigSettings()
    settings = settings.model_copy(
        update={
            "memory": settings.memory.model_copy(
                update={
                    "long_term": settings.memory.long_term.model_copy(
                        update={"persist_directory": str(tmp_path)}
                    )
                }
            )
        }
    )

    await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        procedural=FakeProceduralMemory(),
    )

    assert isinstance(captured[0], LocalEmbeddingProvider)
```

Before writing this test, open `tests/test_runtime/test_assembly.py` and reuse the fakes that file already defines for `build_runtime` tests — the post-merge file has both `main`'s and the branch's helpers in it (`RecordingProvider`, `FakeCollection`, `FakeEmbeddings`, and the procedural-memory double used by the existing `test_build_runtime_compiles_a_graph_from_injected_collaborators`). If a name above differs from the file's, use the file's name; do not add a second, duplicate fake.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_provider_factory.py tests/test_config.py tests/test_runtime/test_assembly.py -v -k "embedding or strict_mode"`
Expected: FAIL — `ImportError: cannot import name 'build_embedding_provider'` and `ValidationError: Extra inputs are not permitted [embedding_provider]`.

- [ ] **Step 4: Add the config field and the strict-secret rules**

In `src/deep_research/utils/config.py`, add the alias next to the other provider aliases:

```python
EmbeddingProviderName = Literal["local", "openai"]
```

Add the field to `LLMConfig`, directly above `embedding_model`:

```python
    # Independent of ``provider``: chat and embeddings need not share a
    # vendor, and the default stack is DeepSeek chat with local embeddings.
    embedding_provider: EmbeddingProviderName = "local"
```

Add the environment override immediately after `"LLM_MODEL"`:

```python
    "LLM_EMBEDDING_PROVIDER": ("llm", "embedding_provider"),
```

Replace the required-secret constants with:

```python
_COMMON_REQUIRED_ENVIRONMENT_VARIABLES = ("TAVILY_API_KEY",)
_CHAT_PROVIDER_ENVIRONMENT_VARIABLES = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}
_EMBEDDING_PROVIDER_ENVIRONMENT_VARIABLES = {
    "local": (),
    "openai": ("OPENAI_API_KEY",),
}
```

Replace `_validate_runtime_secrets` with:

```python
def _validate_runtime_secrets(
    *,
    provider: ProviderName,
    embedding_provider: EmbeddingProviderName,
    tracing_enabled: bool,
) -> None:
    """Raise when strict-mode runtime secrets are absent or blank.

    Only the credentials the configured stack actually uses are required:
    the default DeepSeek-chat/local-embeddings stack needs no OpenAI key
    at all. De-duplicated in first-seen order so selecting OpenAI for both
    chat and embeddings names ``OPENAI_API_KEY`` once.
    """
    required: list[str] = []
    for name in (
        *_CHAT_PROVIDER_ENVIRONMENT_VARIABLES[provider],
        *_EMBEDDING_PROVIDER_ENVIRONMENT_VARIABLES[embedding_provider],
        *_COMMON_REQUIRED_ENVIRONMENT_VARIABLES,
    ):
        if name not in required:
            required.append(name)
    if tracing_enabled:
        required.extend(_LANGSMITH_ENVIRONMENT_VARIABLES)
    missing = [
        environment_name
        for environment_name in required
        if not os.getenv(environment_name, "").strip()
    ]
    if missing:
        names = ", ".join(missing)
        raise MissingSecretsError(
            f"Missing required environment variables in strict mode: {names}"
        )
```

And update its one caller in `load_config`:

```python
    if strict:
        _validate_runtime_secrets(
            provider=settings.llm.provider,
            embedding_provider=settings.llm.embedding_provider,
            tracing_enabled=settings.langsmith.tracing_enabled,
        )
```

- [ ] **Step 5: Add the embedding selector to the provider factory**

In `src/deep_research/providers/factory.py`, extend the imports and append the function below `build_chat_provider`:

```python
from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    LOCAL_EMBEDDING_PROVIDER,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from deep_research.utils.config import EmbeddingProviderName, LLMConfig
```

```python
EmbeddingAdapter: TypeAlias = LocalEmbeddingProvider | OpenAIEmbeddingProvider


def build_embedding_provider(
    provider: EmbeddingProviderName,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> EmbeddingAdapter:
    """Select the embedding adapter by name, with no inference or fallback.

    ``model`` applies to the OpenAI adapter only: the local adapter has
    exactly one model and a fixed vector width, so naming a model for it
    would be a setting that cannot take effect.
    """
    if provider == LOCAL_EMBEDDING_PROVIDER:
        return LocalEmbeddingProvider()
    if provider == "openai":
        return OpenAIEmbeddingProvider(model=model)
    raise ProviderConfigurationError(
        f"Unsupported embedding provider {provider!r}; "
        "accepted values: local, openai"
    )
```

Export `build_embedding_provider` and `EmbeddingAdapter` from `src/deep_research/providers/__init__.py` (import from `deep_research.providers.factory`, and add both names to `__all__` in sorted position). Add them to `tests/test_imports.py::test_provider_public_api_imports` too, with one assertion:

```python
    assert build_embedding_provider.__name__ == "build_embedding_provider"
```

- [ ] **Step 6: Wire `build_runtime`**

In `src/deep_research/runtime/assembly.py`, change the providers import to add `build_embedding_provider`, then insert this block immediately after the existing `validate_agent_model_configs` guard and before `tracker = tracker or ...`:

```python
    try:
        embeddings = build_embedding_provider(
            settings.llm.embedding_provider,
            model=settings.llm.embedding_model,
        )
    except ProviderConfigurationError as error:
        raise configuration_error(
            reason="provider_unconfigured",
            message=(
                f"The selected {settings.llm.embedding_provider} embedding "
                f"provider is not configured: {error}"
            ),
        ) from error
```

and replace the `OpenAIEmbeddingProvider(...)` argument in the long-term-memory construction with the already-built object:

```python
        if long_term is None:
            long_term = LongTermMemory.from_config(
                settings.memory.long_term,
                embeddings=embeddings,
                tracker=tracker,
            )
```

Remove `OpenAIEmbeddingProvider` from this module's import list — it is no longer referenced here.

- [ ] **Step 7: Update the missing-secrets hint**

In `src/deep_research/runtime/errors.py`, replace the `missing_secrets` hint text:

```python
    "missing_secrets": (
        "Set the selected chat provider's API key (DEEPSEEK_API_KEY by "
        "default) and TAVILY_API_KEY in the environment or in a .env file "
        "next to config.yaml. OPENAI_API_KEY is required only when a "
        "provider or embedding_provider of 'openai' is configured."
    ),
```

- [ ] **Step 8: Update `config.yaml` and `.env.example`**

Add one line to `config.yaml`'s `llm` block, directly above `embedding_model`:

```yaml
  embedding_provider: local
```

Replace the top of `.env.example` (through `TAVILY_API_KEY=`) with:

```dotenv
# Required for the default DeepSeek chat provider
DEEPSEEK_API_KEY=

# Optional: required only when provider or embedding_provider is 'openai'
OPENAI_API_KEY=

# Optional chat and embedding configuration overrides
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
LLM_THINKING_MODE=enabled
LLM_REASONING_EFFORT=high
LLM_EMBEDDING_PROVIDER=local
LLM_EMBEDDING_MODEL=text-embedding-3-small
LLM_TIMEOUT=60.0
LLM_RETRY_COUNT=2
```

Keep the file's remaining LangSmith and Tavily lines as they are.

- [ ] **Step 9: Fix the branch tests that still assume OpenAI is always required**

In `tests/test_config.py`, three tests that the merge brought in from the branch assert `OPENAI_API_KEY` is unconditionally required. Change them as follows and change nothing else:

- `test_load_config_strict_missing_secrets_raise_the_typed_error` — change its `@pytest.mark.parametrize` list from `["OPENAI_API_KEY", "TAVILY_API_KEY"]` to `["DEEPSEEK_API_KEY", "TAVILY_API_KEY"]`, and set `DEEPSEEK_API_KEY` instead of `OPENAI_API_KEY` in the environment it populates.
- `test_load_config_strict_always_requires_provider_secrets` — same parametrize and environment change.
- `test_strict_deepseek_requires_both_chat_and_embedding_keys` — rename to `test_strict_deepseek_requires_only_the_chat_key_with_local_embeddings`, drop the `OPENAI_API_KEY` setup line, and keep the `pytest.raises(MissingSecretsError, match="DEEPSEEK_API_KEY")` assertion.

Also make sure any test whose environment previously supplied only `OPENAI_API_KEY` + `TAVILY_API_KEY` for a successful strict load now supplies `DEEPSEEK_API_KEY` + `TAVILY_API_KEY` (`test_load_config_strict_env_ok`, `test_load_config_strict_treats_blank_secrets_as_missing`, `test_load_config_loads_sibling_dotenv_before_strict_validation`).

- [ ] **Step 10: Run the tests to verify they pass**

Run: `python -m pytest tests/test_provider_factory.py tests/test_config.py tests/test_runtime/ -v`
Expected: PASS

- [ ] **Step 11: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 12: Commit**

```bash
git add src/deep_research/utils/config.py src/deep_research/providers/factory.py src/deep_research/providers/__init__.py src/deep_research/runtime/assembly.py src/deep_research/runtime/errors.py config.yaml .env.example tests/test_provider_factory.py tests/test_config.py tests/test_imports.py tests/test_runtime/test_assembly.py
git commit -m "feat: select the embedding provider from configuration"
```

---

## Task 4: Record the model identifier DeepSeek actually returns

The cutover spec requires recording whichever identifier the provider actually
returns in experiment metadata, with no silent fallback, because
`deepseek-v4-flash` may be a bare alias for the dated `DeepSeek-V4-Flash-0731`.
`OpenAIChatProvider` already exposes `last_model_returned` and both
`evaluation/targets.py` and `evaluation/judging.py` read it through `getattr`,
so a DeepSeek provider without it silently records `None`.

**Files:**
- Modify: `src/deep_research/providers/deepseek_provider.py` (`DeepSeekChatProvider.__init__`, `complete`, `_structured_attempt`)
- Test: `tests/test_deepseek_provider.py`

**Interfaces:**
- Consumes: `DeepSeekChatProvider` (Task 1).
- Produces: `DeepSeekChatProvider.last_model_returned -> str | None` — `None` before the first successful response, otherwise the `model` field the response carried, falling back to the requested effective model only when the response omits it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deepseek_provider.py`. Reuse the module's existing fake client and response builders — open the file and use the names it already defines (it has a fake `chat.completions.create` client used by every test in it). The two tests below are written against a locally-built minimal double so they are self-contained if no suitable helper exists:

```python
@pytest.mark.asyncio
async def test_last_model_returned_is_none_before_any_call(tracker) -> None:
    from deep_research.providers import DeepSeekChatProvider
    from deep_research.utils.config import LLMConfig

    provider = DeepSeekChatProvider(LLMConfig(), tracker, client=object())

    assert provider.last_model_returned is None


@pytest.mark.asyncio
async def test_last_model_returned_records_the_dated_snapshot(tracker) -> None:
    """The alias is requested; whatever the API answers with is recorded."""
    from deep_research.providers import ChatMessage, DeepSeekChatProvider
    from deep_research.utils.config import LLMConfig

    class _Message:
        content = "answer text"

    class _Choice:
        message = _Message()

    class _Usage:
        prompt_tokens = 3
        completion_tokens = 4

    class _Response:
        id = "resp-1"
        model = "DeepSeek-V4-Flash-0731"
        choices = [_Choice()]
        usage = _Usage()

    class _Completions:
        async def create(self, **kwargs):
            del kwargs
            return _Response()

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    provider = DeepSeekChatProvider(LLMConfig(), tracker, client=_Client())

    result = await provider.complete(
        [ChatMessage(role="user", content="hello")]
    )

    assert result.model == "deepseek-v4-flash"
    assert provider.last_model_returned == "DeepSeek-V4-Flash-0731"
```

If `tests/test_deepseek_provider.py` has no `tracker` fixture, build one inline exactly as that file's other tests do.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_deepseek_provider.py -v -k last_model_returned`
Expected: FAIL with `AttributeError: 'DeepSeekChatProvider' object has no attribute 'last_model_returned'`

- [ ] **Step 3: Write the implementation**

In `src/deep_research/providers/deepseek_provider.py`, add to `DeepSeekChatProvider.__init__`, after `self._client = ...`:

```python
        self._last_model_returned: str | None = None
```

Add the property directly below `__init__`:

```python
    @property
    def last_model_returned(self) -> str | None:
        """The model identifier the last successful response reported.

        ``deepseek-v4-flash`` is requested as a bare alias; the API may
        answer as a dated snapshot such as ``DeepSeek-V4-Flash-0731``. The
        evaluation harness records the requested alias *and* what was
        actually served, and never substitutes one for the other.
        """
        return self._last_model_returned
```

In `complete`, immediately before the `return ChatResult(...)`:

```python
                self._last_model_returned = (
                    getattr(response, "model", None) or effective.model
                )
```

In `_structured_attempt`, immediately after `_set_span_result(span, response, usage)`:

```python
            self._last_model_returned = (
                getattr(response, "model", None) or model
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_deepseek_provider.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "feat: record the model identifier DeepSeek actually returns"
```

---

## Task 5: DeepSeek evaluation baseline in `EvaluationConfig` and `config.yaml`

**Files:**
- Modify: `src/deep_research/utils/config.py:_DEFAULT_TARGET_EFFORTS` and `class EvaluationConfig`
- Modify: `src/deep_research/evaluation/config.py:247-283` (the three reads of the deleted field)
- Modify: `config.yaml` (`evaluation` block)
- Test: `tests/test_config.py`, `tests/test_evaluation/test_config.py`, `tests/test_evaluation/test_runner_preflight.py`

**Interfaces:**
- Consumes: `ReasoningEffort` including `"max"` (Task 1).
- Produces:
  - `EvaluationConfig.target_model = "deepseek-v4-flash"`, `judge_model = "deepseek-v4-flash"`
  - `EvaluationConfig.target_reasoning_effort = "max"`, `judge_reasoning_effort = "max"`
  - `_DEFAULT_TARGET_EFFORTS = {"planner": "max", "researcher": "high", "source_evaluator": "high", "fact_checker": "max", "synthesizer": "max", "critic": "max"}`
  - `EvaluationConfig.embedding_model = "local"`
  - **`EvaluationConfig.reasoning_mode` no longer exists.**

- [ ] **Step 1: Write the failing tests**

In `tests/test_evaluation/test_config.py`, replace the body of `test_the_baseline_efforts_match_the_approved_profile` with:

```python
def test_the_baseline_efforts_match_the_approved_profile() -> None:
    """Researcher and Source Evaluator at high; everyone else, and the
    judge, at max — DeepSeek V4 Flash supports only those two levels."""
    config = EvaluationConfig()

    assert resolve_target_effort(config, "planner", override=None) == "max"
    assert resolve_target_effort(config, "researcher", override=None) == "high"
    assert (
        resolve_target_effort(config, "source_evaluator", override=None)
        == "high"
    )
    assert (
        resolve_target_effort(config, "fact_checker", override=None) == "max"
    )
    assert resolve_target_effort(config, "synthesizer", override=None) == "max"
    assert resolve_target_effort(config, "critic", override=None) == "max"
    assert resolve_judge_effort(config, override=None) == "max"


def test_the_baseline_models_are_deepseek_v4_flash() -> None:
    config = EvaluationConfig()

    assert config.target_model == "deepseek-v4-flash"
    assert config.judge_model == "deepseek-v4-flash"
    assert config.embedding_model == "local"


def test_the_evaluation_config_no_longer_carries_a_reasoning_mode() -> None:
    """Thinking mode replaced it; a stale key must not load silently."""
    assert "reasoning_mode" not in EvaluationConfig.model_fields

    with pytest.raises(ValueError):
        EvaluationConfig(reasoning_mode="standard")
```

In `tests/test_config.py`, delete `test_evaluation_rejects_pro_reasoning_mode` and update the `assert evaluation.reasoning_mode == "standard"` line (in the evaluation-defaults test around line 603) to assert the new baseline instead:

```python
    assert evaluation.target_model == "deepseek-v4-flash"
    assert evaluation.judge_reasoning_effort == "max"
```

In `tests/test_evaluation/test_runner_preflight.py`, delete `test_pro_reasoning_mode_is_rejected` — Pro mode is not representable at all now, and the replacement assertion lives in `test_evaluation/test_config.py`.

Two more tests in `tests/test_evaluation/test_config.py` break on the new defaults and need a value change, not a weakening:

- `test_changing_the_judge_effort_refingerprints_the_judge` passes `judge_reasoning_effort="max"`, which is now the *default* — the fingerprint would be identical and the assertion would fail. Change that argument to `"high"`.
- `test_the_runtime_config_freezes_both_efforts` asserts `runtime.target_reasoning_effort == "low"` for the `researcher` the `build()` helper defaults to. Change it to `"high"` and its `judge_reasoning_effort` assertion to `"max"`. (Task 6 revisits this same test for `thinking_mode`; making the effort change here keeps this task's suite green.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_config.py -v -k "baseline or reasoning_mode"`
Expected: FAIL — `assert 'medium' == 'max'` and `assert 'reasoning_mode' not in ...`.

- [ ] **Step 3: Write the implementation**

In `src/deep_research/utils/config.py`, replace `_DEFAULT_TARGET_EFFORTS`:

```python
# DeepSeek V4 Flash supports exactly two enabled efforts: high and max. The
# original OpenAI baseline's low/medium levels map onto them as approved in
# the cutover spec: the two cheapest agents to high, everything else to max.
_DEFAULT_TARGET_EFFORTS: dict[str, ReasoningEffort] = {
    "planner": "max",
    "researcher": "high",
    "source_evaluator": "high",
    "fact_checker": "max",
    "synthesizer": "max",
    "critic": "max",
}
```

In `class EvaluationConfig`, change four defaults and delete one field:

```python
    target_model: str = Field(default="deepseek-v4-flash", min_length=1)
    target_reasoning_effort: ReasoningEffort = "max"
    target_reasoning_effort_overrides: dict[str, ReasoningEffort] = Field(
        default_factory=lambda: dict(_DEFAULT_TARGET_EFFORTS)
    )
    judge_model: str = Field(default="deepseek-v4-flash", min_length=1)
    judge_reasoning_effort: ReasoningEffort = "max"
    # ``local`` selects the offline embedding provider; any other value is
    # an OpenAI embedding model name. Live-tier bundles are the only
    # consumer.
    embedding_model: str = Field(default="local", min_length=1)
```

Delete the line `reasoning_mode: Literal["standard"] = "standard"` from `EvaluationConfig`. `EvaluationConfig` already sets `extra="forbid"`, so `EvaluationConfig(reasoning_mode=...)` now raises, which is what the new test asserts.

- [ ] **Step 4: Repoint the three readers of the deleted field**

`build_runtime_config` in `src/deep_research/evaluation/config.py` reads `evaluation.reasoning_mode` three times and would now raise `AttributeError`. `EvaluationRuntimeConfig.reasoning_mode` is still a field until Task 6, so keep the field and change only what feeds it.

In both `fingerprint({...})` payloads, replace the `"reasoning_mode": evaluation.reasoning_mode,` entry, in place, with:

```python
            "thinking_mode": "enabled",
```

In the `EvaluationRuntimeConfig(...)` constructor call, replace `reasoning_mode=evaluation.reasoning_mode,` with:

```python
        reasoning_mode="standard",
```

Both fingerprints change value as a result, which is correct and intended: a different reasoning profile must not reuse the previous fingerprint.

- [ ] **Step 5: Rewrite `config.yaml`'s `evaluation` block**

Replace the whole `evaluation:` block with:

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

Note the overrides map now lists only the two agents that differ from the `max` default — that is the spec's block verbatim, and the four omitted agents resolve to `target_reasoning_effort: max` through the existing precedence rule.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/test_config.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS. Tests elsewhere in `tests/test_evaluation/` that assert on the old `low`/`medium` efforts or on `gpt-5.6-luna` fixture literals may fail here; update those literals to the new baseline rather than reverting the defaults. `tests/test_evaluation/conftest.py`'s `_target_output(...)` helper (around line 312) hard-codes `target_model_requested="gpt-5.6-luna"`, `target_model_returned="gpt-5.6-luna"`, `target_reasoning_effort="low"` — change them to `"deepseek-v4-flash"`, `"deepseek-v4-flash"`, `"high"`.

Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/utils/config.py src/deep_research/evaluation/config.py config.yaml tests/test_config.py tests/test_evaluation/
git commit -m "feat: move the evaluation baseline onto DeepSeek V4 Flash"
```

---

## Task 6: `thinking_mode` in the runtime config and the effective LLM configs

**Files:**
- Modify: `src/deep_research/evaluation/config.py:174-214` (`EvaluationRuntimeConfig`), `:216-298` (`build_runtime_config`), `:301-332` (`target_llm_config`, `judge_llm_config`), `:398-428` (`experiment_metadata`), `:50-61` (`_validated_effort`)
- Modify: `src/deep_research/evaluation/judging.py:398` (`judge_evaluator_metadata`)
- Modify: `src/deep_research/evaluation/cli.py` (`_REASONING_EFFORT_CHOICES`)
- Test: `tests/test_evaluation/test_config.py`, `tests/test_evaluation/test_judging.py`

**Interfaces:**
- Consumes: `EvaluationConfig` without `reasoning_mode` (Task 5); `LLMConfig` with `provider`/`thinking_mode` (Task 1).
- Produces:
  - `EvaluationRuntimeConfig.thinking_mode: Literal["enabled"]` — **`EvaluationRuntimeConfig.reasoning_mode` is gone**
  - `target_llm_config(runtime, base) -> LLMConfig` setting `provider=base.provider`, `model`, `model_overrides={}`, `reasoning_effort`, `thinking_mode="enabled"`
  - `judge_llm_config(runtime, base) -> LLMConfig` setting the same plus `temperature` **only when `runtime.judge_temperature is not None`**
  - `experiment_metadata(...)["thinking_mode"]` and `judge_evaluator_metadata(...)["thinking_mode"]` replace their `["reasoning_mode"]` entries

- [ ] **Step 1: Write the failing tests**

In `tests/test_evaluation/test_config.py`:

- In `test_the_runtime_config_freezes_both_efforts`, replace `assert runtime.reasoning_mode == "standard"` with `assert runtime.thinking_mode == "enabled"`. Its two effort assertions were already corrected to `"high"` / `"max"` in Task 5.
- In `test_experiment_metadata_records_everything_the_spec_names`, replace `"reasoning_mode"` with `"thinking_mode"` in the key tuple.
- Replace `test_the_target_llm_config_carries_the_frozen_effort_and_model` and `test_the_judge_llm_config_is_independent_of_the_target` with:

```python
def test_the_target_llm_config_carries_the_frozen_effort_and_model() -> None:
    llm = target_llm_config(build(agent_name="planner"), ConfigSettings().llm)

    assert llm.provider == "deepseek"
    assert llm.model == "deepseek-v4-flash"
    assert llm.reasoning_effort == "max"
    assert llm.thinking_mode == "enabled"
    assert llm.model_overrides == {}


def test_the_target_llm_config_is_accepted_by_the_capability_registry() -> None:
    """Fail-closed: the baseline profile must be a combination DeepSeek
    actually supports, checked against the local table, not assumed."""
    from deep_research.providers import resolve_request_settings

    for agent_name in ("planner", "researcher", "source_evaluator",
                       "fact_checker", "synthesizer", "critic"):
        llm = target_llm_config(build(agent_name=agent_name), ConfigSettings().llm)
        resolved = resolve_request_settings(llm.provider, llm.resolve_for(None))
        assert resolved.reasoning_effort in ("high", "max")
        assert resolved.include_temperature is False


def test_the_judge_llm_config_is_independent_of_the_target() -> None:
    llm = judge_llm_config(
        build(agent_name="researcher"), ConfigSettings().llm
    )

    assert llm.provider == "deepseek"
    assert llm.model == "deepseek-v4-flash"
    assert llm.reasoning_effort == "max"
    assert llm.thinking_mode == "enabled"
    assert llm.temperature == 0.0


def test_the_judge_llm_config_keeps_the_base_temperature_when_unset() -> None:
    """``temperature`` is non-optional on ``LLMConfig``; a ``None`` judge
    temperature means "do not override", never "send null"."""
    settings = ConfigSettings()
    settings = settings.model_copy(
        update={
            "evaluation": settings.evaluation.model_copy(
                update={"judge_temperature": None}
            )
        }
    )
    runtime = build_runtime_config(
        settings,
        agent_name="researcher",
        tier="controlled",
        case_id=None,
        reasoning_effort=None,
        judge_reasoning_effort=None,
        output_directory=None,
        experiment_prefix=None,
        now=NOW,
        git=GIT,
    )

    assert judge_llm_config(runtime, settings.llm).temperature == 0.7
```

Add `build_runtime_config` to this module's imports from `deep_research.evaluation.config` if it is not already there.

Append to `tests/test_evaluation/test_judging.py`:

```python
def test_judge_evaluator_metadata_records_thinking_mode(runtime_config_for) -> None:
    from deep_research.evaluation.judging import judge_evaluator_metadata

    metadata = judge_evaluator_metadata(runtime_config_for("planner"))

    assert metadata["thinking_mode"] == "enabled"
    assert "reasoning_mode" not in metadata
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_config.py tests/test_evaluation/test_judging.py -v`
Expected: FAIL with `AttributeError: 'EvaluationRuntimeConfig' object has no attribute 'thinking_mode'`

- [ ] **Step 3: Write the implementation**

In `src/deep_research/evaluation/config.py`:

Update `_validated_effort`'s message to list all seven levels (the merge added `minimal`):

```python
        raise ValueError(
            f"invalid reasoning effort {value!r}; expected one of: "
            "none, minimal, low, medium, high, xhigh, max"
        ) from error
```

In `EvaluationRuntimeConfig`, replace `reasoning_mode: Literal["standard"]` with:

```python
    # Every evaluation case runs with thinking on; no case needs it off, so
    # the field is deliberately single-valued rather than a free toggle.
    thinking_mode: Literal["enabled"]
```

In `build_runtime_config`, Task 5 already changed the two `fingerprint({...})` payloads to `"thinking_mode": "enabled"`; leave those alone. Change only the `EvaluationRuntimeConfig(...)` constructor call, replacing `reasoning_mode="standard",` with:

```python
        thinking_mode="enabled",
```

Replace `target_llm_config` and `judge_llm_config` with:

```python
def target_llm_config(
    runtime: EvaluationRuntimeConfig, base: LLMConfig
) -> LLMConfig:
    """The LLM config the target agent actually runs under.

    ``provider`` is inherited from the application config, so an
    evaluation run always talks to the same vendor production does.
    ``model_overrides`` is cleared on purpose: the spec enables no
    agent-specific model overrides in the baseline, and an inherited
    production override would silently change the target model.
    """
    return base.model_copy(
        update={
            "provider": base.provider,
            "model": runtime.target_model,
            "model_overrides": {},
            "reasoning_effort": runtime.target_reasoning_effort,
            "thinking_mode": runtime.thinking_mode,
        }
    )


def judge_llm_config(
    runtime: EvaluationRuntimeConfig, base: LLMConfig
) -> LLMConfig:
    """The judge's LLM config, independent of every target-agent setting.

    ``temperature`` is applied only when the evaluation config actually
    pins one: ``LLMConfig.temperature`` is non-optional, and the selected
    provider's capability table — not a ``None`` here — decides whether the
    parameter is sent at all. For DeepSeek with thinking enabled it never is.
    """
    update: dict[str, object] = {
        "provider": base.provider,
        "model": runtime.judge_model,
        "model_overrides": {},
        "reasoning_effort": runtime.judge_reasoning_effort,
        "thinking_mode": runtime.thinking_mode,
    }
    if runtime.judge_temperature is not None:
        update["temperature"] = runtime.judge_temperature
    return base.model_copy(update=update)
```

In `experiment_metadata`, replace the `"reasoning_mode": runtime.reasoning_mode,` entry with:

```python
        "thinking_mode": runtime.thinking_mode,
```

In `src/deep_research/evaluation/judging.py`'s `judge_evaluator_metadata`, replace its `"reasoning_mode": runtime.reasoning_mode,` entry with the same line:

```python
        "thinking_mode": runtime.thinking_mode,
```

That is the only other reader of the renamed field; `grep -rn "runtime.reasoning_mode" src/` must return nothing after this step.

- [ ] **Step 4: Widen the CLI's effort choices**

In `src/deep_research/evaluation/cli.py`, replace:

```python
_REASONING_EFFORT_CHOICES = (
    "none", "minimal", "low", "medium", "high", "xhigh", "max",
)
```

so `--reasoning-effort` and `--judge-reasoning-effort` accept the same seven values `ReasoningEffort` does. Validation against what the provider actually supports happens in preflight (Task 9), not in argparse.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/test_config.py tests/test_evaluation/test_judging.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS. `tests/test_evaluation/test_reporting.py`'s `metadata["reasoning_mode"]` assertion and `conftest.py`'s `_REPORTING_METADATA` fixture are Task 7's; those two are static dictionaries that do not read the renamed field, so they still pass here and are corrected in Task 7 for accuracy rather than to fix a break.

Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/evaluation/config.py src/deep_research/evaluation/judging.py src/deep_research/evaluation/cli.py tests/test_evaluation/test_config.py tests/test_evaluation/test_judging.py
git commit -m "refactor: replace evaluation reasoning_mode with thinking_mode"
```

---

## Task 7: `thinking_mode` on the target output

**Files:**
- Modify: `src/deep_research/evaluation/models.py:258` (`TargetOutput`)
- Test: `tests/test_evaluation/test_models.py`, `tests/test_evaluation/conftest.py` (`_REPORTING_METADATA`), `tests/test_evaluation/test_reporting.py:197`

**Interfaces:**
- Consumes: `EvaluationRuntimeConfig.thinking_mode` (Task 6).
- Produces: `TargetOutput.thinking_mode: Literal["enabled"] = "enabled"` — **`TargetOutput.reasoning_mode` is gone.**

- [ ] **Step 1: Write the failing tests**

In `tests/test_evaluation/test_reporting.py`, replace line 197's assertion with:

```python
    assert metadata["thinking_mode"] == "enabled"
```

Append to `tests/test_evaluation/test_models.py`:

```python
def test_target_output_records_thinking_mode_not_reasoning_mode() -> None:
    from deep_research.evaluation.models import TargetOutput

    assert "reasoning_mode" not in TargetOutput.model_fields
    assert TargetOutput.model_fields["thinking_mode"].default == "enabled"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_models.py tests/test_evaluation/test_reporting.py -v -k thinking`
Expected: FAIL with `KeyError: 'thinking_mode'` and `KeyError: 'thinking_mode'` from `model_fields`.

- [ ] **Step 3: Write the implementation**

In `src/deep_research/evaluation/models.py`, replace the `TargetOutput` field `reasoning_mode: Literal["standard"] = "standard"` with:

```python
    thinking_mode: Literal["enabled"] = "enabled"
```

Nothing constructs `TargetOutput` with that field explicitly — `targets.py` relies on the default — so this is a one-line change with no call-site follow-up.

In `tests/test_evaluation/conftest.py`, replace `"reasoning_mode": "standard",` in `_REPORTING_METADATA` with:

```python
    "thinking_mode": "enabled",
```

While in that fixture, also update its stale OpenAI literals so the reporting fixtures describe a real DeepSeek run: `"target_model": "deepseek-v4-flash"`, `"target_model_returned": "deepseek-v4-flash"`, `"target_reasoning_effort": "high"`, `"judge_reasoning_effort": "max"`. Do the same for `_scored_judge`'s `judge_model="gpt-5.6-luna"` immediately below it, and for `tests/evaluation_fakes.py`'s `FakeStructuredProvider.last_model_returned` default, which is currently `"gpt-5.6-luna-fake"` and becomes `"deepseek-v4-flash-fake"`. If any test asserts on those exact strings, update the assertion to match; do not weaken it to a substring check.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/ -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/evaluation/models.py tests/evaluation_fakes.py tests/test_evaluation/conftest.py tests/test_evaluation/test_reporting.py tests/test_evaluation/test_models.py
git commit -m "refactor: record thinking mode on target outputs"
```

---

## Task 8: Evaluation credential requirements

**Files:**
- Modify: `src/deep_research/evaluation/config.py:43-48` (`_SECRET_ENVIRONMENT_VARIABLES`)
- Modify: `src/deep_research/evaluation/dependencies.py:145-155` (`required_credentials`) and its call site in `build_live_dependencies`
- Modify: `src/deep_research/evaluation/runner.py` (preflight step 4's controlled-tier credential pair)
- Test: `tests/test_evaluation/test_config.py`, `tests/test_evaluation/test_dependencies_live.py`

**Interfaces:**
- Consumes: `ProviderName` (Task 1).
- Produces:
  - `_SECRET_ENVIRONMENT_VARIABLES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LANGSMITH_API_KEY", "TAVILY_API_KEY", "LANGSMITH_WORKSPACE_ID")`
  - `deep_research.evaluation.dependencies.CHAT_PROVIDER_CREDENTIALS: dict[str, str]`
  - `required_credentials(agent_name: AgentName, *, provider: ProviderName) -> tuple[str, ...]` — **the `provider` keyword is now required at every call site.**

Note on `_SECRET_ENVIRONMENT_VARIABLES`: `OPENAI_API_KEY` is *kept* in this tuple even though it is no longer required. That tuple is not a requirement list — it is the set of values `known_secret_values` redacts from artifacts and traces. A key that happens to be present in the environment must still be scrubbed. Only the *requirement* moves to DeepSeek.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluation/test_config.py`:

```python
def test_known_secret_values_covers_deepseek() -> None:
    environ = {
        "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
        "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
    }

    assert "sk-deepseek-abcdefgh" in known_secret_values(environ)


def test_known_secret_values_still_redacts_a_present_openai_key() -> None:
    """No longer required, but still scrubbed if the environment has one."""
    environ = {"OPENAI_API_KEY": "sk-abcdefghijklmnop"}

    assert known_secret_values(environ) == ("sk-abcdefghijklmnop",)
```

In `tests/test_evaluation/test_dependencies_live.py`, replace `test_only_applicable_credentials_are_required` and `test_openai_and_langsmith_are_always_required` with:

```python
def test_only_applicable_credentials_are_required() -> None:
    assert required_credentials("source_evaluator", provider="deepseek") == (
        "DEEPSEEK_API_KEY",
        "LANGSMITH_API_KEY",
    )
    assert "TAVILY_API_KEY" in required_credentials(
        "researcher", provider="deepseek"
    )
    assert "TAVILY_API_KEY" not in required_credentials(
        "source_evaluator", provider="deepseek"
    )


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_the_chat_provider_and_langsmith_are_always_required(agent_name) -> None:
    required = required_credentials(agent_name, provider="deepseek")

    assert "DEEPSEEK_API_KEY" in required
    assert "LANGSMITH_API_KEY" in required
    assert "OPENAI_API_KEY" not in required


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_selecting_openai_chat_requires_the_openai_key(agent_name) -> None:
    assert "OPENAI_API_KEY" in required_credentials(
        agent_name, provider="openai"
    )
```

In the same file, change `FULL_ENVIRONMENT`'s `"OPENAI_API_KEY": "sk-abcdefghijklmnop",` entry to `"DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_dependencies_live.py tests/test_evaluation/test_config.py -v -k "credential or secret"`
Expected: FAIL with `TypeError: required_credentials() got an unexpected keyword argument 'provider'`

- [ ] **Step 3: Write the implementation**

In `src/deep_research/evaluation/config.py`:

```python
_SECRET_ENVIRONMENT_VARIABLES = (
    "DEEPSEEK_API_KEY",
    # Not required by the DeepSeek baseline, but still redacted whenever it
    # is present: this tuple defines what gets scrubbed, not what is needed.
    "OPENAI_API_KEY",
    "LANGSMITH_API_KEY",
    "TAVILY_API_KEY",
    "LANGSMITH_WORKSPACE_ID",
)
```

In `src/deep_research/evaluation/dependencies.py`, add the mapping above `required_credentials` and rewrite the function:

```python
CHAT_PROVIDER_CREDENTIALS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def required_credentials(
    agent_name: AgentName, *, provider: ProviderName
) -> tuple[str, ...]:
    """The environment variables one live run of ``agent_name`` must provide.

    The selected chat provider's key and LangSmith are required for every
    agent; Tavily only for agents whose declared tools reach it.
    ``LANGSMITH_PROJECT`` is validated by the tracker's own runtime config,
    so it is not duplicated here. Embeddings contribute nothing: the
    baseline embedding provider is local and has no credential.
    """
    chat_key = CHAT_PROVIDER_CREDENTIALS[provider]
    if "tavily" in LIVE_DEPENDENCIES[agent_name]:
        return (chat_key, "LANGSMITH_API_KEY", "TAVILY_API_KEY")
    return (chat_key, "LANGSMITH_API_KEY")
```

Import `ProviderName` from `deep_research.utils.config` at the top of the module.

Update the one call site inside `build_live_dependencies`:

```python
    missing = [
        variable
        for variable in required_credentials(
            runtime.agent_name, provider=settings.llm.provider
        )
        if not environ.get(variable, "").strip()
    ]
```

In `src/deep_research/evaluation/runner.py`, preflight step 4, replace the `required = (...)` expression:

```python
    required = (
        required_credentials(runtime.agent_name, provider=settings.llm.provider)
        if runtime.tier == "live"
        else (
            CHAT_PROVIDER_CREDENTIALS[settings.llm.provider],
            "LANGSMITH_API_KEY",
        )
    )
```

and extend that module's `from deep_research.evaluation.dependencies import (...)` block with `CHAT_PROVIDER_CREDENTIALS`. Update the step-4 comment block above it so it says "the selected chat provider and LangSmith are required for every tier" rather than "OpenAI and LangSmith", and drop its now-false claim that step 5 is a "real network call".

- [ ] **Step 4: Update the remaining OpenAI-only environments in the evaluation tests**

Change `"OPENAI_API_KEY": "sk-abcdefghijklmnop",` to `"DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",` in every evaluation-test environment dict that stands for "the credentials a run needs":

- `tests/test_evaluation/test_runner_preflight.py::ENVIRONMENT`
- `tests/test_evaluation/conftest.py` — the two environ dicts inside `live_evaluation_harness` and the live researcher harness factory (around lines 1527 and 2129), and the expected message string `"missing required credentials: OPENAI_API_KEY, LANGSMITH_API_KEY"` (around line 2313), which becomes `"missing required credentials: DEEPSEEK_API_KEY, LANGSMITH_API_KEY"`.
- `tests/test_evaluation/test_cli.py:237` — `monkeypatch.setenv("OPENAI_API_KEY", ...)` becomes `monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-abcdefgh")`.

Leave `tests/test_evaluation/conftest.py`'s `leaking_runner` reading `OPENAI_API_KEY` alone — that fixture deliberately proves an *arbitrary* known secret is redacted, and `OPENAI_API_KEY` is still a known secret.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/ -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/evaluation/config.py src/deep_research/evaluation/dependencies.py src/deep_research/evaluation/runner.py tests/test_evaluation/
git commit -m "feat: require the selected chat provider's key, not OpenAI's"
```

---

## Task 9: Capability-table preflight replaces the live model-access call

**Files:**
- Modify: `src/deep_research/evaluation/runner.py:133-149` (delete `verify_model_access`), `:180-263` (`preflight` signature, docstring, step 5), `:84` (imports)
- Modify: `src/deep_research/evaluation/runner.py:303` (preflight step 8's provider construction)
- Modify: `src/deep_research/evaluation/cli.py` (drop `openai_client=` from its `preflight` call)
- Modify: `tests/evaluation_fakes.py` (delete `FakeModelsClient` and `FakeOpenAIClient`)
- Test: `tests/test_evaluation/test_runner_preflight.py`

**Also modify:** `src/deep_research/providers/factory.py` — `build_chat_provider` gains an optional `api_key` passthrough (see Step 3a and the note below).

**Interfaces:**
- Consumes: `resolve_request_settings`, `ProviderConfigurationError`, `build_chat_provider` (Task 1); `target_llm_config` / `judge_llm_config` (Task 6); `CHAT_PROVIDER_CREDENTIALS` (Task 8).
- Produces:
  - `deep_research.evaluation.runner.validate_model_capabilities(settings: ConfigSettings, runtime: EvaluationRuntimeConfig) -> None`, raising `PreflightError("model_unavailable", ...)`
  - `preflight(settings, runtime, *, cases, environ, langsmith_client, root) -> None` — **`openai_client` is removed from the signature.**
  - `build_chat_provider(config: LLMConfig, tracker: Tracker, *, api_key: str | None = None) -> ChatAdapter` — the new keyword defaults to `None`, which preserves the existing "read the key from the process environment" behaviour for `build_runtime`.
  - `verify_model_access` no longer exists.
  - `PREFLIGHT_REASONS` is unchanged (still nine reasons), so `preflight_exit_code` and the CLI exit-code table are unaffected.

**Why `build_chat_provider` needs the passthrough:** preflight's step 8 builds a real chat provider to smoke-test agent construction. Both adapters' `_build_client` fall back to `os.getenv("DEEPSEEK_API_KEY")` / `os.getenv("OPENAI_API_KEY")`, but preflight is given credentials as an explicit `environ` mapping and its whole test suite passes a synthetic one. Without the passthrough, a clean preflight would raise `ProviderConfigurationError` in any environment that has no real key exported — including CI. This is an additive optional keyword on the factory, not a change to either adapter, and both adapters already accept `api_key`.

- [ ] **Step 1: Write the failing tests**

Rewrite `tests/test_evaluation/test_runner_preflight.py`'s helper and replace the four model-access tests. The full replacements:

```python
"""Preflight fails before any experiment is created, without a network call."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.runner import (
    PREFLIGHT_REASONS,
    PreflightError,
    preflight,
    validate_model_capabilities,
)
from tests.evaluation_fakes import FakeLangSmithClient

ENVIRONMENT = {
    "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
    "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
    "LANGSMITH_PROJECT": "evaluation",
}


async def run(settings, runtime, tmp_path, **overrides):
    kwargs = dict(
        cases=cases_for(runtime.agent_name, runtime.tier),
        environ=dict(ENVIRONMENT),
        langsmith_client=FakeLangSmithClient(),
        root=tmp_path,
    )
    kwargs.update(overrides)
    return await preflight(settings, runtime, **kwargs)
```

Delete `test_an_inaccessible_target_model_never_falls_back`, `test_the_embedding_model_is_only_checked_for_live_runs`, `test_a_live_run_checks_the_embedding_model`, `test_model_access_is_verified_before_any_dataset_write`, and `test_verify_model_access_requests_each_model_once`.

Rewrite `test_a_live_run_missing_tavily_fails_before_model_access`, which currently builds a `FakeOpenAIClient` and asserts `client.models.requested == []`. Its point survives — a missing Tavily key must be reported as `missing_credentials`, not deferred — but there is no model client left to inspect. Replace the whole test with:

```python
@pytest.mark.asyncio
async def test_a_live_run_missing_tavily_fails_with_missing_credentials(
    settings, runtime_config_for, tmp_path
) -> None:
    """Researcher's live tier also needs Tavily; step 4 must catch that as
    ``missing_credentials``, not defer it to step 7's
    ``guards_uninstallable``."""
    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime_config_for("researcher", tier="live"),
            tmp_path,
            cases=cases_for("researcher", "live"),
        )

    assert caught.value.reason == "missing_credentials"
    assert "TAVILY_API_KEY" in str(caught.value)
```

Then add:

```python
def _settings_with_target_model(settings, model):
    evaluation = settings.evaluation.model_copy(update={"target_model": model})
    return settings.model_copy(update={"evaluation": evaluation})


@pytest.mark.asyncio
async def test_an_unsupported_target_model_fails_closed_with_no_network(
    settings, runtime_config_for, tmp_path
) -> None:
    broken = _settings_with_target_model(settings, "deepseek-v9-imaginary")
    runtime = runtime_config_for("planner").model_copy(
        update={"target_model": "deepseek-v9-imaginary"}
    )
    client = FakeLangSmithClient()

    with pytest.raises(PreflightError) as caught:
        await run(broken, runtime, tmp_path, langsmith_client=client)

    assert caught.value.reason == "model_unavailable"
    assert "deepseek-v9-imaginary" in str(caught.value)
    assert client.created_datasets == []


@pytest.mark.asyncio
async def test_an_unsupported_effort_for_a_supported_model_fails_closed(
    settings, runtime_config_for, tmp_path
) -> None:
    """DeepSeek V4 Flash accepts only high and max with thinking enabled."""
    runtime = runtime_config_for("planner").model_copy(
        update={"target_reasoning_effort": "low"}
    )

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime, tmp_path)

    assert caught.value.reason == "model_unavailable"
    assert "low" in str(caught.value)


@pytest.mark.asyncio
async def test_an_unsupported_judge_model_fails_closed(
    settings, runtime_config_for, tmp_path
) -> None:
    runtime = runtime_config_for("planner").model_copy(
        update={"judge_model": "gpt-5.6-luna"}
    )

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime, tmp_path)

    assert caught.value.reason == "model_unavailable"
    assert "gpt-5.6-luna" in str(caught.value)


def test_capability_validation_accepts_the_openai_provider_too(
    settings, runtime_config_for
) -> None:
    """Fail-closed applies symmetrically; OpenAI stays selectable."""
    openai_settings = settings.model_copy(
        update={
            "llm": settings.llm.model_copy(update={"provider": "openai"}),
            "evaluation": settings.evaluation.model_copy(
                update={
                    "target_model": "gpt-5.6-luna",
                    "judge_model": "gpt-5.6-luna",
                }
            ),
        }
    )
    runtime = runtime_config_for("planner").model_copy(
        update={"target_model": "gpt-5.6-luna", "judge_model": "gpt-5.6-luna"}
    )

    validate_model_capabilities(openai_settings, runtime)
```

`EvaluationRuntimeConfig` is frozen but `model_copy` on a frozen Pydantic model returns a new instance, which is what these tests rely on.

Append to `tests/test_provider_factory.py`:

```python
def test_build_chat_provider_passes_an_explicit_key_through(tracker) -> None:
    """Callers holding credentials as data must not need the process env."""
    import os

    from deep_research.providers import build_chat_provider
    from deep_research.utils.config import LLMConfig

    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        provider = build_chat_provider(
            LLMConfig(), tracker, api_key="sk-deepseek-abcdefgh"
        )
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous

    assert provider is not None
```

Use whatever offline `tracker` fixture that file already has; if it has none, build one inline as `Tracker(LangSmithRuntimeConfig(tracing_enabled=False))`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_runner_preflight.py tests/test_provider_factory.py -v`
Expected: FAIL with `ImportError: cannot import name 'validate_model_capabilities'` and `TypeError: build_chat_provider() got an unexpected keyword argument 'api_key'`

- [ ] **Step 3a: Add the factory passthrough**

In `src/deep_research/providers/factory.py`:

```python
def build_chat_provider(
    config: LLMConfig, tracker: Tracker, *, api_key: str | None = None
) -> ChatAdapter:
    """Select the chat adapter by configured name; never infer, never fall back.

    ``api_key`` lets a caller that already holds credentials as data — the
    evaluation harness passes its own ``environ`` mapping — supply the key
    explicitly. ``None`` keeps the adapters' default behaviour of reading
    the process environment, which is what production wiring relies on.
    """
    if config.provider == "deepseek":
        return DeepSeekChatProvider(config, tracker, api_key=api_key)
    if config.provider == "openai":
        return OpenAIChatProvider(config, tracker, api_key=api_key)
    raise ProviderConfigurationError(
        f"Unsupported chat provider {config.provider!r}; "
        "accepted values: deepseek, openai"
    )
```

- [ ] **Step 3b: Write the preflight implementation**

In `src/deep_research/evaluation/runner.py`, replace the import of `OpenAIChatProvider`:

```python
from deep_research.providers import (
    ProviderConfigurationError,
    build_chat_provider,
    resolve_request_settings,
)
```

Delete `verify_model_access` entirely and put this in its place:

```python
def validate_model_capabilities(
    settings: ConfigSettings, runtime: EvaluationRuntimeConfig
) -> None:
    """Check target and judge settings against the local capability table.

    Entirely local and fail-closed: the registry does not attempt to
    discover live account entitlements, so an unsupported model, thinking
    mode, or reasoning effort is rejected here without a single network
    call, for either provider. There is no fallback — a rejected
    combination fails preflight by name.

    The embedding model is deliberately not checked: it is not a chat
    model, it is not in the chat capability table, and the baseline
    embedding provider is local, with nothing to entitle.
    """
    for label, config in (
        ("target", target_llm_config(runtime, settings.llm)),
        ("judge", judge_llm_config(runtime, settings.llm)),
    ):
        try:
            resolve_request_settings(config.provider, config.resolve_for(None))
        except ProviderConfigurationError as error:
            raise PreflightError("model_unavailable", f"{label}: {error}") from error
```

Change `preflight`'s signature — delete the `openai_client: Any,` parameter — and replace its step-5 block:

```python
    # 5. Every model and effort this run will send is one the selected
    # provider actually supports, checked against the local capability
    # table. Never a substitute: an unsupported combination fails preflight
    # by name, full stop, and no network call is made to find out.
    validate_model_capabilities(settings, runtime)
```

Update `preflight`'s docstring: it currently claims "the one client call that only reads (model access) comes before every client call that could write". Replace that sentence with "every model check is local, so the only client call in the whole sequence is the dataset synchronization at the end, which is also the only one that can write."

Replace preflight step 8's provider construction and widen the `except` that guards agent construction, so a provider that cannot be configured fails preflight by reason instead of escaping as a raw exception:

```python
    # 8. The agent itself builds with those tools -- never deferred to the
    # first real repetition. The provider is built from this run's own
    # credential mapping rather than the process environment, so preflight
    # checks exactly the credentials step 4 verified.
    try:
        provider = build_chat_provider(
            target_llm_config(runtime, settings.llm),
            tracker,
            api_key=environ.get(
                CHAT_PROVIDER_CREDENTIALS[settings.llm.provider]
            ),
        )
        build_agent(
            runtime.agent_name,
            bundle.settings,
            tracker=tracker,
            provider=provider,
            tools=bundle.tools,
            session_id=evaluation_session_id(
                runtime, case_id=smoke_case.case_id, repetition=1
            ),
            reputation=bundle.reputation,
        )
    except (AgentConfigurationError, ProviderConfigurationError) as error:
        raise PreflightError("agent_unbuildable", str(error)) from error
```

`ProviderConfigurationError` messages name provider, model, and setting values only — never a key — so embedding `str(error)` here leaks nothing.

If `Any` is now unused in the module's `typing` import, drop it; Ruff's `F` rule will flag it if so.

- [ ] **Step 4: Update the two `preflight` call sites and the fakes**

In `src/deep_research/evaluation/cli.py`'s `_run_agent_pipeline`, delete the `openai_client=openai_client,` argument from the `await preflight(...)` call. (The rest of that function is Task 10.)

In `tests/evaluation_fakes.py`, delete `FakeModelsClient` and `FakeOpenAIClient` — nothing references them once Step 1's rewrite lands. Grep to confirm before deleting:

```bash
grep -rn "FakeOpenAIClient\|FakeModelsClient" tests/
```

Expected after the edits: no matches.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/test_runner_preflight.py tests/test_provider_factory.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/providers/factory.py src/deep_research/evaluation/runner.py src/deep_research/evaluation/cli.py tests/evaluation_fakes.py tests/test_provider_factory.py tests/test_evaluation/test_runner_preflight.py
git commit -m "feat: validate evaluation models against the local capability table"
```

---

## Task 10: Production evaluation wiring through the provider factory

**Files:**
- Modify: `src/deep_research/evaluation/cli.py:273-322` (`_run_agent_pipeline`)
- Modify: `src/deep_research/evaluation/runner.py:919-1022` (`run_suite_evaluation`)
- Test: `tests/test_evaluation/test_suite.py`, `tests/test_evaluation/test_cli.py`

**Interfaces:**
- Consumes: `build_chat_provider` (Task 1); `target_llm_config` / `judge_llm_config` (Task 6); `preflight` without `openai_client` (Task 9).
- Produces: no new public names. `run_suite_evaluation` and `_run_agent_pipeline` construct providers through `build_chat_provider`, and neither module imports `openai` any more.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluation/test_suite.py`:

```python
def test_the_suite_builds_providers_through_the_provider_factory() -> None:
    """No direct OpenAI construction survives in the production wiring."""
    import inspect

    from deep_research.evaluation import runner

    source = inspect.getsource(runner)

    assert "AsyncOpenAI" not in source
    assert "OpenAIChatProvider" not in source
    assert "build_chat_provider" in source


def test_the_evaluation_cli_builds_providers_through_the_provider_factory() -> None:
    import inspect

    from deep_research.evaluation import cli

    source = inspect.getsource(cli)

    assert "AsyncOpenAI" not in source
    assert "OpenAIChatProvider" not in source
    assert "build_chat_provider" in source
```

These are structural guards, not behaviour tests, and that is deliberate: neither production function is reachable from any offline test (every suite test injects a fake `evaluate`, every CLI test injects a fake runner), so the only offline-safe way to pin this wiring is to assert on the source. The existing `tests/test_evaluation/test_suite.py` harnesses already exercise `run_suite_evaluation` end to end with those fakes and will catch a construction-time error.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_suite.py -v -k provider_factory`
Expected: FAIL with `assert 'AsyncOpenAI' not in source`

- [ ] **Step 3: Rewrite the suite's provider construction**

In `src/deep_research/evaluation/runner.py`'s `run_suite_evaluation`, delete the lazy `from openai import AsyncOpenAI` import and its explanatory comment, and replace the per-agent client/provider block with:

```python
            tracker = Tracker.from_config(settings.langsmith, environ=environ)
            chat_key = environ.get(
                CHAT_PROVIDER_CREDENTIALS[settings.llm.provider]
            )
            target_provider = build_chat_provider(
                target_llm_config(runtime, settings.llm),
                tracker,
                api_key=chat_key,
            )
            judge_provider = build_chat_provider(
                judge_llm_config(runtime, settings.llm),
                tracker,
                api_key=chat_key,
            )
```

The old `api_key=environ.get("OPENAI_API_KEY") or "sk-not-configured"` placeholder is gone: a missing key now raises `ProviderConfigurationError` at construction, which the surrounding `except Exception` already turns into that one agent's `INFRASTRUCTURE FAILURE` result without aborting the other five. That is strictly better than a placeholder key that would have failed later, at the first real request, as an opaque 401.

Delete the now-unused `import os` only if nothing else in the module uses it — `run_suite_evaluation` still calls `dict(os.environ)`, so it stays.

- [ ] **Step 4: Rewrite the CLI's agent pipeline**

In `src/deep_research/evaluation/cli.py`, replace `_run_agent_pipeline`'s prelude:

```python
async def _run_agent_pipeline(
    settings: Any,
    runtime: Any,
    cases: Sequence[EvaluationCase],
    environ: dict[str, str],
) -> ExperimentResult:
    # Imported lazily: constructing a real LangSmith client is a
    # meaningfully heavier import than anything else this module needs at
    # load time, and every test here injects a fake runner instead of ever
    # reaching this function.
    from langsmith import Client as LangSmithClient

    langsmith_client = LangSmithClient()
    tracker = Tracker.from_config(settings.langsmith, environ=environ)
    chat_key = environ.get(CHAT_PROVIDER_CREDENTIALS[settings.llm.provider])
    target_provider = build_chat_provider(
        target_llm_config(runtime, settings.llm), tracker, api_key=chat_key
    )
    judge_provider = build_chat_provider(
        judge_llm_config(runtime, settings.llm), tracker, api_key=chat_key
    )
```

Change the module's provider import from `from deep_research.providers import OpenAIChatProvider` to `from deep_research.providers import build_chat_provider`, and add `CHAT_PROVIDER_CREDENTIALS` to the existing `from deep_research.evaluation.dependencies import (...)` block. Leave the rest of the function — the dependency factory selection, the `preflight(...)` call (already stripped of `openai_client` in Task 9), and the `run_agent_evaluation(...)` call — unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/test_suite.py tests/test_evaluation/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/evaluation/runner.py src/deep_research/evaluation/cli.py tests/test_evaluation/test_suite.py
git commit -m "feat: build evaluation providers through the provider factory"
```

---

## Task 11: Live-tier bundles select their embedding provider

**Files:**
- Modify: `src/deep_research/evaluation/dependencies.py:57` (imports), `:820-835` (`build_live_dependencies`'s `LongTermMemory.from_config` call)
- Test: `tests/test_evaluation/test_dependencies_live.py`

**Interfaces:**
- Consumes: `build_embedding_provider`, `LOCAL_EMBEDDING_PROVIDER` (Task 3); `EvaluationConfig.embedding_model = "local"` (Task 5).
- Produces: no new public names. `build_live_dependencies` keeps its existing injectable `embeddings` parameter, which every offline test still uses.

`build_controlled_dependencies` is deliberately untouched: it already uses `_DeterministicEmbeddings` and never constructs a real provider.

- [ ] **Step 1: Write the failing test**

In `tests/test_evaluation/test_dependencies_live.py`, replace the test that monkeypatches `deep_research.evaluation.dependencies.OpenAIEmbeddingProvider` (the one defining `RecordingEmbeddings` around line 195) with:

```python
def test_a_live_bundle_builds_the_local_embedding_provider_by_default(
    tracker, settings, tmp_path, runtime_config_for, live_case_for, monkeypatch
) -> None:
    """``embedding_model: local`` selects the offline provider, and the
    real ONNX model is never constructed in an offline test."""
    captured: list[tuple[str, str]] = []

    class RecordingEmbeddings:
        def embed_query(self, text):  # pragma: no cover - never called offline
            raise AssertionError("offline tests must not embed")

        def embed_documents(self, texts):  # pragma: no cover
            raise AssertionError("offline tests must not embed")

    def recording_build(provider, *, model):
        captured.append((provider, model))
        return RecordingEmbeddings()

    monkeypatch.setattr(
        "deep_research.evaluation.dependencies.build_embedding_provider",
        recording_build,
    )

    build_live_dependencies(
        runtime_config_for("planner", tier="live"),
        live_case_for("planner"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
    )

    assert captured == [("local", "local")]


def test_a_live_bundle_selects_openai_for_a_named_embedding_model(
    tracker, settings, tmp_path, runtime_config_for, live_case_for, monkeypatch
) -> None:
    captured: list[tuple[str, str]] = []

    def recording_build(provider, *, model):
        captured.append((provider, model))
        return object()

    monkeypatch.setattr(
        "deep_research.evaluation.dependencies.build_embedding_provider",
        recording_build,
    )
    runtime = runtime_config_for("planner", tier="live").model_copy(
        update={"embedding_model": "text-embedding-3-small"}
    )

    build_live_dependencies(
        runtime,
        live_case_for("planner"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
    )

    assert captured == [("openai", "text-embedding-3-small")]
```

These replace the existing `test_the_live_embedding_model_is_the_configured_one`, and deliberately keep its exact call shape: the planner declares no `web_search`, so no `search_client` double is needed and nothing in the bundle touches the network.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_evaluation/test_dependencies_live.py -v -k embedding`
Expected: FAIL with `AttributeError: <module ...dependencies> has no attribute 'build_embedding_provider'`

- [ ] **Step 3: Write the implementation**

In `src/deep_research/evaluation/dependencies.py`, replace the `OpenAIEmbeddingProvider` import with:

```python
from deep_research.providers import (
    LOCAL_EMBEDDING_PROVIDER,
    build_embedding_provider,
)
```

and replace the `LongTermMemory.from_config` call inside `build_live_dependencies`:

```python
    # ``embedding_model`` carries one of two things: the sentinel "local",
    # which selects the offline provider, or an OpenAI embedding model
    # name. There is only ever one local model, so it needs no name of its
    # own and the sentinel doubles as the provider selector.
    embedding_provider_name = (
        "local"
        if runtime.embedding_model == LOCAL_EMBEDDING_PROVIDER
        else "openai"
    )
    long_term = LongTermMemory.from_config(
        isolated.memory.long_term,
        embeddings=embeddings
        or build_embedding_provider(
            embedding_provider_name, model=runtime.embedding_model
        ),
        tracker=tracker,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_evaluation/test_dependencies_live.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and Ruff**

Run: `python -m pytest`
Expected: PASS
Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/evaluation/dependencies.py tests/test_evaluation/test_dependencies_live.py
git commit -m "feat: select the live evaluation embedding provider from config"
```

---

## Task 12: Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md` (Model and Cost Policy; Reasoning-Effort Policy; runtime-secrets list; the `config.yaml` sample block)
- Modify: `docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md` (header paragraph + Prerequisites)
- Modify: `README.md` (title line, Project Status, Setup step 4, `## OpenAI Providers` section, Memory example, Agent Runtime example, CLI strict-mode paragraph, `## Individual Agent Evaluation` warning + environment table, `## Development`)
- Test: none — documentation only. The suite and Ruff still must pass.

**Interfaces:**
- Consumes: every name produced by Tasks 1–11.
- Produces: nothing importable.

The 2026-08-16 spec is a historical record for its *architecture*, but its Model and Cost Policy and credential list are superseded, and a reader who lands there must not be misled. Edit those two sections in place and mark them as superseded, exactly as described below; leave the rest of that document untouched.

- [ ] **Step 1: Look up DeepSeek's current pricing and model identifier**

Before editing any document, re-check `https://api-docs.deepseek.com/quick_start/pricing`. The cutover spec's figures were captured on 2026-08-20 and are explicitly subject to a recheck-before-implementation requirement, because rates and model IDs move. Record what you actually observe; do not copy the table below if the live page disagrees with it.

Also settle the alias question the spec flags: the pricing page shows the dated identifier `DeepSeek-V4-Flash-0731`, while requests use the bare alias `deepseek-v4-flash`. Determine whether the API accepts the bare alias. **Do not add a fallback in code either way** — Task 4 already records whatever identifier the response actually reports in `last_model_returned`, and `targets.py` writes it to `TargetOutput.target_model_returned`, which flows into experiment metadata. If the bare alias turns out *not* to be accepted, that is a `config.yaml` and `providers/capabilities.py` pattern change (the anchored pattern is `^deepseek-v4-(flash|pro)$` and would need to admit the dated form) plus a note in the runbook — not a silent substitution at request time. Write the finding down in Step 3's runbook edit whichever way it goes.

- [ ] **Step 2: Update the evaluation design spec's superseded sections**

In `docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md`, replace the `## Model and Cost Policy` intro and model table with:

```markdown
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
```

Replace the Reasoning-Effort Policy table's rows with the approved mapping and replace its "All calls use standard reasoning mode; Pro mode is not part of the initial evaluation harness" paragraph:

```markdown
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
```

Replace the runtime-secrets list and the two sentences below it:

```markdown
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
```

Finally, update that document's sample `config.yaml` `evaluation:` block so it matches the block Task 5 wrote into the real `config.yaml`, and delete its `reasoning_mode: standard` line.

- [ ] **Step 3: Update the live-verification runbook**

In `docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md`, change the opening paragraph's "real network calls to OpenAI and LangSmith" to "real network calls to DeepSeek and LangSmith", and replace the Prerequisites sentence with:

```markdown
Prerequisites before starting: `DEEPSEEK_API_KEY`, `LANGSMITH_API_KEY`,
`LANGSMITH_PROJECT` set in the environment (or a `.env` file next to
`config.yaml`); optionally `LANGSMITH_ENDPOINT` / `LANGSMITH_WORKSPACE_ID`;
`TAVILY_API_KEY` set if step 3 or step 4 exercises an agent that declares
`web_search`; the LangSmith UI open in a browser under the correct
project/workspace. `OPENAI_API_KEY` is **not** required — chat runs on
DeepSeek and embeddings run locally.
```

Then replace the whole `## Step 4 pricing findings` section with your Step 1 findings: the DeepSeek rates you observed, the date you observed them, and an explicit statement of whether the API accepts the bare `deepseek-v4-flash` alias or requires `DeepSeek-V4-Flash-0731`. Add a line noting that the runbook's four open unknowns (judge Source/Evaluator-trace visibility, trace nesting, `temperature` on a reasoning model, the `none` reasoning-effort value) are now to be re-resolved against DeepSeek, not OpenAI — and that the `temperature` unknown is partly answered already: DeepSeek's capability entry sends `temperature` only with thinking `disabled`, which no evaluation case uses.

- [ ] **Step 4: Update the README**

Apply these edits:

1. Line 3: `Multi-agent deep research system using LangGraph, DeepSeek, ChromaDB, and LangSmith.`
2. Line 7 (Project Status): replace `OpenAI chat/embedding providers` with `selectable DeepSeek/OpenAI chat providers, local and OpenAI embedding providers`.
3. Setup step 4: after the existing `.env` paragraph, add: `The default stack is DeepSeek chat with local embeddings, so DEEPSEEK_API_KEY and TAVILY_API_KEY are the only keys a research run needs. OPENAI_API_KEY is required only when provider or embedding_provider is set to openai.`
4. Rename `## OpenAI Providers` to `## Chat and Embedding Providers` and replace its body with the branch README's version of that section (which Task 1 merged in), **corrected for this cutover**: `LLM_EMBEDDING_PROVIDER` joins the override list, `LocalEmbeddingProvider` is described as the default, the claim that "OpenAI embeddings remain active in DeepSeek mode" is deleted, and the secret matrix becomes:

```markdown
| Selected providers | Required for a full research run |
| --- | --- |
| DeepSeek chat + local embeddings (default) | `DEEPSEEK_API_KEY`, `TAVILY_API_KEY` |
| DeepSeek chat + OpenAI embeddings | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI chat + local embeddings | `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI chat + OpenAI embeddings | `OPENAI_API_KEY`, `TAVILY_API_KEY` |
```

5. Memory section example: change `embeddings=OpenAIEmbeddingProvider()` to `embeddings=LocalEmbeddingProvider()` and its import to `from deep_research.providers import LocalEmbeddingProvider`.
6. Agent Runtime example: `from deep_research.providers import build_chat_provider` and `provider=build_chat_provider(settings.llm, tracker)`.
7. CLI strict-mode paragraph: replace `OPENAI_API_KEY` and `TAVILY_API_KEY` with `the selected chat provider's key (DEEPSEEK_API_KEY by default) and TAVILY_API_KEY`.
8. `## Individual Agent Evaluation`: change the money warning's "real OpenAI and LangSmith calls" / "calls the real OpenAI API" to "real DeepSeek and LangSmith calls" / "calls the real DeepSeek API", and replace the first row of the environment-variable table:

```markdown
| `DEEPSEEK_API_KEY` | Always | Target and judge model calls. |
```

adding, below the table: `OPENAI_API_KEY is not required: the evaluation baseline runs chat on DeepSeek and embeddings locally. It is needed only if config.yaml selects an OpenAI chat provider or an OpenAI embedding model.`

9. `## Development`: change `ruff check src/` to `ruff check src/ tests/`, and add a short `## Live DeepSeek Smoke Test` subsection if the merge did not already bring one in (Task 1 merges the branch's README, which contains it — check before adding a duplicate).

- [ ] **Step 5: Verify the docs did not break anything**

Run: `python -m pytest`
Expected: PASS. `tests/test_evaluation/test_scope.py` and the case-registry tests read source files, so a stray edit inside `src/` would surface here.

Run: `python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Confirm no stale requirement survives**

```bash
grep -rn "OPENAI_API_KEY" README.md docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md
```

Expected: every remaining hit describes `OPENAI_API_KEY` as *optional* or as required only under an explicitly-selected OpenAI provider. No hit may say it is always required.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md docs/superpowers/plans/2026-08-16-individual-agent-evaluation-live-verification.md
git commit -m "docs: document the DeepSeek and local-embedding cutover"
```

---

## Final verification

After Task 12, run the acceptance checks from the spec in one pass:

```bash
python -m pytest
python -m ruff check src/ tests/
python -c "from deep_research.utils.config import ConfigSettings as C; s=C(); print(s.llm.provider, s.llm.model, s.llm.thinking_mode, s.llm.reasoning_effort, s.llm.embedding_provider)"
python -c "from deep_research.utils.config import ConfigSettings as C; e=C().evaluation; print(e.target_model, e.judge_model, e.judge_reasoning_effort, e.embedding_model, e.target_reasoning_effort_overrides)"
python -m deep_research.evaluation list
grep -rn "verify_model_access\|openai_client" src/
```

Expected:

- Both test and lint commands clean.
- `deepseek deepseek-v4-flash enabled high local`
- `deepseek-v4-flash deepseek-v4-flash max local {'planner': 'max', 'researcher': 'high', 'source_evaluator': 'high', 'fact_checker': 'max', 'synthesizer': 'max', 'critic': 'max'}`
- `list` prints the agent/tier/case listing without touching the network.
- The final grep returns no matches in `src/`.

No persisted long-term-memory Chroma collection exists, so the 1536-to-384-dimension change needs no migration step — confirm with `ls memory/` that no `chroma.sqlite3` is present before the first real run; if one is, delete the collection directory rather than attempting a migration.

---

## Notes and open questions for the implementer

- **`EvaluationConfig.judge_temperature` is kept.** The spec did not ask for its removal, and it still contributes to `judge_configuration_fingerprint`. Under DeepSeek with thinking enabled the capability table never sends `temperature`, so the value is currently inert for the baseline — but it becomes live again the moment someone selects an OpenAI judge model whose family sends temperature. Task 6's `judge_llm_config` handles the `None` case explicitly because `LLMConfig.temperature` is non-optional after the merge.
- **`evaluation.embedding_model: local` is a sentinel, not a model name.** Task 11 derives the provider selector from it rather than adding an `EvaluationConfig.embedding_provider` field, because the cutover spec pins the `config.yaml` block verbatim and puts new configuration-loading paths out of scope. If a second local embedding model is ever added, that shortcut must become a real field.
- **The embedding model is no longer preflight-checked.** It used to be verified for live runs via OpenAI's model-retrieve endpoint. There is no local capability table for embedding models and no offline way to check an OpenAI entitlement, so an invalid OpenAI embedding model name now fails at the first live-tier embed call instead of at preflight. This is a deliberate, spec-consistent narrowing of preflight ("no live-availability network call for either provider"), and it costs nothing for the local default. Flagging it because it is a real behaviour regression for the `embedding_model: text-embedding-3-small` configuration.
- **`main`'s and the branch's `OpenAIEmbeddingProvider` are two different classes with the same name.** `providers/embeddings.py` holds the synchronous one that `LongTermMemory` actually uses and that `providers/__init__.py` exports; `providers/openai_provider.py` holds an unused async one with an `embed_texts` method. This duplication predates this plan, survives the merge unchanged, and is deliberately left alone here — do not "tidy" it as part of this work. It is worth a separate cleanup ticket.
