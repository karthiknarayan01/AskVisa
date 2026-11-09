"""The live channel over a real WebSocket: hello, commands, refusals, many sessions on one socket,
and a tab that reconnects in the middle of an answer. The app runs in uvicorn on a free port, on
the test's own event loop."""

import asyncio
import contextlib
import json

import httpx
import pytest
import uvicorn
import websockets

from runtime.api import create_app
from runtime.model import ModelError
from tests.api_helpers import BlockingModel, make_agent, make_log
from tests.fake_model import FakeModel, call, say


@pytest.fixture
def log(tmp_path):
    with make_log(tmp_path) as log:
        yield log


@contextlib.asynccontextmanager
async def serve(app):
    """The app on 127.0.0.1 at a free port: yields (http client, ws url)."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http:
        try:
            yield http, f"ws://127.0.0.1:{port}/api/ws"
        finally:
            server.should_exit = True
            await task


async def receive(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 5))


async def send(ws, message):
    await ws.send(message if isinstance(message, str) else json.dumps(message))


async def until_end(ws, session_id):
    """Messages from the socket until `end` for the session; returns the turn's (event, data)."""
    got = []
    while True:
        m = await receive(ws)
        if m["type"] == "event" and m["session_id"] == session_id:
            got.append((m["event"], m["data"]))
            if m["event"] == "end":
                return got


async def next_of(ws, kind):
    while True:
        m = await receive(ws)
        if m["type"] == kind:
            return m


async def test_a_conversation_over_the_socket(log):
    async with serve(create_app(make_agent([call("echo", text="hi"), say("It echoed hi.")]), log)) as (http, url):
        sid = (await http.post("/api/sessions")).json()["id"]
        async with websockets.connect(url) as ws:
            assert (await receive(ws)) == {"type": "hello", "running": []}
            await send(ws, {"type": "send", "session_id": sid, "text": "  Echo hi  "})
            got = await until_end(ws, sid)

        assert got[0][0] == "user_message" and got[0][1]["text"] == "Echo hi"
        assert got[-1][1]["outcome"] == "completed" and got[-1][1]["text"] == "It echoed hi."
        stored = (await http.get(f"/api/sessions/{sid}")).json()
        assert [i["type"] for i in stored["items"]] == ["user_message", "assistant_message", "tool_finished", "assistant_message"]
        assert (await http.get("/api/sessions")).json()[0]["title"] == "Echo hi"


async def test_two_sessions_answer_at_once_on_one_socket(log):
    async with serve(create_app(make_agent([say("one"), say("two")]), log)) as (http, url):
        a = (await http.post("/api/sessions")).json()["id"]
        b = (await http.post("/api/sessions")).json()["id"]
        async with websockets.connect(url) as ws:
            await receive(ws)
            await send(ws, {"type": "send", "session_id": a, "text": "first"})
            await send(ws, {"type": "send", "session_id": b, "text": "second"})
            ends = {}
            while len(ends) < 2:
                m = await receive(ws)
                if m["type"] == "event" and m["event"] == "end":
                    ends[m["session_id"]] = m["data"]["outcome"]
        assert ends == {a: "completed", b: "completed"}


async def test_refused_commands_come_back_as_errors(log):
    model = BlockingModel(say("never"))
    async with serve(create_app(make_agent([], model), log)) as (http, url):
        sid = (await http.post("/api/sessions")).json()["id"]
        async with websockets.connect(url) as ws:
            await receive(ws)
            for command, reason in [
                ({"type": "send", "session_id": "nope", "text": "x"}, "no session nope"),
                ({"type": "send", "session_id": sid, "text": "   "}, "the message is empty"),
                ({"type": "send", "session_id": sid, "text": "x" * 20_001}, "longer than 20,000"),
                ({"type": "cancel", "session_id": sid}, "no turn is running"),
                ({"type": "resume", "session_id": sid}, "no interrupted turn"),
                ({"type": "fly", "session_id": sid}, "unknown command"),
                ({"type": "send"}, "needs a session_id"),
            ]:
                await send(ws, command)
                error = await next_of(ws, "error")
                assert reason in error["message"] and error["session_id"] == command.get("session_id")
            await send(ws, "not json")
            assert "JSON" in (await next_of(ws, "error"))["message"]

            await send(ws, {"type": "send", "session_id": sid, "text": "first"})
            await next_of(ws, "session")
            await send(ws, {"type": "send", "session_id": sid, "text": "second"})
            assert "already running" in (await next_of(ws, "error"))["message"]
            await send(ws, {"type": "cancel", "session_id": sid})
            end = (await until_end(ws, sid))[-1]
            assert end[1]["outcome"] == "cancelled"


async def test_an_interrupted_turn_must_be_resumed(log):
    class FailsOnce(FakeModel):
        async def complete(self, *args, **kwargs):
            if not self.requests:
                self.requests.append(None)
                raise ModelError("overloaded")
            return await super().complete(*args, **kwargs)

    async with serve(create_app(make_agent([], FailsOnce([say("Recovered.")])), log)) as (http, url):
        sid = (await http.post("/api/sessions")).json()["id"]
        async with websockets.connect(url) as ws:
            await receive(ws)
            await send(ws, {"type": "send", "session_id": sid, "text": "q"})
            assert (await until_end(ws, sid))[-1][1]["outcome"] == "error"
            assert (await http.get(f"/api/sessions/{sid}")).json()["interrupted"] is True
            await send(ws, {"type": "send", "session_id": sid, "text": "next"})
            assert "resume" in (await next_of(ws, "error"))["message"]
            await send(ws, {"type": "resume", "session_id": sid})
            assert (await until_end(ws, sid))[-1][1]["text"] == "Recovered."


async def test_a_tab_that_reconnects_mid_answer_picks_it_up(log):
    model = BlockingModel(say("The full answer."))
    async with serve(create_app(make_agent([], model), log)) as (http, url):
        sid = (await http.post("/api/sessions")).json()["id"]
        async with websockets.connect(url) as first:
            await receive(first)
            await send(first, {"type": "send", "session_id": sid, "text": "slow question"})
            await next_of(first, "session")
        assert (await http.get("/api/sessions")).json()[0]["running"] is True

        async with websockets.connect(url) as second:
            (running,) = (await receive(second))["running"]
            assert running["session_id"] == sid
            assert running["events"][0]["event"] == "user_message"
            assert running["events"][0]["data"]["text"] == "slow question"
            model.release.set()
            assert (await until_end(second, sid))[-1][1]["text"] == "The full answer."


async def test_deleting_a_session_tells_every_tab(log):
    async with serve(create_app(make_agent([]), log)) as (http, url):
        sid = (await http.post("/api/sessions")).json()["id"]
        async with websockets.connect(url) as ws:
            await receive(ws)
            assert (await http.delete(f"/api/sessions/{sid}")).status_code == 204
            assert (await next_of(ws, "session_deleted")) == {"type": "session_deleted", "session_id": sid}
