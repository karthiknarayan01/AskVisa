"""One-line summaries of tool results, for a UI to show under each tool call.

    web_search      "5 results" / "no results"
    read_web_page   "12,480 characters"
    anything else   the result's first line (the date line, the calculator's answer, ...)
"""

from __future__ import annotations

MAX_SUMMARY_CHARS = 120


def summarize(tool: str, output: str) -> str:
    """A one-line summary of a successful tool result."""
    lines = output.splitlines()
    if tool == "web_search":
        if output == "No results.":
            return "no results"
        count = sum(1 for line in lines if line.startswith("[") and "] " in line)
        return f"{count} result" + ("" if count == 1 else "s")
    if tool == "read_web_page":
        return f"{len(output):,} characters"
    return shorten(lines[0]) if lines else "done"


def shorten(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
