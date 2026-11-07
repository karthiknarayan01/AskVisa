"""PERM Tracker tools: DOL case, employer, occupation and visa bulletin data from permtracker.app.

PERM Tracker (https://permtracker.app) republishes the Department of Labor's PERM, prevailing
wage and H-1B LCA data, DOL processing times, the State Department visa bulletin and O*NET
occupation data, and serves it through a JSON API (https://permtracker.app/developers).

Always available (no API key):
    match_employer(name)                GET /lookup/employer
    find_soc_code(query)                GET /lookup/soc

Only when PERMTRACKER_API_KEY is set, so the model never sees a tool that can only fail:
    perm_case_status(case_number, live?) GET /cases/{caseNumber}
    perm_decision_estimate(filed_date)   GET /estimate
    perm_queue()                         GET /queue
    visa_bulletin(month?)                GET /visa-bulletin
    search_employers(query)              GET /employers
    employer_profile(slug)               GET /employers/{slug}
    search_law_firms(query)              GET /law-firms
    law_firm_profile(slug)               GET /law-firms/{slug}
    search_occupations(query)            GET /occupations
    occupation_profile(slug)             GET /occupations/{slug}
    export_search(kind, ...)             GET /exports/{kind}

Not tools: /me (the key's own usage), and /webhooks and /watches (they deliver events to an
https endpoint the user runs). The key is sent as a Bearer token; free keys allow 10 calls a
minute and 300 a day, the lookups 60 a minute.

Answers come back as {"data": ..., "meta": {"source", "asOf", "url"}}. A tool returns a first
line with the date of the data, then the data as JSON. match_employer adds a line between them
saying whether the match is exact, possible or none and what to do about it, so that guidance is
in front of the model when it reads the numbers.

The descriptions say when to use each tool and when not to; the system prompt only says what
the group is for. All tools only read, so they need no approval and are safe to re-run.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from runtime.tools import Tool

BASE_URL = "https://permtracker.app/v1"
API_KEY_ENV = "PERMTRACKER_API_KEY"
EXPORT_KINDS = ["cases", "employers", "law-firms", "occupations"]
TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 20_000


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
months, and H-1B LCA and wage request counts. The first line gives the date of the data: \
mention it when how current the numbers are matters."""

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
rates and wages are history, not a prediction for the user's case. The first line gives the date \
of the data: mention it when how current the numbers are matters."""

PERM_CASE_STATUS = """\
Look up one Department of Labor case by its case number: its status, when it was filed, the \
employer, and once decided, DOL's record of the decision (outcome, dates, job, wage, worksite).

Use it when the user gives a DOL case number, for example:
- "What's the status of G-100-26045-123456?" (PERM cases start with G-100)
- "Was my prevailing wage request P-100-… issued?" (wage requests start with P-100)
- "Is LCA I-200-… certified?" (H-1B LCAs start with I-200)
- H-2A, H-2B and CW-1 cases too, with workers, work period and worksite

If the user wants a case's status but hasn't given the number, ask for it. USCIS receipt numbers \
(three letters then ten digits, like IOE0912345678 or EAC2190012345) are not DOL cases: this tool \
can't look those up. Set live to true only if a recent case number isn't found, to ask DOL \
directly (limited to 200 a day)."""

PERM_DECISION_ESTIMATE = """\
Estimate when the Department of Labor will decide a pending PERM case, from its filing date: a \
likely date, a range and caveats.

Use it for "when will my PERM be approved?", "my PERM was filed in February 2026, how much longer?" \
and similar. It needs the filing date: if the user gave a case number instead, call \
perm_case_status first to get it; if they gave neither, ask. Say it is an estimate based on how \
DOL has been processing cases, not a DOL prediction, and give the range, not just one date. \
For a case that is already decided, use perm_case_status instead."""

PERM_QUEUE = """\
The Department of Labor's current PERM processing times, and how many PERM cases are pending for \
each filing month.

Use it for "how long is PERM taking right now?", "which filing month is DOL working on?", "how big \
is the PERM backlog?", or to put the user's own filing date in context (how many cases filed \
before theirs are still waiting). For one case's estimated decision date, use \
perm_decision_estimate."""

VISA_BULLETIN = """\
A month's State Department visa bulletin: the cutoff dates for employment-based (EB-1 to EB-5) \
and family-based categories, by country of birth (all chargeability, China, India, Mexico, \
Philippines), in both charts: final action dates and dates for filing.

Use it for "is my priority date current?", "what's the EB-2 India cutoff this month?", "how far \
did EB-3 move since last month?" and similar. Pass month as YYYY-MM, or leave it out for the \
newest bulletin; call it for two months to compare movement. A priority date is current when it \
is earlier than the cutoff in the user's category and country ("C" means current for everyone). \
Which chart applies to filing an I-485 is set by USCIS each month; say so rather than assuming."""

SEARCH_EMPLOYERS = """\
Search employers that have filed PERM cases, by name. Returns the matching employers, each with \
the slug to pass to employer_profile.

Use it when match_employer found no exact match and you want to see the candidates, or when the \
user asks to browse employers ("which Acme entities file PERM?"). For a quick yes/no on one \
employer's sponsorship, match_employer is enough."""

EMPLOYER_PROFILE = """\
One employer's full PERM record: its cases and outcomes, median days from filing to a decision, \
recent filings, and pending cases by stage.

Use it after search_employers (or match_employer) gives you the employer's slug, for deeper \
questions: "how long do Google's PERM cases take?", "what has Acme filed recently?", "how many of \
their cases are pending, and at what stage?". The numbers are the employer's history, not a \
prediction for the user's case."""

SEARCH_LAW_FIRMS = """\
Search immigration law firms that file PERM cases, by name. Returns matching firms, each with \
the slug to pass to law_firm_profile.

Use it when the user names their employer's law firm or asks about one ("how does Fragomen do on \
PERM?")."""

LAW_FIRM_PROFILE = """\
One law firm's PERM record (cases and outcomes) and its pending cases by stage.

Use it after search_law_firms gives you the firm's slug. Its numbers describe the firm's past \
filings, not the quality of its advice or the outcome of the user's case: never rank or \
recommend firms from them."""

SEARCH_OCCUPATIONS = """\
Search occupations that PERM cases were filed under, by title. Returns matching occupations, \
each with the slug to pass to occupation_profile.

Use it to get from a job title to an occupation's PERM record. To find which SOC code a job \
title is filed under, find_soc_code is more direct."""

OCCUPATION_PROFILE = """\
One occupation's PERM record: its SOC code, case outcomes, and the median offered wage.

Use it after search_occupations gives you the slug, for "how are PERM cases for software \
developers doing?" or "what do employers usually offer data scientists in PERM cases?". The \
numbers are history, not a prediction for the user's case."""

EXPORT_SEARCH = """\
All the results of a search, past the first page, as JSON. kind is cases, employers, law-firms \
or occupations; query searches by name. For cases you can also filter by law firm, worksite \
state, occupation, filing date range and outcome.

Use it only when a question needs the complete list, for example "every PERM case Acme filed in \
California in 2026" or "all certified cases for software developers filed last March". For one \
employer, firm or occupation, the search and profile tools are cheaper. Long results are clipped: \
narrow the filters rather than asking for everything."""


def permtracker_tools(client: PermTrackerClient | None = None) -> list[Tool]:
    """The keyless tools, plus the keyed ones when the client has an API key."""
    client = client or PermTrackerClient()
    tools = [
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
    if client.api_key:
        tools += keyed_tools(client)
    return tools


def keyed_tools(client: PermTrackerClient) -> list[Tool]:
    """The tools for the endpoints that need an API key."""
    query = _string("Name or words to search for")
    slug = _string("The slug from a search result, e.g. 'google-llc'")
    return [
        _tool(
            "perm_case_status",
            PERM_CASE_STATUS,
            {
                "case_number": _string("The DOL case number, e.g. G-100-26045-123456"),
                "live": {
                    "type": "boolean",
                    "description": "Ask DOL directly, only when a recent case number isn't found",
                },
            },
            ["case_number"],
            lambda case_number, live=False: render(
                client.get(f"/cases/{_segment(case_number)}", {"live": 1} if live else {})
            ),
        ),
        _tool(
            "perm_decision_estimate",
            PERM_DECISION_ESTIMATE,
            {"filed_date": _string("The PERM filing date, YYYY-MM-DD")},
            ["filed_date"],
            lambda filed_date: render(client.get("/estimate", {"filed": filed_date})),
        ),
        _tool("perm_queue", PERM_QUEUE, {}, [], lambda: render(client.get("/queue", {}))),
        _tool(
            "visa_bulletin",
            VISA_BULLETIN,
            {"month": _string("YYYY-MM; the newest bulletin if omitted")},
            [],
            lambda month=None: render(client.get("/visa-bulletin", {"month": month} if month else {})),
        ),
        _tool("search_employers", SEARCH_EMPLOYERS, {"query": query}, ["query"],
              lambda query: render(client.get("/employers", {"q": query}))),
        _tool("employer_profile", EMPLOYER_PROFILE, {"slug": slug}, ["slug"],
              lambda slug: render(client.get(f"/employers/{_segment(slug)}", {}))),
        _tool("search_law_firms", SEARCH_LAW_FIRMS, {"query": query}, ["query"],
              lambda query: render(client.get("/law-firms", {"q": query}))),
        _tool("law_firm_profile", LAW_FIRM_PROFILE, {"slug": slug}, ["slug"],
              lambda slug: render(client.get(f"/law-firms/{_segment(slug)}", {}))),
        _tool("search_occupations", SEARCH_OCCUPATIONS, {"query": query}, ["query"],
              lambda query: render(client.get("/occupations", {"q": query}))),
        _tool("occupation_profile", OCCUPATION_PROFILE, {"slug": slug}, ["slug"],
              lambda slug: render(client.get(f"/occupations/{_segment(slug)}", {}))),
        _tool(
            "export_search",
            EXPORT_SEARCH,
            {
                "kind": {"type": "string", "enum": EXPORT_KINDS},
                "query": _string("Words to search for by name"),
                "firm": _string("cases only: law firm"),
                "state": _string("cases only: worksite state, e.g. CA"),
                "occupation": _string("cases only: occupation"),
                "from_date": _string("cases only: filed on or after, YYYY-MM-DD"),
                "to_date": _string("cases only: filed on or before, YYYY-MM-DD"),
                "outcome": _string("cases only: decision, e.g. certified or denied"),
            },
            ["kind"],
            lambda kind, **filters: render(client.get(f"/exports/{kind}", _export_params(filters))),
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
    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None):
        """`api_key` defaults to PERMTRACKER_API_KEY; `http` is for tests."""
        self.api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "").strip()
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def get(self, path: str, params: dict[str, Any]) -> Any:
        """GET an endpoint and return its decoded JSON (or text, if it isn't JSON)."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = self.http.get(BASE_URL + path, params=params, headers=headers)
        except httpx.TimeoutException:
            raise PermTrackerError("the lookup didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise PermTrackerError(f"couldn't reach the lookup service: {e}") from None
        if response.status_code >= 400:
            raise PermTrackerError(_error_message(response))
        try:
            return response.json()
        except ValueError:
            return response.text


def render(body: Any, note: str | None = None) -> str:
    """A line with the data's date, an optional note, then the data as JSON, clipped."""
    if isinstance(body, str):
        return _clip(body)
    if not isinstance(body, dict) or "data" not in body:
        return _clip(json.dumps(body, ensure_ascii=False))
    as_of = (body.get("meta") or {}).get("asOf")
    dated = f"Department of Labor data as of {as_of}" if as_of else "Department of Labor data"
    lines = [dated, note, json.dumps(body["data"], ensure_ascii=False)]
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
    if response.status_code == 401:
        return f"{text}. The service refused its API key, so this lookup isn't available right now"
    if response.status_code == 404:
        return f"{text}. No record of that"
    if response.status_code == 429:
        wait = response.headers.get("Retry-After")
        return f"{text}. Rate limited" + (f"; retry in {wait} seconds" if wait else "")
    return text


def _export_params(filters: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments -> export query parameters, always as JSON."""
    names = {"query": "q", "from_date": "from", "to_date": "to"}
    params = {names.get(k, k): v for k, v in filters.items() if v not in (None, "")}
    params["format"] = "json"
    return params


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


def _segment(value: str) -> str:
    """A value as one URL path segment, so it can't reach a different endpoint."""
    return quote(value.strip(), safe="")


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… clipped: {len(text):,} characters in all; narrow the search"
