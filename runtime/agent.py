"""What the agent is made of: its model, its tools, and the options every session gets.

`topic_model` is the same model set up for short side requests (naming a conversation from its
first question, runtime/topics.py); without one, conversations are listed under their first
question.

    agent = build_agent("ollama")            the model and tools, from the provider's defaults
    await agent.check()                      fail early if a local model isn't ready
    session = Session.create(log, agent.model, agent.tools, **agent.session_options())

The system prompt is built for each session from today's date and the tools present, so a
session started tomorrow says tomorrow's date. A model that knows its own context window (a local
model is told its size) gets a compaction policy for that window.

No tool needs approval today. Sessions get DenyAll as their approver, so a tool added later with
requires_approval=True is refused rather than run unasked until the web app can ask the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from runtime.approval import DenyAll
from runtime.calculator_tools import calculator_tools
from runtime.compaction import CompactionPolicy
from runtime.federal_register_tools import federal_register_tools
from runtime.model import ModelClient
from runtime.permtracker_tools import permtracker_tools
from runtime.prompts import system_prompt
from runtime.providers import DEFAULT_MODELS, LOCAL_PROVIDERS, make_model, make_topic_model
from runtime.regulation_tools import regulation_tools
from runtime.tools import Tool, ToolRegistry
from runtime.web_tools import web_tools


@dataclass
class Agent:
    provider: str
    model_name: str
    model: ModelClient
    tools: ToolRegistry
    topic_model: ModelClient | None = None

    async def check(self) -> None:
        """For a local model: raise ModelError with what to do if it isn't ready."""
        if self.provider in LOCAL_PROVIDERS:
            await self.model.check()

    def session_options(self) -> dict[str, Any]:
        """The options for Session.create / Session.open."""
        options: dict[str, Any] = {
            "approver": DenyAll(),
            "system_prompt": system_prompt(date.today(), [t.name for t in self.tools.specs()]),
        }
        window = getattr(self.model, "context_window", None)
        if window is not None:
            options["compaction"] = CompactionPolicy(context_window=window)
        return options


def build_agent(provider: str, model: str | None = None, web: bool = True) -> Agent:
    """The agent for a provider, with its default model unless `model` is given."""
    return Agent(
        provider=provider,
        model_name=model or DEFAULT_MODELS[provider],
        model=make_model(provider, model),
        tools=ToolRegistry(agent_tools(web)),
        topic_model=make_topic_model(provider, model),
    )


def agent_tools(web: bool = True) -> list[Tool]:
    """The PERM Tracker tools, the regulations, the Federal Register, the date calculators, and
    the web tools unless turned off."""
    return (
        permtracker_tools() + regulation_tools() + federal_register_tools() + calculator_tools()
        + (web_tools() if web else [])
    )
