"""Date calculators for immigration deadlines: exact arithmetic the model shouldn't do in its head.

    date_math(operation, ...)                    days between two dates, or a date plus or minus days
    opt_unemployment_days(opt_type, ...)         OPT unemployment days used and left (90, or 150 on STEM)
    grace_period_end(status, end_date, ...)      when the grace period after a job or program ends
    max_stay_end(classification, start_date, ...) the H-1B / L-1 maximum stay, with time abroad added back
    naturalization_filing_date(green_card_date, ...) when an N-400 can first be filed

Each result's first line is the answer; then the rule it applies, with its citation, and the
assumptions it made. Where the rules leave a detail open (whether the first day counts, for
example) the calculators take the reading that gives the earlier deadline and say so, since a
deadline remembered a day early is harmless and a day late is not. They compute; they don't know
the user's facts, so they only answer as well as the dates they are given.

All tools are pure functions with no network access: no approval, safe to re-run.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date, timedelta

from runtime.tools import Tool

OPT_LIMITS = {"post_completion": 90, "stem": 150}
GRACE_60_DAYS = {"H-1B", "H-1B1", "E-1", "E-2", "E-3", "L-1", "O-1", "TN"}
GRACE_OTHER = {"F-1": 60, "J-1": 30, "M-1": 30}
MAX_STAY_YEARS = {"H-1B": 6, "L-1A": 7, "L-1B": 5}

DATE_MATH = """\
Exact date arithmetic: the number of days between two dates, or a date plus or minus a number of days.

Use it for:
- any count of days or any deadline you would otherwise work out yourself, for example "how many \
days until my I-94 expires?" or "what date is 180 days after my I-485 was filed?"
- checking a date before you tell it to the user

Don't use it for:
- OPT unemployment, grace periods, H-1B or L-1 maximum stay, or naturalization dates: the \
dedicated calculators apply those rules

Returns:
- for days_between: the days from start to end, and the count including both days
- for add_days: the resulting date and its weekday"""

OPT_UNEMPLOYMENT = """\
Counts an F-1 student's OPT unemployment days: used, left, and the date the limit is reached if \
unemployment continues. The limit is 90 days on post-completion OPT and 150 days in total on STEM \
OPT (including the days used on post-completion OPT).

Use it for questions like:
- "I've been unemployed on OPT since September 15; how many days do I have left?"
- "I used 40 days on OPT and I'm now on STEM OPT; how many can I still use?"

How to use it:
- opt_type is post_completion or stem.
- days_used is unemployment days already used before the current stretch (on STEM, include the \
days from post-completion OPT); ask if the user hasn't said.
- unemployed_since is the first day of the current unemployment, if they are unemployed now.
- Say that the SEVP Portal's or DSO's count is the official one, and this is an estimate.

Returns:
- days used and left, and the date the limit is reached if unemployment continues"""

GRACE_PERIOD = """\
Works out when a nonimmigrant's grace period ends after their job or program ends: the last day \
they can stay without status while they change status, transfer, or leave.

Use it for questions like:
- "I was laid off on H-1B; my last day was October 3. How long can I stay?"
- "My OPT ends on June 30; when do I have to leave?"
- "My J-1 program ended on May 15; how long is my grace period?"

How to use it:
- status is the user's status, e.g. H-1B, L-1, O-1, TN, E-3, F-1, J-1.
- end_date is the last day of employment (for work visas) or the end of the program or OPT (for \
F-1, J-1, M-1).
- i94_expiry is the I-94 end date; for work visas the grace period never goes past it. Ask for it \
if the user hasn't given it.
- Mention that the user can't work during it, and that USCIS can shorten it.

Returns:
- the last day of the grace period, the rule and citation, and what can and can't be done in it"""

MAX_STAY = """\
Works out when an H-1B or L-1 worker reaches the maximum time allowed in that status (H-1B 6 \
years, L-1A 7, L-1B 5), adding back time spent outside the US, which doesn't count.

Use it for questions like:
- "My H-1B started on 2021-10-01 and I've spent 120 days abroad; when do I max out?"
- "How long can I stay on L-1B?"

How to use it:
- classification is H-1B, L-1A or L-1B.
- start_date is when the user first started in that status.
- days_outside_us is the total days spent outside the US since then; ask for it, since it moves \
the date (only whole days abroad count).
- Mention the ways past the H-1B limit (an extension based on a PERM or I-140 filed at least 365 \
days earlier, or an approved I-140 with a backlogged priority date) without promising one.

Returns:
- the date the maximum is reached, the time used and left as of today, and the rule"""

NATURALIZATION = """\
Works out when a green card holder can first file for US citizenship (Form N-400): the date they \
meet the residence requirement, and the earliest filing date 90 days before it.

Use it for questions like:
- "I got my green card on 2022-03-15; when can I apply for citizenship?"
- "I'm married to a US citizen and became a permanent resident in June 2023; when can I file?"

How to use it:
- green_card_date is the "resident since" date on the green card.
- path is five_year, or three_year for someone married to and living with a US citizen for those \
three years.
- days_outside_us (optional) is the total days spent abroad since then: with it, the result checks \
the physical presence requirement (at least half the period in the US).
- longest_trip_days (optional) flags trips long enough to break continuous residence.

Returns:
- the residence date, the earliest filing date, the physical presence check when given the days, \
and the other requirements to meet"""


def calculator_tools(today: Callable[[], date] = date.today) -> list[Tool]:
    """The date calculators. `today` gives the date used when as_of isn't passed; evals fix it."""

    def as_of_today(fn):
        return lambda as_of=None, **arguments: fn(as_of=as_of or today().isoformat(), **arguments)

    day = {"type": "string", "description": "A date, YYYY-MM-DD"}
    return [
        _tool(
            "date_math",
            DATE_MATH,
            {
                "operation": {"type": "string", "enum": ["days_between", "add_days"]},
                "start": day,
                "end": {**day, "description": "days_between only: the later date, YYYY-MM-DD"},
                "days": {"type": "integer", "description": "add_days only: days to add (negative to subtract)"},
            },
            ["operation", "start"],
            date_math,
        ),
        _tool(
            "opt_unemployment_days",
            OPT_UNEMPLOYMENT,
            {
                "opt_type": {"type": "string", "enum": list(OPT_LIMITS)},
                "days_used": {"type": "integer", "minimum": 0,
                              "description": "Unemployment days already used before the current stretch"},
                "unemployed_since": {**day, "description": "First day of the current unemployment, YYYY-MM-DD"},
                "as_of": {**day, "description": "Count up to this date (default today), YYYY-MM-DD"},
            },
            ["opt_type"],
            as_of_today(opt_unemployment_days),
        ),
        _tool(
            "grace_period_end",
            GRACE_PERIOD,
            {
                "status": {"type": "string", "enum": sorted(GRACE_60_DAYS | set(GRACE_OTHER))},
                "end_date": {**day, "description": "Last day of employment, or end of the program or OPT, YYYY-MM-DD"},
                "i94_expiry": {**day, "description": "The I-94 end date, YYYY-MM-DD"},
            },
            ["status", "end_date"],
            grace_period_end,
        ),
        _tool(
            "max_stay_end",
            MAX_STAY,
            {
                "classification": {"type": "string", "enum": list(MAX_STAY_YEARS)},
                "start_date": {**day, "description": "First day in this status, YYYY-MM-DD"},
                "days_outside_us": {"type": "integer", "minimum": 0,
                                    "description": "Whole days spent outside the US since then"},
                "as_of": {**day, "description": "Report time used up to this date (default today), YYYY-MM-DD"},
            },
            ["classification", "start_date"],
            as_of_today(max_stay_end),
        ),
        _tool(
            "naturalization_filing_date",
            NATURALIZATION,
            {
                "green_card_date": {**day, "description": "The 'resident since' date, YYYY-MM-DD"},
                "path": {"type": "string", "enum": ["five_year", "three_year"]},
                "days_outside_us": {"type": "integer", "minimum": 0,
                                    "description": "Total days spent outside the US since then"},
                "longest_trip_days": {"type": "integer", "minimum": 0,
                                      "description": "Days of the longest single trip abroad"},
            },
            ["green_card_date", "path"],
            naturalization_filing_date,
        ),
    ]


def date_math(operation: str, start: str, end: str | None = None, days: int | None = None) -> str:
    begin = parse_date(start, "start")
    if operation == "days_between":
        if end is None:
            raise ValueError("days_between needs end")
        finish = parse_date(end, "end")
        between = (finish - begin).days
        return "\n".join([
            f"{between} days from {begin} to {finish}",
            f"Counting both {begin} and {finish}: {abs(between) + 1} days.",
        ])
    if days is None:
        raise ValueError("add_days needs days")
    result = begin + timedelta(days=days)
    return f"{result} ({result:%A}) is {days} days {'after' if days >= 0 else 'before'} {begin}"


def opt_unemployment_days(
    opt_type: str, days_used: int = 0, unemployed_since: str | None = None, as_of: str | None = None
) -> str:
    limit = OPT_LIMITS[opt_type]
    today = parse_date(as_of, "as_of") if as_of else date.today()
    current = 0
    if unemployed_since:
        since = parse_date(unemployed_since, "unemployed_since")
        if since > today:
            raise ValueError("unemployed_since is after as_of")
        current = (today - since).days + 1
    used = days_used + current
    left = limit - used
    if left <= 0:
        answer = f"Over the limit: {used} of {limit} unemployment days used ({-left} over)"
    elif current:
        answer = f"{left} of {limit} unemployment days left; at this rate the limit is reached on {today + timedelta(days=left)}"
    else:
        answer = f"{left} of {limit} unemployment days left"
    lines = [
        answer,
        f"Used: {used} ({days_used} before" + (f", plus {current} since {unemployed_since}" if current else "") + ")",
        "Rule: 8 CFR 214.2(f)(10)(ii)(E): at most 90 days of unemployment on post-completion OPT; "
        "150 in total with a STEM OPT extension, counting the post-completion days.",
        "Assumptions: every calendar day counts, weekends included, and both the first day of "
        "unemployment and today count. The SEVP Portal's unemployment counter is the official count.",
    ]
    return "\n".join(lines)


def grace_period_end(status: str, end_date: str, i94_expiry: str | None = None) -> str:
    ended = parse_date(end_date, "end_date")
    if status in GRACE_60_DAYS:
        last = ended + timedelta(days=59)
        capped = False
        if i94_expiry:
            expiry = parse_date(i94_expiry, "i94_expiry")
            if expiry < last:
                last, capped = expiry, True
        lines = [
            f"The grace period ends on {last} at the latest",
            "Rule: 8 CFR 214.1(l)(2): after the job ends, up to 60 consecutive days or until the end "
            "of the authorized validity period, whichever is shorter, once per validity period.",
            ("Capped by the I-94 end date, which comes before the 60 days run out."
             if capped else
             "Not capped: no I-94 date was given, and the period never goes past it."
             if not i94_expiry else "The I-94 end date comes after the 60 days, so it doesn't cut it short."),
            f"Assumption: day 1 is taken to be the last day of employment ({ended}), so day 60 is "
            f"{ended + timedelta(days=59)}. If day 1 is the day after, it runs one day longer, to "
            f"{ended + timedelta(days=60)}; plan on the earlier date.",
            "In the grace period the person can't work, but a new employer can file an H-1B transfer, "
            "or they can apply to change status, or leave. USCIS can shorten or refuse it at its discretion.",
        ]
        return "\n".join(lines)
    days = GRACE_OTHER[status]
    last = ended + timedelta(days=days - 1)
    rules = {
        "F-1": "8 CFR 214.2(f)(5)(iv): 60 days after completing the program or OPT, to prepare to "
               "leave, transfer schools, or change status (15 days if the program was ended with the "
               "school's authorization; none if status was lost).",
        "J-1": "8 CFR 214.2(j)(1)(ii): 30 days after the program ends, to prepare to leave.",
        "M-1": "8 CFR 214.2(m)(5): 30 days after the program ends, to prepare to leave.",
    }
    return "\n".join([
        f"The grace period ends on {last}",
        f"Rule: {rules[status]}",
        f"Assumption: day 1 is taken to be the end date ({ended}), so day {days} is {last}. If day 1 "
        f"is the day after, it runs one day longer, to {ended + timedelta(days=days)}; plan on the earlier date.",
        "The person can't work in the grace period.",
    ])


def max_stay_end(classification: str, start_date: str, days_outside_us: int = 0, as_of: str | None = None) -> str:
    years = MAX_STAY_YEARS[classification]
    started = parse_date(start_date, "start_date")
    today = parse_date(as_of, "as_of") if as_of else date.today()
    ends = add_years(started, years) - timedelta(days=1) + timedelta(days=days_outside_us)
    total = (add_years(started, years) - started).days
    used = max(0, min((today - started).days - days_outside_us, total))
    lines = [
        f"The {classification} maximum is reached on {ends}",
        f"That is {years} years from {started}, plus {days_outside_us} days outside the US added back.",
        f"As of {today}: {used} of {total} days used, {total - used} left.",
        f"Rule: 8 CFR 214.2(h)(13)(iii)(A) for H-1B (6 years) and 8 CFR 214.2(l)(12)(i) for L-1 "
        f"(L-1A 7 years, L-1B 5); time in H-1B and L-1 status counts toward the same limit, and whole "
        f"days outside the US can be recaptured.",
    ]
    if classification == "H-1B":
        lines.append(
            "Past 6 years, an H-1B can be extended under AC21 if a PERM or I-140 was filed at least "
            "365 days before (1-year extensions), or if an I-140 is approved and the priority date "
            "isn't current (3-year extensions)."
        )
    else:
        lines.append("After reaching the L-1 maximum, a person must spend a year outside the US before a new L-1.")
    return "\n".join(lines)


def naturalization_filing_date(
    green_card_date: str, path: str, days_outside_us: int | None = None, longest_trip_days: int | None = None
) -> str:
    resident = parse_date(green_card_date, "green_card_date")
    years = 5 if path == "five_year" else 3
    anniversary = add_years(resident, years)
    earliest = anniversary - timedelta(days=90)
    lines = [
        f"The earliest N-400 filing date is {earliest}",
        f"The {years}-year residence requirement is met on {anniversary}; USCIS accepts the form up "
        f"to 90 days before that, and denies one filed earlier.",
        "Rule: INA 316(a) and 8 CFR 334.2(b) (five years); INA 319(a) (three years, married to and "
        "living with a US citizen who has been a citizen for those three years).",
    ]
    if days_outside_us is not None:
        period = (anniversary - resident).days
        needed = -(-period // 2)
        present = period - days_outside_us
        verdict = "meets" if present >= needed else "does not meet"
        lines.append(
            f"Physical presence: {present} of the {period} days in the US, assuming no more time abroad "
            f"before {anniversary}, which {verdict} the {needed}-day requirement (half the period)."
        )
    if longest_trip_days is not None:
        if longest_trip_days >= 365:
            lines.append("A trip of a year or more breaks continuous residence: the clock restarts after it.")
        elif longest_trip_days > 180:
            lines.append("A trip of more than 6 months is presumed to break continuous residence unless the person can show it didn't.")
        else:
            lines.append("No trip over 6 months, so trips don't break continuous residence.")
    lines.append("Other requirements still apply: good moral character, 3 months in the state or USCIS "
                 "district before filing, and the English and civics test.")
    return "\n".join(lines)


def parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a date as YYYY-MM-DD, got {value!r}") from None


def add_years(day: date, years: int) -> date:
    """The same day `years` later; 29 February becomes 28 February in a year without one."""
    year = day.year + years
    return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))


def _tool(name, description, properties, required, fn) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        fn=fn,
        idempotent=True,
    )
