"""The eCFR tools against a mock HTTP transport: requests, the current date, text, paragraphs."""

import os

import httpx
import pytest

from runtime.messages import ToolCall
from runtime.regulation_tools import MAX_TEXT_CHARS, RegulationClient, from_paragraph, regulation_tools, xml_blocks
from runtime.tools import ToolRegistry

TITLES = {"titles": [
    {"number": 8, "name": "Aliens and Nationality", "up_to_date_as_of": "2026-11-05"},
    {"number": 20, "name": "Employees' Benefits", "up_to_date_as_of": "2026-11-04"},
    {"number": 22, "name": "Foreign Relations", "up_to_date_as_of": "2026-11-05"},
]}

SECTION_XML = """<?xml version="1.0"?>
<DIV8 N="214.2" TYPE="SECTION">
  <HEAD>§ 214.2 Special requirements for admission, extension, and maintenance of status.</HEAD>
  <P>(a) <I>Foreign government officials</I>—(1) <I>Classification.</I> An official ...</P>
  <P>(2) <I>Admission.</I> See paragraph (f)(10)(ii) of this section.</P>
  <P>(f) <I>Students in colleges</I>—(1) <I>Admission of student</I>—(i) <I>Eligibility.</I> A student ...</P>
  <P>(10) <I>Practical training.</I> Practical training may be authorized ...</P>
  <P>(i) <I>Curricular practical training.</I> ...</P>
  <P>(ii) <I>Optional practical training</I>—(A) <I>General.</I> ...</P>
  <P>(E) <I>Periods of unemployment.</I> During post-completion OPT, F-1 status is dependent upon employment. Students may not accrue an aggregate of more than 90 days of unemployment ...</P>
  <P>(11) <I>OPT application.</I> ...</P>
</DIV8>"""

SEARCH = {"results": [
    {"hierarchy": {"title": "8", "part": "214", "section": "214.2"},
     "headings": {"section": "Special requirements for admission, extension, and maintenance of status."},
     "full_text_excerpt": "more than 90 days of <strong>unemployment</strong> during"},
    {"hierarchy": {"title": "8", "part": "214", "section": "214.1"},
     "headings": {"section": "Requirements for admission, extension, and maintenance of status."},
     "full_text_excerpt": "a grace period of up to 60 days"},
]}


class Server:
    def __init__(self, routes=None):
        self.routes = {"/api/versioner/v1/titles.json": (200, TITLES), **(routes or {})}
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        status, body = next(((s, b) for path, (s, b) in self.routes.items() if request.url.path.startswith(path)), (404, "none"))
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)


def registry(server):
    return ToolRegistry(regulation_tools(RegulationClient(http=httpx.AsyncClient(transport=httpx.MockTransport(server)))))


async def call(tools, tool, **arguments):
    return await tools.run(ToolCall("c1", tool, arguments))


async def test_search_asks_for_the_title_as_of_its_current_date():
    server = Server({"/api/search/v1/results": (200, SEARCH)})
    result = await call(registry(server), "search_regulations", query="OPT unemployment")

    request = server.requests[-1]
    assert dict(request.url.params) == {
        "query": "OPT unemployment", "hierarchy[title]": "8", "date": "2026-11-05", "per_page": "8",
    }
    assert result.output.startswith("8 CFR, current through 2026-11-05: 2 matching sections")
    assert "8 CFR 214.2: Special requirements for admission" in result.output
    assert "more than 90 days of unemployment during" in result.output and "<strong>" not in result.output


async def test_search_with_no_results():
    result = await call(registry(Server({"/api/search/v1/results": (200, {"results": []})})), "search_regulations", query="x", title=22)
    assert result.output == "22 CFR, current through 2026-11-05: no matching sections"


async def test_read_returns_the_whole_section_as_text():
    server = Server({"/api/versioner/v1/full/2026-11-05/title-8.xml": (200, SECTION_XML)})
    result = await call(registry(server), "read_regulation", section="214.2")

    assert dict(server.requests[-1].url.params) == {"part": "214", "section": "214.2"}
    lines = result.output.split("\n")
    assert lines[0] == "§ 214.2 Special requirements for admission, extension, and maintenance of status."
    assert lines[1] == "8 CFR 214.2, current through 2026-11-05"
    assert "(a) Foreign government officials—(1) Classification." in result.output


async def test_read_from_a_paragraph_skips_cross_references_and_finds_nested_labels():
    server = Server({"/api/versioner/v1/full/2026-11-05/title-8.xml": (200, SECTION_XML)})
    result = await call(registry(server), "read_regulation", section="214.2", paragraph="(f)(10)(ii)(E)")

    assert "8 CFR 214.2, from paragraph (f)(10)(ii)(E), current through 2026-11-05" in result.output
    body = result.output.split("\n\n", 1)[1]
    assert body.startswith("(E) Periods of unemployment.") and "aggregate of more than 90 days" in body


async def test_one_cached_date_per_title():
    server = Server({"/api/versioner/v1/full/": (200, SECTION_XML)})
    tools = registry(server)
    await call(tools, "read_regulation", section="214.2")
    await call(tools, "read_regulation", section="656.17", title=20)
    assert sum(r.url.path.endswith("titles.json") for r in server.requests) == 1
    assert server.requests[-1].url.path == "/api/versioner/v1/full/2026-11-04/title-20.xml"


async def test_a_missing_paragraph_or_bad_section_is_an_error_the_model_can_fix():
    server = Server({"/api/versioner/v1/full/": (200, SECTION_XML)})
    missing = await call(registry(server), "read_regulation", section="214.2", paragraph="(f)(10)(ix)")
    assert missing.is_error and "paragraph (f)(10)(ix) not found" in missing.output
    bad = await call(registry(server), "read_regulation", section="part 214")
    assert bad.is_error and "section must look like '214.2'" in bad.output


async def test_a_section_symbol_is_accepted():
    server = Server({"/api/versioner/v1/full/": (200, SECTION_XML)})
    assert not (await call(registry(server), "read_regulation", section="§ 214.2")).is_error


async def test_long_text_is_clipped_with_a_way_to_continue():
    long_xml = "<DIV8><HEAD>§ 1.1 Long.</HEAD>" + "".join(f"<P>({i}) {'x' * 500}</P>" for i in range(1, 60)) + "</DIV8>"
    result = await call(registry(Server({"/api/versioner/v1/full/": (200, long_xml)})), "read_regulation", section="1.1")
    assert len(result.output) < MAX_TEXT_CHARS + 300 and result.output.endswith("read a later paragraph to continue")


@pytest.mark.parametrize("status, says", [(404, "no such section"), (503, "HTTP 503")])
async def test_http_errors(status, says):
    result = await call(registry(Server({"/api/versioner/v1/full/": (status, "down")})), "read_regulation", section="214.2")
    assert result.is_error and says in result.output


async def test_unreachable_is_an_error():
    def fail(request):
        raise httpx.ConnectError("no route", request=request)

    tools = ToolRegistry(regulation_tools(RegulationClient(http=httpx.AsyncClient(transport=httpx.MockTransport(fail)))))
    result = await call(tools, "search_regulations", query="x")
    assert result.is_error and "couldn't reach the eCFR" in result.output


def test_xml_blocks_keep_order_and_join_inline_markup():
    assert xml_blocks(SECTION_XML)[:2] == [
        "§ 214.2 Special requirements for admission, extension, and maintenance of status.",
        "(a) Foreign government officials—(1) Classification. An official ...",
    ]


def test_from_paragraph_needs_labels():
    with pytest.raises(ValueError, match="paragraph must look like"):
        from_paragraph(["(a) x"], "f-10")


def test_descriptions_follow_the_shared_layout():
    for tool in regulation_tools(RegulationClient(http=object())):
        assert "\nUse it for" in tool.description and "\nReturns:\n- " in tool.description
        assert tool.idempotent and not tool.requires_approval


@pytest.mark.skipif(not os.environ.get("AGENT_LIVE_WEB_TESTS"), reason="set AGENT_LIVE_WEB_TESTS=1")
async def test_live_ecfr():
    tools = ToolRegistry(regulation_tools())
    found = await call(tools, "search_regulations", query="optional practical training unemployment")
    text = await call(tools, "read_regulation", section="214.2", paragraph="(f)(10)(ii)(E)")
    assert not found.is_error and "214.2" in found.output
    assert not text.is_error and "90 days" in text.output
