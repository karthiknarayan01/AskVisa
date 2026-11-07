"""Shared helpers for the API tests: an agent on a scripted model, and reading an SSE stream."""

from __future__ import annotations

import asyncio
import json

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


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """The events in an SSE body, skipping comment lines (pings)."""
    events = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        kind = next(line[len("event: "):] for line in lines if line.startswith("event: "))
        data = next(line[len("data: "):] for line in lines if line.startswith("data: "))
        events.append((kind, json.loads(data)))
    return events


async def collect(stream) -> str:
    return "".join([chunk async for chunk in stream])


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
