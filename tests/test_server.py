"""agent-server: its options, and what it does before serving."""

from runtime import server


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
    monkeypatch.setattr(server.uvicorn, "run", lambda app, host, port, log_level: served.update(app=app, host=host, port=port))
    db = tmp_path / "s.db"
    assert server.main(["--provider", "deepseek", "--model", "x", "--db", str(db), "--port", "9001",
                        "--allow-origin", "http://localhost:5173"]) == 0
    assert db.exists()
    assert served["port"] == 9001 and served["host"] == "127.0.0.1"
    assert "Serving x (deepseek) on http://127.0.0.1:9001" in capsys.readouterr().out
