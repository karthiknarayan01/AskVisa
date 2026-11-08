"""Regulation tools: the current text of the federal immigration regulations, from the eCFR.

The eCFR (https://www.ecfr.gov) is the official, continuously updated Code of Federal
Regulations, published by the National Archives with a free JSON/XML API that needs no key:

    search_regulations(query, title?)              GET /api/search/v1/results
    read_regulation(section, paragraph?, title?)   GET /api/versioner/v1/full/{date}/title-{title}.xml

Three titles hold US immigration rules: 8 (DHS: visas and status, USCIS benefits, naturalization),
20 (Department of Labor: PERM in part 656, LCAs and prevailing wage in part 655) and 22 (State
Department: visas and consular processing, parts 40-53). Both tools ask for the text in force on
the title's "up to date as of" date (GET /api/versioner/v1/titles.json, read once per client), so
answers are the current rule, and say that date.

A section can be very long (8 CFR 214.2 is the whole nonimmigrant classification rulebook), so
read_regulation can start at a paragraph, e.g. "(f)(10)(ii)": it finds each label in turn, in
document order, and returns the text from there.

Both tools only read, so they need no approval and are safe to re-run.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from runtime.tools import Tool, awaited

BASE_URL = "https://www.ecfr.gov/api"
TITLES = {8: "Aliens and Nationality", 20: "Employees' Benefits", 22: "Foreign Relations"}
TIMEOUT_SECONDS = 20
MAX_RESULTS = 8
MAX_TEXT_CHARS = 12_000
SECTION = re.compile(r"^\d+\.\d+[a-z]?$")
LABEL = re.compile(r"\(([^()\s]{1,6})\)")
TEXT_TAGS = {"HEAD", "HD", "P", "FP", "NOTE", "EXTRACT"}

SEARCH_REGULATIONS = """\
Searches the current federal immigration regulations (the Code of Federal Regulations) by words, \
and returns the matching sections with a short excerpt each.

Use it for:
- finding which section holds a rule, before reading it, for example "OPT unemployment", "H-1B \
grace period", "priority date retention", "PERM recruitment", "cap-gap"
- questions about what the rules say, when you don't know the section number

Don't use it for:
- news, recent changes not yet in force, processing times, fees on a form, or how-to steps: use \
the other tools or the web

How to use it:
- title is 8 (DHS and USCIS rules, the default), 20 (Department of Labor: PERM in part 656, LCAs \
and prevailing wage in part 655) or 22 (State Department: visas and consular processing).
- Then call read_regulation for the section that fits, to quote the actual text.

Returns:
- up to 8 sections, each with its number, heading and a short matching excerpt
- the date the regulations are current through"""

READ_REGULATION = """\
Reads the exact current text of one section of the federal immigration regulations, optionally \
starting at a paragraph.

Use it for:
- quoting the rule itself when the user asks what the rules say, so the answer rests on the text, \
not on memory, for example 8 CFR 214.2(f)(10)(ii)(E) for OPT unemployment, 8 CFR 214.1(l)(2) for \
the 60-day grace period, 8 CFR 204.5(e) for priority date retention, 20 CFR 656.17 for PERM \
recruitment
- checking a citation before you give it

How to use it:
- section is the section number, e.g. "214.2"; title is 8 (default), 20 or 22.
- paragraph starts the text at that paragraph, e.g. "(f)(10)(ii)": use it for long sections, \
since a whole section can be clipped.
- Quote the relevant sentence, cite it as "8 CFR 214.2(f)(10)(ii)(E)", and explain it in plain words.
- Regulations can lag behind newer laws or court rulings; if a question turns on a recent \
change, check the web as well.

Returns:
- the section heading and its text (from the paragraph when given), up to 12,000 characters
- the date the text is current through"""


def regulation_tools(client: RegulationClient | None = None) -> list[Tool]:
    """The two eCFR tools, sharing one client."""
    client = client or RegulationClient()
    title = {"type": "integer", "enum": list(TITLES), "description": "8 (default), 20 or 22"}
    return [
        _tool(
            "search_regulations",
            SEARCH_REGULATIONS,
            {"query": {"type": "string", "description": "Words to search for"}, "title": title},
            ["query"],
            lambda query, title=8: search_regulations(client, query, title),
        ),
        _tool(
            "read_regulation",
            READ_REGULATION,
            {
                "section": {"type": "string", "description": "The section number, e.g. '214.2'"},
                "paragraph": {"type": "string", "description": "Start at this paragraph, e.g. '(f)(10)(ii)'"},
                "title": title,
            },
            ["section"],
            lambda section, paragraph=None, title=8: read_regulation(client, section, paragraph, title),
        ),
    ]


async def search_regulations(client: RegulationClient, query: str, title: int = 8) -> str:
    """Matching sections, each with its heading and an excerpt."""
    current = await client.current_date(title)
    body = await client.get_json("/search/v1/results", {
        "query": query, "hierarchy[title]": title, "date": current, "per_page": MAX_RESULTS,
    })
    results = body.get("results") or []
    lines = [f"{title} CFR, current through {current}: {len(results)} matching sections"]
    if not results:
        return lines[0].replace(f"{len(results)} matching sections", "no matching sections")
    for result in results:
        hierarchy = result.get("hierarchy") or {}
        headings = result.get("headings") or {}
        section = hierarchy.get("section") or ""
        heading = _plain(headings.get("section") or headings.get("part") or "")
        excerpt = _plain(result.get("full_text_excerpt") or "")
        lines += ["", f"{title} CFR {section}: {heading}".rstrip(": "), f"  {excerpt}" if excerpt else ""]
    return "\n".join(line for line in lines if line is not None).rstrip()


async def read_regulation(client: RegulationClient, section: str, paragraph: str | None = None, title: int = 8) -> str:
    """The section's text, from `paragraph` when given, clipped to MAX_TEXT_CHARS."""
    section = section.strip().removeprefix("§").strip()
    if not SECTION.match(section):
        raise ValueError(f"section must look like '214.2', got {section!r}")
    current = await client.current_date(title)
    xml = await client.get_text(f"/versioner/v1/full/{current}/title-{title}.xml", {
        "part": section.split(".")[0], "section": section,
    })
    blocks = xml_blocks(xml)
    if not blocks:
        raise ValueError(f"{title} CFR {section} has no text")
    heading, body = blocks[0], blocks[1:]
    start = ""
    if paragraph:
        body = from_paragraph(body, paragraph)
        start = f", from paragraph {paragraph}"
    text = "\n\n".join(body)
    clipped = ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        clipped = "\n\n… clipped; read a later paragraph to continue"
    return f"{heading}\n{title} CFR {section}{start}, current through {current}\n\n{text}{clipped}"


def xml_blocks(xml: str) -> list[str]:
    """The text of each heading and paragraph in a section's XML, in order."""
    root = ET.fromstring(xml)
    blocks = []
    for element in root.iter():
        if element.tag in TEXT_TAGS and not any(child.tag in TEXT_TAGS for child in element):
            text = " ".join("".join(element.itertext()).split())
            if text:
                blocks.append(text)
    return blocks


def from_paragraph(blocks: list[str], paragraph: str) -> list[str]:
    """The blocks from the paragraph with these labels, found one label at a time in order."""
    labels = LABEL.findall(paragraph)
    if not labels:
        raise ValueError(f"paragraph must look like '(f)(10)(ii)', got {paragraph!r}")
    index = 0
    for depth, label in enumerate(labels):
        found = next((i for i in range(index, len(blocks)) if _starts_with_label(blocks[i], label, depth == 0)), None)
        if found is None:
            path = "".join(f"({l})" for l in labels[: depth + 1])
            raise ValueError(f"paragraph {path} not found in this section")
        index = found
    return blocks[index:]


def _starts_with_label(block: str, label: str, first: bool) -> bool:
    """Whether a paragraph has `label` as one of its own labels.

    A top-level label opens its paragraph. A deeper one can also follow a heading inside the
    paragraph above it, after a dash or a full stop, as in "(f) Students—(1) Admission". A label
    inside a cross-reference ("paragraph (f)(10)(ii) of this section") doesn't count.
    """
    marker = re.escape(f"({label})")
    pattern = rf"^{marker}" if first else rf"(?:^|—\s*|\.\s+){marker}"
    return re.search(pattern, block) is not None


class RegulationError(Exception):
    """An eCFR request that failed."""


class RegulationClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        """`http` is for tests. eCFR requires compressed responses, which httpx asks for."""
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._current: dict[int, str] = {}

    async def current_date(self, title: int) -> str:
        """The date the title is up to date as of; asked once per title."""
        if title not in TITLES:
            raise ValueError(f"title must be one of {sorted(TITLES)}")
        if title not in self._current:
            body = await self.get_json("/versioner/v1/titles.json", {})
            for entry in body.get("titles", []):
                if entry.get("number") in TITLES and entry.get("up_to_date_as_of"):
                    self._current[entry["number"]] = entry["up_to_date_as_of"]
            if title not in self._current:
                raise RegulationError(f"eCFR didn't say how current title {title} is")
        return self._current[title]

    async def get_json(self, path: str, params: dict[str, Any]) -> Any:
        return (await self._get(path, params)).json()

    async def get_text(self, path: str, params: dict[str, Any]) -> str:
        return (await self._get(path, params)).content.decode("utf-8")

    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        try:
            response = await self.http.get(BASE_URL + path, params=params)
        except httpx.TimeoutException:
            raise RegulationError("the eCFR didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise RegulationError(f"couldn't reach the eCFR: {e}") from None
        if response.status_code == 404:
            raise RegulationError("no such section in the current regulations (HTTP 404)")
        if response.status_code >= 400:
            raise RegulationError(f"the eCFR returned HTTP {response.status_code}: {response.text[:200]}")
        return response


def _plain(text: str) -> str:
    """Text without HTML tags or extra whitespace."""
    return " ".join(re.sub(r"<[^>]+>", "", text).split())


def _tool(name, description, properties, required, fn) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        fn=awaited(fn),
        idempotent=True,
    )
