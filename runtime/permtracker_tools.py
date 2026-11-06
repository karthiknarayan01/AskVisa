"""PERM Tracker tools: employer sponsorship records and SOC occupation codes from permtracker.app.

PERM Tracker (https://permtracker.app) republishes the Department of Labor's PERM, prevailing
wage and H-1B LCA data and O*NET occupation data, and serves it through a JSON API
(https://permtracker.app/developers). These are the endpoints that need no API key:

    match_employer(name)    GET /lookup/employer    an employer's PERM and H-1B LCA record
    find_soc_code(query)    GET /lookup/soc         SOC codes for a job title, or one code's details

They allow 60 calls a minute per address. Answers come back as
{"data": ..., "meta": {"source", "asOf", "url"}}. A tool returns a first line naming the source,
its date and its page, which the API terms require to be credited, then the data as JSON. Both
tools only read, so they need no approval and are safe to re-run.
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


def permtracker_tools(client: PermTrackerClient | None = None) -> list[Tool]:
    """The PERM Tracker tools, sharing one client."""
    client = client or PermTrackerClient()
    return [
        _tool(
            "match_employer",
            "Look up an employer's green card (PERM) and H-1B sponsorship record by name, as "
            "written on an offer letter or job posting. Returns whether it matched (exact, "
            "possible or none), PERM decisions, the share certified, pending cases, the newest "
            "filing, filings in the last 12 months, and H-1B LCA and wage request counts.",
            {"name": _string("The employer name, e.g. 'Google' or 'Acme Corp LLC'")},
            ["name"],
            lambda name: client.get("/lookup/employer", {"name": name}),
        ),
        _tool(
            "find_soc_code",
            "For a job title: the SOC occupation codes employers filed it under in PERM cases, "
            "with how often each was used. For a SOC code: its title, O*NET description and "
            "tasks, common job titles filed under it, the PERM approval rate and median offered "
            "wage, and for a 2010 code the 2018 codes it became.",
            {"query": _string("A job title, e.g. 'data scientist', or a SOC code, e.g. 15-2051")},
            ["query"],
            lambda query: client.get("/lookup/soc", {"q": query}),
        ),
    ]


class PermTrackerError(Exception):
    """An API call that failed, with what to do about it when there is something to do."""


class PermTrackerClient:
    def __init__(self, http: httpx.Client | None = None):
        """`http` is for tests."""
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def get(self, path: str, params: dict[str, Any]) -> str:
        """GET an endpoint and return its answer as text for the model."""
        try:
            response = self.http.get(BASE_URL + path, params=params, headers={"Accept": "application/json"})
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
            meta.get("source") or DEFAULT_SOURCE,
            f"as of {meta['asOf']}" if meta.get("asOf") else None,
            meta.get("url"),
        ) if part
    )
    return _clip(f"{credit}\n{json.dumps(body['data'], ensure_ascii=False)}")


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
