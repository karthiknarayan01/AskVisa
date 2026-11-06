"""The approval panel: what it shows (the arguments) and how answers map."""

import io

import pytest
from rich.console import Console

from runtime.approval import Decision
from runtime.messages import ToolCall
from runtime.terminal_approval import TerminalApprover, parse_answer
from runtime.terminal_input import Terminal


@pytest.mark.parametrize(
    "answer, decision",
    [
        ("y", Decision("approve")),
        ("YES", Decision("approve")),
        ("a", Decision("approve_for_session")),
        ("always", Decision("approve_for_session")),
        ("", Decision("deny")),
        ("n", Decision("deny")),
        ("use b.txt instead", Decision("deny", feedback="use b.txt instead")),
    ],
)
def test_answers_map_to_decisions(answer, decision):
    assert parse_answer(answer) == decision


async def test_panel_shows_the_arguments_shortened():
    out = await _ask(ToolCall("c1", "send", {"to": "sam", "body": "x" * 1_000}))
    assert "Allow send?" in out and "sam" in out and "(1000 characters)" in out
    assert "a always allow send" in out


async def _ask(call):
    out = io.StringIO()
    console = Console(file=out, width=100)
    approver = TerminalApprover(Terminal(lambda prompt: "y", out), console)
    assert await approver.ask(call) == Decision("approve")
    return out.getvalue()
