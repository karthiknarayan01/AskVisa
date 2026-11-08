import { describe, expect, it } from "vitest";
import { parseEvent } from "./api";
import { applyEvent, fromItems, newTurn, type Turn } from "./turns";
import type { StreamEvent } from "./types";
import { stampText, tilt } from "./tools";

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

describe("the stream", () => {
  it("parses events and skips pings", () => {
    expect(parseEvent('event: text\ndata: {"delta": "hi"}')).toEqual({ event: "text", data: { delta: "hi" } });
    expect(parseEvent(": ping")).toBeNull();
  });
});

describe("stamps", () => {
  it("say what was looked up", () => {
    expect(stampText("read_regulation", { section: "214.2", paragraph: "(f)(10)" })).toEqual(["8 CFR 214.2(f)(10)", ""]);
    expect(stampText("read_web_page", { url: "https://www.uscis.gov/i-485" })).toEqual(["Web page", "uscis.gov"]);
    expect(stampText("unknown_tool", {})).toEqual(["unknown tool", ""]);
  });

  it("tilt the same way every time, within 3 degrees", () => {
    expect(tilt("c1")).toBe(tilt("c1"));
    for (const id of ["a", "b", "call_123", "x".repeat(40)]) expect(Math.abs(tilt(id))).toBeLessThanOrEqual(3);
  });
});
