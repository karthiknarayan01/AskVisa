"""start.sh end to end, with a fake Ollama, a fake curl and an already-installed fake venv.

The fake askvisa-server prints the arguments and API key it was started with, so each test can
check what the script launched.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "start.sh"

FAKE_OLLAMA = """#!/bin/sh
echo "$*" >> "$FAKE_DIR/ollama.log"
case "$1" in
    serve) touch "$FAKE_DIR/up" ;;
    show) [ -f "$FAKE_DIR/pulled" ] ;;
    pull) touch "$FAKE_DIR/pulled" ;;
esac
"""
# The Ollama API answers once `ollama serve` ran; AskVisa's own health URL only when a test says
# an earlier server is still running.
FAKE_CURL = """#!/bin/sh
case "$*" in
    *"/api/health"*) [ -f "$FAKE_DIR/serving" ] ;;
    *) [ -f "$FAKE_DIR/up" ] ;;
esac
"""
FAKE_NPM = """#!/bin/sh
echo "$*" >> "$FAKE_DIR/npm.log"
if [ "$1 $2" = "run build" ]; then mkdir -p dist && echo app > dist/index.html; fi
"""
FAKE_CLI = '#!/bin/sh\necho "ARGS $*"\necho "ANTHROPIC ${ANTHROPIC_API_KEY:-none}"\n'


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    _executable(bin_dir / "ollama", FAKE_OLLAMA)
    _executable(bin_dir / "curl", FAKE_CURL)
    _executable(bin_dir / "npm", FAKE_NPM)
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "package-lock.json").write_text("{}")
    (web / "src" / "main.tsx").write_text("one")
    _executable(venv / "bin" / "askvisa-server", FAKE_CLI)
    checksum = subprocess.run(
        ["cksum"], stdin=open(ROOT / "pyproject.toml"), capture_output=True, text=True, check=True
    ).stdout
    (venv / ".askvisa-installed").write_text(checksum)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ASKVISA_VENV": str(venv),
        "FAKE_DIR": str(fake),
        "ASKVISA_WEB_DIR": str(web),
        "ASKVISA_NO_BROWSER": "1",
        "ASKVISA_ENV_FILE": str(tmp_path / ".env"),
    }


def run(env, stdin="", *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], input=stdin, env=env, capture_output=True, text=True, timeout=60
    )


def config(env) -> Path:
    return Path(env["XDG_CONFIG_HOME"]) / "askvisa" / "config"


def ollama_calls(env) -> list[str]:
    log = Path(env["FAKE_DIR"]) / "ollama.log"
    return log.read_text().splitlines() if log.exists() else []


def test_first_run_with_qwen_starts_ollama_downloads_the_model_and_opens_the_chat(env):
    result = run(env, "1\n")

    assert result.returncode == 0, result.stderr
    calls = ollama_calls(env)
    assert "serve" in calls
    pulls = [c for c in calls if c.startswith("pull ")]
    assert len(pulls) == 1 and pulls[0].startswith("pull qwen3.5:")
    model = pulls[0].split()[1]
    assert f"ARGS --provider ollama --model {model}" in result.stdout
    assert f"MODEL={model}" in config(env).read_text()
    assert stat.S_IMODE(config(env).stat().st_mode) == 0o600


def test_a_server_already_running_on_the_port_is_named_instead_of_opened(env):
    run(env, "1\n")
    (Path(env["FAKE_DIR"]) / "serving").touch()

    result = run(env, "", "--port", "9100")

    assert result.returncode == 1
    assert "AskVisa is already running on port 9100" in result.stderr
    assert "pkill -f askvisa-server" in result.stderr
    assert "ARGS" not in result.stdout


def test_later_runs_skip_the_choice_and_the_download(env):
    run(env, "1\n")
    before = len(ollama_calls(env))

    result = run(env, "", "--resume", "latest")

    assert result.returncode == 0, result.stderr
    assert "Using qwen3.5:" in result.stdout
    assert not [c for c in ollama_calls(env)[before:] if c.startswith("pull ")]
    assert "--resume latest" in result.stdout


def test_ollama_already_running_is_not_started_again(env):
    (Path(env["FAKE_DIR"]) / "up").touch()
    run(env, "1\n")
    assert "serve" not in ollama_calls(env)


def test_anthropic_key_is_asked_for_saved_and_passed_to_the_chat(env):
    result = run(env, "2\nsk-test\n")

    assert result.returncode == 0, result.stderr
    assert "ARGS --provider anthropic --model claude-sonnet-5-5" in result.stdout
    assert "ANTHROPIC sk-test" in result.stdout
    assert "ANTHROPIC_API_KEY=sk-test" in config(env).read_text()
    assert ollama_calls(env) == []

    again = run(env)
    assert "ANTHROPIC sk-test" in again.stdout


def test_a_key_already_in_the_environment_is_used_and_not_saved(env):
    result = run({**env, "ANTHROPIC_API_KEY": "sk-env"}, "2\n")

    assert "ANTHROPIC sk-env" in result.stdout
    assert "sk-env" not in config(env).read_text()


def test_service_keys_in_the_env_file_reach_the_chat_without_a_prompt(env):
    Path(env["ASKVISA_ENV_FILE"]).write_text('PERMTRACKER_API_KEY="pt_live_abc"\nOTHER=ignored\n')
    _executable(Path(env["ASKVISA_VENV"]) / "bin" / "askvisa-server",
                '#!/bin/sh\necho "PERM ${PERMTRACKER_API_KEY:-none} OTHER ${OTHER:-none}"\n')
    result = run(env, "1\n")

    assert "PERM pt_live_abc OTHER none" in result.stdout
    assert "PERMTRACKER" not in config(env).read_text()
    assert "PERM Tracker" not in result.stdout


def test_a_key_already_in_the_environment_beats_the_env_file(env):
    Path(env["ASKVISA_ENV_FILE"]).write_text("PERMTRACKER_API_KEY=from_file\n")
    _executable(Path(env["ASKVISA_VENV"]) / "bin" / "askvisa-server",
                '#!/bin/sh\necho "PERM ${PERMTRACKER_API_KEY:-none}"\n')
    result = run({**env, "PERMTRACKER_API_KEY": "from_env"}, "1\n")
    assert "PERM from_env" in result.stdout


def test_setup_flag_asks_again(env):
    run(env, "1\n")
    result = run(env, "3\nds-key\n", "--setup")

    assert "ARGS --provider deepseek --model deepseek-v4-pro" in result.stdout
    assert "PROVIDER=deepseek" in config(env).read_text()


def test_no_answer_and_no_config_fails_with_a_hint(env):
    result = run(env)

    assert result.returncode != 0
    assert "No model is set up yet" in result.stderr


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def npm_calls(env):
    log = Path(env["FAKE_DIR"]) / "npm.log"
    return log.read_text().splitlines() if log.exists() else []


def test_the_web_app_is_built_once_and_again_when_its_sources_change(env):
    run(env, "2\nsk-test\n")
    assert npm_calls(env) == ["ci --no-audit --no-fund", "run build"]
    assert (Path(env["ASKVISA_WEB_DIR"]) / "dist" / "index.html").exists()

    run(env)
    assert len(npm_calls(env)) == 2

    (Path(env["ASKVISA_WEB_DIR"]) / "src" / "main.tsx").write_text("two")
    run(env)
    assert len(npm_calls(env)) == 4
