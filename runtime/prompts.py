"""The system prompt the CLI gives the model.

It states today's date, since a model can't know it otherwise and needs it to judge what
"latest" or "this week" means and how old a search result is. It is built fresh on every start,
so a resumed session gets the current date.
"""

from __future__ import annotations

from datetime import date

BASE = (
    "You are a helpful assistant working in the user's terminal. Today's date is {today}. "
    "Use the tools to look things up instead of guessing."
)

WEB = (
    "For current events, news, recent releases, prices, or anything that may have changed since "
    "your training, search the web before answering: use web_search with topic \"news\" for news, "
    "and a time_range when recency matters. Use read_web_page when a result's snippet isn't "
    "enough. Prefer the most recent sources, mention dates, and list the URLs you relied on."
)

PERM = (
    "For whether and how often an employer sponsors green cards (PERM) or H-1B visas, and for "
    "which SOC occupation code fits a job, use match_employer and find_soc_code: they hold "
    "Department of Labor data. Credit PERM Tracker (permtracker.app) with the page link from "
    "the tool result whenever you use its data, and say how current it is."
)

END = "When you have the answer, reply without calling a tool."


def system_prompt(today: date, web: bool, perm: bool = False) -> str:
    """The prompt for a session; `web` and `perm` add the guidance for those tools."""
    parts = [BASE.format(today=today.strftime("%A, %B %d, %Y"))]
    if perm:
        parts.append(PERM)
    if web:
        parts.append(WEB)
    parts.append(END)
    return " ".join(parts)
