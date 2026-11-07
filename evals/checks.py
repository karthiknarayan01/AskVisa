"""Deterministic checks: what a case says about the tool calls, tested against the run's trace.

Each check gives a pass or fail and a one-line reason, so a failed run says what went wrong
("called perm_case_status, which this case forbids") rather than only that it failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evals.cases import Case


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def run_checks(case: Case, calls: list[ToolCallRecord]) -> list[CheckResult]:
    """Every check the case asks for, in a fixed order."""
    names = [c.name for c in calls]
    results: list[CheckResult] = []
    if case.no_tools:
        results.append(_check("no_tools", not calls, f"called {', '.join(names)}" if calls else "no tool calls"))
    for tool in case.must_call:
        results.append(_check(f"calls {tool}", tool in names, "called" if tool in names else "never called"))
    for tool in case.must_not_call:
        count = names.count(tool)
        results.append(_check(f"doesn't call {tool}", count == 0, f"called {count}x" if count else "not called"))
    if case.call_order:
        results.append(check_order(case.call_order, names))
    for tool, expected in case.args.items():
        results.append(check_args(tool, expected, calls))
    if case.max_calls is not None:
        results.append(_check(
            f"at most {case.max_calls} calls", len(calls) <= case.max_calls, f"{len(calls)} calls"
        ))
    return results


def check_order(order: tuple[str, ...], names: list[str]) -> CheckResult:
    """The first call to each tool in `order` comes after the first call to the one before it."""
    label = "order " + " → ".join(order)
    missing = [t for t in order if t not in names]
    if missing:
        return CheckResult(label, False, f"never called {', '.join(missing)}")
    firsts = [names.index(t) for t in order]
    if firsts != sorted(firsts):
        return CheckResult(label, False, "called in the order " + " → ".join(sorted(order, key=names.index)))
    return CheckResult(label, True, "in order")


def check_args(tool: str, expected: dict[str, str], calls: list[ToolCallRecord]) -> CheckResult:
    """Some call to `tool` has each expected value inside the actual one, ignoring case."""
    label = f"{tool} args " + ", ".join(f"{k}~{v!r}" for k, v in expected.items())
    tried = [c.arguments for c in calls if c.name == tool]
    if not tried:
        return CheckResult(label, False, "never called")
    for arguments in tried:
        if all(v.lower() in _text(arguments.get(k)).lower() for k, v in expected.items()):
            return CheckResult(label, True, json.dumps(arguments))
    return CheckResult(label, False, "got " + "; ".join(json.dumps(a) for a in tried))


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def _check(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name, passed, detail)
