"""Running cases end to end with a scripted model: fixtures, trace, checks, judge, usage, errors."""

from datetime import date

from evals.cases import Case, Fixture, Suite
from evals.fixtures import NO_FIXTURE, fixture_registry
from evals.runner import run_case, run_suite
from runtime.events import EventLog
from runtime.messages import ToolCall
from runtime.migrate import migrate
from runtime.model import ModelError, ModelReply, Usage
from tests.fake_model import FakeModel, call, say

SUITE = Suite(
    name="demo",
    today=date(2026, 11, 7),
    tools=("perm", "perm_keyed", "web"),
    defaults={"web_search": Fixture(output="No results.")},
    cases=(),
)
PASS_ALL = '{"items": [{"pass": true, "reason": "fine"}]}'


def log_at(tmp_path):
    db = str(tmp_path / "evals.db")
    migrate(db)
    return EventLog(db)


async def test_fixtures_answer_in_place_of_the_real_tools():
    case = Case("c", "q", fixtures={"match_employer": Fixture(output="Acme data"), "perm_queue": Fixture(error="HTTP 429")})
    tools = fixture_registry(SUITE, case)

    assert "perm_case_status" in {t.name for t in tools.specs()}
    assert (await tools.run(ToolCall("1", "match_employer", {"name": "Acme"}))).output == "Acme data"
    assert (await tools.run(ToolCall("2", "web_search", {"query": "x"}))).output == "No results."
    assert (await tools.run(ToolCall("3", "find_soc_code", {"query": "x"}))).output == NO_FIXTURE
    failed = await tools.run(ToolCall("4", "perm_queue", {}))
    assert failed.is_error and failed.output == "perm_queue failed: DataLookupError: HTTP 429"


async def test_a_passing_case_records_calls_checks_verdicts_and_usage(tmp_path):
    case = Case(
        "sponsors", "Does Acme sponsor?",
        must_call=("match_employer",), args={"match_employer": {"name": "acme"}}, judge=("Says yes",),
        fixtures={"match_employer": Fixture(output="Acme: 38 cases")},
    )
    model = FakeModel([
        ModelReply(tool_calls=(ToolCall("c1", "match_employer", {"name": "Acme"}),), usage=Usage(900, 20)),
        ModelReply(text="Yes, 38 cases.", usage=Usage(1000, 30)),
    ])
    judge = FakeModel([say(PASS_ALL)])
    with log_at(tmp_path) as log:
        attempt = await run_case(SUITE, case, model, judge, log)

    assert attempt.passed, attempt
    assert attempt.answer == "Yes, 38 cases."
    assert [(c.name, c.arguments, c.output) for c in attempt.calls] == [("match_employer", {"name": "Acme"}, "Acme: 38 cases")]
    assert all(c.passed for c in attempt.checks) and [v.passed for v in attempt.verdicts] == [True]
    assert (attempt.input_tokens, attempt.output_tokens) == (1900, 50)
    assert "Today's date is Saturday, November 07, 2026." in model.requests[0].system
    assert "Acme: 38 cases" in judge.requests[0].messages[0].text


async def test_a_failed_check_fails_the_attempt(tmp_path):
    case = Case("c", "q", must_not_call=("perm_case_status",))
    model = FakeModel([call("perm_case_status", case_number="IOE0912345678"), say("Approved!")])
    with log_at(tmp_path) as log:
        attempt = await run_case(SUITE, case, model, None, log)
    assert not attempt.passed
    assert [(c.name, c.passed) for c in attempt.checks] == [("doesn't call perm_case_status", False)]


async def test_a_failed_verdict_fails_the_attempt(tmp_path):
    case = Case("c", "q", judge=("Says yes",))
    judge = FakeModel([say('{"items": [{"pass": false, "reason": "said no"}]}')])
    with log_at(tmp_path) as log:
        attempt = await run_case(SUITE, case, FakeModel([say("No.")]), judge, log)
    assert not attempt.passed and attempt.verdicts[0].reason == "said no"


async def test_without_a_judge_only_the_checks_count(tmp_path):
    case = Case("c", "q", no_tools=True, judge=("Says yes",))
    with log_at(tmp_path) as log:
        attempt = await run_case(SUITE, case, FakeModel([say("hi")]), None, log)
    assert attempt.passed and attempt.verdicts == []


async def test_a_model_error_is_a_failed_attempt_not_a_crash(tmp_path):
    class Broken:
        async def complete(self, *args, **kwargs):
            raise ModelError("overloaded")

    with log_at(tmp_path) as log:
        attempt = await run_case(SUITE, Case("c", "q", judge=("x",)), Broken(), FakeModel([]), log)
    assert not attempt.passed and attempt.error == "ModelError: overloaded"
    assert attempt.verdicts == []


async def test_a_judge_error_fails_the_items(tmp_path):
    case = Case("c", "q", judge=("Says yes",))
    with log_at(tmp_path) as log:
        attempt = await run_case(SUITE, case, FakeModel([say("yes")]), FakeModel([]), log)
    assert not attempt.passed and attempt.verdicts[0].reason.startswith("judge failed:")


async def test_suite_runs_each_case_repeat_times_in_case_order(tmp_path):
    db = str(tmp_path / "runs.db")
    migrate(db)
    cases = [Case("a", "q1", no_tools=True), Case("b", "q2", no_tools=True)]
    model = FakeModel([say("1"), say("2"), say("3"), say("4")])
    seen = []
    attempts = await run_suite(SUITE, cases, model, None, db, repeat=2, parallel=3, on_attempt=seen.append)

    assert [(a.case_id, a.attempt) for a in attempts] == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]
    assert len(seen) == 4 and len({a.session_id for a in attempts}) == 4
    with EventLog(db) as log:
        assert all(log.has_session(a.session_id) for a in attempts)
