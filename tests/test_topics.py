"""A new conversation's topic: worked out in the background from its first question, it becomes
the title unless the session has one by then."""

import asyncio

import pytest

from runtime.agent import Agent
from runtime.anthropic_model import AnthropicModel
from runtime.model import ModelError
from runtime.ollama_model import OllamaModel
from runtime.providers import TOPIC_MAX_TOKENS, make_topic_model
from runtime.tools import ToolRegistry
from runtime.topics import MAX_TOPIC_CHARS, TOPIC_PROMPT, clean_topic, topic_for
from runtime.turns import Turns
from tests.api_helpers import ECHO, BlockingModel, drain, make_log, tab
from tests.fake_model import FakeModel, say


def agent_with(topic_model, answers=("The answer.", "Another answer.")):
    return Agent("anthropic", "test-model", FakeModel([say(a) for a in answers]), ToolRegistry([ECHO]),
                 topic_model=topic_model)


def titles(messages):
    return [m["session"]["title"] for m in messages if m["type"] == "session"]


@pytest.mark.parametrize("reply, title", [
    ("H-1B layoff grace period", "H-1B layoff grace period"),
    ('"OPT unemployment days."', "OPT unemployment days"),
    ("Title: EB-2 India priority date\n\nThis is about...", "EB-2 India priority date"),
    ("  **Infosys   green card sponsorship**  ", "Infosys green card sponsorship"),
    ("\n\n", None),
    ("'...'", None),
])
def test_cleaning_the_reply(reply, title):
    assert clean_topic(reply) == title


def test_a_long_reply_is_cut_at_a_word():
    cleaned = clean_topic("Naturalization filing date after five years as a permanent resident in the US")
    assert len(cleaned) <= MAX_TOPIC_CHARS and not cleaned.endswith(" ")
    assert "Naturalization filing date after five years as a" in cleaned


async def test_the_model_is_asked_with_the_question_and_no_tools():
    model = FakeModel([say("H-1B layoff grace period")])
    assert await topic_for(model, "I was laid off. When does my grace period end?") == "H-1B layoff grace period"
    (request,) = model.requests
    assert request.system == TOPIC_PROMPT and request.tools == []
    assert [m.text for m in request.messages] == ["I was laid off. When does my grace period end?"]


async def test_the_first_question_titles_the_session_and_every_tab_hears(tmp_path):
    topics = FakeModel([say("H-1B layoff grace period")])
    with make_log(tmp_path) as log:
        turns = Turns(log, agent_with(topics))
        sid = turns.create().session_id
        client = tab(turns)
        await turns.start(sid, "I was laid off on October 20. When does my grace period end?")
        await turns.topics_settled()
        assert log.session_summary(sid).title == "H-1B layoff grace period"
        assert titles(drain(client))[-1] == "H-1B layoff grace period"

        await turns.start(sid, "And can I change employers?")
        await turns.topics_settled()
        assert len(topics.requests) == 1


async def test_the_topic_never_holds_up_the_answer(tmp_path):
    topics = BlockingModel(say("Slow topic"))
    with make_log(tmp_path) as log:
        turns = Turns(log, agent_with(topics))
        sid = turns.create().session_id
        await asyncio.wait_for(turns.start(sid, "q"), 2)
        assert log.session_summary(sid).title == "q"
        topics.release.set()
        await turns.topics_settled()
        assert log.session_summary(sid).title == "Slow topic"


async def test_a_name_given_before_the_topic_arrives_is_kept(tmp_path):
    topics = BlockingModel(say("Worked-out topic"))
    with make_log(tmp_path) as log:
        turns = Turns(log, agent_with(topics))
        sid = turns.create().session_id
        await turns.start(sid, "q")
        await topics.started.wait()
        turns.rename(sid, "My name")
        client = tab(turns)
        topics.release.set()
        await turns.topics_settled()
        assert log.session_summary(sid).title == "My name"
        assert titles(drain(client)) == []


async def test_a_failed_topic_leaves_the_first_question_as_the_title(tmp_path):
    class Fails:
        async def complete(self, *args, **kwargs):
            raise ModelError("overloaded")

    with make_log(tmp_path) as log:
        turns = Turns(log, agent_with(Fails()))
        sid = turns.create().session_id
        await turns.start(sid, "When does my grace period end?")
        await turns.topics_settled()
        assert log.session_summary(sid).title == "When does my grace period end?"
        assert turns.session(sid).state.messages[-1].text == "The answer."


async def test_a_session_deleted_before_its_topic_arrives(tmp_path):
    topics = BlockingModel(say("Too late"))
    with make_log(tmp_path) as log:
        turns = Turns(log, agent_with(topics))
        sid = turns.create().session_id
        await turns.start(sid, "q")
        await turns.delete(sid)
        topics.release.set()
        await turns.topics_settled()
        assert not log.has_session(sid)


def test_the_topic_model_is_short_and_a_local_one_does_not_think(monkeypatch):
    local = make_topic_model("ollama")
    assert isinstance(local, OllamaModel) and local._think is False and local._max_tokens == TOPIC_MAX_TOKENS
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    hosted = make_topic_model("anthropic")
    assert isinstance(hosted, AnthropicModel) and hosted._max_tokens == TOPIC_MAX_TOKENS


async def test_ollama_sends_think_only_when_set():
    from tests.test_ollama_model import FakeClient, chunk

    for think, sent in [(False, {"think": False}), (None, {})]:
        client = FakeClient([chunk(content="x", done=True, done_reason="stop")])
        await OllamaModel(think=think, client=client).complete("sys", [], [])
        assert {k: v for k, v in client.requests[0].items() if k == "think"} == sent
