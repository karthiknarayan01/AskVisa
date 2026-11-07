"""The regulations suite: it loads, names only real tools and parameters, and its fixtures use
the regulation tools' real output format."""

from pathlib import Path

from evals.cases import load_suite
from evals.fixtures import real_tools

SUITE = load_suite(Path(__file__).resolve().parent.parent / "evals" / "suites" / "regulations.toml")
TOOLS = {t.name: t for t in real_tools(SUITE.tools)}


def test_the_suite_has_dev_and_held_out_cases():
    splits = [c.split for c in SUITE.cases]
    assert splits.count("dev") >= 6 and splits.count("held_out") >= 3


def test_every_tool_and_argument_a_case_names_exists():
    for case in SUITE.cases:
        named = {*case.must_call, *case.must_not_call, *case.call_order, *case.args, *case.fixtures}
        assert named <= set(TOOLS), (case.id, named - set(TOOLS))
        for tool, expected in case.args.items():
            assert set(expected) <= set(TOOLS[tool].input_schema["properties"]), (case.id, tool)


def test_read_fixtures_use_the_real_output_format():
    for case in SUITE.cases:
        fixture = case.fixtures.get("read_regulation")
        if fixture is None or fixture.output is None:
            continue
        heading, source, blank, *_ = fixture.output.split("\n")
        assert heading.startswith("§ "), case.id
        assert " CFR " in source and ", current through " in source, case.id
        assert blank == "", case.id


def test_search_fixture_uses_the_real_output_format():
    assert SUITE.defaults["search_regulations"].output.startswith("8 CFR, current through ")
