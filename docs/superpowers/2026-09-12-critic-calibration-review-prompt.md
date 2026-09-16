# Review Prompt — Critic Calibration Decisions

Paste the block below into the reviewing session. If that session has no access to
this repository, attach
`docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md` with it;
that document is self-contained and carries the full evidence.

---

You are reviewing a calibration decision in the `deep-research` repository (a
Python multi-agent research system with an LLM-as-a-judge evaluation harness).
Your job is to produce a **recommendation**, not a summary and not an endorsement.

## Where to look

- Branch: `codex/cross-agent-planner-fix-parity`. The work under review is the
  commit range `479065d..HEAD`. **Read the tip and the commit count from the
  branch**, not from these documents — an earlier version of this prompt stated a
  fixed tip and a commit count, and was stale within one commit.
- **Read first:**
  `docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md`.
  It is self-contained: context, every measurement, both decisions, the options,
  and an inventory of code locations.
- Then the underlying record:
  `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`
  sections 83–88.
- **Verify against the code rather than trusting the brief.** It cites file paths
  and line numbers; check a sample of them. Relevant files:
  `src/deep_research/agents/prompts.py` (`CRITIQUE_INSTRUCTION` at `:207`),
  `src/deep_research/agents/critic.py` (the score bands at `:137`),
  `src/deep_research/evaluation/cases/critic.py` (fixture at `:810`, the
  expectation at `:1012`), `evaluators.py` (`_no_spurious_gaps_passes` at
  `:2109`), `models.py` (`JudgeVerdict`), `judging.py` (the judge prompt).
- Live artifacts: `output/evaluations/live-critic-canary/critic/*/results.json`
  (three canary repetitions). `output/` is gitignored — if you cannot see it, say
  so explicitly and work from the brief instead of guessing.

## What is already settled — do not re-open it

The Critic's live failure was: a structured review request that offered no tools,
paired with a system prompt instructing the model to use tools, which made it emit
DeepSeek tool-call markup as plain text, failing local JSON validation and leaving
the judge with nothing to score. That is fixed and verified across **3 live canary
repetitions and 33 consecutive clean review calls**. Do not propose re-litigating
the root cause, the tool-free review prompt, or the report fence.

## What I need from you

Two decisions, both stated fully in the brief:

1. **The live Critic case's reference expectation** — `minimum_score: 7` and
   `expected_route: "end"`, written at the case's creation and predating the score
   band table the Critic now applies. Is it stale, or still correct? Note that
   nothing functional reads it; only the LLM judge sees it as context.
2. **The `unsupported_claims` definition** in `CRITIQUE_INSTRUCTION` —
   *"statements the report makes that no cited source or verified claim backs"*.
   Is the intended reading **strict** (a statement needs independent verification)
   or **lenient** (an inline attribution to a cited source counts)? The Critic
   currently reads it strictly. This changes behaviour on **every** run, not just
   the fixture.

For each decision, deliver:

- **The recommendation, with the option chosen** and the reasoning that actually
  decides it — not a survey of trade-offs.
- **What evidence would change your mind.** Name the measurement that would
  falsify your pick.
- **The concrete change required**: the file, roughly what changes, and what it
  invalidates. Any Critic prompt change moves `target_prompt_fingerprint`, which
  is recorded on artifacts but **not pinned by a test** (brief §8.2); any judge
  change moves the judge fingerprint, which is pinned. Either requires a fresh
  3-repetition canary — about 20 model calls, and paid runs in this project are
  authorised individually with a stated request count.
- **Anything in the brief you disagree with, or any claim the evidence does not
  support.** Section 9 lists the author's own known weaknesses; add to that list
  rather than repeating it.

## Stance

Be adversarial about the **evidence**, not about the author. The brief contains
the author's own lean (§7) — treat it as an input, not a steer, and say plainly if
you think it is wrong. Two earlier steps in this campaign were reversed by
measurement after reasoning had predicted the opposite, so do not accept an
argument because it sounds coherent. Where you are uncertain, name the specific
measurement that would resolve it.

Do not widen the scope: no redesign, no change of model or transport, and no
relaxing the acceptance threshold to make runs pass.
