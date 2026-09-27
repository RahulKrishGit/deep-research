# Deep Research — front-end design decisions

Scope: a React/Next front end for the existing FastAPI interface, for one local
operator, with no authentication. This document records only the decisions the
active design system (Perplexity AI) does not already answer. Colour, type,
spacing, radius, elevation, motion budgets and component recipes come from that
system and are not restated here except where a product fact forces a
qualification.

Grounding sources read before this document was written:

- `README.md` (§ FastAPI Interface, § LangGraph Orchestration, event tables per agent)
- `src/deep_research/api/app.py` — routes, error codes, SSE encoding
- `src/deep_research/api/sessions.py` — session lifecycle, terminal statuses
- `src/deep_research/api/models.py` — `SessionStatus` literal, response shapes
- `src/deep_research/api/events.py` — `encode_sse`, frame contract
- `src/deep_research/graph/state.py` — node names, route reasons, status derivation
- `src/deep_research/runtime/outcome.py` — quality status, token totals, "zero means unavailable"
- `src/deep_research/utils/types.py` — `ResearchEvent`, `ResearchError`, report composition

---

## 1. Product shape that drives every layout

Four facts, taken from the API surface, constrain the interface more than any
style choice does.

1. **A run is watched, not clicked.** Minutes to tens of minutes, one session at
   a time, single user. The interface must be worth leaving open on a second
   monitor and must survive a refresh without losing its place.
2. **A partially completed run is a normal outcome.** `max_iterations` and
   `incomplete` are first-class terminal statuses that still produce a report.
   The interface must not present them as failures, and must not present them as
   full successes either.
3. **Progress is replayable evidence, not a nicety.** `GET /research/{id}/stream`
   replays from event id 1 and then follows live. A late subscriber sees the same
   history as an early one, so the progress surface can be entered at any time and
   must render a 60-event backlog and a live tail identically.
4. **Absence is meaningful.** `trace_url` is `null` while running; `report_path`
   is `null` for a run that published nothing; token usage is not in the API
   response at all; a report is only *accepted* when a quality pass judged it.
   Every one of those renders as muted text, never as `0`, `—`, `null`, a
   placeholder, or a disabled control.

---

## 2. Three layouts for the investigation state

The investigation state is the screen the operator lives in for the length of a
run: the report is the deliverable, progress and observability are supporting
evidence. Three structurally distinct answers follow. All three use the same
tokens; they differ in what holds the primary column and where evidence lives.

### A. Narrative column with a details rail

The centre column is the report as a document: executive summary, findings,
verified claims, uncertainty, limitations, citations. A details rail beside it
carries session facts, the stage spine, and the recent event tail, and can swap
to source scores and claim verdicts for the passage currently in view.

- **Optimises** for the deliverable. The operator reads top to bottom the way the
  report was composed, citations sit inline as numbered references, and the rail
  absorbs every fact that would otherwise interrupt the prose. It is the only one
  of the three that is still correct when the run finishes and the operator stops
  watching and starts reading.
- **Costs** rail width (`--rail`, 300px at desktop in the built prototype) and
  forces a decision about what earns a place in it. Continuous event detail has to
  be compressed into a tail plus a count, which loses the "what happened at
  14:32" question unless the rail can open a full log.
- **Suits** the operator whose job is the answer: reads the report, spot-checks a
  claim against the rail, closes the session.

### B. Linear event timeline

The run is the interface. Every `ResearchEvent` is a row — `graph.node.started`,
`researcher.tool_call`, `fact_checker.claim.checked`, `graph.route.decided` —
in stream order with its `iteration` and `metadata` rendered as structured
columns. The report appears as the terminal row's payload rather than as the
page.

- **Optimises** for diagnosis and for watching. It is the most honest rendering of
  what the API actually streams, it makes a stalled run immediately visible, and
  it needs no interpretation layer: frame in, row out.
- **Costs** the deliverable. A 60-event run is already a long scroll; a
  three-pass run with a full tool budget will exceed 200 rows, and the report —
  the reason the run exists — ends up below the fold of its own screen. Reading a
  finished report in this layout means scrolling past the process that produced it.
- **Suits** the operator debugging the pipeline, or watching a short run where the
  question is "is it progressing", not "what did it find".

### C. Split ledger — claims against sources

A genuine two-pane ledger. Left: every `Claim` with its verdict and confidence.
Right: the `ScoredSource` rows the selected claim rests on, with authority,
recency, relevance, corroboration, `low_confidence`, and — where the run recorded
one — the contradicting passage. Verification is done by walking the left pane,
not by reading a document.

- **Optimises** for verification. Claim and evidence are visible at once, the
  contradiction and insufficient-evidence states are impossible to miss, and
  source quality stops being a footnote. It is the strongest answer to "should I
  believe this", which is the actual question behind most runs.
- **Costs** reading. Neither pane is wide enough for sustained prose, so the
  narrative the report was written to deliver has to be reassembled by the
  operator. A claim resting on five sources has no single-pane home, and the
  ledger's value collapses if the panes are stacked on a narrow screen.
- **Suits** the operator auditing a report they did not commission, or comparing a
  refinement pass against the previous one.

### Recommendation: A, with C available as a mode

Build **A** as the frame and keep **C** as a toggle on the same session object,
not as a separate screen. The product facts decide this: the report is the
deliverable, so the report gets the primary column; but the report's own content
is claim-linked, and the ledger is the only layout that renders the claims and
sources the run actually recorded. Making them two views over one session means
the operator reads in A and verifies in C without losing scroll position or
re-fetching anything. B's content — the event stream — is not discarded; it
becomes the rail's expandable log in A and the timeline in the running state,
where watching genuinely is the task.

The prototype implements A for the investigation state. It deliberately does
**not** render the event stream as a surface of its own: continuous event
detail is observability the operator rarely reads, and B's contribution is
reduced to the stage spine and the derived counters in the details rail (§5.8).

---

## 3. Screen inventory

One page, five stages. The session is the page: `/` and `/research/[session_id]`
are the same UI, and the stage is derived from the session's status rather than
chosen by the operator.

| # | Stage | Server state that selects it | What it shows | Transition out |
|---|---|---|---|---|
| 1 | **Idle** | no active session | Composer only | Operator submits → `202` → stage 2 |
| 2 | **Submitted** | `status == "running"`, first beat | The question read back, the model and effort in use — and nothing else | Held ~2.2s → stage 3 |
| 3 | **Running** | `status == "running"` | **The pipeline, centred**, with the question and its settings above it | Server status leaves `running` → stage 4 or 5 |
| 4 | **Report** | any terminal status with a report | The question, the settings in force, actions, then the whole Markdown body and its evidence panels | Opening another session, or New research |
| 5 | **Failed** | `failed` | Enumerated error type, why there is no artifact, what survived the halt | New research |

**Stage 2 carries the question and its settings, and nothing else.** It used to open a card
underneath them: a "Starting session" chip, the session id, `POST /research → 202`, and two
paragraphs explaining that overrides are read once and that the console rejoins a run by its
id. All of it was accurate, and none of it was wanted. The endpoint label and the session id
are the two things the operator had already asked to have taken off the other stages, and the
prose is the same "text where state would do" that §3.2 and §3.5 keep running into. The beat
has one job — hold the question still for a moment before the pipeline takes the screen — so
it now shows the question, the settings in force, and the eyebrow that names the beat.

**The composer exists on stage 1 only.** Once a question is sent, the text box is
gone for the rest of that session's life: stages 2, 3 and 4 show the locked-in
question and the settings it was run with, and nothing that accepts typing. A new
question is started from **New research** in the sidebar, which clears the active
session and returns to stage 1. This is deliberate — a text box beside a running
pipeline invites a second submission that the API would treat as an unrelated
session, and a text box above a finished report invites a question the operator
would expect to refine the report in place.

**The pipeline owns the running stage.** It was a 280px rail in the previous pass
and is now the centred column at reading width: seven rows, one per graph node,
each showing its ordinal, its name and what it does, with the active row marked
and checked rows behind it. It is the only thing in the running stage competing
for attention, because watching is the whole task. The session facts and cost and
usage column that sat beside it has since been removed as well: it repeated the
state the pipeline already showed, and its figures (`current_agent`, a token
count that only arrives with the finished outcome) either duplicated a row or read
`Not yet` for the whole run.

Supporting surfaces that are **not** stages:

- **Session history** is a collapsible sidebar on every stage, not a route. It is
  a rail of past sessions that navigates the main region — the same relationship
  a mail client has between its list and its reading pane. See §3.1.
- **The report artifact** (`GET /research/{id}/report`) renders inside stage 4.
  It has no independent navigation state.
- **The trace link** leaves the application entirely (`trace_url` is a LangSmith
  URL). It is a link with an external indicator, never an embedded viewer — and it is
  **one link, in one place.**

  It used to appear three times on the report page: in the report's own action row, again
  as a small button beside the topbar status chip, and a third time inside a rail card
  headed *Artifacts* that also repeated the report download. One destination, offered three
  ways, reads as three different destinations; the operator asked for it to be one. The
  action row keeps it, the topbar button and the whole Artifacts card are gone, and the
  report download is now likewise the single `Download Report` beside it.

  Removing the card also brought the stage back inside the design system's accent budget.
  The card carried two accent-coloured links, so the page was showing three accent elements
  against a limit of one per view (a documented override of two). It now shows exactly one:
  the primary download.
- **`states.html`** is a review artifact, not a screen: it renders the empty,
  configuration-error, failed and partial cases plus the status-mapping chips.

There is no settings screen, no account surface, no share surface, no
collaboration affordance, and no billing — single local operator, no auth.

### 3.0 The settings strip

Every stage after submission carries the same three facts, in the same order, as
a mono chip row: **model, thinking, effort** (and the output directory when one
was set). They are rendered from the session's own copy, not from the live
composer state, because a session's settings are fixed when it is created — the
override dict is read once by `prepare_research_settings`. When thinking is
`disabled` the strip reads `effort not sent` rather than showing a value that has
no effect, which is the same rule §5.3 applies to the control itself.


### 3.1 The sidebar, and the one thing it cannot do

The sidebar is the redesign's most constrained surface, because **the API has no
collection route**. `SessionStore._sessions` is a process-local dict and the only
five routes are `POST /research` plus four `GET`s keyed by an id the client must
already hold. A session id is generated server-side (`new_session_id()`), so a
client cannot even guess one.

The sidebar therefore renders a **client-assembled ledger**, and says so in its
own footer rather than implying a server-side history:

| Sidebar row shows | Source | Honest when absent |
|---|---|---|
| Question text | The client's own copy of what it submitted, clamped to two lines | Never absent for rows the client created |
| A running mark | `status === "running"` in the session snapshot | Absent on every settled row, by design — see below |
| Session id | `session_id`, carried on the row as `data-session` and `title` | Never rendered as text, so it cannot be missing from the page |
| Report body on click | `GET /research/{id}/report` | A failed row opens stage 4, not an empty reader |

A row shows the question, and a spinning `--status-ok` ring while a run is in
flight. It shows nothing else. Status chips, iteration counts and durations were all
removed from this list: together they turned an index of questions into a status
table, which is not what an operator scanning for "the one I asked earlier" is
reading it for. A row is identified by what was asked, and every settled outcome is
already visible on the session itself.

The consequence is that **the list does not report how anything ended.** A completed
run, a partial run and a failure look identical in the sidebar, and the only state
it carries is "still going". That is a real cost, accepted deliberately: the
alternative was four status words in a 296px column, against a question the operator
could not finish reading.

The ring is reserved at 9px in *every* row rather than only in running ones, so a
question does not reflow at the moment its run settles. That is why a settled row
still shows a faint `--border` ring — it is holding the mark's place, not reporting
a status.

**This row is the one place in the product where a status has no word.** Everywhere else
§5.1's rule holds and a status carries its label. Here the running mark is a ring, and the
cues that carry it are the ring's `--status-ok` hue, its spin, a step from `--muted` to `--fg`
on the question, and an accessible name ending in `— running`. Under
`prefers-reduced-motion` the spin is suppressed, which leaves the hue and the text step.
That is a genuine narrowing of the rule, taken because four status words in a 296px column
cost more than they returned, and it is recorded rather than glossed.

Two consequences that are stated in the UI, not hidden:

1. The list is per-browser and resets when browser storage is cleared or the
   service restarts. It is a convenience, never a system of record.
2. Clicking a row for a session the client did not start is impossible — there is
   nothing to click.

**A row is a label, not the question.** `.sb-item .q` clamps to two lines with an
ellipsis. Without it, a pasted summary set a single row **1677px tall** — eighty-odd lines
— which pushed every other session out of the list and made the sidebar useless for the
thing it is for. Two lines is what the sidebar already gives an ordinary question, so a
long one ends in an ellipsis where a short one ends in a full stop.

`max-height: calc(2 * 1.5em)` is the guarantee and `-webkit-line-clamp` is the refinement:
the clamp supplies the ellipsis, and where it does not take effect the box still ends at
exactly two lines rather than slicing the third through its glyphs. The clamp is scoped to
`.sb-item .q` and deliberately does not reach the starter chips on the composer, which share
the `.q` class name but are a different component with different content.

**This is the one surface that keeps a clamp.** §3.2 explains at length why the question
itself no longer has one; the short version is that a row is a label and the question is
not, and only the row can be shortened without the operator losing something they asked to
see. A row has no control whose measurement a clamp could break, either — which is what made
the clamp safe here and unsafe there.

### 3.2 The composer, and what it sends

The composer lives on the idle stage and nowhere else. It carries the question,
a `+` popover for run settings, and the submit action; §3 explains why it does not
follow the operator into the running and report stages.

It is also **static markup rather than an injected template**, and the app opens on
the idle stage rather than on a session. Both are deliberate. The first page is the
console's entry point, so it must render its primary control without depending on a
script having run: a page whose main element is assembled at runtime is blank in any
renderer that does not execute scripts, and flashes empty in one that does. The
composer therefore sits in the document once, on the stage that owns it, and the
stage switch is only a visibility change.

**What sits under the composer is a set of questions to run, not an explanation of
the console.** The first page previously carried a note describing where earlier
reports live and how the client assembles its session list — accurate, and the
wrong thing to put in front of someone with a question. It is now six starter
questions, and **one click runs the one you pick.**

They used to fill the composer and wait to be sent, which made a single decision into two. A
starter is a complete question — it is not a draft to be edited first — so clicking it sets the
field and submits inside the same tick. Because nothing is painted in between, the text is never
seen sitting in the box: it leaves the composer's frame and lands as the record, which is exactly
what typing it and pressing send looks like.

**The chips are set in the composer's own type for that reason.** They were `--text-sm` (13px)
while the box that carried the question to page 2 was `--text-base` (15px), so the question
visibly grew as it left the chip — the one thing a hand-off must not do. Family, size and
leading now match `.composer textarea` exactly, so the text that flies is the text that was
read.

The starters are **real questions from this project**, not invented example copy:
four from the question bank in `tests/…/test_cases.py`, one from the published run
in `docs/reports/`, and one from the README. They were chosen for shape, not to
flatter the engine — a constraint survey, a maturity assessment, a
better-technology question, a technical limit, a causal policy question, and one
about measurement reliability. A starter is therefore a query the engine is known
to handle rather than a claim about what it can do.

This is the same rule §3.5 applies to the pass machinery: **text on this screen is
for what cannot be shown.** A static explanation of the client's own architecture
is neither actionable nor state — the operator does not need it to ask a question,
and it ages badly.

**The submit action must not depend on form submission.** The send button is
`type="submit"`, and for a long time that was its *only* activation path: the click
went to the browser, the browser raised `submit`, and the form's listener ran. That
works in a standalone page and fails silently in a sandboxed iframe — a preview pane
without `allow-forms` blocks the submission outright, so no `submit` event is ever
raised and the button does nothing at all. The console looks inert: the first page
renders, the operator types, and pressing send has no effect. The textarea's Enter
handler was never affected, which is what made the fault so hard to read — the
keyboard worked and the pointer did not.

The send button therefore carries its **own click handler**, the form's `submit`
listener is kept only for implicit submission, and `submitResearch` refuses to run
from any stage but idle so the two paths cannot mint two sessions. Any future
interactive control on this page should be checked the same way: a preview is a
sandbox, and `localStorage`, form submission and cross-frame access are the three
things it takes away. (The six `localStorage` reads and writes here are all
`try`-wrapped for exactly that reason; there is no `history`, `cookie` or
`navigator` access to worry about.)

**A long question grows the box; it never hides inside it.** The textarea starts at
two rows and is measured against its content on every input, so the field fits what has
been typed. It stops at `min(232px, 32vh)` and scrolls past that — the cap is
viewport-relative as well as absolute, so a short window cannot let the composer eat the
page. `overflow-y` stays `auto` rather than `hidden`, because the text has to remain
reachable even if the script that does the growing never runs.

The same question becomes a *heading* on two later stages, and there the first attempt
was the wrong instrument. Capping `.ask-locked` and `.report-q` at `min(340px, 42vh)`
with `overflow-y: auto` kept the page from breaking, but it handed the operator a heading
they had to scroll — which is not a heading. The real fault was typographic: a question is
a heading while it is *heading-length* and a passage once it is not, and `.ask-q` set
every question at `--text-3xl`, the display size, which is what a *title* is for. A pasted
summary at that size came out as a forty-line wall of 28px text that pushed the pipeline
off the running stage and the report off the report stage.

**One size, and it is the size the question was typed at.** The stepped tiers were the
second wrong answer to the same problem. Set at display size a pasted summary became a
forty-line wall; stepped down by length it stopped being a wall but started arriving
*changed* — a short question left the composer at 15px and landed in the record at 28px.
The operator's words: it "does not reappear again". It reads as a different piece of text
rather than the same one coming back, which is exactly what a hand-over must not do.

`.ask-q` and `.report-q` are both `--text-base / 400 / --leading-body` now — the composer's
own type — so nothing about the question changes as it moves. The flight box already carries
the composer's type for the whole journey, so matching it on arrival is also what turns the
landing into a hand-over rather than a swap. There are no tiers and no size classes left.

**What a short question gets instead of a bigger size is the middle of the frame.** The frame
is the width of the input box, so a single line leaves space on both sides and centring uses
it; a question that fills the width gets nothing, because centring it would only make both
edges ragged. The script sets `q-center` on the hosts where the text is short enough to sit
on one line.

That decision is a character count (`Q_CENTER_MAX`, 80) and not a measurement, deliberately.
The frame is 720px of content at 15px, so a line holds roughly 95 characters and 80 keeps
every centred question clear of a wrap. Measuring the laid-out text would mean reading
geometry off an element that may still be `display: none` — the ordering hazard that broke
the old clamp control, and not one worth re-introducing for a cosmetic decision.

**The measure belongs to the frame, not to the type.** It was a `ch` value, which was wrong
in its own right: `ch` is relative to the font size, so the frame came out *narrower* the
longer the question got — `44ch` is ~684px at the display size and only ~366px at the passage
size, and the long tier's `60ch` was the 485px the operator was looking at. They type into a
`--reading-max` box (720px) and then watched the same words re-set into a 485px frame with
dead space either side. The record was narrower than the thing it recorded.

Both question frames now read `max-width: min(100%, var(--reading-max))` **and
`width: 100%`** — the same token the composer's frame uses, and the full width of it whether
or not the text fills it. The `width` matters: without it the frame is shrink-to-fit and a
short question has no space inside it to be moved into. It also means the travelling box has
no width difference to hold across the flight, for any question.

This does run past the design system's 55–70 character guidance; 720px at 15px is about 96
characters a line. That guidance is for reading prose, and a question is a single utterance
the operator typed into a box of exactly this width. Matching the input is the stronger
principle here — the alternative is the same words changing frame size between two pages.

**Nothing clips the question, on any stage, and that is a decision reached the hard way.**
It was clamped to four lines in the ask head for three turns, to stop a pasted paragraph
pushing the pipeline off the running stage, and it failed in three separate ways. Every one
was found by the operator; none by the tests.

1. **Four lines hid most of a long question.** A `Show full question` control that reveals
   the text is not the same thing as showing it, and the operator said so twice before the
   point landed.
2. **The cap was measured against the wrong box.** `box-sizing: border-box` is global, so a
   cap of "four lines" against the border box is four lines *minus* 32px of padding and 2px
   of border — two and a half lines, with the third cut through its glyphs.
3. **The clamp destroyed the evidence of its own truncation.** `-webkit-line-clamp`
   truncates the *rendered content*, which leaves the element with nothing to scroll:
   `scrollHeight` came back equal to `clientHeight`, so the overflow test that reveals the
   control could never fire. The text was hidden and the control meant to un-hide it was
   hidden with it.

What remains is one size and the page scrolling. The whole question is shown, at the size it
was typed at. A question is not a summary of itself, and the operator scrolling past their own
question to reach the pipeline is a smaller cost than the pipeline being shown a question that
has been silently shortened.

**The sidebar is the exception, and deliberately so.** `.sb-item .q` still clamps to two lines
with an ellipsis, and its row can reach 1677px without it. A sidebar row is a label: nobody
expects it to be complete, and it carries no control whose measurement a clamp could break.
The two surfaces want different things and now get them.

Both failure modes are worth remembering because they generalise. A cap written in the same
units as the content still measures whatever box the element's `box-sizing` names — and any
truncation mechanism that removes the overflow it creates also removes the ability to detect
it. The earlier version was "verified" by a stub that *asserted* an overflow
(`scrollHeight = 400, clientHeight = 100`) rather than deriving one, so it passed while the
real element reported none; the checks now model the DOM facts instead.

The composer's cap is still declared once, in the stylesheet, and the script reads its own
cap back from the computed style rather than repeating the number — so the
viewport-relative half keeps working without the script knowing anything about it. A grown
composer also moves the control row the run-settings panel is anchored to, so that panel
is re-placed while it is open rather than left pointing at where the row used to be.

What the composer sends — the four knobs it exposes:

| Control | Override path | Constraint the UI enforces |
|---|---|---|
| Model | `llm.model` | `deepseek-v4-flash` or `deepseek-v4-pro`, from the capability registry |
| Thinking | `llm.thinking_mode` | `enabled` or `disabled` |
| Reasoning effort | `llm.reasoning_effort` | `high` or `max`; **omitted and disabled when thinking is off** |
| Refinements | `max_iterations` | **1–5**, default **3**; the stepper's buttons disable at both bounds |
| Output directory | `output.directory` | Free text under `config_overrides` |

The effort control is disabled — not hidden, not silently defaulted — when
thinking is `disabled`, because `ModelCapability` for the DeepSeek family
declares `enabled_efforts = {high, max}` and `disabled_effort = None`; the
resolver rejects an effort paired with disabled thinking. Making that
impossible in the UI is cheaper than a `422` the operator has to decode.

### The refinement budget

### The settings panel must open where it can be used

Every control above lives in one popover anchored to the composer's `+` button, and
that panel is **taller than the space above the composer on the first page**. Its
default placement is above the bar, which is correct when the composer sits low —
the running and report stages — but on the idle page it put the panel's top at
roughly `y = -300`: off the top of the viewport. Its notes were long enough to
reach back down into view, so the panel looked present and readable while the
model, thinking and effort controls sat above the window with no way to click
them.

Placement is **measured at open time**: the panel takes the side of the bar with
room for it, positioned in viewport coordinates when it flips below, and its height
is capped to the room actually free on that side — not to the whole viewport, which
let a tall panel start below the bar and still finish off the bottom of the screen.
It scrolls internally only when the window is shorter than the panel itself.
Re-measured on resize, since a flipped panel is positioned in viewport coordinates
and would otherwise drift from its anchor.

**The panel does not explain the API.** Its controls are labelled in the
interface's own words, and the mapping to override paths lives in the table above,
not in the UI. A note reading "Sent as `llm.thinking_mode`" told the operator
nothing the two buttons did not already say, and five such notes made the panel tall
enough to fall off the screen. One sentence survives inside the panel: the effort
control explaining why it is unavailable when thinking is off, which is state rather
than plumbing.

The general rule this leaves behind: **a popover that is taller than the space it
opens into is a bug, not a layout compromise.** It is invisible in a code review —
the markup and the handlers are all correct — and only shows up as "the buttons
don't work", because the controls are reachable in the DOM but not on screen.

`max_iterations` is the operator's control over how long a run may take, so it is
surfaced as a **bounded stepper** rather than a segmented row or a free number
field. A segmented row would imply a short closed set; a number field would accept
values the run cannot honour. The stepper disables its minus at the floor and its
plus at the ceiling, so the bound is *visible* rather than silently applied by a
clamp.

- **Floor is 1**, which is the schema's own: `graph.max_iterations` is validated
  `ge=1`, so zero is not a budget, it is an invalid request.
- **Ceiling is 5**, and that is a UI decision rather than a server one. The field
  has no upper bound — only a lower one — so the ceiling is chosen because a
  refinement costs an entire research pass and 5 is the largest budget this
  console can present honestly. It is one constant to raise.
- **Default is 3**, matching the configured `graph.max_iterations`.

The value is a ceiling, not a target, and the control says so: a run the critic
accepts first time uses one pass however high the budget is set. This is the same
distinction §3.5 is built around, and it is why the budget is labelled
"Refinements" with the count of passes rather than "iterations" with a maximum.

**The chosen budget drives everything downstream.** On submit it becomes the
session's pass ceiling, the pipeline script is built to that length, the top-right
chip counts to it (`iteration n of 5`), and the report's iteration fact reports the
pass the run actually ended on. Verified across budgets 1 and 5: the stepper value,
`session.passes`, the chip sequence (`[0]` versus `[0,1,2,3,4]`) and the report
fact all agree.

Output format is **not** surfaced. The API accepts `output_format`, so this is a
product decision rather than a gap: `output.default_format: markdown` is used, and
it is the only value the schema accepts today. There is also **no `llm.provider`
override path** in `ConfigSettings`, so a model from another provider cannot be
selected at all; the composer therefore offers DeepSeek models only, and says so
rather than presenting OpenAI options that would fail validation.

### 3.3 The question, after it has been sent

The question stays at the top of the running and report stages — it is what the
whole screen is about, and an operator returning to a tab after ten minutes needs
it before anything else. It is **not** editable, and the read-only state is shown
in three ways rather than by disabling something:

1. It is an `<h1>`, not an input. There is no field to focus, and no disabled
   input standing in for one.
2. It is set in a dashed-border block, so it reads as a fixed record rather than
   as a heading that happens to sit above a form.
3. Its settings chips are `aria-describedby` from the heading, so assistive
   technology reads the question and the model it was run with as one statement.

**The frame is on both stages, and until recently it was only on the running one.** The
report's question was bare text — same size, same width, nothing around it — which put it on
the page as one more line among the provenance meta above it and the options
below. The operator said they might miss it, which is the right reading: it was the subject of
everything underneath and it looked like a caption.

It now wears the same dashed block as the running stage's, which also makes the two question
boxes geometrically identical: 720px wide with `16px/20px` inside, a 678px measure on both.
That agreement is load-bearing rather than decorative — the travelling box lands on that
measure — so a change to either frame has to be made to both.

**The whole header block shares one axis, and that is why the settings chips moved.** The
report's header is the question frame and the run's settings — the same things the running
stage puts in its ask head. Both are centred on the page axis.

The chips used to sit down in the report column, beside the report card, on the reasoning that
they describe the run whose report is below them. That column is narrower than the stage by
`--rail` plus the grid gap, so its axis sits exactly half that — 166px — to the left of the
frame's. Centring them where they were would have left them 166px off the frame, which reads
worse than the left alignment it replaced. They belong to the run's header, so they are in the
header: `.report-head` now holds the meta-and-actions bar, the question and the settings, in
that order, and the report column holds the report.

**The status badge is not in the header, because it is in the topbar.** It was there — a second
copy of the chip, drawn at 15px in its own band beneath the question — and the operator asked
for it to go on the grounds that the topbar already says it. It does: `renderTopbar()` draws the
same chip on every stage that has a session, so the report head was repeating the chrome. The
running stage had already dropped its own copy for the same reason, which makes the topbar the
single place status is reported. `chipHTML()` therefore lost its `large` variant as well, since
the report head was its only caller.

The **failed** stage carried the same duplication, and it has now been removed. Its header held a
second `Failed` chip — the same word in the same colour as the topbar chip one band up. It
survived the earlier pass only because it sat outside the element that had been marked for
change. Removing it makes all three session stages consistent: the topbar is the single place
status is reported, and the *reason* a run stopped stays a separate element beside the question.

This is a structural move rather than a rule change, and it is the one part of that request
that reached outside the marked elements. It is noted here because the alternative — centring
each row inside whatever container it happened to be in — produces three different axes on one
screen.

Editing is not offered because the API cannot accept it: the question and the
override dict are read once by `prepare_research_settings` when the session is
created, and no route updates a running session. A different question is a
different session, which is what **New research** starts.

### 3.4 The pipeline's colour contract

Progress is carried by **the step number and the connector line**, and by nothing
else. The row surface stays nearly neutral so the colour that means something is
not competing with a tint that means nothing.

Each step is numbered 1–7 and keeps that number for the whole run. Three states,
and **the state of the run is readable in a single frame without waiting for any
animation to reach a useful phase**:

- **Pending — a hollow ring.** `--border` outline, muted digit. Nothing has
  happened here yet.
- **In progress — a solid node with a halo.** `--status-ok` fill with the digit
  inverted to `--bg` (~10:1), plus a 5px soft halo in the same hue. The halo is
  what separates "running now" from "already finished" at a glance; the fill alone
  would read as done.
- **Done — the same solid node, a quieter ring.** The halo drops from 5px at 16%
  to 4px at 9%, so a finished step stays in the same colour family as the running
  one without competing for attention.

The connector between two steps grows from its top when the step *above* it
finishes, filling with `--status-ok` over `620ms` after a shared `170ms` offset.
The offset is what makes the fill read as travelling down the list a beat behind
the highlight rather than snapping. A pending step's connector stays `--border`, so
an unfilled line always means "the step above me has not finished".

**It spans node to node, not row gap to row gap.** The connector was originally
sized to the 8px gap between rows, which left a 12px dash stranded in a 42.75px
space — 21px of nothing above it and 10px below, so it read as a stray tick rather
than a joint, and it sat 3px off the axis through the number nodes. It is now
anchored to the row's padding box, whose midpoint is exactly the node's centre once
the row centres its items, so `-50% - gap` to `50%` lands on two node centres with
no magic numbers and no dependence on how tall a label wraps. Both nodes are opaque
and painted above the line, so the overlap at each end is hidden; the rail still
begins at the first node and stops at the last, so there is no overhang to clip.

| Step state | Number node | Connector below | Row surface | Meaning |
|---|---|---|---|---|
| `pending` | hollow, `--border`, muted digit | `--border` | none | not reached |
| `active` | solid `--status-ok`, inverted digit, 5px halo at 16% | `--border` | `--status-ok` at 6% | running now |
| `done` | solid `--status-ok`, inverted digit, 4px ring at 9% | `--status-ok` | none | finished this pass |
| `loop` | as `done` | `--status-ok` | none | re-armed by a refinement pass |
| `skipped` | `--status-danger` outline | `--border` | `--status-danger` at 9% | never ran: the run halted |

**Why the running step does not blink.** An earlier pass blinked the active
number, and it was wrong for three reasons. A blink changes the one element the
eye is already tracking, so it competes with the fills travelling past it. Half of
its cycle is a low-contrast frame. And it costs the operator a second of watching
before the state is legible, which is the opposite of what a progress indicator is
for. A steady differentiated state reads instantly; motion is then free to do a
different job.

**One loop, and it is a ring.** The running node's halo eases between a 4px and a
7px radius at 13–20% over `2.2s`; the header status dot uses the same `halo`. That
is the whole animation budget for this screen.

A second loop — a soft radial blob that travelled down the running row — was built
and then removed. It failed on craft rather than on principle: a blurred
`radial-gradient` moving across the row has no clean edge, so it read as a smear
drifting over the hairline connectors rather than as progress, and it drew the eye
to the space *between* steps instead of to the step that was running. The lesson
recorded here is that on this surface a shape has to be a ring or a line to sit
cleanly next to a 1px connector; anything without an edge reads as dirt.

| Rule | Why |
|---|---|
| **Colour is never the only signal.** | State is carried by node treatment (hollow / solid+halo / solid+quiet ring), by the row's accessible name (`Writing report (in progress)`), and by the progress track's `aria-valuenow`. The digit slot only departs from a number when a step never ran, where there is no number left to fill. |
| **Only status tokens, never accent.** | Tinting the pipeline violet would burn the accent budget many times over on one screen. `--status-ok` and `--status-danger` are the semantic tokens, so the accent stays available for the single interactive element per view (§5.3). |
| **Motion is optional; state is not.** | Under `prefers-reduced-motion: reduce` the halo stops at a pinned radius, the connector arrives already filled — and every state still reads exactly as it does with motion, because none of the three states depends on an animation being mid-cycle. |

**Pacing is a prototype concern, not a design one.** A real run takes minutes and
each step holds its highlight for as long as it actually runs. The prototype
cannot reproduce that, so it compresses the schedule and weights it per step —
the researcher emits a tool-call event per tool per sub-topic and would otherwise
starve every other step of screen time. The weighting changes when a step is
highlighted, never what it says.

### 3.5 Passes, and why the pipeline restarts

A **pass** is one complete run of the seven steps. It is not a retry of a failed
step: after each pass the critic either accepts the report or routes the whole run
back for another pass, and `graph.max_iterations` bounds how many a run may take.
So a run's real shape is a loop, and the pipeline control is linear. That mismatch
is the single most confusing thing about this screen, and it needs to be designed
for rather than left to emerge.

**The number of passes varies, and the interface must not imply otherwise.**
`graph_route` checks `state.iteration >= state.max_iterations` *before*
`critique.should_continue`, so the bound is the graph's own law and outranks any
model judgement — but the bound is a ceiling, not a target. A run the critic
accepts on the first review publishes after one pass; a run that keeps drawing
refinement requests uses the whole budget. Both are ordinary, and the same UI has
to read correctly for a one-pass run, a two-pass run, and a budget-exhausted run.
An earlier prototype hardcoded a three-pass arc, which made a variable loop look
like a fixed ritual.

Three things make it legible, and **none of them is a sentence**:

1. **The return arc is drawn.** A curved path runs down the left gutter from the
   Reviewing node back up to the Planning node, with a dashed overlay that travels
   along it and an arrowhead pointing into Planning. This is the piece that was
   missing: the jump from Reviewing to Planning was invisible in a linear list, so
   the reset read as progress being lost rather than as a loop closing. The arc is
   measured from the live node positions on every layout pass, so it stays attached
   to the nodes through a resize, a wrap, or a font change.

   It has three states, driven by events rather than by a timer:

   | State | Set by | Reads as |
   |---|---|---|
   | `off` | run start, session end | a run that has not looped |
   | `flowing` | `graph.route.decided` = `refine` | the handoff: dashes travel Reviewing → Planning |
   | `settled` | `graph.refinement.started` | this pass is the one running; the arc rests lit |

   A one-pass run never shows it at all, which is correct — nothing looped.

   **The endpoints are bound to stage identity, not list position.** The arc
   resolves `critic` and `planner` from each row's `data-stage`, because anchoring
   it to the last row instead drew a **Publishing → Planning** arc: the arrow
   appeared to leave the step that finishes the run, so the diagram read as
   "publishing is done, now refining". Publishing is not in the loop at all — the
   routing decision is the critic's, and the next pass restarts from Planning.
   Position-based anchoring would also silently re-point the arc if a stage were
   ever added after Publishing.

2. **The pipeline is always exactly one pass.** When the critic routes back, every
   mark clears and the list starts hollow, so step 1 holding the highlight is
   correct rather than contradictory. An earlier build cleared nothing: it set the
   researcher to `loop` and left steps 2–7 marked `done` from the pass before,
   which made the panel read as progress moving *backwards*. The reset fires on
   `graph.route.decided` with `destination: refine` rather than on
   `graph.refinement.started`, so the spine is already hollow as the arc flows
   instead of showing a stale "Reviewing" for one frame.
3. **A step re-armed by refinement carries a `↺` mark**, not a label.

The **refinement track was removed from the running stage.** It was a second
representation of the same fact the arc already carries — how many passes the run
has taken — shown as a dot track in its own card with a `pass 2 of 3` label. Once
the arc existed, the card was a duplicate sitting in a rail that should hold only
supporting detail, and its label was the last piece of pass vocabulary on the
screen. The arc is now the only statement that a loop is happening, and it is
positioned on the pipeline where the loop actually occurs.

Its two nodes remain in the document as a visually-hidden host, because the event
handling still writes to `#passTrack` and `#passSummary`. Keeping the nodes is
deliberate: the wiring stays intact and the track can return by moving one `div`
back into the layout. Removing them properly means deleting `renderPassTrack` and
its call sites together, which is a larger change than dropping a card.

The reasoning that produced the track is kept here rather than discarded, because
the visual language is reusable if the count is ever needed again: one dot per pass
the run may take, on a hairline — hollow before it runs, filled with a halo while it
runs, filled and quiet once done, with the connectors between dots filling as the
run advances, and a pass in flight opening its dot into a ring on the same
`refining` window the arc uses so the two agree.

### Why this says nothing in prose

An earlier pass explained the loop in text: a paragraph on what a pass is, a line
naming each pass's fate, and a caption defending the `6 of 7` count. That was
wrong, and worth recording so it is not reintroduced.

- **It explains the mechanism instead of showing the state.** A reader who needs
  the paragraph has already been failed by the interface; a reader who does not
  needs it out of the way. The loop is a property of the process, and a process is
  the one thing an in-progress screen can demonstrate rather than describe.
- **It defends a number that should not have needed defending.** The `6 of 7` count
  existed only because the strip counted steps. A dot does not invite the question
  "out of how many", so dropping the count removed the need for the caption.
- **It ages badly.** The explanation described a ceiling as if it were a rule, and
  was wrong for any run that did not take exactly that many passes.

The rule this leaves behind: **state that can be shown is not written.** Text on
this screen is reserved for what cannot be shown — an enumerated error type, an
absent field, a boundary the operator has to respect.

**The prototype scripts the pass count per session** rather than fixing it, because
the count is a property of the run: one demo session publishes after a single pass,
one after two, and one uses its full budget. A session submitted from the composer —
which has no recorded outcome yet — is scripted at the configured ceiling,
`graph.max_iterations: 3`.

The honest limitation: the API reports `iteration` on the session snapshot but not
the route history, so the refinement track is driven by `graph.route.decided` and
`graph.refinement.started` rather than by the session. Reopening a finished session
can therefore show `iteration` but not rebuild the track's history.

### 3.6 Meter colour is a judgement about the number

The four meters in the report's quality snapshot — claim confidence, source authority,
corroboration, scored sources cited — carry a figure and a bar. The bar's colour is **derived
from the figure**, in one place, rather than set beside it:

| Figure | Fill | Role |
|---|---|---|
| above `0.80` | green | `--status-ok` |
| `0.40` to `0.80` | yellow | `--status-warn` |
| below `0.40` | red | `--status-danger` |
| no figure at all | grey | `--muted` — absent, not zero |

The boundaries are the operator's, and `0.80` sits in the middle band: the green band is
*above* it. `0.40` sits in the middle band too, since the red band is *below* it.

**Deriving it was the point.** The first version carried the class on one bar by hand — the
claim-confidence row was green and the other three fell through to the default grey, which read
as "no verdict" for three figures that plainly had one. A fill hand-classed `ok` while its row
reads `0.62` is a disagreement nobody notices until it matters, so `paintMeters()` reads each
row's own number and `meterClass()` is the only place the thresholds exist.

A row with no figure, or with something that is not one, keeps the default fill rather than
falling into a band. That is the same rule §4 applies everywhere else: a value that is absent is
rendered as absent, never as a zero and never as a verdict it did not earn.

The three fills use the product's existing status roles rather than new colours, so the meters
cannot drift away from the rest of the status language.

---

## 4. Status mapping

The API's `SessionStatus` is a five-value literal. The interface shows five
statuses. This table is the contract between them, and it is exhaustive: no
status may be invented and none may be dropped.

| Interface status | API `status` | Source of truth | Token role | Copy shown to the operator |
|---|---|---|---|---|
| **Running** | `running` | `ResearchSessionResponse.status` | `--fg` label, `--success` live dot (the one non-text use) | `Running` + current stage |
| **Completed** | `completed` | `…status` **and** `outcome.quality_status == "accepted"` | `--fg` label, `--success` dot | `Completed` |
| **Partially completed** | `max_iterations`, `incomplete` | `…status`; `completed` also lands here when the quality gates did not accept the report | `--fg` label, `--warn` dot | `Partially completed` + the report's own reason |
| **Failed** | `failed` | `…status` | `--fg` label, `--danger` dot | `Failed` + the enumerated error type |
| **Unavailable** | *not a status* | any `null` field | `--muted` text, no chip, no icon, no control | `Not recorded`, `Not available while running` |

Rules that follow from the table:

1. **`failed` is not the same as "no report".** `_record_failure` sets
   `status="failed"` and appends a non-recoverable `ResearchError`, but
   `GET /report` then answers `409 report_unavailable`, not 404. The failed
   screen states the failure and then states, separately, that no artifact was
   published.
2. **`completed` is not automatically "accepted".** `graph_quality_status`
   returns `partial` whenever `state.quality` is `None` or the route was not
   `critique_satisfied`. A `completed` session with no quality snapshot renders
   as *Partially completed* with the reason `not yet quality-gated`. The front end
   must not infer acceptance from `status == "completed"`.
3. **`max_iterations` and `incomplete` are not degraded states.** They render with
   the same visual weight as `completed` — a different dot colour and an accurate
   label, never a warning banner, never an apology. The report is present and
   authoritative.
4. **`unavailable` never becomes a value.** `trace_url: null` is
   `Not available while running` in `--muted`; `report_path: null` is
   `Not published`; token usage is `Not recorded` (its source —
   `total_token_usage` — documents that zero means "no provider reported usage",
   so a summed `0` is not a fact about the run); a request-budget snapshot that is
   absent is `Not recorded` and its ceiling stays absent rather than becoming a
   limit of zero. No `0`, `—`, `null`, empty chip, or greyed-out button stands in
   for a missing value. **A missing value is text, and it is always visible text.**
5. **Status is never carried by colour alone.** Every status has a text label at
   `--text-sm` minimum; the dot is redundant reinforcement, and a duplicated
   colour-vision simulation of the four chips is in `states.html`.
6. **`waiting` does not exist.** Before the first frame, or between
   `POST /research` and the first event, the screen is *Running* with stage
   `starting`. There is no sixth status.

### Derived stage display

`current_agent` is only populated between `graph.node.started` and the node's
completion, and is `null` from the first `graph.node.completed` of the terminal
node until the response arrives. The stage spine therefore derives its state from
the event stream, not from `current_agent`:

| Node | Label | Predecessor order |
|---|---|---|
| `planner` | Planning | 1 |
| `researcher` | Researching | 2 |
| `source_evaluator` | Evaluating sources | 3 |
| `fact_checker` | Checking claims | 4 |
| `synthesizer` | Writing report | 5 |
| `critic` | Reviewing | 6 |
| `refine` | Refinement pass | loops back to 2 |
| `finalize_report` | Publishing | 7 |

`current_agent` is used only as a fallback when the log is empty. A node's state
comes from `graph.node.started` / `graph.node.completed` / `graph.node.skipped`
for that node name. Refinement is a loop, so the whole list resets when the critic
routes back (§3.5) — `graph.route.decided` with `destination: refine` is the signal,
not `graph.refinement.started`.

### `iteration` in two places, and it must agree in both

The session's iteration shows up in the topbar chip and the report header. Two
rules, both learned from bugs:

- **It is read from the event stream, not the snapshot.** The API reports
  `iteration` on the session, but while a run is live it is the events that move it:
  every node event carries the iteration it belongs to. The chip is therefore
  updated as events arrive. The topbar is otherwise only rebuilt on a stage
  change, and an earlier build rendered it once — so the chip read
  `iteration 0 of 3` for an entire run while the pipeline moved through three
  passes.
- **A finished run's iteration is the pass it ended on, not the ceiling.** It is
  `last iteration carried by an event + 1`, so a one-pass run reports 1. Hardcoding
  the configured maximum made a single-pass run claim it had refined twice, which
  is the kind of number an operator would reasonably trust.
- **The ceiling beside it comes from the run, not from a constant.** The chip read
  `iteration {i} of 3` with the `3` written into the `STATUS` table, so a session created
  with one refinement announced "of 3" for its whole life — the same class of error as the
  one above, in the denominator rather than the numerator. It now reads `session.passes`,
  the refinement budget the session was created with, which is the same figure the pipeline
  and the `pass n of m` counters already use; the chip was the last surface still guessing.
  A session carrying no pass count drops the clause and shows `iteration 1` alone, because a
  wrong ceiling is worse than an absent one — it is a claim about the server's configuration
  that the client has nothing to support.

Internal iteration is zero-based — the first pass is `iteration: 0`, and the router
halts when `state.iteration >= state.max_iterations`, so a three-pass run carries
0, 1, 2. **The interface never shows that value.** Every site that displays it adds
one, through a single `passNumber()` helper, so the operator sees the pass they are
on rather than a zero-based index:

| Shows | Value | Notes |
|---|---|---|
| Topbar chip | `passNumber(iteration)` over the run's own ceiling | `iteration 1 of 2` for a run created with two refinements |
| Report header | the pass it ended on | already 1-based, set at publish time |

The stored session keeps the API's zero-based value. Converting at the display
boundary rather than in the data keeps the prototype's model faithful to the
server's, and keeps one helper as the only place the offset lives — an offset
applied by hand at each display site is how the chip and the report drifted apart
in the first place.

### Error rendering

`ResearchError` carries `error_type`, `source`, `message`, `recoverable`,
`timestamp`, `details`. Two levels only:

- `recoverable: true` — **not surfaced while a run is in progress.** A recoverable
  error is the normal case: it never stops the run, and the report already
  enumerates it in its own Limitations section. A live counter was therefore a
  number that asked to be read and told the operator nothing actionable, and the
  card that held it was removed before the rail itself was.
- `recoverable: false` — promoted into the failure panel, with the enumerated
  `error_type` as the headline, the API's own `message` as the sentence beneath it, and
  `source`, `recoverable` and `report_path` as labelled rows. This is the one
  place an error is a headline, because it is the reason the run ended.

**`message` was being discarded, and that is now fixed.** `graph/errors.py` is explicit that
an error's `message` is curated per enumerated type and is never `str(exception)`. The failed
panel nonetheless carried a fixed sentence, and the rail's Errors card rendered `error_type`
alone — so on both surfaces where errors appear, the one field written as a sentence for a
human to read was the one field not shown. Both now render it, falling back to the old static
copy when an error record carries no message. The fixture data was corrected at the same time:
it had been carrying `synthesizer_report_not_written`, a type the API does not define — the
synthesizer emits `synthesizer_report_provider_error`, `synthesizer_invalid_draft` and
`synthesizer_no_evidence` — and no `message` on any error at all.

**`timestamp` and `details` are still served and still not rendered**, and this section used to
claim otherwise about `details`. It is a flat dict whose shape changes with the error type —
`{"exception_type": …}` for a provider failure, `{"sub_topic", "priority", "stop_reason"}` for a
coverage gap, `{"rejected": […]}` for a refused draft — so rendering it means deciding how to
present arbitrary structured data: how many array items before truncating, what to do with a
nested object, whether an empty dict shows nothing or a dash. That is a presentation decision
rather than an oversight, which is why it is written down here instead of guessed at. `message`
was the same class of gap with an obvious answer, which is why it was fixed and this was not.

Halting types (`graph_planning_failed`, `graph_provider_configuration_error`,
`graph_agent_configuration_error`, `graph_invalid_agent_state`,
`graph_invalid_route`, `graph_request_attempt_limit_exceeded`) end the run;
everything else is recoverable and does not.

The report stage still carries an errors panel with a count and a disclosure, since
that is a finished run being reviewed rather than a live one being watched, and the
count there is answerable against the report it belongs to.

---

## 5. Decisions the design system does not answer

The design system covers colour roles, type scale, spacing, radius, elevation,
motion durations and the component recipes. These are the additions this product
needs; they are expressed as custom properties so they stay one contract.

### 5.1 Colour tokens measured against the canvas

Sampled against `--bg` (`#0f0f10`), the design system's own values measure:

```
--success  #22c55e   8.41:1 on --bg      7.71:1 on --surface
--warn     #f59e0b   8.92:1 on --bg      8.18:1 on --surface
--danger   #ef4444   5.09:1 on --bg      4.67:1 on --surface
```

**All three clear 4.5:1, `--danger` included.** An earlier draft of this section claimed
6.90:1, 9.61:1 and 4.37:1, and used the last of those to justify lifting the danger value —
"the second does not [clear 4.5:1]" was false, and a token was changed on the strength of a
number that does not hold. It is recorded rather than quietly corrected because the same three
figures had been copied into `states.html`, and two deliverables disagreeing about an
accessibility figure is worse than either being wrong.

The derived status tokens are kept, but only one of them is a change:

```
--status-ok:     #22c55e   oklch(72% 0.19 145)   8.41:1 on --bg   — alias of --success
--status-warn:   #f59e0b   oklch(74% 0.17 72)    8.92:1 on --bg   — alias of --warn
--status-danger: #f87171   oklch(71% 0.17 22)    6.93:1 on --bg   — --danger lifted, see below
```

`--status-ok` and `--status-warn` carry the system's own hex values unchanged; the alias exists
to say *status use only*, which is what keeps the accent budget rule in §5.3 enforceable. Only
`--status-danger` departs from the system, lifting `#ef4444` 0.08 up the OKLCh L channel — the
move the system's own hover rule prescribes.

That lift now rests on a weaker argument than it was given. `#ef4444` passes at 5.09:1, so
nothing *requires* the lighter red. What still recommends it is that status colour is used
mainly for 6–9px marks rather than text, and on `--surface` the lighter red holds **6.35:1**
against the system red's **4.67:1** — the difference between comfortable and marginal at dot
size. Reverting to `--danger` exactly is therefore a legitimate choice rather than a
regression; what is not legitimate is keeping the old figure.

**`--meta` is a second, unresolved shortfall, and it is worse than the accent one.**
`#5c5c5e` measures **2.63:1** on `--surface` and **2.87:1** on `--bg`. It is the quietest
token in the system, and the prototype uses it for text rather than for decoration:

| Where | What it is |
|---|---|
| `.sb-group` | the sidebar's `Today` / `Yesterday` group labels, 11px |
| `.setting-pill .k` | the settings-pill keys, 11px mono |
| `.starters .cap` | the starter block's caption |
| `textarea::placeholder`, `.input::placeholder` | both placeholder texts |
| `.starter .arw` | the starter rows' arrow glyph |

That is below even the 3:1 floor for large text, on text that is genuinely meant to be read —
"Today" and "Yesterday" are the only structure the session list has. It is left in place
because `--meta` is the design system's own token and darkening it is a change to the system's
hierarchy, not a bug fix: at 4.5:1 it would be indistinguishable from `--muted` (6.32:1 on
`--surface`), and the product would lose the third level of its text ramp.

The honest options, in the order this document would take them:

1. **Darken `--meta`.** `#818183` is the lowest value in the same ramp that clears 4.5:1, at
   **4.52:1** on `--surface`; `#8a8a8c` clears it with margin at **5.10:1** and is the value this
   document would pick. The ramp compresses rather than collapses — `--muted` is 6.32:1 and
   `--fg` is 15.42:1, so a darkened `--meta` still reads as the quietest of the three, helped by
   being 11px mono on a 15px page.
2. **Keep `--meta` for decoration only** and move the group labels and settings keys to
   `--muted`, which means the ramp keeps three levels but those two elements lose their
   quietness.
3. **Leave it**, on the grounds that a group label is a wayfinding aid and not content — the
   weakest of the three, and the one currently shipped.

This is a decision the front end should make deliberately rather than inherit by accident, which
is why it is written down instead of fixed.

### 5.2 Primary-button contrast, measured

`--accent` (`#a855f7`) carries **3.96:1** against `--accent-on` white. That is below the 4.5:1
normal-text requirement, and **the label does not qualify as large text** — WCAG's large-bold
threshold is 18.66px and this label is 15px/600.

Three ratios in this section were wrong before this note was rewritten, all of them estimated
rather than measured: the accent was given as 4.36:1 (it is 3.96), `--fg` on `--bg` as 8.19
(it is 16.81), and `--accent` on `--bg` as 6.02 (it is 4.84). The claim that 15px/600 cleared the
large-text rule was wrong in the same way. Measuring is the only thing that caught it.

**The resting state is therefore a known shortfall, recorded rather than resolved.** It keeps the
design system's own combination — `--accent` with `--accent-on`, which the system reserves for
buttons and badges — rather than darkening off-token. The three ways out are someone's call:

- **darken the fill** to buy the ratio, at the cost of no longer being the theme's accent;
- **use `--bg` as the label colour** rather than white, which measures 4.77:1 on the same fill but
  departs from `--accent-on`;
- **accept it**, on the grounds that a 36px solid button with a 600-weight label reads fine at
  3.96:1 even though the rule says otherwise.

**Hover used to make it worse, and that is fixed.** `--accent-hover` is *lighter*, and white on
`#c084fc` measures **2.64:1** — failing even the 3:1 large-text floor. Hovering the primary action
therefore *reduced* the label's contrast, which is the one thing a state change must never do.
Hover now goes to `--accent-active` (`#9333ea`) at **5.38:1**, better than the resting state, and
the pressed state goes one step further still — derived from the token with `color-mix` rather
than invented.

That also answers the operator's note that the button "did not match the theme". At rest it
always did; what did not was the state that hovering put it in.

**The same defect sat on the send control, and is fixed the same way.** `.send` is a 36px icon
button: it has no label to fall back on, so the fill carries the whole contrast budget for a white
glyph. It hovered through `--accent-hover` exactly as the primary button used to, putting the icon
at the same **2.64:1** — under even the 3:1 floor that applies to non-text content. It now deepens
to `--accent-active` (**5.38:1**) on hover and takes the same `color-mix` step when pressed, so the
two accent controls in the product behave identically.

`--accent-hover` is consequently referenced nowhere in the prototype. It stays declared because it
is part of the pasted system and the system may use it elsewhere; on this dark canvas, lightening a
fill under white text is not a state this product can use.

Outlined and ghost controls use `--fg` or `--accent` on `--bg` — **16.81:1** and **4.84:1** — so
neither has this constraint.

### 5.3 Accent budget per screen

The design system allows one accent interaction per view. Applied literally this
collides with the product: an active tab underline, a focus ring, a primary CTA
and a live link can coexist on one screen. The rule used here is narrower and
enforceable: **accent is applied only to interactive or focus-bearing elements,
never to static text, decoration, or data.** Within that, the accent count per
screen is capped at two, and no two are in the same viewport region. The live
progress indicator uses `--status-ok`, not accent, so watching a run costs no
accent budget.

### 5.4 Product-specific property names

```
--reading-max:               720px   /* report column */
--rail:                      300px   /* details rail; stacks below 1120px */
--sidebar:                   296px   /* session ledger */
--sidebar-collapsed:          64px
--topbar:                     56px
--container-gutter-desktop:   24px
--container-gutter-phone:     16px
```

These are declared once in the prototype's `:root` and referenced everywhere else, so the
layout carries no repeated magic numbers. There is no `--shell-max` and no `--spine`: the shell
is a two-column grid sized by `--sidebar` and whatever remains, and the running stage's spine is
a grid track rather than a named width.

`--reading-max` is the design system's answer-column figure, and §3.3 records that at 15px it
runs to about 96 characters — past the system's 55–70 guidance. That trade was made
deliberately, and the token's own comment in the prototype no longer claims the guidance is met.

**`--rail` is 300px, against the design system's 280px shell spec.** That is a departure, and
its measurable cost is worth the front end knowing. A `.bar-row` in a rail card spends 136px on
its label, 46px on its figure and 24px on the two gaps; the card spends 40px on padding. At
300px the quality meters' track is left about **54px**, and at the system's 280px it would be
**34px**. Four rows at 54px are legible but tight, and this is the first thing to re-examine if
the rail is ever revisited.

The breakpoints, in order:

| Width | Behaviour |
|---|---|
| above 1120px | report and details rail side by side; the rail is sticky |
| ≤ 1120px | the rail stacks beneath the report column and stops being sticky |
| ≤ 1080px | the sidebar leaves the grid and becomes a fixed drawer behind a scrim |
| ≤ 900px | phone gutter, and every control is raised to a 44px touch target |

The rail never becomes a drawer, and never overlays the report column — a stacked rail is
always fully readable, because a rail that hides evidence on a narrow screen is
worse than a longer page.

**The rail is sticky above 1120px.** It annotates the report, and the two heights do not match:
948px against roughly 2,100px. Left static it runs out two thirds of the way down, and the last
third of the report is annotated by nothing. It sticks at `calc(var(--topbar) + var(--space-4))`
so it clears the sticky topbar, and is capped at `calc(100dvh - var(--topbar) - var(--space-8))`
with its own scroll.

The cap is what makes the sticky safe rather than decorative. The rail is shorter than the
report but *taller than a laptop viewport*, so a sticky rail without a height cap would park its
bottom cards off-screen with no way to reach them — the fix would have made the rail less
readable than leaving it alone. `scrollbar-width: thin` matches `.sb-list`, so the product's two
scrolling columns read as the same control. `overscroll-behavior` is deliberately left at its
default: the rail only overflows by about 130px on a laptop, and containing the scroll would trap
the wheel once the rail bottomed out, with the page refusing to move until the pointer left it.
Sticky is dropped at ≤ 1120px, where the rail follows the report instead of sitting beside it and
something sticky would chase the reader down the page.

### 5.5 Typeface delivery

The design system names Inter and JetBrains Mono. This prototype performs **no
web-font fetch**: it binds the system's family names and falls back through
`ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` and
`ui-monospace, "Cascadia Code", Menlo, Monaco, Consolas, monospace`. The React
front end should self-host both faces; until it does, the fallback stack is the
rendering, not a placeholder.

### 5.6 Motion

The design system's durations cover controls; the pipeline needs its own, longer
ones, because it is watched for minutes rather than glanced at. Four rules:

- **Stage transitions.** A stage that is merely switched — sidebar selection, a
  deep link, failure — fades the outgoing stage out while the incoming one fades
  and rises in: one `200ms` `ease-out` run for the pair. The two transitions in the
  research flow are handoffs rather than switches, and are choreographed instead;
  the subsection below specifies them.
- **Reduced motion removes travel, not feedback.** Under
  `prefers-reduced-motion: reduce` every duration and delay collapses to `0ms`. The
  delay matters as much as the duration: a staggered element keeps `both` fill, so a
  surviving `60ms` would hold a card at `opacity: 0` before it snapped in. But
  zeroing *every* duration also turned each page change into a jump cut, which is not
  what the preference asks for — it targets travel, and the `translateY` in
  `enter` / `leave` / `arrive` is the part that belongs to it. Those three names are
  redefined inside the media query with the movement dropped and the fade kept, and
  the rules that consume them get `160ms` back. They win on specificity rather than
  source order: `.stage.is-on` is `0,2,0` against `*` at `0,0,0`, with both
  declarations `!important`. A page change is therefore a cross-fade in both motion
  modes, and the script no longer gates `is-leaving` or `is-arriving` on the
  preference.
- **The submitted beat.** The hold before the running stage takes over is one
  budget, not two timers racing: with motion it is the journey plus a settled pause
  (`320 + 900 + 200 + 750 = 2170ms`). Under reduced motion the travel is removed but
  the beats are not — the same drain and dissolve run without the lift, so the hold is
  `320 + 200 + 750 = 1270ms` rather than the earlier bare read-back. The read-back is
  never skipped under reduced motion — it is information about what was sent, not
  an animation. If a real `POST /research` is wired up, the hold ends when the `202`
  arrives instead, whichever comes first for a slow request.
- **Pipeline fills settle rather than switch.** `--fill-solid: 700ms` for a node
  (with a `60ms` per-number offset), `--fill-line: 620ms` for a connector, and
  `--motion-fluid: 420ms` for the row surface and the progress track. The easing is
  `--ease-entrance: cubic-bezier(0.32, 0.72, 0, 1)` — a long settle outward rather
  than an ease-out that lands abruptly. `--motion-lift: 900ms` uses the same easing
  for the same reason: the box has to arrive rather than stop, and at `540ms` a 400px
  rise read as a jump rather than as the gradual move it is meant to be. These, plus
  `--motion-clear: 320ms` and `--motion-dissolve: 200ms`, are the only durations in
  the product deliberately outside the design system's own set; §3.4 explains what the
  pipeline ones carry, and the handoffs above explain the other three.
- **One decorative loop, and it is not load-bearing.** `halo` runs at
  `--motion-halo: 2200ms` on the running node and the header status dot. It stops
  under reduced motion, and §3.4's table is identical either way — no state on this
  screen depends on an animation being mid-cycle. A second loop was tried and
  removed; §3.4 records why.

No content is ever withheld behind an animation, with one deliberate exception
recorded below: during a handoff the arriving page is laid out with its body held
back until the travelling element has landed on it, and that window is bounded by the
journey's own beats, never by a timer of its own.

#### The two handoffs

The design system says there are no entrance animations for content. The research
flow asks for two anyway, so this is a case of the explicit request overriding the
system rather than an oversight — and the override is confined to these two
moments. Everywhere else the rule holds.

**Idle → running, in three beats.** The composer is the only thing the operator has
touched, so the thing that travels is the question, not the page. The order is the
point: the page goes, then the box moves, then the next page assembles. Nothing here
overlaps with anything else.

1. **Clear** (`--motion-base: 200ms`). Everything on page 1 except the box fades
   out — the display line, the starter questions, the composer's own settings row and
   hint — leaving the box alone on screen. The stage is *not* cross-faded as a whole,
   because the box is inside it and would go with it; instead the stage holds at full
   opacity, its content is drained, and only then does the box start to move. This is
   the beat that makes the box read as one continuous object rather than as something
   that is replaced.
2. **Lift** (`--motion-lift: 900ms`, `--ease-entrance`). The box rises to where the
   locked question sits and becomes it. Only geometry interpolates — position, height,
   radius, fill — because the type is already the record's type and has no reason to
   change on the way. It is `position: fixed` and appended to `body`, because the composer
   belongs to the outgoing page and nothing inside a stage can travel out of it. It is
   `aria-hidden`, so the question is never announced twice.

   **The question slides to the middle of the frame as the frame rises**, and only when
   the frame is wider than the text. The span carrying it is `inline-block`, so as a block
   it measures the frame's content width and as an inline-block it measures its own text;
   the difference is the room the text has to move through, and for a question that fills
   the frame the two measurements are equal, so the offset is zero and nothing moves. That
   is the "only when there is width to do so" rule read off the layout rather than guessed
   at — and the threshold is the record's own `Q_CENTER_MAX`, so the flight and the record
   cannot disagree about which questions get the middle.

   It used to fade out over the second half of the journey and let the record fade in
   underneath. A dissolve is not a move, and the operator read it as the text disappearing
   and reappearing. It now stays visible for the whole flight and arrives already where the
   record is.
3. **Settle** (`--motion-dissolve: 200ms`, then `750ms`). The box dissolves and the
   real locked question is revealed beneath it, with page 2 following in a
   `70ms`-staggered rise (`150ms` for the card). Because the flight text and the record
   text are now the same size, the same colour, at the same inset and centred the same way,
   the dissolve has nothing left to hide: it is the frame that lets go, not the words. Then
   it holds still. The pause is not padding: the page has just been rebuilt around the
   question, and the operator needs the beat to read what is now on screen before the
   pipeline replaces it.

**The box is never duplicated and the composer is never seen to disappear.** The
flight box is created at the composer's own measured frame and the real composer is
hidden in the same frame, so for that instant the two are indistinguishable and there
is exactly one box on screen for the whole journey. Three things make that instant
invisible, and all three have to keep holding:

- The flight box starts as the composer's frame: same `--surface` background, same
  `1px solid var(--border)`, same `--radius-lg`.
- The inset register is **`16px 20px` at both ends** — `--space-4 --space-5` — which
  is exactly the composer's `--space-3` padding plus the textarea's own
  `--space-1 --space-2`. So the text sits in the same place relative to its frame at
  every point of the journey, and neither the padding nor the wrap point ever moves.
- It is measured from the **composer's frame, not the textarea**. Measuring the
  textarea — which is what an earlier pass did — starts the box `12px` small and
  `12px` off its own text, which is precisely the jump that reads as "the box
  vanished and a different box moved".

The register is not a coincidence: it falls out of two independent padding choices,
which is why it has to be rechecked whenever either changes.

**Nothing in the flight flips discretely any more, and that is the point.** An earlier pass
carried three `allow-discrete` properties — `font-family`, `border-style` and `text-align` —
each of which snapped at the 50% mark, so the midpoint of the journey carried three
unrelated jumps at once. Two are simply gone: the question is one size and one family on
every stage, so there is no type to morph, and the left-to-centre move is *performed* by the
slide rather than cut. The third, `border-style`, belongs to the frame and arrives with the
record under the dissolve.

The rule the work settled on: **if a property cannot interpolate, do not transition it —
either remove the need for it, or move it into a beat where nothing is being read.**

**Submitted → running.** Nothing travels here; the seam is held still instead. Both
pages open with the same question in the same place, because both are a `.run-wrap`
with no top padding inside the same `.viewport` — the `var(--space-8)` opening offset
belongs to the viewport and is therefore shared by every stage. So the generic `6px`
slide is suppressed (`no-enter`) and only the body below the header rises
(`is-arriving`, a `60ms` offset on the card). The header is the strongest signal that
the page changed and the weakest thing to move, so it is the one thing that does not.

The submitted stage's `padding-top` was the one thing breaking that, and it is why it
is now gone: it carried an extra `var(--space-8)` on the run-wrap, so its header sat
`32px` below the running stage's and the question jumped upward at the handoff.
Restoring it re-introduces the jump. This is a coupling worth stating plainly — the
two stages are only interchangeable at the seam while their opening offsets are
identical, and neither one can gain padding without the other.

**Running → report: the header block slides down.** Here the two stages genuinely disagree
about where the question goes. Both put the same frame on screen, with the run's settings in the
same row beneath it, but the report sets a bar above them carrying the session meta,
**Download Report** and the trace link, so the block belongs lower down. Cross-fading it into a
different place reads as the block being replaced; moving it reads as the same block relocating,
so that is what happens. `enterReport()` walks `REPORT_HANDOFF`, reading each running-stage
element's position, switching stage, placing its report-stage counterpart at that position for a
single frame, and releasing it to its own — the transform then transitions over `--motion-base`,
matching the outgoing page's fade.

The block is a **list of pairs**, not one element, and that is the point: the question frame and
the settings chips do not travel the same distance. The gap beneath the frame is `--space-3` on
page 2 and `--space-5` on page 3, so the chips move 8px further than the frame does. Pairing them
separately lands both exactly; moving them as one group would leave the chips 8px out at the end.

Four things make that work and all four are load-bearing:

- **Each pair shares an x with its counterpart**, because `.report-q` and `#reportOpts` are both
  centred in the same column `.run-wrap` is centred in. Without that the moves would have a
  sideways component too, and a block that travels diagonally reads as a different object rather
  than the same one relocating.
- **The whole sequence is synchronous** — measure, switch, apply — with a single forced reflow
  committing every start before any of them is released, so the browser paints once. Nothing is
  seen sitting at its destination before the slide begins, which is the failure this kind of
  animation always has.
- **The start positions are measured from the running stage while it is still laid out.** From
  any other stage the measurements come back empty and the block is simply there, which is why
  opening a finished session from the sidebar does not slide it.
- **Nothing is left mid-slide.** The timer that clears the inline transform and transition is
  cancelled and re-armed on every entry, and each pair is reset before it is measured, so a
  second handoff can never inherit an offset from an abandoned first one.

**Under reduced motion** neither handoff's *journey* runs. No flight box is created,
page 1 is not drained (`is-clearing` is never applied), the composer keeps its own
frame (no `is-handing-off`), and page 2 is never held back with `is-preparing`. What
does still run is the page change itself: `showStage` cross-fades the pair in both
motion modes, and under the preference that cross-fade is opacity-only.

Gating the cross-fade in script was the mistake. `if (prev && !reducedMotion())` meant
that for anyone whose system asks for reduced motion, *every* stage change in the
app was a hard cut — including the one from page 1 to page 2, which then had no
visible transition at all. That is not "reduced", it is removed. The preference is
honoured in the stylesheet, where the keyframes drop their `translateY` and keep
their fade, and the duration restores below it.

The `is-preparing` guard does still have to live in script rather than in CSS: left to
the media query, page 2 would be held at `opacity: 0` with no beat left to clear it.
That specific failure is why the reduced-motion check sits at the top of the beat,
guarding the whole journey, rather than on each step of it. It also means the
incoming stage keeps its enter animation there — the fade without the rise — so the
change still reads even when the journey does not run.

The hold is still there under reduced motion, at `1270ms` rather than `2170ms`: it is
a read-back of what was sent, not an animation, so the lift is subtracted while the
reading time and the two fade beats are not.

### 5.7 Event stream is not a surface

The stream is consumed, not rendered. The running stage shows four things derived
from it — the active pipeline node (with its one-line explanation), the stage
position, the pass number, and the progress bar — and nothing else. The tool-call
and claim counts were among those derived values until the cost card that displayed
them was removed; the counters are still accumulated, but nothing reads them. A raw
log is deliberately absent from the main region.

This is a product judgement, stated so it can be overruled: a run emits well over
200 events, the operator's question is "is it progressing and what is it costing",
and a tail answers neither better than a stage spine does. The event stream stays
the source of truth for the derived values, and the running screen reads them from
it live, so nothing is lost that a future log view could not reintroduce.

### 5.8 Cost and usage, honestly

The running stage no longer carries a "Cost and usage" card. It was removed along
with the session-facts rail beside it: while a run is live the card could only
show elapsed time, because token totals are **not** in
`ResearchSessionResponse` — they live in `ResearchOutcome.token_usage`, computed
from tracker metrics at the end of the run — and the runner's own tool-call and
claim counters were never returned at all. A card whose values read `Not yet` for
the whole run, beside a pipeline that was already showing the same progress, was
carrying no information the operator could act on.

The report stage keeps its own "Cost and usage" card, where the honest statement
is `Not recorded`. Nothing is ever rendered as `0`: `total_token_usage` already
documents that a zero total means "no provider reported usage", so a rendered `0`
would assert something the API never said.

---

## 6. What each stage needs that the API does not serve

Full detail, with the request shape each gap implies, is in
[`api-gaps.md`](./api-gaps.md). Summary:

| Stage | Blocked by |
|---|---|
| Idle | the session's own `query` is never returned; no endpoint lists sessions; no preflight/health route |
| Running | no token or tool-call counts in the snapshot; no `max_iterations`; no terminal frame on the stream; no `quality_status` |
| Report | no `evidence_path` in the snapshot; no report structure (sections, citations, claims, sources) — Markdown only |
| Failed | configuration failures are only reachable by attempting a run |
| Sidebar | no `GET /research`; no persisted store; SSE event history is not addressable after the fact |

Every one of these is worked around in the prototype rather than faked: the gaps
document names the workaround and, where there is no honest workaround, the
prototype says so in place instead of rendering a zero.

---

## 7. Prototype notes

`prototype/index.html` is the redesign: one page, five stages (idle, submitted,
running, report, failed), a collapsible session sidebar, and a composer confined
to the idle stage. After submission the page swaps the text box for the
locked-in question and its settings, hands the screen to the centred pipeline,
and finishes by replacing the pipeline with the full Markdown report.

The two handoffs in that journey are the only scripted motion in the product, and
§5.6 specifies them. The first is three beats and never overlaps itself: page 1 drains
to the box (`320ms`), the box rises and becomes the locked question (`900ms`), then it
dissolves and page 2 assembles and holds (`200 + 750ms`) before the pipeline takes
over — `2170ms` in all. The second handoff moves nothing and holds the question still
while the body below it changes. Both were built against a 1252 × 853 viewport; the
flight is geometry-derived rather than hard-coded, so it follows the composer wherever
the layout puts it, and it does nothing at all when either box cannot be measured.

The durations live in the stylesheet (`--motion-lift`, `--motion-dissolve`) and the
script reads them back, so the timing has one source of truth rather than two that
have to be kept in step by hand. The hold before the pipeline is the journey's own
return value, not a constant: change a beat and the handoff follows it.

For review, `window.drConsole` exposes `submit(question)`, `open(sessionId)`,
`finish()`, `stage()` and the `sessions` ledger, so any stage can be reached
directly without replaying a run. Jumping to a stage through that hook uses the
plain stage transition rather than a handoff, which is the correct behaviour and
also the quickest way to see the two side by side: toggle
`prefers-reduced-motion` and repeat either handoff to confirm that no state
information lives in the motion.

`prototype/states.html` is the review companion from the previous pass. It stays
useful as the rendering contract for the states the console reaches only in
unusual circumstances — empty, configuration error, session failed, partial
report — and it carries the status-mapping table rendered as live chips, so §4 can
be read against the actual pixels. It is not part of the app.

Both files are self-contained: no build step, no external scripts, no network
fetches, no web fonts. All colour, type, space, radius and motion values resolve
through the design system's custom properties; the only literal colours in either
file are inside the single `:root` block. The sidebar's collapsed/expanded choice
and the last-opened session persist to `localStorage`
(`dr.console.sidebar`, `dr.console.active`), and the page uses `window.scrollTo`
rather than `scrollIntoView`.

**Three working behaviours are scripted rather than wired, and the UI no longer says so about
all three.** The run is driven by an event sequence whose shape is taken from the documented
event catalogue rather than a live `EventSource`; the sidebar is a client ledger because there is
no collection route (§3.1); and the report body is one real published Markdown document reused
for every completed session.

That last one used to be labelled inside the report card — a note explaining that the body is a
fixture and that the app replaces it with the body returned from `GET /research/{id}/report`.
The operator asked for the note to go, and it has. **The limitation did not go with it:** what the
report stage shows is the same document for every session, and nothing on screen says so now. It
remains recorded here and in `api-gaps.md`, and it is the one thing in this file that should
probably be disclosed on screen again — briefly — wherever the prototype is shown to someone who
did not build it.

