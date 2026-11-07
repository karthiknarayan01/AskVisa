"""eval.sh end to end, with a fake Ollama, a fake curl and an already-installed fake venv.

The fake agent-eval prints the arguments it was started with, one run per line, so each test
can check which suites ran with which options.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "eval.sh"

FAKE_OLLAMA = """#!/bin/sh
echo "$*" >> "$FAKE_DIR/ollama.log"
case "$1" in
    serve) touch "$FAKE_DIR/up" ;;
    show) grep -qx "$2" "$FAKE_DIR/pulled" 2>/dev/null ;;
    pull) echo "$2" >> "$FAKE_DIR/pulled" ;;
esac
"""
FAKE_CURL = '#!/bin/sh\n[ -f "$FAKE_DIR/up" ]\n'
FAKE_EVAL = '#!/bin/sh\necho "EVAL $*"\nexit "${FAKE_EVAL_EXIT:-0}"\n'


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    for path, text in [(bin_dir / "ollama", FAKE_OLLAMA), (bin_dir / "curl", FAKE_CURL),
                       (venv / "bin" / "agent-eval", FAKE_EVAL), (venv / "bin" / "agent-server", "#!/bin/sh\n")]:
        path.write_text(text)
        path.chmod(0o755)
    checksum = subprocess.run(["cksum"], stdin=open(ROOT / "pyproject.toml"), capture_output=True, text=True).stdout
    (venv / ".agent-runtime-installed").write_text(checksum)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "AGENT_RUNTIME_VENV": str(venv),
        "AGENT_RUNTIME_ENV_FILE": str(tmp_path / ".env"),
        "FAKE_DIR": str(fake),
    }


def run(env, *args, cwd=None):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60, cwd=cwd)


def eval_runs(result):
    return [line[len("EVAL "):] for line in result.stdout.splitlines() if line.startswith("EVAL ")]


def ollama_calls(env):
    log = Path(env["FAKE_DIR"]) / "ollama.log"
    return log.read_text().splitlines() if log.exists() else []


def test_with_no_arguments_every_suite_runs_on_local_qwen_after_setting_it_up(env):
    result = run(env)

    assert result.returncode == 0, result.stderr
    suites = sorted(str(p) for p in (ROOT / "evals" / "suites").glob("*.toml"))
    runs = eval_runs(result)
    assert len(runs) == len(suites)
    model = next(c for c in ollama_calls(env) if c.startswith("pull ")).split()[1]
    assert model.startswith("qwen3.5:")
    assert runs[0] == (f"{suites[0]} --provider ollama --model {model} --out {ROOT}/evals/results "
                       "--repeat 3 --parallel 1")
    assert "serve" in ollama_calls(env)


def test_a_model_already_downloaded_is_not_pulled_and_a_running_ollama_not_started(env):
    (Path(env["FAKE_DIR"]) / "up").touch()
    (Path(env["FAKE_DIR"]) / "pulled").write_text("qwen3.5:9b\n")
    run(env, "--model", "qwen3.5:9b")
    calls = ollama_calls(env)
    assert "serve" not in calls and not [c for c in calls if c.startswith("pull ")]


def test_a_suite_path_and_extra_options_are_passed_through(env, tmp_path):
    suite = ROOT / "evals" / "suites" / "permtracker.toml"
    result = run(env, "evals/suites/permtracker.toml", "--case", "out-of-scope", "--repeat", "1",
                 "--no-judge", "--model", "qwen3.5:4b", cwd=ROOT)

    assert eval_runs(result) == [
        f"{suite} --provider ollama --model qwen3.5:4b --out {ROOT}/evals/results --case out-of-scope "
        "--repeat 1 --no-judge --parallel 1"
    ]


def test_a_separate_local_judge_model_is_downloaded_too(env):
    run(env, "--model", "qwen3.5:9b", "--judge-model", "qwen3.5:27b")
    pulls = sorted(c for c in ollama_calls(env) if c.startswith("pull "))
    assert pulls == ["pull qwen3.5:27b", "pull qwen3.5:9b"]


def test_without_a_judge_only_the_model_is_downloaded(env):
    run(env, "--model", "qwen3.5:9b", "--judge-model", "qwen3.5:27b", "--no-judge")
    assert [c for c in ollama_calls(env) if c.startswith("pull ")] == ["pull qwen3.5:9b"]


def test_a_hosted_model_needs_no_ollama(env):
    result = run(env, "--provider", "anthropic")
    assert ollama_calls(env) == []
    run_args = eval_runs(result)[0]
    assert "--provider anthropic" in run_args and "--model" not in run_args and "--parallel" not in run_args


def test_a_hosted_model_with_a_local_judge_downloads_the_judge(env):
    result = run(env, "--provider", "anthropic", "--judge-provider", "ollama")
    pulls = [c for c in ollama_calls(env) if c.startswith("pull ")]
    assert len(pulls) == 1 and pulls[0].startswith("pull qwen3.5:")
    assert f"--judge-model {pulls[0].split()[1]}" in eval_runs(result)[0]


def test_a_failing_suite_makes_the_script_fail(env):
    result = run({**env, "FAKE_EVAL_EXIT": "1"})
    assert result.returncode == 1
    assert eval_runs(result)


def test_service_keys_from_the_env_file_reach_the_evals(env):
    Path(env["AGENT_RUNTIME_ENV_FILE"]).write_text("PERMTRACKER_API_KEY=pt_live_abc\n")
    eval_bin = Path(env["AGENT_RUNTIME_VENV"]) / "bin" / "agent-eval"
    eval_bin.write_text('#!/bin/sh\necho "KEY ${PERMTRACKER_API_KEY:-none}"\n')
    assert "KEY pt_live_abc" in run(env, "--provider", "anthropic").stdout
