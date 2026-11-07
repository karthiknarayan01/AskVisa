"""The judge: a model that grades an answer against a case's checklist, item by item.

It sees the user's question, every tool call with its result (results clipped), and the final
answer, so it can grade claims about the data ("reads the cutoff from the bulletin correctly")
as well as about the wording. It must answer with JSON, one verdict per item:

    {"items": [{"pass": true, "reason": "..."}, ...]}

Anything else, or the wrong number of verdicts, fails every item with the reason, so a broken
judge shows up as failures rather than passing silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from evals.checks import ToolCallRecord
from runtime.messages import Message
from runtime.model import ModelClient

MAX_RESULT_CHARS = 3_000

JUDGE_PROMPT = """\
You grade an AI assistant's answer against a checklist. The assistant answers questions about US \
immigration and can call tools; you see the question, its tool calls with their results, and \
its final answer.

For each checklist item, decide whether the final answer satisfies it:
- Judge only what is in the final answer, using the tool results to check facts.
- Be strict: an item that is only partly met fails.
- Give a one-sentence reason for every verdict.

Reply with only this JSON, one entry per item, in the checklist's order:
{"items": [{"pass": true or false, "reason": "..."}]}"""


@dataclass(frozen=True)
class Verdict:
    criterion: str
    passed: bool
    reason: str


async def judge_answer(
    judge: ModelClient, question: str, calls: list[ToolCallRecord], answer: str, criteria: tuple[str, ...]
) -> list[Verdict]:
    """One verdict per criterion."""
    reply = await judge.complete(JUDGE_PROMPT, [Message("user", render_transcript(question, calls, answer, criteria))], [])
    return parse_verdicts(reply.text, criteria)


def render_transcript(question: str, calls: list[ToolCallRecord], answer: str, criteria: tuple[str, ...]) -> str:
    lines = [f"QUESTION:\n{question}", ""]
    if calls:
        lines.append("TOOL CALLS:")
        for i, call in enumerate(calls, 1):
            output = call.output
            if len(output) > MAX_RESULT_CHARS:
                output = output[:MAX_RESULT_CHARS] + " … [clipped]"
            label = "ERROR" if call.is_error else "RESULT"
            lines += [f"{i}. {call.name}({json.dumps(call.arguments)})", f"   {label}: {output}"]
    else:
        lines.append("TOOL CALLS: none")
    lines += ["", f"FINAL ANSWER:\n{answer}", "", "CHECKLIST:"]
    lines += [f"{i}. {c}" for i, c in enumerate(criteria, 1)]
    return "\n".join(lines)


def parse_verdicts(text: str, criteria: tuple[str, ...]) -> list[Verdict]:
    """The judge's JSON as verdicts; every item fails if it can't be read."""
    try:
        items = json.loads(text[text.index("{") : text.rindex("}") + 1])["items"]
        if len(items) != len(criteria):
            raise ValueError(f"{len(items)} verdicts for {len(criteria)} items")
        return [Verdict(c, bool(i["pass"]), str(i.get("reason", ""))) for c, i in zip(criteria, items)]
    except (ValueError, KeyError, TypeError) as e:
        reason = f"judge reply unreadable ({e}): {text[:200]!r}"
        return [Verdict(c, False, reason) for c in criteria]
