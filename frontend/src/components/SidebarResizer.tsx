// The line between the conversation list and the conversation: drag it (or focus it and use the
// arrow keys) to make the list wider or narrower. Double-click puts it back. The width is kept
// in this browser for next time.

import { useCallback, useEffect, useRef, useState } from "react";

export const DEFAULT_WIDTH = 280;
export const MIN_WIDTH = 200;
const MAX_WIDTH = 480;
const MAX_SHARE = 0.5; // never more than half the window
const STEP = 16;
const STORAGE_KEY = "askvisa.sidebarWidth";

/** The list's width in pixels, and a setter that keeps it in bounds and remembers it. */
export function useSidebarWidth(): [number, (width: number) => void] {
  const [width, setWidth] = useState(() => clamp(readStored() ?? DEFAULT_WIDTH));
  const set = useCallback((next: number) => {
    const clamped = clamp(next);
    setWidth(clamped);
    try {
      localStorage.setItem(STORAGE_KEY, String(clamped));
    } catch {
      // Storage can be unavailable (private windows); the width then lasts for this visit only.
    }
  }, []);
  useEffect(() => {
    const onResize = () => setWidth((w) => clamp(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return [width, set];
}

type Props = { width: number; onResize: (width: number) => void };

export function SidebarResizer({ width, onResize }: Props) {
  const dragging = useRef(false);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    document.body.classList.add("is-resizing");
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragging.current) onResize(e.clientX);
  };
  const stop = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    document.body.classList.remove("is-resizing");
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const next = { ArrowLeft: width - STEP, ArrowRight: width + STEP, Home: MIN_WIDTH, End: maxWidth() }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    onResize(next);
  };

  return (
    <div
      className="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the conversation list"
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={maxWidth()}
      aria-valuenow={width}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onResize(DEFAULT_WIDTH)}
    />
  );
}

function clamp(width: number): number {
  return Math.round(Math.min(Math.max(width, MIN_WIDTH), maxWidth()));
}

function maxWidth(): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.floor(window.innerWidth * MAX_SHARE)));
}

function readStored(): number | null {
  try {
    const value = Number(localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}
