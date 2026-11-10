// Past conversations, newest first, grouped by day, and the button for a new question.
// Each row (HistoryRow) has a ⋯ menu to rename or delete it.

import { AnimatePresence } from "motion/react";
import type { SessionSummary } from "../types";
import { HistoryRow } from "./HistoryRow";
import { CloseIcon, PenIcon } from "./icons";

type Props = {
  sessions: SessionSummary[];
  current: string | null;
  open: boolean;
  onClose: () => void;
  onOpen: (id: string | null) => void;
  onRename: (id: string, title: string) => void;
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

export function Sidebar({ sessions, current, open, onClose, onOpen, onRename, onDelete }: Props) {
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
                    onRename={(title) => onRename(s.id, title)}
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
