"""PERM Tracker tools against a mock HTTP transport: the request each tool sends, and the answer."""

import json
import os

import httpx
import pytest

from runtime.messages import ToolCall
from runtime.permtracker_tools import (
    BASE_URL,
    MAX_OUTPUT_CHARS,
    PermTrackerClient,
    permtracker_tools,
)
from runtime.tools import ToolRegistry

ANSWER = {
    "data": {"caseNumber": "G-100-26045-123456", "status": "Certified"},
    "meta": {"source": "PERM Tracker (permtracker.app)", "asOf": "2026-11-06",
             "url": "https://permtracker.app/perm-cases/G-100-26045-123456"},
}


class Server:
    """Records each request and answers with a fixed status and body."""

    def __init__(self, status=200, body=ANSWER, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.body, str):
            return httpx.Response(self.status, text=self.body, headers=self.headers)
        return httpx.Response(self.status, json=self.body, headers=self.headers)


def registry(server, api_key="pt_test_key"):
    client = PermTrackerClient(api_key=api_key, http=httpx.Client(transport=httpx.MockTransport(server)))
    return ToolRegistry(permtracker_tools(client))


async def call(tools, tool, **arguments):
    return await tools.run(ToolCall("c1", tool, arguments))


@pytest.mark.parametrize(
    "name, arguments, path, params",
    [
        ("perm_case_status", {"case_number": "G-100-26045-123456"}, "/cases/G-100-26045-123456", {}),
        ("perm_case_status", {"case_number": "G-100-26045-123456", "live": True},
         "/cases/G-100-26045-123456", {"live": "1"}),
        ("perm_decision_estimate", {"filed_date": "2026-02-15"}, "/estimate", {"filed": "2026-02-15"}),
        ("perm_queue", {}, "/queue", {}),
        ("visa_bulletin", {}, "/visa-bulletin", {}),
        ("visa_bulletin", {"month": "2026-10"}, "/visa-bulletin", {"month": "2026-10"}),
        ("search_employers", {"query": "acme"}, "/employers", {"q": "acme"}),
        ("employer_profile", {"slug": "google-llc"}, "/employers/google-llc", {}),
        ("search_law_firms", {"query": "fragomen"}, "/law-firms", {"q": "fragomen"}),
        ("law_firm_profile", {"slug": "fragomen-del-rey"}, "/law-firms/fragomen-del-rey", {}),
        ("search_occupations", {"query": "software"}, "/occupations", {"q": "software"}),
        ("occupation_profile", {"slug": "software-developers"}, "/occupations/software-developers", {}),
        ("match_employer", {"name": "Google"}, "/lookup/employer", {"name": "Google"}),
        ("find_soc_code", {"query": "data scientist"}, "/lookup/soc", {"q": "data scientist"}),
        ("export_search", {"kind": "employers", "query": "acme"}, "/exports/employers",
         {"q": "acme", "format": "json"}),
        ("export_search", {"kind": "cases", "firm": "fragomen", "state": "CA", "from_date": "2026-01-01",
                           "to_date": "2026-03-31", "outcome": "certified"}, "/exports/cases",
         {"firm": "fragomen", "state": "CA", "from": "2026-01-01", "to": "2026-03-31",
          "outcome": "certified", "format": "json"}),
        ("permtracker_usage", {}, "/me", {}),
    ],
)
async def test_each_tool_calls_its_endpoint(name, arguments, path, params):
    server = Server()
    result = await call(registry(server), name, **arguments)

    assert not result.is_error, result.output
    request = server.requests[0]
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == BASE_URL + path
    assert dict(request.url.params) == params
    assert request.headers["Authorization"] == "Bearer pt_test_key"


def test_there_is_one_tool_per_read_endpoint_and_none_needs_approval():
    tools = permtracker_tools(PermTrackerClient(api_key=""))
    assert len(tools) == 14
    assert all(t.idempotent and not t.requires_approval for t in tools)


async def test_answer_starts_with_the_credit_line_then_the_data():
    result = await call(registry(Server()), "perm_case_status", case_number="G-100-26045-123456")
    first, rest = result.output.split("\n", 1)
    assert first == ("PERM Tracker (permtracker.app) · as of 2026-11-06 · "
                     "https://permtracker.app/perm-cases/G-100-26045-123456")
    assert json.loads(rest) == ANSWER["data"]


async def test_a_path_value_stays_one_segment():
    server = Server()
    await call(registry(server), "employer_profile", slug="../me")
    assert server.requests[0].url.raw_path.decode() == "/v1/employers/..%2Fme"


async def test_without_a_key_the_lookups_still_work():
    server = Server()
    tools = registry(server, api_key="")
    result = await call(tools, "find_soc_code", query="15-2051")
    assert not result.is_error
    assert "Authorization" not in server.requests[0].headers


async def test_without_a_key_other_tools_say_how_to_get_one_and_send_nothing():
    server = Server()
    result = await call(registry(server, api_key=""), "perm_queue")
    assert result.is_error
    assert "permtracker.app/developers" in result.output and "PERMTRACKER_API_KEY" in result.output
    assert server.requests == []


def test_the_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("PERMTRACKER_API_KEY", " pt_live_abc ")
    assert PermTrackerClient().api_key == "pt_live_abc"


@pytest.mark.parametrize(
    "status, body, headers, expected",
    [
        (401, {"error": {"code": "invalid_key", "message": "Unknown key"}}, {},
         "HTTP 401 invalid_key: Unknown key. The API key was refused"),
        (404, {"error": {"code": "not_found"}}, {}, "HTTP 404 not_found. PERM Tracker has no record of that"),
        (429, {"error": {"code": "daily_limit"}}, {"Retry-After": "120"},
         "HTTP 429 daily_limit. Rate limited; retry in 120 seconds"),
        (503, {"error": {"code": "dol_unavailable"}}, {}, "HTTP 503 dol_unavailable"),
        (500, "oops", {}, "HTTP 500"),
    ],
)
async def test_errors_come_back_with_what_to_do(status, body, headers, expected):
    result = await call(registry(Server(status, body, headers)), "perm_case_status", case_number="G-1")
    assert result.is_error
    assert expected in result.output


async def test_network_failures_are_errors_not_crashes():
    def fail(request):
        raise httpx.ConnectError("no route", request=request)

    client = PermTrackerClient(api_key="k", http=httpx.Client(transport=httpx.MockTransport(fail)))
    result = await ToolRegistry(permtracker_tools(client)).run(ToolCall("c1", "perm_queue", {}))
    assert result.is_error and "couldn't reach PERM Tracker" in result.output


async def test_long_answers_are_clipped():
    big = {"data": {"rows": ["x" * 100] * 1_000}, "meta": {}}
    result = await call(registry(Server(body=big)), "export_search", kind="cases")
    assert len(result.output) < MAX_OUTPUT_CHARS + 200
    assert "clipped" in result.output


async def test_bad_arguments_are_rejected_before_any_request():
    server = Server()
    result = await call(registry(server), "export_search", kind="everything")
    assert result.is_error and server.requests == []


@pytest.mark.skipif(not os.environ.get("AGENT_LIVE_WEB_TESTS"), reason="set AGENT_LIVE_WEB_TESTS=1")
async def test_live_lookups_without_a_key():
    tools = ToolRegistry(permtracker_tools(PermTrackerClient(api_key="")))
    soc = await tools.run(ToolCall("c1", "find_soc_code", {"query": "software developer"}))
    employer = await tools.run(ToolCall("c2", "match_employer", {"name": "Google"}))
    assert not soc.is_error and "15-1252" in soc.output
    assert not employer.is_error and employer.output.startswith("PERM Tracker")


@pytest.mark.skipif(not os.environ.get("PERMTRACKER_API_KEY"), reason="needs PERMTRACKER_API_KEY")
async def test_live_keyed_endpoints():
    tools = ToolRegistry(permtracker_tools())
    for name, arguments in [("permtracker_usage", {}), ("perm_queue", {}), ("visa_bulletin", {})]:
        result = await tools.run(ToolCall("c1", name, arguments))
        assert not result.is_error, (name, result.output)
