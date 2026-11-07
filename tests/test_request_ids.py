"""request_id: every event of one user request (turn) carries the same id; seq stays the order."""

import shutil
import sqlite3
import uuid

import httpx
import pytest

from runtime.api import create_app
from runtime.events import EventLog
from runtime.messages import Message
from runtime.migrate import MIGRATIONS_DIR, migrate
from runtime.api_events import conversation
from runtime.compaction import summary_message_text
from runtime.session import Session
from runtime.state import SessionState
from runtime.tools import ToolRegistry
from tests.api_helpers import ECHO, make_agent, parse_sse
from tests.fake_model import FakeModel, call, say


class Crash(BaseException):
    pass


@pytest.fixture
def log(db):
    with EventLog(db) as log:
        yield log


def is_uuid(value):
    return str(uuid.UUID(value)) == value


def by_request(events):
    groups = {}
    for event in events:
        groups.setdefault(event.request_id, []).append(event.type)
    return groups


def test_append_and_load_keep_the_request_id(log):
    sid = log.new_session()
    log.append(sid, "user_message", {"text": "a"}, "r1")
    log.append(sid, "assistant_message", {"text": "x", "reasoning": "", "tool_calls": [], "usage": {}}, "r1")
    log.append(sid, "user_message", {"text": "b"}, "r2")
    assert [(e.type, e.request_id) for e in log.load(sid)] == [
        ("user_message", "r1"), ("assistant_message", "r1"), ("user_message", "r2")]
    assert [e.seq for e in log.load_request(sid, "r1")] == [1, 2]
    assert log.load_request(sid, "nope") == []


async def test_each_turn_gets_a_new_id_shared_by_all_its_events(log):
    model = FakeModel([call("echo", text="hi"), say("done"), say("second answer")])
    session = Session.create(log, model, ToolRegistry([ECHO]))
    await session.run_turn("first")
    await session.run_turn("second")

    groups = by_request(log.load(session.session_id))
    assert len(groups) == 2 and all(is_uuid(rid) for rid in groups)
    first, second = groups.values()
    assert first == ["user_message", "assistant_message", "tool_started", "tool_finished", "assistant_message"]
    assert second == ["user_message", "assistant_message"]
    assert session.state.request_id == list(groups)[1]


async def test_a_resumed_turn_keeps_its_id(log):
    class CrashingModel(FakeModel):
        async def complete(self, *args, **kwargs):
            if len(self.requests) == 1:
                raise Crash()
            return await super().complete(*args, **kwargs)

    session = Session.create(log, CrashingModel([call("echo", text="hi"), say("after crash")]), ToolRegistry([ECHO]))
    with pytest.raises(Crash):
        await session.run_turn("question")

    reopened = Session.open(log, session.session_id, FakeModel([say("after crash")]), ToolRegistry([ECHO]))
    assert reopened.needs_resume
    await reopened.resume()
    assert len(by_request(log.load(session.session_id))) == 1


def test_the_migration_backfills_existing_events_by_turn(tmp_path):
    old_migrations = tmp_path / "migrations"
    old_migrations.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_create_sessions_and_events.sql", old_migrations)
    db = str(tmp_path / "old.db")
    migrate(db, old_migrations)
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO sessions VALUES (?, 't')", [("s1",), ("s2",)])
    rows = [("s1", "user_message"), ("s1", "assistant_message"), ("s2", "user_message"),
            ("s1", "turn_stopped"), ("s1", "user_message"), ("s1", "assistant_message")]
    conn.executemany("INSERT INTO events (session_id, type, payload, ts) VALUES (?, ?, '{}', 't')", rows)
    conn.commit()
    conn.close()

    migrate(db)
    conn = sqlite3.connect(db)
    got = conn.execute("SELECT seq, session_id, request_id FROM events ORDER BY seq").fetchall()
    conn.close()
    ids = {seq: rid for seq, _, rid in got}
    assert all(is_uuid(rid) for rid in ids.values())
    assert ids[1] == ids[2] == ids[4]          # s1's first turn, including the stop
    assert ids[5] == ids[6] != ids[1]          # s1's second turn
    assert ids[3] not in (ids[1], ids[5])      # s2's turn


def test_conversation_items_get_the_ids_of_their_turns_even_after_a_compaction():
    state = SessionState()
    state.messages = [
        Message("user", summary_message_text("Earlier turns.", "third")),
        Message("user", "third"), Message("assistant", "3"),
        Message("user", "fourth"), Message("assistant", "4"),
    ]
    items = conversation(state, ["r1", "r2", "r3", "r4"])
    assert [(i["type"], i["request_id"]) for i in items] == [
        ("compaction", None), ("user_message", "r3"), ("assistant_message", "r3"),
        ("user_message", "r4"), ("assistant_message", "r4")]


async def test_the_api_streams_and_returns_the_request_id(db):
    with EventLog(db) as log:
        app = create_app(make_agent([say("one"), say("two")]), log)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            sid = (await http.post("/api/sessions")).json()["id"]
            first = parse_sse((await http.post(f"/api/sessions/{sid}/messages", json={"text": "a"})).text, keep_ids=True)
            second = parse_sse((await http.post(f"/api/sessions/{sid}/messages", json={"text": "b"})).text, keep_ids=True)
            stored = (await http.get(f"/api/sessions/{sid}")).json()

        rid1, rid2 = first[0][1]["request_id"], second[0][1]["request_id"]
        assert is_uuid(rid1) and rid1 != rid2
        assert first[-1][1]["request_id"] == rid1 and second[-1][1]["request_id"] == rid2
        assert [i["request_id"] for i in stored["items"]] == [rid1, rid1, rid2, rid2]
        assert [e.type for e in log.load_request(sid, rid2)] == ["user_message", "assistant_message"]
