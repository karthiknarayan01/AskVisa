"""Deleting a session: its events go, nothing else does, and a running turn is stopped first."""

import httpx
import pytest

from runtime.api import create_app
from runtime.events import EventLog, UnknownSession
from tests.api_helpers import BlockingModel, make_agent, make_log
from tests.fake_model import say


@pytest.fixture
def log(db):
    with EventLog(db) as log:
        yield log


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_deleting_a_session_removes_it_and_only_it(log):
    keep, drop = log.new_session(), log.new_session()
    log.append(keep, "user_message", {"text": "keep"}, "r1")
    log.append(drop, "user_message", {"text": "drop"}, "r2")

    log.delete_session(drop)

    assert not log.has_session(drop)
    with pytest.raises(UnknownSession):
        log.load(drop)
    assert [e.payload["text"] for e in log.load(keep)] == ["keep"]
    assert [s.session_id for s in log.list_sessions()] == [keep]


def test_deleting_an_unknown_session_changes_nothing(log):
    sid = log.new_session()
    log.append(sid, "user_message", {"text": "a"}, "r1")
    with pytest.raises(UnknownSession):
        log.delete_session("nope")
    assert len(log.load(sid)) == 1


async def test_the_api_deletes_a_session(tmp_path):
    with make_log(tmp_path) as log:
        app = create_app(make_agent([say("one")]), log)
        async with client(app) as http:
            sid = (await http.post("/api/sessions")).json()["id"]
            await app.state.turns.start(sid, "q")

            assert (await http.delete(f"/api/sessions/{sid}")).status_code == 204
            assert (await http.get(f"/api/sessions/{sid}")).status_code == 404
            assert (await http.get("/api/sessions")).json() == []
            assert (await http.delete(f"/api/sessions/{sid}")).status_code == 404


async def test_deleting_a_session_stops_its_running_turn_first(tmp_path):
    model = BlockingModel(say("never"))
    with make_log(tmp_path) as log:
        app = create_app(make_agent([], model), log)
        async with client(app) as http:
            sid = (await http.post("/api/sessions")).json()["id"]
            done = app.state.turns.start(sid, "slow")
            await model.started.wait()

            assert (await http.delete(f"/api/sessions/{sid}")).status_code == 204
            assert done.done()
            assert not app.state.turns.is_running(sid)
            assert not log.has_session(sid)
