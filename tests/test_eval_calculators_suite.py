"""The calculators suite: it loads, names only real tools and parameters, and its expected
answers are what the calculators actually return for each case's dates."""

import asyncio
from pathlib import Path

import pytest

from evals.cases import load_suite
from evals.fixtures import fixture_registry, real_tools
from runtime.messages import ToolCall

SUITE = load_suite(Path(__file__).resolve().parent.parent / "evals" / "suites" / "calculators.toml")
TOOLS = {t.name: t for t in real_tools(SUITE.tools)}


def test_the_suite_has_dev_and_held_out_cases():
    splits = [c.split for c in SUITE.cases]
    assert splits.count("dev") >= 8 and splits.count("held_out") >= 4


def test_every_tool_and_argument_a_case_names_exists():
    for case in SUITE.cases:
        named = {*case.must_call, *case.must_not_call, *case.call_order, *case.args, *case.fixtures}
        assert named <= set(TOOLS), (case.id, named - set(TOOLS))
        for tool, expected in case.args.items():
            assert set(expected) <= set(TOOLS[tool].input_schema["properties"]), (case.id, tool)


@pytest.mark.parametrize("case_id, tool, arguments, answer", [
    ("grace-h1b-layoff", "grace_period_end", {"status": "H-1B", "end_date": "2026-10-03", "i94_expiry": "2027-09-30"}, "2026-12-01"),
    ("grace-capped-by-i94", "grace_period_end", {"status": "TN", "end_date": "2026-11-01", "i94_expiry": "2026-11-20"}, "2026-11-20"),
    ("grace-after-opt", "grace_period_end", {"status": "F-1", "end_date": "2027-06-30"}, "2027-08-28"),
    ("opt-days-left", "opt_unemployment_days", {"opt_type": "post_completion", "days_used": 20, "unemployed_since": "2026-10-01"}, "32 of 90"),
    ("opt-stem-total", "opt_unemployment_days", {"opt_type": "stem", "days_used": 75}, "75 of 150"),
    ("h1b-max-with-time-abroad", "max_stay_end", {"classification": "H-1B", "start_date": "2021-10-01", "days_outside_us": 120}, "2028-01-28"),
    ("h1b-max-mentions-recapture", "max_stay_end", {"classification": "H-1B", "start_date": "2022-03-01"}, "2028-02-29"),
    ("naturalization-five-year", "naturalization_filing_date", {"green_card_date": "2022-03-15", "path": "five_year"}, "2026-12-15"),
    ("naturalization-three-year", "naturalization_filing_date", {"green_card_date": "2024-06-01", "path": "three_year"}, "2027-03-03"),
    ("naturalization-physical-presence", "naturalization_filing_date", {"green_card_date": "2022-03-15", "path": "five_year", "days_outside_us": 1000}, "does not meet the 913-day"),
    ("date-plus-days", "date_math", {"operation": "add_days", "start": "2026-08-20", "days": 180}, "2027-02-16"),
    ("days-until-i94-expires", "date_math", {"operation": "days_between", "start": "2026-11-07", "end": "2027-03-01"}, "114 days"),
])
def test_the_judge_items_match_what_the_calculator_returns(case_id, tool, arguments, answer):
    case = next(c for c in SUITE.cases if c.id == case_id)
    tools = fixture_registry(SUITE, case)
    result = asyncio.run(tools.run(ToolCall("c1", tool, arguments)))
    assert not result.is_error and answer in result.output, result.output


def test_calculators_use_the_suites_date_not_the_real_one():
    case = next(c for c in SUITE.cases if c.id == "opt-days-left")
    result = asyncio.run(fixture_registry(SUITE, case).run(
        ToolCall("c1", "opt_unemployment_days", {"opt_type": "post_completion", "unemployed_since": "2026-11-01"})
    ))
    assert "Used: 7 (0 before, plus 7 since 2026-11-01)" in result.output
