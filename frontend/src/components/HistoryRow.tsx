// One conversation in the sidebar: its title, and a ⋯ menu with Rename and Delete.
// Rename turns the title into a text box in place (Enter or clicking away saves, Escape keeps
// the old title). Delete asks once more inside the row, then the row folds away.
// The menu floats above the list (a portal), so a row near the bottom or a scrolled list
// never clips it.

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SessionSummary } from "../types";
import { DotsIcon, PencilIcon, TrashIcon } from "./icons";

export const MAX_TITLE_CHARS = 120;

type Props = {
  session: SessionSummary;
  current: boolean;
  onOpen: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
};

type Mode = "idle" | "renaming" | "confirming";

export function HistoryRow({ session, current, onOpen, onRename, onDelete }: Props) {
  const [mode, setMode] = useState<Mode>("idle");
  const [menuOpen, setMenuOpen] = useState(false);
  const dots = useRef<HTMLButtonElement>(null);
  const reduce = useReducedMotion();

  return (
    <motion.li
      layout={!reduce}
      className={`history__row history__row--${mode}${menuOpen ? " history__row--menu" : ""}`}
      exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0, x: -12, transition: { duration: 0.22, ease: [0.3, 0, 0.2, 1] } }}
    >
      {mode === "renaming" ? (
        <RenameBox
          title={session.title}
          onDone={(title) => {
            setMode("idle");
            if (title && title !== session.title) onRename(title);
          }}
        />
      ) : mode === "confirming" ? (
        <ConfirmDelete title={session.title} onDelete={onDelete} onKeep={() => setMode("idle")} />
      ) : (
        <>
          <a
            href={`/c/${session.id}`}
            className={`history__item${current ? " history__item--current" : ""}`}
            aria-current={current ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); onOpen(); }}
          >
            {session.running && <span className="history__running" aria-label="Answering" title="Answering" />}
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={session.title}
                className="history__title"
                initial={reduce ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduce ? undefined : { opacity: 0, y: -4 }}
                transition={{ duration: 0.25 }}
              >
                {session.title}
              </motion.span>
            </AnimatePresence>
          </a>
          <button
            type="button"
            ref={dots}
            className="history__dots"
            aria-label={`Options for "${session.title}"`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <DotsIcon />
          </button>
          {menuOpen && (
            <RowMenu
              anchor={dots}
              onClose={() => setMenuOpen(false)}
              onRename={() => { setMenuOpen(false); setMode("renaming"); }}
              onDelete={() => { setMenuOpen(false); setMode("confirming"); }}
            />
          )}
        </>
      )}
    </motion.li>
  );
}

type MenuProps = {
  anchor: React.RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
};

/** The ⋯ menu, placed under its button (above it when there's no room below). Closes on a
 * click elsewhere, Escape, Tab, scrolling or resizing; arrow keys move between the items. */
function RowMenu({ anchor, onClose, onRename, onDelete }: MenuProps) {
  const menu = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number; above: boolean } | null>(null);

  useLayoutEffect(() => {
    const button = anchor.current?.getBoundingClientRect();
    const height = menu.current?.offsetHeight ?? 88;
    const width = menu.current?.offsetWidth ?? 160;
    if (!button) return;
    const above = button.bottom + height + 8 > window.innerHeight;
    setPlace({
      top: above ? button.top - height - 4 : button.bottom + 4,
      left: Math.max(8, Math.min(button.right - width, window.innerWidth - width - 8)),
      above,
    });
  }, [anchor]);

  useEffect(() => {
    menu.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const away = (e: Event) => {
      const target = e.target as Node;
      if (!menu.current?.contains(target) && !anchor.current?.contains(target)) onClose();
    };
    const close = () => onClose();
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("resize", close);
    document.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      window.removeEventListener("resize", close);
      document.removeEventListener("scroll", close, true);
    };
  }, [anchor, onClose]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape" || e.key === "Tab") {
      e.preventDefault();
      onClose();
      anchor.current?.focus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    }
  };

  return createPortal(
    <div
      ref={menu}
      role="menu"
      className={`row-menu${place?.above ? " row-menu--above" : ""}`}
      style={place ? { top: place.top, left: place.left } : { visibility: "hidden", top: 0, left: 0 }}
      onKeyDown={onKeyDown}
    >
      <button type="button" role="menuitem" className="row-menu__item" onClick={onRename}>
        <PencilIcon /> Rename
      </button>
      <button type="button" role="menuitem" className="row-menu__item row-menu__item--danger" onClick={onDelete}>
        <TrashIcon /> Delete
      </button>
    </div>,
    document.body,
  );
}

/** The title as a text box: Enter or clicking away saves, Escape keeps the old title. */
function RenameBox({ title, onDone }: { title: string; onDone: (title: string | null) => void }) {
  const [value, setValue] = useState(title);
  const box = useRef<HTMLInputElement>(null);
  const finished = useRef(false);

  useEffect(() => {
    box.current?.focus();
    box.current?.select();
  }, []);

  const finish = (save: boolean) => {
    if (finished.current) return;
    finished.current = true;
    onDone(save ? value.replace(/\s+/g, " ").trim() || null : null);
  };

  return (
    <input
      ref={box}
      className="history__rename"
      value={value}
      maxLength={MAX_TITLE_CHARS}
      aria-label="Conversation title"
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); finish(true); }
        if (e.key === "Escape") { e.preventDefault(); finish(false); }
      }}
    />
  );
}

/** "Delete for good?" in place of the row, with Delete and Keep; Escape keeps it. */
function ConfirmDelete({ title, onDelete, onKeep }: { title: string; onDelete: () => void; onKeep: () => void }) {
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => keep.current?.focus(), []);
  return (
    <div
      className="history__confirm"
      role="group"
      aria-label={`Delete "${title}"?`}
      onKeyDown={(e) => { if (e.key === "Escape") onKeep(); }}
    >
      <span className="history__confirm-text">Delete for good?</span>
      <button type="button" className="history__confirm-delete" onClick={onDelete}>
        Delete
      </button>
      <button type="button" ref={keep} className="history__confirm-keep" onClick={onKeep}>
        Keep
      </button>
    </div>
  );
}
