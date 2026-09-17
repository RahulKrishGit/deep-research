# Task 3 report — acquisition produces target-bearing read evidence

## Outcome

Task 3 is implemented on `codex/agent-cli-quality-trace-plan` at the
controller-provided baseline `dfb8f7776b11990b294e225d6a27c4cd34795868`.
The implementation keeps the Task 1 read/evidence contract as the only path
to admissible evidence and adds the deterministic acquisition queue, native
proposal accounting, complete decision context, passage selection, shared
successful-read reuse, and Researcher production wiring.

The dispatch routing was `gpt-5.6-luna` with reasoning effort `max`. The
dispatch interface exposed no separate fast-mode flag, so none is claimed.
All verification was offline with scripted providers and fake clients; no
live or paid provider call was made. Task 6's real FactChecker integration is
not claimed here.

## Implemented contract

- `AcquisitionState` and `CandidateRecord` are typed, canonical-URL keyed,
  persisted in `ResearchState`, and merged without resurrecting consumed
  candidates. Pending passage/extraction IDs and separate model-turn and
  external-call capacity are retained.
- `next_acquisition_action` follows the exact ruled ordering. `run_react_loop`
  accepts an immediate pre-execution policy for every native batch call,
  keeps policy rejections out of external budget accounting, and stamps each
  proposal with a local `job/turn/call` ID on `ReActStep`.
- Successful web/document reads are admitted once from their complete body
  through `build_read_record` and `passages_from_chunks`; failed payloads,
  empty-document failures, access shells, and malformed content never mint a
  read. Each read and selection emits a boundary audit. Partial reads remain
  citable but are not cache-seeded.
- Selection scans all extracted chunks with deterministic query variants,
  late-page/paragraph/data support, and deferred locator IDs. The packet is
  complete-record based and target-local, with explicit candidate/read/status,
  evidence locator, pending-work, capacity, disposition, and continuation
  fields; the public observation summary remains 200 characters.
- The run-level read registry is shared across Researcher acquisition passes.
  Cache admissions reselect target-local passages without a second network
  body or corroboration credit. A changed body gets a new read identity and
  stale target evidence is explicitly disposed. URL suffix, MIME, redirect,
  denied-page, and same-host document routing are bounded and exact-URL
  scoped.
- Researcher extraction requires admitted read IDs, exact locators, exact
  excerpts, and target IDs. Events expose proposal IDs, successful-read
  counts, useful evidence yield, and publisher/URL/work/finding retention
  counters. Config and assembly carry the 4-passage / 24,000-character
  defaults.

## TDD evidence

Representative RED evidence was captured before each production seam was
completed:

1. The policy test first failed with
   `TypeError: run_react_loop() got an unexpected keyword argument 'tool_policy'`.
   This was expected because the native loop had no pre-execution policy hook.
2. The fresh BaseAgent decision-context test failed before the hook was added;
   the next provider request had no context callback path. The redirect test
   likewise failed before resolved-suffix dispatch, and the reducer test
   observed a consumed queued URL being resurrected by the state merge.
3. The green focused boundary run after those fixes was:

   ```text
   python -m pytest tests/test_agents/test_acquisition.py tests/test_agents/test_react.py tests/test_agents/test_steps.py tests/test_agents/test_researcher.py tests/test_agents/test_native_react_boundary.py tests/test_agents/test_planner_researcher_seam.py tests/test_tools/test_passage_selection.py tests/test_tools/test_document_reader.py tests/test_tools/test_web_scraper.py tests/test_config.py tests/test_runtime/test_assembly.py -q
   487 passed, 1 warning in 3.02s
   ```

   The BaseAgent hook suite was:

   ```text
   python -m pytest tests/test_agents/test_base.py -q
   32 passed in 0.32s
   ```

## Verification

Every pytest command was run from this worktree with its `src` directory first
on `PYTHONPATH`.

- Full offline suite: `python -m pytest -q` → `3433 passed, 1 deselected,
  2 warnings in 50.85s`.
- Ruff: `python -m ruff check src tests` → `All checks passed!`.
- Diff hygiene: `git diff --check` → clean; Git emitted only the expected
  Windows LF/CRLF normalization warnings.
- The full-suite prompt pins were deliberately updated after the required
  shared renderer change (`tests/test_evaluation/test_config.py`). The live
  Researcher fingerprint observed by the conformance test is
  `bebb16cebfe9`; the other shared-renderer pins were updated to their
  observed values.

## Files changed

Production: `config.yaml`; acquisition, BaseAgent, native ReAct, Researcher,
prompt, and step modules; persisted state/types; runtime assembly; document
reader; and public exports. New modules are
`src/deep_research/agents/acquisition.py` and
`src/deep_research/tools/passage_selection.py`.

Tests cover acquisition, passage selection, BaseAgent context, document
redirect routing, configuration, and the existing native-loop/researcher/tool
regressions. The only additional test path outside the brief's focused list is
the existing evaluation fingerprint pin file, changed solely to acknowledge
the required prompt packet interface drift.

## Self-review and concerns

- No provider/vendor call IDs were invented; proposal IDs are local and
  deterministic. No live FactChecker cross-agent assertion was added.
- Cache reuse intentionally reports useful local target yield separately from
  acquired network work; cache hits do not create a second network read or
  corroboration source.
- Existing unrelated baseline warnings remain: the LangSmith `ast.Str`
  deprecation and Starlette/httpx deprecation. They do not affect the gates.
