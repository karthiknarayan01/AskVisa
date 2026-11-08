"""Stopping a turn: the work it started is cancelled, and the stop is recorded."""

import asyncio

import httpx

from runtime.agent import Agent, agent_tools
from runtime.calculator_tools import calculator_tools
from runtime.messages import ToolCall
from runtime.ollama_model import OllamaModel
from runtime.permtracker_tools import PermTrackerClient, permtracker_tools
from runtime.tools import Tool, ToolRegistry
from runtime.turns import Turns
from tests.api_helpers import collect, make_log, parse_sse
from tests.fake_model import FakeModel, call, say


class Waiting:
    """An async tool that waits until cancelled, noting that it started and that it was cancelled."""

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self) -> str:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "never"


async def test_stopping_cancels_the_running_tool_and_records_that_the_user_stopped_it(tmp_path):
    wait = Waiting()
    tool = Tool("wait", "Wait.", {"type": "object", "properties": {}}, wait.run, idempotent=True)
    agent = Agent("anthropic", "test-model", FakeModel([call("wait"), say("never")]), ToolRegistry([tool]))
    with make_log(tmp_path) as log:
        turns = Turns(log, agent)
        sid = turns.create().session_id
        stream = asyncio.create_task(collect(turns.stream(sid, "question")))
        await wait.started.wait()

        assert turns.cancel(sid)
        events = parse_sse(await stream)

        assert wait.cancelled
        assert not turns.is_running(sid)
        assert [kind for kind, _ in events][-2:] == ["turn_stopped", "end"]
        assert events[-1] == ("end", {"outcome": "cancelled"})
        stored = [(e.type, e.payload) for e in log.load(sid)]
        assert stored[-1] == ("turn_stopped", {"reason": "cancelled"})
        finished = next(payload for kind, payload in stored if kind == "tool_finished")
        assert finished["is_error"] and finished["output"].startswith("Stopped: the user stopped the request")


async def test_a_stopped_lookup_abandons_its_http_request():
    started, abandoned = asyncio.Event(), asyncio.Event()

    async def never_answers(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            abandoned.set()
            raise

    client = PermTrackerClient(api_key="", http=httpx.AsyncClient(transport=httpx.MockTransport(never_answers)))
    tools = ToolRegistry(permtracker_tools(client, read_page=lambda url: ""))
    running = asyncio.create_task(tools.execute(ToolCall("c1", "match_employer", {"name": "Infosys"})))
    await started.wait()
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert abandoned.is_set()


def test_tools_that_call_services_run_on_the_event_loop_so_they_can_be_stopped(monkeypatch):
    monkeypatch.setenv("PERMTRACKER_API_KEY", "k")
    in_threads = {t.name for t in calculator_tools()}  # pure date arithmetic, no I/O
    for tool in agent_tools(web=False):
        if tool.name not in in_threads:
            assert asyncio.iscoroutinefunction(tool.fn), tool.name


async def test_a_stopped_reply_closes_the_stream_from_ollama():
    closed, streaming = asyncio.Event(), asyncio.Event()

    class Client:
        async def chat(self, **request):
            async def stream():
                try:
                    streaming.set()
                    await asyncio.Event().wait()
                    yield None
                finally:
                    closed.set()

            return stream()

    running = asyncio.create_task(OllamaModel(client=Client()).complete("sys", [], []))
    await streaming.wait()
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert closed.is_set()
