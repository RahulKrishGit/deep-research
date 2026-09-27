# Audit of a live report under rubric v1, with root-cause attribution (judge, read-only)

Plan tree `<repository root>`.

Constraints:
- Read-only: never edit, commit, or re-run the pipeline.
- Never read `.env`.
- Python `<venv python>`, `PYTHONPATH='src;.'`. You may render prompts, replay code paths, or fetch the run's LangSmith trace the way `scratch/ev1_trace_fetch.py <trace-id>` does. The trace id is in the run's `cli.log` "Trace:" line.

Your task message gives:
- the question;
- the session id;
- the head;
- the report path (`output/report-<session>-<n>.md`);
- the evidence log (`…-evidence.md`);
- the quality JSON (`…-quality.json`);
- the launch directory (`output/live-proof/<label>/` with `cli.log` and `run.env`).

Before reading any code, form your reading of the report against its cited pages and the question.

## Part 1 — score and verdict (rubric v1, unchanged)

Apply `.superpowers/sdd/2026-09-24-evidence-verifier-pipeline/scoring-rubric-v1.md` exactly, with every honest-pass checklist item (§5).

**Defects:**
- Class each defect cosmetic, minor or material, by its effect on the reader.
- Charge each defect once, to one item (A1–A7).
- On a borderline, choose the stricter class.

**Score:**
- Give item scores by the §3 formula.
- Compute the raw score, apply the §4 caps, and state the overall score and band.
- Show the arithmetic.

**Required lists:**
- the untraceable-claims list, even when empty;
- the independent research you did (§5.2): the searches and the pages you read that the run did not read.

**Gap section:** the gap section is whatever the report's layout uses to admit what it could not find. In the consumer format that is "What we couldn't confirm".

**Last line:** the §6.1 verdict line, `VERDICT: RELEASE | DO NOT RELEASE — rubric v1 — score …`.

## Part 2 — root-cause attribution (operator requirement)

For every defect, record the following fields.

**Class** — exactly one of:
- `PROMPT`: a model-read text instructed the wrong thing, omitted the rule, or contradicted another prompt or the code. Name the block (planner, researcher, extraction, evidence verifier, writer section or bottom line, reviewer) and quote the phrase or name the omission.
- `MODEL COMPLIANCE`: the rule is right and the model did not follow it. Quote the rule and the output that broke it.
- `CODE`: deterministic logic produced the defect. Name the `file:function` and the input that triggers it.
- `DATA / WEB`: the web did not carry the fact and the pipeline handled that correctly. Say what the report should have disclosed.

**Evidence:**
- the trace, the state, the quality JSON, the evidence log, the prompt text, or a code probe;
- mark [INFERENCE] wherever you could not prove the point.

**Fix** — the smallest general fix, valid for any question and any domain:
- for PROMPT, the direction for the wording;
- for CODE, the rule to change;
- for MODEL COMPLIANCE, whether to use a clearer rule, an example or a code guard, and why.

**Owner** — the file that must change.

## Output

One markdown document containing, in order:
1. The defect table: id, report line, page text or its absence, class, item.
2. The item scores, raw score, caps, overall score, band and honest-pass ticks.
3. The attribution table.
4. The ranked changes that would most raise this and any future report, split into PROMPT and CODE.
5. The verdict line, which is the document's last line.
