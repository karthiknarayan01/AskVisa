// The HTTP part of the API (runtime/api.py): listing, opening, creating, renaming and deleting
// sessions.
// Turns themselves run over the live channel (live.ts).

import type { SessionSummary, StoredSession } from "./types";

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
  rename: (id: string, title: string) =>
    fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then((r) => json<SessionSummary>(r)),
  remove: async (id: string) => {
    const response = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) await json(response);
  },
};
