"""The date calculators: each rule's arithmetic, its edge cases, and its errors."""

import pytest

from runtime.calculator_tools import (
    add_years,
    calculator_tools,
    date_math,
    grace_period_end,
    max_stay_end,
    naturalization_filing_date,
    opt_unemployment_days,
)
from runtime.messages import ToolCall
from runtime.tools import ToolRegistry


def first_line(text):
    return text.split("\n", 1)[0]


# --- date_math ---------------------------------------------------------------------------------

def test_days_between_counts_both_ways():
    out = date_math("days_between", "2026-11-07", "2027-01-01")
    assert first_line(out) == "55 days from 2026-11-07 to 2027-01-01"
    assert "Counting both 2026-11-07 and 2027-01-01: 56 days." in out


def test_add_days_forward_and_back_with_weekday():
    assert date_math("add_days", "2026-11-07", days=180) == "2027-05-06 (Thursday) is 180 days after 2026-11-07"
    assert date_math("add_days", "2026-03-01", days=-1) == "2026-02-28 (Saturday) is -1 days before 2026-03-01"


def test_date_math_needs_its_operands():
    with pytest.raises(ValueError, match="needs end"):
        date_math("days_between", "2026-11-07")
    with pytest.raises(ValueError, match="needs days"):
        date_math("add_days", "2026-11-07")


# --- OPT unemployment ----------------------------------------------------------------------------

def test_opt_counts_the_current_stretch_including_both_ends():
    out = opt_unemployment_days("post_completion", 10, "2026-09-15", "2026-11-07")
    assert first_line(out) == "26 of 90 unemployment days left; at this rate the limit is reached on 2026-12-03"
    assert "Used: 64 (10 before, plus 54 since 2026-09-15)" in out


def test_stem_opt_allows_150_in_total():
    assert first_line(opt_unemployment_days("stem", 95)) == "55 of 150 unemployment days left"


def test_opt_over_the_limit():
    assert first_line(opt_unemployment_days("post_completion", 85, "2026-11-01", "2026-11-07")) == (
        "Over the limit: 92 of 90 unemployment days used (2 over)"
    )


def test_opt_start_after_as_of_is_an_error():
    with pytest.raises(ValueError, match="after as_of"):
        opt_unemployment_days("stem", 0, "2026-12-01", "2026-11-07")


# --- grace periods ---------------------------------------------------------------------------------

def test_work_visa_grace_period_is_60_days_counting_the_last_day():
    out = grace_period_end("H-1B", "2026-10-03")
    assert first_line(out) == "The grace period ends on 2026-12-01 at the latest"
    assert "8 CFR 214.1(l)(2)" in out and "runs one day longer, to 2026-12-02" in out


def test_work_visa_grace_period_stops_at_the_i94_date():
    out = grace_period_end("TN", "2026-10-03", "2026-10-20")
    assert first_line(out) == "The grace period ends on 2026-10-20 at the latest"
    assert "Capped by the I-94 end date" in out


def test_a_later_i94_date_doesnt_shorten_it():
    assert "doesn't cut it short" in grace_period_end("O-1", "2026-10-03", "2027-09-30")


@pytest.mark.parametrize("status, end, last", [("F-1", "2026-06-30", "2026-08-28"), ("J-1", "2026-05-15", "2026-06-13")])
def test_student_and_exchange_grace_periods(status, end, last):
    assert first_line(grace_period_end(status, end)) == f"The grace period ends on {last}"


# --- maximum stay ----------------------------------------------------------------------------------

def test_h1b_maximum_adds_back_time_abroad():
    out = max_stay_end("H-1B", "2021-10-01", 120, "2026-11-07")
    assert first_line(out) == "The H-1B maximum is reached on 2028-01-28"
    assert "As of 2026-11-07: 1743 of 2191 days used, 448 left." in out
    assert "AC21" in out


def test_h1b_maximum_without_time_abroad_is_the_day_before_the_sixth_anniversary():
    assert first_line(max_stay_end("H-1B", "2021-10-01", 0, "2026-11-07")) == "The H-1B maximum is reached on 2027-09-30"


@pytest.mark.parametrize("classification, last", [("L-1A", "2027-12-31"), ("L-1B", "2025-12-31")])
def test_l1_maximums(classification, last):
    out = max_stay_end(classification, "2021-01-01", 0, "2026-11-07")
    assert first_line(out) == f"The {classification} maximum is reached on {last}"
    assert "a year outside the US" in out


# --- naturalization --------------------------------------------------------------------------------

def test_naturalization_matches_the_uscis_early_filing_example():
    out = naturalization_filing_date("2017-07-04", "five_year")
    assert first_line(out) == "The earliest N-400 filing date is 2022-04-05"
    assert "met on 2022-07-04" in out


def test_three_year_path():
    assert first_line(naturalization_filing_date("2023-06-01", "three_year")) == "The earliest N-400 filing date is 2026-03-03"


def test_physical_presence_check():
    meets = naturalization_filing_date("2022-03-15", "five_year", days_outside_us=200)
    assert "1626 of the 1826 days in the US" in meets and "which meets the 913-day requirement" in meets
    short = naturalization_filing_date("2022-03-15", "five_year", days_outside_us=1000)
    assert "does not meet the 913-day requirement" in short


@pytest.mark.parametrize("days, says", [
    (40, "don't break continuous residence"),
    (200, "presumed to break continuous residence"),
    (400, "clock restarts"),
])
def test_long_trips_are_flagged(days, says):
    assert says in naturalization_filing_date("2022-03-15", "five_year", longest_trip_days=days)


# --- shared ---------------------------------------------------------------------------------------

def test_leap_day_anniversaries_fall_on_28_february():
    from datetime import date

    assert add_years(date(2024, 2, 29), 5) == date(2029, 2, 28)
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)


async def test_bad_dates_and_values_come_back_as_errors():
    tools = ToolRegistry(calculator_tools())
    bad_date = await tools.run(ToolCall("1", "grace_period_end", {"status": "H-1B", "end_date": "Oct 3"}))
    assert bad_date.is_error and "end_date must be a date as YYYY-MM-DD" in bad_date.output
    bad_status = await tools.run(ToolCall("2", "grace_period_end", {"status": "B-2", "end_date": "2026-10-03"}))
    assert bad_status.is_error


def test_descriptions_follow_the_shared_layout_and_tools_only_read():
    for tool in calculator_tools():
        assert "\nUse it for" in tool.description and "\nReturns:\n- " in tool.description, tool.name
        assert tool.idempotent and not tool.requires_approval
