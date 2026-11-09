"""Turns: a turn published live, one at a time per session, cancel, interrupted turns, and what a
tab that connects mid-turn is sent."""

import asyncio

import pytest

from runtime.model import ModelError, ModelReply
from runtime.turns import TurnBusy, TurnInterrupted, Turns
from tests.api_helpers import BlockingModel, drain, events, make_agent, make_log, tab
from tests.fake_model import FakeModel, call, say


async def test_a_turn_publishes_every_step_and_ends_completed(tmp_path):
    agent = make_agent([call("echo", text="hi"), ModelReply(text="Done: it echoed.", reasoning="ok then")])
    with make_log(tmp_path) as log:
        turns = Turns(log, agent)
        sid = turns.create().session_id
        client = tab(turns)
        await turns.start(sid, "Echo hi")
        got = events(drain(client), sid)

    kinds = [k for k, _ in got]
    assert kinds[0] == "user_message" and kinds[-1] == "end"
    assert kinds.index("tool_started") < kinds.index("tool_finished") < kinds.index("text")
    finished = next(d for k, d in got if k == "tool_finished")
    assert finished["summary"] == "echo: hi" and finished["output"] == "echo: hi\nsecond line"
    assert "".join(d["delta"] for k, d in got if k == "text") == "Done: it echoed."
    assert "".join(d["delta"] for k, d in got if k == "reasoning") == "ok then"
    assert got[-1] == ("end", {"outcome": "completed", "text": "Done: it echoed."})


async def test_every_event_carries_its_session_and_request(tmp_path):
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([say("a")]))
        sid = turns.create().session_id
        client = tab(turns)
        await turns.start(sid, "q")
        messages = [m for m in drain(client) if m["type"] == "event"]
    rid = messages[0]["data"]["request_id"]
    assert all(m["session_id"] == sid and m["request_id"] == rid for m in messages)


async def test_the_sessions_list_hears_when_a_turn_starts_and_ends(tmp_path):
    model = BlockingModel(say("answer"))
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        sid = turns.create().session_id
        client = tab(turns)
        done = turns.start(sid, "When does my grace period end?")
        await model.started.wait()
        started = [m["session"] for m in drain(client) if m["type"] == "session"]
        model.release.set()
        await done
        ended = [m["session"] for m in drain(client) if m["type"] == "session"]
    assert started[0]["id"] == sid and started[0]["title"] == "When does my grace period end?"
    assert started[0]["running"] is True and ended[-1]["running"] is False


async def test_every_tab_gets_every_session(tmp_path):
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([say("one"), say("two")]))
        a, b = turns.create().session_id, turns.create().session_id
        first, second = tab(turns), tab(turns)
        await asyncio.gather(turns.start(a, "qa"), turns.start(b, "qb"))
        for client in (first, second):
            messages = drain(client)
            assert events(messages, a)[-1][0] == "end" and events(messages, b)[-1][0] == "end"


async def test_one_turn_at_a_time_and_cancel_stops_it(tmp_path):
    model = BlockingModel(say("never"))
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        sid = turns.create().session_id
        client = tab(turns)
        done = turns.start(sid, "first")
        await model.started.wait()
        assert turns.is_running(sid)
        with pytest.raises(TurnBusy):
            turns.start(sid, "second")
        assert turns.cancel(sid)
        await done
        got = events(drain(client), sid)

    assert ("turn_stopped", {"reason": "cancelled"}) in got
    assert got[-1] == ("end", {"outcome": "cancelled"})
    assert not turns.is_running(sid) and not turns.cancel(sid)


async def test_a_tab_that_connects_mid_turn_gets_it_so_far_then_the_rest(tmp_path):
    model = BlockingModel(say("The answer."))
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        sid = turns.create().session_id
        done = turns.start(sid, "q")
        await model.started.wait()

        late = tab(turns)
        hello = drain(late)[0]
        model.release.set()
        await done
        rest = events(drain(late), sid)

    (running,) = hello["running"]
    assert running["session_id"] == sid and running["base"] == []
    assert [e["event"] for e in running["events"]] == ["user_message"]
    assert running["request_id"] == running["events"][0]["data"]["request_id"]
    assert [k for k, _ in rest] == ["assistant_message", "end"]
    assert not turns.snapshot()


async def test_a_model_error_keeps_the_turn_and_resume_finishes_it(tmp_path):
    class Flaky(FakeModel):
        async def complete(self, *args, **kwargs):
            if not self.requests:
                self.requests.append(None)
                raise ModelError("overloaded")
            return await super().complete(*args, **kwargs)

    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], Flaky([say("Here it is.")])))
        sid = turns.create().session_id
        client = tab(turns)
        await turns.start(sid, "q")
        assert events(drain(client), sid)[-1] == ("end", {"outcome": "error", "message": "overloaded", "retryable": True})
        with pytest.raises(TurnInterrupted):
            turns.start(sid, "another question")
        await turns.resume(sid)
        assert events(drain(client), sid)[-1] == ("end", {"outcome": "completed", "text": "Here it is."})
        with pytest.raises(ValueError, match="no interrupted turn"):
            turns.resume(sid)


async def test_a_resumed_turn_is_sent_with_its_earlier_steps(tmp_path):
    class Crashes(FakeModel):
        async def complete(self, *args, **kwargs):
            if len(self.requests) == 1:
                self.requests.append(None)
                raise ModelError("overloaded")
            return await super().complete(*args, **kwargs)

    model = Crashes([call("echo", text="hi"), say("after")])
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        sid = turns.create().session_id
        await turns.start(sid, "q")
        rid = turns.session(sid).state.request_id
        done = turns.resume(sid)
        (running,) = turns.snapshot()
        await done
    assert running["request_id"] == rid
    assert [i["type"] for i in running["base"]] == ["user_message", "assistant_message", "tool_finished"]
    assert all(i["request_id"] == rid for i in running["base"])


async def test_a_step_limit_ends_stopped(tmp_path):
    agent = make_agent([call("echo", call_id=f"c{i}", text="x") for i in range(25)])
    with make_log(tmp_path) as log:
        turns = Turns(log, agent)
        sid = turns.create().session_id
        client = tab(turns)
        await turns.start(sid, "loop")
        end = events(drain(client), sid)[-1][1]
    assert end["outcome"] == "stopped" and "steps" in end["message"]


async def test_a_turn_finishes_with_no_tab_connected(tmp_path):
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([say("Finished anyway.")]))
        session = turns.create()
        await turns.start(session.session_id, "q")
        assert session.state.messages[-1].text == "Finished anyway."


async def test_sessions_are_opened_from_the_log_once(tmp_path):
    with make_log(tmp_path) as log:
        first = Turns(log, make_agent([say("one")]))
        sid = first.create().session_id
        await first.start(sid, "q")
        second = Turns(log, make_agent([]))
        session = second.session(sid)
        assert session is second.session(sid)
        assert [m.text for m in session.state.messages] == ["q", "one"]
