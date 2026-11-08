// One turn: the question, the steps the agent took (thinking, stamps, answer), and how it ended.

import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Step, Turn } from "../turns";
import { ChevronIcon } from "./icons";
import { Stamp, StampDetails } from "./Stamp";

type Props = { turn: Turn; live: boolean; isLast: boolean; onRetry: () => void };

export function TurnView({ turn, live, isLast, onRetry }: Props) {
  if (turn.steps.length === 1 && turn.steps[0].kind === "compaction") {
    return <p className="compaction-note">Earlier messages were summarized to keep the conversation within the model's memory.</p>;
  }
  const lastStep = turn.steps[turn.steps.length - 1];
  const waiting = live && !turn.end && (!lastStep || lastStep.kind === "tools");
  return (
    <article className="turn" aria-busy={live && !turn.end}>
      <h2 className="question">{turn.question}</h2>
      {turn.steps.map((step, i) => (
        <StepView key={i} step={step} active={live && !turn.end && i === turn.steps.length - 1} />
      ))}
      {waiting && <p className="working" role="status">Working on it</p>}
      {turn.end && <EndNote turn={turn} canRetry={isLast && !live} onRetry={onRetry} />}
    </article>
  );
}

function StepView({ step, active }: { step: Step; active: boolean }) {
  if (step.kind === "thinking") return <Thinking text={step.text} active={active} />;
  if (step.kind === "answer") {
    return (
      <div className={`answer${active ? " answer--streaming" : ""}`}>
        <Markdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>
          {step.text}
        </Markdown>
      </div>
    );
  }
  if (step.kind === "tools") return <Stamps tools={step.tools} />;
  return <p className="compaction-note">Earlier messages were summarized to keep the conversation within the model's memory.</p>;
}

function Stamps({ tools }: { tools: Extract<Step, { kind: "tools" }>["tools"] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="stamps">
      <div className="stamps__row">
        {tools.map((tool) => (
          <div className="stamps__item" key={tool.id}>
            <Stamp tool={tool} open={open === tool.id} onToggle={() => setOpen(open === tool.id ? null : tool.id)} />
            {tool.summary && <p className="stamps__summary">{tool.summary}</p>}
          </div>
        ))}
      </div>
      <StampDetails tool={tools.find((t) => t.id === open)} />
    </div>
  );
}

function Thinking({ text, active }: { text: string; active: boolean }) {
  const [open, setOpen] = useState(false);
  const preview = text.trim().split("\n").filter(Boolean).pop() ?? "";
  return (
    <div className={`thinking${active ? " thinking--active" : ""}`}>
      <button type="button" className="thinking__toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`thinking__chevron${open ? " thinking__chevron--open" : ""}`}><ChevronIcon /></span>
        <span className="thinking__label">{active ? "Thinking" : "Thought it through"}</span>
      </button>
      {open ? <p className="thinking__text">{text}</p> : active && <p className="thinking__preview">{preview}</p>}
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
