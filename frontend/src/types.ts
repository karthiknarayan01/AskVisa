// The API's shapes (runtime/api_events.py, runtime/live.py). A stored conversation and a live
// turn use the same item types, so history and streaming render with the same components.

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

export type SessionSummary = { id: string; title: string; created_at: string; updated_at: string; running?: boolean };

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

/** A turn that is running, as the server's hello describes it to a tab that has just connected. */
export type RunningTurn = {
  session_id: string;
  request_id: string | null;
  base: Item[];
  events: StreamEvent[];
};

export type ServerMessage =
  | { type: "hello"; running: RunningTurn[] }
  | ({ type: "event"; session_id: string; request_id: string | null } & StreamEvent)
  | { type: "session"; session: SessionSummary }
  | { type: "session_deleted"; session_id: string }
  | { type: "error"; session_id: string | null; message: string };
