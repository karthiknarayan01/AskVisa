"""Loading eval suites: the case format, its defaults, and the errors for malformed files."""

from datetime import date

import pytest

from evals.cases import Fixture, SuiteError, load_suite

SUITE = """
[suite]
name = "demo"
today = "2026-11-07"
tools = ["perm", "web"]

[defaults.web_search]
output = "No results."

[[case]]
id = "one"
question = "  Does Acme sponsor?  "
must_call = ["match_employer"]
must_not_call = ["perm_case_status"]
max_calls = 2
judge = ["Says yes"]
[case.args.match_employer]
name = "acme"
[case.fixtures.match_employer]
output = "data"

[[case]]
id = "two"
split = "held_out"
question = "Weather?"
no_tools = true
[case.fixtures.perm_queue]
error = "HTTP 429"
"""


def write(tmp_path, text):
    path = tmp_path / "suite.toml"
    path.write_text(text)
    return path


def test_a_suite_loads_with_its_cases_defaults_and_fixtures(tmp_path):
    suite = load_suite(write(tmp_path, SUITE))

    assert (suite.name, suite.today, suite.tools) == ("demo", date(2026, 11, 7), ("perm", "web"))
    assert suite.defaults == {"web_search": Fixture(output="No results.")}
    one, two = suite.cases
    assert one.question == "Does Acme sponsor?"
    assert one.must_call == ("match_employer",) and one.must_not_call == ("perm_case_status",)
    assert one.args == {"match_employer": {"name": "acme"}}
    assert one.fixtures == {"match_employer": Fixture(output="data")}
    assert (one.split, one.max_calls, one.judge) == ("dev", 2, ("Says yes",))
    assert two.no_tools and two.split == "held_out"
    assert two.fixtures == {"perm_queue": Fixture(error="HTTP 429")}


def test_cases_are_selected_by_split_and_id(tmp_path):
    suite = load_suite(write(tmp_path, SUITE))
    assert [c.id for c in suite.select()] == ["one", "two"]
    assert [c.id for c in suite.select("held_out")] == ["two"]
    assert [c.id for c in suite.select(None, ["two"])] == ["two"]
    with pytest.raises(ValueError, match="no case with id nope"):
        suite.select(None, ["nope"])


@pytest.mark.parametrize(
    "case, message",
    [
        ('id = "x"\nquestion = "q"\ntypo = 1', "unknown keys"),
        ('id = "x"', "question is required"),
        ('id = "x"\nquestion = "q"\nsplit = "test"', "split must be one of"),
        ('id = "x"\nquestion = "q"\nno_tools = true\nmust_call = ["a"]', "no_tools can't be combined"),
        ('id = "x"\nquestion = "q"\n[case.fixtures.t]\noutput = "a"\nerror = "b"', "exactly one of output or error"),
    ],
)
def test_malformed_cases_say_what_is_wrong(tmp_path, case, message):
    with pytest.raises(SuiteError, match=message):
        load_suite(write(tmp_path, f'[suite]\ntools = []\n[[case]]\n{case}\n'))


def test_duplicate_ids_and_unknown_tool_groups_are_rejected(tmp_path):
    twice = '[[case]]\nid = "x"\nquestion = "q"\n'
    with pytest.raises(SuiteError, match="used twice"):
        load_suite(write(tmp_path, twice + twice))
    with pytest.raises(SuiteError, match="unknown groups"):
        load_suite(write(tmp_path, '[suite]\ntools = ["files"]\n'))
