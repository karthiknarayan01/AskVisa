"""Federal Register tools against a mock HTTP transport: queries, rendering, the agency fallback."""

import os

import httpx
import pytest

from runtime.federal_register_tools import AGENCIES, MAX_TEXT_CHARS, FederalRegisterClient, federal_register_tools
from runtime.messages import ToolCall
from runtime.tools import ToolRegistry

RULE = {
    "document_number": "2026-21456",
    "title": "Weighted Selection in the H-1B Registration Process",
    "type": "Rule",
    "abstract": "DHS is amending its regulations so that H-1B registrations are selected with weights by <em>wage level</em>.",
    "publication_date": "2026-09-30",
    "effective_on": "2027-01-15",
    "agencies": [{"name": "U.S. Citizenship and Immigration Services"}, {"raw_name": "HOMELAND SECURITY DEPARTMENT"}],
    "html_url": "https://www.federalregister.gov/documents/2026/09/30/2026-21456/weighted-selection",
    "citation": "91 FR 61234",
}
PROPOSED = {
    "document_number": "2026-22001", "title": "Fee Schedule Adjustments", "type": "Proposed Rule",
    "publication_date": "2026-10-20", "comments_close_on": "2026-12-19", "agencies": [{"name": "USCIS"}],
    "html_url": "https://www.federalregister.gov/d/2026-22001",
}


class Server:
    def __init__(self, handler=None):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        if self.handler:
            return self.handler(request)
        if request.url.path == "/api/v1/documents.json":
            return httpx.Response(200, json={"count": 2, "results": [PROPOSED, RULE]})
        if request.url.path == "/api/v1/documents/2026-21456.json":
            return httpx.Response(200, json={**RULE, "dates": "<p>Effective January 15, 2027.</p>",
                                             "raw_text_url": "https://www.federalregister.gov/documents/full_text/text/2026/09/30/2026-21456.txt"})
        if request.url.path.endswith("2026-21456.txt"):
            return httpx.Response(200, text="SUMMARY:  DHS is amending\n its regulations.\n\nDATES: Effective January 15, 2027.")
        return httpx.Response(404, text="not found")


def registry(server):
    return ToolRegistry(federal_register_tools(FederalRegisterClient(http=httpx.AsyncClient(transport=httpx.MockTransport(server)))))


async def call(tools, tool, **arguments):
    return await tools.run(ToolCall("c1", tool, arguments))


def params(request):
    return request.url.params.multi_items()


async def test_search_filters_to_the_immigration_agencies_newest_first():
    server = Server()
    result = await call(registry(server), "search_federal_register", query="H-1B registration")

    items = params(server.requests[0])
    assert ("conditions[term]", "H-1B registration") in items and ("order", "newest") in items
    assert sorted(v for k, v in items if k == "conditions[agencies][]") == sorted(AGENCIES.values())
    assert ("fields[]", "effective_on") in items and ("fields[]", "comments_close_on") in items
    assert result.output.startswith("2 Federal Register documents match, newest first:")


async def test_search_renders_type_agencies_dates_abstract_and_page():
    result = await call(registry(Server()), "search_federal_register", query="H-1B")
    rule = result.output.split("\n\n")[2].split("\n")
    assert rule[0] == ("2026-09-30 · Final rule · U.S. Citizenship and Immigration Services, HOMELAND SECURITY "
                       "DEPARTMENT · document 2026-21456")
    assert rule[1] == "Weighted Selection in the H-1B Registration Process"
    assert rule[2] == "Effective 2027-01-15; 91 FR 61234"
    assert "by wage level." in rule[3] and "<em>" not in rule[3]
    assert rule[4] == RULE["html_url"]
    assert "Comments close 2026-12-19" in result.output


async def test_type_agency_and_since_narrow_the_search():
    server = Server()
    await call(registry(server), "search_federal_register", query="TPS", document_type="notice", agency="uscis", since="2026-01-01")
    items = params(server.requests[0])
    assert ("conditions[type][]", "NOTICE") in items
    assert ("conditions[publication_date][gte]", "2026-01-01") in items
    assert [v for k, v in items if k == "conditions[agencies][]"] == ["u-s-citizenship-and-immigration-services"]


async def test_a_rejected_agency_filter_is_retried_without_it():
    def handler(request):
        if any(k == "conditions[agencies][]" for k, _ in params(request)):
            return httpx.Response(400, json={"errors": {"agencies": "invalid value"}})
        return httpx.Response(200, json={"count": 1, "results": [RULE]})

    server = Server(handler)
    result = await call(registry(server), "search_federal_register", query="H-1B")
    assert len(server.requests) == 2
    assert "(the agency filter was rejected, so these are from all agencies)" in result.output
    assert "Weighted Selection" in result.output


async def test_no_matches():
    server = Server(lambda r: httpx.Response(200, json={"count": 0, "results": []}))
    assert (await call(registry(server), "search_federal_register", query="zzz")).output == "No Federal Register documents match."


async def test_read_gives_details_dates_and_text():
    result = await call(registry(Server()), "read_federal_register_document", document_number="2026-21456")
    assert result.output.startswith("2026-09-30 · Final rule · U.S. Citizenship and Immigration Services")
    assert "Effective 2027-01-15; 91 FR 61234" in result.output
    assert "Dates: Effective January 15, 2027." in result.output
    assert result.output.endswith("SUMMARY: DHS is amending its regulations. DATES: Effective January 15, 2027.")


async def test_long_text_is_clipped_with_a_link():
    def handler(request):
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json={**RULE, "raw_text_url": "https://www.federalregister.gov/x.txt"})
        return httpx.Response(200, text="word " * 5_000)

    result = await call(registry(Server(handler)), "read_federal_register_document", document_number="2026-21456")
    assert len(result.output) < MAX_TEXT_CHARS + 800
    assert result.output.endswith(f"the full document is at {RULE['html_url']}")


async def test_text_is_only_fetched_from_the_federal_register():
    def handler(request):
        return httpx.Response(200, json={**RULE, "raw_text_url": "https://evil.example/x.txt"})

    result = await call(registry(Server(handler)), "read_federal_register_document", document_number="2026-21456")
    assert result.is_error and "refusing to fetch text from outside federalregister.gov" in result.output


async def test_bad_numbers_missing_documents_and_outages_are_errors():
    tools = registry(Server())
    bad = await call(tools, "read_federal_register_document", document_number="../../etc")
    assert bad.is_error and "document_number must look like" in bad.output
    missing = await call(tools, "read_federal_register_document", document_number="2026-00000")
    assert missing.is_error and "no such Federal Register document" in missing.output

    def fail(request):
        raise httpx.ConnectError("no route", request=request)

    down = await call(registry(Server(fail)), "search_federal_register", query="x")
    assert down.is_error and "couldn't reach the Federal Register" in down.output


def test_descriptions_follow_the_shared_layout():
    for tool in federal_register_tools(FederalRegisterClient(http=object())):
        assert "\nUse it for" in tool.description and "\nReturns:\n- " in tool.description
        assert tool.idempotent and not tool.requires_approval


@pytest.mark.skipif(not os.environ.get("AGENT_LIVE_WEB_TESTS"), reason="set AGENT_LIVE_WEB_TESTS=1")
async def test_live_federal_register():
    tools = ToolRegistry(federal_register_tools())
    found = await call(tools, "search_federal_register", query="H-1B")
    assert not found.is_error and "agency filter was rejected" not in found.output, found.output
    number = found.output.split("document ", 1)[1].split()[0]
    doc = await call(tools, "read_federal_register_document", document_number=number)
    assert not doc.is_error and len(doc.output) > 500
