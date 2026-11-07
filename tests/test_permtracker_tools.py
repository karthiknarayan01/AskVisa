"""PERM Tracker tools against a mock HTTP transport: the request each tool sends, and the answer."""

import json
import os

import httpx
import pytest

from runtime.messages import ToolCall
from runtime.permtracker_tools import BASE_URL, MAX_OUTPUT_CHARS, PermTrackerClient, permtracker_tools
from runtime.tools import ToolRegistry

ANSWER = {
    "data": {"query": "Google", "match": "exact", "employer": {"name": "Google LLC", "slug": "google-llc"}},
    "meta": {"source": "PERM Tracker (permtracker.app)", "asOf": "2026-11-06",
             "url": "https://permtracker.app/perm-employers/google-llc"},
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


def registry(server, api_key=""):
    client = PermTrackerClient(api_key=api_key, http=httpx.Client(transport=httpx.MockTransport(server)))
    return ToolRegistry(permtracker_tools(client))


async def call(tools, tool, **arguments):
    return await tools.run(ToolCall("c1", tool, arguments))


@pytest.mark.parametrize(
    "tool, arguments, path, params",
    [
        ("match_employer", {"name": "Google"}, "/lookup/employer", {"name": "Google"}),
        ("find_soc_code", {"query": "data scientist"}, "/lookup/soc", {"q": "data scientist"}),
        ("find_soc_code", {"query": "15-2051"}, "/lookup/soc", {"q": "15-2051"}),
    ],
)
async def test_each_tool_calls_its_endpoint_without_a_key(tool, arguments, path, params):
    server = Server()
    result = await call(registry(server), tool, **arguments)

    assert not result.is_error, result.output
    request = server.requests[0]
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == BASE_URL + path
    assert dict(request.url.params) == params
    assert "Authorization" not in request.headers


KEYED = [
    "perm_case_status", "perm_decision_estimate", "perm_queue", "visa_bulletin",
    "search_employers", "employer_profile", "search_law_firms", "law_firm_profile",
    "search_occupations", "occupation_profile", "export_search",
]


def test_without_a_key_only_the_keyless_tools_are_offered():
    tools = permtracker_tools(PermTrackerClient(api_key=""))
    assert [t.name for t in tools] == ["match_employer", "find_soc_code"]


def test_with_a_key_every_read_endpoint_is_a_tool_and_none_needs_approval():
    tools = permtracker_tools(PermTrackerClient(api_key="pt_test_key"))
    assert [t.name for t in tools] == ["match_employer", "find_soc_code", *KEYED]
    assert all(t.idempotent and not t.requires_approval for t in tools)


def test_the_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("PERMTRACKER_API_KEY", " pt_live_abc ")
    assert PermTrackerClient().api_key == "pt_live_abc"
    monkeypatch.delenv("PERMTRACKER_API_KEY")
    assert PermTrackerClient().api_key == ""


@pytest.mark.parametrize(
    "tool, arguments, path, params",
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
        ("export_search", {"kind": "employers", "query": "acme"}, "/exports/employers",
         {"q": "acme", "format": "json"}),
        ("export_search", {"kind": "cases", "firm": "fragomen", "state": "CA", "from_date": "2026-01-01",
                           "to_date": "2026-03-31", "outcome": "certified"}, "/exports/cases",
         {"firm": "fragomen", "state": "CA", "from": "2026-01-01", "to": "2026-03-31",
          "outcome": "certified", "format": "json"}),
    ],
)
async def test_each_keyed_tool_calls_its_endpoint_with_the_key(tool, arguments, path, params):
    server = Server()
    result = await call(registry(server, api_key="pt_test_key"), tool, **arguments)

    assert not result.is_error, result.output
    request = server.requests[0]
    assert str(request.url).split("?")[0] == BASE_URL + path
    assert dict(request.url.params) == params
    assert request.headers["Authorization"] == "Bearer pt_test_key"


async def test_a_path_value_stays_one_segment():
    server = Server()
    await call(registry(server, api_key="k"), "employer_profile", slug="../me")
    assert server.requests[0].url.raw_path.decode() == "/v1/employers/..%2Fme"


async def test_a_refused_key_is_an_error_the_model_can_explain():
    server = Server(401, {"error": {"code": "revoked_key"}})
    result = await call(registry(server, api_key="k"), "perm_queue")
    assert result.is_error and "HTTP 401 revoked_key" in result.output and "isn't available" in result.output


async def test_answer_is_the_date_line_then_the_data():
    result = await call(registry(Server(body={**ANSWER, "data": {"codes": ["15-1252"]}})), "find_soc_code", query="x")
    first, rest = result.output.split("\n", 1)
    assert first == "Department of Labor data as of 2026-11-06"
    assert json.loads(rest) == {"codes": ["15-1252"]}


@pytest.mark.parametrize(
    "match, says",
    [
        ("exact", "Match: exact."),
        ("possible", "ask them to confirm it is theirs"),
        ("none", "not proof the employer never sponsors"),
    ],
)
async def test_match_employer_says_what_to_do_with_each_kind_of_match(match, says):
    body = {**ANSWER, "data": {**ANSWER["data"], "match": match}}
    lines = (await call(registry(Server(body=body)), "match_employer", name="Google")).output.split("\n")
    assert lines[0] == "Department of Labor data as of 2026-11-06"
    assert says in lines[1]
    assert json.loads(lines[2]) == body["data"]


async def test_an_unknown_match_value_adds_no_note():
    body = {**ANSWER, "data": {"match": "fuzzy"}}
    lines = (await call(registry(Server(body=body)), "match_employer", name="x")).output.split("\n")
    assert len(lines) == 2 and json.loads(lines[1]) == {"match": "fuzzy"}


def test_descriptions_say_when_to_use_each_tool_and_when_not_to():
    by_name = {t.name: t.description for t in permtracker_tools(PermTrackerClient(api_key="k"))}
    assert all("Use it" in d for d in by_name.values())
    assert "USCIS receipt numbers" in by_name["perm_case_status"]
    assert "not a DOL prediction" in by_name["perm_decision_estimate"]
    assert "final action dates and dates for filing" in by_name["visa_bulletin"]
    assert "Does Acme sponsor green cards?" in by_name["match_employer"]
    assert "Don't use it for the status of one case" in by_name["match_employer"]
    assert "What SOC code would my job" in by_name["find_soc_code"]
    assert all("date of the data" in by_name[n] for n in ("match_employer", "find_soc_code"))


async def test_results_and_descriptions_never_name_the_data_provider():
    tools = permtracker_tools(PermTrackerClient(api_key="k"))
    assert not any("PERM Tracker" in t.description or "permtracker" in t.description for t in tools)
    result = await call(registry(Server()), "match_employer", name="Google")
    assert "PERM Tracker" not in result.output and "permtracker.app" not in result.output


async def test_date_line_without_a_date_when_meta_is_missing():
    result = await call(registry(Server(body={"data": []})), "find_soc_code", query="x")
    assert result.output.splitlines()[0] == "Department of Labor data"


@pytest.mark.parametrize(
    "status, body, headers, expected",
    [
        (404, {"error": {"code": "not_found"}}, {}, "HTTP 404 not_found. No record of that"),
        (429, {"error": {"code": "rate_limited", "message": "Slow down"}}, {"Retry-After": "30"},
         "HTTP 429 rate_limited: Slow down. Rate limited; retry in 30 seconds"),
        (503, {"error": {"code": "unavailable"}}, {}, "HTTP 503 unavailable"),
        (500, "oops", {}, "HTTP 500"),
    ],
)
async def test_errors_come_back_with_what_to_do(status, body, headers, expected):
    result = await call(registry(Server(status, body, headers)), "match_employer", name="x")
    assert result.is_error
    assert expected in result.output


async def test_network_failures_are_errors_not_crashes():
    def fail(request):
        raise httpx.ConnectError("no route", request=request)

    client = PermTrackerClient(api_key="", http=httpx.Client(transport=httpx.MockTransport(fail)))
    result = await ToolRegistry(permtracker_tools(client)).run(ToolCall("c1", "find_soc_code", {"query": "x"}))
    assert result.is_error and "couldn't reach the lookup service" in result.output


async def test_long_answers_are_clipped():
    big = {"data": {"titles": ["x" * 100] * 1_000}, "meta": {}}
    result = await call(registry(Server(body=big)), "find_soc_code", query="engineer")
    assert len(result.output) < MAX_OUTPUT_CHARS + 200
    assert "clipped" in result.output


async def test_bad_arguments_are_rejected_before_any_request():
    server = Server()
    result = await call(registry(server), "match_employer", employer="Google")
    assert result.is_error and server.requests == []


@pytest.mark.skipif(not os.environ.get("AGENT_LIVE_WEB_TESTS"), reason="set AGENT_LIVE_WEB_TESTS=1")
async def test_live_lookups():
    tools = ToolRegistry(permtracker_tools(PermTrackerClient(api_key="")))
    soc = await tools.run(ToolCall("c1", "find_soc_code", {"query": "software developer"}))
    employer = await tools.run(ToolCall("c2", "match_employer", {"name": "Google"}))
    assert not soc.is_error and "15-1252" in soc.output
    assert not employer.is_error and employer.output.startswith("Department of Labor data")


@pytest.mark.skipif(not os.environ.get("PERMTRACKER_API_KEY"), reason="needs PERMTRACKER_API_KEY")
async def test_live_keyed_endpoints():
    tools = ToolRegistry(permtracker_tools())
    for name, arguments in [("perm_queue", {}), ("visa_bulletin", {}), ("search_employers", {"query": "google"})]:
        result = await tools.run(ToolCall("c1", name, arguments))
        assert not result.is_error, (name, result.output)
