"""Mapping session events to the wire, and stored conversations to the same shapes."""

import json

from runtime.api_events import MAX_OUTPUT_CHARS, conversation, to_wire, tool_result
from runtime.compaction import summary_message_text
from runtime.events import Event
from runtime.messages import Message, ToolCall
from runtime.model import StreamDelta
from runtime.state import SessionState, ToolCallState


def event(type_, **payload):
    return Event("s", 1, type_, payload, "t")


def test_deltas_become_text_and_reasoning():
    state = SessionState()
    assert to_wire(StreamDelta("text", "Hi"), state) == ("text", {"delta": "Hi"})
    assert to_wire(StreamDelta("reasoning", "hmm"), state) == ("reasoning", {"delta": "hmm"})


def test_messages_and_tool_events():
    state = SessionState()
    call = ToolCall("c1", "web_search", {"query": "H-1B"})
    state.tool_calls["c1"] = ToolCallState(call=call)
    assert to_wire(Event("s", 1, "user_message", {"text": "q"}, "t", "r-1"), state) == (
        "user_message", {"text": "q", "request_id": "r-1"})
    kind, data = to_wire(event("assistant_message", text="", reasoning="r",
                               tool_calls=[{"id": "c1", "name": "web_search", "arguments": {"query": "H-1B"}, "arguments_error": None}],
                               usage={"input_tokens": 5, "output_tokens": 2}), state)
    assert kind == "assistant_message"
    assert data["tool_calls"] == [{"id": "c1", "name": "web_search", "arguments": {"query": "H-1B"}}]
    assert to_wire(event("tool_started", call_id="c1"), state) == (
        "tool_started", {"id": "c1", "name": "web_search", "arguments": {"query": "H-1B"}})
    assert to_wire(event("tool_restarted", call_id="c1"), state)[0] == "tool_started"
    kind, data = to_wire(event("tool_finished", call_id="c1", output="No results.", is_error=False), state)
    assert kind == "tool_finished" and data["summary"] == "no results" and data["name"] == "web_search"
    assert to_wire(event("tool_skipped", call_id="c1", reason="denied"), state) == (
        "tool_skipped", {"id": "c1", "name": "web_search", "reason": "denied"})
    assert to_wire(event("turn_stopped", reason="cancelled"), state) == ("turn_stopped", {"reason": "cancelled"})
    assert to_wire(event("compaction", replaced_count=12), state) == ("compaction", {"replaced_count": 12})
    assert to_wire(event("approval_decided", call_id="c1", decision="approve"), state) is None


def test_tool_results_summarize_and_clip():
    ok = tool_result("c1", "grace_period_end", "The grace period ends on 2026-12-01 at the latest\nRule: …", False)
    assert ok["summary"] == "The grace period ends on 2026-12-01 at the latest"
    error = tool_result("c1", "perm_queue", "perm_queue failed: DataLookupError: HTTP 429\nmore", True)
    assert error["summary"] == "perm_queue failed: DataLookupError: HTTP 429" and error["is_error"]
    long = tool_result("c1", "read_web_page", "x" * (MAX_OUTPUT_CHARS + 10), False)
    assert long["summary"] == f"{MAX_OUTPUT_CHARS + 10:,} characters" and long["output"].endswith(" …")


def test_a_stored_conversation_uses_the_same_shapes():
    state = SessionState()
    call = ToolCall("c1", "echo", {"text": "hi"})
    state.messages = [
        Message("user", summary_message_text("They asked about OPT.", "What now?")),
        Message("user", "Does Acme sponsor?"),
        Message("assistant", "", reasoning="look it up", tool_calls=(call,)),
        Message("tool", "echo: hi", tool_call_id="c1"),
        Message("assistant", "Yes."),
    ]
    items = conversation(state)
    assert [i["type"] for i in items] == ["compaction", "user_message", "assistant_message", "tool_finished", "assistant_message"]
    assert items[2]["tool_calls"] == [{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]
    assert items[3]["name"] == "echo" and items[3]["arguments"] == {"text": "hi"} and items[3]["summary"] == "echo: hi"
    json.dumps(items)


def test_a_dol_result_with_a_note_is_summarized_by_the_note():
    from runtime.tool_summary import summarize

    noted = "Department of Labor data as of 2026-11-06\nMatch: exact. The numbers below are this employer's record.\n{}"
    assert summarize("match_employer", noted) == "Match: exact. The numbers below are this employer's record."
    plain = "Department of Labor data as of 2026-11-06\n{\"codes\": []}"
    assert summarize("find_soc_code", plain) == "Department of Labor data as of 2026-11-06"
