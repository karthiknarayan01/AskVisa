"""Eval suites: a TOML file of cases, each a question with the checks its answer must pass.

    [suite]
    name = "permtracker"
    today = "2026-11-07"                  # the date the system prompt states, so fixtures stay true
    tools = ["perm", "perm_keyed", "web"] # perm, perm_keyed, regulations, federal_register,
                                          # calculators, web

    [defaults.web_search]                 # what a tool returns when a case doesn't say
    output = "..."

    [[case]]
    id = "employer-sponsors"
    split = "dev"                         # "dev" to tune against, "held_out" to check on
    question = "Does Infosys sponsor green cards?"
    must_call = ["match_employer"]        # each is called at least once
    must_not_call = ["perm_case_status"]  # none of these is called
    call_order = ["a", "b"]               # first calls happen in this order
    no_tools = true                       # no tool is called at all
    max_calls = 4                         # at most this many tool calls in the turn
    judge = ["Says ...", "Does not ..."]  # graded by a model, each pass or fail

    [case.args.match_employer]            # some call to the tool has these arguments: each
    name = "infosys"                      # value must appear in the actual one, ignoring case

    [case.fixtures.match_employer]        # what the tool returns in this case:
    output = "..."                        # this text, or
    error = "HTTP 429 rate_limited"       # this error

Tools return fixtures instead of calling services, so a run tests the model's decisions on
fixed data: the same case gives every model the same tool results.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

TOOL_GROUPS = {"perm", "perm_keyed", "regulations", "federal_register", "calculators", "web"}
SPLITS = {"dev", "held_out"}
CASE_KEYS = {
    "id", "split", "question", "must_call", "must_not_call", "call_order", "no_tools",
    "max_calls", "args", "judge", "fixtures",
}


@dataclass(frozen=True)
class Fixture:
    """A tool's canned result: its output text, or an error message."""

    output: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    split: str = "dev"
    must_call: tuple[str, ...] = ()
    must_not_call: tuple[str, ...] = ()
    call_order: tuple[str, ...] = ()
    no_tools: bool = False
    max_calls: int | None = None
    args: dict[str, dict[str, str]] = field(default_factory=dict)
    judge: tuple[str, ...] = ()
    fixtures: dict[str, Fixture] = field(default_factory=dict)


@dataclass(frozen=True)
class Suite:
    name: str
    today: date
    tools: tuple[str, ...]
    defaults: dict[str, Fixture]
    cases: tuple[Case, ...]

    def select(self, split: str | None = None, ids: list[str] | None = None) -> list[Case]:
        """The cases in `split` (all splits if None), limited to `ids` when given."""
        cases = [c for c in self.cases if split is None or c.split == split]
        if ids:
            unknown = set(ids) - {c.id for c in self.cases}
            if unknown:
                raise ValueError(f"no case with id {', '.join(sorted(unknown))}")
            cases = [c for c in cases if c.id in ids]
        return cases


class SuiteError(ValueError):
    """The suite file is malformed; the message says where."""


def load_suite(path: Path) -> Suite:
    """Read and check a suite file."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    meta = raw.get("suite", {})
    tools = tuple(meta.get("tools", []))
    if not set(tools) <= TOOL_GROUPS:
        raise SuiteError(f"suite.tools: unknown groups {sorted(set(tools) - TOOL_GROUPS)}")
    cases = tuple(_case(c, i) for i, c in enumerate(raw.get("case", [])))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise SuiteError(f"case id {case.id!r} is used twice")
        seen.add(case.id)
    return Suite(
        name=meta.get("name", path.stem),
        today=date.fromisoformat(str(meta.get("today", date.today().isoformat()))),
        tools=tools,
        defaults={name: _fixture(f, f"defaults.{name}") for name, f in raw.get("defaults", {}).items()},
        cases=cases,
    )


def _case(raw: dict[str, Any], index: int) -> Case:
    where = f"case {raw.get('id', index)!r}"
    unknown = set(raw) - CASE_KEYS
    if unknown:
        raise SuiteError(f"{where}: unknown keys {sorted(unknown)}")
    for key in ("id", "question"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise SuiteError(f"{where}: {key} is required")
    split = raw.get("split", "dev")
    if split not in SPLITS:
        raise SuiteError(f"{where}: split must be one of {sorted(SPLITS)}")
    if raw.get("no_tools") and (raw.get("must_call") or raw.get("call_order") or raw.get("args")):
        raise SuiteError(f"{where}: no_tools can't be combined with must_call, call_order or args")
    return Case(
        id=raw["id"],
        question=raw["question"].strip(),
        split=split,
        must_call=tuple(raw.get("must_call", [])),
        must_not_call=tuple(raw.get("must_not_call", [])),
        call_order=tuple(raw.get("call_order", [])),
        no_tools=bool(raw.get("no_tools", False)),
        max_calls=raw.get("max_calls"),
        args={tool: {k: str(v) for k, v in values.items()} for tool, values in raw.get("args", {}).items()},
        judge=tuple(raw.get("judge", [])),
        fixtures={name: _fixture(f, f"{where} fixtures.{name}") for name, f in raw.get("fixtures", {}).items()},
    )


def _fixture(raw: dict[str, Any], where: str) -> Fixture:
    if ("output" in raw) == ("error" in raw):
        raise SuiteError(f"{where}: give exactly one of output or error")
    return Fixture(output=raw.get("output"), error=raw.get("error"))
