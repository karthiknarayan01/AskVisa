"""The judge: what it is shown, and how its reply is read."""

from evals.checks import ToolCallRecord
from evals.judge import JUDGE_PROMPT, MAX_RESULT_CHARS, judge_answer, parse_verdicts, render_transcript
from runtime.model import ModelReply
from tests.fake_model import FakeModel

CRITERIA = ("Says yes", "Gives a number")


def test_transcript_shows_question_calls_answer_and_checklist():
    calls = [
        ToolCallRecord("match_employer", {"name": "Acme"}, "x" * (MAX_RESULT_CHARS + 50), False),
        ToolCallRecord("perm_queue", {}, "HTTP 429", True),
    ]
    text = render_transcript("Does Acme sponsor?", calls, "Yes, 38 cases.", CRITERIA)

    assert text.startswith("QUESTION:\nDoes Acme sponsor?")
    assert '1. match_employer({"name": "Acme"})' in text and "… [clipped]" in text
    assert "2. perm_queue({})\n   ERROR: HTTP 429" in text
    assert "FINAL ANSWER:\nYes, 38 cases." in text
    assert text.endswith("CHECKLIST:\n1. Says yes\n2. Gives a number")


def test_transcript_says_when_there_were_no_calls():
    assert "TOOL CALLS: none" in render_transcript("q", [], "a", CRITERIA)


def test_verdicts_are_read_from_json_even_inside_other_text():
    reply = 'Here you go:\n{"items": [{"pass": true, "reason": "ok"}, {"pass": false, "reason": "no number"}]}'
    verdicts = parse_verdicts(reply, CRITERIA)
    assert [(v.criterion, v.passed, v.reason) for v in verdicts] == [
        ("Says yes", True, "ok"), ("Gives a number", False, "no number"),
    ]


def test_an_unreadable_reply_fails_every_item_with_the_reason():
    for reply in ("I think it passes.", '{"items": [{"pass": true}]}', '{"verdicts": []}'):
        verdicts = parse_verdicts(reply, CRITERIA)
        assert all(not v.passed and "judge reply unreadable" in v.reason for v in verdicts)


async def test_judge_sends_the_transcript_without_tools():
    judge = FakeModel([ModelReply(text='{"items": [{"pass": true, "reason": "a"}, {"pass": true, "reason": "b"}]}')])
    verdicts = await judge_answer(judge, "q", [], "answer", CRITERIA)

    assert all(v.passed for v in verdicts)
    request = judge.requests[0]
    assert request.system == JUDGE_PROMPT and request.tools == []
    assert "FINAL ANSWER:\nanswer" in request.messages[0].text
