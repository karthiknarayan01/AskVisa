"""Append-only event log: the single source of truth for every session.

Every state change in the runtime (a user message, a model reply, a tool starting,
a tool finishing, an approval decision, a compaction) is written here as one event
BEFORE the runtime acts on it. Events are never updated, and are deleted only together with
their whole session (delete_session); all other state is rebuilt by replaying a session's
events. If `append()` returned, the event is on disk,
so a restarted process sees exactly what the dead one saw.

Tables (created by runtime/migrations/0001_create_sessions_and_events.sql, with request_id
added by 0002_add_request_id_to_events.sql):

    sessions(session_id PK, created_at, title)          title added by 0003
    events(seq INTEGER PK, session_id FK, type, payload JSON, ts, request_id)

`seq` is assigned by SQLite on insert, above every seq in the table, so ordering a session's
events by seq gives the order they were appended in. It is a 64-bit integer, so it can't run
out in practice. `request_id` groups the events of one user request (a turn): its user
message, every model step and tool call, and how it ended. load_request() reads one request.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.migrate import latest_version


@dataclass(frozen=True, slots=True)
class Event:
    """One row of the log, with its payload already decoded from JSON."""

    session_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    ts: str
    request_id: str | None = None  # the user request (turn) the event belongs to


@dataclass(frozen=True)
class SessionSummary:
    """One session for a list: its title, or its first user message while it has none."""

    session_id: str
    created_at: str
    updated_at: str
    title: str


class UnknownSession(KeyError):
    """Raised when appending to or loading a session that was never created."""


class SchemaOutOfDate(RuntimeError):
    """Raised when the database has not been migrated to the version this code needs."""


class EventLog:
    def __init__(self, db_path: str):
        """Open the log file and check it has been migrated.

        Connection settings, all chosen for durability:
          isolation_level=None  autocommit: each INSERT is its own committed transaction
          journal_mode=WAL      writers append to a write-ahead log; readers never block
          synchronous=FULL      fsync the WAL on every commit, so a committed event
                                survives power loss, not only a process crash
          foreign_keys=ON       an event for a session that doesn't exist is rejected
                                by the database itself, with no extra lookup
        """
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._check_schema(db_path)

    def _check_schema(self, db_path: str) -> None:
        """Fail fast if migrations haven't run, instead of failing later on a missing table."""
        try:
            (version,) = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        except sqlite3.OperationalError:
            version = 0
        if version < latest_version():
            self._conn.close()
            raise SchemaOutOfDate(
                f"{db_path} is at schema version {version}, need {latest_version()}; "
                f"run: python -m runtime.migrate {db_path}"
            )

    def new_session(self) -> str:
        """Create an empty session and return its id (a random 32-char hex uuid)."""
        session_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", (session_id, _now())
        )
        return session_id

    def append(
        self, session_id: str, type: str, payload: dict[str, Any], request_id: str | None = None
    ) -> Event:
        """Write one event and return it. The event is durable when this returns.

        One INSERT in autocommit mode is atomic and committed (and fsynced) before
        execute() returns. SQLite assigns seq. The foreign key rejects unknown sessions.
        """
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ts = _now()
        try:
            cursor = self._conn.execute(
                "INSERT INTO events (session_id, type, payload, ts, request_id) VALUES (?, ?, ?, ?, ?)",
                (session_id, type, body, ts, request_id),
            )
        except sqlite3.IntegrityError as e:
            raise UnknownSession(session_id) from e
        return Event(session_id, cursor.lastrowid, type, payload, ts, request_id)

    def load(self, session_id: str, after_seq: int = 0) -> list[Event]:
        """Return a session's events in seq order, optionally only those after `after_seq`.

        This is what a restarted process calls to rebuild its state. The sessions table is
        only consulted when no events come back, to tell "empty" apart from "unknown".
        """
        rows = self._conn.execute(
            "SELECT seq, type, payload, ts, request_id FROM events "
            "WHERE session_id = ? AND seq > ? ORDER BY seq",
            (session_id, after_seq),
        ).fetchall()
        if not rows and not self.has_session(session_id):
            raise UnknownSession(session_id)
        return [Event(session_id, seq, t, json.loads(p), ts, rid) for seq, t, p, ts, rid in rows]

    def load_request(self, session_id: str, request_id: str) -> list[Event]:
        """One request's events, in seq order: its user message, every step and tool call, and
        how it ended. Empty if the session has no such request."""
        rows = self._conn.execute(
            "SELECT seq, type, payload, ts FROM events "
            "WHERE session_id = ? AND request_id = ? ORDER BY seq",
            (session_id, request_id),
        ).fetchall()
        return [Event(session_id, seq, t, json.loads(p), ts, request_id) for seq, t, p, ts in rows]

    def delete_session(self, session_id: str) -> None:
        """Delete a session and all its events, in one transaction. Raises UnknownSession.

        The only way events are ever removed.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
            deleted = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,)).rowcount
            if not deleted:
                raise UnknownSession(session_id)
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def has_session(self, session_id: str) -> bool:
        """True if `new_session` created this id."""
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def latest_session(self) -> str | None:
        """The most recently created session id, or None if there are none."""
        row = self._conn.execute(
            "SELECT session_id FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        """The most recently active sessions first, with their titles (see SessionSummary)."""
        return self._summaries("", (limit,))

    def session_summary(self, session_id: str) -> SessionSummary:
        """One session's summary, as list_sessions gives it. Raises UnknownSession."""
        found = self._summaries("WHERE s.session_id = ?", (session_id, 1))
        if not found:
            raise UnknownSession(session_id)
        return found[0]

    def set_title(self, session_id: str, title: str, *, only_if_unset: bool = False) -> bool:
        """Set the session's title. With only_if_unset, a title already set (a name the user
        chose) is kept. Returns whether the title changed. Raises UnknownSession."""
        sql = "UPDATE sessions SET title = ? WHERE session_id = ?"
        if only_if_unset:
            sql += " AND title IS NULL"
        if self._conn.execute(sql, (title, session_id)).rowcount:
            return True
        if not self.has_session(session_id):
            raise UnknownSession(session_id)
        return False

    def _summaries(self, where: str, params: tuple[Any, ...]) -> list[SessionSummary]:
        rows = self._conn.execute(
            f"""
            SELECT s.session_id, s.created_at, COALESCE(MAX(e.ts), s.created_at) AS updated_at, s.title,
                   (SELECT payload FROM events
                     WHERE session_id = s.session_id AND type = 'user_message'
                     ORDER BY seq LIMIT 1) AS first_message
              FROM sessions s LEFT JOIN events e ON e.session_id = s.session_id
             {where}
             GROUP BY s.session_id
             ORDER BY updated_at DESC, s.rowid DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            SessionSummary(sid, created, updated, title or (json.loads(first)["text"] if first else ""))
            for sid, created, updated, title, first in rows
        ]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _now() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
