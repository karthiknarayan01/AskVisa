"""Federal Register tools: new and upcoming immigration rules and notices, from federalregister.gov.

The Federal Register is the government's daily journal: every new or proposed rule, and notices
such as TPS designations and fee changes, are published there with their effective dates and
comment periods. Its API (https://www.federalregister.gov/developers) is free and needs no key:

    search_federal_register(query, ...)            GET /api/v1/documents.json
    read_federal_register_document(document_number) GET /api/v1/documents/{number}.json, then its text

Searches are limited to the immigration agencies by default (AGENCIES). If the API rejects the
agency filter, the search is retried without it and the result says so, so one renamed agency
can't break every search.

The regulation tools give the rule as it stands today; these give what changed, when, and what is
coming. Both tools only read: no approval, safe to re-run.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from runtime.tools import Tool

BASE_URL = "https://www.federalregister.gov/api/v1"
TIMEOUT_SECONDS = 20
MAX_RESULTS = 8
MAX_ABSTRACT_CHARS = 500
MAX_TEXT_CHARS = 12_000
DOCUMENT_NUMBER = re.compile(r"^[A-Za-z0-9-]{4,20}$")

AGENCIES = {
    "uscis": "u-s-citizenship-and-immigration-services",
    "dhs": "homeland-security-department",
    "ice": "u-s-immigration-and-customs-enforcement",
    "cbp": "u-s-customs-and-border-protection",
    "state": "state-department",
    "labor": "labor-department",
    "eta": "employment-and-training-administration",
    "eoir": "executive-office-for-immigration-review",
}
TYPES = {"rule": "RULE", "proposed_rule": "PRORULE", "notice": "NOTICE", "presidential": "PRESDOCU"}
TYPE_NAMES = {"Rule": "Final rule", "Proposed Rule": "Proposed rule", "Notice": "Notice",
              "Presidential Document": "Presidential document"}
FIELDS = [
    "document_number", "title", "type", "action", "abstract", "publication_date", "effective_on",
    "comments_close_on", "agencies", "html_url", "citation",
]

SEARCH_FEDERAL_REGISTER = """\
Searches the Federal Register, the government's official journal of new and proposed rules and \
notices, for immigration documents: what changed, when it takes effect, and what is proposed.

Use it for questions like:
- "Did the H-1B lottery rules change this year?" or "Is there a new rule on…?"
- "When does the new USCIS fee rule take effect?"
- "Was TPS for my country extended, and until when?"
- "Is a rule on this proposed? Can I still comment on it?"

Don't use it for:
- what a rule says today (use search_regulations and read_regulation)
- processing times, case status or forms (use the other tools or the web)

How to use it:
- query is the words to search for, e.g. "H-1B registration", "temporary protected status Haiti".
- document_type narrows it: rule (final rules), proposed_rule, notice, presidential.
- agency narrows it to one agency; by default all immigration agencies are searched.
- since (YYYY-MM-DD) keeps only documents published on or after that date.
- Results are newest first. A final rule's effective date can be in the future: say whether it is \
already in effect.
- Then call read_federal_register_document for the one that answers the question.

Returns:
- up to 8 documents, each with its publication date, type, agency, document number, title, \
effective date and comment deadline when it has them, a short abstract, and its page"""

READ_FEDERAL_REGISTER_DOCUMENT = """\
Reads one Federal Register document: its dates, summary and text.

Use it for:
- the details of a rule or notice found with search_federal_register: what exactly it changes, \
who it applies to, when it takes effect, and how to comment on a proposed rule

How to use it:
- document_number comes from a search result, e.g. "2026-21456".
- Say the document's effective date and whether it is already in effect, and give its page.

Returns:
- the title, type, agencies, publication and effective dates, comment deadline, citation and page
- the document's text from the start, up to 12,000 characters (the summary and dates come first)"""


def federal_register_tools(client: FederalRegisterClient | None = None) -> list[Tool]:
    """The two Federal Register tools, sharing one client."""
    client = client or FederalRegisterClient()
    return [
        _tool(
            "search_federal_register",
            SEARCH_FEDERAL_REGISTER,
            {
                "query": {"type": "string", "description": "Words to search for"},
                "document_type": {"type": "string", "enum": list(TYPES)},
                "agency": {"type": "string", "enum": list(AGENCIES), "description": "One agency; all immigration agencies if omitted"},
                "since": {"type": "string", "description": "Published on or after this date, YYYY-MM-DD"},
            },
            ["query"],
            lambda query, document_type=None, agency=None, since=None: search_federal_register(
                client, query, document_type, agency, since
            ),
        ),
        _tool(
            "read_federal_register_document",
            READ_FEDERAL_REGISTER_DOCUMENT,
            {"document_number": {"type": "string", "description": "From a search result, e.g. '2026-21456'"}},
            ["document_number"],
            lambda document_number: read_document(client, document_number),
        ),
    ]


def search_federal_register(
    client: FederalRegisterClient,
    query: str,
    document_type: str | None = None,
    agency: str | None = None,
    since: str | None = None,
) -> str:
    """Matching documents, newest first."""
    params: list[tuple[str, Any]] = [("conditions[term]", query), ("order", "newest"), ("per_page", MAX_RESULTS)]
    params += [("fields[]", field) for field in FIELDS]
    if document_type:
        params.append(("conditions[type][]", TYPES[document_type]))
    if since:
        params.append(("conditions[publication_date][gte]", since))
    slugs = [AGENCIES[agency]] if agency else list(AGENCIES.values())
    note = ""
    try:
        body = client.get_json("/documents.json", params + [("conditions[agencies][]", s) for s in slugs])
    except AgencyFilterRejected:
        body = client.get_json("/documents.json", params)
        note = " (the agency filter was rejected, so these are from all agencies)"
    results = body.get("results") or []
    if not results:
        return f"No Federal Register documents match{note}."
    lines = [f"{body.get('count', len(results))} Federal Register documents match, newest first{note}:"]
    for doc in results:
        lines += [""] + describe(doc, abstract=True)
    return "\n".join(lines)


def read_document(client: FederalRegisterClient, document_number: str) -> str:
    """The document's details, then its text from the start."""
    number = document_number.strip()
    if not DOCUMENT_NUMBER.match(number):
        raise ValueError(f"document_number must look like '2026-21456', got {document_number!r}")
    doc = client.get_json(f"/documents/{number}.json", [("fields[]", f) for f in FIELDS + ["raw_text_url", "dates"]])
    lines = describe(doc, abstract=False)
    if doc.get("dates"):
        lines.append(f"Dates: {_plain(doc['dates'])}")
    text = ""
    if doc.get("raw_text_url"):
        text = " ".join(client.get_text(doc["raw_text_url"]).split())
    if not text:
        text = _plain(doc.get("abstract") or "") or "(no text available)"
    clipped = ""
    if len(text) > MAX_TEXT_CHARS:
        text, clipped = text[:MAX_TEXT_CHARS], f"\n\n… clipped; the full document is at {doc.get('html_url', '')}"
    return "\n".join(lines) + f"\n\n{text}{clipped}"


def describe(doc: dict[str, Any], abstract: bool) -> list[str]:
    """A document's identifying lines: date, type, agency, number; title; dates; abstract; page."""
    agencies = ", ".join(a.get("name") or a.get("raw_name") or "" for a in doc.get("agencies") or []) or "unknown agency"
    kind = TYPE_NAMES.get(doc.get("type", ""), doc.get("type") or "Document")
    lines = [
        f"{doc.get('publication_date', '?')} · {kind} · {agencies} · document {doc.get('document_number', '?')}",
        _plain(doc.get("title") or "(untitled)"),
    ]
    dates = []
    if doc.get("effective_on"):
        dates.append(f"effective {doc['effective_on']}")
    if doc.get("comments_close_on"):
        dates.append(f"comments close {doc['comments_close_on']}")
    if doc.get("citation"):
        dates.append(doc["citation"])
    if dates:
        joined = "; ".join(dates)
        lines.append(joined[0].upper() + joined[1:])
    if abstract and doc.get("abstract"):
        text = _plain(doc["abstract"])
        lines.append(text if len(text) <= MAX_ABSTRACT_CHARS else text[:MAX_ABSTRACT_CHARS] + "…")
    if doc.get("html_url"):
        lines.append(doc["html_url"])
    return lines


class FederalRegisterError(Exception):
    """A Federal Register request that failed."""


class AgencyFilterRejected(FederalRegisterError):
    """The API refused an agency slug; the caller retries without the filter."""


class FederalRegisterClient:
    def __init__(self, http: httpx.Client | None = None):
        """`http` is for tests."""
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)

    def get_json(self, path: str, params: list[tuple[str, Any]]) -> Any:
        response = self._get(BASE_URL + path, params)
        if response.status_code == 400 and "agencies" in response.text:
            raise AgencyFilterRejected(response.text[:200])
        self._raise_for(response)
        return response.json()

    def get_text(self, url: str) -> str:
        if not url.startswith("https://www.federalregister.gov/"):
            raise FederalRegisterError(f"refusing to fetch text from outside federalregister.gov: {url}")
        response = self._get(url, [])
        self._raise_for(response)
        return response.text

    def _get(self, url: str, params: list[tuple[str, Any]]) -> httpx.Response:
        try:
            return self.http.get(url, params=params)
        except httpx.TimeoutException:
            raise FederalRegisterError("the Federal Register didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise FederalRegisterError(f"couldn't reach the Federal Register: {e}") from None

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code == 404:
            raise FederalRegisterError("no such Federal Register document (HTTP 404)")
        if response.status_code >= 400:
            raise FederalRegisterError(f"the Federal Register returned HTTP {response.status_code}: {response.text[:200]}")


def _plain(text: str) -> str:
    """Text without HTML tags or extra whitespace."""
    return " ".join(re.sub(r"<[^>]+>", "", text).split())


def _tool(name, description, properties, required, fn) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        fn=fn,
        idempotent=True,
    )
