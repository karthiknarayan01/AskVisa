"""askvisa-server: its options, and what it does before serving."""

import asyncio

from runtime import server
from runtime.model import ModelError


def test_defaults():
    args = server.parse_args([])
    assert (args.provider, args.host, args.port, args.db, args.allow_origin) == ("anthropic", "127.0.0.1", 8000, "agent.db", [])


def test_a_missing_key_exits_with_the_reason(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def no_key(*args, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(server, "build_agent", no_key)
    assert server.main([]) == 1
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_it_migrates_the_database_and_serves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    served = {}

    async def fake_serve(self):
        served.update(host=self.config.host, port=self.config.port)

    monkeypatch.setattr(server.uvicorn.Server, "serve", fake_serve)
    db = tmp_path / "s.db"
    assert server.main(["--provider", "deepseek", "--model", "x", "--db", str(db), "--port", "9001",
                        "--allow-origin", "http://localhost:5173"]) == 0
    assert db.exists()
    assert served["port"] == 9001 and served["host"] == "127.0.0.1"
    assert "Open http://127.0.0.1:9001 to use AskVisa (x, deepseek)." in capsys.readouterr().out


def test_the_model_check_and_the_turns_share_one_event_loop(tmp_path, monkeypatch):
    """The model client pools connections per loop; a check on a loop that then closes broke every turn."""
    loops = {}

    class Agent:
        provider, model_name = "ollama", "qwen"

        async def check(self):
            loops["check"] = asyncio.get_running_loop()

    async def fake_serve(self):
        loops["serve"] = asyncio.get_running_loop()

    monkeypatch.setattr(server, "build_agent", lambda *a, **k: Agent())
    monkeypatch.setattr(server, "create_app", lambda *a, **k: object())
    monkeypatch.setattr(server.uvicorn.Server, "serve", fake_serve)
    assert server.main(["--db", str(tmp_path / "s.db")]) == 0
    assert loops["check"] is loops["serve"]


def test_a_model_that_is_not_ready_exits_with_what_to_do(tmp_path, monkeypatch, capsys):
    class Agent:
        async def check(self):
            raise ModelError("Ollama isn't running. Start it with: ollama serve")

    monkeypatch.setattr(server, "build_agent", lambda *a, **k: Agent())
    db = tmp_path / "s.db"
    assert server.main(["--db", str(db)]) == 1
    assert "ollama serve" in capsys.readouterr().err
    assert not db.exists()
