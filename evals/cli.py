"""agent-eval: run an eval suite against a model and report how it did.

    agent-eval evals/suites/permtracker.toml                       Anthropic, judged by Anthropic
    agent-eval evals/suites/permtracker.toml --provider ollama     local Qwen, judged by Anthropic
    agent-eval evals/suites/permtracker.toml --split held_out      only the held-out cases
    agent-eval evals/suites/permtracker.toml --case visa-bulletin-not-current --repeat 5
    agent-eval evals/suites/permtracker.toml --no-judge            only the deterministic checks

Each run writes a directory under evals/results/ (git-ignored) holding results.json and the
sessions database, so any attempt's full trace can be opened with
`agent-runtime --db <dir>/sessions.db --resume <session id>`.

The judge defaults to Anthropic's default model, a different model from the one usually being
graded, so an agent isn't grading itself; --judge-provider and --judge-model change it. The exit
status is 0 only when every attempt passed, so a suite can gate CI.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

from evals.cases import SuiteError, load_suite
from evals.report import first_failure, print_report, write_results
from evals.runner import run_suite
from runtime.migrate import migrate
from runtime.model import ModelError
from runtime.providers import DEFAULT_MODELS, LOCAL_PROVIDERS, PROVIDERS, make_model

RESULTS_DIR = Path("evals/results")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()
    try:
        return asyncio.run(run(args, console))
    except (SuiteError, ValueError, ModelError) as e:
        console.print(f"[red]✗[/] {e}")
        return 2


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent-eval", description="Run an eval suite against a model.")
    parser.add_argument("suite", type=Path, help="the suite file, e.g. evals/suites/permtracker.toml")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="anthropic", help="the model being evaluated")
    parser.add_argument("--model", help="model name (default: the provider's default)")
    parser.add_argument("--judge-provider", choices=sorted(PROVIDERS), default="anthropic")
    parser.add_argument("--judge-model", help="judge model name (default: the provider's default)")
    parser.add_argument("--no-judge", action="store_true", help="skip judge items; run only the checks")
    parser.add_argument("--split", choices=["dev", "held_out", "all"], default="all")
    parser.add_argument("--case", action="append", dest="cases", help="run only this case id (repeatable)")
    parser.add_argument("--repeat", type=int, default=1, help="attempts per case (default 1)")
    parser.add_argument("--parallel", type=int, default=2, help="attempts run at once (default 2)")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="where run directories go")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.parallel < 1:
        parser.error("--repeat and --parallel must be at least 1")
    return args


async def run(args: argparse.Namespace, console: Console) -> int:
    """Load, run, report; 0 when every attempt passed, 1 otherwise."""
    suite = load_suite(args.suite)
    cases = suite.select(None if args.split == "all" else args.split, args.cases)
    if not cases:
        raise SuiteError("no cases selected")
    model_name = args.model or DEFAULT_MODELS[args.provider]
    model = _make(args.provider, args.model, "the model")
    if args.provider in LOCAL_PROVIDERS:
        await model.check()
    judge = None if args.no_judge else _make(args.judge_provider, args.judge_model, "the judge (or pass --no-judge)")
    judge_name = "none" if judge is None else (args.judge_model or DEFAULT_MODELS[args.judge_provider])

    started = datetime.now()
    run_dir = args.out / f"{suite.name}-{_slug(model_name)}-{started:%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True)
    db_path = str(run_dir / "sessions.db")
    migrate(db_path)
    console.print(
        f"[bold]{suite.name}[/]: {len(cases)} cases × {args.repeat} on {model_name} ({args.provider}), "
        f"judge {judge_name}"
    )

    def progress(attempt) -> None:
        mark = "[green]✓[/]" if attempt.passed else "[red]✗[/]"
        detail = "" if attempt.passed else f"  [dim]{first_failure(attempt)}[/]"
        console.print(f"  {mark} {attempt.case_id} #{attempt.attempt}{detail}", highlight=False)

    attempts = await run_suite(suite, cases, model, judge, db_path, args.repeat, args.parallel, progress)
    console.print()
    print_report(console, attempts, f"{suite.name} · {model_name} ({args.provider}) · judge {judge_name}")
    write_results(run_dir / "results.json", attempts, {
        "suite": suite.name,
        "provider": args.provider,
        "model": model_name,
        "judge": judge_name,
        "split": args.split,
        "repeat": args.repeat,
        "started": started.isoformat(timespec="seconds"),
    })
    console.print(f"[dim]results and traces: {run_dir}[/]")
    return 0 if all(a.passed for a in attempts) else 1


def _make(provider: str, name: str | None, role: str):
    """make_model, with a missing key reported as a usage error for `role`."""
    try:
        return make_model(provider, name)
    except RuntimeError as e:
        raise ValueError(f"{e}: it is needed for {role}") from None


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", name).strip("-")


if __name__ == "__main__":
    sys.exit(main())
