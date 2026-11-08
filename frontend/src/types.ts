// The API's shapes (runtime/api_events.py). A stored conversation and a live turn use the same
// item types, so history and streaming render with the same components.

export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

export type Item =
  | { type: "user_message"; text: string; request_id: string | null }
  | {
      type: "assistant_message";
      text: string;
      reasoning: string;
      tool_calls: ToolCall[];
      request_id: string | null;
    }
  | {
      type: "tool_finished";
      id: string;
      name: string;
      summary: string;
      output: string;
      is_error: boolean;
      arguments?: Record<string, unknown>;
      request_id: string | null;
    }
  | { type: "compaction"; summary?: string; request_id: string | null };

export type SessionSummary = { id: string; title: string; created_at: string; updated_at: string };

export type StoredSession = {
  id: string;
  title: string;
  items: Item[];
  running: boolean;
  interrupted: boolean;
};

export type EndEvent = {
  outcome: "completed" | "cancelled" | "stopped" | "error";
  request_id: string | null;
  text?: string;
  message?: string;
  retryable?: boolean;
};

export type StreamEvent =
  | { event: "user_message"; data: { text: string; request_id: string } }
  | { event: "reasoning" | "text"; data: { delta: string } }
  | { event: "assistant_message"; data: { text: string; reasoning: string; tool_calls: ToolCall[] } }
  | { event: "tool_started"; data: { id: string; name: string; arguments: Record<string, unknown> } }
  | {
      event: "tool_finished";
      data: { id: string; name: string; summary: string; output: string; is_error: boolean };
    }
  | { event: "tool_skipped"; data: { id: string; name: string; reason: string } }
  | { event: "compaction"; data: { replaced_count: number } }
  | { event: "turn_stopped"; data: { reason: string } }
  | { event: "end"; data: EndEvent };
