"""askvisa-server: serve the assistant, the web app and its REST API.

    askvisa-server                                  Anthropic, on http://127.0.0.1:8000
    askvisa-server --provider ollama                local Qwen
    askvisa-server --port 9000 --db sessions.db
    askvisa-server --allow-origin http://localhost:5173    let a dev frontend on another port call it

The web app is served from frontend/dist when it has been built (npm run build in frontend/, which
start.sh does); --static points elsewhere.

Before serving it migrates the database and, for a local model, checks that Ollama is running
and the model is downloaded, exiting with what to do if not. The API's docs are at /docs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

from runtime.agent import Agent, build_agent
from runtime.api import create_app
from runtime.events import EventLog
from runtime.migrate import migrate
from runtime.model import ModelError
from runtime.providers import PROVIDERS

WEB_APP = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        agent = build_agent(args.provider, args.model, web=not args.no_web)
    except (RuntimeError, ValueError, ModelError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    return asyncio.run(serve(agent, args))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="askvisa-server", description="Serve the assistant: the web app and its REST API.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="anthropic")
    parser.add_argument("--model", help="model name (default: the provider's default)")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="agent.db", help="the sessions database (default agent.db)")
    parser.add_argument("--no-web", action="store_true", help="don't give the agent web search")
    parser.add_argument("--allow-origin", action="append", default=[], help="a browser origin allowed to call the API")
    parser.add_argument("--static", type=Path, default=WEB_APP, help="the built web app (default frontend/dist)")
    return parser.parse_args(argv)


async def serve(agent: Agent, args: argparse.Namespace) -> int:
    """Check the model, then serve until stopped, all on one event loop.

    The model's HTTP client keeps connections open in the loop that first used them, so the
    check and the turns must share a loop: a check run under its own asyncio.run leaves pooled
    connections tied to a closed loop, and the first turn fails with "Event loop is closed".
    """
    try:
        await agent.check()
    except ModelError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    migrate(args.db)
    log = EventLog(args.db)
    try:
        app = create_app(agent, log, allow_origins=args.allow_origin, static_dir=args.static)
        print(f"Open http://{args.host}:{args.port} to use AskVisa ({agent.model_name}, {agent.provider}).")
        await uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")).serve()
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
