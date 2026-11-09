// The app's state: the sessions list, every session's live turn, and the open conversation.
// One live connection (live.ts) carries the turns of all sessions, so a conversation keeps going
// while another one is open, and a reload picks a running answer up where it is.
// The open session lives in the URL (/c/<id>), so a reload or a shared link opens it again.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { LiveConnection, socketUrl, type Command, type Status } from "./live";
import { applyEvent, fromItems, fromSnapshot, mergeTurns, newTurn, type Turn } from "./turns";
import type { ServerMessage, SessionSummary } from "./types";

const sessionFromUrl = () => window.location.pathname.match(/^\/c\/([\w-]+)/)?.[1] ?? null;

type Stored = { turns: Turn[]; interrupted: boolean };

const byRecent = (a: SessionSummary, b: SessionSummary) => b.updated_at.localeCompare(a.updated_at);
const without = <T,>(map: Record<string, T>, key: string) => {
  const { [key]: _, ...rest } = map;
  return rest;
};

export function useChat() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(sessionFromUrl);
  const [stored, setStored] = useState<Record<string, Stored>>({});
  const [live, setLive] = useState<Record<string, Turn>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [model, setModel] = useState<{ provider: string; model: string } | null>(null);
  const connection = useRef<LiveConnection | null>(null);
  const open = useRef(sessionId);
  open.current = sessionId;
  const liveNow = useRef(live);
  liveNow.current = live;
  // Sessions whose command was sent but whose turn hasn't produced an event yet.
  const pending = useRef(new Set<string>());

  const refreshSessions = useCallback(() => {
    api.sessions().then(setSessions).catch(() => {});
  }, []);

  const loadStored = useCallback((id: string) => {
    api
      .session(id)
      .then((s) => setStored((all) => ({ ...all, [id]: { turns: fromItems(s.items), interrupted: s.interrupted } })))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404 && open.current === id) navigate(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const navigate = useCallback((id: string | null) => {
    window.history.pushState(null, "", id ? `/c/${id}` : "/");
    setSessionId(id);
  }, []);

  const onMessage = useCallback(
    (message: ServerMessage) => {
      switch (message.type) {
        case "hello": {
          // Every running turn as it stands; a turn this tab was following that isn't running
          // any more ended (or never started) while it was disconnected, so show the stored one.
          const running = new Set(message.running.map((r) => r.session_id));
          const next: Record<string, Turn> = {};
          for (const [id, turn] of Object.entries(liveNow.current)) {
            if (running.has(id)) continue;
            if (turn.end) next[id] = turn;
            else if (id === open.current) loadStored(id);
          }
          for (const r of message.running) next[r.session_id] = fromSnapshot(r);
          pending.current.clear();
          setLive(next);
          refreshSessions();
          break;
        }
        case "event": {
          const id = message.session_id;
          pending.current.delete(id);
          setLive((all) => ({ ...all, [id]: applyEvent(all[id] ?? newTurn(""), message) }));
          if (message.event === "end" && id === open.current) loadStored(id);
          break;
        }
        case "session":
          setSessions((all) => [message.session, ...all.filter((s) => s.id !== message.session.id)].sort(byRecent));
          break;
        case "session_deleted": {
          const id = message.session_id;
          setSessions((all) => all.filter((s) => s.id !== id));
          setLive((all) => without(all, id));
          setStored((all) => without(all, id));
          if (id === open.current) navigate(null);
          break;
        }
        case "error": {
          const id = message.session_id;
          if (!id) break;
          if (pending.current.delete(id)) setLive((all) => without(all, id));
          setErrors((all) => ({ ...all, [id]: message.message }));
          break;
        }
      }
    },
    [loadStored, navigate, refreshSessions],
  );

  useEffect(() => {
    api.config().then(setModel).catch(() => setServerError("The assistant's server isn't reachable. Start it with ./start.sh."));
    refreshSessions();
    const conn = new LiveConnection(socketUrl(), onMessage, setStatus);
    connection.current = conn;
    conn.start();
    const onPop = () => setSessionId(sessionFromUrl());
    window.addEventListener("popstate", onPop);
    return () => {
      conn.stop();
      window.removeEventListener("popstate", onPop);
    };
  }, [onMessage, refreshSessions]);

  // Load a conversation each time it is opened, so it is current even if another tab added to it.
  useEffect(() => {
    if (sessionId) loadStored(sessionId);
  }, [sessionId, loadStored]);

  const command = useCallback((id: string, cmd: Command): boolean => {
    setErrors((all) => without(all, id));
    if (connection.current?.send(cmd)) return true;
    setErrors((all) => ({ ...all, [id]: "Not connected to the assistant's server. Reconnecting…" }));
    return false;
  }, []);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question) return;
      let id = sessionId;
      if (id && live[id] && !live[id].end) return;
      if (!id) {
        try {
          id = (await api.create()).id;
        } catch (e) {
          setServerError(e instanceof Error ? e.message : "Couldn't start a conversation.");
          return;
        }
        setStored((all) => ({ ...all, [id!]: { turns: [], interrupted: false } }));
        navigate(id);
      }
      const now = new Date().toISOString();
      pending.current.add(id);
      setLive((all) => ({ ...all, [id!]: newTurn(question) }));
      setSessions((all) =>
        all.some((s) => s.id === id)
          ? all
          : [{ id: id!, title: question, created_at: now, updated_at: now, running: true }, ...all],
      );
      if (!command(id, { type: "send", session_id: id, text: question })) {
        pending.current.delete(id);
        setLive((all) => without(all, id!));
      }
    },
    [sessionId, live, navigate, command],
  );

  const stop = useCallback(() => {
    if (sessionId) command(sessionId, { type: "cancel", session_id: sessionId });
  }, [sessionId, command]);

  const resume = useCallback(() => {
    if (!sessionId) return;
    const last = stored[sessionId]?.turns.at(-1);
    if (!last) return;
    pending.current.add(sessionId);
    setLive((all) => ({ ...all, [sessionId]: { ...last, end: undefined } }));
    if (!command(sessionId, { type: "resume", session_id: sessionId })) {
      pending.current.delete(sessionId);
      setLive((all) => without(all, sessionId));
    }
  }, [sessionId, stored, command]);

  /** Delete a conversation. It leaves the list at once; if it is open, the app goes to a new question. */
  const remove = useCallback(
    async (id: string) => {
      setSessions((all) => all.filter((s) => s.id !== id));
      if (id === sessionId) navigate(null);
      try {
        await api.remove(id);
      } catch (e) {
        setServerError(e instanceof Error ? e.message : "The conversation couldn't be deleted.");
        refreshSessions();
      }
    },
    [sessionId, navigate, refreshSessions],
  );

  const liveTurn = sessionId ? live[sessionId] : undefined;
  const running = Boolean(liveTurn && !liveTurn.end);
  const turns = useMemo(
    () => (sessionId ? mergeTurns(stored[sessionId]?.turns ?? [], liveTurn) : []),
    [sessionId, stored, liveTurn],
  );
  const interrupted = Boolean(sessionId && stored[sessionId]?.interrupted && !liveTurn);
  const error = (sessionId && errors[sessionId]) || serverError;

  return {
    sessions, sessionId, turns, running, interrupted, error, model, status,
    send, stop, resume, navigate, remove,
  };
}
