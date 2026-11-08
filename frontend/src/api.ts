// The REST API (runtime/api.py). A turn streams as Server-Sent Events over a POST, so it is read
// with fetch() and a reader over the response body; EventSource can't send a POST body.

import type { SessionSummary, StoredSession, StreamEvent } from "./types";

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? `The server answered ${response.status}.`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  config: () => fetch("/api/config").then((r) => json<{ provider: string; model: string }>(r)),
  sessions: () => fetch("/api/sessions").then((r) => json<SessionSummary[]>(r)),
  session: (id: string) => fetch(`/api/sessions/${id}`).then((r) => json<StoredSession>(r)),
  create: () => fetch("/api/sessions", { method: "POST" }).then((r) => json<{ id: string }>(r)),
  cancel: (id: string) => fetch(`/api/sessions/${id}/cancel`, { method: "POST" }),
};

/** Send a message (or resume an interrupted turn) and call onEvent for each event as it arrives. */
export async function streamTurn(
  sessionId: string,
  text: string | null,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const url = `/api/sessions/${sessionId}/${text === null ? "resume" : "messages"}`;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: text === null ? undefined : JSON.stringify({ text }),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? `The server answered ${response.status}.`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const event = parseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event) onEvent(event);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

/** One SSE block as an event, or null for a comment (the server's keep-alive pings). */
export function parseEvent(block: string): StreamEvent | null {
  let event = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!event || !data) return null;
  return { event, data: JSON.parse(data) } as StreamEvent;
}
