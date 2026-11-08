// A conversation as turns: the question, then the steps the agent took, then how it ended.
// Built from a stored conversation (fromItems) and kept up to date from live events (applyEvent),
// so history and a streaming answer render the same way.

import type { EndEvent, Item, StreamEvent, ToolCall } from "./types";

export type ToolView = {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "queued" | "running" | "done" | "error" | "skipped";
  summary: string;
  output: string;
};

export type Step =
  | { kind: "thinking"; text: string }
  | { kind: "answer"; text: string }
  | { kind: "tools"; tools: ToolView[] }
  | { kind: "compaction" };

export type Turn = {
  key: string;
  requestId: string | null;
  question: string;
  steps: Step[];
  end?: EndEvent;
};

let counter = 0;
const newKey = () => `turn-${Date.now()}-${counter++}`;

export function newTurn(question: string): Turn {
  return { key: newKey(), requestId: null, question, steps: [] };
}

/** A stored conversation as turns. Turns there have ended, except possibly the last. */
export function fromItems(items: Item[]): Turn[] {
  const turns: Turn[] = [];
  let current: Turn | null = null;
  for (const item of items) {
    if (item.type === "user_message") {
      current = { ...newTurn(item.text), requestId: item.request_id };
      turns.push(current);
      continue;
    }
    if (item.type === "compaction") {
      turns.push({ ...newTurn(""), steps: [{ kind: "compaction" }] });
      current = null;
      continue;
    }
    if (!current) continue;
    if (item.type === "assistant_message") {
      addReply(current, item.reasoning, item.text, item.tool_calls, "queued");
    } else {
      finishTool(current, item.id, item.name, item.is_error ? "error" : "done", item.summary, item.output, item.arguments);
    }
  }
  return turns;
}

/** The turn after one live event. Returns a new object, so React sees the change. */
export function applyEvent(turn: Turn, event: StreamEvent): Turn {
  const next: Turn = { ...turn, steps: turn.steps.map((s) => (s.kind === "tools" ? { ...s, tools: [...s.tools] } : { ...s })) };
  switch (event.event) {
    case "user_message":
      next.requestId = event.data.request_id;
      break;
    case "reasoning":
    case "text": {
      const kind = event.event === "reasoning" ? "thinking" : "answer";
      const last = next.steps[next.steps.length - 1];
      if (last && last.kind === kind) last.text += event.data.delta;
      else next.steps.push({ kind, text: event.data.delta });
      break;
    }
    case "assistant_message":
      settleReply(next, event.data.reasoning, event.data.text, event.data.tool_calls);
      break;
    case "tool_started":
      setTool(next, event.data.id, event.data.name, event.data.arguments, { status: "running" });
      break;
    case "tool_finished":
      finishTool(next, event.data.id, event.data.name, event.data.is_error ? "error" : "done", event.data.summary, event.data.output);
      break;
    case "tool_skipped":
      setTool(next, event.data.id, event.data.name, {}, { status: "skipped", summary: event.data.reason });
      break;
    case "compaction":
      next.steps.push({ kind: "compaction" });
      break;
    case "end":
      next.end = event.data;
      if (event.data.request_id) next.requestId = event.data.request_id;
      for (const step of next.steps) {
        if (step.kind === "tools") {
          step.tools = step.tools.map((t) => (t.status === "running" || t.status === "queued" ? { ...t, status: "skipped" } : t));
        }
      }
      break;
  }
  return next;
}

function addReply(turn: Turn, reasoning: string, text: string, calls: ToolCall[], status: ToolView["status"]) {
  if (reasoning) turn.steps.push({ kind: "thinking", text: reasoning });
  if (text) turn.steps.push({ kind: "answer", text });
  if (calls.length) {
    turn.steps.push({
      kind: "tools",
      tools: calls.map((c) => ({ id: c.id, name: c.name, arguments: c.arguments, status, summary: "", output: "" })),
    });
  }
}

/** A step's complete reply arrived: make the streamed steps match it exactly, then add its tools. */
function settleReply(turn: Turn, reasoning: string, text: string, calls: ToolCall[]) {
  const streamed: Step[] = [];
  while (turn.steps.length && ["thinking", "answer"].includes(turn.steps[turn.steps.length - 1].kind)) {
    streamed.unshift(turn.steps.pop()!);
  }
  if (!streamed.length) {
    addReply(turn, reasoning, text, calls, "queued");
    return;
  }
  turn.steps.push(...streamed.filter((s) => s.kind !== "answer" || text));
  const answer = turn.steps.findLast((s) => s.kind === "answer");
  if (answer && answer.kind === "answer") answer.text = text;
  if (calls.length) addReply(turn, "", "", calls, "queued");
}

function toolsStep(turn: Turn): { kind: "tools"; tools: ToolView[] } {
  const last = turn.steps[turn.steps.length - 1];
  if (last && last.kind === "tools") return last;
  const step = { kind: "tools" as const, tools: [] as ToolView[] };
  turn.steps.push(step);
  return step;
}

function setTool(turn: Turn, id: string, name: string, args: Record<string, unknown>, change: Partial<ToolView>) {
  for (const step of turn.steps) {
    if (step.kind !== "tools") continue;
    const index = step.tools.findIndex((t) => t.id === id);
    if (index !== -1) {
      step.tools[index] = { ...step.tools[index], ...change };
      return;
    }
  }
  toolsStep(turn).tools.push({ id, name, arguments: args, status: "running", summary: "", output: "", ...change });
}

function finishTool(
  turn: Turn, id: string, name: string, status: "done" | "error", summary: string, output: string,
  args: Record<string, unknown> = {},
) {
  setTool(turn, id, name, args, { status, summary, output });
}
