"""Sessions for the server: opening them, running one turn at a time each, streaming it, stopping it.

    turns = Turns(log, agent)
    session = turns.session(session_id)          opened once, then kept in memory
    async for chunk in turns.stream(session_id, "question"):   Server-Sent Events, ending with `end`
    turns.cancel(session_id)                     stop the running turn

A turn runs as its own task, not inside the HTTP response. If the browser disconnects, only the
stream stops: the turn finishes and is saved, and the conversation shows it when reopened, the
way chat apps behave. One turn runs per session at a time; starting another raises TurnBusy.

A turn interrupted by a crash stays open in the log. stream() refuses a new message for such a
session (TurnInterrupted); stream_resume() finishes it instead, streamed the same way.

While a turn runs, a comment line (": ping") is sent whenever nothing else has been sent for
PING_SECONDS, so proxies and browsers don't drop a stream while a local model thinks.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from runtime.agent import Agent
from runtime.api_events import sse, to_wire
from runtime.events import EventLog
from runtime.model import ModelError
from runtime.session import CompactionFailed, MaxStepsReached, ReplyTruncated, Session, TurnResult

PING_SECONDS = 15


class TurnBusy(RuntimeError):
    """The session already has a turn running."""


class TurnInterrupted(RuntimeError):
    """The session's last turn was interrupted and must be resumed before a new message."""


class Turns:
    def __init__(self, log: EventLog, agent: Agent):
        self.log = log
        self.agent = agent
        self._sessions: dict[str, Session] = {}
        self._running: dict[str, asyncio.Task] = {}

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

    def stream(self, session_id: str, text: str) -> AsyncIterator[str]:
        """Start a turn for `text` and stream it. Raises TurnBusy or TurnInterrupted first."""
        session = self.session(session_id)
        self._refuse_if_running(session_id)
        if session.needs_resume:
            raise TurnInterrupted("the last turn was interrupted; resume it first")
        return self._start(session, lambda: session.run_turn(text))

    def stream_resume(self, session_id: str) -> AsyncIterator[str]:
        """Finish an interrupted turn and stream it. Raises TurnBusy, or ValueError if nothing to resume."""
        session = self.session(session_id)
        self._refuse_if_running(session_id)
        if not session.needs_resume:
            raise ValueError("there is no interrupted turn to resume")
        return self._start(session, session.resume)

    def cancel(self, session_id: str) -> bool:
        """Stop the running turn; False if none is running."""
        task = self._running.get(session_id)
        if task is None:
            return False
        task.cancel()
        return True

    def _refuse_if_running(self, session_id: str) -> None:
        # A running turn is also an open one, so this is checked before needs_resume.
        if session_id in self._running:
            raise TurnBusy("a turn is already running in this session")

    def _start(self, session: Session, run: Callable[[], Awaitable[TurnResult | None]]) -> AsyncIterator[str]:
        """Run the turn as a task and return the stream of its events."""
        sid = session.session_id
        events = session.subscribe()

        async def turn() -> TurnResult | None:
            try:
                return await run()
            except asyncio.CancelledError:
                session.cancel()
                raise

        task = asyncio.create_task(turn())
        self._running[sid] = task

        def finished(t: asyncio.Task) -> None:
            self._running.pop(sid, None)
            events.close()
            if not t.cancelled():
                t.exception()  # retrieved here; reported to the stream by end_event()

        task.add_done_callback(finished)
        return self._follow(session, events, task)

    async def _follow(self, session: Session, events: Any, task: asyncio.Task) -> AsyncIterator[str]:
        """The turn's events as SSE, a ping while quiet, and `end` last. Stops early on disconnect."""
        iterator = aiter(events)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(anext(iterator), PING_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                except StopAsyncIteration:
                    break
                wire = to_wire(item, session.state)
                if wire is not None:
                    yield sse(*wire)
            yield sse("end", end_event(task))
        finally:
            events.close()


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
