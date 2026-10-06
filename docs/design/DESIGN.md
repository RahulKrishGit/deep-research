# Deep Research — front-end design decisions

Scope: a React/Next front end for the existing FastAPI interface, for one local
operator, with no authentication. This document records only the decisions the
active design system (Perplexity AI) does not already answer. Colour, type,
spacing, radius, elevation, motion budgets and component recipes come from that
system and are not restated here except where a product fact forces a
qualification.

Implemented by the Next.js app in `web/`; this package is the design reference.

Code and docs this document is grounded in:

- `docs/ARCHITECTURE.md` — the graph, event vocabulary, and agents
- `README.md` — the FastAPI interface
- `src/deep_research/api/app.py` — routes, error codes, SSE encoding
- `src/deep_research/api/sessions.py` — session lifecycle, terminal statuses
- `src/deep_research/api/models.py` — `SessionStatus` literal, response shapes
- `src/deep_research/api/events.py` — `encode_sse`, frame contract
- `src/deep_research/graph/state.py` — node names, route reasons, status derivation
- `src/deep_research/runtime/outcome.py` — quality status, token totals, "zero means unavailable"
- `src/deep_research/utils/types.py` — `ResearchEvent`, `ResearchError`, report composition
- `src/deep_research/graph/events.py` — the graph's own progress events: node lifecycle, route decisions, extra passes, redrafts, publication
- `src/deep_research/agents/report.py` — the consumer report and the evidence log, as published

---

## 1. Product shape that drives every layout

Four facts, taken from the API surface, constrain the interface more than any
style choice does.

1. **A run is watched, not clicked.** Minutes to tens of minutes, one session at
   a time, single user. The interface must be worth leaving open on a second
   monitor and must survive a refresh without losing its place.
2. **A partially completed run is a normal outcome.** `max_iterations` and
   `incomplete` are first-class terminal statuses that still produce a report.
   `max_iterations` now means the extra-pass ceiling was spent with a required
   target still missing; `incomplete` means the review did not accept the report,
   a quality gate blocked acceptance, or no review score exists. The interface
   must not present them as failures, and must not present them as full
   successes either.
3. **Progress is replayable evidence, not a nicety.** `GET /research/{id}/stream`
   replays from event id 1 and then follows live. A late subscriber sees the same
   history as an early one, so the progress surface can be entered at any time and
   must render a 60-event backlog and a live tail identically.
4. **Absence is meaningful.** `trace_url` is `null` while running; `report_path`
   is `null` for a run that published nothing; token usage is not in the API
   response at all; `evidence_counts` is `null` when a run left neither a
   composition nor a quality snapshot; `semantic_review_score` `null` means no
   score exists, never a score of zero. Every one of those renders as muted text,
   never as `0`, `—`, `null`, a placeholder, or a disabled control.

---

## 2. Three layouts for the investigation state

The investigation state is the screen the operator lives in for the length of a
run: the report is the deliverable, progress and observability are supporting
evidence. Three structurally distinct answers follow. All three use the same
tokens; they differ in what holds the primary column and where evidence lives.

### A. Narrative column with a details rail

The centre column is the report as a document, in the server's own order: the
bottom line (a direct answer — or, when none survived the check, one muted line saying
so — then one line per topic and per reader note), the parts with their cited points,
the Key figures or Options compared table, what could not be confirmed, the sources —
each section its own card, with a contents list beside or above them.
coverage, the evidence counts and the session facts, and can swap to source scores and
figure checks for the passage currently in view.

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
  finding against the rail, closes the session.

### B. Linear event timeline

The run is the interface. Every `ResearchEvent` is a row — `graph.node.started`,
`researcher.tool_call`, `evidence_verifier.verification.completed`, `graph.route.decided` —
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

### C. Evidence ledger — findings against their sources

A flat list of every finding the run kept or dropped, filtered by status —
verified, corrected, quoted, dropped, not found, refused — with the required
target it serves as a tag on each row, and a detail pane beside it for the
selected row: the snippet and passage, the source's authority, recency,
relevance and overall scores, and the Context Check line — the kept figure's
organisation, kind and release when one was judged, `context unchecked` when a
figure exists but none was confirmed kept, or `not run (no figures)` on a
finding that carries no figures at all (every quoted or dropped finding) —
and each figure kept or dropped with its reason. A not-found target
shows the queries searched and the pages read; a refused sentence shows why it
was kept out of the report.

- **Optimises** for verification. A finding and the page it came from are
  visible at once, a dropped snippet or an unchecked context is impossible to
  miss, and source quality stops being a footnote. It is the strongest answer to
  "should I believe this", which is the actual question behind most runs.
- **Costs** reading. The list is not prose, so the narrative the report was
  written to deliver has to be read in A. A finding cited from three places has
  one row, not three, and the pane's value collapses if the list and the pane
  are stacked on a narrow screen.
- **Suits** the operator auditing a report they did not commission, or checking
  why a required target was never answered.

### Recommendation: A, with C available as a mode

Build **A** as the frame and keep **C** as a toggle on the same session object,
not as a separate screen. The product facts decide this: the report is the
deliverable, so the report gets the primary column; but the report's own content
is finding-linked, and the ledger is the only layout that renders the findings and
sources the run actually recorded. Making them two views over one session means
the operator reads in A and verifies in C without losing scroll position or
re-fetching anything. B's content — the event stream — is not discarded; it
becomes the rail's expandable log in A and the timeline in the running state,
where watching genuinely is the task.

The prototype implements A for the investigation state and C as the **Evidence**
view of the same session — a `Report | Evidence` toggle in the report's head bar.
It deliberately does **not** render the event stream as a surface of its own:
continuous event detail is observability the operator rarely reads, and B's
contribution is reduced to the stage spine, whose running row opens on a live
brief (§3.4).

---

## 3. Screen inventory

One page, six stages, and a one-time check (2a) that can come between the second
and the third. The session is the page: `/` and `/research/[session_id]`
are the same UI, and the stage is derived from the session's status rather than
chosen by the operator.

| # | Stage | Server state that selects it | What it shows | Transition out |
|---|---|---|---|---|
| 1 | **Idle** | no active session | Composer only | Operator submits → `202` → stage 2 |
| 2 | **Submitted** | `status == "running"` or `"needs_input"`, first beat | The question read back and the four-chip settings strip (`model · thinking · effort · out`) — and nothing else | Held ~2.2s → stage 2a or 3 |
| 2a | **Check** | `status == "needs_input"`, or the stream's `session.clarification.requested` until the planner's `graph.node.started` | One question at a time in the pipeline card's place, under the eyebrow `Before we start`, the locked question and the settings strip: the answers, the best guess marked, **Other…**, then Back · Skip this one · Just start and the countdown; after the answers, the one summary line `Starting research with: …` | The planner starts → stage 3 |
| 3 | **Running** | `status == "running"` | **The pipeline, centred**, with the question and its settings above it and the note line at the card's foot | Server status leaves `running` → stage 4 or 5 |
| 4 | **Report** | any terminal status with a report — `completed`, or the three partial outcomes: the extra-pass ceiling spent (`max_iterations`), a review that did not accept or a gate that blocked acceptance (`incomplete`, scored), no review score (`incomplete`, unavailable) | The question, the settings in force, actions, then the server's Markdown as one card per section — the bottom line with a line for each topic and each reader note first — with a contents list, and its rail — or the Evidence view | Opening another session, or New research |
| 5 | **Failed** | `failed` | Enumerated error type, why there is no artifact, what survived the halt | New research |
| 6 | **Stopped by you** | `stopped` | The question, its settings and one short note — when the reader stopped and how far in, that no report was written — with **Ask again**; then the pipeline frozen at the stopped row (no pipeline card after a stop during the check) | Ask again (a new session, the same question), or New research |

**Stage 6 keeps what was done.** A session the
reader stopped (`stopped`, §4) opens on its own stage, `#stage-user-stopped` — not the
service-stopped stage a shutdown leaves (`running` with `finished_at`): the eyebrow
`Stopped by you`, the locked question and its settings strip, then one short note on the card
surface — `You stopped this research at {HH:MM}, {N} minutes in.` (the reader's own time;
`less than a minute in`, `1 minute in`), `No report was written. The plan and what research
found so far are kept below until the service restarts.` and **Ask again**, a ghost button that
starts a new session with the same question and this tab's recorded settings, else the
defaults. Below it the brief spine is frozen at the row the reader stopped. Finished rows keep
their outcomes and still open. The stopped row has a quiet node — a `--muted` edge, its digit in
`--fg`, the surface fill, no halo — the subtitle `Stopped · {its live facts}` (Researching's
facts line — `none of {n} topics done` or `{k} of {n} topics done`, then the pages read and
findings the run measured and only those, so `Stopped · none of 3 topics done` before any
is measured; `Stopped` for a row with none), and it opens to its frozen brief, where a
topic that was running reads `stopped` beside its ring, and one that had not started reads
`not run`, with no ring. Every later row reads `not run` in `--meta`, or `not run again`
for a row the loop had re-armed. There is no arc, no hand-off, no note line and no
counters block. A stop during the one-time check shows no pipeline card, and the
note reads `You stopped this research at {HH:MM}, before it started.`

**Stop asks once.** From the one-time check
through Reviewing, a small ghost **Stop** — an 8px square in the text colour, then the word,
kept at every width — sits after the status chip in the topbar, before the replay chip. It asks
once: a popover hung below it, right-aligned, 320px wide on one surface, `Stop this research?`
and `It stops right away and nothing more is spent. What's done so far stays here, but no report
is written.`, with `Keep going` (quiet, focused on open) and `Stop research`, the one red word:
`--status-danger` text on a `--border` edge, never a filled button. Escape, a click outside or
Keep going closes it and gives focus back to Stop; nothing stops until the reader says so.
Stopping cancels the run where it stands, with every call it has in flight, writes nothing and
opens stage 6. Once the run decides to publish the control is gone, because the API refuses a
stop from that decision (`409 not_stoppable`); if Stop or its popover held focus when it went,
focus moves to the status chip in the topbar (focusable by script only, no tab stop, the app's
own focus style) instead of falling to the page; a
refusal that still reaches an open popover says
`Too late to stop — the research is finishing.` with a Close button, and any other failure says
`Couldn't stop — try again` and keeps both buttons. On a phone the popover spans the width
between the gutters, under the topbar. Under reduced motion it fades in without rising.

**Stage 2 carries the question and its settings, and nothing else.** It used to open a card
underneath them: a "Starting session" chip, the session id, `POST /research → 202`, and two
paragraphs explaining that overrides are read once and that the console rejoins a run by its
id. All of it was accurate, and none of it was wanted. The endpoint label and the session id
are the two things the operator had already asked to have taken off the other stages, and the
prose is the same "text where state would do" that §3.2 and §3.5 keep running into. The beat
has one job — hold the question still for a moment before the pipeline takes the screen — so
it now shows the question, the settings in force, and the eyebrow that names the beat.

**Stage 2a, the one-time check, asks once and only when it matters.** With "Ask me when the question is unclear" on (§3.2), the session
first asks the configured model whether the question leaves something material open —
geography, period, purpose or scope. Usually it does not, and the run starts as before.
Otherwise the session waits (`needs_input`) and the check takes the pipeline card's
place below the locked question and its settings strip: one question at a time, at most
three, two to four answers each with one marked `best guess`, and **Other…** for the
reader's own words. A tapped answer moves on after 240ms. The footer offers Back, Skip
this one and Just start, and its cap counts down to the start on best guesses.
The answers are shown once, in the summary line, and never again: there is no
assumptions UI. No purple at rest: the only accent is the focus ring, because
nothing on the card is the page's primary action. Two refusals have their own face, in
place of the summary (the answers are posted once, never retried): `Already started with best guesses`
when the API answers 409 (the check is no longer waiting), and
`Your answers could not be sent; the run starts with best guesses.` when the POST fails
any other way. Either way the run goes on with best guesses, and the stream then moves
the stage on.
There is no `checking` status; while the
check call runs, for up to `hitl.check_timeout_s` (20s), `/status` reads `running` and
the page shows stage 3; if questions come back, stage 2a replaces the pipeline card.

**The composer exists on stage 1 only.** Once a question is sent, the text box is
gone for the rest of that session's life: stages 2, 3 and 4 show the locked-in
question and the settings it was run with, and nothing that accepts a question. A new
question is started from **New research** in the sidebar, which clears the active
session and returns to stage 1. This is deliberate — a text box beside a running
pipeline invites a second submission that the API would treat as an unrelated
session, and a text box above a finished report invites a question the operator
would expect to refine the report in place.

**The note line is the one text entry after stage 1, and it is not a second question.** The running stage's pipeline card ends
in a quiet line under a `--border-soft` hairline: a borderless field reading
`Add a note — something to focus on, leave out or change` and a neutral icon send, never
the purple primary. Enter sends, and the field is read-only while the note is in flight.
A note steers *this* run — the session reads it with a fast model and every step but
Verifying uses it — so it never reads as a new session. The running row
acknowledges each note at the top of its brief, with a muted dot: `Reading your note…`,
then `Got it — {the run's reading}{where it applies}`, with
`…, replacing your earlier note about {…}` when it contradicts an earlier one and
`Got it — passed on as you wrote it` when the reading failed; past two notes it shows the
latest two and how many it leaves out (`and 1 earlier note`, `and {n} earlier notes`). The
run's reading is shown, never the note echoed back. Each acknowledgement is a polite
live region, so a screen reader hears it once the note is read, and nothing moves focus. After the tenth note the field and the send are
disabled with no message and the same placeholder; once `finalize_report` has
started — from the run's decision to publish — a note is refused and one caption,
`Notes are closed — the report is being published`, takes the line's place. Any other
failed send keeps the text and says `Couldn't send — try again` under the field until the
next edit. The report then states what became of each note in its bottom line, after the
topic lines: one line per note the run has read by the time it publishes (a replaced note has
none), labelled `Your note · {short}` (the note's subject in one to three words). A note read
after the reviewer's last merge is taken in as already settled when the report is published, so
it changes no route, replaces no earlier note (that note keeps its line) and reads as a note
nothing judged; a note that arrives after the run has
ended has no line, and in replay mode that is every note, because the engine finishes before the
stream is paced out. A
research note's line is its own topic's line or, when
the bottom line kept none for it, says `See the section below.`, `No source we could check
covers this.` or `Not researched.`; a steering note's line says how the report treated it —
`Followed:`, `Not followed in this report:`, `No source we could check covers this:` or
`Not checked:`, then the run's reading of the note. A mark leads the line: ✓ when the note
was covered, ✗ when it was not found or not followed, none when nothing in the finished run
could judge it. A mixed note's line is its topic's line or text, then one sentence for the rest
of the note, with ✗ when either half missed. There is no
separate notes block. Each note's outcome also stays on the session's `/status`: `covered`,
`not_found`, `not_addressed` (the findings bore on it and the report still does not follow it,
after its one redraft — never `covered`), `not_checked` (nothing in the finished run could
judge it: no review did, or its own topic never researched it) or `replaced`; a research
note's comes from its own topic's targets, never from the review (§5.6).

**A bottom line with no answer.** The bottom line opens with a direct answer of one or two
sentences, each one checked against the findings. When no answer sentence survives that
check, the answer is not invented: the card opens on one muted line, `*The direct answer
could not be checked this time; each topic's checked line follows.*`, and the topic lines
and note lines follow as usual. The run also records a recoverable
`report_writer_bottom_line_no_answer` error ("No direct-answer sentence was kept after the
check; the bottom line holds the topic lines alone."), which, like every recoverable error,
is not shown while the run is in progress and joins the run's recorded errors. The assembled
bottom line (the call failed, or every sentence was refused) has its own muted line and
its own error, `report_writer_bottom_line_failed`.

**The pipeline owns the running stage.** It was a 280px rail in the previous pass
and is now the centred column at reading width: seven rows, one per graph node,
each showing its ordinal, its name and what it does, with the active row marked
and checked rows behind it. It is the only thing in the running stage competing
for attention, because watching is the whole task. The session facts and cost and
usage column that sat beside it has since been removed as well: it repeated the
state the pipeline already showed, and its figures (`current_agent`, a token
count that only arrives with the finished outcome) either duplicated a row or read
`Not yet` for the whole run.
The counters block that later sat below the spine has been removed from the running
stage too: each row counts for itself, in its live
subtitle while it runs and in its outcome line once it is done (§3.4). The block
remains on the Failed and service-stopped stages, as what survived the halt (§5.8).

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

Every stage after submission carries the same four facts, in the same order, as
a mono chip row: **model, thinking, effort, out** — `model deepseek-flash` ·
`thinking enabled` · `effort per agent` · `out output/`. There is no extra-passes
chip (§3.2), and none for the one-time check's setting: whether
the check ran is visible as stage 2a itself. They are rendered from the session's own copy, not from the live
composer state, because a session's settings are fixed when it is created — the
override dict is read once by `prepare_research_settings` — and the API echoes
nothing back (api-gaps 1.3). When thinking is `disabled` the strip reads `effort
not sent` rather than showing a value that has no effect: the resolver sends no
effort at all in that mode (`providers/capabilities.py`). Effort is otherwise
`per agent`, because every role carries its own configured value (§3.2).


### 3.1 The sidebar, and the one thing it cannot do

The sidebar is the redesign's most constrained surface, because **the API keeps no
durable collection**. `SessionStore._sessions` is a process-local dict, and the routes
are `POST /research`, `GET /research` (the newest sessions that process holds, memory
only), the `GET`s keyed by an id the client must already hold (`/research/{id}/status`,
`/stream`, `/report`, `/evidence`, `/trace`), `POST /research/{id}/answers` for the
one-time check and `POST /research/{id}/notes` for the reader's notes. A session id is
generated server-side (`new_session_id()`), so a client cannot even guess one.

The sidebar therefore lists **only what this API process holds** (`GET /research`, newest
first, memory only), and says so in its own footer rather than implying a durable history:

| Sidebar row shows | Source | Honest when absent |
|---|---|---|
| Question text | The session's `query`, as `GET /research` returns it, clamped to two lines | Never absent: every session has one |
| A running mark | `isLive(status)` in the session snapshot: `running`, or `needs_input` while the session waits for the reader's answers | Absent on every settled row, by design — see below |
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
on the question, and an accessible name ending in `— running` (`— waiting for you` while the
session waits for the reader's answers). A session the reader stopped carries no ring; its
accessible name alone says so, ending in `— stopped by you`, with no
status word on screen. Under
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

The starters are **real questions from this project**, not invented example copy.
They were chosen for shape, not to
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

What the composer sends — the four knobs it exposes, and the one line it only
states:

| Control | Sent as | Constraint the UI enforces |
|---|---|---|
| Model | `config_overrides.llm.model` | `deepseek-flash` (default, the configured model), `deepseek-v4-flash` or `deepseek-v4-pro`, from the capability registry (`providers/capabilities.py`) |
| Thinking | `config_overrides.llm.thinking_mode` | `enabled` or `disabled` |
| Effort | nothing | **read-only line**: `effort per agent: planner max · reviewer max · others high`, or `effort: not sent (thinking disabled)` |
| Output directory | `config_overrides.output.directory` | Free text |
| Ask me when the question is unclear | top-level `ask_clarifying_questions` | **On** or **Off**, default **On**, in the slot the extra-passes stepper left; Off makes no check (stage 2a) |

The request body, exactly:

```json
{"query": "…", "output_format": "markdown",
 "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"},
                      "output": {"directory": "output/"}},
 "ask_clarifying_questions": true}
```

Effort is not a control, and the reasons are recorded here rather than in the
panel. Every role carries its own `reasoning_effort` in `llm.model_overrides`
(`config.yaml`: planner `max`, report_reviewer `max`, the other four
`high`), and a per-role value wins over `llm.reasoning_effort`
(`LLMConfig.resolve_for` in `utils/config.py`) — so a global
`{"llm": {"reasoning_effort": "max"}}` override is accepted and silently changes
nothing. A per-agent override is worse: `llm.model_overrides` entries are
replaced whole (`apply_config_overrides` in `utils/config.py`), so
`{"llm": {"model_overrides": {"researcher": {"reasoning_effort": "max"}}}}` drops
the researcher's `timeout`. With thinking `disabled` the DeepSeek capability
declares `disabled_effort = None` (`providers/capabilities.py`) and no effort is sent at
all. The line states what is in force; nothing in the panel invites the operator
to change it, and api-gaps lists per-agent effort editing among the gaps the
front end should not close.

The provider is not a control either. `{"llm": {"provider": "openai"}}` is
accepted by validation, but nothing lists the valid provider/model/mode/effort
combinations (api-gaps 1.4), so the composer offers the configured provider's
three models and says nothing about others.

### The extra-pass budget

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
enough to fall off the screen. One sentence survives inside the panel: the read-only
effort line and its thinking-off text, which is state rather
than plumbing.

The general rule this leaves behind: **a popover that is taller than the space it
opens into is a bug, not a layout compromise.** It is invisible in a code review —
the markup and the handlers are all correct — and only shows up as "the buttons
don't work", because the controls are reachable in the DOM but not on screen.

**There is no extra-pass control.** The stepper, its
pill and its chip in the settings strip were removed: a reader had to guess the
budget before anything had been found. The reviewer's own gap-filling stays at the
configured `graph.max_extra_passes` (`config.yaml`, default 1); the composer
sends no `max_iterations`, and the API still accepts one (`api/models.py`), so a
scripted client keeps the old control.

The value is a ceiling, not a target, and the loop is not a retry: an extra pass
is bought only when a required evidence target still has no verified finding
after the review, and only while budget remains (`extra_pass_target_ids` in `graph/state.py`). A run
whose targets are all answered publishes after one pass. Nothing on the running
stage counts passes (§3.5): a row that a loop reopens says why it reopened, and
the report states the passes in plain words (§4).

Output format is **not** surfaced. The API accepts `output_format`, so this is a
product decision rather than a gap: `output.default_format: markdown` is used, and
it is the only value the schema accepts today. The provider is not surfaced
either: an `llm.provider` override is accepted by validation, but no route lists
which providers, models, thinking modes and efforts go together, so the composer
offers the configured provider's models and says so rather than presenting
options that would fail at the first call.

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

**Rows that expand keep the connector on the nodes.** The
running stage's rows open into a brief, so rows no longer share one height and the
midpoint rule above no longer lands on a node. In that spine (`ol.spine-lg.briefs`)
rows align to the top, each node sits at a fixed offset — its centre is
`var(--space-3) + 16.5px` below its row's padding box — and the connector is drawn
by the *upper* row, from its own node centre to the next node's centre
(`bottom: -(--space-2 + 2px + --space-3 + 16.5px)`: the list gap, the two rows' 1px
borders and the next node's offset). The fill scales from the top
(`transform: scaleY(0 → 1)`) once the upper row is `done` or `loop`; `data-fed`
stays on the lower row. The Failed and service-stopped stages keep the compact rows and the
midpoint rule; stage 6 keeps this spine, frozen at the row the reader stopped.

**What an open row says.** The
active row is always open; a done or loop row is closed on its outcome line and
reopens from its head (a `button[aria-expanded]` over the head); a pending row never
opens, save Reviewing's after a loop route (§3.5). While a row runs its subtitle
is green and live: Planning's elapsed time (`Xm SSs`); Researching's `{done} of {n}
topics done · {pages} pages read · {findings} findings`, each count only when the run
has measured it (or `{n} topics · researching` before the first topic is done);
`{rated} of {n} rated`; `{checked} of {n} checked`; `{written} of {n} sections written`
(written is the sections settled less the ones that ended failed), then ` · {f} couldn't be
written` when any section failed, then `writing the bottom line`; `reading the draft ·
{elapsed}`, which reads `read the draft in {elapsed}` once the review has landed, frozen at
that moment. Each row's body is its own:

- **Planning** — a status line that cross-fades `Reading your question…` → `Drafting
  a plan for your question…` → `Checking the plan covers everything you asked…` →
  `Fixing {k} topics the check flagged…` → `Plan ready · research starts now`, over
  four skeleton slots that fill with the plan's titles and tick as the check passes
  (`being fixed`, `fixed`, an amber ✗ `still flagged`); a research note read during
  Planning has its own slot, `joins the plan`, then `from your note`.
- **Researching** — the pass's topics: ○ `not yet`, ● `reading` (green, with the
  halo), ✓ `{n} findings`. Topics run concurrently, so several can be reading at once.
- **Evaluating sources** — `Rating {n} sources for trustworthiness and relevance`, a
  determinate bar, and Rated / Strong / Fair / Weak, each `not yet` until the first
  batch lands.
- **Verifying evidence** and **Writing report** — a ticker (`Just checked`, `Just
  written`) showing one real finding or drafted sentence at a time with its verdict
  in words — a kept one green, a dropped finding or a removed sentence amber — over a
  determinate bar and a tally. Until its first sample Writing's ticker waits on `The
  first section is being drafted…` (no section has returned), then on `The first
  sentences are being checked…` for as long as a drafted sentence is still unsettled
  (neither checked nor failed to check); once every one is settled and none could be
  checked, it reads `None of the drafted sentences could be checked`. A part
  that returned with every point refused drafted no sentence, so it never reads `being
  checked`: while some part is still out it stays on `The first section is being
  drafted…` (sentences may still come), and once every part has returned with none
  drafted it reads `No sentences were drafted to check`. Those two lines
  belong to the sections' phase: while the bottom line is written and nothing at all has been
  drafted yet (the count includes the bottom line's own candidates), on either edge (a note
  pass whose own part was fully refused, or a pass with no part to draft), it reads `Writing
  the bottom line…`, as the subtitle does. Once the bottom line has drafted sentences, both
  edges read as the sections' path does: `The first sentences are being checked…` while any
  is unsettled, `None of the drafted sentences could be checked` once every one is settled
  and none was. `Writing the bottom
  line…` is the in-flight line: a Writing row that has finished (done, or hollow after a
  loop) and is reopened shows how the step ended, so with nothing ever drafted it reads `No
  sentences were drafted to check`; a running row, and one frozen by a stop, keep the
  in-flight line. The tally's `section {k} of {n}` is the settle count, failed sections
  included; the subtitle is where the sections that could not be written are named. Its
  written count can fall by one: a section's draft returns and counts as written, then its
  Statement Check refuses every point and it becomes `couldn't be written`, so `{written}
  of {n} sections written` can go down as well as up, while the settle count and the bar
  never do.
- **Reviewing** — five criteria (`Covers your whole question`, `Rests on strong
  evidence`, `Every claim is credited correctly`, `Honest about what is uncertain`,
  `Easy to read`) and the reader's notes: rings that read `reading` under an
  indeterminate bar while the one call runs, then ✓, or an amber ✗ with the issue in
  plain words. Never a score. When the route sends the run back, Reviewing stays open
  on its checks and its verdict line for 2s before the run goes back (§3.5). Its
  subtitle reads `reading the draft · {elapsed}` while the call runs and `read the draft in
  {elapsed}` once it has landed, the time frozen at the landing. A route that halted would
  read `Halted` as its outcome, never `Review unavailable`; the backend publishes no such
  decision today.
- **Publishing** — `Saving the report and evidence log`.

Once done, a row's subtitle is its outcome: `{n} sub-topics · {k} from your note ·
{duration}`; `{n} topics · {pages} pages read · {findings} findings`; `{n} sources
rated · {s} strong · {f} fair · {w} weak`; `{v} verified · {c} corrected · {d}
dropped`; `Report drafted · {s} sentences · {c} citations`; Reviewing's route in
words with its duration (`Accepted · all 5 met · {duration}`, `{d} things to fix ·
back to the writer`, `Not accepted · {m} of 5 met`); `Published`. Reviewing's static
meta is `5 checks`, and `5 checks · your notes` while the run holds a note. A reopened
row shows its final body. In Researching's texts a measured zero reads in words (`no
findings`, `no pages read`), never a bare 0; the other steps' counts are numbers (`0
dropped`). A count the run has not measured is left out, never printed as 0:
Researching's line before a page or a finding is measured holds the topics alone, and
a redraft route with no defect count reads `Things to fix · …`, not `0 things to fix`.

| Step state | Number node | Connector below | Row surface | Meaning |
|---|---|---|---|---|
| `pending` | hollow, `--border`, muted digit | `--border` | none | not reached |
| `active` | solid `--status-ok`, inverted digit, 5px halo at 16% | `--border` | `--status-ok` at 6% | running now |
| `done` | solid `--status-ok`, inverted digit, 4px ring at 9% | `--status-ok` | none | finished this pass |
| `loop` | as `done` | `--status-ok` | none | re-armed by an extra pass, a note pass or a redraft, and completed again |
| `skipped` | `--status-danger` outline | `--border` | `--status-danger` at 9% | never ran: the run halted |

**Why the running step does not blink.** An earlier pass blinked the active
number, and it was wrong for three reasons. A blink changes the one element the
eye is already tracking, so it competes with the fills travelling past it. Half of
its cycle is a low-contrast frame. And it costs the operator a second of watching
before the state is legible, which is the opposite of what a progress indicator is
for. A steady differentiated state reads instantly; motion is then free to do a
different job.

**Three loops, each a ring or a line, each only while its step runs.** The running node's halo eases between a 4px and a 7px radius at 13–20%
over `2.2s`; the header status dot and each running topic's or slot's dot use the same
`halo`. Planning's skeleton slots carry a sheen — a `--fg` gradient at 7% sweeping
the bar over `--motion-halo` — only while Planning is the running row and its titles
have not arrived. Reviewing's bar carries a drift — a 28% segment crossing a hairline
over `--motion-halo` — only while its one call runs; it stops when the review lands.
Everything else runs once: a row opening or closing, the hand-off between steps, a
drawn ✓ or ✗, a status line's cross-fade, a ticker's sample, a count's tween (§5.6).
Under reduced motion neither the sheen nor the drift runs: the skeleton bar is still,
and Reviewing's bar is a static full-width line at 35%.

An earlier second loop — a soft radial blob that travelled down the running row — was
built and then removed. It failed on craft rather than on principle: a blurred
`radial-gradient` moving across the row has no clean edge, so it read as a smear
drifting over the hairline connectors rather than as progress, and it drew the eye
to the space *between* steps instead of to the step that was running. The lesson
recorded here is that on this surface a shape has to be a ring or a line to sit
cleanly next to a 1px connector; anything without an edge reads as dirt — which is
why the two later loops are a line's sheen and a line's drift.

| Rule | Why |
|---|---|
| **Colour is never the only signal.** | State is carried by node treatment (hollow / solid+halo / solid+quiet ring), by the row's accessible name (`Writing report (in progress)`), and by `aria-current="step"` on the running row. The digit slot only departs from a number when a step never ran, where there is no number left to fill. |
| **Only status tokens, never accent.** | Tinting the pipeline violet would burn the accent budget many times over on one screen. `--status-ok` and `--status-danger` are the semantic tokens, so the accent stays available for the single interactive element per view (§5.3). |
| **Motion is optional; state is not.** | Under `prefers-reduced-motion: reduce` the halo stops at a pinned radius, the connector arrives already filled — and every state still reads exactly as it does with motion, because none of the three states depends on an animation being mid-cycle. |

**Pacing is a prototype concern, not a design one.** A real run takes minutes and
each step holds its highlight for as long as it actually runs; on the real stream
each event arrives as it happens (§5.7). The prototype cannot reproduce either, so it
plays one event per tick, compresses the schedule and weights it per node — the
researcher emits a tool-call event per tool per sub-topic and would otherwise
starve every other step of screen time. The weighting changes when a step is
highlighted, never what it says, and every rule in §3.5 is written so the screen
reads the same whether events arrive one at a time or as a burst.

### 3.5 Passes, extra passes and redrafts

A **pass** is one complete run of the seven steps. It is not a retry of a failed
step: after each pass the reviewer scores the report and the graph decides where
to go — `finalize` and publish, `extra_pass` back to Researching for the required
targets that still have no verified finding, `note_pass` back to Researching for
the reader's notes that found no evidence yet (it is not
counted as an extra pass), or `redraft` back to Writing for a report with a
material defect (`graph_route` in `graph/state.py`). `max_extra_passes` bounds the extra
passes and `MAX_WRITER_REDRAFTS = 1` bounds the redrafts; each note is given at
most one pass and one redraft. So a run's real shape is a loop with two places it
returns to, and the pipeline control is linear. That mismatch is the single most
confusing thing about this screen, and it needs to be designed for rather than left to
emerge.

**The number of passes varies, and the interface must not imply otherwise.** An
extra pass is bought before acceptance is even considered, whenever a required
target is missing and budget remains, so a `completed` run with a not-found
target has spent its extra pass (or had a budget of 0). A run whose targets are
all answered publishes after one pass; a run that keeps missing a target uses
the whole budget and ends `max_iterations`. Both are ordinary, and the same UI
has to read correctly for a one-pass run, an extra-pass run, a redrafted run and
a budget-exhausted run.

**The active row is derived, and it is derived from completions.** A node's
`graph.node.started` now arrives live when the node starts (§5.7), but a reconnect
replays the whole log as one burst (the reviewer's own start is live too), so
completions stay the rule and read the same either way.
The active row is the **successor of the last `graph.node.completed`**: Planning until
the planner completes, then
Researching, and so on. The exceptions are keyed on the reviewer's route
decision, which it emits before its own completion (`report_reviewer_node` in `graph/nodes.py`):

| Trigger | Active row |
|---|---|
| no `graph.node.completed` yet | 1 Planning |
| `graph.node.completed` for `planner` … `report_writer` | the next row |
| `graph.route.decided` | the destination's row, immediately: `extra_pass` or `note_pass` → Researching, `redraft` → Writing, `finalize` → Publishing, `end` → none (the failed stage follows) |
| `graph.node.completed` for `report_reviewer` | **inert after a loop decision** — it neither marks Reviewing `done` nor moves the active row; after `finalize` or `end` it marks Reviewing `done` as any completion does |
| `graph.node.completed` for the hops `extra_pass` / `note_pass` / `writer_redraft` | nothing: hops never map to a row |
| `graph.node.completed` for `finalize_report`, or `graph.session.completed` | none; the stage transition follows |

Three things make the loop legible, and **none of them is a sentence**:

1. **Return arcs are drawn in the spine's left gutter.** Each leaves the
   Reviewing node and returns to the row the graph re-runs: the **extra-pass
   arc** to Researching, stroked `--warn`; the **note-pass arc** to Researching
   too, stroked `--meta`, because a reader's note is not a warning;
   and the **redraft arc** to Writing, stroked `--meta`. A dashed overlay travels along the lit arc and an arrowhead
   points into the destination. The arc is measured from the live node positions
   on every layout pass, so it stays attached to the nodes through a resize, a
   wrap, or a font change; the endpoints are resolved from each row's
   `data-stage`, never from list position, so Publishing — which is in neither
   loop — can never be an endpoint.

   Each arc has three states, driven by events rather than by a timer:

   | State | Set by | Reads as |
   |---|---|---|
   | `off` | run start; `graph.session.completed`; the next `graph.route.decided` | a run that has not looped, or whose loop is over |
   | `flowing` | `graph.route.decided` with `destination: extra_pass`, `note_pass` or `redraft` | the handoff: dashes travel from Reviewing to the destination |
   | `settled` | `graph.extra_pass.started` / `graph.note_pass.started` / `graph.report.redraft_requested` / `graph.note_redraft.requested` | the re-armed rows are the ones running; the arc rests lit |

   At most one arc is lit; `#spineWrap` carries `data-arc="extra_pass"|"note_pass"|"redraft"`
   beside `data-loop`. A one-pass run never shows any, which is correct —
   nothing looped. `--meta` is a stroke here and never text; the extra-pass reopen line's amber
   is the text-safe `--status-warn`. Under `prefers-reduced-motion` every arc
   arrives already lit.

2. **The pipeline is always exactly one pass.** On `graph.route.decided` with
   `destination: extra_pass` or `note_pass` rows 2–6 go hollow and Planning keeps `done`; with
   `destination: redraft` rows 5–6 go hollow and rows 1–4 keep `done`. The reset
   fires on the route decision, not on the hop's own event, so the spine is
   already hollow as the arc flows. **And the reviewer's own completion, which
   arrives after its route decision in the same burst, is inert in a looping
   pass.** An earlier build cleared nothing: it set the researcher to `loop` and
   left steps 2–7 marked `done` from the pass before, which made the panel read
   as progress moving *backwards*. The same defect returns in a subtler form if
   the reviewer's completion is allowed to mark Reviewing `done` above the hollow
   rows it just reset — so it is not allowed to.

3. **A step re-armed by a loop carries a `↺` mark**, not a label: on Researching
   for an extra pass or a note pass, on Writing for a redraft, once the row has completed again.

**A loop route holds its verdict for 2s.** The active row moves on the route decision as the table
says, but the painting waits: Reviewing stays painted as the row that ran — open on its
checks, its notes and the verdict line (`1 thing to fix · sending the draft back to the
writer`, `1 gap to fill · going back to research`, a note route's line) — for
`HANDOFF_HOLD_MS` (2,000ms), while the row the run returns to waits, closed and pending.
Then the ordinary hand-off runs: Reviewing folds with the from-role timings and that row
opens with the to-role's (§5.6). The hold paints and never marks: during it Reviewing
paints as the running row (`data-state="active"`) while its mark stays `pending`, as item
2's reset left it; it turns hollow when the hold ends, and its own completion stays inert.
Afterwards the hollow Reviewing row keeps a toggle on its head that reopens it on that
review — the hollow node and `5 checks` stay — until Reviewing runs again. A Stop during
the hold ends it.

**A reopened row says why it reopened.** On
`graph.extra_pass.started` Researching's brief opens on `Going back to research {k}
gaps the review found` (`k` = the event's `targets`; text `--status-warn`), and its
checklist lists only the topics that pass re-runs, as they start; on
`graph.report.redraft_requested` Writing's opens on `Rewriting to fix {n} issues the
review found` (`--muted`). Both pluralise (`1 gap`, `1 issue`). This replaces the
header's loop tag, which went with the "Now" header.

**A research note never waits for a review.** A note whose reading includes `new_angle` is researched as its own topic,
`Your note: …`. Read during Planning, it joins the plan when the plan is published, and
Planning's finished brief lists it with the plan's titles; read during Researching, it
starts its own topic at once, beside the topics already running, as a row of the
checklist, and the step does not finish until that topic does. Its acknowledgement says
which: `, as its own topic`; `, researching it as its own topic now` once its own topic
has started; `, researched as its own topic after this draft is reviewed` from Evaluating
to Writing; `, researched as its own topic next` during Reviewing. A note read after
Researching — or one whose topic failed or never started — buys one note pass, after
which only that note's part and the bottom line are rewritten. A mixed note does both:
its topic, and its steering half judged and enforced like a steering note.

**A reader's note buys its own reopenings.** A
research note owed its pass, or a steering note the review found no evidence for, buys
one targeted research pass: on `graph.note_pass.started` Researching opens on
`Researching your note: {the run's reading}` (`Researching your notes: {a}; {b}` for
several; `--muted`), and its checklist lists only the notes' own sub-topics,
`Your note: …`. A note with a steering kind the report ignores, or one that arrived while
the review ran, buys one redraft, which rewrites every part: on
`graph.note_redraft.requested` Writing opens on `Rewriting for your note: {…}`. Neither
spends the review's own budget — a note pass is not an extra pass and does not advance
`iteration`, and a note redraft is not the one writer re-run — and each note buys at most
one of each, so ten notes bind the run.

**No pass counter.** The pass track, and later the pass number
in the header and the chip, were second representations of what the arc already
draws; both are gone. The internal `iteration` stays in the API and the trace, and
the report states the passes in plain words (§4). The prototype keeps its hidden
`#passTrack` host as a historical record.

### Why this says nothing in prose

An earlier pass explained the loop in text: a paragraph on what a pass is, a line
naming each pass's fate, and a caption defending a step count. That was wrong,
and worth recording so it is not reintroduced.

- **It explains the mechanism instead of showing the state.** A reader who needs
  the paragraph has already been failed by the interface; a reader who does not
  needs it out of the way. The loop is a property of the process, and a process is
  the one thing an in-progress screen can demonstrate rather than describe.
- **It defends a number that should not have needed defending.** A step count
  invites the question "out of how many"; the running stage now keeps no counter
  at all, and a reopened row says why it reopened.
- **It ages badly.** The explanation described a ceiling as if it were a rule, and
  was wrong for any run that did not take exactly that many passes.

The rule this leaves behind: **state that can be shown is not written.** Text on
this screen is reserved for what cannot be shown — the loop's reason, an
enumerated error type, an absent field, a boundary the operator has to respect.

**The prototype scripts the loop per session** rather than fixing it, because the
loop is a property of the run: one demo session takes an extra pass, one takes a
redraft, and a session submitted from the composer — which has no recorded
outcome yet — plays a one-pass accepted script at whatever budget it was given.

The honest limitation: the API reports `iteration` on the session snapshot but not
the route history, so the arcs and the reopen lines are driven by
`graph.route.decided`, `graph.extra_pass.started`, `graph.note_pass.started`,
`graph.report.redraft_requested` and `graph.note_redraft.requested` rather than by the
session. A finished session
opens on its report, which states the passes in plain words (§4); its loops are
not redrawn.

### 3.6 Meter colour is a judgement about the number

The meters — the report rail's review score and scored sources cited, and the four source scores
(authority, recency, relevance, overall) in the Evidence detail pane — carry a figure and a bar. The
bar's colour is **derived from the figure**, in one place, rather than set beside it:

| Figure | Fill | Role |
|---|---|---|
| above `0.80` | green | `--status-ok` |
| `0.40` to `0.80` | yellow | `--status-warn` |
| below `0.40` | red | `--status-danger` |
| no figure at all | grey | `--muted` — absent, not zero |

The boundaries are the operator's, and `0.80` sits in the middle band: the green band is
*above* it. `0.40` sits in the middle band too, since the red band is *below* it.

**Deriving it was the point.** The first version carried the class on one bar by hand — the
first row was green and the other three fell through to the default grey, which read
as "no verdict" for three figures that plainly had one. A fill hand-classed `ok` while its row
reads `0.62` is a disagreement nobody notices until it matters, so `paintMeters()` reads each
row's own number and `meterClass()` is the only place the thresholds exist.

A row with no figure, or with something that is not one, keeps the default fill rather than
falling into a band. That is the same rule §4 applies everywhere else: a value that is absent is
rendered as absent, never as a zero and never as a verdict it did not earn.

The three fills use the product's existing status roles rather than new colours, so the meters
cannot drift away from the rest of the status language.

**`0.80` paints yellow, and that is recorded on purpose.** Review acceptance is `≥ 0.80`
(`SEMANTIC_REVIEW_MEAN` in `utils/types.py`) while the green band starts *above* `0.80`, so a review score or a
cited-sources ratio of exactly `0.80` sits in the middle band. Two fixtures show it: session
`9ea4c220`'s `Scored sources cited` is 4 of 5 = 0.80, and Evidence finding `F03`'s source scores 0.80
overall. Neither side is to be "fixed": the meter states the operator's bands, the reviewer states its
own threshold, and the status text beside the meter says which outcome the run had.

---

## 4. Status mapping

The API's `SessionStatus` is a seven-value literal (`api/models.py`;
`needs_input` joined it with the one-time check, and `stopped`
with Stop). The interface shows seven statuses. This
table is the contract between them, and it is
exhaustive: no status may be invented and none may be dropped.

| Interface status | API `status` | Also read | Token role | Copy shown to the operator |
|---|---|---|---|---|
| **Running** | `running` | the active row from the stream (§3.5) | `--fg` label, `--success` live dot (the one non-text use) | `Running · {step}`, e.g. `Running · Researching` |
| **Waiting for you** | `needs_input` | the check's phase from the stream (`session.clarification.*`, stage 2a), which outranks a `/status` read taken just before it | `--fg` label, `--warn` dot | `Waiting for you · a few quick questions` |
| **Completed** | `completed` | `semantic_review_score`, `coverage.not_found_target_ids` | `--fg` label, `--success` dot | `Completed · review accepted · {score}` + the not-found clause |
| **Partially completed** | `max_iterations` | `coverage` | `--fg` label, `--warn` dot | `Partially completed · extra passes used` + the not-found clause |
| **Partially completed** | `incomplete` with `semantic_review_status == "scored"` | `semantic_review_score` | `--fg` label, `--warn` dot | `Partially completed · not accepted · {score}` |
| **Partially completed** | `incomplete` with any other `semantic_review_status` | — | `--fg` label, `--warn` dot | `Partially completed · review unavailable` |
| **Failed** | `failed` | `errors` | `--fg` label, `--danger` dot | `Failed · halted`; the failed stage headlines the halting type |
| **Stopped by you** | `stopped` | `stopped_step` | `--fg` label, neutral `--muted` dot | `Stopped by you · at {step}`, e.g. `Stopped by you · at Researching`; `at the questions` after a stop during the one-time check |
| **Unavailable** | *not a status* | any `null` field | `--muted` text, no chip, no icon, no control | `not measured`, `not scored`, `Not recorded`, `Not available while running` |

Rules that follow from the table:

1. **`failed` is not the same as "no report".** A halt sets `status="failed"` and
   records a non-recoverable `ResearchError`, but `GET /report` then answers
   `409 report_unavailable`, not 404. The failed screen states the failure and
   then states, separately, that nothing was published.
2. **`completed` is exactly `report_accepted`.** That route reason is the only one
   that yields `completed` (`graph/state.py`), so the chip infers
   acceptance from the status alone and reads the score from
   `semantic_review_score`, printed with two decimals. There is no
   `quality_status` on the response and none is needed.
3. **`max_iterations` and `incomplete` are not degraded states.** They render with
   the same visual weight as `completed` — a different dot colour and an accurate
   clause, never a warning banner, never an apology. The clause is counted, never
   assumed: `n = coverage.not_found_target_ids.length` is omitted at 0, reads
   `· 1 target not found` at 1 and `· {n} targets not found` above 1
   (`max_iterations` can end with an empty list when only the review's own
   coverage defect remained, `extra_pass_target_ids` in `graph/state.py`). `not accepted · {score}` covers
   a passed review too: acceptance needs a scored review with a mean of at least
   0.80 and no material defect, complete coverage, and no quality-gate hard
   failure (`agents/report_reviewer.py`, `graph/state.py`), so a gate
   can block acceptance while the review itself passed, and the clause says the
   report was not accepted rather than that the review failed. The report is
   present and authoritative, and it says itself what it could not confirm.
4. **`unavailable` never becomes a value.** `trace_url: null` is
   `Not available while running` in `--muted`; `report_path: null` is
   `Not published`; token usage is `Not recorded` (its source —
   `total_token_usage` — documents that zero means "no provider reported usage",
   so a summed `0` is not a fact about the run); `evidence_counts: null` is
   `not measured`; `semantic_review_score: null` is `not scored`. No `0`, `—`,
   `null`, empty chip, or greyed-out button stands in for a missing value.
   **A missing value is text, and it is always visible text.**
5. **Status is never carried by colour alone.** Every status has a text label at
   `--text-sm` minimum; the dot is redundant reinforcement, and a duplicated
   colour-vision simulation of the chips is in `states.html`.
6. **The only waiting status is the reader's.** Before the first frame, or
   between `POST /research` and the first event, the screen is *Running* with
   stage `starting`. `needs_input` means the one-time check is waiting for the
   reader's answers, shown as *Waiting for you*; nothing
   else waits, and a slow service never reads as waiting.
7. **The chip can shrink on a narrow viewport; its note cannot wrap.** Below the
   width the full clause needs, the topbar chip's note truncates with an
   ellipsis instead of wrapping or overflowing — the topbar's own height never
   changes — while the fixed status label and its dot stay exactly as visible as
   they are at full width. The clause the ellipsis hides is not lost: the report
   stage's rail states it in full.
8. **`stopped` is the reader's own end, not a failure.** Its dot is neutral (`--muted`),
   never `--danger`, and a stop adds no error. Like a halt it publishes nothing:
   `GET /report` answers `409 report_unavailable` and `/evidence`
   `409 evidence_unavailable`, and every note the run took reads `not checked`.

### Derived stage display

`current_agent` is only populated between `graph.node.started` and the node's
completion, and is `null` from the first `graph.node.completed` of the terminal
node until the response arrives. The stage spine therefore derives its state from
the event stream, not from `current_agent`:

| Node | Label | Row |
|---|---|---|
| `planner` | Planning | 1 |
| `researcher` | Researching | 2 |
| `source_evaluator` | Evaluating sources | 3 |
| `evidence_verifier` | Verifying evidence | 4 |
| `report_writer` | Writing report | 5 |
| `report_reviewer` | Reviewing | 6 |
| `extra_pass` | — | a hop, not a row: the graph returns to 2 |
| `note_pass` | — | a hop, not a row: the graph returns to 2 |
| `writer_redraft` | — | a hop, not a row: the graph returns to 5 |
| `finalize_report` | Publishing | 7 |

`current_agent` is used only as a fallback when the log is empty. A row is `done`
on its own `graph.node.completed` — except Reviewing, whose completion is inert
after a loop decision (§3.5) — and `skipped` on its `graph.node.skipped`; the
active row is the successor of the last completion, with the loop keyed on
`graph.route.decided`. Publishing is marked `skipped` on a halted run by the
client rule `graph.session.completed.status == "failed"` — never by
`has_report`, which can be true when the halt came after a first-pass writer
(`agents/report_writer.py`) — because the graph never runs
`finalize_report` after a halt and no event exists for it.

### The passes, in plain words

The interface never shows a pass number. While a
run is live the chip names the running row — `Running · {step}` — from the stream's
active row (§3.5), or from `/status.current_agent` before the stream opens (a hop
reads as the row it leads back to; with neither, `starting`); after
`graph.session.completed` and until `/status` turns terminal it names the row the run
ended on. After the run the report states how many times the run went back, from
`/status.iteration` (zero-based: an extra pass advances it, a redraft does not), in
the report head bar and as the Session facts' pass fact (`passFact`,
`web/lib/format.ts`):

| `iteration` | `note_passes` | Text |
|---|---|---|
| 0 | 0 | `One research round` |
| 1 | 0 | `Went back once to fill gaps` |
| 2 | 0 | `Went back twice to fill gaps` |
| n > 2 | 0 | `Went back n times to fill gaps` |
| any | k > 0 | the above, plus ` · went back once / twice / k times for your notes` |

`note_passes` is the status response's count of the targeted passes the reader's notes
bought: 0 for a run without notes, and for a status recorded before
notes existed. The one rule the old counter taught still holds: `researcher.tool_call` carries
an `iteration` that is the ReAct step index, never the pass, so nothing reads the
pass from agent events.

### Error rendering

`ResearchError` carries `error_type`, `source`, `message`, `recoverable`,
`timestamp`, `details` (`utils/types.py`). Two levels only:

- `recoverable: true` — **not surfaced while a run is in progress.** A recoverable
  error — a statement-check batch the writer could not judge
  (`report_writer_statement_check_failed`), a context-check batch the verifier
  could not judge (`evidence_verifier_context_check_failed`), a section the
  writer could not draft, a bottom line left with no answer sentence
  (`report_writer_bottom_line_no_answer`) — is the normal case: it never stops the
  run, and the evidence log records what it left unchecked. A live counter was therefore a
  number that asked to be read and told the operator nothing actionable, and the
  card that held it was removed before the rail itself was.
- `recoverable: false` — promoted into the failure panel, with the halting type
  **in plain words** as the headline, the API's own `message` as the sentence
  beneath it, and the enumerated `error_type`, `source`, `recoverable` and
  `report_path` as labelled rows. This is the one place an error is a headline,
  because it is the reason the run ended.

**`message` was being discarded, and that is now fixed.** `graph/errors.py` is explicit that
an error's `message` is curated per enumerated type and is never `str(exception)`. The failed
panel nonetheless carried a fixed sentence, and the rail's Errors card rendered `error_type`
alone — so on both surfaces where errors appear, the one field written as a sentence for a
human to read was the one field not shown. Both now render it, falling back to a static
sentence when an error record carries no message. The fixture data was corrected at the same
time: it had been carrying error types the API does not define, and no `message` on any error.
The fixtures now carry only real types — `report_writer_statement_check_failed`
(`agents/report_writer.py`), `evidence_verifier_context_check_failed`
(`agents/evidence_verifier.py`) and the halting `graph_provider_configuration_error` —
each with its curated message.

**`timestamp` and `details` are still served and still not rendered**, and this section used to
claim otherwise about `details`. It is a flat dict whose shape changes with the error type —
`{"exception_type": …}` for a provider failure, a batch index for a failed check, a part title
for a failed section — so rendering it means deciding how to present arbitrary structured data:
how many array items before truncating, what to do with a nested object, whether an empty dict
shows nothing or a dash. That is a presentation decision rather than an oversight, which is why
it is written down here instead of guessed at. `message` was the same class of gap with an
obvious answer, which is why it was fixed and this was not.

Halting types (`HALTING_ERROR_TYPES` in `graph/state.py`) end the run; everything else is
recoverable and does not. The failed stage headlines each in plain words:

| `error_type` | Headline |
|---|---|
| `graph_planning_failed` | Planning failed |
| `graph_provider_configuration_error` | Model provider misconfigured |
| `graph_agent_configuration_error` | Agent misconfigured |
| `graph_invalid_agent_state` | Invalid agent state |
| `graph_invalid_route` | Invalid route |
| `graph_request_attempt_limit_exceeded` | Request attempt limit reached |

Two more headlines cover a failure the graph never sees: `api/sessions.py`
can mark a session `failed` from the API layer alone — no graph error record, no
`graph.session.completed` — and the client falls back to the session's own `status` to
mark Publishing `skipped` in that case, the same as a graph halt:

| `error_type` | Headline |
|---|---|
| `api.research.configuration_error` | Service configuration error |
| `api.research.failed` | Research run failed |

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
a grid track rather than a named width. The Evidence view reuses the same `--rail`
track for its detail pane, and the running stage has no rail at all, so this revision
added no custom property.

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
  `--motion-fluid: 420ms` for the row surface. The easing is
  `--ease-entrance: cubic-bezier(0.32, 0.72, 0, 1)` — a long settle outward rather
  than an ease-out that lands abruptly. `--motion-lift: 900ms` uses the same easing
  for the same reason: the box has to arrive rather than stop, and at `540ms` a 400px
  rise read as a jump rather than as the gradual move it is meant to be. These, plus
  `--motion-clear: 320ms` and `--motion-dissolve: 200ms`, are the only durations in
  the product deliberately outside the design system's own set; §3.4 explains what the
  pipeline ones carry, and the handoffs above explain the other three.
- **Step briefs open, hand off and tick, once each.**
  Only `transform`, `opacity`, `grid-template-rows` and the check's
  `stroke-dashoffset` animate. A row opens by `grid-template-rows: 0fr → 1fr` over
  `--motion-fluid`, then its lines rise 4px and fade in over 240ms from 280ms, 60ms
  apart; it closes by fading its lines out over 160ms, then closing the height over
  420ms from 160ms. At a hand-off the finished row's lines fade out (180ms, at 0),
  its height closes (at 100ms), its subtitle cross-fades to its outcome (200ms, at
  260ms) and the connector below it fills (`--fill-line`, at 180ms); the next row's
  node fills and its height opens at 600ms and its lines rise from 900ms, so the
  next row opens while the line is still filling (about 1.3s in all). When a route
  decision arrives ahead of the finishing row's own completion (Reviewing), the next
  row starts its opening at the decision and the row it left stays open until its
  completion, then folds with the same finishing-row timings. A finished
  topic draws its ✓ (`stroke-dashoffset` 14 → 0 over 360ms after 80ms) as its dot
  fades (200ms); counts tween to their new value over 400ms. **Under reduced
  motion** heights change at once, lines fade over 160ms with no stagger and no
  rise, the ✓ appears without drawing, counts jump and the halos are pinned at rest.
- **The one-time check moves once per question.**
  Its card arrives as the pipeline card does (`.is-arriving`); a tapped answer shows
  as chosen for 240ms, then the next question fades and rises in (`enter`,
  `--motion-base`), and the summary line arrives the same way. The step dots change
  colour, never size. **Under reduced motion** the next question and the summary fade
  in place over 160ms, with no rise.
- **A note's acknowledgement rises in once.** A new
  `.ack` line starts from its `@starting-style` and rises 4px as it fades in over
  200ms: at once in a row that is already open, and in its place in the stagger in
  the row a hand-off is opening. **Under reduced motion** it fades in place over
  160ms, like every brief line.
- **A contents jump scrolls, then lands on the heading.** Choosing an entry in the report's contents marks it current, scrolls its
  card to the top — `scroll-margin-top` is the topbar, the chip row's 56px and
  `--space-4` — smoothly, and moves focus to the card's heading, which shows no ring.
  **Under reduced motion** the scroll is instant (`behavior: "auto"`); the current entry
  and the focus move the same way, and the chip row scrolls its current chip into view
  without animation either way.
- **Each step's own brief changes in place.**
  A status line, a slot's title and a check's fact cross-fade: opacity over
  `--motion-base` and a 5px settle over `--motion-fluid`. A slot or a note's slot that
  arrives later rises in as an acknowledgement does; a surplus skeleton fades out,
  then leaves the layout. A determinate bar fills over `--fill-line`. A ticker's sample
  changes at most once every 1,200ms (`TICKER_HOLD_MS`, a dwell like the hand-off's
  hold, not an animation), and a burst ends on its newest sample. When a review lands
  its checks change 60ms apart. **Under reduced motion** every cross-fade is opacity
  only over 160ms with no settle, a skeleton leaves at once, the bars jump, the ✓ and
  ✗ appear without drawing and counts jump.
- **The tickers and WCAG 2.2.2.** A ticker changes its sample on its own, at most
  once every 1,200ms, and has no pause. Success criterion 2.2.2 (Pause, Stop, Hide) asks for
  a way to pause information that updates automatically, unless the updating is essential.
  The ticker does not meet that exception on its own: what it shows is also available another
  way, since the row's subtitle, bar and tally carry every count a sample illustrates. Having
  no pause control is therefore an accepted risk, not a claim of conformance. Three things limit it: the ticker is not an
  `aria-live` region (the only live region in the running spine is a note's acknowledgement),
  so a screen reader is not interrupted at each sample and reads one only when the reader
  reaches it; its change is a cross-fade, which under reduced motion is opacity only over
  160ms with no 5px settle; and every count is also in the row's subtitle, bar and tally. If
  the risk is ever to be closed, the remedy is to hold the samples while the pointer is over
  the ticker or focus is inside it, with a keyboard "Pause samples" toggle. The three
  decorative loops below are a separate matter: they stop under reduced motion.
- **A loop route holds the verdict.** When the route sends the run
  back, Reviewing stays open on its checks and its verdict for `HANDOFF_HOLD_MS`
  (2,000ms) before the hand-off runs (§3.5). The hold is a dwell on a timer, like the
  hand-off's own hold, not an animation, so it is kept under reduced motion; the fold
  and the open that follow are the hand-off's, opacity only under reduced motion.
- **Three decorative loops, none load-bearing.** `halo` runs at
  `--motion-halo: 2200ms` on the running node, on each running topic's and slot's dot
  and on the header status dot; Planning's skeleton sheen and Reviewing's drift run
  at the same duration, each only while its step runs (§3.4). All three stop under
  reduced motion, and §3.4's table is identical either way — no state on this screen
  depends on an animation being mid-cycle. A blob that travelled down the running row
  was tried and removed; §3.4 records why.

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

**Under reduced motion** neither handoff's *journey* runs. No flight box is created and the
composer keeps its own frame (no `is-handing-off`); the report slide never applies a new offset.
What does still run, in both handoffs, is the surrounding beat: page 1 is still drained
(`is-clearing` still applies, on its own `--motion-clear` timing — see "the submitted beat" above,
320 + 200 + 750 = 1270ms with no lift) and page 2 is still held back with `is-preparing` until that
beat ends, the same as with motion. Only the *travel* is what the preference removes, not the
beats either handoff is built from; `showStage` cross-fades the pair in both motion modes, and
under the preference that cross-fade is opacity-only.

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

The stream is consumed, not rendered. The running stage shows what it derives
from it — the spine's row states, the open row's live brief (§3.4), each finished
row's outcome line and the arcs — and nothing else. A raw log is deliberately
absent from the main region.

What each surface derives, and from which events (`graph/events.py`,
`agents/*.py`): the active row from `graph.node.completed` and
`graph.route.decided`; the arcs from `graph.route.decided`,
`graph.extra_pass.started`, `graph.note_pass.started`, `graph.report.redraft_requested`
and `graph.note_redraft.requested`, which also give a reopened row its first line; Researching's checklist from
`planner.planning.completed.sub_topics` (titles, in plan order) and
`researcher.sub_topic.started` / `.completed` (by `coverage_id`), and its live facts
line from the completed topics' `successful_reads` and `findings_retained` until
`researcher.research.completed.findings`; the outcome lines from
`planner.planning.completed`, `researcher.research.completed`,
`source_evaluator.evaluation.completed`, `evidence_verifier.verification.completed`,
`report_writer.report.written`, `graph.report.reviewed` with `graph.route.decided`,
and Publishing's own `graph.node.completed`; the chip's step from the active row;
the failed stage's skipped rows from `graph.node.skipped` and its Publishing row
from `graph.session.completed`. Each step's own brief (§3.4) reads its progress event —
`planner.progress` (Planning's status line and slots, then `planner.planning.completed`'s
final slot states and note slots), `source_evaluator.progress` (the bar and the split),
`evidence_verifier.progress` and `report_writer.progress` (the ticker, the bar and the
tally) — each a cumulative snapshot whose latest wins; Reviewing reads
`graph.report.reviewed`'s `criteria` and `notes`, its verdict from `graph.route.decided`
(with the latest `graph.quality.assessed` for a refusal), and every row's elapsed time
and duration from its `graph.node.started` and `graph.node.completed` timestamps.

**Delivery is live.** Each event reaches the stream as it happens: `graph.node.started`
is published live when an agent node or the reviewer starts (Publishing and the hop
nodes keep snapshot publication); each agent's progress events are published as the
agent builds them — the researcher's `researcher.sub_topic.started`,
`researcher.tool_call` (built when its step's observation is recorded) and
`researcher.sub_topic.completed` while its topics run, concurrently; the four step
progress events (`planner.progress`, `source_evaluator.progress`,
`evidence_verifier.progress`, `report_writer.progress`) as each unit of work starts or
settles; and the reviewer's `graph.report.reviewed` the moment its review returns. The
step progress events are live-only: they are never in the run's state, so a checkpoint,
the quality record and `graph.node.completed.event_count` never hold them, and a
reconnect replays them from the session's own log. Events that are not published
live — the graph's route, hop and completion events, and
`researcher.research.completed` — arrive with their node's snapshot, and an id already
published live is never published twice (`graph/live.py`, `graph/orchestrator.py`;
api-gaps 3.7, closed). The screen therefore moves within a node: the Researching
checklist ticks topics off as they finish. Every derivation above is still written so
the state after event *k* depends only on events 1..*k*: a live run, a 100-event replay
and a reconnect's burst paint the same screen.

This is a product judgement, stated so it can be overruled: a run emits well over
100 events, the operator's question is "is it progressing and what has it found",
and a tail answers neither better than a stage spine whose running row carries its own counts does. The
event stream stays the source of truth for the derived values, so nothing is lost
that a future log view could not reintroduce.

### 5.8 Counted from the event stream

The running stage no longer carries the counters block; each row counts for itself, in its live subtitle while it runs and its
outcome line once it is done (§3.4). The Failed and service-stopped stages keep the block,
with the eyebrow `counted from the event stream`, as what survived the halt. Its
rows, each with its scope:

| Row | Scope | From |
|---|---|---|
| sub-topics researched | this pass | `{count} this pass` while only `researcher.sub_topic.completed` events have arrived; then `{researched} of {researched + skipped}` from `researcher.research.completed` |
| tool calls | whole run · researcher only | the number of `researcher.tool_call` events |
| findings | this pass | `researcher.research.completed.findings` |
| sources scored | whole run | `source_evaluator.evaluation.completed.source_count` |
| verified / corrected / dropped | this pass | `evidence_verifier.verification.completed` |
| sentences / refused | current draft | `report_writer.report.written.statements` / `.refused` |
| review score | latest review | `graph.report.reviewed.mean_score`, two decimals; muted `not scored` when null |

Rules: the block is derived from the same stream and the same handlers as the
running stage, frozen where the run stopped; a row whose node never ran reads
`not reached`, never `0`; on `graph.extra_pass.started` the this-pass rows reset
and the block's caption reads `pass p`; on `graph.note_pass.started` the this-pass
rows reset too, and the caption keeps its pass (a note pass is not an extra pass);
on `graph.report.redraft_requested` the current-draft rows and the review score
reset.

The block is honest where the earlier cost card was not: each row names the pass
or draft it counts. Token usage is still `Not recorded`: totals are **not** in
`ResearchSessionResponse` — they live in `ResearchOutcome.token_usage`, computed
at the end of the run — and the report stage's "Cost and usage" card says so.
Nothing is ever rendered as `0`: `total_token_usage` already documents that a
zero total means "no provider reported usage", so a rendered `0` would assert
something the API never said, and the stream-derived, researcher-only tool-call
count is not copied into that card.

---

## 6. What each stage needs that the API does not serve

Full detail, with the request shape each gap implies, is in
[`api-gaps.md`](./api-gaps.md). Summary, keyed to the six stages of §3:

| Stage | Blocked by |
|---|---|
| Evidence (every stage) | **E1** — no `GET /research/{id}/evidence`: the Evidence view, the `Download evidence log` button and coverage's question text are prototype-only until it exists |
| Idle | no effective-settings echo; no `/capabilities`; no `/health` |
| Submitted | nothing beyond Idle |
| Check | nothing: `needs_input`, the two `session.clarification.*` events and `POST /research/{id}/answers` serve it |
| Notes | nothing: `POST /research/{id}/notes`, the two `session.note.*` events and the status's `notes`, `notes_remaining` and `note_passes` serve them; on the replay server a note is acknowledged but never applied, and the report prints no line for it (api-gaps 3.9) |
| Stopped | nothing: `POST /research/{id}/stop`, the `stopped` status with `stopped_step`, and `session.stopped` serve it |
| Running | no token usage; no terminal frame; no `Last-Event-ID` resume (events carry an `event_id`, but a reconnect replays from event 1); the halting vocabulary is a client copy; shutdown leaves `running` |
| Report | Markdown only (a JSON projection is a nice-to-have now that the format is stable); no report hash on the response |
| Failed | what survived a halt comes only from the stream; the halted state still needs a seeded session |
| Sidebar | no result summary per row; no durable store (`GET /research` lists only what the process holds) |

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
`finish()`, `stage()`, `motion()` and the `sessions` ledger, plus
`view(name?)` (switches or reports the `Report | Evidence` toggle),
`evidence(sessionId)` (opens a session on its Evidence view),
`advanceTo(eventType)` (plays the active playback session's script synchronously
up to and including the first event of that type, then pauses), `arc()`
(`"extra_pass"`, `"redraft"` or `null`) and `loop()` (`"off"`, `"flowing"` or
`"settled"`). Any stage and either arc can be reached directly without watching a
run. Jumping to a stage through the hook uses the plain stage transition rather
than a handoff, which is the correct behaviour and also the quickest way to see
the two side by side: toggle `prefers-reduced-motion` and repeat either handoff
to confirm that no state information lives in the motion.

**The fixture sessions** are shaped like `ResearchSessionResponse`, with
every question from the battery-storage set:
`8f2c1d90` (playback; a pass-0 review names two missing targets → extra pass →
accepted at 0.86 on pass 1), `c3d7e5f1` (playback; a pass-0 review names one
material defect → redraft → re-review accepts at 0.84), `b41e77aa` (`completed`
after its extra pass was spent, one target not found — the shape of the replay
case `extra-pass-finds-nothing`), `7c0d13ff` (`max_iterations`, one target not
found), `5ff1ab07` (`incomplete`, not accepted at 0.71, budget 0), `9ea4c220`
(`incomplete`, review unavailable; its `Scored sources cited` is 4 of 5 = 0.80,
painted yellow) and `2ad900b1` (`failed`, `graph_provider_configuration_error` in
the planner). The halted fixture carries a static `HALTED_EVENTS` list —
`graph.session.started`, `graph.node.started` for the planner, five
`graph.node.skipped`, `graph.session.completed` with `status: "failed"` —
consumed once by the failed-stage renderer through the same event handlers the
running stage uses, never played. The two playback fixtures share one `play`
state: opening one restarts its script from the beginning, and the other keeps
`status: "running"` until it is reopened. Finishing a playback run, whether via
`finish()` or by letting its script play to the end, sets its duration from the
simulated run clock rather than wall time: `finishPlayback` rounds
`play.elapsed` — the same figure the running header shows as elapsed — into
`durationSeconds`, and derives `finished` by adding that duration to `started`.

**The Evidence fixture** (`EVIDENCE.default`) is one set in the shape api-gaps
E1 proposes, for the one battery-storage report: six findings covering every
status (including one with no verification, shown only under `All`), one
not-found target (`T04`) with its queries and pages read, and one refused
sentence citing `F03`. Every value is one the engine can produce — figure
attribution `own`/`relayed`, kind `actual`; here the quoted finding (`F03`) and
the dropped one (`F04`) both carry no figures because `F04`'s drop is
`snippet_not_on_page`, before the Context Check runs — not a blanket rule (an
`all_figures_dropped` finding carries every figure instead, api-gaps E1) — and
the `context unchecked` flag on a verified finding with a kept figure
(`F06`). `F03`'s source scores 0.80 overall, so its `overall` meter paints yellow
(§3.6).

`prototype/states.html` is the review companion from the previous pass. It stays
useful as the rendering contract for the states the console reaches only in
unusual circumstances — empty, configuration error, session failed, the three
partial outcomes, an options table wide enough to scroll — and it carries the
status-mapping table rendered as live chips, so §4 can be read against the
actual pixels. It is not part of the app.

Both files are self-contained: no build step, no external scripts, no network
fetches, no web fonts. All colour, type, space, radius and motion values resolve
through the design system's custom properties; the only literal colours in either
file are inside the single `:root` block. The sidebar's collapsed/expanded choice
and the last-opened session persist to `localStorage`
(`dr.console.sidebar`, `dr.console.active`), and the page uses `window.scrollTo`
rather than `scrollIntoView`.

`docs/design/prototype/` still shows the running stage's counters block and the
composer's `extra passes` pill, which the app's running stage and composer no longer draw.

At ≤ 900px the
composer bar hides its `thinking` pill (`#pillThinking`) and keeps only the
`model` pill, thinking staying visible in the settings panel and the settings
strip. On the
report stage the `Report | Evidence` toggle takes the right end of the head bar; `#reportMeta` is the element that
shrinks and wraps to make room (`flex:1 1 240px;min-width:0`), so the toggle
and the two action buttons stay on the bar's one row.

**Three working behaviours are scripted rather than wired, and the UI no longer says so about
all three.** The run is driven by an event sequence whose names and metadata keys are the
engine's own (checked offline against two replayed runs) rather than a live
`EventSource`; the sidebar is a client ledger because the prototype has no API behind it (the app lists `GET /research`, §3.1); and the
report body is a hand re-composition in the consumer format, reused
for every completed session.

That last one used to be labelled inside the report card — a note explaining that the body is a
fixture and that the app replaces it with the body returned from `GET /research/{id}/report`.
The operator asked for the note to go, and it has. **The limitation did not go with it:** what the
report stage shows is the same document for every session, and nothing on screen says so now. It
remains recorded here and in `api-gaps.md`, and it is the one thing in this file that should
probably be disclosed on screen again — briefly — wherever the prototype is shown to someone who
did not build it.
