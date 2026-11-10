"""Sessions for the server: opening them, running one turn at a time each, publishing it live.

    turns = Turns(log, agent, live)
    session = turns.session(session_id)          opened once, then kept in memory
    await turns.start(session_id, "question")    the turn runs; its events go to every tab
    turns.cancel(session_id)                     stop the running turn
    await turns.delete(session_id)               stop it if running, then delete the session
    turns.rename(session_id, "H-1B layoff")      give it a title, and tell every tab

A turn runs as its own task, not tied to any connection. If a tab disconnects, the turn
finishes and is saved, and a tab that connects later is sent everything it has done so far
(snapshot()) before its live events. One turn runs per session at a time; starting another
raises TurnBusy.

A turn interrupted by a crash stays open in the log. start() refuses a new message for such a
session (TurnInterrupted); resume() finishes it instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from runtime.agent import Agent
from runtime.api_events import conversation, to_wire
from runtime.events import EventLog, SessionSummary
from runtime.live import LiveChannel
from runtime.model import ModelError
from runtime.session import CompactionFailed, MaxStepsReached, ReplyTruncated, Session, TurnResult


class TurnBusy(RuntimeError):
    """The session already has a turn running."""


class TurnInterrupted(RuntimeError):
    """The session's last turn was interrupted and must be resumed before a new message."""


@dataclass
class RunningTurn:
    """A turn while it runs: everything a tab needs to show it from the start."""

    session_id: str
    request_id: str | None
    base: list[dict[str, Any]]                  # items recorded before this run (a resumed turn)
    events: list[dict[str, Any]] = field(default_factory=list)   # {"event", "data"}, in order
    task: asyncio.Task | None = None            # the turn itself
    done: asyncio.Task | None = None            # finishes once `end` has been published


class Turns:
    def __init__(self, log: EventLog, agent: Agent, live: LiveChannel | None = None):
        self.log = log
        self.agent = agent
        self.live = live or LiveChannel()
        self._sessions: dict[str, Session] = {}
        self._running: dict[str, RunningTurn] = {}

    def create(self) -> Session:
        session = Session.create(self.log, self.agent.model, self.agent.tools, **self.agent.session_options())
        self._sessions[session.session_id] = session
        return session

    def session(self, session_id: str) -> Session:
        """The session, opened from the log the first time. Raises UnknownSession."""
        if session_id not in self._sessions:
            self._sessions[session_id] = Session.open(
                self.log, session_id, self.agent.model, self.agent.tools, **self.agent.session_options()
            )
        return self._sessions[session_id]

    def is_running(self, session_id: str) -> bool:
        return session_id in self._running

    def start(self, session_id: str, text: str) -> asyncio.Task:
        """Start a turn for `text`. Returns a task that finishes when the turn has ended and its
        `end` event is published. Raises UnknownSession, TurnBusy or TurnInterrupted first."""
        session = self.session(session_id)
        self._refuse_if_running(session_id)
        if session.needs_resume:
            raise TurnInterrupted("the last turn was interrupted; resume it first")
        return self._start(session, lambda: session.run_turn(text), base=[])

    def resume(self, session_id: str) -> asyncio.Task:
        """Finish an interrupted turn, published the same way. Raises UnknownSession, TurnBusy,
        or ValueError if there is nothing to resume."""
        session = self.session(session_id)
        self._refuse_if_running(session_id)
        if not session.needs_resume:
            raise ValueError("there is no interrupted turn to resume")
        rid = session.state.request_id
        base = [i for i in conversation(session.state, self._request_ids(session_id)) if i["request_id"] == rid]
        return self._start(session, session.resume, base=base)

    def cancel(self, session_id: str) -> bool:
        """Stop the running turn; False if none is running."""
        running = self._running.get(session_id)
        if running is None or running.task is None:
            return False
        running.task.cancel()
        return True

    async def delete(self, session_id: str) -> None:
        """Delete the session and its events, and tell every tab. A running turn is stopped first
        and waited for, so it can't write to the session after it is gone. Raises UnknownSession."""
        while (running := self._running.get(session_id)) is not None:  # one started meanwhile too
            self.cancel(session_id)
            await asyncio.wait([running.done])
        self.log.delete_session(session_id)
        self._sessions.pop(session_id, None)
        self.live.publish({"type": "session_deleted", "session_id": session_id})

    def rename(self, session_id: str, title: str) -> dict[str, Any]:
        """Set the session's title (replacing any topic worked out for it), tell every tab, and
        return the new summary. Raises UnknownSession."""
        self.log.set_title(session_id, title)
        summary = self.summary(session_id)
        self.live.publish({"type": "session", "session": summary})
        return summary

    def snapshot(self) -> list[dict[str, Any]]:
        """Every running turn so far, for a tab that has just connected."""
        return [
            {"session_id": r.session_id, "request_id": r.request_id, "base": r.base, "events": list(r.events)}
            for r in self._running.values()
        ]

    def summary(self, session_id: str) -> dict[str, Any]:
        """A session as the sessions list shows it. Raises UnknownSession."""
        return self.describe(self.log.session_summary(session_id))

    def describe(self, s: SessionSummary) -> dict[str, Any]:
        return {"id": s.session_id, "title": s.title, "created_at": s.created_at,
                "updated_at": s.updated_at, "running": self.is_running(s.session_id)}

    def _refuse_if_running(self, session_id: str) -> None:
        # A running turn is also an open one, so this is checked before needs_resume.
        if session_id in self._running:
            raise TurnBusy("a turn is already running in this session")

    def _request_ids(self, session_id: str) -> list[str | None]:
        return [e.request_id for e in self.log.load(session_id) if e.type == "user_message"]

    def _start(self, session: Session, run: Callable[[], Awaitable[TurnResult | None]], base: list) -> asyncio.Task:
        """Run the turn as a task, and publish its events from a second task that follows it."""
        sid = session.session_id
        events = session.subscribe()

        async def turn() -> TurnResult | None:
            try:
                return await run()
            except asyncio.CancelledError:
                session.cancel()
                raise

        running = RunningTurn(sid, session.state.request_id if base else None, base)
        running.task = task = asyncio.create_task(turn())
        task.add_done_callback(lambda _: events.close())
        self._running[sid] = running
        running.done = asyncio.create_task(self._publish(session, events, task, running))
        return running.done

    async def _publish(self, session: Session, events: Any, task: asyncio.Task, running: RunningTurn) -> None:
        """Send each of the turn's events to every tab, keep it for tabs that connect later,
        then `end` once the turn has finished."""
        sid = session.session_id
        async for item in events:
            wire = to_wire(item, session.state)
            if wire is None:
                continue
            name, data = wire
            if name == "user_message":
                running.request_id = data["request_id"]
            self._emit(running, name, data)
            if name == "user_message":
                self.live.publish({"type": "session", "session": self.summary(sid)})
        await asyncio.wait([task])
        self._running.pop(sid, None)
        self._emit(running, "end", {**end_event(task), "request_id": session.state.request_id})
        if self.log.has_session(sid):
            self.live.publish({"type": "session", "session": self.summary(sid)})

    def _emit(self, running: RunningTurn, name: str, data: dict[str, Any]) -> None:
        running.events.append({"event": name, "data": data})
        self.live.publish({"type": "event", "session_id": running.session_id,
                           "request_id": running.request_id, "event": name, "data": data})


def end_event(task: asyncio.Task) -> dict[str, Any]:
    """How the turn finished, for the closing `end` event."""
    if task.cancelled():
        return {"outcome": "cancelled"}
    error = task.exception()
    if error is None:
        result = task.result()
        if result is not None and result.denied_call is not None:
            return {"outcome": "stopped", "message": f"{result.denied_call.name} needs approval, which isn't available here"}
        return {"outcome": "completed", "text": result.text if result else ""}
    if isinstance(error, (MaxStepsReached, ReplyTruncated, CompactionFailed)):
        return {"outcome": "stopped", "message": str(error)}
    if isinstance(error, ModelError):
        return {"outcome": "error", "message": str(error), "retryable": True}
    return {"outcome": "error", "message": f"{type(error).__name__}: {error}"}
