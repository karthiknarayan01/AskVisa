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

PERM = """\
Employer sponsorship and occupation codes: match_employer and find_soc_code

Both tools answer from Department of Labor records, republished by PERM Tracker. PERM (labor certification) is the first step of most employment-based green cards: the employer proves to DOL that no qualified US worker is available for the job. An LCA (Labor Condition Application) is the DOL filing an employer makes before an H-1B petition. A SOC code is the government's code for an occupation (for example 15-1252, Software Developers); every PERM, prevailing wage and LCA filing is made under one, and together with the job's location and requirements it sets the minimum wage the employer must offer.

Use match_employer when the question is about a specific employer's track record of sponsoring, for example:
- "Does Acme sponsor green cards?" or "Will this company sponsor my H-1B?"
- "How many PERM cases has Google filed, and how many were certified or denied?"
- "Is this employer still filing PERM cases?" (look at the newest filing and filings in the last 12 months)
- comparing two job offers by how often each employer sponsors
Pass the employer name as the user wrote it. The result says whether the match is exact, possible or none. For a possible match, name the employer you found and ask the user to confirm it is theirs before relying on the numbers. For no match, say the employer has no PERM record under that name, which is not proof they never sponsor: suggest the legal name (from an offer letter or pay stub) and try again.

Use find_soc_code when the question is about the occupation rather than the employer, for example:
- "What SOC code would my job title, data scientist, be filed under?"
- "What does SOC 15-2051 cover, and what titles are filed under it?"
- "What is the median offered wage for software developers in PERM cases?" or "How often are PERM cases in my occupation approved?"
- "My old filing used a 2010 SOC code; what is it now?"
Pass a job title or a SOC code. A title can map to several codes: give the most common ones with how often each was used, and say the right code depends on the actual job duties, not the title.

Do not use these tools for the status of a specific case, processing times, visa bulletin dates, or what a law or rule says: they don't hold that, so use the other tools or say you can't look it up. A high certified share or a common SOC code is history, not a prediction for the user's own case, so never present it as one.

Whenever you use data from either tool, credit "PERM Tracker (permtracker.app)" with the page link from the first line of the tool result, and say how current the data is (the "as of" date on that line).\
"""

END = "When you have the answer, reply without calling a tool."


def system_prompt(today: date, web: bool, perm: bool = False) -> str:
    """The prompt for a session; `web` and `perm` add the guidance for those tools."""
    parts = [BASE.format(today=today.strftime("%A, %B %d, %Y"))]
    if perm:
        parts.append(PERM)
    if web:
        parts.append(WEB)
    parts.append(END)
    return "\n\n".join(parts)
