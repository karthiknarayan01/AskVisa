import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveConnection, socketUrl, type Status } from "./live";

class FakeSocket {
  static OPEN = 1;
  static made: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.made.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("the live connection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.made = [];
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("sends commands only while open, and hands every message on", () => {
    const messages: unknown[] = [];
    const conn = new LiveConnection("ws://x/api/ws", (m) => messages.push(m), () => {});
    conn.start();
    const socket = FakeSocket.made[0];
    expect(conn.send({ type: "cancel", session_id: "s" })).toBe(false);
    socket.open();
    expect(conn.send({ type: "cancel", session_id: "s" })).toBe(true);
    expect(JSON.parse(socket.sent[0])).toEqual({ type: "cancel", session_id: "s" });
    socket.onmessage?.({ data: '{"type":"hello","running":[]}' });
    socket.onmessage?.({ data: "not json" });
    expect(messages).toEqual([{ type: "hello", running: [] }]);
  });

  it("reconnects after a drop, waiting longer each time it fails", () => {
    const statuses: Status[] = [];
    const conn = new LiveConnection("ws://x/api/ws", () => {}, (s) => statuses.push(s));
    conn.start();
    FakeSocket.made[0].open();
    FakeSocket.made[0].drop();
    expect(statuses).toEqual(["connecting", "open", "reconnecting"]);

    vi.advanceTimersByTime(499);
    expect(FakeSocket.made).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.made).toHaveLength(2);
    FakeSocket.made[1].drop();
    vi.advanceTimersByTime(999);
    expect(FakeSocket.made).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.made).toHaveLength(3);
    FakeSocket.made[2].open();
    expect(statuses.at(-1)).toBe("open");
  });

  it("stays closed once stopped", () => {
    const conn = new LiveConnection("ws://x/api/ws", () => {}, () => {});
    conn.start();
    FakeSocket.made[0].open();
    conn.stop();
    vi.advanceTimersByTime(10_000);
    expect(FakeSocket.made).toHaveLength(1);
  });

  it("uses wss on an https page", () => {
    expect(socketUrl({ protocol: "https:", host: "askvisa.example" } as Location)).toBe("wss://askvisa.example/api/ws");
    expect(socketUrl({ protocol: "http:", host: "127.0.0.1:8000" } as Location)).toBe("ws://127.0.0.1:8000/api/ws");
  });
});
