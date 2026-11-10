import { describe, expect, it } from "vitest";
import { applyEvent, fromItems, fromSnapshot, mergeTurns, newTurn, type Step, type ToolView, type Turn } from "./turns";
import type { StreamEvent } from "./types";
import { activityLine, toolLine } from "./activity";
import { describeCall } from "./tools";

const play = (events: StreamEvent[]): Turn => events.reduce(applyEvent, newTurn("Does Acme sponsor?"));

describe("a live turn", () => {
  it("streams thinking, then stamps, then the answer", () => {
    const turn = play([
      { event: "user_message", data: { text: "Does Acme sponsor?", request_id: "r1" } },
      { event: "reasoning", data: { delta: "Look it " } },
      { event: "reasoning", data: { delta: "up." } },
      { event: "assistant_message", data: { text: "", reasoning: "Look it up.", tool_calls: [{ id: "c1", name: "match_employer", arguments: { name: "Acme" } }] } },
      { event: "tool_started", data: { id: "c1", name: "match_employer", arguments: { name: "Acme" } } },
      { event: "tool_finished", data: { id: "c1", name: "match_employer", summary: "Match: exact.", output: "…", is_error: false } },
      { event: "text", data: { delta: "Yes, " } },
      { event: "text", data: { delta: "it does." } },
      { event: "assistant_message", data: { text: "Yes, it does.", reasoning: "", tool_calls: [] } },
      { event: "end", data: { outcome: "completed", text: "Yes, it does.", request_id: "r1" } },
    ]);
    expect(turn.requestId).toBe("r1");
    expect(turn.steps.map((s) => s.kind)).toEqual(["thinking", "tools", "answer"]);
    expect(turn.steps[0]).toEqual({ kind: "thinking", text: "Look it up." });
    const tools = turn.steps[1];
    expect(tools.kind === "tools" && tools.tools[0]).toMatchObject({ status: "done", summary: "Match: exact." });
    expect(turn.steps[2]).toEqual({ kind: "answer", text: "Yes, it does." });
    expect(turn.end?.outcome).toBe("completed");
  });

  it("uses the complete reply when nothing was streamed", () => {
    const turn = play([{ event: "assistant_message", data: { text: "Hello.", reasoning: "", tool_calls: [] } }]);
    expect(turn.steps).toEqual([{ kind: "answer", text: "Hello." }]);
  });

  it("marks a failed lookup and skips calls left open when the turn ends", () => {
    const turn = play([
      { event: "assistant_message", data: { text: "", reasoning: "", tool_calls: [{ id: "a", name: "perm_queue", arguments: {} }, { id: "b", name: "visa_bulletin", arguments: {} }] } },
      { event: "tool_finished", data: { id: "a", name: "perm_queue", summary: "perm_queue failed: HTTP 429", output: "", is_error: true } },
      { event: "end", data: { outcome: "cancelled", request_id: null } },
    ]);
    const step = turn.steps[0];
    expect(step.kind === "tools" && step.tools.map((t) => t.status)).toEqual(["error", "skipped"]);
  });

  it("doesn't change the turn it was given", () => {
    const before = newTurn("q");
    applyEvent(before, { event: "text", data: { delta: "hi" } });
    expect(before.steps).toEqual([]);
  });
});

describe("a stored conversation", () => {
  it("becomes the same turns", () => {
    const turns = fromItems([
      { type: "compaction", summary: "…", request_id: null },
      { type: "user_message", text: "Does Acme sponsor?", request_id: "r1" },
      { type: "assistant_message", text: "", reasoning: "Look it up.", tool_calls: [{ id: "c1", name: "match_employer", arguments: { name: "Acme" } }], request_id: "r1" },
      { type: "tool_finished", id: "c1", name: "match_employer", summary: "Match: exact.", output: "…", is_error: false, request_id: "r1" },
      { type: "assistant_message", text: "Yes.", reasoning: "", tool_calls: [], request_id: "r1" },
    ]);
    expect(turns.map((t) => t.steps.map((s) => s.kind))).toEqual([["compaction"], ["thinking", "tools", "answer"]]);
    expect(turns[1].requestId).toBe("r1");
    const tools = turns[1].steps[1];
    expect(tools.kind === "tools" && tools.tools[0].status).toBe("done");
  });
});

describe("a turn picked up after a reconnect", () => {
  it("is rebuilt from the events so far, question included", () => {
    const turn = fromSnapshot({
      session_id: "s1",
      request_id: "r2",
      base: [],
      events: [
        { event: "user_message", data: { text: "Does Acme sponsor?", request_id: "r2" } },
        { event: "reasoning", data: { delta: "Look it up." } },
        { event: "tool_started", data: { id: "c1", name: "match_employer", arguments: { name: "Acme" } } },
      ],
    });
    expect(turn.question).toBe("Does Acme sponsor?");
    expect(turn.requestId).toBe("r2");
    expect(turn.steps.map((s) => s.kind)).toEqual(["thinking", "tools"]);
    expect(turn.end).toBeUndefined();
  });

  it("keeps a resumed turn's earlier steps", () => {
    const turn = fromSnapshot({
      session_id: "s1",
      request_id: "r1",
      base: [
        { type: "user_message", text: "Does Acme sponsor?", request_id: "r1" },
        { type: "assistant_message", text: "", reasoning: "", tool_calls: [{ id: "c1", name: "match_employer", arguments: {} }], request_id: "r1" },
        { type: "tool_finished", id: "c1", name: "match_employer", summary: "Match: exact.", output: "…", is_error: false, request_id: "r1" },
      ],
      events: [{ event: "text", data: { delta: "Yes." } }],
    });
    expect(turn.steps.map((s) => s.kind)).toEqual(["tools", "answer"]);
  });
});

describe("showing a conversation", () => {
  const stored = fromItems([
    { type: "user_message", text: "first", request_id: "r1" },
    { type: "assistant_message", text: "one", reasoning: "", tool_calls: [], request_id: "r1" },
    { type: "user_message", text: "second", request_id: "r2" },
  ]);

  it("puts the live turn in place of its stored copy", () => {
    const live = { ...newTurn("second"), requestId: "r2", steps: [{ kind: "answer" as const, text: "two so far" }] };
    const shown = mergeTurns(stored, live);
    expect(shown.map((t) => t.question)).toEqual(["first", "second"]);
    expect(shown[1].steps).toEqual(live.steps);
    expect(shown[1].key).toBe("r2");
  });

  it("adds a live turn that isn't stored yet at the end", () => {
    const shown = mergeTurns(stored.slice(0, 1), newTurn("just asked"));
    expect(shown.map((t) => t.question)).toEqual(["first", "just asked"]);
  });

  it("is the stored turns when nothing is live", () => {
    expect(mergeTurns(stored, undefined)).toBe(stored);
  });
});

describe("a tool call's phrase", () => {
  it("says what is being looked up, while it runs and once it has", () => {
    expect(describeCall("read_regulation", { section: "214.2", paragraph: "(f)(10)" })).toEqual({
      doing: "Reading 8 CFR 214.2(f)(10)",
      done: "Read 8 CFR 214.2(f)(10)",
    });
    expect(describeCall("read_web_page", { url: "https://www.uscis.gov/i-485" }).done).toBe("Read uscis.gov");
    expect(describeCall("visa_bulletin", { month: "2026-11" }).doing).toBe("Reading the November 2026 visa bulletin");
    expect(describeCall("web_search", {}).doing).toBe("Searching the web");
    expect(describeCall("unknown_tool", {}).done).toBe("Checked unknown tool");
  });
});

describe("a turn's activity line", () => {
  const tool = (id: string, name: string, status: ToolView["status"], args = {}): ToolView => ({
    id, name, arguments: args, status, summary: "", output: "",
  });
  const lookups: Step[] = [
    { kind: "thinking", text: "Check the employer first." },
    { kind: "tools", tools: [tool("a", "match_employer", "done", { name: "Infosys" }), tool("b", "perm_queue", "running")] },
  ];

  it("says what the agent is doing while it works", () => {
    expect(activityLine([], true)).toEqual({ text: "Thinking", active: true });
    expect(activityLine(lookups.slice(0, 1), true)).toEqual({ text: "Thinking", active: true });
    expect(activityLine(lookups, true)).toEqual({ text: "Checking DOL’s PERM queue", active: true });
  });

  it("sums up what it did once the answer starts", () => {
    const answered: Step[] = [...lookups, { kind: "answer", text: "Yes." }];
    expect(activityLine(answered, true)).toEqual({ text: "Looked up Infosys’s sponsorship record, and 1 more", active: false });
    expect(activityLine([lookups[0], { kind: "answer", text: "Yes." }], false)).toEqual({ text: "Thought it through", active: false });
    expect(activityLine([{ kind: "answer", text: "Hi." }], false)).toBeNull();
  });

  it("marks a failed lookup in the step list", () => {
    expect(toolLine(tool("c", "perm_queue", "error"))).toBe("Checked DOL’s PERM queue: not available");
  });
});
