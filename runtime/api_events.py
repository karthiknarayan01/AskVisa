"""What a turn looks like on the wire: one event per step of the turn, sent over the live
channel (runtime/live.py) as {"type": "event", "session_id", "request_id", "event", "data"},
in the order things happen:

    user_message       {"text", "request_id"}                    the turn's question, and the turn's id
    reasoning          {"delta"}                                 a piece of the model's thinking
    text               {"delta"}                                 a piece of the answer
    assistant_message  {"text", "reasoning", "tool_calls", "usage"}  the complete reply of one step
    tool_started       {"id", "name", "arguments"}
    tool_finished      {"id", "name", "summary", "output", "is_error"}
    tool_skipped       {"id", "name", "reason"}
    compaction         {"replaced_count"}                        older messages were summarized
    turn_stopped       {"reason"}                                cancelled, max_steps, truncated, denied
    end                {"outcome", "request_id", "text"?, "message"?}  always last

`text` and `reasoning` deltas are the reply while it streams; `assistant_message` follows with
the whole of it. A step that calls tools has an assistant_message with tool_calls and usually no
text, then its tool events, then the next step. `end` says how the turn finished:

    completed   "text" is the final answer
    cancelled   the user stopped it
    stopped     the turn stopped early: "message" says why (step limit, truncated reply, ...)
    error       a model API error: "message"; the turn is kept, and a resume retries it

The same shapes describe a stored conversation (GET /api/sessions/{id}), so a UI renders history
and live turns with one code path. There, the summary that replaced older messages after a
compaction is a "compaction" item holding the summary text.
"""

from __future__ import annotations

from typing import Any

from runtime.compaction import SUMMARY_HEADER
from runtime.events import Event
from runtime.model import StreamDelta
from runtime.state import SessionState
from runtime.tool_summary import summarize

MAX_OUTPUT_CHARS = 4_000


def to_wire(item: Event | StreamDelta, state: SessionState) -> tuple[str, dict[str, Any]] | None:
    """A published item as (event type, data), or None for items a UI has no use for."""
    if isinstance(item, StreamDelta):
        return ("reasoning" if item.kind == "reasoning" else "text"), {"delta": item.text}
    payload = item.payload
    if item.type == "user_message":
        return "user_message", {"text": payload["text"], "request_id": item.request_id}
    if item.type == "assistant_message":
        return "assistant_message", {
            "text": payload.get("text", ""),
            "reasoning": payload.get("reasoning", ""),
            "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in payload.get("tool_calls", [])],
            "usage": payload.get("usage") or {},
        }
    if item.type in ("tool_started", "tool_restarted"):
        call = state.tool_calls[payload["call_id"]].call
        return "tool_started", {"id": call.id, "name": call.name, "arguments": call.arguments}
    if item.type == "tool_finished":
        call = state.tool_calls[payload["call_id"]].call
        return "tool_finished", tool_result(call.id, call.name, payload["output"], payload["is_error"])
    if item.type == "tool_skipped":
        call = state.tool_calls[payload["call_id"]].call
        return "tool_skipped", {"id": call.id, "name": call.name, "reason": payload["reason"]}
    if item.type == "compaction":
        return "compaction", {"replaced_count": payload.get("replaced_count", 0)}
    if item.type == "turn_stopped":
        return "turn_stopped", {"reason": payload["reason"]}
    return None


def tool_result(call_id: str, name: str, output: str, is_error: bool) -> dict[str, Any]:
    """A finished tool call: a one-line summary for display, and the output (clipped)."""
    first_line = output.splitlines()[0] if output else ""
    return {
        "id": call_id,
        "name": name,
        "summary": first_line if is_error else summarize(name, output),
        "output": output if len(output) <= MAX_OUTPUT_CHARS else output[:MAX_OUTPUT_CHARS] + " …",
        "is_error": is_error,
    }


def conversation(state: SessionState, request_ids: list[str | None] | None = None) -> list[dict[str, Any]]:
    """A stored conversation as the same items a live turn streams, without the deltas.

    `request_ids` are the session's turn ids in order (from its user_message events). Each item
    gets the id of the turn it belongs to. After a compaction the earliest turns are summarized
    away, so the ids are matched to the user messages still present from the end.
    """
    users = sum(1 for m in state.messages if m.role == "user" and not m.text.startswith(SUMMARY_HEADER))
    ids = iter((request_ids or [])[-users:] if users else [])
    names = {c.id: c.name for m in state.messages for c in m.tool_calls}
    arguments = {c.id: c.arguments for m in state.messages for c in m.tool_calls}
    current: str | None = None
    items: list[dict[str, Any]] = []
    for message in state.messages:
        if message.role == "user" and message.text.startswith(SUMMARY_HEADER):
            items.append({"type": "compaction", "summary": message.text, "request_id": None})
            continue
        if message.role == "user":
            current = next(ids, None)
            item: dict[str, Any] = {"type": "user_message", "text": message.text}
        elif message.role == "assistant":
            item = {
                "type": "assistant_message",
                "text": message.text,
                "reasoning": message.reasoning,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls],
            }
        else:
            call_id = message.tool_call_id or ""
            item = {
                "type": "tool_finished",
                **tool_result(call_id, names.get(call_id, ""), message.text, message.is_error),
                "arguments": arguments.get(call_id, {}),
            }
        item["request_id"] = current
        items.append(item)
    return items
