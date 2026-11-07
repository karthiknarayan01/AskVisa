"""Turns: streaming a turn, one at a time per session, cancel, disconnect, interrupted turns, pings."""

import asyncio

import pytest

from runtime import turns as turns_module
from runtime.model import ModelError, ModelReply
from runtime.turns import TurnBusy, TurnInterrupted, Turns
from tests.api_helpers import BlockingModel, collect, make_agent, make_log, parse_sse
from tests.fake_model import FakeModel, call, say


async def test_a_turn_streams_every_step_and_ends_completed(tmp_path):
    agent = make_agent([call("echo", text="hi"), ModelReply(text="Done: it echoed.", reasoning="ok then")])
    with make_log(tmp_path) as log:
        turns = Turns(log, agent)
        session = turns.create()
        events = parse_sse(await collect(turns.stream(session.session_id, "Echo hi")))

    kinds = [k for k, _ in events]
    assert kinds[0] == "user_message" and kinds[-1] == "end"
    assert kinds.index("tool_started") < kinds.index("tool_finished") < kinds.index("text")
    finished = next(d for k, d in events if k == "tool_finished")
    assert finished["summary"] == "echo: hi" and finished["output"] == "echo: hi\nsecond line"
    assert "".join(d["delta"] for k, d in events if k == "text") == "Done: it echoed."
    assert "".join(d["delta"] for k, d in events if k == "reasoning") == "ok then"
    assert events[-1] == ("end", {"outcome": "completed", "text": "Done: it echoed."})


async def test_one_turn_at_a_time_and_cancel_stops_it(tmp_path):
    model = BlockingModel(say("never"))
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        sid = turns.create().session_id
        stream = turns.stream(sid, "first")
        reading = asyncio.create_task(collect(stream))
        await model.started.wait()
        assert turns.is_running(sid)
        with pytest.raises(TurnBusy):
            turns.stream(sid, "second")
        assert turns.cancel(sid)
        events = parse_sse(await reading)

    assert ("turn_stopped", {"reason": "cancelled"}) in events
    assert events[-1] == ("end", {"outcome": "cancelled"})
    assert not turns.is_running(sid) and not turns.cancel(sid)


async def test_a_disconnected_stream_lets_the_turn_finish(tmp_path):
    model = BlockingModel(say("Finished anyway."))
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        session = turns.create()
        stream = turns.stream(session.session_id, "q")
        first = await anext(stream)
        assert first.startswith("event: user_message")
        await stream.aclose()
        model.release.set()
        while turns.is_running(session.session_id):
            await asyncio.sleep(0.01)
        assert session.state.messages[-1].text == "Finished anyway."


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
        failed = parse_sse(await collect(turns.stream(sid, "q")))
        assert failed[-1] == ("end", {"outcome": "error", "message": "overloaded", "retryable": True})
        with pytest.raises(TurnInterrupted):
            turns.stream(sid, "another question")
        resumed = parse_sse(await collect(turns.stream_resume(sid)))
        assert resumed[-1] == ("end", {"outcome": "completed", "text": "Here it is."})
        with pytest.raises(ValueError, match="no interrupted turn"):
            turns.stream_resume(sid)


async def test_a_step_limit_ends_stopped(tmp_path):
    agent = make_agent([call("echo", call_id=f"c{i}", text="x") for i in range(25)])
    with make_log(tmp_path) as log:
        turns = Turns(log, agent)
        sid = turns.create().session_id
        events = parse_sse(await collect(turns.stream(sid, "loop")))
    assert events[-1][1]["outcome"] == "stopped" and "steps" in events[-1][1]["message"]


async def test_pings_keep_a_quiet_stream_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(turns_module, "PING_SECONDS", 0.05)
    model = BlockingModel(say("slow answer"))
    with make_log(tmp_path) as log:
        turns = Turns(log, make_agent([], model))
        sid = turns.create().session_id
        reading = asyncio.create_task(collect(turns.stream(sid, "q")))
        await asyncio.sleep(0.2)
        model.release.set()
        body = await reading
    assert ": ping\n\n" in body and parse_sse(body)[-1][1]["outcome"] == "completed"


async def test_sessions_are_opened_from_the_log_once(tmp_path):
    with make_log(tmp_path) as log:
        first = Turns(log, make_agent([say("one")]))
        sid = first.create().session_id
        await collect(first.stream(sid, "q"))
        second = Turns(log, make_agent([]))
        session = second.session(sid)
        assert session is second.session(sid)
        assert [m.text for m in session.state.messages] == ["q", "one"]
