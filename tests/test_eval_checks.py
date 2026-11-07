"""The deterministic checks, each passing and failing with a reason."""

from evals.cases import Case
from evals.checks import ToolCallRecord, run_checks


def calls(*specs):
    return [ToolCallRecord(name, args, "out", False) for name, args in specs]


def results(case, trace):
    return {r.name: (r.passed, r.detail) for r in run_checks(case, trace)}


def test_must_call_and_must_not_call():
    case = Case("c", "q", must_call=("match_employer",), must_not_call=("perm_case_status",))
    assert results(case, calls(("match_employer", {}))) == {
        "calls match_employer": (True, "called"),
        "doesn't call perm_case_status": (True, "not called"),
    }
    assert results(case, calls(("perm_case_status", {}), ("perm_case_status", {}))) == {
        "calls match_employer": (False, "never called"),
        "doesn't call perm_case_status": (False, "called 2x"),
    }


def test_no_tools():
    case = Case("c", "q", no_tools=True)
    assert results(case, []) == {"no_tools": (True, "no tool calls")}
    assert results(case, calls(("web_search", {}))) == {"no_tools": (False, "called web_search")}


def test_call_order_uses_each_tools_first_call():
    case = Case("c", "q", call_order=("perm_case_status", "perm_decision_estimate"))
    label = "order perm_case_status → perm_decision_estimate"
    ok = calls(("perm_case_status", {}), ("perm_decision_estimate", {}), ("perm_case_status", {}))
    assert results(case, ok)[label] == (True, "in order")
    wrong = calls(("perm_decision_estimate", {}), ("perm_case_status", {}))
    assert results(case, wrong)[label] == (False, "called in the order perm_decision_estimate → perm_case_status")
    assert results(case, calls(("perm_case_status", {})))[label] == (False, "never called perm_decision_estimate")


def test_args_match_when_any_call_contains_each_value_ignoring_case():
    case = Case("c", "q", args={"match_employer": {"name": "infosys"}})
    label = "match_employer args name~'infosys'"
    trace = calls(("match_employer", {"name": "Google"}), ("match_employer", {"name": "Infosys Ltd"}))
    assert results(case, trace)[label][0]
    passed, detail = results(case, calls(("match_employer", {"name": "Google"})))[label]
    assert not passed and detail == 'got {"name": "Google"}'
    assert results(case, [])[label] == (False, "never called")


def test_args_compare_non_string_values_as_json():
    case = Case("c", "q", args={"perm_case_status": {"live": "true"}})
    assert results(case, calls(("perm_case_status", {"live": True})))["perm_case_status args live~'true'"][0]


def test_max_calls():
    case = Case("c", "q", max_calls=1)
    assert results(case, calls(("a", {}))) == {"at most 1 calls": (True, "1 calls")}
    assert results(case, calls(("a", {}), ("b", {}))) == {"at most 1 calls": (False, "2 calls")}
