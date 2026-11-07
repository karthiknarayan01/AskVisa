"""The reasoning suite: it loads, names only real tools, and its calculator answers are right."""

import asyncio
from pathlib import Path

from evals.cases import load_suite
from evals.fixtures import fixture_registry, real_tools
from runtime.messages import ToolCall

SUITE = load_suite(Path(__file__).resolve().parent.parent / "evals" / "suites" / "reasoning.toml")
TOOLS = {t.name: t for t in real_tools(SUITE.tools)}


def test_the_suite_has_dev_and_held_out_cases_and_every_case_is_judged():
    splits = [c.split for c in SUITE.cases]
    assert splits.count("dev") >= 4 and splits.count("held_out") >= 2
    assert all(len(c.judge) >= 2 for c in SUITE.cases)


def test_every_tool_and_argument_a_case_names_exists():
    for case in SUITE.cases:
        named = {*case.must_call, *case.must_not_call, *case.call_order, *case.args, *case.fixtures}
        assert named <= set(TOOLS), (case.id, named - set(TOOLS))
        for tool, expected in case.args.items():
            assert set(expected) <= set(TOOLS[tool].input_schema["properties"]), (case.id, tool)


def test_the_grace_period_dates_in_the_judge_items_are_the_calculators():
    case = next(c for c in SUITE.cases if c.id == "h1b-layoff-options")
    tools = fixture_registry(SUITE, case)
    h1b = asyncio.run(tools.run(ToolCall("1", "grace_period_end", {"status": "H-1B", "end_date": "2026-10-20", "i94_expiry": "2027-08-31"})))
    opt = asyncio.run(tools.run(ToolCall("2", "grace_period_end", {"status": "F-1", "end_date": "2027-03-31"})))
    assert "2026-12-18" in h1b.output.split("\n")[0]
    assert "2027-05-29" in opt.output.split("\n")[0]
