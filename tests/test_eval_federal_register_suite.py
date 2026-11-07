"""The Federal Register suite: it loads, names only real tools and parameters, and its fixtures use
the tools' real output format."""

from pathlib import Path

from evals.cases import load_suite
from evals.fixtures import real_tools

SUITE = load_suite(Path(__file__).resolve().parent.parent / "evals" / "suites" / "federal_register.toml")
TOOLS = {t.name: t for t in real_tools(SUITE.tools)}


def test_the_suite_has_dev_and_held_out_cases():
    splits = [c.split for c in SUITE.cases]
    assert splits.count("dev") >= 5 and splits.count("held_out") >= 3


def test_every_tool_and_argument_a_case_names_exists():
    for case in SUITE.cases:
        named = {*case.must_call, *case.must_not_call, *case.call_order, *case.args, *case.fixtures}
        assert named <= set(TOOLS), (case.id, named - set(TOOLS))
        for tool, expected in case.args.items():
            assert set(expected) <= set(TOOLS[tool].input_schema["properties"]), (case.id, tool)


def test_fixtures_use_the_real_output_format():
    for case in SUITE.cases:
        search = case.fixtures.get("search_federal_register")
        if search and search.output:
            first = search.output.split("\n")[0]
            assert first == "No Federal Register documents match." or first.endswith("match, newest first:"), case.id
            if not first.startswith("No"):
                assert " · document " in search.output.split("\n")[2], case.id
        read = case.fixtures.get("read_federal_register_document")
        if read and read.output:
            assert " · document " in read.output.split("\n")[0], case.id
