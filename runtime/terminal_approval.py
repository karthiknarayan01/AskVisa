"""The terminal's approval prompt: a panel showing the tool call's arguments, then a one-key answer.

    ╭─ Allow send_email? ──────────────────────╮
    │ to       someone@example.com             │
    │ subject  Your appointment                │
    ╰──────────────────────────────────────────╯
      y yes   a always allow send_email   n no   or type what to do instead
    ❯

    y / yes      approve this call
    a / always   approve this tool for the rest of the session
    n / no / ""  deny, and stop the turn
    anything     deny, and send the text to the model as feedback
"""

from __future__ import annotations


from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from runtime.approval import Decision
from runtime.messages import ToolCall
from runtime.terminal_input import Terminal

MAX_ARGUMENT_CHARS = 300


class TerminalApprover:
    def __init__(self, terminal: Terminal, console: Console):
        self.terminal = terminal
        self.console = console

    async def ask(self, call: ToolCall) -> Decision:
        """Show the panel once the display has caught up, then read the answer."""
        await self.terminal.caught_up()
        self.console.print()
        self.console.print(Panel(
            self.describe(call),
            title=Text(f"Allow {call.name}?", style="bold yellow"),
            title_align="left",
            border_style="yellow",
            expand=False,
        ))
        options = Text("  ")
        for key, meaning in (("y", "yes"), ("a", f"always allow {call.name}"), ("n", "no")):
            options.append(key, style="bold")
            options.append(f" {meaning}   ", style="dim")
        options.append("or type what to do instead", style="dim")
        self.console.print(options)
        answer = await self.terminal.ask(_prompt_marker(self.console))
        return parse_answer(answer.strip())

    def describe(self, call: ToolCall) -> RenderableType:
        """The panel's body: each argument, shortened if long."""
        arguments = Table.grid(padding=(0, 2))
        arguments.add_column(style="dim")
        arguments.add_column()
        for name, value in call.arguments.items():
            arguments.add_row(name, _shorten(value))
        return arguments


def parse_answer(answer: str) -> Decision:
    lowered = answer.lower()
    if lowered in {"y", "yes"}:
        return Decision("approve")
    if lowered in {"a", "always"}:
        return Decision("approve_for_session")
    if lowered in {"", "n", "no"}:
        return Decision("deny")
    return Decision("deny", feedback=answer)


def _prompt_marker(console: Console) -> str:
    """"❯ " in yellow, as ANSI text the input backends can display."""
    with console.capture() as capture:
        console.print(Text("❯ ", style="bold yellow"), end="")
    return capture.get()


def _shorten(value: object, limit: int = MAX_ARGUMENT_CHARS) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) > limit:
        return f"{text[:limit]}… ({len(text)} characters)"
    return text
