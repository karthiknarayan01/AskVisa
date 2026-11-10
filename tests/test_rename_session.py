"""Session titles: the column, set_title (and keeping a user's name), and PATCH /api/sessions/{id}."""

import shutil
import sqlite3

import httpx
import pytest

from runtime.api import create_app
from runtime.events import EventLog, UnknownSession
from runtime.migrate import MIGRATIONS_DIR, migrate
from tests.api_helpers import drain, make_agent, make_log, tab
from tests.fake_model import say


@pytest.fixture
def log(db):
    with EventLog(db) as log:
        yield log


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_a_session_is_listed_under_its_first_question_until_it_has_a_title(log):
    sid = log.new_session()
    log.append(sid, "user_message", {"text": "When does my grace period end?"}, "r1")
    assert log.session_summary(sid).title == "When does my grace period end?"

    assert log.set_title(sid, "H-1B grace period")
    assert log.session_summary(sid).title == "H-1B grace period"
    assert log.list_sessions()[0].title == "H-1B grace period"


def test_only_if_unset_keeps_a_title_already_given(log):
    sid = log.new_session()
    assert log.set_title(sid, "Topic", only_if_unset=True)
    assert not log.set_title(sid, "Another topic", only_if_unset=True)
    assert log.set_title(sid, "My name for it")
    assert not log.set_title(sid, "A late topic", only_if_unset=True)
    assert log.session_summary(sid).title == "My name for it"


def test_titling_an_unknown_session(log):
    with pytest.raises(UnknownSession):
        log.set_title("nope", "x")
    with pytest.raises(UnknownSession):
        log.set_title("nope", "x", only_if_unset=True)


def test_the_migration_adds_an_empty_title_to_existing_sessions(tmp_path):
    old = tmp_path / "migrations"
    old.mkdir()
    for name in ("0001_create_sessions_and_events.sql", "0002_add_request_id_to_events.sql"):
        shutil.copy(MIGRATIONS_DIR / name, old)
    db = str(tmp_path / "old.db")
    migrate(db, old)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO sessions VALUES ('s1', 't')")
    conn.execute("""INSERT INTO events (session_id, type, payload, ts, request_id)
                    VALUES ('s1', 'user_message', '{"text": "old question"}', 't', 'r')""")
    conn.commit()
    conn.close()

    assert migrate(db) == ["0003_add_title_to_sessions"]
    with EventLog(db) as log:
        assert log.session_summary("s1").title == "old question"


async def test_the_api_renames_a_session_and_tells_every_tab(tmp_path):
    with make_log(tmp_path) as log:
        app = create_app(make_agent([say("one")]), log)
        async with client(app) as http:
            sid = (await http.post("/api/sessions")).json()["id"]
            await app.state.turns.start(sid, "When does my grace period end?")
            watcher = tab(app.state.turns)
            drain(watcher)

            renamed = await http.patch(f"/api/sessions/{sid}", json={"title": "  H-1B\n grace   period "})
            assert renamed.status_code == 200
            assert renamed.json()["title"] == "H-1B grace period" and renamed.json()["id"] == sid
            assert (await http.get("/api/sessions")).json()[0]["title"] == "H-1B grace period"
            assert (await http.get(f"/api/sessions/{sid}")).json()["title"] == "H-1B grace period"
            (sent,) = [m for m in drain(watcher) if m["type"] == "session"]
            assert sent["session"]["title"] == "H-1B grace period"


async def test_the_api_refuses_bad_titles(tmp_path):
    with make_log(tmp_path) as log:
        app = create_app(make_agent([]), log)
        async with client(app) as http:
            sid = (await http.post("/api/sessions")).json()["id"]
            assert (await http.patch(f"/api/sessions/{sid}", json={"title": "   "})).status_code == 422
            assert (await http.patch(f"/api/sessions/{sid}", json={"title": "x" * 121})).status_code == 422
            assert (await http.patch(f"/api/sessions/{sid}", json={})).status_code == 422
            assert (await http.patch("/api/sessions/nope", json={"title": "x"})).status_code == 404
