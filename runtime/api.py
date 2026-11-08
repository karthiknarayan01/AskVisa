"""The REST API: sessions, and turns streamed as Server-Sent Events.

    GET    /api/health                       {"status": "ok"}
    GET    /api/config                       {"provider", "model"}
    GET    /api/sessions                     recent sessions: [{"id", "title", "created_at", "updated_at"}]
    POST   /api/sessions                     a new session: 201 {"id"}
    GET    /api/sessions/{id}                {"id", "title", "items", "running", "interrupted"}
    POST   /api/sessions/{id}/messages       {"text"} -> the turn as text/event-stream (see api_events)
    POST   /api/sessions/{id}/resume         finish an interrupted turn, streamed the same way
    POST   /api/sessions/{id}/cancel         stop the running turn: 202, or 409 if none is running

Errors are JSON {"detail": "..."}: 404 for an unknown session, 409 when a turn is already running
or the last one was interrupted (resume it), 422 for an empty message.

A browser reads the stream with fetch() and a reader over response.body, the way chat apps do;
EventSource can't send a POST body. Interactive docs are at /docs.

Given `static_dir` (the built web app, frontend/dist), the app is served too: its files as they
are, and index.html for any other path outside /api, so /c/<session id> opens in the app.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from runtime.agent import Agent
from runtime.api_events import conversation
from runtime.events import EventLog, UnknownSession
from runtime.turns import TurnBusy, TurnInterrupted, Turns

STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


def create_app(
    agent: Agent, log: EventLog, allow_origins: list[str] | None = None, static_dir: Path | None = None
) -> FastAPI:
    """The app, serving one agent's sessions from one event log, and the web app if built."""
    app = FastAPI(title="AskVisa API", version="1")
    if allow_origins:
        app.add_middleware(CORSMiddleware, allow_origins=allow_origins, allow_methods=["*"], allow_headers=["*"])
    turns = Turns(log, agent)
    app.state.turns = turns

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/config")
    async def config() -> dict:
        return {"provider": agent.provider, "model": agent.model_name}

    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50) -> list[dict]:
        return [
            {"id": s.session_id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}
            for s in log.list_sessions(limit)
        ]

    @app.post("/api/sessions", status_code=201)
    async def create_session() -> dict:
        return {"id": turns.create().session_id}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        session = _session(turns, session_id)
        request_ids = [e.request_id for e in log.load(session_id) if e.type == "user_message"]
        items = conversation(session.state, request_ids)
        title = next((i["text"] for i in items if i["type"] == "user_message"), "")
        return {
            "id": session_id,
            "title": title,
            "items": items,
            "running": turns.is_running(session_id),
            "interrupted": session.needs_resume and not turns.is_running(session_id),
        }

    @app.post("/api/sessions/{session_id}/messages")
    async def post_message(session_id: str, message: NewMessage) -> StreamingResponse:
        _session(turns, session_id)
        if not message.text.strip():
            raise HTTPException(422, "the message is empty")
        try:
            stream = turns.stream(session_id, message.text.strip())
        except (TurnBusy, TurnInterrupted) as e:
            raise HTTPException(409, str(e)) from None
        return StreamingResponse(stream, media_type="text/event-stream", headers=STREAM_HEADERS)

    @app.post("/api/sessions/{session_id}/resume")
    async def resume(session_id: str) -> StreamingResponse:
        _session(turns, session_id)
        try:
            stream = turns.stream_resume(session_id)
        except (TurnBusy, ValueError) as e:
            raise HTTPException(409, str(e)) from None
        return StreamingResponse(stream, media_type="text/event-stream", headers=STREAM_HEADERS)

    @app.post("/api/sessions/{session_id}/cancel", status_code=202)
    async def cancel(session_id: str) -> dict:
        _session(turns, session_id)
        if not turns.cancel(session_id):
            raise HTTPException(409, "no turn is running")
        return {"cancelling": True}

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
