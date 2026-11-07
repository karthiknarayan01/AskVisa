"""Results: a table in the terminal, and every detail in results.json.

    case                         pass   calls  tokens in/out   time   first failure
    employer-sponsors            3/3    1.0    5.1k / 210      4.2s
    receipt-number-is-not-dol    2/3    1.3    6.0k / 380      6.8s   doesn't call perm_case_status: called 1x

results.json holds, per attempt, the answer, each tool call with its arguments and result, every
check and judge verdict with its reason, tokens and time: enough to see why an attempt failed
without rerunning it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from evals.runner import Attempt


def print_report(console: Console, attempts: list[Attempt], title: str) -> None:
    """One row per case, then the overall pass rate."""
    by_case: dict[str, list[Attempt]] = defaultdict(list)
    for attempt in attempts:
        by_case[attempt.case_id].append(attempt)
    table = Table(title=title, title_justify="left", header_style="bold")
    for column, justify in (("case", "left"), ("pass", "right"), ("calls", "right"),
                            ("tokens in/out", "right"), ("time", "right"), ("first failure", "left")):
        table.add_column(column, justify=justify, overflow="fold", no_wrap=column == "case")
    for case_id, runs in by_case.items():
        passed = sum(a.passed for a in runs)
        style = "green" if passed == len(runs) else ("yellow" if passed else "red")
        table.add_row(
            case_id,
            f"[{style}]{passed}/{len(runs)}[/]",
            f"{_mean(len(a.calls) for a in runs):.1f}",
            f"{_tokens(_mean(a.input_tokens for a in runs))} / {_tokens(_mean(a.output_tokens for a in runs))}",
            f"{_mean(a.seconds for a in runs):.1f}s",
            next((first_failure(a) for a in runs if not a.passed), ""),
        )
    console.print(table)
    total = sum(a.passed for a in attempts)
    cases_all = sum(all(a.passed for a in runs) for runs in by_case.values())
    console.print(
        f"[bold]{total}/{len(attempts)}[/] attempts passed ({_percent(total, len(attempts))}); "
        f"[bold]{cases_all}/{len(by_case)}[/] cases passed every attempt"
    )


def first_failure(attempt: Attempt) -> str:
    """The first thing that went wrong in an attempt, as one line."""
    if attempt.error:
        return f"error: {attempt.error}"
    for check in attempt.checks:
        if not check.passed:
            return f"{check.name}: {check.detail}"
    for verdict in attempt.verdicts:
        if not verdict.passed:
            return f"judge: {verdict.criterion}: {verdict.reason}"
    return ""


def write_results(path: Path, attempts: list[Attempt], meta: dict[str, Any]) -> None:
    """Everything about the run, as JSON."""
    path.write_text(json.dumps(
        {
            **meta,
            "passed": sum(a.passed for a in attempts),
            "attempts": len(attempts),
            "results": [{**asdict(a), "passed": a.passed} for a in attempts],
        },
        indent=2,
        ensure_ascii=False,
    ))


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _tokens(n: float) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "0%"
