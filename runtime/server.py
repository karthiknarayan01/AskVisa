"""agent-server: serve the agent's REST API.

    agent-server                                  Anthropic, on http://127.0.0.1:8000
    agent-server --provider ollama                local Qwen
    agent-server --port 9000 --db sessions.db
    agent-server --allow-origin http://localhost:5173    let a dev frontend on another port call it

Before serving it migrates the database and, for a local model, checks that Ollama is running
and the model is downloaded, exiting with what to do if not. The API's docs are at /docs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from runtime.agent import build_agent
from runtime.api import create_app
from runtime.events import EventLog
from runtime.migrate import migrate
from runtime.model import ModelError
from runtime.providers import PROVIDERS


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        agent = build_agent(args.provider, args.model, web=not args.no_web)
        asyncio.run(agent.check())
    except (RuntimeError, ValueError, ModelError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    migrate(args.db)
    log = EventLog(args.db)
    app = create_app(agent, log, allow_origins=args.allow_origin)
    print(f"Serving {agent.model_name} ({agent.provider}) on http://{args.host}:{args.port}  (API docs: /docs)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    log.close()
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent-server", description="Serve the agent's REST API.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="anthropic")
    parser.add_argument("--model", help="model name (default: the provider's default)")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="agent.db", help="the sessions database (default agent.db)")
    parser.add_argument("--no-web", action="store_true", help="don't give the agent web search")
    parser.add_argument("--allow-origin", action="append", default=[], help="a browser origin allowed to call the API")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
