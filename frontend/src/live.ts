// The live channel (runtime/live.py): one WebSocket for the whole tab, carrying every session's
// turns. Commands go up, events for any session come down tagged with their session id. If the
// connection drops it reconnects on its own, waiting a little longer after each failure, and the
// server's hello on the new connection says which turns are still running and what they have
// done so far.

import type { ServerMessage } from "./types";

export type Command =
  | { type: "send"; session_id: string; text: string }
  | { type: "resume"; session_id: string }
  | { type: "cancel"; session_id: string };

export type Status = "connecting" | "open" | "reconnecting";

const FIRST_RETRY_MS = 500;
const LONGEST_RETRY_MS = 8_000;

export function socketUrl(location: Location = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`;
}

export class LiveConnection {
  private socket: WebSocket | null = null;
  private retryMs = FIRST_RETRY_MS;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private readonly url: string,
    private readonly onMessage: (message: ServerMessage) => void,
    private readonly onStatus: (status: Status) => void,
  ) {}

  start(): void {
    this.stopped = false;
    this.open("connecting");
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.socket?.close();
    this.socket = null;
  }

  /** Send a command now. False if the connection is down (the caller says so to the user). */
  send(command: Command): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(command));
    return true;
  }

  private open(status: Status): void {
    this.onStatus(status);
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.retryMs = FIRST_RETRY_MS;
      this.onStatus("open");
    };
    socket.onmessage = (e) => {
      try {
        this.onMessage(JSON.parse(e.data) as ServerMessage);
      } catch {
        // A message this version of the app can't read; skip it rather than drop the connection.
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket || this.stopped) return;
      this.socket = null;
      this.onStatus("reconnecting");
      this.retryTimer = setTimeout(() => this.open("reconnecting"), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, LONGEST_RETRY_MS);
    };
  }
}
