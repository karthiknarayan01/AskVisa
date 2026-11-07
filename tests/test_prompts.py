"""The system prompt: scope, today's date, and a line for each group of tools that is present."""

from datetime import date

from runtime.prompts import system_prompt

TODAY = date(2026, 11, 6)
WEB = ["web_search", "read_web_page"]
LOOKUPS = ["match_employer", "find_soc_code", "immigration_data_page"]
RECORDS = ["perm_case_status", "perm_decision_estimate", "perm_queue", "visa_bulletin"]


def test_prompt_states_todays_date():
    assert "Today's date is Friday, November 06, 2026." in system_prompt(TODAY, [])


def test_each_group_is_described_only_when_its_tools_are_present():
    assert "web_search" in system_prompt(TODAY, WEB)
    assert "web_search" not in system_prompt(TODAY, LOOKUPS)
    assert "match_employer" in system_prompt(TODAY, LOOKUPS)
    assert "perm_case_status" not in system_prompt(TODAY, LOOKUPS + WEB)
    assert "perm_case_status" in system_prompt(TODAY, LOOKUPS + RECORDS)


def test_prompt_ends_with_how_to_finish():
    assert system_prompt(TODAY, WEB).endswith("reply without calling a tool.")


def test_prompt_never_names_the_data_provider():
    prompt = system_prompt(TODAY, LOOKUPS + RECORDS + WEB)
    assert "PERM Tracker" not in prompt and "permtracker" not in prompt


def test_prompt_keeps_to_us_immigration_and_declines_opinions():
    prompt = system_prompt(TODAY, WEB)
    assert "Answer only questions about US immigration" in prompt
    assert "politely decline" in prompt and "good or bad" in prompt


def test_web_is_the_fallback_after_the_specialized_tools():
    prompt = system_prompt(TODAY, LOOKUPS + RECORDS + WEB)
    assert "cover everything the other tools don't" in prompt
    assert prompt.index("match_employer") < prompt.index("perm_case_status") < prompt.index("web_search")


def test_the_dol_tools_are_one_section_of_the_prompt():
    prompt = system_prompt(TODAY, LOOKUPS + RECORDS + WEB)
    section = next(p for p in prompt.split("\n\n") if "match_employer" in p)
    assert "perm_case_status" in section and "visa_bulletin" in section


def test_the_order_is_record_tools_then_data_pages_then_the_web():
    prompt = system_prompt(TODAY, LOOKUPS + RECORDS + WEB)
    assert prompt.index("perm_case_status") < prompt.index("- published statistics") < prompt.index("web_search")
    assert "use it only when they don't cover the question, and use it before the web" in prompt


def test_each_purpose_is_its_own_bullet_and_only_for_tools_present():
    keyless = system_prompt(TODAY, LOOKUPS)
    assert "\n- whether and how often an employer sponsors" in keyless
    assert "\n- which SOC occupation code fits a job" in keyless
    assert "perm_case_status" not in keyless and "visa_bulletin" not in keyless
    keyed = system_prompt(TODAY, LOOKUPS + RECORDS)
    assert "\n- the status and details of a DOL case, by its case number: perm_case_status" in keyed


def test_web_section_lists_when_to_use_it_as_bullets():
    prompt = system_prompt(TODAY, WEB)
    assert "\n- when no other tool covers the question\n- when a tool returned nothing useful" in prompt


CALCULATORS = ["opt_unemployment_days", "grace_period_end", "max_stay_end", "naturalization_filing_date", "date_math"]


def test_calculators_have_their_own_section_before_the_web():
    prompt = system_prompt(TODAY, LOOKUPS + CALCULATORS + WEB)
    section = next(p for p in prompt.split("\n\n") if p.startswith("Date calculators"))
    assert all(f": {name}" in section for name in CALCULATORS)
    assert "Never work out a date or a count of days yourself" in prompt
    assert prompt.index("match_employer") < prompt.index("Date calculators") < prompt.index("web_search")
    assert "Date calculators" not in system_prompt(TODAY, LOOKUPS + WEB)


def test_regulations_have_their_own_section_before_the_calculators():
    prompt = system_prompt(TODAY, LOOKUPS + ["search_regulations", "read_regulation"] + CALCULATORS + WEB)
    section = next(p for p in prompt.split("\n\n") if p.startswith("The federal immigration regulations"))
    assert "search_regulations" in section and "read_regulation" in section
    assert "quote the regulation and cite it" in prompt
    assert prompt.index("match_employer") < prompt.index("search_regulations") < prompt.index("Date calculators")
    assert "read_regulation" not in system_prompt(TODAY, LOOKUPS + WEB)
