"""PERM Tracker tools: PERM, prevailing wage, H-1B LCA and visa bulletin data from permtracker.app.

PERM Tracker (https://permtracker.app) republishes the Department of Labor's PERM, prevailing
wage (PWD) and LCA case data, DOL processing times, the State Department visa bulletin and
O*NET occupation data, and serves it through a JSON API (https://permtracker.app/developers).
There is one tool per read endpoint:

    perm_case_status(case_number, live?)    GET /cases/{caseNumber}
    perm_decision_estimate(filed_date)      GET /estimate
    perm_queue()                            GET /queue
    visa_bulletin(month?)                   GET /visa-bulletin
    search_employers(query)                 GET /employers
    employer_profile(slug)                  GET /employers/{slug}
    search_law_firms(query)                 GET /law-firms
    law_firm_profile(slug)                  GET /law-firms/{slug}
    search_occupations(query)               GET /occupations
    occupation_profile(slug)                GET /occupations/{slug}
    match_employer(name)                    GET /lookup/employer     no key needed
    find_soc_code(query)                    GET /lookup/soc          no key needed
    export_search(kind, ...)                GET /exports/{kind}      all results, past one page
    permtracker_usage()                     GET /me                  the key's plan and usage

The webhook and watch endpoints (/webhooks, /watches) aren't tools: they deliver events to an
https endpoint the user runs, which a chat has no use for.

Every endpoint except the two lookups needs a key, read from PERMTRACKER_API_KEY (free, from
permtracker.app Settings). Without one, those tools return an error saying how to get one.
Free keys allow 10 calls a minute and 300 a day; the lookups allow 60 a minute without a key.

Answers come back as {"data": ..., "meta": {"source", "asOf", "url"}}. A tool returns a first
line naming the source, its date and its page, which the API terms require to be credited, then
the data as JSON. All tools only read, so they need no approval and are safe to re-run.
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
TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 20_000
EXPORT_KINDS = ["cases", "employers", "law-firms", "occupations"]
GET_A_KEY = (
    f"this needs a PERM Tracker API key: create one for free at https://permtracker.app/developers "
    f"(Settings, API keys) and set {API_KEY_ENV}"
)


def permtracker_tools(client: PermTrackerClient | None = None) -> list[Tool]:
    """The PERM Tracker tools, sharing one client."""
    client = client or PermTrackerClient()
    query = _string("Name or words to search for")
    slug = _string("The page slug from a search result, e.g. 'google-llc'")
    return [
        _tool(
            "perm_case_status",
            "Look up one DOL case by its case number: status, filing date, employer and, once "
            "decided, DOL's record of it. Works for PERM (G-100-…), prevailing wage requests "
            "(P-100-…), H-1B LCAs (I-200-…) and H-2A/H-2B/CW-1 cases.",
            {
                "case_number": _string("e.g. G-100-26045-123456"),
                "live": {
                    "type": "boolean",
                    "description": "Ask DOL directly for a case number PERM Tracker doesn't have "
                    "yet (limited to 200 a day)",
                },
            },
            ["case_number"],
            lambda case_number, live=False: client.get(
                f"/cases/{_segment(case_number)}", {"live": 1} if live else {}
            ),
        ),
        _tool(
            "perm_decision_estimate",
            "Estimate when DOL will decide a pending PERM case filed on a given date, with a range "
            "and caveats. This is PERM Tracker's estimate, not a DOL prediction.",
            {"filed_date": _string("The PERM filing date, YYYY-MM-DD")},
            ["filed_date"],
            lambda filed_date: client.get("/estimate", {"filed": filed_date}),
        ),
        _tool(
            "perm_queue",
            "DOL's current PERM processing times, and how many PERM cases are pending for each "
            "filing month.",
            {},
            [],
            lambda: client.get("/queue"),
        ),
        _tool(
            "visa_bulletin",
            "A month's State Department visa bulletin: the employment-based and family-based "
            "charts (final action dates and dates for filing) by category and country.",
            {"month": _string("YYYY-MM; the newest bulletin if omitted")},
            [],
            lambda month=None: client.get("/visa-bulletin", {"month": month} if month else {}),
        ),
        _tool(
            "search_employers",
            "Search PERM-sponsoring employers by name. Returns matching employers with the slug "
            "for employer_profile.",
            {"query": query},
            ["query"],
            lambda query: client.get("/employers", {"q": query}),
        ),
        _tool(
            "employer_profile",
            "One employer's PERM record: cases, outcomes, median days to a decision, recent "
            "filings and pending cases by stage.",
            {"slug": slug},
            ["slug"],
            lambda slug: client.get(f"/employers/{_segment(slug)}"),
        ),
        _tool(
            "search_law_firms",
            "Search immigration law firms that file PERM cases, by name. Returns the slug for "
            "law_firm_profile.",
            {"query": query},
            ["query"],
            lambda query: client.get("/law-firms", {"q": query}),
        ),
        _tool(
            "law_firm_profile",
            "One law firm's PERM record and its pending cases by stage.",
            {"slug": slug},
            ["slug"],
            lambda slug: client.get(f"/law-firms/{_segment(slug)}"),
        ),
        _tool(
            "search_occupations",
            "Search occupations filed under PERM by title. Returns the slug for "
            "occupation_profile.",
            {"query": query},
            ["query"],
            lambda query: client.get("/occupations", {"q": query}),
        ),
        _tool(
            "occupation_profile",
            "One occupation's PERM record: its SOC code, case outcomes and median offered wage.",
            {"slug": slug},
            ["slug"],
            lambda slug: client.get(f"/occupations/{_segment(slug)}"),
        ),
        _tool(
            "match_employer",
            "Match an employer name as written (e.g. on an offer letter) to its PERM Tracker "
            "record: exact, possible or no match, with PERM decisions, certified share, pending "
            "cases, newest filing and H-1B LCA counts. Needs no API key.",
            {"name": _string("The employer name as written")},
            ["name"],
            lambda name: client.get("/lookup/employer", {"name": name}, needs_key=False),
        ),
        _tool(
            "find_soc_code",
            "For a job title: the SOC occupation codes employers filed it under, with shares. For "
            "a SOC code: its title, O*NET description and tasks, common filed titles, PERM "
            "approval rate and median wage (and, for a 2010 code, the 2018 codes it became). "
            "Needs no API key.",
            {"query": _string("A job title, e.g. 'data scientist', or a SOC code, e.g. 15-2051")},
            ["query"],
            lambda query: client.get("/lookup/soc", {"q": query}, needs_key=False),
        ),
        _tool(
            "export_search",
            "All results of a search, past the first page, as JSON. For cases it can filter by "
            "law firm, state, occupation, filing dates and outcome. Counts as one call.",
            {
                "kind": {"type": "string", "enum": EXPORT_KINDS},
                "query": _string("Words to search for"),
                "firm": _string("cases only: law firm"),
                "state": _string("cases only: worksite state, e.g. CA"),
                "occupation": _string("cases only: occupation"),
                "from_date": _string("cases only: filed on or after, YYYY-MM-DD"),
                "to_date": _string("cases only: filed on or before, YYYY-MM-DD"),
                "outcome": _string("cases only: decision, e.g. certified or denied"),
            },
            ["kind"],
            lambda kind, **filters: client.get(f"/exports/{kind}", _export_params(filters)),
        ),
        _tool(
            "permtracker_usage",
            "The PERM Tracker API key's plan, limits, and calls used today and this month. Not "
            "counted against the limits.",
            {},
            [],
            lambda: client.get("/me"),
        ),
    ]


class PermTrackerError(Exception):
    """An API call that failed, with what to do about it when there is something to do."""


class PermTrackerClient:
    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None):
        """`api_key` defaults to PERMTRACKER_API_KEY; `http` is for tests."""
        self.api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "").strip()
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def get(self, path: str, params: dict[str, Any] | None = None, needs_key: bool = True) -> str:
        """GET an endpoint and return its answer as text for the model."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif needs_key:
            raise PermTrackerError(GET_A_KEY)
        try:
            response = self.http.get(BASE_URL + path, params=params or {}, headers=headers)
        except httpx.TimeoutException:
            raise PermTrackerError("PERM Tracker didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise PermTrackerError(f"couldn't reach PERM Tracker: {e}") from None
        if response.status_code >= 400:
            raise PermTrackerError(_error_message(response))
        return render(response)


def render(response: httpx.Response) -> str:
    """A credit line (source, date, page), then the data as JSON, clipped to MAX_OUTPUT_CHARS."""
    try:
        body = response.json()
    except ValueError:
        return _clip(response.text)
    if not isinstance(body, dict) or "data" not in body:
        return _clip(json.dumps(body, ensure_ascii=False))
    meta = body.get("meta") or {}
    credit = " · ".join(
        str(part) for part in (
            meta.get("source") or "PERM Tracker (permtracker.app)",
            f"as of {meta['asOf']}" if meta.get("asOf") else None,
            meta.get("url"),
        ) if part
    )
    return _clip(f"{credit}\n{json.dumps(body['data'], ensure_ascii=False)}")


def _error_message(response: httpx.Response) -> str:
    """The API's error code and message, with a hint for the ones the user can act on."""
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
        return f"{text}. The API key was refused; {GET_A_KEY}"
    if response.status_code == 404:
        return f"{text}. PERM Tracker has no record of that"
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
