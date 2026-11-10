"""The HTTP part of the API: sessions, errors, CORS and serving the web app."""

import httpx
import pytest

from runtime.api import create_app
from tests.api_helpers import make_agent, make_log
from tests.fake_model import say


@pytest.fixture
def log(tmp_path):
    with make_log(tmp_path) as log:
        yield log


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_and_config(log):
    async with client(create_app(make_agent([]), log)) as http:
        assert (await http.get("/api/health")).json() == {"status": "ok"}
        assert (await http.get("/api/config")).json() == {"provider": "anthropic", "model": "test-model"}


async def test_sessions_are_listed_most_recent_first(log):
    app = create_app(make_agent([say("a"), say("b")]), log)
    async with client(app) as http:
        older = (await http.post("/api/sessions")).json()["id"]
        newer = (await http.post("/api/sessions")).json()["id"]
        await app.state.turns.start(newer, "second")
        await app.state.turns.start(older, "first")
        listed = (await http.get("/api/sessions")).json()
        assert [s["id"] for s in listed] == [older, newer]
        assert listed[0]["title"] == "first" and listed[0]["running"] is False
        stored = (await http.get(f"/api/sessions/{older}")).json()
        assert stored["title"] == "first" and not stored["running"] and not stored["interrupted"]
        assert [i["type"] for i in stored["items"]] == ["user_message", "assistant_message"]


async def test_unknown_sessions(log):
    async with client(create_app(make_agent([]), log)) as http:
        assert (await http.get("/api/sessions/nope")).status_code == 404
        assert (await http.delete("/api/sessions/nope")).status_code == 404


async def test_cors_only_when_origins_are_allowed(log):
    allowed = create_app(make_agent([]), log, allow_origins=["http://localhost:5173"])
    async with client(allowed) as http:
        response = await http.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    async with client(create_app(make_agent([]), log)) as http:
        response = await http.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in response.headers


async def test_the_web_app_is_served_with_a_fallback_to_index(log, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("no")
    async with client(create_app(make_agent([]), log, static_dir=dist)) as http:
        assert (await http.get("/")).text == "<html>app</html>"
        assert (await http.get("/c/some-session")).text == "<html>app</html>"
        assert (await http.get("/assets/app.js")).text == "console.log(1)"
        assert (await http.get("/../secret.txt")).text != "no"
        assert (await http.get("/api/nope")).status_code == 404
        assert (await http.get("/api/health")).json() == {"status": "ok"}


async def test_the_page_is_always_revalidated_and_hashed_assets_are_cached_for_good(log, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "index-abc123.js").write_text("x")
    (dist / "favicon.svg").write_text("<svg/>")
    async with client(create_app(make_agent([]), log, static_dir=dist)) as http:
        for path in ("/", "/c/some-session", "/index.html", "/favicon.svg"):
            assert (await http.get(path)).headers["cache-control"] == "no-cache", path
        assert "immutable" in (await http.get("/assets/index-abc123.js")).headers["cache-control"]


async def test_without_a_built_app_only_the_api_is_served(log, tmp_path):
    async with client(create_app(make_agent([]), log, static_dir=tmp_path / "missing")) as http:
        assert (await http.get("/")).status_code == 404
