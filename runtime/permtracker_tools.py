"""PERM Tracker tools: employer sponsorship records and SOC occupation codes from permtracker.app.

PERM Tracker (https://permtracker.app) republishes the Department of Labor's PERM, prevailing
wage and H-1B LCA data and O*NET occupation data, and serves it through a JSON API
(https://permtracker.app/developers). These are the endpoints that need no API key:

    match_employer(name)    GET /lookup/employer    an employer's PERM and H-1B LCA record
    find_soc_code(query)    GET /lookup/soc         SOC codes for a job title, or one code's details

They allow 60 calls a minute per address. Answers come back as
{"data": ..., "meta": {"source", "asOf", "url"}}. A tool returns a first line naming the source,
its date and its page, which the API terms require to be credited, then the data as JSON.
match_employer adds a line between them saying whether the match is exact, possible or none and
what to do about it, so that guidance is in front of the model when it reads the numbers.

The descriptions say when to use each tool and when not to; the system prompt only says what
the group is for. Both tools only read, so they need no approval and are safe to re-run.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from runtime.tools import Tool

BASE_URL = "https://permtracker.app/v1"
TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 20_000
DEFAULT_SOURCE = "PERM Tracker (permtracker.app)"


MATCH_NOTES = {
    "exact": "Match: exact. The numbers below are this employer's record.",
    "possible": (
        "Match: possible. Tell the user which employer this is (name and any location) and ask "
        "them to confirm it is theirs before relying on the numbers."
    ),
    "none": (
        "Match: none. No PERM record under this name. That is not proof the employer never "
        "sponsors: filings are under the legal name, so ask for it (on an offer letter or pay "
        "stub) and look it up again."
    ),
}

MATCH_EMPLOYER = """\
Look up an employer's track record of sponsoring green cards (PERM labor certification) and H-1B \
visas (LCAs), from Department of Labor filings.

Use it when the user asks about a specific employer, for example:
- "Does Acme sponsor green cards?" / "Will this company sponsor my H-1B?"
- "How many PERM cases has Google filed? How many were certified or denied?"
- "Is this employer still filing PERM cases?" (see the newest filing and filings in the last 12 months)
- comparing job offers by how often each employer sponsors

Don't use it for the status of one case, processing times, visa bulletin dates or what the rules \
say. Its numbers are the employer's history, not a prediction for the user's own case.

Returns: the match (exact, possible or none, with what to do for each), and for a match the \
employer's PERM decisions, certified share, pending cases, newest filing, filings in the last 12 \
months, and H-1B LCA and wage request counts. The first line credits PERM Tracker with the page \
link and the date of the data: cite both when you use it."""

FIND_SOC_CODE = """\
Look up SOC occupation codes. A SOC code is the government's code for an occupation (e.g. 15-1252 \
Software Developers); every PERM, prevailing wage and H-1B LCA filing is made under one, and with \
the job's location and requirements it sets the minimum wage the employer must offer.

Use it when the user asks about an occupation rather than an employer, for example:
- "What SOC code would my job, data scientist, be filed under?"
- "What does SOC 15-2051 cover, and which job titles are filed under it?"
- "What's the median offered wage in PERM cases for software developers?"
- "How often are PERM cases in my occupation approved?"
- "My old filing used a 2010 SOC code; what is it now?"

Pass a job title or a code. For a title it returns the codes employers filed it under, with how \
often each was used: give the common ones, and say the right code depends on the actual duties, \
not the title. For a code it returns its title, O*NET description and tasks, common filed titles, \
PERM approval rate and median offered wage, and for a 2010 code the 2018 codes it became. Approval \
rates and wages are history, not a prediction for the user's case. The first line credits PERM \
Tracker with the page link and the date of the data: cite both when you use it."""


def permtracker_tools(client: PermTrackerClient | None = None) -> list[Tool]:
    """The PERM Tracker tools, sharing one client."""
    client = client or PermTrackerClient()
    return [
        _tool(
            "match_employer",
            MATCH_EMPLOYER,
            {"name": _string("The employer's name as the user gave it, e.g. 'Google' or 'Acme Corp LLC'")},
            ["name"],
            lambda name: match_employer(client, name),
        ),
        _tool(
            "find_soc_code",
            FIND_SOC_CODE,
            {"query": _string("A job title, e.g. 'data scientist', or a SOC code, e.g. '15-2051'")},
            ["query"],
            lambda query: render(client.get("/lookup/soc", {"q": query})),
        ),
    ]


def match_employer(client: PermTrackerClient, name: str) -> str:
    """The lookup, with a line saying how far to trust the match and what to do next."""
    body = client.get("/lookup/employer", {"name": name})
    data = body.get("data") if isinstance(body, dict) else None
    match = data.get("match") if isinstance(data, dict) else None
    return render(body, MATCH_NOTES.get(match) if isinstance(match, str) else None)


class PermTrackerError(Exception):
    """An API call that failed, with what to do about it when there is something to do."""


class PermTrackerClient:
    def __init__(self, http: httpx.Client | None = None):
        """`http` is for tests."""
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def get(self, path: str, params: dict[str, Any]) -> Any:
        """GET an endpoint and return its decoded JSON (or text, if it isn't JSON)."""
        try:
            response = self.http.get(BASE_URL + path, params=params, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise PermTrackerError("PERM Tracker didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise PermTrackerError(f"couldn't reach PERM Tracker: {e}") from None
        if response.status_code >= 400:
            raise PermTrackerError(_error_message(response))
        try:
            return response.json()
        except ValueError:
            return response.text


def render(body: Any, note: str | None = None) -> str:
    """A credit line (source, date, page), an optional note, then the data as JSON, clipped."""
    if isinstance(body, str):
        return _clip(body)
    if not isinstance(body, dict) or "data" not in body:
        return _clip(json.dumps(body, ensure_ascii=False))
    meta = body.get("meta") or {}
    credit = " · ".join(
        str(part) for part in (
            meta.get("source") or DEFAULT_SOURCE,
            f"as of {meta['asOf']}" if meta.get("asOf") else None,
            meta.get("url"),
        ) if part
    )
    lines = [credit, note, json.dumps(body["data"], ensure_ascii=False)]
    return _clip("\n".join(line for line in lines if line))


def _error_message(response: httpx.Response) -> str:
    """The API's error code and message, with a hint for the ones the model can act on."""
    code, message = "", ""
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            code, message = error.get("code", ""), error.get("message", "")
        elif isinstance(error, str):
            code = error
    except (ValueError, AttributeError):
        pass
    text = f"HTTP {response.status_code}" + (f" {code}" if code else "") + (f": {message}" if message else "")
    if response.status_code == 404:
        return f"{text}. PERM Tracker has no record of that"
    if response.status_code == 429:
        wait = response.headers.get("Retry-After")
        return f"{text}. Rate limited" + (f"; retry in {wait} seconds" if wait else "")
    return text


def _tool(name, description, properties, required, fn) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        fn=fn,
        idempotent=True,
    )


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… clipped: {len(text):,} characters in all; narrow the search"
