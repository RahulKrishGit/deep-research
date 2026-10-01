// Mirrors src/deep_research/api/models.py (+ query, SessionListResponse, the E1 models).
/* "needs_input": the one-time check is waiting for the reader (live-briefs spec §4.4); not terminal.
   "stopped": the reader stopped the run (notes-progress-report spec §8); terminal, nothing published. */
export type SessionStatus = "running" | "needs_input" | "completed" | "max_iterations" | "incomplete" | "failed" | "stopped";
export type ApiMode = "live" | "replay";

/* No `max_iterations`: the console never sends one, so the API uses the configured extra-pass
   budget (live-briefs spec §4.2, D15). The API itself still accepts the field.
   `ask_clarifying_questions` is the settings row "Ask me when the question is unclear" (D16). */
export interface ResearchRequest {
  query: string;
  output_format: "markdown";
  config_overrides: Record<string, unknown>;
  ask_clarifying_questions: boolean;
}
export interface ResearchError {
  error_type: string; source: string; message: string; recoverable: boolean; timestamp: string;
  details: Record<string, unknown>;
}
export interface CoverageProgress {
  required_targets: number; answered_targets: number;
  missing_required_target_ids: string[]; not_found_target_ids: string[];
}
export interface EvidenceCounts {
  read_records: number; network_reads: number; cache_reads: number; unique_works: number; publishers: number;
  source_urls: number; findings: number; assessed_sources: number; cited_assessed_sources: number;
  verified_findings: number; corrected_findings: number; quoted_findings: number; dropped_findings: number;
  context_unchecked_findings: number; cited_findings: number;
}
/* notes-progress-report spec §7.5: one "## " heading of the published report, in order. */
export type ReportOutlineKind = "bottom_line" | "topic" | "key_figures" | "options" | "not_confirmed" | "sources";
export interface ReportOutlineEntry {
  heading: string; kind: ReportOutlineKind; label: string;
  topic_index: number | null; topic_count: number | null; note_id: string | null;
}
export interface ResearchSessionResponse {
  /* The published report's headings (spec §7.5, §7.6). Optional because a response recorded before
     the report became cards (the replay captures under test/fixtures) carries none. */
  report_outline?: ReportOutlineEntry[] | null;
  session_id: string; query: string; status: SessionStatus; current_agent: string | null; iteration: number;
  started_at: string; finished_at: string | null; report_path: string | null; trace_url: string | null;
  errors: ResearchError[];
  evidence_path: string | null; quality_path: string | null; quality_contract_version: string | null;
  semantic_review_status: string | null; semantic_review_score: number | null; duration_seconds: number | null;
  coverage: CoverageProgress | null; evidence_counts: EvidenceCounts | null;
  /* live-briefs spec §4.6: the reader's notes, how many more the run takes (D11a), the note passes
     it bought and the one-time check it asked. Optional because a response recorded before notes
     existed (the replay captures under test/fixtures) carries none of them. */
  notes?: ReaderNoteRecord[]; notes_remaining?: number; note_passes?: number; clarification?: ClarificationRecord | null;
  /* notes-progress-report spec §8.4: the step the reader stopped the run at — "check" or a pipeline
     row's node id — on a stopped session, null otherwise. Optional because a response recorded before
     Stop existed carries none. */
  stopped_step?: string | null;
}
/* One accepted note: as the reader wrote it, the run's reading once interpreted, and what the run
   concluded — "not_addressed" when the report still does not follow it after its one redraft; with
   nothing to judge it by, "pending" while the session goes on and "not_checked" once it has ended
   (notes-progress-report spec §4 item 2). */
export type ReaderNoteOutcome = "covered" | "not_found" | "not_addressed" | "pending" | "not_checked" | "replaced";
/* steering_outcome (notes-progress-report spec §5.6, D20): a mixed note's steering half; null for every other
   note, and absent from a response recorded before the field existed. */
export interface ReaderNoteRecord { note_id: string; text: string; restatement: string | null; outcome: ReaderNoteOutcome; steering_outcome?: ReaderNoteOutcome | null }
export interface ClarificationRecord {
  questions: { id: string; dimension: string; text: string; short: string; options: string[]; best_guess: string }[];
  answers: { question_id: string; value: string; source: "chosen" | "typed" | "best_guess" }[];
}
/* POST /research/{id}/notes answers 202 with the note's id; its reading follows on the stream. */
export interface NoteAcceptedResponse { note_id: string; status: "received" }
export interface SessionListResponse { sessions: ResearchSessionResponse[] }
/* POST /research/{id}/answers (api/models.py ClarificationAnswersRequest): each answer carries
   exactly one of an offered choice or the reader's own text (at most 200 characters). */
export type ClarificationAnswer = { question_id: string; choice: string } | { question_id: string; text: string };
export interface ClarificationAnswersRequest { answers: ClarificationAnswer[]; skip: boolean }
/* `event_id` is the event's identity (live-briefs spec E1); the web app does not read it yet. */
export interface ResearchEvent {
  event_type: string; source: string; message: string; timestamp: string; metadata: Record<string, unknown>; event_id: string;
}
export interface ValidationIssue { location: string; type: string }
export interface ApiErrorBody {
  code: string; message: string; reason: string | null; issues: ValidationIssue[];
  target?: string; // the proxy's 502 api_unreachable carries the API origin here
}
export interface EvidenceSource {
  url: string; title: string; organisation: string; evaluation_status: string | null; low_confidence: boolean;
  authority_score: number | null; recency_score: number | null; relevance_score: number | null; overall_score: number | null;
}
export interface EvidenceFigure {
  value: string; kept: boolean; period: string | null; scope: string | null; organisation: string | null;
  attribution: "own" | "relayed" | "unattributed" | null; kind: "actual" | "forecast" | null; release: string | null;
  evidence_words: string | null; corrected: boolean; dropped_reason: string | null; reason: string | null;
}
export interface EvidenceFinding {
  label: string; status: "verified" | "verified_corrected" | "quoted" | "dropped" | null; dropped_reason: string | null;
  context_unchecked: boolean; cited: boolean; target_ids: string[]; content: string; snippet: string | null;
  passage: string | null; source: EvidenceSource; figures: EvidenceFigure[];
}
export interface EvidenceNotFound { target_id: string; question: string; queries: string[]; pages_read: string[]; searched: boolean }
export interface EvidenceRefused { where: string; text: string; reason: string; finding_labels: string[] }
export interface EvidenceResponse {
  session_id: string; iteration: number; findings: EvidenceFinding[]; not_found: EvidenceNotFound[]; refused: EvidenceRefused[];
}

// ── the client ─────────────────────────────────────────────────────────────
// Every call goes to the app's own origin (/api/*, the proxy of app/api/[...path]/route.ts),
// never caches, and maps any non-2xx to ApiError — or ApiUnreachableError for the proxy's
// 502 api_unreachable. Nothing here retries; a POST is exactly one fetch.

export class ApiError extends Error {
  constructor(readonly status: number, readonly body: ApiErrorBody) { super(body.message); this.name = "ApiError"; }
}
export class ApiUnreachableError extends Error {
  constructor(readonly target: string) { super(`Research service not reachable at ${target}`); this.name = "ApiUnreachableError"; }
}
export interface ApiResult<T> { data: T; mode: ApiMode | null }

export function modeOf(response: Response): ApiMode | null {
  const m = response.headers.get("x-deep-research-mode");
  return m === "live" || m === "replay" ? m : null;
}
async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  if (response.ok) return response;
  let body: ApiErrorBody | null = null;
  try { body = ((await response.json()) as { error?: ApiErrorBody }).error ?? null; } catch { body = null; }
  if (body?.code === "api_unreachable") throw new ApiUnreachableError(body.target ?? "");
  throw new ApiError(response.status, body ?? { code: `http_${response.status}`, message: response.statusText || "Request failed.", reason: null, issues: [] });
}
const id = (sessionId: string) => encodeURIComponent(sessionId);
export const reportUrl = (sessionId: string) => `/api/research/${id(sessionId)}/report`;
export const evidenceMarkdownUrl = (sessionId: string) => `/api/research/${id(sessionId)}/evidence?format=markdown`;
export const streamUrl = (sessionId: string) => `/api/research/${id(sessionId)}/stream`;

export async function startResearch(body: ResearchRequest): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request("/api/research", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
export async function getStatus(sessionId: string): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/status`);
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
export async function listSessions(limit = 50): Promise<ApiResult<SessionListResponse>> {
  const r = await request(`/api/research?limit=${limit}`);
  return { data: (await r.json()) as SessionListResponse, mode: modeOf(r) };
}
export async function getReport(sessionId: string): Promise<ApiResult<string>> {
  const r = await request(reportUrl(sessionId));
  return { data: await r.text(), mode: modeOf(r) };
}
export async function getEvidence(sessionId: string): Promise<ApiResult<EvidenceResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/evidence`);
  return { data: (await r.json()) as EvidenceResponse, mode: modeOf(r) };
}
/* The one-time check's answers, posted once (live-briefs spec §4.5): a 409 not_waiting_for_input
   means the check already started on best guesses. */
export async function submitAnswers(sessionId: string, body: ClarificationAnswersRequest): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/answers`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
/* One reader note, posted once (live-briefs spec §4.6-§4.7): 409 notes_closed once publishing has
   begun, 409 note_limit_reached past the tenth note. */
export async function addNote(sessionId: string, text: string): Promise<ApiResult<NoteAcceptedResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/notes`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
  return { data: (await r.json()) as NoteAcceptedResponse, mode: modeOf(r) };
}
/* Stop a session, posted once with no body (notes-progress-report spec §8.1): 202 with the stopped
   session; 409 not_stoppable once it has ended, is publishing or the service is closing. */
export async function stopResearch(sessionId: string): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/stop`, { method: "POST" });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
