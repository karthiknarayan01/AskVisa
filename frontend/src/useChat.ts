// The app's state: the sessions list, the open conversation, and the turn being streamed.
// The open session lives in the URL (/c/<id>), so a reload or a shared link opens it again.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, streamTurn } from "./api";
import { applyEvent, fromItems, newTurn, type Turn } from "./turns";
import type { SessionSummary } from "./types";

const sessionFromUrl = () => window.location.pathname.match(/^\/c\/([\w-]+)/)?.[1] ?? null;

export function useChat() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(sessionFromUrl);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [running, setRunning] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState<{ provider: string; model: string } | null>(null);
  const abort = useRef<AbortController | null>(null);

  const refreshSessions = useCallback(() => {
    api.sessions().then(setSessions).catch(() => {});
  }, []);

  useEffect(() => {
    api.config().then(setModel).catch(() => setError("The assistant's server isn't reachable. Start it with ./start.sh."));
    refreshSessions();
    const onPop = () => setSessionId(sessionFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [refreshSessions]);

  // Load the open session's history whenever it changes (and nothing is streaming into it).
  useEffect(() => {
    if (!sessionId) {
      setTurns([]);
      setInterrupted(false);
      return;
    }
    if (running) return;
    let current = true;
    api
      .session(sessionId)
      .then((stored) => {
        if (!current) return;
        setTurns(fromItems(stored.items));
        setInterrupted(stored.interrupted);
      })
      .catch((e) => {
        if (current && e instanceof ApiError && e.status === 404) navigate(null);
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const navigate = useCallback((id: string | null) => {
    window.history.pushState(null, "", id ? `/c/${id}` : "/");
    setSessionId(id);
    setError(null);
  }, []);

  const run = useCallback(
    async (id: string, text: string | null) => {
      setRunning(true);
      setError(null);
      setInterrupted(false);
      const controller = new AbortController();
      abort.current = controller;
      if (text !== null) setTurns((all) => [...all, newTurn(text)]);
      const update = (fn: (turn: Turn) => Turn) =>
        setTurns((all) => (all.length ? [...all.slice(0, -1), fn(all[all.length - 1])] : all));
      try {
        await streamTurn(id, text, (event) => update((turn) => applyEvent(turn, event)), controller.signal);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "The answer stopped unexpectedly.");
          if (text !== null) setTurns((all) => all.slice(0, -1));
        }
      } finally {
        abort.current = null;
        setRunning(false);
        refreshSessions();
      }
    },
    [refreshSessions],
  );

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || running) return;
      let id = sessionId;
      if (!id) {
        id = (await api.create()).id;
        window.history.pushState(null, "", `/c/${id}`);
        setSessionId(id);
      }
      await run(id, question);
    },
    [running, sessionId, run],
  );

  const stop = useCallback(() => {
    if (sessionId && running) api.cancel(sessionId);
  }, [sessionId, running]);

  const resume = useCallback(() => {
    if (sessionId && !running) run(sessionId, null);
  }, [sessionId, running, run]);

  /** Delete a conversation. It leaves the list at once; if it is open, the app goes to a new question. */
  const remove = useCallback(
    async (id: string) => {
      setSessions((all) => all.filter((s) => s.id !== id));
      if (id === sessionId) {
        abort.current?.abort();
        navigate(null);
      }
      try {
        await api.remove(id);
      } catch (e) {
        setError(e instanceof Error ? e.message : "The conversation couldn't be deleted.");
      } finally {
        refreshSessions();
      }
    },
    [sessionId, navigate, refreshSessions],
  );

  return { sessions, sessionId, turns, running, interrupted, error, model, send, stop, resume, navigate, remove };
}
