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

**This falsifies the simplest hypothesis for Q1's shortfall.** The leading
explanation had been that Q1 failed because it ran out of budget: the researcher
hit its per-loop tool budget repeatedly, so subtopics were cut off mid-gathering.
This run had *more* headroom on every axis — 251 of 700 DeepSeek attempts, 352
of 450 Tavily, three refinement passes, 749k tokens against 703k — and it covered
**fewer** topics and scored **worse** with the critic. More searching and more
passes did not buy coverage. Whatever limits coverage here, **budget quantity is
not it**, and Task 18's "raise the tool budget" fix is therefore aimed at
something this evidence does not support as the primary cause.

Tool activity: `web_search` 352 calls (8 failed); `web_scraper` **17 calls, 8
failed (47%)** — improved from Q1's 14 of 24 (58%), still high; `document_reader`
34; `query_memory` 83; `write_document` 2.

## 4. Task 15's actual deliverable — the diagnosis is BLOCKED, and that is the finding

The plan asks for "the observed `web_scraper` failure classes with counts from
the run's agent records". **The counts exist; the classes do not.**

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

**The defect this task was meant to diagnose therefore cannot be diagnosed from
this run's artifacts, and that publishing gap is itself the finding.** This is
not a process complaint: Task 4 is titled "Project bounded scraper diagnostics
into agent records", and if the only public artifact drops them, that work is
invisible in the product.

**Fixed after the run, and explicitly not validated by it.** Commit `34fed3a`
adds a bounded `Details` column that publishes details for `agent_tool_failed`
only — the one type whose values are produced by the ReAct projection, which
revalidates every value it copies — and **withholds** every other error type's
details, because the ledger is public and an unvetted key could carry text this
project never publishes. It carries a test proving the scraper class appears and
that an unvetted type's details (and a hostile URL inside them) do not. **The
next live run will be the first that can actually be diagnosed by class.**

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
