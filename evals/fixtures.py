"""The agent's real tools, answering from fixtures instead of calling services.

Each tool keeps its real name, description and input schema, so the model sees exactly what it
sees in production; only the function behind it is replaced. The function returns the case's
fixture for that tool, else the suite default, else a plain "no data" result. A fixture with an
error raises it as the exception the real tool raises (DataLookupError for the DOL tools,
RuntimeError for the web tools), so the error result the model reads looks like a real one.
"""

from __future__ import annotations

from dataclasses import replace

from evals.cases import Case, Fixture, Suite
from runtime.calculator_tools import calculator_tools
from runtime.permtracker_tools import DataLookupError, PermTrackerClient, permtracker_tools
from runtime.tools import Tool, ToolRegistry
from runtime.web_tools import web_tools

NO_FIXTURE = "No data for this request."
WEB_TOOLS = {"web_search", "read_web_page"}


def fixture_registry(suite: Suite, case: Case) -> ToolRegistry:
    """The suite's tool groups, each tool answering from the case's fixtures."""
    return ToolRegistry([_answer_from_fixtures(t, suite, case) for t in real_tools(suite.tools)])


def real_tools(groups: tuple[str, ...]) -> list[Tool]:
    """The production tools for these groups. None of them is called."""
    tools: list[Tool] = []
    if "perm" in groups or "perm_keyed" in groups:
        key = "eval" if "perm_keyed" in groups else ""
        tools += permtracker_tools(PermTrackerClient(api_key=key), read_page=lambda url: "")
    if "calculators" in groups:
        tools += calculator_tools()
    if "web" in groups:
        tools += web_tools(client=object())
    return tools


def _answer_from_fixtures(tool: Tool, suite: Suite, case: Case) -> Tool:
    fixture = case.fixtures.get(tool.name) or suite.defaults.get(tool.name) or Fixture(output=NO_FIXTURE)
    failure = RuntimeError if tool.name in WEB_TOOLS else DataLookupError

    def answer(**arguments: object) -> str:
        if fixture.error is not None:
            raise failure(fixture.error)
        return fixture.output or ""

    return replace(tool, fn=answer)
