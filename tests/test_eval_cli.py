"""askvisa-eval end to end with scripted models: output, results.json, trace database, exit status."""

import json

import pytest

from evals import cli
from runtime.messages import ToolCall
from runtime.model import ModelReply
from tests.fake_model import FakeModel, call, say

SUITE = """
[suite]
name = "demo"
today = "2026-11-07"
tools = ["perm"]

[[case]]
id = "sponsors"
question = "Does Acme sponsor?"
must_call = ["match_employer"]
judge = ["Says yes"]
[case.fixtures.match_employer]
output = "Acme data"

[[case]]
id = "weather"
split = "held_out"
question = "Weather?"
no_tools = true
"""


@pytest.fixture
def suite(tmp_path):
    path = tmp_path / "demo.toml"
    path.write_text(SUITE)
    return path


def use_models(monkeypatch, agent_replies, judge_replies=()):
    models = {"agent": FakeModel(list(agent_replies)), "judge": FakeModel(list(judge_replies))}
    monkeypatch.setattr(cli, "make_model", lambda provider, model=None: models["judge" if provider == "deepseek" else "agent"])
    return models


def run(suite, tmp_path, *flags):
    return cli.main([str(suite), "--out", str(tmp_path / "results"), "--judge-provider", "deepseek", "--parallel", "1", *flags])


def test_a_passing_run_exits_0_and_writes_results_and_traces(suite, tmp_path, monkeypatch, capsys):
    use_models(monkeypatch, [ModelReply(tool_calls=(ToolCall("c1", "match_employer", {"name": "Acme"}),)), say("Yes."), say("I only answer immigration questions.")],
               [say('{"items": [{"pass": true, "reason": "ok"}]}')])
    assert run(suite, tmp_path) == 0

    out = capsys.readouterr().out
    assert "✓ sponsors #1" in out and "✓ weather #1" in out
    assert "2/2 attempts passed (100%)" in out
    run_dir = next((tmp_path / "results").iterdir())
    results = json.loads((run_dir / "results.json").read_text())
    assert (results["suite"], results["passed"], results["attempts"]) == ("demo", 2, 2)
    assert results["results"][0]["calls"][0]["name"] == "match_employer"
    assert (run_dir / "sessions.db").exists()


def test_a_failing_run_exits_1_and_shows_the_first_failure(suite, tmp_path, monkeypatch, capsys):
    use_models(monkeypatch, [say("Probably."), call("web_search", query="weather"), say("Sunny.")])
    assert run(suite, tmp_path, "--no-judge") == 1

    out = capsys.readouterr().out
    assert "✗ sponsors #1" in out and "calls match_employer: never called" in out
    assert "no_tools: called web_search" in out
    assert "0/2 attempts passed" in out


def test_split_and_case_select_what_runs(suite, tmp_path, monkeypatch, capsys):
    use_models(monkeypatch, [say("No."), say("No.")])
    assert run(suite, tmp_path, "--split", "held_out", "--no-judge", "--repeat", "2") == 0
    assert "1 cases × 2" in capsys.readouterr().out


def test_a_bad_suite_is_reported_not_raised(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[[case]]\nid = "x"\n')
    assert cli.main([str(bad), "--out", str(tmp_path)]) == 2
    assert "question is required" in capsys.readouterr().out


def test_a_missing_key_is_reported_with_who_needs_it(suite, tmp_path, monkeypatch, capsys):
    def no_key(provider, model=None):
        raise RuntimeError(f"{provider.upper()}_API_KEY is not set")

    monkeypatch.setattr(cli, "make_model", no_key)
    assert cli.main([str(suite), "--out", str(tmp_path)]) == 2
    assert "ANTHROPIC_API_KEY is not set: it is needed for the model" in capsys.readouterr().out


@pytest.mark.parametrize(
    "flags, expected",
    [
        ([], ("anthropic", "claude-sonnet-5-5")),
        (["--provider", "ollama"], ("ollama", "qwen3.5:9b")),
        (["--provider", "ollama", "--model", "qwen3.5:4b"], ("ollama", "qwen3.5:4b")),
        (["--provider", "ollama", "--judge-model", "qwen3.5:27b"], ("ollama", "qwen3.5:27b")),
        (["--provider", "ollama", "--judge-provider", "anthropic"], ("anthropic", "claude-sonnet-5-5")),
    ],
)
def test_the_judge_defaults_to_the_evaluated_model(flags, expected):
    args = cli.parse_args(["suite.toml", *flags])
    model_name = args.model or cli.DEFAULT_MODELS[args.provider]
    assert cli.judge_choice(args, model_name) == expected


class LocalModel(FakeModel):
    def __init__(self, replies, checked):
        super().__init__(replies=replies)
        self.checked = checked

    async def check(self):
        self.checked.append(True)


def test_a_separate_local_judge_is_checked_at_startup(suite, tmp_path, monkeypatch, capsys):
    checked_agent, checked_judge = [], []
    agent = LocalModel([say("No."), say("No.")], checked_agent)
    judge = LocalModel([], checked_judge)
    monkeypatch.setattr(cli, "make_model", lambda provider, model=None: judge if model == "qwen3.5:27b" else agent)
    code = cli.main([str(suite), "--out", str(tmp_path), "--provider", "ollama", "--judge-model", "qwen3.5:27b",
                     "--split", "held_out"])
    assert code == 0 and checked_agent == [True] and checked_judge == [True]
    assert "judge qwen3.5:27b (ollama)" in capsys.readouterr().out
