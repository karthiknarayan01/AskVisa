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

Always available too: immigration_data_page(topic), which reads one of the site's statistics
pages (daily PERM decisions, denial rates, USCIS processing times, H-1B lottery odds, visa
interview waits, ...) for data the API doesn't serve. It returns the page's text, read with the
same reader as read_web_page, so a page redesign can change what it gets.

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
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from runtime.tools import Tool

SITE_URL = "https://permtracker.app"
BASE_URL = f"{SITE_URL}/v1"
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
Looks up an employer's record of sponsoring green cards (PERM labor certification) and H-1B visas \
(LCAs), from Department of Labor filings.

Use it for questions about one employer, for example:
- "Does Acme sponsor green cards?" or "Will this company sponsor my H-1B?"
- "How many PERM cases has Google filed, and how many were certified or denied?"
- "Is this employer still filing PERM cases?" (look at the newest filing and filings in the last 12 months)
- comparing job offers by how often each employer sponsors

Don't use it for:
- the status of one case (use perm_case_status)
- processing times, visa bulletin dates or what the rules say

How to use it:
- Pass the employer's name as the user gave it.
- The result says whether the match is exact, possible or none, and what to do for each.
- The numbers are the employer's history, not a prediction for the user's own case.

Returns:
- the match, and for a match: PERM decisions, certified share, pending cases, newest filing, \
filings in the last 12 months, and H-1B LCA and wage request counts
- a first line with the date of the data: mention it when how current the numbers are matters"""

FIND_SOC_CODE = """\
Looks up SOC occupation codes. A SOC code is the government's code for an occupation (e.g. \
15-1252, Software Developers). Every PERM, prevailing wage and H-1B LCA filing is made under one, \
and with the job's location and requirements it sets the minimum wage the employer must offer.

Use it for questions about an occupation rather than an employer, for example:
- "What SOC code would my job, data scientist, be filed under?"
- "What does SOC 15-2051 cover, and which job titles are filed under it?"
- "What's the median offered wage in PERM cases for software developers?"
- "How often are PERM cases in my occupation approved?"
- "My old filing used a 2010 SOC code; what is it now?"

How to use it:
- Pass a job title or a SOC code.
- A title can map to several codes: give the common ones, and say the right code depends on the \
actual job duties, not the title.
- Approval rates and wages are history, not a prediction for the user's case.

Returns:
- for a title: the codes employers filed it under, with how often each was used
- for a code: its title, O*NET description and tasks, common filed titles, PERM approval rate and \
median offered wage, and for a 2010 code the 2018 codes it became
- a first line with the date of the data: mention it when how current the numbers are matters"""

PERM_CASE_STATUS = """\
Looks up one Department of Labor case by its case number.

Use it when the user gives a DOL case number, for example:
- "What's the status of G-100-26045-123456?" (PERM cases start with G-100)
- "Was my prevailing wage request P-100-… issued?" (wage requests start with P-100)
- "Is LCA I-200-… certified?" (H-1B LCAs start with I-200)
- H-2A, H-2B and CW-1 cases

Don't use it for:
- USCIS receipt numbers (three letters then ten digits, like IOE0912345678 or EAC2190012345): \
those are not DOL cases and this tool can't look them up

How to use it:
- If the user wants a case's status but hasn't given the number, ask for it.
- Set live to true only when a recent case number isn't found, to ask DOL directly (limited to \
200 a day).

Returns:
- the status, filing date and employer
- once decided, DOL's record of the decision: outcome, dates, job, wage and worksite"""

PERM_DECISION_ESTIMATE = """\
Estimates when the Department of Labor will decide a pending PERM case, from its filing date.

Use it for questions like:
- "When will my PERM be approved?"
- "My PERM was filed in February 2026; how much longer?"

Don't use it for:
- a case that is already decided (use perm_case_status)

How to use it:
- It needs the filing date. If the user gave a case number instead, call perm_case_status first \
to get it; if they gave neither, ask.
- Say it is an estimate based on how DOL has been processing cases, not a DOL prediction.
- Give the range, not just one date.

Returns:
- a likely decision date, a range and caveats"""

PERM_QUEUE = """\
The Department of Labor's current PERM processing times, and how many PERM cases are pending for \
each filing month.

Use it for questions like:
- "How long is PERM taking right now?"
- "Which filing month is DOL working on?"
- "How big is the PERM backlog?"
- putting the user's own filing date in context: how many cases filed before theirs are still waiting

Don't use it for:
- one case's estimated decision date (use perm_decision_estimate)

Returns:
- current processing times, and pending cases by filing month"""

VISA_BULLETIN = """\
A month's State Department visa bulletin: the priority date cutoffs for green cards.

Use it for questions like:
- "Is my priority date current?"
- "What's the EB-2 India cutoff this month?"
- "How far did EB-3 move since last month?"

How to use it:
- Pass month as YYYY-MM, or leave it out for the newest bulletin.
- To compare movement, call it for both months.
- A priority date is current when it is earlier than the cutoff for the user's category and \
country; "C" means current for everyone.
- Which chart applies to filing an I-485 is set by USCIS each month: say so rather than assuming.

Returns:
- cutoff dates for the employment-based (EB-1 to EB-5) and family-based categories
- by country of birth: all chargeability, China, India, Mexico, Philippines
- both charts: final action dates and dates for filing"""

SEARCH_EMPLOYERS = """\
Searches employers that have filed PERM cases, by name.

Use it for:
- seeing the candidates when match_employer found no exact match
- browsing an employer's entities, e.g. "which Acme companies file PERM?"

Don't use it for:
- a quick yes or no on one employer's sponsorship (match_employer is enough)

Returns:
- matching employers, each with the slug to pass to employer_profile"""

EMPLOYER_PROFILE = """\
One employer's full PERM record.

Use it for deeper questions about one employer, for example:
- "How long do Google's PERM cases take?"
- "What has Acme filed recently?"
- "How many of their cases are pending, and at what stage?"

How to use it:
- Get the slug from search_employers first.
- The numbers are the employer's history, not a prediction for the user's case.

Returns:
- cases and outcomes, median days from filing to a decision, recent filings, and pending cases by stage"""

SEARCH_LAW_FIRMS = """\
Searches immigration law firms that file PERM cases, by name.

Use it for:
- a law firm the user names, e.g. their employer's firm, or "how does Fragomen do on PERM?"

Returns:
- matching firms, each with the slug to pass to law_firm_profile"""

LAW_FIRM_PROFILE = """\
One law firm's PERM record.

Use it for:
- questions about a law firm's PERM filings, after search_law_firms gives you its slug

How to use it:
- The numbers describe the firm's past filings, not the quality of its advice or the outcome of \
the user's case: never rank or recommend firms from them.

Returns:
- cases and outcomes, and pending cases by stage"""

SEARCH_OCCUPATIONS = """\
Searches occupations that PERM cases were filed under, by title.

Use it for:
- getting from a job title to an occupation's PERM record

Don't use it for:
- finding which SOC code a job title is filed under (find_soc_code is more direct)

Returns:
- matching occupations, each with the slug to pass to occupation_profile"""

OCCUPATION_PROFILE = """\
One occupation's PERM record.

Use it for questions like:
- "How are PERM cases for software developers doing?"
- "What do employers usually offer data scientists in PERM cases?"

How to use it:
- Get the slug from search_occupations first.
- The numbers are history, not a prediction for the user's case.

Returns:
- the SOC code, case outcomes and the median offered wage"""

EXPORT_SEARCH = """\
Returns all the results of a search, past the first page, as JSON.

Use it only when a question needs the complete list, for example:
- "Every PERM case Acme filed in California in 2026"
- "All certified cases for software developers filed last March"

Don't use it for:
- one employer, firm or occupation (the search and profile tools are cheaper)

How to use it:
- kind is cases, employers, law-firms or occupations; query searches by name.
- For cases you can also filter by law firm, worksite state, occupation, filing date range and outcome.
- Long results are clipped: narrow the filters rather than asking for everything.

Returns:
- every matching record"""

# topic -> (path, what the page shows). Only pages with data; the site's calculators run in the
# browser, so their pages have no answers to read.
PAGES: dict[str, tuple[str, str]] = {
    "today": ("/today", "where every step of the green card process stands right now, on one page"),
    "perm_daily_decisions": ("/perm-decision-activity", "how many PERM cases DOL decided each day, certified and denied"),
    "perm_filings_by_day": ("/filings-by-day", "how many PERM cases were filed each day"),
    "perm_processing_times": ("/perm-processing-times", "DOL's PERM processing times and their trend"),
    "perm_denial_rates": ("/perm-denial-risk", "PERM denial rates and what drives them"),
    "perm_rfi_audits": ("/perm-rfi-audit", "how often PERM cases get a request for information or an audit"),
    "perm_layoff_disclosures": ("/perm-layoff-disclosures", "layoffs employers reported on PERM filings"),
    "perm_case_statuses": ("/perm-case-statuses", "what each PERM case status means"),
    "prevailing_wage_requests": ("/pwd-cases", "prevailing wage request (PWD) processing and recent determinations"),
    "uscis_processing_times": ("/uscis-processing-times", "USCIS processing times by form, e.g. I-140, I-485, I-765"),
    "uscis_rfe_rates": ("/uscis-rfe-rates", "how often USCIS issues requests for evidence, by form"),
    "i140_awaiting_visa": ("/i140-awaiting-visa", "approved I-140s waiting for a visa number, by category and country"),
    "i485_by_field_office": ("/i485-by-field-office", "pending I-485 cases and processing by USCIS field office"),
    "nvc_waiting_list": ("/nvc-waiting-list", "the National Visa Center waiting list for immigrant visas"),
    "visa_issuances": ("/visa-issuances", "immigrant and nonimmigrant visas issued, by category and country"),
    "green_card_timelines": ("/green-card-timelines", "green card timelines reported by applicants, step by step"),
    "perm_by_state": ("/perm-by-state", "PERM filings and outcomes by worksite state"),
    "perm_by_city": ("/perm-cities", "PERM filings and outcomes by city"),
    "perm_by_industry": ("/perm-industries", "PERM filings and outcomes by industry"),
    "perm_by_country": ("/perm-countries", "PERM filings and outcomes by the worker's country of citizenship"),
    "perm_wages": ("/perm-wages", "wages offered in PERM cases, by job"),
    "h1b_wages": ("/lca-wages", "H-1B salaries from LCAs, by job and employer"),
    "h1b_employers": ("/h1b-employers", "the employers filing the most H-1B LCAs"),
    "h1b_cap_exempt_employers": ("/h1b-cap-exempt-employers", "H-1B cap-exempt employers (universities, nonprofits, research)"),
    "h1b_lottery_odds": ("/h1b-lottery-odds", "H-1B lottery registrations, selections and the odds of being picked"),
    "opt_employers": ("/opt-employers", "employers of students on OPT and CPT"),
    "visa_wait_times": ("/visa-wait-times", "visa interview appointment waits at US consulates"),
    "visa_refusals": ("/visa-refusals", "why visas are refused, and refusal rates"),
    "layoff_notices": ("/layoffs", "layoff notices (WARN) from sponsoring employers"),
    "debarments": ("/debarments", "employers debarred from sponsoring"),
    "wage_violations": ("/wage-violations", "employers found in violation of visa wage rules"),
    "employers_under_review": ("/perm-employers/under-review", "employers whose PERM cases are under extra DOL review"),
    "law_firm_rankings": ("/law-firm-rankings", "law firms ranked by PERM filings"),
    "policy_changes": ("/policy-changes", "a summary list of recent policy changes; for a rule's text and dates, use search_federal_register first"),
}

IMMIGRATION_DATA_PAGE = """\
Reads a published statistics page on US employment-based immigration, for data the other DOL \
tools don't return.

Pages, by topic:
{topics}

Use it when one of these pages answers the question and no other tool does, for example:
- "How many PERM cases were approved yesterday?" (perm_daily_decisions)
- "What are the odds in the H-1B lottery?" (h1b_lottery_odds)
- "How long is USCIS taking on I-485s?" (uscis_processing_times)
- "How long is the visa interview wait in Mumbai?" (visa_wait_times)

Don't use it for:
- anything perm_case_status, perm_queue, visa_bulletin or the other DOL tools cover: they return \
exact records, while this returns the text of a web page

How to use it:
- Use it before searching the web.
- Read the numbers carefully, and say what date the page gives for its data.
- If the page doesn't hold the answer, say so or try web_search.

Returns:
- the page's text, with tables as text, up to 20,000 characters"""


def permtracker_tools(
    client: PermTrackerClient | None = None, read_page: Callable[[str], str] | None = None
) -> list[Tool]:
    """The keyless tools and the page reader, plus the keyed tools when the client has a key.

    `read_page(url)` returns a page's text; it defaults to read_web_page's reader.
    """
    client = client or PermTrackerClient()
    read_page = read_page or _default_page_reader()
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
    tools.append(
        _tool(
            "immigration_data_page",
            IMMIGRATION_DATA_PAGE.format(
                topics="\n".join(f"- {topic}: {about}" for topic, (_, about) in PAGES.items())
            ),
            {"topic": {"type": "string", "enum": list(PAGES), "description": "Which page to read"}},
            ["topic"],
            lambda topic: data_page(read_page, topic),
        )
    )
    if client.api_key:
        tools += keyed_tools(client)
    return tools


def data_page(read_page: Callable[[str], str], topic: str) -> str:
    """The text of the page for `topic`, under a line naming what it shows."""
    path, about = PAGES[topic]
    return f"Page: {about}\n\n{read_page(SITE_URL + path)}"


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


class DataLookupError(Exception):
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
            raise DataLookupError("the lookup didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise DataLookupError(f"couldn't reach the lookup service: {e}") from None
        if response.status_code >= 400:
            raise DataLookupError(_error_message(response))
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


def _default_page_reader() -> Callable[[str], str]:
    """read_web_page's reader, imported here so tests can pass their own without ddgs."""
    from ddgs import DDGS

    from runtime.web_tools import TIMEOUT_SECONDS as WEB_TIMEOUT, read_web_page

    search = DDGS(timeout=WEB_TIMEOUT)
    return lambda url: read_web_page(search, url)


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
