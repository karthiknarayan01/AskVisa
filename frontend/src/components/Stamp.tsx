// A tool call as an ink stamp. While the lookup runs it is a faint dashed outline; when it
// finishes it is pressed onto the page (a quick scale-down) in stamp ink, or in red when the
// lookup failed. Clicking a finished stamp shows what the lookup returned.

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { ToolView } from "../turns";
import { stampText, tilt } from "../tools";

type Props = { tool: ToolView; open: boolean; onToggle: () => void };

const STATUS_WORD: Record<ToolView["status"], string> = {
  queued: "Waiting",
  running: "Checking",
  done: "Checked",
  error: "Not available",
  skipped: "Skipped",
};

export function Stamp({ tool, open, onToggle }: Props) {
  const reduce = useReducedMotion();
  const [title, detail] = stampText(tool.name, tool.arguments);
  const settled = tool.status === "done" || tool.status === "error";
  return (
    <motion.button
      type="button"
      className={`stamp stamp--${tool.status}`}
      style={{ ["--tilt" as string]: `${tilt(tool.id)}deg` }}
      onClick={settled ? onToggle : undefined}
      aria-expanded={settled ? open : undefined}
      aria-label={`${title}${detail ? `, ${detail}` : ""}: ${STATUS_WORD[tool.status]}${tool.summary ? `. ${tool.summary}` : ""}`}
      disabled={!settled}
      key={settled ? "pressed" : "pending"}
      initial={settled && !reduce ? { scale: 1.35, opacity: 0 } : false}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 700, damping: 26, mass: 0.6 }}
    >
      <span className="stamp__ink">
        <span className="stamp__title">{title}</span>
        {detail && <span className="stamp__detail">{detail}</span>}
        <span className="stamp__status">{STATUS_WORD[tool.status]}</span>
      </span>
    </motion.button>
  );
}

export function StampDetails({ tool }: { tool: ToolView | undefined }) {
  return (
    <AnimatePresence initial={false}>
      {tool && (
        <motion.div
          key={tool.id}
          className="stamp-details"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
        >
          <pre>{tool.output || tool.summary}</pre>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** The SVG filter that roughens stamp edges like ink on paper. Rendered once per page. */
export function InkFilter() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <filter id="ink">
        <feTurbulence type="fractalNoise" baseFrequency="0.7" numOctaves="2" seed="7" result="noise" />
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="1.2" xChannelSelector="R" yChannelSelector="G" result="rough" />
        <feComponentTransfer in="noise" result="speckle">
          <feFuncA type="discrete" tableValues="1 1 1 1 0.6 1 1 1 1 1" />
        </feComponentTransfer>
        <feComposite in="rough" in2="speckle" operator="in" />
      </filter>
    </svg>
  );
}
