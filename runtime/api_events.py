"""What a turn looks like on the wire: Server-Sent Events, one per step of the turn.

Each event is `event: <type>` and `data: <json>`, in the order things happen:

    user_message       {"text"}                                  the turn's question, as recorded
    reasoning          {"delta"}                                 a piece of the model's thinking
    text               {"delta"}                                 a piece of the answer
    assistant_message  {"text", "reasoning", "tool_calls", "usage"}  the complete reply of one step
    tool_started       {"id", "name", "arguments"}
    tool_finished      {"id", "name", "summary", "output", "is_error"}
    tool_skipped       {"id", "name", "reason"}
    compaction         {"replaced_count"}                        older messages were summarized
    turn_stopped       {"reason"}                                cancelled, max_steps, truncated, denied
    end                {"outcome", "text"?, "message"?}          always last

`text` and `reasoning` deltas are the reply while it streams; `assistant_message` follows with
the whole of it. A step that calls tools has an assistant_message with tool_calls and usually no
text, then its tool events, then the next step. `end` says how the turn finished:

    completed   "text" is the final answer
    cancelled   the user stopped it
    stopped     the turn stopped early: "message" says why (step limit, truncated reply, ...)
    error       a model API error: "message"; the turn is kept, and /resume retries it

The same shapes describe a stored conversation (GET /api/sessions/{id}), so a UI renders history
and live turns with one code path. There, the summary that replaced older messages after a
compaction is a "compaction" item holding the summary text.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.compaction import SUMMARY_HEADER
from runtime.events import Event
from runtime.model import StreamDelta
from runtime.state import SessionState
from runtime.tool_summary import summarize

MAX_OUTPUT_CHARS = 4_000


def sse(event_type: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def to_wire(item: Event | StreamDelta, state: SessionState) -> tuple[str, dict[str, Any]] | None:
    """A published item as (event type, data), or None for items a UI has no use for."""
    if isinstance(item, StreamDelta):
        return ("reasoning" if item.kind == "reasoning" else "text"), {"delta": item.text}
    payload = item.payload
    if item.type == "user_message":
        return "user_message", {"text": payload["text"]}
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


def conversation(state: SessionState) -> list[dict[str, Any]]:
    """A stored conversation as the same items a live turn streams, without the deltas."""
    items: list[dict[str, Any]] = []
    names = {c.id: c.name for m in state.messages for c in m.tool_calls}
    arguments = {c.id: c.arguments for m in state.messages for c in m.tool_calls}
    for message in state.messages:
        if message.role == "user" and message.text.startswith(SUMMARY_HEADER):
            items.append({"type": "compaction", "summary": message.text})
        elif message.role == "user":
            items.append({"type": "user_message", "text": message.text})
        elif message.role == "assistant":
            items.append({
                "type": "assistant_message",
                "text": message.text,
                "reasoning": message.reasoning,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls],
            })
        else:
            call_id = message.tool_call_id or ""
            items.append({
                "type": "tool_finished",
                **tool_result(call_id, names.get(call_id, ""), message.text, message.is_error),
                "arguments": arguments.get(call_id, {}),
            })
    return items
