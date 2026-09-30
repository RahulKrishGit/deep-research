# Claude Design brief: live step briefs and plan review for the Deep Research console

Paste this file into Claude Design as your first message, and attach everything else in this folder. If Claude Design can read a GitHub repository, also give it the public repo https://github.com/RahulKrishGit/deep-research. The app is in `web/`; the design package is in `docs/design/`.

## What this is

Deep Research is a web console. You ask a research question, and an agent pipeline researches it in 7 steps:

1. Planning
2. Researching
3. Evaluating sources
4. Verifying evidence
5. Writing report
6. Reviewing
7. Publishing

The console already exists. Its look is fixed and approved:
- `theme/globals.css` is the real stylesheet. Its tokens and classes are copied verbatim from the approved prototype.
- `prototype/index.html` is the full working prototype. Open it in a browser.
- `reference/*.png` shows the approved screens.
- `current/app-*.png` shows the shipped app today.

I want two new things designed for page 2 (the running stage, `reference/03-running.png`). They must look as if they were always part of this console.

## Deliverable

One **standalone HTML prototype** that:
- extends the running stage of `prototype/index.html`;
- links or inlines `theme/globals.css`, adding only token-based rules of its own;
- **plays the full sequence below** on a timer, with Play, Pause and Replay controls and a scrubber;
- shows every state at **1252 px desktop** and **390 px phone**;
- honours `prefers-reduced-motion: reduce`: movement becomes fades, and the timing stays the same.

Then export it as a Claude Code handoff and as standalone HTML.

## Feature 1: the step in progress expands with a live brief

The pipeline is a vertical spine: seven rows, each with a number node, a name and a one-line description (see `reference/03-running.png`). The layout is decided: **inline expansion.**
- The active row itself grows taller to hold a short live brief.
- The connector line continues past it.
- A finished row collapses to a one-line outcome and can be clicked to re-open.
- Pending rows stay as they are today.

`current/step-briefs-options.png` shows the three layouts that were considered; **Option A** was chosen.

**The open question is the motion.** The human said: *"inline expansion is good but I want the animation to look better."* Design motion that feels calm, premium and alive, never busy. It should:
- open and close rows smoothly (height, then content);
- bring brief lines in with a small stagger;
- tick sub-topics off with a drawn check;
- tick counts up smoothly;
- hand off from a finishing step to the next one (the connector filling down, then the next row opening);
- show a gentle "working" signal while a step runs, reusing the existing `halo` pulse on the active node and the `loopflow` dash, or something equally quiet.

No bounce, no confetti, no new colours.

**Tone of the brief.** Consumer level: plain words, counts and topic titles. No tool names, no IDs, no jargon. Scores appear only where a reader understands them, such as the review score. The wording below is proposed and can be refined.

| Step | While running | When done (one line) |
|---|---|---|
| Planning | "Breaking your question into sub-topics…", then the topic titles appear one by one | "5 sub-topics · plan approved" |
| Researching | "Researching topic 3 of 5: Permitting and siting rules" · "42 pages read · 18 useful findings" · a topic checklist: ✓ done (with its finding count), ● current, ○ not started | "5 topics · 90 pages read · 639 findings" |
| Evaluating sources | "Rating 83 sources for trustworthiness and relevance" | "83 sources rated" |
| Verifying evidence | "Checking 639 findings against their pages" · "134 verified · 39 corrected so far" | "134 verified · 39 corrected · 1 dropped" |
| Writing report | "Writing the report from verified findings only" | "Report drafted · 89 sources cited" |
| Reviewing | "Reviewing the draft on 7 dimensions" | "Accepted · 0.83" |
| Publishing | "Saving the report and evidence log" | "Published" |

The example question is "What are the current constraints on grid-scale battery storage deployment?". Its five sub-topics:
1. Grid interconnection queues
2. Supply chain for battery cells and minerals
3. Permitting and siting rules
4. Costs and financing
5. Fire-safety standards

## Feature 2: plan review (human in the loop after Planning)

When Planning finishes, the run **pauses** so the person can approve or edit the plan in place.

**Placement is decided: inline under Planning.** The review opens inside the pipeline card, directly under the Planning row, as Planning's expanded content. The spine's connector runs down its left edge into row 2. Rows 2–7 stay below in their normal pending style.

**Content and behaviour (decided):**
- **Topbar chip:** "Waiting for you · plan review", using the existing `.chip` with the amber `.dot-warn`. Amber is used because this is a status.
- **Planning row:** done, with the outcome "5 sub-topics · waiting for your review".
- **Header:** an eyebrow reading "PLAN REVIEW", the title "Review the plan", and the helper line "Research starts with this plan. Edit topics or questions, or approve as is."
- **The five topics.** Each has:
  - an **editable title**;
  - its 2–3 questions as **editable lines**, each with a **required | optional** toggle and a remove control;
  - "+ Add a question";
  - a remove-topic control.

  After the last topic comes "+ Add a topic". A topic that has been edited shows a quiet "edited" marker.
- **Countdown:** a **3-minute timer**, shown as "continues with this plan in 2:41" with a thin draining track. While the person is editing, it reads "paused while you edit" and stops. At zero, the run continues with the plan as it stands.
- **Buttons:**
  - **Approve and continue** (primary, purple);
  - **Save changes & continue** (ghost). It is enabled only once there are edits, and it continues straight away with no second review.
- **After approval,** the review collapses into Planning's one-line outcome and Researching expands (Feature 1's hand-off).
- **Settings:** a toggle in the composer's settings popover, "Review plan before research", **on by default**. When it's off, there's no pause. See the popover in `reference/01-idle.png` and in the prototype.

**What went wrong last time.** `current/plan-review-v2-off-theme.png` is a previous attempt, and it was rejected as off-theme. Specifically:
- cards nested inside cards;
- orange × icons and an orange "edited" border;
- purple "+ Add" links;
- invented white-bordered required/optional pills;
- underlined text inputs;
- a bright orange progress bar.

Don't repeat any of these.

## Theme rules (non-negotiable)

1. **Tokens only.** Use `var(--…)` from `theme/globals.css`, with no colour literals. Reuse the existing classes before writing new ones:
   - `.card`, `.card-title`, `.eyebrow`, `.sm`, `.cap`, `.avail-mono`;
   - `.chip` with `.dot-*`;
   - `.btn` with `-primary` / `-ghost` / `-quiet` / `-sm`;
   - `.seg` (the popover's segmented control, which suits required | optional);
   - `.icon-btn`, `.tag.soft`;
   - the spine classes.
2. **Colour means status.** Green (`--status-ok`) means active or OK, amber means waiting or warning, red means danger. Purple (`--accent`) appears only on the one primary button. Everything else is `--fg` / `--muted` / `--border` greys.
3. **One surface per region.** Inside the pipeline card, separate content with hairlines (`--border-soft`), like the sidebar rows and pipeline rows. Never nest boxes.
4. **Text entry looks like text.** Editable titles and questions read as plain text in the console's type. The edit affordance appears only on hover (a `--border-soft` background) or on focus (the standard focus ring), like the composer textarea: borderless and transparent.
5. **Type.** Row names follow the pipeline's row-name style, questions use body text, and numbers and small facts use the mono `.cap` / `.avail-mono` styles.
6. **Motion.** Use the existing motion tokens (`--motion-fast`, `--motion-base`, `--motion-lift`, `--ease-standard`, `--ease-entrance`, `--motion-halo`). Animate only transform, opacity and the `grid-template-rows: 0fr → 1fr` height technique.
7. **Governing rule from the design.** A value that isn't known yet shows as muted words ("not yet"), never `0`, `—` or `null`.

`DESIGN.md` is the full design rationale. Its sections on the running stage, the spine, motion ("The two handoffs") and the governing rule are the most relevant.

## Sequence the prototype should play (about 30 s, loopable)

1. **Planning active.** The brief types in, the five topics appear one by one, and the node pulses.
2. **Planning done, review opens** under it (Feature 2). The chip turns amber and the countdown drains.
3. **Editing.** The person edits topic 1's title and marks one question optional. The timer shows "paused while you edit", and "edited" appears.
4. **Save changes & continue.** The review folds into Planning's outcome line, the connector fills down, and Researching expands.
5. **Researching.** The brief shows the current topic, counts tick up, and topics tick off 1 → 2 → 3.
6. **Hand-off.** Researching collapses to its outcome and Evaluating sources expands briefly.
7. **The end.** A finished row is shown re-opened on click.

Include a phone-width (390 px) view of steps 2–3 and 5.

## Out of scope

The report page, the Evidence view, the idle page, and the page 1→2 and 2→3 transitions. Those are being fixed separately.
