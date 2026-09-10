# Streamlit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Every task uses checkbox (`- [ ]`) tracking, a fresh implementation subagent, and an independent task-scoped review. **Do not dispatch a whole-branch review until the human explicitly asks for it.**

**Goal:** Implement the approved **Editorial Research Canvas** Streamlit UI for Deep Research: a narrow persistent session sidebar, an editorial main canvas that progresses from Question -> Investigation -> Answer, and a quiet secondary details rail. The UI must start research through the existing shared engine, show truthful live progress without console noise, render the final Markdown report as the dominant completed-state object, expose compact credibility/fact-check summaries, and provide local searchable session history.

**Architecture:** Keep Streamlit a thin local frontend over the existing `run_research_sync` engine. A UI-local `LocalResearchController` owns one worker thread per started session, receives existing typed `ResearchEvent` callbacks, and exposes immutable UI snapshots. Pure projector functions transform event/state/outcome contracts into presentation models. Streamlit keeps view/navigation state in `st.session_state`, uses a persistent sidebar for New research/recent sessions/history, and refreshes only the running-state fragment every two seconds. Styling is centralized, token-driven, and limited to typography, content width, spacing rhythm, hairline borders, semantic status treatments, selected/active tints, and compact labels. No custom JavaScript is allowed.

**Tech Stack:** Python 3.11+, Streamlit `>=1.49`, Pydantic v2, standard-library `threading`/`pathlib`/`json`, existing `deep_research.main`, existing `ResearchEvent` / `ResearchOutcome` / `ResearchState` contracts, pytest, Streamlit `streamlit.testing.v1.AppTest`, Ruff.

**Functional spec:** `docs/superpowers/specs/2026-07-25-14-streamlit-ui-design.md`

**Parent design:** `docs/superpowers/specs/2026-07-25-agentic-deep-research-design.md`

**Approved visual handoff:** `docs/superpowers/plans/deep-research-streamlit-ui-design-handoff.docx`

The visual handoff is the approved UX Pilot artifact titled **Deep Research Streamlit UI Design Handoff**, selected direction **Editorial Research Canvas**. If the DOCX is not committed at the path above, it must be attached to the implementation/review session. **No visual implementation task may start without access to the handoff screenshots.**

**Visual authority order:**

1. embedded final UX Pilot screen visuals (Figures 1-4 in the handoff);
2. screen-specific written specifications (handoff Sections 4-7);
3. exact visual tokens, component inventory, state matrix, Streamlit mapping, and acceptance checklist;
4. flexible design decisions.

If a screenshot conflicts with a written Streamlit feasibility rule, preserve the written Streamlit rule and the screenshot's information hierarchy rather than recreating an impractical interaction.

---

## Execution and Review Contract

This section is binding for the SDD controller.

- **Implementation model for Tasks 1-10:** GPT-5.6 Luna, **High** reasoning effort. Dispatch a fresh implementation subagent for every task.
- **Task-review model after every task:** GPT-5.6 Luna, **Max** reasoning effort. Every review must independently return both (1) spec/design-compliance verdict and (2) code-quality verdict.
- **Visual-review requirement for Tasks 5-10:** the Luna Max reviewer must have the approved DOCX open and compare the task result against the relevant embedded figure(s), not only against this Markdown plan.
- **Fix rounds 1-3:** resume the original GPT-5.6 Luna High implementer with review findings verbatim, then run a scoped GPT-5.6 Luna Max re-review.
- **Fix rounds 4-5:** dispatch a fresh GPT-5.6 Luna **Max** implementation subagent for the task, then run a scoped GPT-5.6 Luna Max re-review.
- **Five-round breaker:** if important findings remain after round 5, adjudicate and ledger them exactly as required by SDD; do not silently discard findings.
- **No parallel implementation subagents:** tasks share UI contracts and app state. Read-only analysis/review preparation may overlap, implementation may not.
- **Task ledger:** use `.superpowers/sdd/2026-09-08-streamlit-ui/progress.md` and the plan-owned SDD workspace. Do not reuse another plan's ledger.
- **Evidence directory:** task reviewers may save local screenshots/notes under `.superpowers/sdd/2026-09-08-streamlit-ui/evidence/`; this is execution evidence, not product source.
- **Branch-review hard stop:** after Task 10's task-scoped reviewer approves, **STOP**. Do not dispatch a whole-branch reviewer, do not invoke `superpowers:requesting-code-review`, do not invoke `superpowers:finishing-a-development-branch`, and do not merge/push/publish. Record `Branch review: HALTED by explicit human instruction` and wait.

---

## Approved Visual Direction — Fixed Decisions

The following are fixed unless the human explicitly approves a design change:

- **Editorial Research Canvas** is the product direction.
- Persistent **left sidebar**: product identity, `+ New research`, up to five recent sessions, and `Session history` entry point.
- Stable Question -> Investigation -> Answer shell; do not replace it with separate routed pages or a dashboard home.
- Research question dominates New Research and remains identifiable while Running.
- Final report dominates Completed and is never hidden behind a tab or card.
- Running narrative hierarchy is **Agent -> Subtopic -> Current action**; macro iteration is secondary.
- Exactly **three** meaningful recent activity summaries are visible by default while running.
- Tool activity, agent internals, tokens, trace, report path, and technical metadata are secondary/progressively disclosed.
- Credibility uses explicit **High / Moderate / Low / Unrated** counts; fact checking uses explicit **Verified / Unverified / Contradicted / Insufficient evidence** counts. Do not replace either with an opaque overall score or chart.
- Limitations and execution errors are semantically and visually separate.
- Primary navigation does not use tabs.
- Session history is a wrapped-row archive, not `st.dataframe` and not a card grid.
- Custom JavaScript, complex animation, draggable UI, hover-only behavior, and React-only interaction patterns are prohibited.
- Teal = active/primary action; green = completion/verified; amber = limitation/max iterations; red = actual error/contradicted evidence. Color must never be the only status cue.

### Flexible details

The following may adapt to Streamlit without requesting approval, provided hierarchy/fidelity remains intact:

- 2-4px pixel-level spacing shifts.
- Source Sans 3 / Inter may fall back to Streamlit's default sans.
- Source Serif 4 may fall back to Georgia.
- Icon glyphs may change if text labels and semantic weight remain the same.
- Radius may vary from 6px to 8px.
- The right details rail may stack below content earlier than the mock when width becomes cramped.
- A progress bar may be omitted when no defensible numeric progress exists.
- The quiet three-step `What happens next` explanation may be shortened if vertical space is constrained.

---

## Exact Visual Tokens

Use these values as the central token source. Do not scatter ad-hoc colors/spacing throughout render code.

| Token | Value | Usage |
|---|---:|---|
| `color.background` | `#FCFCFA` | Base app/report surface |
| `color.surface` | `#F5F7F6` | Secondary grouped areas |
| `color.text` | `#172126` | Primary text |
| `color.textMuted` | `#66727A` | Metadata/support labels |
| `color.border` | `#DCE2DF` | Dividers/input borders |
| `color.active` | `#0F6F68` | Primary action/active research |
| `color.activeTint` | `#E8F3F1` | Selected session/active subtopic |
| `color.success` | `#2F7A4D` | Completed/verified |
| `color.successTint` | `#EAF4ED` | Quiet success background |
| `color.warning` | `#946200` | Limitations/max iterations |
| `color.warningTint` | `#FFF4D6` | Limitation background |
| `color.error` | `#B42318` | Failure/contradicted evidence |
| `color.errorTint` | `#FDECEA` | Error background |
| `space.1/2/3` | `4/8/12px` | Micro spacing/control gaps |
| `space.4/6/8` | `16/24/32px` | Groups/sections |
| `space.10/12` | `40/48px` | Major transitions |
| `radius.control` | `6px` | Inputs/compact controls |
| `radius.container` | `8px` | Active status/selected row |
| `border.default` | `1px solid #DCE2DF` | Hairline rules |
| `shadow.default` | `none` | No routine elevation |
| `width.sidebar` | `240-270px` | Desktop sidebar intent |
| `width.detailsRail` | `220-260px` | Running/completed secondary rail |
| `width.report` | `720-820px` | Long-form report/form/progress column |
| `type.body` | `15-16px / 1.55-1.7` | UI/report body |
| `type.label` | `12-13px / 600` | Eyebrows/metadata |
| `type.h3` | `19-22px / 600` | Report subheading |
| `type.h2` | `24-28px / 600` | Major report section |
| `type.h1` | `36-42px / 600` | Question/report title |

Additional layout rules:

- Main canvas practical maximum: about `1120px`.
- Main desktop padding: `32-48px`; narrow layout: `20-24px` side padding.
- Target report line length: `65-75` characters.
- Prefer dividers, alignment, whitespace, and typography before adding containers/cards.
- No routine shadows.

---

## Global Engineering Constraints

- Preserve `requires-python = ">=3.11"`.
- Add exactly one runtime dependency: `streamlit>=1.49`. This floor is required by the keyed `st.container` contract used for selected-row and editorial-column styling. Do not add a separate frontend framework, refresh package, browser runtime, database, queue, or new HTTP client.
- The Streamlit UI calls the existing synchronous adapter `run_research_sync` from a worker thread. Do not duplicate orchestration logic and do not require FastAPI.
- Preserve existing CLI/FastAPI behavior.
- Markdown remains the only output format.
- No authentication, multi-user collaboration, report editing, WYSIWYG, provider configuration editor, or secret-entry UI.
- No default test may call DeepSeek/OpenAI, Tavily, LangSmith, ChromaDB, or any live network service.
- Do not run a paid/live research session unless separately authorized at execution time.
- Session-history metadata remains local under `Path(settings.output.directory) / "sessions"` and must not duplicate report bodies, raw events, prompts, tool arguments/results, model messages, provider config, secrets, environment variables, or config file contents.
- Persist history atomically; isolate malformed entries.
- Never render `str(exception)` for unexpected failures. Safe project-owned configuration reason/hint only.
- Token usage is omitted when unavailable; do **not** show `0 tokens` or `Not available` in the visual UI.
- Trace action is omitted when no valid trace URL exists; do not render dead/disabled links.
- Unsupported progress percentages and ETAs are prohibited.
- Raw LangGraph events, node/run/span IDs, serialized graph state, raw prompts/model messages, tool arguments/full responses, retry internals, stack traces, per-agent token tables, and provider details never appear in the primary UI.
- Use Pydantic v2 contracts, typed helpers, Ruff `E/F/I`, pytest, and no hidden mutable globals beyond Streamlit Session State and explicitly locked controller state.

---

## Design-to-Engine Compatibility Rulings

These rulings prevent implementation agents from inventing behavior where the UX handoff asks for information that the current engine does not expose exactly.

1. **Credibility tiers are presenter-level labels, not new research semantics.**
   - `Unrated`: a `ScoredSource` is treated as unrated when `low_confidence is True` and both `recency_score == 0.0` and `relevance_score == 0.0`. This matches the current fallback-scoring path, which floors model-judged recency/relevance while preserving locally computed corroboration and possibly reputation-blended authority.
   - Otherwise `Low`: `low_confidence is True`.
   - Otherwise `Moderate`: `overall_score < 0.75`.
   - Otherwise `High`.
   - The `0.75` boundary is a UI bucketing rule, not an engine threshold. Do not feed it back into agents.
2. **Source-detail “report section” is not a structured engine field.** Do not parse Markdown to fabricate it. Instead, group `ResearchState.raw_findings` by source URL and show distinct `Finding.related_sub_topic` values under the label **Used in research topics**. This preserves structured provenance without pretending to know exact rendered-report section placement.
3. **Corroboration state:** expose the structured `corroboration_score` as a compact textual value/details row; do not invent a categorical label unless one is added by a later spec.
4. **Tool activity:** backend tool names may be used internally for aggregation, but the UI renders allowlisted/plain-language summaries such as `Searched sources`, `Reviewed documents`, or `Extracted evidence`. Never render tool arguments or full outputs.
5. **Agent labels:** convert engine identifiers to plain labels (`source_evaluator` -> `Source evaluator`) with a deterministic formatting helper; no avatars/personas.
6. **Historical “running” metadata:** local history is not a process-resume system. A previously persisted `running` entry with no matching active in-memory controller record must render as **Incomplete**, never as currently live.

If an implementation agent believes a different engine contract change is required, stop that task, ledger the exact gap, and request adjudication. Do not silently extend agents/providers/synthesizer behavior.

---

## Existing Contracts This Plan Reuses

Read these before Task 1 and treat them as source-of-truth interfaces:

- `src/deep_research/main.py`
  - `DEFAULT_CONFIG_PATH = "config.yaml"`
  - `SUPPORTED_OUTPUT_FORMATS = ("markdown",)`
  - `new_session_id()`
  - `prepare_research_settings(*, config_path: str, output_format: str | None, config_overrides: Mapping[str, JsonValue] | None = None) -> ConfigSettings`
  - `run_research_sync(**kwargs) -> ResearchOutcome`
  - synchronous `event_handler: Callable[[ResearchEvent], None]`
- `src/deep_research/runtime/outcome.py`
  - `ResearchOutcome.session_id`, `question`, `status`, `state`, `trace_url`, `report_path`, `token_usage`, `tool_calls`, `report`, `errors`
- `src/deep_research/utils/types.py`
  - `Finding.source_url`, `Finding.related_sub_topic`
  - `ScoredSource.url`, `title`, dimension scores, `overall_score`, `rationale`, `low_confidence`
  - `Claim.text`, `source_urls`, `verdict`, `confidence`, `evidence`, `contradictions`
  - `ResearchEvent`, `ResearchError`, `ResearchState`
- `src/deep_research/agents/source_evaluator.py`
  - current fallback path floors `recency_score` and `relevance_score` and sets `low_confidence=True`
  - current domain low-confidence threshold is engine-owned; the UI must not alter it
- `src/deep_research/graph/events.py`
  - `graph.node.started` has node/iteration metadata
  - `graph.session.completed` has terminal status/iteration metadata
- `src/deep_research/agents/researcher.py`
  - subtopic start/completion and tool-call events
- `src/deep_research/utils/config.py`
  - non-strict config may discover local UI defaults/output path
  - strict `prepare_research_settings` remains the gate immediately before a run

---

## Concrete File Map

| File | Change | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add Streamlit runtime dependency. |
| `src/deep_research/ui/__init__.py` | Create | Export framework-light controller/contracts. |
| `src/deep_research/ui/models.py` | Create | Validated UI snapshots, quality/detail models, history metadata. |
| `src/deep_research/ui/progress.py` | Create | Pure event projection, humanized activity, credibility/fact summaries. |
| `src/deep_research/ui/history.py` | Create | Safe atomic metadata store/report reads. |
| `src/deep_research/ui/runner.py` | Create | Thread-safe in-process controller around `run_research_sync`. |
| `src/deep_research/ui/styles.py` | Create | Exact design tokens + static CSS boundary. |
| `src/deep_research/ui/components.py` | Create | Reusable Streamlit render helpers for shell/status/quality/history rows. |
| `src/deep_research/ui/app.py` | Create | Entry point, navigation state, four approved screen states. |
| `tests/test_ui/__init__.py` | Create | UI test package marker. |
| `tests/test_ui/fakes.py` | Create | Offline controller/outcome/state fixtures. |
| `tests/test_ui/test_models.py` | Create | UI contract tests. |
| `tests/test_ui/test_progress.py` | Create | Projector/activity/quality tests. |
| `tests/test_ui/test_history.py` | Create | Persistence/path-safety tests. |
| `tests/test_ui/test_runner.py` | Create | Non-blocking controller/failure/history tests. |
| `tests/test_ui/test_styles.py` | Create | Token/CSS-boundary tests. |
| `tests/test_ui/test_app.py` | Create | AppTest coverage for shell and all four states. |
| `tests/test_ui/manual_mock_app.py` | Create | Deterministic offline visual acceptance app. |
| `README.md` | Modify | Launch/behavior/history/offline acceptance docs. |

No agent, graph, provider, memory, or observability implementation file is planned for modification.

---

### Task 1: Add Streamlit and the presentation contracts

**Files:**
- Modify: `pyproject.toml`
- Create: `src/deep_research/ui/__init__.py`
- Create: `src/deep_research/ui/models.py`
- Create: `tests/test_ui/__init__.py`
- Create: `tests/test_ui/test_models.py`

**Interfaces produced:**

```python
UiSessionStatus = Literal[
    "running",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
]

UiCredibilityTier = Literal["high", "moderate", "low", "unrated"]

class UiTokenUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

class UiToolCallSummary(BaseModel):
    tool_name: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    calls: int = Field(ge=0)
    failures: int = Field(ge=0)

class UiSubTopicProgress(BaseModel):
    index: int = Field(ge=1)
    title: str = Field(min_length=1)
    status: Literal["queued", "running", "completed"]
    priority: int | None = Field(default=None, ge=1)
    findings: int | None = Field(default=None, ge=0)

class UiRecentActivity(BaseModel):
    event_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)

class UiSourceDetail(BaseModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    tier: UiCredibilityTier
    overall_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    corroboration_score: float = Field(ge=0.0, le=1.0)
    related_sub_topics: list[str] = Field(default_factory=list)

class UiSourceSummary(BaseModel):
    total: int = Field(ge=0)
    high: int = Field(ge=0)
    moderate: int = Field(ge=0)
    low: int = Field(ge=0)
    unrated: int = Field(ge=0)
    details: list[UiSourceDetail] = Field(default_factory=list)

class UiClaimDetail(BaseModel):
    text: str = Field(min_length=1)
    verdict: ClaimVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)

class UiFactCheckSummary(BaseModel):
    verified: int = Field(ge=0)
    unverified: int = Field(ge=0)
    contradicted: int = Field(ge=0)
    insufficient_evidence: int = Field(ge=0)
    details: list[UiClaimDetail] = Field(default_factory=list)

class UiSessionSnapshot(BaseModel):
    session_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: UiSessionStatus
    started_at: datetime
    finished_at: datetime | None = None
    current_agent: str | None = None
    iteration: int = Field(ge=0)
    max_iterations: int = Field(ge=1)
    sub_topics: list[UiSubTopicProgress] = Field(default_factory=list)
    recent_activity: list[UiRecentActivity] = Field(default_factory=list)
    tool_calls: list[UiToolCallSummary] = Field(default_factory=list)
    token_usage: UiTokenUsage | None = None
    trace_url: str | None = None
    report_path: str | None = None
    report: str | None = None
    source_summary: UiSourceSummary
    fact_check_summary: UiFactCheckSummary
    errors: list[ResearchError] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    events_seen: int = Field(default=0, ge=0)

class SessionHistoryEntry(BaseModel):
    session_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: UiSessionStatus
    started_at: datetime
    finished_at: datetime | None = None
    iteration: int = Field(ge=0)
    max_iterations: int = Field(ge=1)
    report_path: str | None = None
    trace_url: str | None = None
    token_usage: UiTokenUsage | None = None
    source_summary: UiSourceSummary
    fact_check_summary: UiFactCheckSummary
    errors: list[ResearchError] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
```

- [ ] **Step 1: Write contract tests first.** Verify valid status/tier values, nonnegative counts, token total, queued/running/completed subtopic states, and Pydantic rejection of unknown statuses.
- [ ] **Step 2: Verify persistent-history minimization.** The earlier “no `recent_activity`” minimization rule was superseded by the later stopping-point remediation: retain only the bounded plain-language `recent_activity` needed to preserve the last known stopping point. Assert `SessionHistoryEntry` has no report bodies, raw events, `raw_findings`, prompts, tool payloads/results, model messages, provider config, secrets, environment variables, or config file contents. `source_summary.details` and `fact_check_summary.details` must also be excluded from persisted history by using summary-only serialization helpers later; do not persist full per-source/per-claim rationale in history.
- [ ] **Step 3: Run RED.**

```bash
python -m pytest tests/test_ui/test_models.py -v
```

Expected: import failure because the UI contracts do not exist.

- [ ] **Step 4: Add the runtime dependency.** Add exactly `"streamlit>=1.49",` to the runtime dependency list. The floor supports the keyed `st.container` contract used by the UI styling.
- [ ] **Step 5: Implement contracts.** Use a shared `ContractModel`-style Pydantic config (`extra="forbid"`, stripped strings, validated defaults). Avoid importing Streamlit from `models.py` or `ui/__init__.py`.
- [ ] **Step 6: Add an explicit history compaction function.** `history_entry_from_snapshot` must copy summary counts but strip `source_summary.details` and `fact_check_summary.details` before persistence.
- [ ] **Step 7: Run focused tests and lint.**

```bash
python -m pytest tests/test_ui/test_models.py -v
python -m ruff check src/deep_research/ui tests/test_ui/test_models.py
```

Expected: PASS.

- [ ] **Step 8: Commit.**

```bash
git add pyproject.toml src/deep_research/ui tests/test_ui
git commit -m "feat: add streamlit presentation contracts"
```

**Task-review gate:** Luna Max verifies model fields against the handoff's component/state requirements, confirms history data minimization, and confirms no Streamlit import leaks into framework-light modules.

---

### Task 2: Build truthful progress, activity, credibility, and fact-check projection

**Files:**
- Create: `src/deep_research/ui/progress.py`
- Create: `tests/test_ui/test_progress.py`

**Interfaces produced:**

```python
class ProgressSummary(BaseModel):
    current_agent: str | None = None
    iteration: int = Field(default=0, ge=0)
    sub_topics: list[UiSubTopicProgress] = Field(default_factory=list)
    recent_activity: list[UiRecentActivity] = Field(default_factory=list)
    tool_calls: list[UiToolCallSummary] = Field(default_factory=list)
    events_seen: int = Field(default=0, ge=0)
```

Functions:

- `project_progress(events: Sequence[ResearchEvent]) -> ProgressSummary`
- `display_agent_name(agent: str | None) -> str`
- `display_tool_name(tool_name: str) -> str`
- `token_usage_from_outcome(outcome: ResearchOutcome) -> UiTokenUsage | None`
- `credibility_tier(source: ScoredSource) -> UiCredibilityTier`
- `source_summary(sources: Sequence[ScoredSource], findings: Sequence[Finding]) -> UiSourceSummary`
- `fact_check_summary(claims: Sequence[Claim]) -> UiFactCheckSummary`
- `limitations_from_outcome(outcome: ResearchOutcome) -> list[str]`

**Projection rules:**

1. `graph.node.started`: set current agent from nonblank `metadata["node"]`.
2. Any non-boolean integer `metadata["iteration"] >= 0`: update macro iteration.
3. `researcher.sub_topic.started`: upsert by positive integer `index`; mark running; preserve title/priority.
4. `researcher.sub_topic.completed`: mark same index completed; preserve title; store findings count when valid.
5. Once subtopic count is known, any missing indices between 1 and max observed index may remain absent; do not invent titles. If the planning state later supplies all subtopics through final outcome, the controller replaces the live list with a complete queued/running/completed sequence.
6. Events containing a nonblank tool identifier are aggregated internally. Failure increments only when `success is False`.
7. `graph.session.completed`: clear current agent after applying terminal iteration.
8. Ignore malformed metadata rather than raising.
9. `events_seen == len(events)`.
10. Recent activity returns the **three newest meaningful summaries only**. Raw event messages are not passed through blindly.

**Meaningful activity formatter:**

Use deterministic mappings for known event families. Examples:

- subtopic completed -> a sentence such as `Completed Market size; 3 evidence items recorded` when a count exists;
- subtopic started -> a sentence such as `Started Regulatory drivers`;
- successful researcher tool call -> a sentence such as `Evaluated new evidence for Regulatory drivers`;
- failed tool call -> `A research step had an issue; continuing`;
- graph node start is not itself a recent-activity row unless no more user-meaningful event exists.

Do not display raw event payloads, tool function names, JSON, IDs, or provider text.

**Credibility tier rules:**

```python
def credibility_tier(source: ScoredSource) -> UiCredibilityTier:
    if (
        source.low_confidence
        and source.recency_score == 0.0
        and source.relevance_score == 0.0
    ):
        return "unrated"
    if source.low_confidence:
        return "low"
    if source.overall_score < 0.75:
        return "moderate"
    return "high"
```

`source_summary` must group `Finding.related_sub_topic` by normalized source URL and attach the distinct ordered topics to each `UiSourceDetail`. Do not parse the report Markdown.

- [ ] **Step 1: Write event-projection tests.** Cover agent/iteration, subtopic start/completion, tool aggregation, failure counts, terminal clear, malformed metadata, ordering, and exactly-three recent-activity cap.
- [ ] **Step 2: Write humanization tests.** Assert `source_evaluator -> Source evaluator`, tool labels are plain-language, raw function names are not rendered in activity summaries, and unknown identifiers fall back to safe title-cased text rather than raw snake_case.
- [ ] **Step 3: Write credibility/fact tests.** Cover all four credibility tiers, all four claim verdicts, related-subtopic grouping, and no report-text parsing.
- [ ] **Step 4: Write token/limitation tests.** `(0,0)` -> `None`; nonzero -> usage. Critic gaps/unsupported claims become deduplicated limitations. Errors remain separate.
- [ ] **Step 5: Run RED.**

```bash
python -m pytest tests/test_ui/test_progress.py -v
```

- [ ] **Step 6: Implement projector as pure functions.** No Streamlit, filesystem, network, or locks in this module.
- [ ] **Step 7: Run tests and lint.**

```bash
python -m pytest tests/test_ui/test_progress.py -v
python -m ruff check src/deep_research/ui/progress.py tests/test_ui/test_progress.py
```

- [ ] **Step 8: Commit.**

```bash
git add src/deep_research/ui/progress.py tests/test_ui/test_progress.py
git commit -m "feat: project research state for editorial ui"
```

**Task-review gate:** Luna Max verifies the presenter never fabricates ETA/progress, recent activity is plain-language and capped at three, credibility tiers follow the plan exactly, and structured state—not report prose—drives summaries.

---

### Task 3: Persist safe local session-history metadata

**Files:**
- Create: `src/deep_research/ui/history.py`
- Create: `tests/test_ui/test_history.py`

**Public interface:**

- `SessionHistoryStore.__init__(self, *, output_directory: Path)`
- `metadata_directory -> Path`
- `upsert(entry: SessionHistoryEntry) -> None`
- `get(session_id: str) -> SessionHistoryEntry | None`
- `list_entries(*, limit: int = 50) -> list[SessionHistoryEntry]`
- `read_report(entry: SessionHistoryEntry) -> str | None`

Persisted layout:

```text
Path(settings.output.directory)/
|-- sessions/
|   |-- {session_id}.json
|   `-- {other_session_id}.json
`-- report files written by the existing synthesizer
```

- [ ] **Step 1: Write persistence tests.** Cover lazy directory creation, overwrite-by-session ID, newest-first sorting, limit, malformed JSON isolation, unknown IDs, missing report path, and report read under output root.
- [ ] **Step 2: Write path traversal test.** A metadata report path resolving outside the configured output root must return `None`.
- [ ] **Step 3: Write stale-running test.** The store may deserialize status `running`, but the controller/UI layer must later convert a historical non-active running entry to `incomplete`; history itself remains a neutral data store.
- [ ] **Step 4: Run RED.**

```bash
python -m pytest tests/test_ui/test_history.py -v
```

- [ ] **Step 5: Implement safe ID validation and atomic writes.** Accept only generated session-ID characters; write to `.json.tmp`, then replace target.
- [ ] **Step 6: Implement per-file error isolation.** Catch only expected I/O/validation failures per entry; do not hide programmer errors around the whole scan.
- [ ] **Step 7: Restrict report reads to output root.** Resolve relative engine paths safely; refuse escapes.
- [ ] **Step 8: Run tests and lint.**

```bash
python -m pytest tests/test_ui/test_history.py -v
python -m ruff check src/deep_research/ui/history.py tests/test_ui/test_history.py
```

- [ ] **Step 9: Commit.**

```bash
git add src/deep_research/ui/history.py tests/test_ui/test_history.py
git commit -m "feat: persist safe local research history"
```

**Task-review gate:** Luna Max focuses on privacy/data minimization, atomic replacement, malformed-file isolation, path traversal resistance, and runtime-only storage.

---

### Task 4: Implement the non-blocking local research controller

**Files:**
- Create: `src/deep_research/ui/runner.py`
- Modify: `src/deep_research/ui/__init__.py`
- Create: `tests/test_ui/fakes.py`
- Create: `tests/test_ui/test_runner.py`

**Protocol members:**

- read-only property `default_max_iterations -> int`
- `start(self, *, question: str, max_iterations: int, output_format: str = "markdown") -> UiSessionSnapshot`
- `snapshot(self, session_id: str) -> UiSessionSnapshot`
- `list_history(self, *, limit: int = 50) -> list[SessionHistoryEntry]`
- `history_entry(self, session_id: str) -> SessionHistoryEntry | None`
- `read_history_report(self, entry: SessionHistoryEntry) -> str | None`

Private active-session record keeps immutable identity/timestamps plus append-only events/outcome behind `threading.RLock`.

- [ ] **Step 1: Create offline fakes.** `make_outcome`, `GatedSyncRunner`, and `FailingSyncRunner` use real domain contracts but no network.
- [ ] **Step 2: Write non-blocking tests.** `start()` returns running before gated runner finishes; exactly one worker starts; strict preflight occurs before registration.
- [ ] **Step 3: Write snapshot tests.** Published events update agent/iteration/subtopics/activity; returned snapshots are copies; final outcome is authoritative.
- [ ] **Step 4: Write terminal-quality tests.** Final source summary uses `evaluated_sources + raw_findings`; claim summary uses `verified_claims`; tool totals use `outcome.tool_calls`; token `None` semantics hold.
- [ ] **Step 5: Write safe-failure tests.** Unexpected exception text never reaches snapshot/history. Late configuration errors store only project-owned reason/hint.
- [ ] **Step 6: Write stale-history normalization test.** `history_entry()` or a dedicated `display_history_entry()` must convert persisted `running` to `incomplete` when no active in-memory session exists.
- [ ] **Step 7: Run RED.**

```bash
python -m pytest tests/test_ui/test_runner.py -v
```

- [ ] **Step 8: Implement controller construction.** Non-strict config only discovers output directory/default max iterations so the app can load; strict `prepare_research_settings` still gates Start.
- [ ] **Step 9: Implement worker start exactly through `run_research_sync`.** Do not call `asyncio.run` directly; do not use FastAPI.
- [ ] **Step 10: Implement locked event publication and snapshot building.** Copy under lock; project outside lock.
- [ ] **Step 11: Persist initial running metadata and terminal metadata.** Initial entry makes the session immediately visible; terminal upsert replaces it.
- [ ] **Step 12: Run focused suite and lint.**

```bash
python -m pytest tests/test_ui/test_runner.py tests/test_ui/test_models.py tests/test_ui/test_progress.py tests/test_ui/test_history.py -v
python -m ruff check src/deep_research/ui tests/test_ui
```

- [ ] **Step 13: Commit.**

```bash
git add src/deep_research/ui tests/test_ui
git commit -m "feat: add nonblocking streamlit research controller"
```

**Task-review gate:** Luna Max focuses on concurrency/lock boundaries, exact engine reuse, safe failures, stale-history normalization, terminal-authoritative state, and no server dependency.

---

### Task 5: Establish the Editorial Research Canvas visual foundation and persistent shell

**Design evidence:** handoff Sections 1-3, Figure sidebars in Figures 1-4, Sections 12-13, and Section 16 fixed decisions.

**Files:**
- Create: `src/deep_research/ui/styles.py`
- Create: `src/deep_research/ui/components.py`
- Create: `src/deep_research/ui/app.py`
- Create: `tests/test_ui/test_styles.py`
- Create: `tests/test_ui/test_app.py`

**Session-state keys:**

```python
_CONTROLLER_KEY = "_deep_research_controller"
_VIEW_KEY = "_deep_research_view"               # "new" | "current" | "history"
_ACTIVE_SESSION_KEY = "_deep_research_active_session_id"
_SELECTED_SESSION_KEY = "_deep_research_selected_session_id"
_HISTORY_SEARCH_KEY = "_deep_research_history_search"
_HISTORY_FILTER_KEY = "_deep_research_history_filter"
_START_ERROR_KEY = "_deep_research_start_error"
```

**Shell behavior:**

- Sidebar is persistent and uses native Streamlit collapse behavior; never a custom floating drawer.
- Sidebar order: product identity -> `+ New research` -> `RECENT SESSIONS` -> up to five recent rows -> bottom `Session history` action.
- Current session appears first in recent sessions and has explicit status text.
- Main canvas maximum around 1120px; inner editorial column around 720-820px.
- Running/Completed may add a 220-260px secondary rail through native columns.
- Primary navigation does not use tabs.

- [ ] **Step 1: Write style-token tests.** Assert `styles.py` exports the exact approved colors, spacing/radii, report width, and static CSS. Assert CSS contains no `<script`, `javascript:`, animation keyframes, external font URL, or network import.
- [ ] **Step 2: Write shell AppTests.** Initial app shows `Deep Research`, `New research`, `Recent sessions`, and `Session history`. No tabs. Recent list caps at five and keeps questions wrapped/truncated only visually, not data-destructively.
- [ ] **Step 3: Write navigation AppTests.** New research sets view `new`; history sets view `history`; a recent session sets selected ID and view `current`. Current selection has explicit status text independent of color.
- [ ] **Step 4: Run RED.**

```bash
python -m pytest tests/test_ui/test_styles.py tests/test_ui/test_app.py -k "shell or navigation or style" -v
```

- [ ] **Step 5: Implement centralized tokens/CSS.** CSS boundary is limited to page/max widths, base/background text colors, serif heading treatment, body rhythm, hairline dividers, compact labels/status badges, active tint, and small spacing corrections. No custom JavaScript.
- [ ] **Step 6: Use font fallbacks without external assets.** Prefer Streamlit/system sans for UI and Georgia for editorial titles/headings unless locally available fonts already exist; do not add font files or network font requests.
- [ ] **Step 7: Implement `render_sidebar(controller)`.** Use native buttons/containers; every status includes word + icon/shape. Do not make recent-session rows filled primary buttons.
- [ ] **Step 8: Implement app/controller resolution.** Store supplied/default controller in session state; no `st.cache_resource` global cross-session controller.
- [ ] **Step 9: Implement view router.** `render_app()` applies styles, renders sidebar, then routes only among the three session-state views; no custom URL router.
- [ ] **Step 10: Run focused tests/lint.**

```bash
python -m pytest tests/test_ui/test_styles.py tests/test_ui/test_app.py -k "shell or navigation or style" -v
python -m ruff check src/deep_research/ui tests/test_ui
```

- [ ] **Step 11: Commit.**

```bash
git add src/deep_research/ui/styles.py src/deep_research/ui/components.py src/deep_research/ui/app.py tests/test_ui/test_styles.py tests/test_ui/test_app.py
git commit -m "feat: add editorial streamlit app shell"
```

**Task-review gate:** Luna Max compares the shell against all four screenshots: narrow persistent sidebar, editorial main canvas, restrained surfaces, no dashboard/card-grid substitution, no tabs, no external assets/JS, and token values match the handoff.

---

### Task 6: Implement Screen 1 — New Research

**Design evidence:** Figure 1 (handoff page 5) and Screen 1 specification.

**Files:**
- Modify: `src/deep_research/ui/components.py`
- Modify: `src/deep_research/ui/app.py`
- Modify: `tests/test_ui/test_app.py`

**Required visual order in main editorial column:**

1. small `NEW RESEARCH SESSION` eyebrow;
2. serif question-oriented title such as `What would you like to research?`;
3. one concise explanatory sentence;
4. dominant research-question textarea, about 140-170px tall, full editorial-column width;
5. divider;
6. compact two-column configuration: Maximum iterations + fixed Markdown output indicator;
7. action row: `Ready to start` (or field validation) + single filled teal `Start Research` button;
8. quiet `WHAT HAPPENS NEXT` three-step explanation below the form.

**Important differences from the pre-design plan:**

- Do **not** render output format as a disabled-looking selector if it appears unavailable. Prefer plain read-only text/caption (`Markdown`) or a legible read-only field.
- `Start Research` is the only filled primary action in the product shell.
- Validation is adjacent to the form/action region.

- [ ] **Step 1: Write first-screen AppTest.** Assert question text area, max iterations, visible Markdown indicator, Start action, and Ready status. Assert the report/history screen does not render on initial load.
- [ ] **Step 2: Write validation AppTests.** Blank/whitespace question prevents submit and preserves values; valid question can submit; configuration error preserves the draft.
- [ ] **Step 3: Write safe-error test.** A fake configuration error with sensitive message renders only generic error plus safe project hint; sensitive message absent.
- [ ] **Step 4: Write start-transition test.** Clicking Start calls controller exactly once, stores session ID, moves view to `current`, and immediately renders a running state on rerun.
- [ ] **Step 5: Run RED.**

```bash
python -m pytest tests/test_ui/test_app.py -k "new_research or start or configuration" -v
```

- [ ] **Step 6: Implement one atomic `st.form`.** Use `st.text_area`, `st.number_input`, read-only Markdown indicator, and `st.form_submit_button`. Keep max-iteration configuration visually subordinate.
- [ ] **Step 7: Implement action-state behavior.** Start is disabled only for invalid/blank or in-flight submission; old terminal sessions do not block a new run.
- [ ] **Step 8: Implement three-step reassurance.** Plain text, no cards: `Plan subtopics` -> `Search & evaluate` -> `Synthesize report`.
- [ ] **Step 9: Run tests/lint.**

```bash
python -m pytest tests/test_ui/test_app.py -k "new_research or start or configuration" -v
python -m ruff check src/deep_research/ui tests/test_ui/test_app.py
```

- [ ] **Step 10: Run offline visual comparison for Figure 1.** Use a fake controller with recent sessions, render New Research, capture evidence, and compare hierarchy/spacing/action emphasis to Figure 1. Do not require pixel-perfect equality; fixed decisions and visual tokens must match.
- [ ] **Step 11: Commit.**

```bash
git add src/deep_research/ui/components.py src/deep_research/ui/app.py tests/test_ui/test_app.py
git commit -m "feat: add streamlit new research screen"
```

**Task-review gate:** Luna Max compares directly with Figure 1 and verifies question dominance, compact config, fixed Markdown treatment, only one filled primary action, sidebar continuity, and safe error behavior.

---

### Task 7: Implement Screen 2 — Research Running with truthful two-second fragment refresh

**Design evidence:** Figure 2 (handoff page 8), Screen 2 specification, progressive-disclosure section.

**Files:**
- Modify: `src/deep_research/ui/components.py`
- Modify: `src/deep_research/ui/app.py`
- Modify: `tests/test_ui/test_app.py`

**Layout:** use `st.columns([3, 1])` for main narrative + quiet details rail when width permits.

**Main column order:**

1. `RESEARCH IN PROGRESS` eyebrow/status;
2. serif research-question header;
3. small metadata line with `Markdown` and configured max iterations;
4. stable current-activity container with plain-language hierarchy:
   - bold agent label;
   - `Subtopic X of Y`;
   - current action (`Searching and evaluating sources` or another deterministic phrase);
   - secondary `Macro iteration N of M`;
   - horizontal progress only if defensible;
   - health line (`No issues detected` or concise issue count);
5. `SUBTOPIC SEQUENCE` rows: completed / active / queued, with text labels + shape/icon + active pale teal tint;
6. `RECENT ACTIVITY`: exactly three newest meaningful plain-language summaries.

**Details rail order:**

- total token metric only when available;
- `Open LangSmith trace` only when valid URL exists;
- collapsed `Tool activity` expander;
- collapsed `Agent details` expander.

**Refresh rule:**

```python
@st.fragment(run_every="2s")
def render_live_progress(controller: ResearchController) -> None:
    session_id = st.session_state.get(_ACTIVE_SESSION_KEY)
    if not isinstance(session_id, str):
        return
    snapshot = controller.snapshot(session_id)
    _render_running_snapshot(snapshot)
```

Only the live region refreshes; no sleep, third-party refresh package, network polling layer, or page-wide timed rerun.

- [ ] **Step 1: Write running-state AppTests.** Assert question, current agent, `Subtopic 2 of 5`, macro iteration, health, sequence states, and exactly three recent activity rows.
- [ ] **Step 2: Write unavailable-observability tests.** When token usage/trace are absent, their widgets are **omitted**; assert `Not available`, `0 tokens`, and dead trace controls do not appear.
- [ ] **Step 3: Write available-observability tests.** Nonzero usage renders one compact total; valid trace renders labeled link button.
- [ ] **Step 4: Write progress-truth tests.** With no defensible numeric progress value, iteration/subtopic counts render but no percentage/ETA. If a numeric fraction is explicitly derived from known completed/total subtopics, label it as phase progress rather than total research completion.
- [ ] **Step 5: Write disclosure tests.** Tool/agent details are collapsed secondary elements; raw event JSON, IDs, tool args/results, prompt text, provider details, stack traces absent.
- [ ] **Step 6: Write terminal transition tests.** Completed -> report view in same canvas; max iterations -> amber terminal treatment; failure retains last known agent/iteration/subtopic/activity and shows safe error.
- [ ] **Step 7: Run RED.**

```bash
python -m pytest tests/test_ui/test_app.py -k "running or progress or observability" -v
```

- [ ] **Step 8: Implement stable live fragment.** Preserve sidebar selection and user-expanded secondary details across fragment updates; do not move focus on every event.
- [ ] **Step 9: Implement health calculation.** No errors -> `No issues detected`; recoverable errors -> concise `N issues`; unrecoverable/failed -> explicit terminal error treatment. Do not expose diagnostic text in primary status.
- [ ] **Step 10: Implement subtopic sequence.** When full state is available, queued items remain visible; one active row receives `color.activeTint`; completed/queued remain restrained.
- [ ] **Step 11: Implement recent activity cap.** Render only `snapshot.recent_activity[-3:]` in chronological display order. No scrolling event console.
- [ ] **Step 12: Run tests/lint.**

```bash
python -m pytest tests/test_ui/test_app.py -k "running or progress or observability" -v
python -m ruff check src/deep_research/ui tests/test_ui/test_app.py
```

- [ ] **Step 13: Run offline visual comparison for Figure 2.** Verify the run narrative visually dominates the rail, current agent is text not persona, recent activity is exactly three rows, and detail rail is quiet.
- [ ] **Step 14: Commit.**

```bash
git add src/deep_research/ui/components.py src/deep_research/ui/app.py tests/test_ui/test_app.py
git commit -m "feat: add calm live research progress view"
```

**Task-review gate:** Luna Max compares Figure 2 and rejects console-like logs, KPI-card grids, raw tool names/details, false percentages/ETA, unavailable telemetry shells, or any layout where the details rail competes with the current research narrative.

---

### Task 8: Implement Screen 3 — Research Completed report-first reading experience

**Design evidence:** Figures 3A-3C (handoff pages 11-13), Screen 3 specification, quality/metadata sections.

**Files:**
- Modify: `src/deep_research/ui/components.py`
- Modify: `src/deep_research/ui/app.py`
- Modify: `tests/test_ui/test_app.py`

**Layout:** `st.columns([3, 1])` where report width remains comfortable; allow the rail to stack below if necessary.

**Main report column order:**

1. explicit completion state label (green for completed, amber for max iterations/partial);
2. research-question title in editorial serif treatment;
3. neutral completion metadata such as `Completed in 3 of 4 iterations` and date/time;
4. report path as selectable code-formatted metadata near the header or inside Session metadata;
5. rendered Markdown report directly on the base surface—no enclosing card/tab;
6. source list if already present in report/state rendering;
7. `Limitations` section after report/sources, amber/neutral only when present;
8. `Execution errors` section separate from limitations; red only for actual failures; quiet `No errors reported` confirmation is allowed.

**Right rail:**

- `SOURCE CREDIBILITY`: High / Moderate / Low / Unrated labeled counts, no chart/overall score;
- expandable source details: source, tier, rationale, `Used in research topics`, corroboration score;
- `FACT-CHECK SUMMARY`: Verified / Unverified / Contradicted / Insufficient evidence labeled counts;
- expandable claim detail: claim, verdict, confidence/evidence, supporting URLs, contradictions where present;
- total tokens only when available;
- `Open LangSmith trace` only when available;
- collapsed `Session metadata`: session ID, start/completion times, configured limit, actual iteration, terminal status, report path.

**Typography:** report body 15-16px, 1.55-1.7 line height, editorial headings, 720-820px reading width, lightweight 13-14px tables.

- [ ] **Step 1: Write completed-report AppTests.** Assert Markdown report visible immediately, completion state, report path, credibility counts, fact-check counts, limitations, error section, tokens/trace when present.
- [ ] **Step 2: Write report-dominance structure test.** The report is in the primary main column and is not behind `st.tabs` or an expander. Quality summaries are secondary.
- [ ] **Step 3: Write credibility details tests.** All four labeled tiers render; no average score/gauge/chart. Expandable details show title/url/tier/rationale/related subtopics/corroboration.
- [ ] **Step 4: Write fact-check detail tests.** All four verdict counts; expanded claims preserve explicit verdict and structured evidence/source URLs.
- [ ] **Step 5: Write limitation/error distinction tests.** Limitations never use red error styling; actual errors use red and remain separate even when both exist.
- [ ] **Step 6: Write telemetry omission tests.** Missing token/trace elements disappear entirely.
- [ ] **Step 7: Write partial/max-iterations tests.** Amber `Max iterations` or `Partial report` state retains readable report when available and states why the run ended without styling it as a crash.
- [ ] **Step 8: Run RED.**

```bash
python -m pytest tests/test_ui/test_app.py -k "completed or report or credibility or fact_check" -v
```

- [ ] **Step 9: Implement report viewer.** Use `st.markdown(snapshot.report)` directly on base canvas. Do not preprocess report content merely for styling.
- [ ] **Step 10: Implement quality rail.** Use compact labeled rows/small metrics, not a 4x dashboard grid and not charts. Every semantic color has visible text.
- [ ] **Step 11: Implement detail expanders.** Source/claim/session metadata remain one level deeper; no raw internal state.
- [ ] **Step 12: Implement limitations and execution errors separately.** Error diagnostics may be in a nested expander but must remain safe/sanitized.
- [ ] **Step 13: Run tests/lint.**

```bash
python -m pytest tests/test_ui/test_app.py -k "completed or report or credibility or fact_check" -v
python -m ruff check src/deep_research/ui tests/test_ui/test_app.py
```

- [ ] **Step 14: Run offline visual comparison against Figures 3A-3C.** Review top/header/quality rail, long-form body/table rhythm, report conclusion/sources, and separate limitations/errors. Ensure rail never squeezes report below readable width.
- [ ] **Step 15: Commit.**

```bash
git add src/deep_research/ui/components.py src/deep_research/ui/app.py tests/test_ui/test_app.py
git commit -m "feat: add report-first completed research view"
```

**Task-review gate:** Luna Max must use Figures 3A-3C and Section 15 acceptance criteria. Reject a dashboard-style completion screen, report hidden behind navigation, opaque source score, merged errors/limitations, or unreadably narrow report column.

---

### Task 9: Implement Screen 4 — searchable local Session History

**Design evidence:** Figure 4 (handoff page 16) and Screen 4 specification.

**Files:**
- Modify: `src/deep_research/ui/components.py`
- Modify: `src/deep_research/ui/app.py`
- Modify: `tests/test_ui/test_app.py`

**Main history layout:**

1. `Research sessions` heading + quiet total count;
2. one row with search input + compact status filter where width permits;
3. newest-first ordering (quiet fixed label or simple control; newest-first default is required);
4. row archive separated by hairline rules, not cards/dataframe;
5. each row: wrapped question (strongest), date/time, useful iteration/interruption context, explicit status, one `Open` action;
6. selected row gets pale teal tint plus a non-color selected cue.

**Status filter choices:**

- `All`
- `Running`
- `Completed`
- `Issues`

`Issues` includes `max_iterations`, `incomplete`, and `failed`.

**Status semantics:**

- Running: teal ring/dot + `Running` only for active in-memory session.
- Completed: green check + `Completed`.
- Max iterations: amber warning shape + `Max iterations`.
- Incomplete: neutral pause shape + `Incomplete`.
- Failed: red error shape + `Failed`.

- [ ] **Step 1: Write history AppTests.** Assert heading/count, search, status filter, newest-first rows, wrapped/full question text, explicit statuses, Open actions, and no `st.dataframe`.
- [ ] **Step 2: Write search tests.** Case-insensitive substring search over question; state preserved across reruns.
- [ ] **Step 3: Write filter tests.** All/Running/Completed/Issues semantics exactly as above.
- [ ] **Step 4: Write stale-running test.** Persisted running session without active controller record displays as Incomplete, never live.
- [ ] **Step 5: Write Open behavior tests.** Completed -> report view; failed/incomplete -> retained progress/stopping-point view with safe error and partial report when available; selection remains in same shell.
- [ ] **Step 6: Run RED.**

```bash
python -m pytest tests/test_ui/test_app.py -k "history or search or filter" -v
```

- [ ] **Step 7: Implement history rendering with native rows.** Use `st.text_input`, `st.segmented_control` if available/stable or `st.selectbox` otherwise, `st.container`, `st.columns`, `st.divider`, and session-specific button keys.
- [ ] **Step 8: Preserve filter/search/order in session state.** Do not add URL routing.
- [ ] **Step 9: Keep recent sidebar + full main archive synchronized.** Current/selected status must agree in both surfaces.
- [ ] **Step 10: Run tests/lint.**

```bash
python -m pytest tests/test_ui/test_app.py -k "history or search or filter" -v
python -m ruff check src/deep_research/ui tests/test_ui/test_app.py
```

- [ ] **Step 11: Run offline visual comparison for Figure 4.** Ensure the question leads each row, status and Open action are subordinate, archive is not a database grid/card wall, and selected tint is restrained.
- [ ] **Step 12: Commit.**

```bash
git add src/deep_research/ui/components.py src/deep_research/ui/app.py tests/test_ui/test_app.py
git commit -m "feat: add searchable streamlit session history"
```

**Task-review gate:** Luna Max compares Figure 4 and verifies search/filter/state semantics, row hierarchy, no data grid, stale-running normalization, same-shell navigation, and persistent sidebar continuity.

---

### Task 10: Add deterministic offline visual acceptance, README documentation, accessibility checks, and full verification

**Files:**
- Modify: `tests/test_ui/fakes.py`
- Create: `tests/test_ui/manual_mock_app.py`
- Modify: `tests/test_ui/test_app.py`
- Modify: `README.md`

**Acceptance target:** all four approved states can be exercised offline with deterministic data and visually compared with the embedded UX Pilot screenshots, while the automated suite verifies functionality/state/security without live providers.

- [ ] **Step 1: Implement `DemoController`.** Deterministic fake supports all four views and terminal variations. Include:
  - a New Research state with five recent sessions;
  - Running: five subtopics (one complete, one active, three queued), three meaningful recent activities, nonzero tokens in one variant, trace in one variant, no telemetry in another;
  - Completed: long Markdown report with multiple headings, bullets, citations/links, and a compact table; source credibility across all four tiers; all four fact verdicts; limitations; optional execution error variant; trace/tokens;
  - History: at least ten rows covering Running, Completed, Max iterations, Incomplete, Failed;
  - partial report variant;
  - configuration-error variant;
  - no network/sleep.
- [ ] **Step 2: Create manual mock app.** It must offer a development-only selector in the fake harness (not production UI) to force `New`, `Running`, `Completed`, `History`, `Max iterations`, and `Failed/partial` states for visual review.
- [ ] **Step 3: Add an end-to-end AppTest flow.** New -> Start -> Running -> Completed -> History -> reopen completed. Keep reruns bounded and deterministic.
- [ ] **Step 4: Add accessibility assertions where AppTest permits.** Visible labels, descriptive `Open LangSmith trace`, explicit status words, no color-only semantics, question/status text present, no hidden report tab.
- [ ] **Step 5: Add README `Streamlit UI` section.** Commands:

```bash
pip install -e .[dev]
streamlit run src/deep_research/ui/app.py
```

Offline visual harness:

```bash
streamlit run tests/test_ui/manual_mock_app.py
```

Document Markdown-only output, default iterations from config, two-second fragment refresh, local history location, no FastAPI requirement, telemetry omission semantics, privacy boundary, and live-provider smoke opt-in.
- [ ] **Step 6: Run UI suite.**

```bash
python -m pytest tests/test_ui -v
```

Expected: PASS with zero live calls.

- [ ] **Step 7: Run full repository tests.**

```bash
python -m pytest -v
```

Expected: PASS; live-marked tests remain excluded by repository defaults.

- [ ] **Step 8: Run Ruff.**

```bash
python -m ruff check .
```

Expected: PASS.

- [ ] **Step 9: Run manual offline visual acceptance.** Launch the mock app and review every state against the DOCX:
  - Figure 1: New Research;
  - Figure 2: Running;
  - Figures 3A-3C: Completed top/body/end;
  - Figure 4: History.

Capture reviewer evidence under `.superpowers/sdd/2026-09-08-streamlit-ui/evidence/` if the environment permits. Do not commit those screenshots unless explicitly requested.
- [ ] **Step 10: Apply the full handoff visual checklist.** Verify sidebar, rail stacking, typography hierarchy, 65-75 character report line length, 8px spacing rhythm, 6-8px radii, hairline borders, no routine shadow, semantic colors, three-activity cap, no false ETA/progress, report dominance, explicit credibility/fact labels, separate limitations/errors, history rows, no custom JS, and fragment refresh that does not reset focus/expanders.
- [ ] **Step 11: Verify reduced-motion/accessibility behavior.** No continuous decorative animation; visible focus remains; inputs have labels; long questions/paths wrap; report headings remain semantic; status words/icons accompany color; progress text remains meaningful when indeterminate.
- [ ] **Step 12: Do not run a paid provider smoke by default.** Production UI may be launched, but starting a real question requires separate human authorization at execution time.
- [ ] **Step 13: Commit.**

```bash
git add README.md tests/test_ui
git commit -m "test: verify editorial streamlit ui end to end"
```

**Task-review gate:** Luna Max performs the final **task-scoped** design/acceptance review against the entire handoff. This is not a whole-branch review. Reviewer must explicitly report every visual checklist item and all automated/manual verification evidence.

---

## Per-Screen Visual Acceptance Matrix

| Area | Fixed acceptance | Evidence |
|---|---|---|
| App shell | Narrow persistent sidebar; editorial main; optional quiet rail; no routed/dashboard shell | Figures 1-4 |
| New | Question is dominant; config compact; Markdown fixed; only Start is filled primary; quiet three-step explanation | Figure 1 |
| Running | Agent -> subtopic -> action; macro iteration secondary; truthful progress; health; sequence; exactly 3 activities; quiet rail | Figure 2 |
| Completed | Report dominates base surface; readable 720-820px column; compact credibility/fact summaries; secondary observability | Figures 3A-3C |
| History | Search + compact filter; row archive; question leads; explicit statuses; Open action; no dataframe/card grid | Figure 4 |
| Status | Text + icon/shape + semantic color | All figures |
| Telemetry | Tokens/trace omitted if unavailable | Running/Completed spec |
| Progressive disclosure | Tools/agent details/source/claim/session diagnostics one level deeper | Sections 5, 6, 11 |
| Accessibility | WCAG 2.2 AA intent; visible labels/focus; no color-only meaning; semantic headings | Section 14 |

---

## Acceptance-Criteria Traceability

| Requirement | Tasks | Verification |
|---|---|---|
| Start research from Streamlit | 4, 6 | Controller non-blocking tests + New AppTest + offline demo |
| Research question input | 6 | First-screen AppTest + Figure 1 review |
| Max iterations | 6 | AppTest/controller forwarding |
| Markdown output display | 6 | Read-only indicator AppTest |
| Current session status | 5-7 | Sidebar/current-state AppTests |
| Current agent | 2, 7 | Projector + Running AppTest |
| Macro iteration | 2, 7 | Projector + Running AppTest |
| Subtopic progress | 2, 7 | Projector + sequence AppTest |
| Three recent activities | 2, 7 | Projector cap + Running AppTest |
| Tool summaries | 2, 7 | Aggregation + disclosure test |
| Token usage when available | 2, 7-8 | None/nonzero tests + omission tests |
| LangSmith link when available | 7-8 | Conditional link AppTests |
| Final Markdown report | 4, 8 | Completed AppTest + Figure 3 review |
| Source credibility | 2, 8 | Tier tests + Completed AppTest |
| Fact-check summary | 2, 8 | Verdict tests + Completed AppTest |
| Report path | 4, 8 | Terminal snapshot + metadata display test |
| Errors and limitations | 2, 4, 8 | Safe failures + distinction tests |
| Session history local metadata | 3, 4, 5, 9 | Store/controller/history tests |
| Searchable/filterable history | 9 | Search/filter AppTests + Figure 4 review |
| Trace URL only; tracing stays engine-owned | 4, 7-8 | No observability changes; outcome URL only |
| Config errors safe | 4, 6 | Unit/AppTest |
| Failed sessions retain stopping point/partial report | 4, 7-9 | Failure/partial tests |
| Pure helper testing | 1-3 | Model/progress/history suites |
| Runner adapter mocked | 4 | Gated/failing runner tests |
| Offline visual acceptance | 10 | deterministic manual mock + Max visual review |
| No branch review yet | Execution contract | ledger + hard-stop section |

---

## Explicit Non-Goals / Rejected Scope

Do not add:

- React, Vue, Next.js, custom JavaScript, or a separate frontend build.
- Authentication, accounts, tenant isolation, collaboration.
- FastAPI as a required Streamlit backend.
- WebSockets/SSE/Redis/Celery/queues/database persistence.
- Report editing or WYSIWYG.
- PDF/HTML export.
- Provider configuration or secret input screens.
- Raw event/prompt/tool-result/provider inspectors.
- Agent avatars/personas.
- Charts for source credibility or fact-check summary.
- Dashboard KPI grids.
- Speculative ETA or unsupported overall completion percentage.
- Cancellation/resume controls unless a later spec adds them.
- Changes to LangSmith tracing behavior.
- Live-provider tests in the default suite.

---

## Post-Task Verification Record

After Task 10's scoped review is clean, the SDD ledger must record exact evidence:

```text
All implementation tasks complete: Tasks 1-10
Implementation model: GPT-5.6 Luna High per task
Task reviewer: GPT-5.6 Luna Max per task
Approved visual handoff available: YES
Offline UI tests: PASS or exact failure reason
Repository tests: PASS or exact failure reason
Ruff: PASS or exact failure reason
Offline manual visual review: PASS or exact deviations ledgered
Figures reviewed: 1, 2, 3A, 3B, 3C, 4
Live paid-provider smoke: NOT RUN unless separately authorized
Whole-branch review: HALTED by explicit human instruction
```

Never claim PASS for an item that did not run.

---

## Branch Review Gate — HALTED

The normal `superpowers:subagent-driven-development` flow would eventually dispatch a broad whole-branch reviewer. **Do not do that for this plan yet.** The human explicitly requested that branch review remain halted until they mention it.

After Task 10's scoped Luna Max review approves, stop in this exact state:

```text
IMPLEMENTATION: COMPLETE
TASK-SCOPED REVIEWS: COMPLETE
VISUAL HANDOFF REVIEW: COMPLETE (or exact deviations ledgered)
FULL OFFLINE VERIFICATION: COMPLETE (or exact exceptions ledgered)
WHOLE-BRANCH REVIEW: HALTED / NOT DISPATCHED
FINISHING-A-DEVELOPMENT-BRANCH: NOT INVOKED
MERGE/PUSH/PUBLISH: NOT PERFORMED
```

Only a later explicit human instruction such as `run the branch review` reopens the final-review phase.
