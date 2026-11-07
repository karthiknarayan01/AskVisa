"""Run a suite: each case as one real session against the model, then checked and judged.

    run_suite(suite, cases, model, judge, db_path, repeat=3, parallel=2)

A case runs exactly like a chat turn: the production system prompt (dated suite.today), the
production tool definitions answering from fixtures, and the runtime's own loop, recorded in an
event log at db_path. That log is the trace: after a run, any case can be inspected with
`agent-server --db <db_path>`, then GET /api/sessions/<session id>.

Models are not deterministic, so each case can be run `repeat` times; a case's score is the
share of attempts that passed. An attempt passes when every check and every judge item passes.
A model error or a turn that never ends counts as a failed attempt, with the error recorded.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from evals.cases import Case, Suite
from evals.checks import CheckResult, ToolCallRecord, run_checks
from evals.fixtures import fixture_registry
from evals.judge import Verdict, judge_answer
from runtime.approval import AutoApprove
from runtime.events import EventLog
from runtime.model import ModelClient
from runtime.prompts import system_prompt
from runtime.session import Session


@dataclass
class Attempt:
    case_id: str
    attempt: int
    session_id: str
    answer: str = ""
    calls: list[ToolCallRecord] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and all(c.passed for c in self.checks)
            and all(v.passed for v in self.verdicts)
        )


async def run_suite(
    suite: Suite,
    cases: list[Case],
    model: ModelClient,
    judge: ModelClient | None,
    db_path: str,
    repeat: int = 1,
    parallel: int = 1,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> list[Attempt]:
    """Every case `repeat` times, at most `parallel` at once, in case order in the result."""
    limit = asyncio.Semaphore(parallel)
    with EventLog(db_path) as log:

        async def one(case: Case, n: int) -> Attempt:
            async with limit:
                attempt = await run_case(suite, case, model, judge, log, n)
            if on_attempt is not None:
                on_attempt(attempt)
            return attempt

        return list(await asyncio.gather(*(one(c, n) for c in cases for n in range(1, repeat + 1))))


async def run_case(
    suite: Suite, case: Case, model: ModelClient, judge: ModelClient | None, log: EventLog, n: int = 1
) -> Attempt:
    """One attempt at one case: the turn, then the checks, then the judge."""
    tools = fixture_registry(suite, case)
    session = Session.create(
        log,
        model,
        tools,
        approver=AutoApprove(),
        system_prompt=system_prompt(suite.today, [t.name for t in tools.specs()]),
    )
    attempt = Attempt(case.id, n, session.session_id)
    started = time.monotonic()
    try:
        result = await session.run_turn(case.question)
        attempt.answer = result.text
    except Exception as e:  # noqa: BLE001 - any failure of the turn is the attempt's result
        attempt.error = f"{type(e).__name__}: {e}"
    attempt.seconds = time.monotonic() - started
    attempt.calls = trace_calls(session)
    attempt.input_tokens, attempt.output_tokens = token_usage(log, session.session_id)
    attempt.checks = run_checks(case, attempt.calls)
    if case.judge and attempt.error is None:
        if judge is None:
            attempt.verdicts = []
        else:
            try:
                attempt.verdicts = await judge_answer(judge, case.question, attempt.calls, attempt.answer, case.judge)
            except Exception as e:  # noqa: BLE001
                attempt.verdicts = [Verdict(c, False, f"judge failed: {type(e).__name__}: {e}") for c in case.judge]
    return attempt


def trace_calls(session: Session) -> list[ToolCallRecord]:
    """The turn's tool calls in the order the model made them, each with its result."""
    results = {m.tool_call_id: m for m in session.state.messages if m.role == "tool"}
    records = []
    for call_state in session.state.tool_calls.values():
        call = call_state.call
        result = results.get(call.id)
        records.append(ToolCallRecord(
            name=call.name,
            arguments=dict(call.arguments),
            output=result.text if result else "",
            is_error=result.is_error if result else False,
        ))
    return records


def token_usage(log: EventLog, session_id: str) -> tuple[int, int]:
    """Input and output tokens over every model request in the session."""
    total_in = total_out = 0
    for event in log.load(session_id):
        usage = event.payload.get("usage") if event.type == "assistant_message" else None
        if usage:
            total_in += usage.get("input_tokens", 0)
            total_out += usage.get("output_tokens", 0)
    return total_in, total_out
