// Past conversations, newest first, grouped by day, and the button for a new question.
// Each one can be deleted: the bin asks once more, then the row folds away.

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { SessionSummary } from "../types";
import { CloseIcon, PenIcon, TrashIcon } from "./icons";

type Props = {
  sessions: SessionSummary[];
  current: string | null;
  open: boolean;
  onClose: () => void;
  onOpen: (id: string | null) => void;
  onDelete: (id: string) => void;
};

function dayGroup(iso: string): string {
  const day = new Date(iso);
  const today = new Date();
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((start(today) - start(day)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "This week";
  return "Earlier";
}

export function Sidebar({ sessions, current, open, onClose, onOpen, onDelete }: Props) {
  const groups = new Map<string, SessionSummary[]>();
  for (const s of sessions.filter((s) => s.title)) {
    const group = dayGroup(s.updated_at);
    groups.set(group, [...(groups.get(group) ?? []), s]);
  }
  return (
    <aside className={`sidebar${open ? " sidebar--open" : ""}`} aria-label="Conversations">
      <div className="sidebar__top">
        <a className="wordmark" href="/" onClick={(e) => { e.preventDefault(); onOpen(null); }}>
          <span className="wordmark__seal" aria-hidden="true" />
          AskVisa
        </a>
        <button type="button" className="icon-button sidebar__close" onClick={onClose} aria-label="Close the list">
          <CloseIcon />
        </button>
      </div>
      <button type="button" className="button new-question" onClick={() => onOpen(null)}>
        <PenIcon size={18} /> New question
      </button>
      <nav className="history">
        {[...groups].map(([group, items]) => (
          <section key={group}>
            <h2 className="history__group">{group}</h2>
            <ul>
              <AnimatePresence initial={false}>
                {items.map((s) => (
                  <HistoryRow
                    key={s.id}
                    session={s}
                    current={s.id === current}
                    onOpen={() => onOpen(s.id)}
                    onDelete={() => onDelete(s.id)}
                  />
                ))}
              </AnimatePresence>
            </ul>
          </section>
        ))}
        {groups.size === 0 && <p className="history__empty">Your conversations will appear here.</p>}
      </nav>
      <p className="sidebar__credit">
        Employer and occupation data from <a href="https://permtracker.app" target="_blank" rel="noreferrer">PERM Tracker</a>.
      </p>
    </aside>
  );
}

type RowProps = { session: SessionSummary; current: boolean; onOpen: () => void; onDelete: () => void };

/** One conversation in the list. The bin turns the row into "Delete?" with Delete and Keep. */
function HistoryRow({ session, current, onOpen, onDelete }: RowProps) {
  const [confirming, setConfirming] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);

  return (
    <motion.li
      layout={!reduce}
      className={`history__row${confirming ? " history__row--confirming" : ""}`}
      exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0, x: -12, transition: { duration: 0.22, ease: [0.3, 0, 0.2, 1] } }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && confirming) setConfirming(false);
      }}
    >
      {confirming ? (
        <div className="history__confirm" role="group" aria-label={`Delete "${session.title}"?`}>
          <span className="history__confirm-text">Delete for good?</span>
          <button type="button" className="history__confirm-delete" onClick={onDelete}>
            Delete
          </button>
          <button type="button" ref={keep} className="history__confirm-keep" onClick={() => setConfirming(false)}>
            Keep
          </button>
        </div>
      ) : (
        <>
          <a
            href={`/c/${session.id}`}
            className={`history__item${current ? " history__item--current" : ""}`}
            aria-current={current ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); onOpen(); }}
          >
            {session.running && <span className="history__running" aria-label="Answering" title="Answering" />}
            {session.title}
          </a>
          <button
            type="button"
            className="history__delete"
            onClick={() => setConfirming(true)}
            aria-label={`Delete "${session.title}"`}
            title="Delete"
          >
            <TrashIcon />
          </button>
        </>
      )}
    </motion.li>
  );
}
