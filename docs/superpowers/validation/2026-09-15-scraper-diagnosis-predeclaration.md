# Scraper-diagnosis canary — predeclaration (plan Task 15)

Status: **DECLARED, NOT YET RUN.** This file is written and committed *before*
the run it authorizes. It is the record of what was decided in advance, so that
nothing below can be adjusted afterwards to suit what the run happened to do.

This canary is **evidence only**. It changes no production code. Its outcome
selects which branch Task 16 takes, and "the failures are not a defect" is an
explicitly valid outcome that reduces Task 16 to its finding.

## 1. Why this run exists

The spent Q1 run failed **14 of 24 `web_scraper` calls (58%)** and the cause is
unknown, because the failure reason was never populated before Task 3 shipped
its classifier and Task 4 projected the classified values into agent records.
The classification now exists (Tasks 3 and 4) and is exercised by tests, but it
has never been observed against live failures. This run buys that knowledge.

## 2. Question — reused verbatim for comparability

> What are the current constraints on grid-scale battery storage deployment?

This is Q1's exact question, reused deliberately. A different question would
make the failure distribution incomparable to the 14/24 baseline this run exists
to explain.

## 3. Candidate

- **Candidate commit: `9d873607d54a2344b11aba32cd42b2fb7e8035c8`** — frozen at the moment
  this file was committed, which is the commit the run must be started from.
- **Acceptance rule for HEAD, stated exactly because committing this file advances
  HEAD past the candidate by construction.** The run is refused unless EITHER
  `git rev-parse HEAD` equals the candidate SHA above, OR HEAD is a descendant
  of the candidate whose **entire** diff from it is this predeclaration document
  and its diagnosis record — i.e. a docs-only commit under
  `docs/superpowers/validation/`. The rule exists because a predeclaration
  cannot contain the SHA of the commit that creates it, and the honest fix is to
  state the exception rather than to back-date the file or drop the check. The
  code under test is byte-identical to the candidate either way, which is the
  property the check is for.
- The run is refused if `git status --short` shows anything modified or staged.
  The only tolerated entries are the known unrelated untracked paths
  `.deepseek-runs/` and `tools/`, which are never staged.

### 3.1 Gate result at the candidate (proven before spending)

| Gate | Result at `9d87360` |
| --- | --- |
| Offline suite (`pytest tests -q`) | **3046 passed, 1 deselected, 2 warnings** |
| `ruff check src tests` | All checks passed |
| `compileall -q src tests` | exit 0 |
| `git diff --check ab79f7a..HEAD` | exit 0 |
| Config assertions | `reasoning_effort: high`, `tool_budget: 10`, `timeout: 60.0` |
| `config.yaml` diff vs `ab79f7a` | empty (unchanged) |
| Controlled campaign (`--tier controlled --repetitions 3`) | **3/3 accepted**, coverage 1.00, judge 0.81-1.00, **network zero** |


## 4. Pre-spend environment probe (mandatory, no provider call)

An earlier attempt in this project loaded a *different worktree's* package, so
the probe is not optional:

```powershell
$env:PYTHONPATH = '<worktree>\src'
& '<shared-venv>\Scripts\python.exe' -c "import pathlib, deep_research; root=pathlib.Path.cwd().resolve(); module=pathlib.Path(deep_research.__file__).resolve(); assert module.is_relative_to(root / 'src'), (root, module)"
```

The probe must pass **before** the run, and it spends nothing.

## 5. Repetitions

**One.** The plan requires running it once and not rerunning after a failed gate.
A repetition would multiply spend without adding a new failure *class*, which is
what this run is for.

## 6. Declared ceilings — enforced by the Task 5/9 `RequestBudget`

These are supplied per-run through the CLI, never as ambient configuration. The
complete config baseline (`reasoning_effort: high`, `agents.tool_budget: 10`,
`llm.timeout: 60.0`) is unchanged and verified by
`git diff ab79f7a8b309f5ffd4905f64d71583d28c412912..HEAD -- config.yaml` being
empty.

| Provider | Declared ceiling | `stop_fraction` | Effective limit |
| --- | --- | --- | --- |
| DeepSeek | 700 | 1.0 | 700 |
| OpenAI | 60 | 1.0 | 60 |
| Tavily | 450 | 1.0 | 450 |

**How these numbers were chosen, and why they are enforcement values rather than
predictions.** Q1's validation record states plainly that the CLI never exposed
DeepSeek or Tavily transport-request counts, so Q1's true attempt counts are
unobservable and *cannot* be reconstructed from its tool-invocation totals. The
ceilings are therefore anchored on structure plus the hard actuals that do exist:

- **Tavily 450.** Q1 made **365** `web_search` tool invocations. Each invocation
  is at least one transport attempt (retries only add), so 365 is a firm lower
  bound on Q1's Tavily attempts. 450 bounds a retry storm at roughly 1.25× the
  observed volume without truncating a comparable run.
- **DeepSeek 700.** Every tool call inside a ReAct loop is preceded by a provider
  decision call, so ~365 tool invocations plus agent turns and the
  planner/synthesizer/critic calls put Q1's true figure in the 400-500 range.
  700 bounds a runaway at roughly 1.5× that estimate. Deliberately generous: a
  ceiling that truncates a legitimate run wastes the entire run's spend, and the
  failure mode this bound exists to prevent is a *runaway*, not a long run.
- **OpenAI 60.** The run's judging and quality evaluation are a handful of calls;
  60 is generous headroom that still bounds a pathological loop.
- **`stop_fraction` 1.0.** No early stop. A fraction below 1 exists to make a
  ceiling bite earlier, which is exactly wrong for an evidence run — the run must
  reach its natural end for its quality criteria to be evaluable.

**A ceiling justified by the number it later observed would be circular.** Q1 is
the cautionary case: its ceiling was derived as `tool_budget × loops`, which was
wrong because `tool_budget` is enforced *per ReAct loop*, and the run breached
its declared bound while nothing enforced it. That is the defect the
`RequestBudget` exists to remove.

### 6.1 The exact invocation (flag names verified against the shipped parser)

```powershell
& '<shared-venv>\Scripts\python.exe' '.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py' '..\..\.env' '<shared-venv>\Scripts\python.exe' -m deep_research "What are the current constraints on grid-scale battery storage deployment?" --config config.yaml --verbose --require-quality --request-deepseek-attempt-ceiling 700 --request-openai-attempt-ceiling 60 --request-tavily-attempt-ceiling 450 --request-stop-fraction 1.0
```

The four flags are the ones the shipped parser defines
(`cli.py:207,214,221,228`), and the ceilings travel as a **partial nested
`config_overrides["request_budget"]`** — never as a `config.yaml` edit, so the
verified config baseline remains byte-identical and any ceiling in force is
visible in the invocation itself.


## 7. Stop conditions — decided in advance

| # | Condition | Action |
| --- | --- | --- |
| S1 | CLI exits **3** (request attempt limit reached) | The ceiling bound the run. Record actuals, extract whatever evidence exists, and **do not rerun**. A ceiling that fires is a finding about the run's behaviour, not a reason to raise the ceiling. |
| S2 | CLI exits **4** (quality gate not met) | The run completed. Extract the scraper evidence as planned; the quality verdict is recorded but is not this canary's subject. |
| S3 | Pre-provider failure — credentials, tracing, environment, or the probe | No spend occurred. Fix and re-run; not counted as a canary attempt. |
| S4 | Wall clock exceeds **45 minutes** | Kill the run and record it as a failed canary. Q1 took 21.0 minutes; 45 is slightly over 2× that. |
| S5 | Any integrity gate reports non-zero | Record it. Evidence extraction for scraper failure classes still proceeds, because that evidence is independent of report integrity. |

## 8. What will be extracted

Every `web_scraper` failure class with its count, taken from the run's agent
records — **attributed across the whole run rather than from one pass**, since a
single pass sees only the calls that pass made. Each class is recorded with what
it implies, and the diagnosis must state explicitly **whether the failures are a
defect at all**: dead links returning 404, or robots denials on sources the
Researcher chose, are legitimate outcomes rather than bugs, and reporting that is
a valid result.

The run's token and transport-request actuals are recorded against the ceilings
in §6, including whether any ceiling was approached. **An actual landing within
10% of its ceiling is itself a finding** and must be reported as one, never
quietly absorbed by raising the ceiling.

## 9. Artifacts

- Run log: `output/cli-canary-<timestamp>-task15.log` (path recorded with its
  SHA-256, as Q1's record does).
- Run outputs: the production CLI's own output directory for the session.
- Diagnosis: `docs/superpowers/validation/<date>-scraper-diagnosis.md`.

## 10. Credentials

Supplied through the repository dotenv launcher by path. No credential value is
printed, logged, or recorded — by the launcher, by the CLI, or by this record.

## 11. Authorization

Within the standing authorization of ~10 live iteration runs and a US$100
currency ceiling. Q1 and its separate paid planner-failure attempt are already
spent and are counted; this run is the next one.

## 12. Pre-flight verification (performed before this run, spending nothing)

| Check | Result |
| --- | --- |
| Dotenv launcher `run_with_repo_env.py` | present |
| `.env` file, shared venv interpreter, worktree `output/` | present |
| `DEEPSEEK_API_KEY` | present, non-empty |
| `TAVILY_API_KEY` | present, non-empty |
| `LANGSMITH_API_KEY` | present, non-empty |
| Tracing | enabled in `config.yaml` (`langsmith.tracing_enabled: true`, project `deep-research-dev`) |
| Config baseline | `reasoning_effort: high`, `agents.tool_budget: 10`, `llm.timeout: 60.0`, `config.yaml` unchanged vs `ab79f7a` |
| Module resolution | `deep_research.__file__` asserted inside this worktree's `src` |
| CLI parses the declared ceilings | `--help` lists all four flags; parsing the exact §6.1 command produced `{'request_budget': {'deepseek_attempt_ceiling': 700, 'openai_attempt_ceiling': 60, 'tavily_attempt_ceiling': 450, 'stop_fraction': 1.0}}` |

**The last row is the one that matters most, and it was checked before spending
rather than trusted.** It proves the declared ceilings survive the whole path
from command line to the nested override the runtime consumes — the same class of
check that would have caught Q1's first attempt, which exited 2 on
`unrecognized arguments: --require-quality` and spent nothing only by luck.


**Credential presence was checked without reading or printing any value.**

**One substantive pre-flight finding.** The production CLI path is
**DeepSeek-only**: `config.yaml` runs `provider: deepseek`, and the quality judge
is `judge_model: deepseek-v4-flash` — there is no OpenAI provider on this path,
and `OPENAI_API_KEY` is not present in `.env` (nor in the process environment).
The OpenAI ceiling in §6 is therefore **declaratory**: it exists so that a
category which *should* report zero attempts is visibly reported as zero rather
than silently omitted, and so that a future run which does construct an OpenAI
provider inherits a bound instead of none. The binding ceilings for this run are
**DeepSeek (700)** and **Tavily (450)**. This was verified before spending rather
than discovered from a failure, because a missing key on a path the run does
touch would have burned a canary attempt on an environment fault — the same
class of failure as Q1's first attempt, which exited 2 with `unrecognized
arguments` and spent nothing only by luck.

