# Individual Agent Evaluation — Live Verification Runbook

Everything in this document is **manual and opt-in**. None of it runs in
pytest. Every step below costs real money and makes real network calls to
OpenAI and LangSmith (and Tavily, for live cases that need search). Do not
run any of these commands as part of an automated CI job or an unattended
script — a human runs each step, reads the output, and checks the boxes.

Prerequisites before starting: `OPENAI_API_KEY`, `LANGSMITH_API_KEY`,
`LANGSMITH_PROJECT` set in the environment (or a `.env` file next to
`config.yaml`); `TAVILY_API_KEY` set if step 3 or step 4 exercises an agent
that declares `web_search`; the LangSmith UI open in a browser under the
correct project/workspace.

## Step 4 pricing findings (recorded here, not run)

Looked up via `WebFetch` on 2026-08-17 against the live OpenAI docs pages:

- `https://developers.openai.com/api/docs/models/gpt-5.6-luna`
- `https://developers.openai.com/api/docs/models/text-embedding-3-small`

**GPT-5.6 Luna** — **confirmed, no change needed**:

- Input: **$0.20** / million tokens (spec said $0.20 — confirmed)
- Cached input: **$0.02** / million tokens (spec said $0.02 — confirmed)
- Output: **$1.20** / million tokens (spec said $1.20 — confirmed)
- `gpt-5.6-luna` is currently a **bare alias**, not a dated snapshot — the
  docs page lists only `gpt-5.6-luna` as the available identifier, with no
  versioned id (e.g. `gpt-5.6-luna-2026-XX-XX`) superseding it. **No
  config or evaluation-version change is required or was made.** If a
  dated snapshot appears later, re-run this recheck and follow the spec's
  requirement for an explicit configuration and evaluation-version change
  before adopting it — do not adopt it silently.
- Additional facts observed (informational only, not part of the spec's
  recorded figures): context window 1,050,000 tokens; knowledge cutoff
  "Feb 16, 2026"; supports image input; max output 128,000 tokens; supports
  reasoning effort levels `none, low, medium, high, xhigh, max`; requests
  over 272,000 input tokens are billed at 2x input / 1.5x output for the
  whole request; cache writes bill at 1.25x the standard input rate.

**`text-embedding-3-small`** — **confirmed, no change needed**:

- Input: **$0.02** / million tokens (spec said $0.02 — confirmed)

No pricing correction is required in `config.yaml` or anywhere else. Both
figures the spec recorded as informational match the live docs exactly as of
this check.

## Fixed verification order

Run these in order. Do not skip ahead to a later step until the earlier
step's confirmation checklist is fully satisfied — later steps assume the
earlier ones are sound.

### 1. One controlled case for one agent

```powershell
python -m deep_research.evaluation agent planner `
  --case focused-decomposition --verbose
```

Confirm:

- [ ] Three target runs and three judge evaluations occurred.
- [ ] The dataset `deep-research-planner-controlled-v1` is visible under
      **Datasets & Experiments** in the LangSmith UI.
- [ ] The experiment name matches
      `planner-controlled-<UTC timestamp>-<short SHA>`.
- [ ] `results.json` exists at
      `output/evaluations/planner/<experiment>/results.json` and revalidates
      (re-parses as the strict result schema without error).

### 2. The selected agent's full controlled dataset

```powershell
python -m deep_research.evaluation agent planner
```

Confirm:

- [ ] Nine target runs occurred (three cases x three repetitions).
- [ ] Nine judge evaluations occurred.
- [ ] A `Review:` block links the lowest-scoring trace for each of the three
      cases.

### 3. One live case for that agent

```powershell
python -m deep_research.evaluation agent planner --tier live
```

Confirm:

- [ ] One target run and one judge evaluation occurred.
- [ ] The evaluation Chroma collection and document directory used are the
      evaluation-only ones (not the production collection/directory).
- [ ] Nothing was written under the production `memory/` or `output/`
      research paths — only under the evaluation output root.

### 4. The six-agent controlled suite

Only run this after steps 1-3 are sound for at least the agent already
exercised above (ideally after spot-checking each agent individually).

```powershell
python -m deep_research.evaluation suite
```

Confirm:

- [ ] All six agents ran their controlled experiments.
- [ ] `output/evaluations/suite/<suite-id>/summary.json` was written and
      revalidates.
- [ ] Each agent's individual `results.json` was also written, exactly as
      in step 2.

## Four unknowns to resolve during step 1, in the LangSmith UI

These are open questions this plan could not resolve offline. Resolve them
while performing verification step 1 above, and record the actual answer in
this section (do not leave placeholders — replace each bullet's answer once
observed).

### Judge Source and Evaluator trace

Open the experiment from step 1, click a `judge_quality` score, and confirm:

- [ ] A **Source** link is present, exposing the versioned judge prompt
      template, rubric, output schema, and evaluator definition.
- [ ] An **Evaluator trace** link is present, exposing the sanitized
      formatted judge input, the model invocation, the structured score, and
      the rationale.
- [ ] Neither view contains a known secret or hidden chain-of-thought.
- [ ] Latency and token usage appear when the provider reports them.
- [ ] If the **Source** link is absent for a `traceable`-wrapped callable
      evaluator, apply the Task 20 fallback: confirm the prompt template,
      rubric, and schema are also published in the experiment metadata under
      `judge_prompt_source`.

**Answer (fill in after observing):** _not yet resolved — pending manual run
of verification step 1._

### Trace nesting

Confirm whether the experiment example nests `research.session` beneath it
or creates a sibling root.

- [ ] If it is a sibling, confirm the correlation metadata from Task 21
      (`experiment_name`, `case_id`, `case_version`, `repetition`,
      `session_id`) lets you navigate between the two traces.
- [ ] Record which shape LangSmith actually produced (nested vs. sibling).

**Answer (fill in after observing):** _not yet resolved — pending manual run
of verification step 1._

### `temperature` on a reasoning model

- [ ] Confirm whether `gpt-5.6-luna` rejects `temperature: 0.0`.
- [ ] If it does reject it, set `evaluation.judge_temperature: null` in
      `config.yaml` (the Task 2 change makes `None` omit the parameter) and
      record that change here, including the exact rejection error observed.

**Answer (fill in after observing):** _not yet resolved — pending manual run
of verification step 1. No change has been made to `config.yaml`'s
`judge_temperature` value as part of this documentation task._

### The `none` reasoning effort

The spec's typed configuration accepts `none`, but OpenAI's accepted value
for that level has varied across models.

- [ ] Send one throwaway request at `--reasoning-effort none` and record
      whether the API accepts it or rejects it (and with what error, if
      rejected).
- [ ] Do not change the accepted-values list in code without a spec
      amendment, regardless of the outcome.

**Answer (fill in after observing):** _not yet resolved — pending manual run
of verification step 1. No change has been made to the accepted reasoning
effort values as part of this documentation task._

## Recording results

This file is the evidence that the four unknowns were resolved rather than
assumed. When a human runs the steps above, they should edit the four
"Answer" placeholders in place with what was actually observed, plus a date
and the experiment name/URL used as evidence, rather than opening a separate
document.
