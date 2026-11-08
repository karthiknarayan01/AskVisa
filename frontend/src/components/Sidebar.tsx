// Past conversations, newest first, grouped by day, and the button for a new question.

import type { SessionSummary } from "../types";
import { CloseIcon, PenIcon } from "./icons";

type Props = {
  sessions: SessionSummary[];
  current: string | null;
  open: boolean;
  onClose: () => void;
  onOpen: (id: string | null) => void;
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

export function Sidebar({ sessions, current, open, onClose, onOpen }: Props) {
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
              {items.map((s) => (
                <li key={s.id}>
                  <a
                    href={`/c/${s.id}`}
                    className={`history__item${s.id === current ? " history__item--current" : ""}`}
                    aria-current={s.id === current ? "page" : undefined}
                    onClick={(e) => { e.preventDefault(); onOpen(s.id); }}
                  >
                    {s.title}
                  </a>
                </li>
              ))}
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
