"""The HTTP API, and the WebSocket that carries every turn live (runtime/live.py).

    GET    /api/health                       {"status": "ok"}
    GET    /api/config                       {"provider", "model"}
    GET    /api/sessions                     recent sessions: [{"id", "title", "created_at", "updated_at", "running"}]
    POST   /api/sessions                     a new session: 201 {"id"}
    GET    /api/sessions/{id}                {"id", "title", "items", "running", "interrupted"}
    PATCH  /api/sessions/{id}                rename it: {"title"} -> 200 with the session as listed,
                                             422 for an empty or over-long title
    DELETE /api/sessions/{id}                delete the session and its history: 204 (a running
                                             turn is stopped first)
    WS     /api/ws                           the live channel: start, resume and stop turns, and
                                             receive the events of every session's turns

Errors are JSON {"detail": "..."}: 404 for an unknown session. Refused live commands come back
on the socket as {"type": "error", ...}.

A browser tab keeps one socket open; uvicorn pings it every 20 seconds, so an idle connection
isn't dropped while a local model thinks. Interactive docs for the HTTP part are at /docs.

Given `static_dir` (the built web app, frontend/dist), the app is served too: its files as they
are, and index.html for any other path outside /api, so /c/<session id> opens in the app.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from runtime.agent import Agent
from runtime.api_events import conversation
from runtime.events import EventLog, UnknownSession
from runtime.live import LiveChannel
from runtime.turns import TurnBusy, TurnInterrupted, Turns

MAX_MESSAGE_CHARS = 20_000
MAX_TITLE_CHARS = 120


class Rename(BaseModel):
    title: str = Field(max_length=MAX_TITLE_CHARS)


def create_app(
    agent: Agent, log: EventLog, allow_origins: list[str] | None = None, static_dir: Path | None = None
) -> FastAPI:
    """The app, serving one agent's sessions from one event log, and the web app if built."""
    app = FastAPI(title="AskVisa API", version="2")
    if allow_origins:
        app.add_middleware(CORSMiddleware, allow_origins=allow_origins, allow_methods=["*"], allow_headers=["*"])
    turns = Turns(log, agent, LiveChannel())
    app.state.turns = turns

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/config")
    async def config() -> dict:
        return {"provider": agent.provider, "model": agent.model_name}

    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50) -> list[dict]:
        return [turns.describe(s) for s in log.list_sessions(limit)]

    @app.post("/api/sessions", status_code=201)
    async def create_session() -> dict:
        return {"id": turns.create().session_id}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        session = _session(turns, session_id)
        request_ids = [e.request_id for e in log.load(session_id) if e.type == "user_message"]
        items = conversation(session.state, request_ids)
        return {
            "id": session_id,
            "title": log.session_summary(session_id).title,
            "items": items,
            "running": turns.is_running(session_id),
            "interrupted": session.needs_resume and not turns.is_running(session_id),
        }

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, body: Rename) -> dict:
        title = " ".join(body.title.split())
        if not title:
            raise HTTPException(422, "the title is empty")
        try:
            return turns.rename(session_id, title)
        except UnknownSession:
            raise HTTPException(404, f"no session {session_id}") from None

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        try:
            await turns.delete(session_id)
        except UnknownSession:
            raise HTTPException(404, f"no session {session_id}") from None
        return Response(status_code=204)

    @app.websocket("/api/ws")
    async def live(socket: WebSocket) -> None:
        await socket.accept()
        client = turns.live.connect({"type": "hello", "running": turns.snapshot()})

        async def send_all() -> None:
            try:
                while True:
                    await socket.send_json(await client.outbox.get())
            except (WebSocketDisconnect, RuntimeError):
                pass  # the tab went away; the receive loop below sees it too

        sender = asyncio.create_task(send_all())
        try:
            while True:
                try:
                    message = await socket.receive_json()
                except ValueError:
                    client.send({"type": "error", "session_id": None, "message": "messages must be JSON"})
                    continue
                refused = command(turns, message)
                if refused is not None:
                    client.send({"type": "error", "session_id": message.get("session_id"), "message": refused})
        except WebSocketDisconnect:
            pass
        finally:
            turns.live.disconnect(client)
            sender.cancel()

    if static_dir is not None and (static_dir / "index.html").is_file():
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def web_app(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404, "no such endpoint")
            file = (root / path).resolve()
            if path and file.is_file() and file.is_relative_to(root):
                return FileResponse(file)
            return FileResponse(root / "index.html")

    return app


def _session(turns: Turns, session_id: str):
    try:
        return turns.session(session_id)
    except UnknownSession:
        raise HTTPException(404, f"no session {session_id}") from None


def command(turns: Turns, message: Any) -> str | None:
    """Carry out one command from a tab. Returns why it was refused, or None if it was done."""
    if not isinstance(message, dict):
        return "a command is a JSON object"
    kind, session_id = message.get("type"), message.get("session_id")
    if not isinstance(session_id, str):
        return "the command needs a session_id"
    try:
        if kind == "send":
            text = message.get("text")
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                return "the message is empty"
            if len(text) > MAX_MESSAGE_CHARS:
                return f"the message is longer than {MAX_MESSAGE_CHARS:,} characters"
            turns.start(session_id, text)
        elif kind == "resume":
            turns.resume(session_id)
        elif kind == "cancel":
            if not turns.cancel(session_id):
                return "no turn is running"
        else:
            return f"unknown command {kind!r}"
    except UnknownSession:
        return f"no session {session_id}"
    except (TurnBusy, TurnInterrupted, ValueError) as e:
        return str(e)
    return None
