"""Shared helpers for the API tests: an agent on a scripted model, and a tab on the live channel."""

from __future__ import annotations

import asyncio

from runtime.agent import Agent
from runtime.events import EventLog
from runtime.migrate import migrate
from runtime.model import ModelReply
from runtime.tools import Tool, ToolRegistry
from tests.fake_model import FakeModel

ECHO = Tool(
    "echo", "Echo the text back.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    lambda text: f"echo: {text}\nsecond line",
    idempotent=True,
)


def make_agent(replies: list[ModelReply], model=None) -> Agent:
    return Agent("anthropic", "test-model", model or FakeModel(list(replies)), ToolRegistry([ECHO]))


def make_log(tmp_path) -> EventLog:
    db = str(tmp_path / "api.db")
    migrate(db)
    return EventLog(db)


def tab(turns):
    """A connected tab, as the WebSocket handler makes one: its hello, then everything published."""
    return turns.live.connect({"type": "hello", "running": turns.snapshot()})


def drain(client) -> list[dict]:
    """Every message queued for the tab so far."""
    messages = []
    while not client.outbox.empty():
        messages.append(client.outbox.get_nowait())
    return messages


def events(messages: list[dict], session_id: str | None = None, keep_ids: bool = False) -> list[tuple[str, dict]]:
    """The turn events in a tab's messages, optionally for one session, as (event, data).
    request_id is dropped from data unless keep_ids, so tests can compare whole events."""
    found = []
    for m in messages:
        if m["type"] != "event" or (session_id is not None and m["session_id"] != session_id):
            continue
        data = dict(m["data"])
        if not keep_ids:
            data.pop("request_id", None)
        found.append((m["event"], data))
    return found


class BlockingModel:
    """A model whose first reply waits until released, to hold a turn open."""

    def __init__(self, reply: ModelReply):
        self.reply = reply
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, system, messages, tools, on_delta=None):
        self.started.set()
        await self.release.wait()
        return self.reply
