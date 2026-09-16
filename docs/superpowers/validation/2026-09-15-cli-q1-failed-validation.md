# CLI live canary Q1 failed-validation record

Status: FAILED — Q1 SPENT — Q2/Q3 BLOCKED

This record preserves the spent Q1 validation at candidate
`7ef89ef057810464857413ea9078ff06b73814d9`. It is evidence of a failed release
gate, not authorization for another provider call. All paths below are relative
to this worktree unless stated otherwise:

`C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity`

## Question and invocation

Q1 question (exact):

> What are the current constraints on grid-scale battery storage deployment?

The production CLI was run from the worktree with the shared virtual environment
and the repository dotenv launcher. The launcher reads the dotenv file by path;
no credential value was printed or recorded. The absolute shared-venv/launcher
invocation was:

```powershell
& 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe' 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py' 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.env' 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe' -m deep_research "What are the current constraints on grid-scale battery storage deployment?" --config config.yaml --verbose --require-quality
```

The same command as recorded from the worktree used the launcher-relative
launcher and dotenv arguments (the shared interpreter remained absolute):

```powershell
& 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe' '.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py' '..\..\.env' 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe' -m deep_research "What are the current constraints on grid-scale battery storage deployment?" --config config.yaml --verbose --require-quality
```

The absolute form above identifies the actual shared interpreter, launcher, and
dotenv locations without including any secret. The pre-spend
worktree probe set `PYTHONPATH` to this worktree's `src` and checked:

```powershell
$env:PYTHONPATH = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\src'
& 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe' -c "import pathlib, deep_research; root=pathlib.Path.cwd().resolve(); module=pathlib.Path(deep_research.__file__).resolve(); assert module.is_relative_to(root / 'src'), (root, module)"
```

Before spending, the run also required `git rev-parse HEAD` to equal the frozen
candidate, and allowed only the known unrelated untracked `.deepseek-runs/` and
`tools/` paths. The probe confirmed that `deep_research` resolved inside this
worktree. These gates addressed an earlier invocation that loaded another
worktree's package.

## Distinct run events

1. **No-spend wrong-worktree abort.** A background executor resolved bare
   `python` to a Codex runtime interpreter whose editable package pointed at
   `.worktrees/streamlit-ui`. Its argparse invocation rejected
   `--require-quality` as an unknown argument and exited 2 before any provider
   call. No tokens or network requests were spent. This was a controller and
   environment failure, not a Q1 validation result.

2. **Paid planner failure at `1839907`.** The corrected absolute-interpreter
   invocation ran Q1 with the then-forced process overrides and spent 22,593
   provider-reported tokens. The planner failed after its bounded repair path;
   the CLI exited 3 with `graph.planner: [graph_planning_failed]`, no report or
   evidence artifact, and no search call. This is the planner-failure log listed
   below. It is a separate paid attempt from the completed pipeline run.

3. **Completed pipeline at `7ef89ef`.** The finalized configuration ran the
   whole production pipeline, publishing both artifacts. It completed the
   pipeline but failed the release-quality gate: terminal quality was `partial`,
   so `--require-quality` exited 4. Q1 was therefore spent and failed.

The earlier planner failure was attributed to the controller's forced
`LLM_REASONING_EFFORT=max` override; the completed run used the finalized
configuration with no process setting override. Q2 and Q3 were not started.

## Completed-run result

| Measure | Observed result |
| --- | --- |
| Candidate | `7ef89ef057810464857413ea9078ff06b73814d9` |
| Terminal quality | `partial` |
| Critic score | `4/10` |
| Topic coverage | `4/6` (67%) |
| CLI exit | `4` (`--require-quality`) |
| Duplicate claim IDs | `0` |
| Duplicate source rows | `0` |
| Unresolved/uncited settled points | `0` |
| Cited sources / scored cited sources | `4 / 4` |
| Verified claims / contradicted claims | `2 / 0` |
| Whole-report judge | Not run; no judge step was reached |

The run disclosed three high-priority sub-topics without findings and skipped
coverage `topic-06`. The evidence was thin rather than presented as a passing
release: four cited sources, two verified claims, and zero contradicted claims.

## Artifact index

All hashes are SHA-256. The reader and evidence paths are separate artifacts.

| Artifact | Path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Reader report | `output/report-50cbf5926e8b479ea90793312396e440-3.md` | 10,724 | `0867c065c29d369115deabe306a329eb924ecfa14cbf4a894d54ef2ff545be01` |
| Evidence ledger | `output/report-50cbf5926e8b479ea90793312396e440-3-evidence.md` | 24,119 | `0812c278f0f4a0045a023d4e89abbf3402cc297cdee1303efb9a53dda062b6ef` |
| Completed Q1 CLI log | `output/cli-canary-20260915-204454-q1.log` | 28,558 | `da07679972e1e3e54174e12542002483e66d486a8e49539c67a3452210a27be7` |
| Planner-failure log | `output/cli-canary-20260915-202152-q1.log` | 2,346 | `3ea7a9e361fb1344e4493190dc655ccb57ce84abeaddc625a527d97b3380e1d1` |

## Counting units and budget evidence

The following are tool invocations, not provider HTTP transport requests:

- `web_search`: 365 invocations.
- `web_scraper`: 24 invocations, 14 failures.

The persisted evidence ledger contains `agent_tool_budget_exhausted` rows by
agent: researcher 46, fact-checker 18, critic 6, for a total of 70 rows. The
completed CLI log contains 72 string occurrences of
`agent_tool_budget_exhausted`. These are different counting units. The earlier
report's `51/34/8` claim is unsupported; this record does not repeat it as an
event count.

The completed run reported 703,427 tokens (569,718 input and 133,709 output).
The planner-failure run reported 22,593 tokens (10,892 input and 11,701
output). These are post-response provider-reported usage totals, not proof of
dollar cost, and they are not pre-request ceilings. The CLI did not expose the
DeepSeek or Tavily transport-request counts, so those request counts are
missing; tool-invocation totals cannot establish the transport ceilings.

## Disposition and release decision

The controller previously exposed a Tavily credential in a session transcript.
That credential was disclosed as an incident and was rotated/updated by the
user before this record was prepared. The rotated credential value, all other
credential values, environment values, raw provider responses, raw prompts,
URLs, and trace payloads are intentionally absent here.

Other controller errors were the configuration override that caused the paid
planner failure, incorrect/unmonitorable request-ceiling arithmetic, and the
initial wrong-worktree invocation. They are recorded only as safe categorical
diagnostics above.

Release status:

- **Q1: SPENT / FAILED** — `partial`, critic `4/10`, coverage `4/6`, exit 4.
- **Q2: BLOCKED** — not run after the failed Q1 gate.
- **Q3: BLOCKED** — not run after the failed Q1 gate.
- **Whole-report judge: NOT RUN.**

No further live provider, search, judge, or LangSmith call is authorized by this
record.
