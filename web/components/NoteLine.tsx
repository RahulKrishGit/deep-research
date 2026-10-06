"use client";
import { useEffect, useRef, useState } from "react";
import { ApiError, addNote } from "@/lib/api";
import { NOTES_CLOSED, NOTE_FIELD_LABEL, NOTE_MAX_CHARS, NOTE_PLACEHOLDER, NOTE_SEND_FAILED, NOTE_SEND_LABEL } from "@/lib/notes";

/* The note line: the last element in the pipeline card, under a hairline — a borderless field and a
   neutral icon send, never the purple primary. Enter sends. While the POST is in flight the field is
   read-only. With no note left to take (`remaining` is 0, or a 409 note_limit_reached) the field and
   the button are disabled, with no message and the placeholder unchanged. Once
   `finalize_report` has started (409 notes_closed) one caption, a status, takes the line's place;
   if focus was on the line (the field, or the send button) it moves to the caption rather than
   falling to the page, as the clarify card's does. Any other failure (a 5xx, a network error) keeps
   the text and shows one caption under the field, "Couldn't send — try again", until the reader
   edits the note or sends it again. */
export function NoteLine({ sessionId, remaining }: { sessionId: string; remaining: number }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [closed, setClosed] = useState(false);
  const [full, setFull] = useState(false);
  const [failed, setFailed] = useState(false);
  const disabled = full || remaining <= 0;
  const line = useRef<HTMLDivElement>(null);
  const caption = useRef<HTMLParagraphElement>(null);
  const hadFocus = useRef(false);
  useEffect(() => { if (closed && hadFocus.current) caption.current?.focus(); }, [closed]);
  if (closed) return <div className="note-line" id="noteLine" data-closed="1"><p className="cap" id="noteClosed" role="status" tabIndex={-1} ref={caption}>{NOTES_CLOSED}</p></div>;
  const send = async () => {
    const body = text.trim();
    if (!body || sending || disabled) return;
    setSending(true);
    setFailed(false);
    try {
      await addNote(sessionId, body);
      setText("");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.body.code === "notes_closed") {
        hadFocus.current = !!line.current?.contains(document.activeElement);
        setClosed(true);
      } else if (error instanceof ApiError && error.status === 409 && error.body.code === "note_limit_reached") setFull(true);
      else setFailed(true);
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="note-line" id="noteLine" data-failed={failed ? "1" : undefined} ref={line}>
      <input className="tx" id="noteInput" type="text" aria-label={NOTE_FIELD_LABEL} placeholder={NOTE_PLACEHOLDER} maxLength={NOTE_MAX_CHARS}
        value={text} readOnly={sending} disabled={disabled} autoComplete="off"
        onChange={(event) => { setText(event.target.value); setFailed(false); }}
        onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
      <button type="button" className="icon-btn" id="noteSend" disabled={disabled} onClick={() => void send()}>
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>
        <span className="sr">{NOTE_SEND_LABEL}</span>
      </button>
      {failed ? <p className="cap" id="noteFailed" role="status">{NOTE_SEND_FAILED}</p> : null}
    </div>
  );
}
