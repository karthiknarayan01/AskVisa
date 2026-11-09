"""The live channel: one WebSocket per browser tab, carrying every session's turns.

A tab opens one connection (GET /api/ws) and keeps it for as long as it is open. Over it the
tab starts, resumes and stops turns in any session, and the server sends the events of every
running turn, each tagged with its session, so the tab can show progress in conversations
that aren't open and switch between them freely. Every connected tab gets every event, so
two tabs stay in step.

Tab -> server, one JSON object per message:

    {"type": "send",   "session_id", "text"}    start a turn
    {"type": "resume", "session_id"}            finish an interrupted turn
    {"type": "cancel", "session_id"}            stop the running turn

Server -> tab:

    {"type": "hello", "running": [{"session_id", "request_id", "base", "events"}]}
        first, on every connection: each running turn so far. `events` are all its events
        since it started (deltas included), `base` the items it had before it started (the
        earlier steps of a resumed turn), so a tab that reconnects mid-answer rebuilds the
        answer and carries on with the live events that follow.
    {"type": "event", "session_id", "request_id", "event", "data"}
        one event of a running turn (event types in runtime/api_events.py); `end` is last
    {"type": "session", "session": {"id", "title", "created_at", "updated_at", "running"}}
        a session's summary changed: a turn started or ended
    {"type": "session_deleted", "session_id"}
    {"type": "error", "session_id", "message"}
        a command was refused (unknown session, a turn already running, ...); only the tab
        that sent it gets this

The rest of the API (listing, opening, creating and deleting sessions) stays plain HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Any


class Client:
    """One connected tab: the messages waiting to be sent to it, in order."""

    def __init__(self) -> None:
        self.outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def send(self, message: dict[str, Any]) -> None:
        self.outbox.put_nowait(message)


class LiveChannel:
    """Every connected tab. publish() queues a message for all of them, without waiting."""

    def __init__(self) -> None:
        self._clients: set[Client] = set()

    def connect(self, hello: dict[str, Any]) -> Client:
        """A new tab, whose first message is `hello`. Nothing published after this call is
        missed, and nothing published before it is sent, so hello plus what follows is exact."""
        client = Client()
        client.send(hello)
        self._clients.add(client)
        return client

    def disconnect(self, client: Client) -> None:
        self._clients.discard(client)

    def publish(self, message: dict[str, Any]) -> None:
        for client in list(self._clients):
            client.send(message)
