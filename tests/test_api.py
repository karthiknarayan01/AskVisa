"""The REST API over HTTP: sessions, streamed turns, errors and CORS."""

import asyncio

import httpx
import pytest

from runtime.api import create_app
from runtime.model import ModelError
from tests.api_helpers import BlockingModel, make_agent, make_log, parse_sse
from tests.fake_model import FakeModel, call, say


@pytest.fixture
def log(tmp_path):
    with make_log(tmp_path) as log:
        yield log


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_and_config(log):
    async with client(create_app(make_agent([]), log)) as http:
        assert (await http.get("/api/health")).json() == {"status": "ok"}
        assert (await http.get("/api/config")).json() == {"provider": "anthropic", "model": "test-model"}


async def test_a_conversation_end_to_end(log):
    app = create_app(make_agent([call("echo", text="hi"), say("It echoed hi.")]), log)
    async with client(app) as http:
        created = await http.post("/api/sessions")
        assert created.status_code == 201
        sid = created.json()["id"]

        response = await http.post(f"/api/sessions/{sid}/messages", json={"text": "  Echo hi  "})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(response.text)
        assert events[0] == ("user_message", {"text": "Echo hi"})
        assert events[-1] == ("end", {"outcome": "completed", "text": "It echoed hi."})

        stored = (await http.get(f"/api/sessions/{sid}")).json()
        assert stored["title"] == "Echo hi" and not stored["running"] and not stored["interrupted"]
        assert [i["type"] for i in stored["items"]] == ["user_message", "assistant_message", "tool_finished", "assistant_message"]

        listed = (await http.get("/api/sessions")).json()
        assert listed[0]["id"] == sid and listed[0]["title"] == "Echo hi"


async def test_sessions_are_listed_most_recent_first(log):
    app = create_app(make_agent([say("a"), say("b")]), log)
    async with client(app) as http:
        older = (await http.post("/api/sessions")).json()["id"]
        newer = (await http.post("/api/sessions")).json()["id"]
        await http.post(f"/api/sessions/{newer}/messages", json={"text": "second"})
        await http.post(f"/api/sessions/{older}/messages", json={"text": "first"})
        assert [s["id"] for s in (await http.get("/api/sessions")).json()] == [older, newer]


async def test_unknown_sessions_and_empty_messages(log):
    async with client(create_app(make_agent([]), log)) as http:
        assert (await http.get("/api/sessions/nope")).status_code == 404
        assert (await http.post("/api/sessions/nope/messages", json={"text": "x"})).status_code == 404
        sid = (await http.post("/api/sessions")).json()["id"]
        assert (await http.post(f"/api/sessions/{sid}/messages", json={"text": ""})).status_code == 422
        assert (await http.post(f"/api/sessions/{sid}/messages", json={"text": "   "})).status_code == 422
        assert (await http.post(f"/api/sessions/{sid}/cancel")).status_code == 409


async def test_a_running_turn_blocks_another_and_can_be_cancelled(log):
    model = BlockingModel(say("never"))
    app = create_app(make_agent([], model), log)
    async with client(app) as http:
        sid = (await http.post("/api/sessions")).json()["id"]
        first = asyncio.create_task(http.post(f"/api/sessions/{sid}/messages", json={"text": "first"}))
        await model.started.wait()
        assert (await http.get(f"/api/sessions/{sid}")).json()["running"] is True
        busy = await http.post(f"/api/sessions/{sid}/messages", json={"text": "second"})
        assert busy.status_code == 409 and "already running" in busy.json()["detail"]
        assert (await http.post(f"/api/sessions/{sid}/cancel")).status_code == 202
        events = parse_sse((await first).text)
        assert events[-1] == ("end", {"outcome": "cancelled"})


async def test_an_interrupted_turn_must_be_resumed(log):
    class FailsOnce(FakeModel):
        async def complete(self, *args, **kwargs):
            if not self.requests:
                self.requests.append(None)
                raise ModelError("overloaded")
            return await super().complete(*args, **kwargs)

    app = create_app(make_agent([], FailsOnce([say("Recovered.")])), log)
    async with client(app) as http:
        sid = (await http.post("/api/sessions")).json()["id"]
        failed = parse_sse((await http.post(f"/api/sessions/{sid}/messages", json={"text": "q"})).text)
        assert failed[-1][1] == {"outcome": "error", "message": "overloaded", "retryable": True}
        assert (await http.get(f"/api/sessions/{sid}")).json()["interrupted"] is True
        blocked = await http.post(f"/api/sessions/{sid}/messages", json={"text": "next"})
        assert blocked.status_code == 409 and "resume" in blocked.json()["detail"]
        resumed = parse_sse((await http.post(f"/api/sessions/{sid}/resume")).text)
        assert resumed[-1] == ("end", {"outcome": "completed", "text": "Recovered."})
        assert (await http.post(f"/api/sessions/{sid}/resume")).status_code == 409


async def test_cors_only_when_origins_are_allowed(log):
    allowed = create_app(make_agent([]), log, allow_origins=["http://localhost:5173"])
    async with client(allowed) as http:
        response = await http.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    async with client(create_app(make_agent([]), log)) as http:
        response = await http.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in response.headers
