"""The command-line chat end to end, driven with scripted input and a fake model.

Output goes to a StringIO, which rich treats as a plain file: no colors, and nothing transient
(spinners), so these tests read the same text a user would see, minus the styling.
"""

import io

import pytest

from runtime import cli
from runtime.cli import main
from runtime.events import EventLog
from runtime.messages import ToolCall
from runtime.migrate import migrate
from runtime.model import ModelError, ModelReply
from runtime.tools import Tool
from tests.fake_model import FakeModel, call, say


def test_welcome_panel_then_a_turn_and_its_answer(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([say("hello there")]))
    out = _run(tmp_path, [], ["hi"])
    assert "agent-runtime" in out and "claude-sonnet-5-5 (anthropic)" in out
    assert "(new)" in out and "/help for commands" in out
    assert "hello there" in out


def test_each_turn_ends_with_a_cost_line(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([say("ok")]))
    out = _run(tmp_path, [], ["hi"])
    cost = [line for line in out.splitlines() if "claude-sonnet-5-5 ·" in line]
    assert cost and "context" in cost[-1] and cost[-1].rstrip().endswith("s")


def test_fresh_database_is_migrated_on_start(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([]))
    _run(tmp_path, [], [])
    with EventLog(str(tmp_path / "agent.db")) as log:
        assert log.latest_session() is not None


def test_resume_latest_continues_the_conversation(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([say("one")]))
    _run(tmp_path, [], ["first"])

    model = FakeModel([say("two")])
    _use_model(monkeypatch, model)
    out = _run(tmp_path, ["--resume", "latest"], ["second"])
    assert "(resumed, 2 messages)" in out and "two" in out
    assert [m.text for m in model.requests[0].messages] == ["first", "one", "second"]


def test_unknown_session_is_reported(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([]))
    with pytest.raises(SystemExit, match="no session to resume"):
        _run(tmp_path, ["--resume", "nope"], [])


def test_interrupted_turn_is_finished_on_start(tmp_path, monkeypatch):
    db = str(tmp_path / "agent.db")
    migrate(db)
    with EventLog(db) as log:
        sid = log.new_session()
        log.append(sid, "user_message", {"text": "hi"})  # crashed before the model replied

    _use_model(monkeypatch, FakeModel([say("sorry for the wait")]))
    out = _run(tmp_path, ["--resume", sid], [])
    assert "Finishing the turn that was interrupted" in out
    assert "sorry for the wait" in out


def test_a_tool_that_needs_approval_asks_and_runs_on_yes(tmp_path, monkeypatch):
    saved = _use_approval_tool(monkeypatch)
    _use_model(monkeypatch, FakeModel([call("save_note", text="hi"), say("saved")]))
    out = _run(tmp_path, [], ["save a note", "y"])

    assert "Allow save_note?" in out
    assert "● save_note  text=hi" in out
    assert "⎿  saved hi" in out
    assert saved == ["hi"]


def test_denied_tool_never_runs(tmp_path, monkeypatch):
    saved = _use_approval_tool(monkeypatch)
    _use_model(monkeypatch, FakeModel([call("save_note", text="hi")]))
    out = _run(tmp_path, [], ["save a note", "n"])

    assert "Stopped: you denied save_note." in out
    assert saved == []


def test_work_is_shown_in_the_order_it_happens(tmp_path, monkeypatch):
    _use_approval_tool(monkeypatch)
    narrate_and_save = ModelReply(
        text="I'll save it.",
        tool_calls=(ToolCall("c1", "save_note", {"text": "hi"}),),
    )
    _use_model(monkeypatch, FakeModel([narrate_and_save, say("All saved.")]))
    out = _run(tmp_path, [], ["save a note", "y"])

    order = [out.index(s) for s in ("I'll save it.", "Allow save_note?", "● save_note", "All saved.")]
    assert order == sorted(order)


def test_streamed_answer_is_shown_once(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([say("one two three four five six seven")]))
    out = _run(tmp_path, [], ["count"])
    assert out.count("one two three four five six seven") == 1


def test_reasoning_is_not_printed(tmp_path, monkeypatch):
    reply = ModelReply(text="42", reasoning="let me think about this carefully for a moment")
    _use_model(monkeypatch, FakeModel([reply]))
    out = _run(tmp_path, [], ["what is the answer?"])
    assert "42" in out and "carefully" not in out


def test_markdown_in_answers_is_rendered(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([say("Use **bold** and `code`.\n\n- one\n- two")]))
    out = _run(tmp_path, [], ["format"])
    assert "**" not in out and "bold" in out
    assert "• one" in out and "• two" in out


def test_web_tools_and_a_dated_prompt_are_given_to_the_model(tmp_path, monkeypatch):
    model = FakeModel([say("ok")])
    _use_model(monkeypatch, model)
    _run(tmp_path, [], ["hi"])
    request = model.requests[0]
    assert {"web_search", "read_web_page"} <= {t.name for t in request.tools}
    assert "Today's date is" in request.system and "web_search" in request.system


def test_without_a_service_key_only_the_keyless_perm_tools_are_given(tmp_path, monkeypatch):
    monkeypatch.delenv("PERMTRACKER_API_KEY", raising=False)
    model = FakeModel([say("ok")])
    _use_model(monkeypatch, model)
    _run(tmp_path, ["--no-web"], ["hi"])
    request = model.requests[0]
    assert {t.name for t in request.tools} == {"match_employer", "find_soc_code", "immigration_data_page"}
    assert "match_employer" in request.system and "perm_case_status" not in request.system


def test_with_a_service_key_every_perm_tool_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMTRACKER_API_KEY", "pt_test_key")
    model = FakeModel([say("ok")])
    _use_model(monkeypatch, model)
    _run(tmp_path, ["--no-web"], ["hi"])
    request = model.requests[0]
    assert {"perm_case_status", "visa_bulletin", "export_search"} <= {t.name for t in request.tools}
    assert "perm_case_status" in request.system


def test_no_web_flag_removes_the_web_tools(tmp_path, monkeypatch):
    model = FakeModel([say("ok")])
    _use_model(monkeypatch, model)
    out = _run(tmp_path, ["--no-web"], ["hi"])
    assert not {"web_search", "read_web_page"} & {t.name for t in model.requests[0].tools}
    assert "web_search" not in model.requests[0].system
    assert "web search" in out and "off" in out


def test_yes_flag_approves_without_asking(tmp_path, monkeypatch):
    saved = _use_approval_tool(monkeypatch)
    _use_model(monkeypatch, FakeModel([call("save_note", text="hi"), say("done")]))
    out = _run(tmp_path, ["--yes"], ["save a note"])

    assert "Allow save_note?" not in out and "automatic (--yes)" in out
    assert saved == ["hi"]


def test_ctrl_c_during_a_turn_cancels_it_and_the_chat_goes_on(tmp_path, monkeypatch):
    saved = _use_approval_tool(monkeypatch)
    _use_model(monkeypatch, FakeModel([call("save_note", text="hi"), say("next answer")]))
    out = _run(tmp_path, [], ["save a note", KeyboardInterrupt, "something else"])

    assert "Turn cancelled." in out
    assert "next answer" in out
    assert saved == []


def test_turn_errors_are_printed_not_raised(tmp_path, monkeypatch):
    _use_approval_tool(monkeypatch)
    model = FakeModel([call("save_note", call_id=f"c{i}", text="x") for i in range(25)])
    _use_model(monkeypatch, model)
    out = _run(tmp_path, ["--yes"], ["loop forever"])
    assert "Turn stopped: no final answer after 20 steps" in out


def test_missing_api_key_is_a_clean_exit(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY is not set"):
        _run(tmp_path, [], [])


def test_model_api_error_keeps_the_turn_and_resume_retries_it(tmp_path, monkeypatch):
    model = FlakyModel(fail_times=1, replies=[say("worked this time")])
    _use_model(monkeypatch, model)
    out = _run(tmp_path, [], ["hi", "something else", "/resume"])

    assert "Anthropic API error: overloaded" in out
    assert "type /resume to retry it" in out
    assert "The last turn didn't finish" in out  # "something else" was refused
    assert "worked this time" in out


def test_help_lists_the_commands(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([]))
    out = _run(tmp_path, [], ["/help"])
    for command in ("/new", "/status", "/resume", "/clear", "/exit", "Ctrl+C", "Alt+Enter"):
        assert command in out


def test_status_shows_the_session_and_context(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([say("ok")]))
    out = _run(tmp_path, [], ["hi", "/status"])
    assert "messages" in out and "2" in out
    assert "% of 128,000 tokens" in out
    assert "web_search" in out


def test_local_model_is_checked_before_the_chat_starts(tmp_path, monkeypatch):
    model = LocalModel([], check_error=ModelError("the model qwen3.5:9b isn't downloaded; run: ollama pull qwen3.5:9b"))
    _use_model(monkeypatch, model)
    with pytest.raises(SystemExit, match="run: ollama pull qwen3.5:9b"):
        _run(tmp_path, ["--provider", "ollama"], ["hi"])
    assert model.requests == []


def test_local_model_runs_and_compaction_uses_its_window(tmp_path, monkeypatch):
    _use_model(monkeypatch, LocalModel([say("hello from qwen")]))
    out = _run(tmp_path, ["--provider", "ollama"], ["hi", "/status"])
    assert "qwen3.5:9b (ollama)" in out
    assert "hello from qwen" in out
    assert "% of 32,768 tokens" in out


def test_new_starts_a_fresh_session(tmp_path, monkeypatch):
    model = FakeModel([say("one"), say("two")])
    _use_model(monkeypatch, model)
    out = _run(tmp_path, [], ["first", "/new", "second"])
    assert "New session" in out
    assert [m.text for m in model.requests[1].messages] == ["second"]
    with EventLog(str(tmp_path / "agent.db")) as log:
        assert len({row for row in log._conn.execute("SELECT session_id FROM sessions")}) == 2


def test_nothing_to_resume(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([]))
    assert "Nothing to resume" in _run(tmp_path, [], ["/resume"])


def test_unknown_command(tmp_path, monkeypatch):
    _use_model(monkeypatch, FakeModel([]))
    assert "Unknown command /nope" in _run(tmp_path, [], ["/nope"])


@pytest.mark.parametrize("command", ["/exit", "/quit"])
def test_exit_commands(tmp_path, monkeypatch, command):
    model = FakeModel([])
    _use_model(monkeypatch, model)
    _run(tmp_path, [], [command, "never sent"])
    assert model.requests == []


# ── helpers ──────────────────────────────────────────────────────────────────────────────


class FlakyModel(FakeModel):
    """Raises ModelError for the first `fail_times` requests, then follows its script."""

    def __init__(self, fail_times, replies):
        super().__init__(replies=replies)
        self.fail_times = fail_times

    async def complete(self, system, messages, tools, on_delta=None):
        if self.fail_times:
            self.fail_times -= 1
            raise ModelError("Anthropic API error: overloaded")
        return await super().complete(system, messages, tools, on_delta)


class LocalModel(FakeModel):
    """A local model: it has a known context window and a startup check."""

    context_window = 32_768

    def __init__(self, replies, check_error=None):
        super().__init__(replies=replies)
        self.check_error = check_error

    async def check(self):
        if self.check_error is not None:
            raise self.check_error


def _use_model(monkeypatch, model):
    monkeypatch.setattr(cli, "make_model", lambda provider, model_name=None: model)


def _use_approval_tool(monkeypatch):
    """Give the agent a `save_note` tool that needs approval; returns the notes it saved."""
    saved = []

    def save_note(text):
        saved.append(text)
        return f"saved {text}"

    tool = Tool(
        "save_note", "Save a note.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        save_note, requires_approval=True,
    )
    monkeypatch.setattr(cli, "agent_tools", lambda web: [tool])
    return saved


def _run(tmp_path, args, inputs):
    """Run main() with scripted prompt answers. A KeyboardInterrupt entry is raised at that prompt."""
    answers = iter(inputs)

    def read(prompt):
        answer = next(answers, EOFError)
        if answer in (EOFError, KeyboardInterrupt):
            raise answer()
        return answer

    out = io.StringIO()
    main(["--db", str(tmp_path / "agent.db"), *args], read=read, out=out)
    return out.getvalue()
