// A turn's intermediate steps (thinking and tool calls) as one line: what the agent is doing
// right now while it works, and a short summary of what it did once the answer is written.

import { describeCall } from "./tools";
import type { Step, ToolView } from "./turns";

export type Activity = { text: string; active: boolean };

/** The line for a turn's steps, or null when there is nothing to say (a plain answer). */
export function activityLine(steps: Step[], working: boolean): Activity | null {
  const last = steps[steps.length - 1];
  if (working && (!last || last.kind !== "answer")) return { text: current(last), active: true };
  const tools = steps.flatMap((s) => (s.kind === "tools" ? s.tools : [])).filter((t) => t.status !== "skipped");
  if (tools.length === 0) return steps.some((s) => s.kind === "thinking") ? { text: "Thought it through", active: false } : null;
  const first = describeCall(tools[0].name, tools[0].arguments).done;
  const text = tools.length === 1 ? first : `${first}, and ${tools.length - 1} more`;
  return { text, active: false };
}

/** What a step says in the expanded list. */
export function toolLine(tool: ToolView): string {
  const { doing, done } = describeCall(tool.name, tool.arguments);
  if (tool.status === "running" || tool.status === "queued") return doing;
  if (tool.status === "error") return `${done}: not available`;
  if (tool.status === "skipped") return `${doing}: skipped`;
  return done;
}

function current(last: Step | undefined): string {
  if (last?.kind === "tools") {
    const running = last.tools.find((t) => t.status === "running") ?? last.tools.find((t) => t.status === "queued");
    if (running) return describeCall(running.name, running.arguments).doing;
  }
  return "Thinking";
}
