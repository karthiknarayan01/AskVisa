// One turn: the question, one line for the steps the agent took (thinking and lookups), the
// answer, and how it ended. The line says what the agent is doing while it works, and opens to
// list each step.

import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { activityLine, toolLine } from "../activity";
import type { Step, Turn } from "../turns";
import { ChevronIcon } from "./icons";

type Props = { turn: Turn; live: boolean; isLast: boolean; onRetry: () => void };

const COMPACTION_NOTE = "Earlier messages were summarized to keep the conversation within the model's memory.";

export function TurnView({ turn, live, isLast, onRetry }: Props) {
  if (turn.steps.length === 1 && turn.steps[0].kind === "compaction") {
    return <p className="compaction-note">{COMPACTION_NOTE}</p>;
  }
  const working = live && !turn.end;
  const activity = activityLine(turn.steps, working);
  const steps = turn.steps.filter((s) => s.kind === "thinking" || s.kind === "tools");
  const last = turn.steps[turn.steps.length - 1];
  return (
    <article className="turn" aria-busy={working}>
      <h2 className="question">{turn.question}</h2>
      {activity && <ActivityLine text={activity.text} active={activity.active} steps={steps} />}
      {turn.steps.map((step, i) =>
        step.kind === "answer" ? (
          <Answer key={i} text={step.text} streaming={working && step === last} />
        ) : step.kind === "compaction" ? (
          <p key={i} className="compaction-note">{COMPACTION_NOTE}</p>
        ) : null,
      )}
      {turn.end && <EndNote turn={turn} canRetry={isLast && !live} onRetry={onRetry} />}
    </article>
  );
}

function ActivityLine({ text, active, steps }: { text: string; active: boolean; steps: Step[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="activity">
      <button
        type="button"
        className={`activity__toggle${active ? " activity__toggle--active" : ""}`}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        disabled={steps.length === 0}
      >
        <span role={active ? "status" : undefined} className="activity__text">{text}</span>
        {steps.length > 0 && (
          <span className={`activity__chevron${open ? " activity__chevron--open" : ""}`}><ChevronIcon /></span>
        )}
      </button>
      {open && (
        <ol className="activity__steps">
          {steps.map((step, i) =>
            step.kind === "thinking" ? (
              <li key={i} className="activity__thinking">{step.text.trim()}</li>
            ) : step.kind === "tools" ? (
              step.tools.map((tool) => (
                <li key={tool.id} className={`activity__tool activity__tool--${tool.status}`}>
                  {toolLine(tool)}
                  {tool.summary && <span className="activity__summary">{tool.summary}</span>}
                </li>
              ))
            ) : null,
          )}
        </ol>
      )}
    </div>
  );
}

function Answer({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className={`answer${streaming ? " answer--streaming" : ""}`}>
      <Markdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>
        {text}
      </Markdown>
    </div>
  );
}

function EndNote({ turn, canRetry, onRetry }: { turn: Turn; canRetry: boolean; onRetry: () => void }) {
  const end = turn.end!;
  if (end.outcome === "completed") return null;
  if (end.outcome === "cancelled") return <p className="end-note">You stopped this answer.</p>;
  return (
    <div className={`end-note${end.outcome === "error" ? " end-note--error" : ""}`} role="alert">
      <p>{end.outcome === "error" ? `The model couldn't answer: ${end.message}` : `This answer stopped early: ${end.message}`}</p>
      {end.retryable && canRetry && (
        <button type="button" className="button button--quiet" onClick={onRetry}>Try again</button>
      )}
    </div>
  );
}
