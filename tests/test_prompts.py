"""The CLI's system prompt: today's date, and the web guidance only when the web tools are on."""

from datetime import date

from runtime.prompts import system_prompt


def test_prompt_states_todays_date():
    assert "Today's date is Friday, November 06, 2026." in system_prompt(date(2026, 11, 6), web=False)


def test_web_guidance_only_with_web_tools():
    assert "web_search" in system_prompt(date(2026, 11, 6), web=True)
    assert "web_search" not in system_prompt(date(2026, 11, 6), web=False)


def test_prompt_ends_with_how_to_finish():
    assert system_prompt(date(2026, 11, 6), web=True).endswith("reply without calling a tool.")


def test_perm_guidance_asks_for_credit_and_marks_estimates():
    prompt = system_prompt(date(2026, 11, 6), web=True, perm=True)
    assert "PERM Tracker (permtracker.app)" in prompt
    assert "never a DOL determination" in prompt
    assert "PERM Tracker" not in system_prompt(date(2026, 11, 6), web=True)
