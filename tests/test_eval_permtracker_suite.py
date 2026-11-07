"""The PERM suite itself: it loads, and every case refers to tools and topics that exist."""

import json
from pathlib import Path

from evals.cases import load_suite
from evals.fixtures import real_tools
from runtime.permtracker_tools import MATCH_NOTES, PAGES

SUITE = load_suite(Path(__file__).resolve().parent.parent / "evals" / "suites" / "permtracker.toml")
TOOLS = {t.name: t for t in real_tools(SUITE.tools)}


def test_the_suite_has_dev_and_held_out_cases():
    splits = [c.split for c in SUITE.cases]
    assert splits.count("dev") >= 10 and splits.count("held_out") >= 5


def test_every_tool_a_case_names_exists():
    for case in SUITE.cases:
        named = {*case.must_call, *case.must_not_call, *case.call_order, *case.args, *case.fixtures}
        assert named <= set(TOOLS), (case.id, named - set(TOOLS))
    assert set(SUITE.defaults) <= set(TOOLS)


def test_expected_arguments_are_parameters_of_their_tool():
    for case in SUITE.cases:
        for tool, expected in case.args.items():
            assert set(expected) <= set(TOOLS[tool].input_schema["properties"]), (case.id, tool)


def test_data_page_topics_exist():
    for case in SUITE.cases:
        topic = case.args.get("immigration_data_page", {}).get("topic")
        assert topic is None or topic in PAGES, case.id


def test_fixtures_use_the_tools_real_output_format():
    for case in SUITE.cases:
        for tool, fixture in case.fixtures.items():
            if fixture.output is None or tool == "immigration_data_page":
                continue
            lines = fixture.output.split("\n")
            assert lines[0].startswith("Department of Labor data"), (case.id, tool)
            json.loads(lines[-1])
            if tool == "match_employer":
                match = json.loads(lines[-1])["match"]
                assert lines[1] == MATCH_NOTES[match], case.id
