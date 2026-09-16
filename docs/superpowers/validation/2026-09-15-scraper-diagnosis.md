# Scraper-diagnosis canary — result record (plan Task 15)

Status: **RUN COMPLETED — TERMINAL QUALITY GATE FAILED.**

The run itself was executed and enforced correctly. The release criteria were
**not** met, and this record says so plainly rather than presenting a completed
run as a passing one. It is the result of the predeclaration at
`docs/superpowers/validation/2026-09-15-scraper-diagnosis-predeclaration.md`,
written and committed before the run started.

## 1. Run identity

| | |
| --- | --- |
| Candidate commit | `9d873607d54a2344b11aba32cd42b2fb7e8035c8` |
| Launched from HEAD | `3d7a4abc98b1e791de02ac50901f7a833d8698d2` (docs-only descendant, per the predeclaration's §3 rule) |
| Log | `output/cli-canary-20260915-183058-task15.log` (196,100 bytes) |
| Wall clock | ~26.4 minutes (Q1: 21.0) |
| Question | Q1's exact question, reused for comparability |
| Ceilings | DeepSeek 700, OpenAI 60, Tavily 450, `stop_fraction` 1.0 |
| Pre-spend probe | passed — `deep_research` resolved inside this worktree |
| CLI exit | **4** — the report was not accepted by the terminal quality gates |

**A measurement caveat that matters.** The background job reported exit code 0.
That is the *wrapper's* exit code — the job's last statement was `Write-Output`,
not the CLI. The CLI's own final log line is authoritative: *"the report was not
accepted by the terminal quality gates; `--require-quality` was set, so this run
exits 4."* A wrapper's exit code must not be read as the program's.

## 2. Budget actuals — the first transport measurement this project has ever had

Q1's record states the CLI "did not expose the DeepSeek or Tavily
transport-request counts, so those request counts are missing". That blind spot
is closed, and this is the measurement that never existed before:

| Provider | Attempts reserved | Declared ceiling | Effective limit | Utilisation |
| --- | ---: | ---: | ---: | ---: |
| DeepSeek | **251** | 700 | 700 | 36% |
| OpenAI | **0** | 60 | 60 | 0% |
| Tavily | **352** | 450 | 450 | **78%** |

- **No ceiling was reached and no attempt was refused.** The enforcement path
  was armed and never had to fire, which is the correct outcome for a bound.
- **Tavily at 78% is the finding to carry forward.** The predeclaration set the
  reporting threshold at "within 10% of a ceiling"; 78% is outside it, so this
  is recorded as an observation rather than a stop — but Tavily is the binding
  constraint, and a run with more refinement passes would reach 450.
- **OpenAI 0 of 60 confirms the pre-flight finding**, made before spending: the
  production CLI path is DeepSeek-only (`provider: deepseek`, judge
  `deepseek-v4-flash`), so that ceiling is declaratory. It is reported as 0
  rather than omitted.
- Tokens: **749,230** total (620,036 in / 129,194 out), against Q1's 703,427.
  Reported post-response, not a cost estimate.

## 3. Release outcome — FAILED, and two criteria regressed against Q1

| Criterion | Q1 | This run | Direction |
| --- | --- | --- | --- |
| Terminal quality | `partial` (critic 4/10) | **`partial` (critic 3/10)** | **worse** |
| Coverage | 4/6 (67%) | **3/6 (50%)** | **worse** |
| Duplicate claims | 0 | 0 | met |
| Duplicate source rows | 0 | 0 | met |
| Uncited settled points | 0 | 0 | met |
| Cited sources scored | 4 cited | 5 cited / 5 scored | met |
| Verified claims | 2 | 2 verified, 0 contradicted | met |
| Status | — | `max_iterations` | refinement budget exhausted before the critic accepted |

The CLI's own words: *"Research completed with limitations: the refinement
budget was exhausted before the critic accepted the report."*

**Correction to an over-claim I made when this run first reported: the leading
hypothesis is NOT falsified — it was not tested.** I initially wrote that this run
falsified the budget-starvation explanation because it had "more headroom on every
axis". That conflated **two different budgets**, and only one of them changed:

| Budget | Q1 | This run | Changed? |
| --- | --- | --- | --- |
| **Transport** attempts (`RequestBudget`) | not enforced, not observable | DeepSeek 251/700, Tavily 352/450 | **new** — and it never bound |
| **Per-ReAct-loop tool budget** (`agents.tool_budget`) | 10 | **10** | **unchanged** |

`config.yaml` is byte-identical to the base commit, so the per-loop tool budget
that Task 18 proposes to raise was **exactly the same in both runs**. The extra
headroom this run had was in the *transport* ceiling — a bound that was never
approached, and was never the proposed fix. **So Task 18's hypothesis is neither
confirmed nor refuted by this run; it remains live and untested.**

What the run *does* show is that **per-loop exhaustion is still happening**: the
finished log's summary carries many `agent_tool_budget_exhausted` warnings, several
reporting 1-4 provider-requested tool calls dropped at once, across the researcher,
fact-checker and critic. The provider keeps asking for more calls than one loop
allows. With `web_scraper` at only 17 attempts against 352 searches, dropped calls
are a plausible route by which reads never happen — which is precisely the
mechanism Task 18 targets.

The conclusion that survives is narrower and still useful: **the new transport
ceiling was not the constraint** (it never bound), and the run produced *worse*
coverage and a *worse* critic score than Q1 on identical agent configuration. The
untested variable remains the per-loop tool budget.

Tool activity: `web_search` 352 calls (8 failed); `web_scraper` **17 calls, 8
failed (47%)** — improved from Q1's 14 of 24 (58%), still high; `document_reader`
34; `query_memory` 83; `write_document` 2.

## 4. Task 15's deliverable — the diagnosis, the artifact gap that nearly blocked it, and the finding underneath

The plan asks for "the observed `web_scraper` failure classes with counts from
the run's agent records". **The counts exist; the classes did not — and they were
recovered from the run's trace instead, at no provider cost.** See §4.1.

At the candidate commit, `src/deep_research/agents/report.py::_run_errors`
rendered exactly five columns — `#`, `Type`, `Source`, `Severity`, `Message` —
and **never `details`**. The bounded classification Tasks 3 and 4 built
(`attempts`, `retries`, `status_code`, `content_type`) travels tool →
`ToolError.details` → `agent_tool_failed` details → `ResearchState.errors[].details`
→ `ReportComposition.errors[].details` and then **stops at the renderer**. The
run's evidence ledger carries **8 `web_scraper` failure rows, every one of them
the same generic sentence** — `web_scraper failed; the agent continued with an
observation.` A failure is countable; it is not *classifiable*, which is the
entire point of having classified it.

**That publishing gap is a real product defect**, independent of this task: Task 4
is titled "Project bounded scraper diagnostics into agent records", and if the only
public artifact drops them, that work is invisible to anyone who reads the ledger.

**It is fixed in `34fed3a`, which adds a bounded `Details` column** that publishes
details for `agent_tool_failed` only — the one type whose values are produced by the
ReAct projection, which revalidates every value it copies — and **withholds** every
other error type's details, because the ledger is public and an unvetted key could
carry text this project never publishes. Its test proves both halves: that the
scraper class appears, and that an unvetted type's details (including a hostile URL
inside them) do not. **The next live run will be the first diagnosable by class from
its own artifacts.** It is not validated by this run — the classes below came from
the trace.

### 4.1 The classes, recovered from the trace with zero additional spend

The run's own LangSmith trace (`deep-research-dev`, trace
`01a0a7d6-b43c-7982-92c5-2fc5f8f70d75`) records every tool span with its error.
Querying it — **no provider call, no spend** — yielded 1,133 distinct runs and the
complete failure taxonomy:

```
web_scraper: 9 succeeded, 8 failed  -> ALL 8 are ONE class:
    ToolExecutionError('the page request failed with an HTTP error status')
web_search:  8 failed               -> ALL 8 are ONE class:
    ForbiddenError("This request exceeds your plan's set usage limit. Please upgrade your plan")
```

### 4.2 The scraper failures are NOT a scraper defect

Every one of the 8 is an **HTTP error status returned by the target host** — a 404,
403 or 5xx from a real site. That is the plan's own *"failures are not a defect"*
branch: dead links, and hosts that refuse automated reads, are **legitimate
outcomes**, not bugs. Nothing in the evidence implicates transport timeouts, the
retry policy, or content-type classification.

**Task 16's decision table therefore selects its last row: no code change to
`web_scraper`.** The plan states that this is a complete result — "the task's
deliverable is that finding" — and it is the outcome the evidence supports. The 47%
failure rate is a property of the pages the Researcher chose to read, not of the
reader.

### 4.3 The finding the plan did not anticipate: the Tavily plan quota, not our ceiling

All 8 `web_search` failures are the **Tavily account's plan limit**:

> `ForbiddenError("This request exceeds your plan's set usage limit. Please upgrade your plan")`

This is an operational constraint on the entire canary programme that was invisible
until now for exactly the reason this amendment exists — nothing counted or reported
search attempts. The run issued **352 Tavily attempts against a declared ceiling of
450**, comfortably inside its own bound, while the account's quota ran out underneath
it. **The declared ceiling and the plan quota are different limits, and only one of
them was ever enforced or observed.** Every future canary must treat the quota as a
real constraint: a run that exhausts it silently loses search results and
under-reports its own coverage for reasons unrelated to pipeline quality.

### 4.4 The evidence funnel, and where the shortfall actually comes from

| Stage | Count |
| --- | ---: |
| `web_search` calls | 352 (8 lost to quota) |
| `web_scraper` attempts | 17 (8 lost to host HTTP status) |
| Sources scored | **5** |
| Cited sources | 5 |
| Verified claims | 2 |
| Topics covered | **3 of 6** |

Retrieval is emphatically **not** the bottleneck. The funnel narrows hardest at
**reading and scoring**, and the report's own headings say why: its findings are
*"Interconnection queue and transmission-level constraints"* and *"Supply chain and
critical minerals: **retrieved but unverified**"*, followed by sections titled *"Not
addressed by independent sources"* and *"Insufficient independent evidence"*. The
five sources it did score are strong (IEA 0.84, LBNL 0.80, RFF 0.74) — **the pipeline
finds quality material and then cannot convert it into enough independently
corroborated, citable sources to cover six topics.**

**Actionable conclusion.** Three things are live, in priority order:

1. **The Tavily plan quota** is an operational constraint that must be treated as a
   ceiling and checked before each run. It silently costs search capacity.
2. **Per-loop tool-budget exhaustion is still occurring** and is the untested
   variable. With only 17 reads against 352 searches and 1-4 provider-requested
   calls dropped per exhaustion event, dropped *reads* are a plausible mechanism for
   the coverage shortfall. This is exactly what Task 18 proposes to change, and the
   next run is the first that would actually test it.
3. **`web_scraper` needs no code change** — its 8 failures are host HTTP statuses,
   i.e. legitimate outcomes (§4.2).

## 5. A correction to my own mid-run reading

Part-way through, I grepped the live log for failure records and found **zero**,
and reported that as a promising signal with the caveat that the final summary
might still surface records the live stream did not. **The caveat was the correct
call and the signal was wrong.** The finished log's summary carries many
`agent_tool_budget_exhausted` warnings, several reporting 1-4 provider-requested
tool calls dropped at once. Budget exhaustion is **still occurring**, and it
occurs in every agent. A live-grep of a stream that does not carry a category is
not evidence that the category is empty.

## 6. What this run does and does not establish

**Establishes:** the request budget is enforced end-to-end in production and its
actuals are finally observable; three integrity criteria and the evidence
criteria pass; the production path is DeepSeek-only; the whole offline gate at
the candidate was green (3046 passed) before a cent was spent.

**Does not establish:** any passing release run. Coverage and critic both
regressed against Q1 despite more budget, so the next experiment must target
evidence *quality and sourcing* — most plausibly the `web_scraper` failure rate
and what the Researcher chooses to read — rather than the size of the budget.

**Still owed:** the failure-class diagnosis this task exists to produce, which
now requires one more live run at a head containing `34fed3a`.
