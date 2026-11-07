"""The system prompt: scope, today's date, and a line for each group of tools that is present."""

from datetime import date

from runtime.prompts import system_prompt

TODAY = date(2026, 11, 6)
WEB = ["web_search", "read_web_page"]
LOOKUPS = ["match_employer", "find_soc_code"]
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
