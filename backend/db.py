"""
ResearchForge PostgreSQL session store.
Replaces the in-memory sessions dict with durable storage.
All functions are synchronous — callers run inside asyncio.to_thread or
are synchronous background tasks.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is required")
        _pool = ConnectionPool(
            db_url,
            min_size=2,
            max_size=10,
            kwargs={"autocommit": True},
        )
        logger.info("Database connection pool initialized")
    return _pool


def setup_db() -> None:
    """
    Create the sessions table if it does not exist.
    Called once at server startup before accepting requests.
    """
    with get_pool().connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id           TEXT        PRIMARY KEY,
                topic        TEXT        NOT NULL,
                depth        TEXT        NOT NULL,
                status       TEXT        NOT NULL DEFAULT 'pending',
                run_name     TEXT,
                trace_url    TEXT,
                versioning_report JSONB,
                state        JSONB       NOT NULL DEFAULT '{}',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    logger.info("Database schema verified")


def create_session(session_id: str, data: dict) -> None:
    """Insert a new session row."""
    with get_pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (id, topic, depth, status, run_name, trace_url, state, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                data["topic"],
                data["depth"],
                data.get("status", "running"),
                data.get("run_name"),
                data.get("trace_url"),
                json.dumps(data.get("state", {})),
                data.get("created_at", datetime.now(timezone.utc).isoformat()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_session(session_id: str) -> dict | None:
    """Return a session dict or None if not found."""
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT id, topic, depth, status, run_name, trace_url,
                   versioning_report, state, created_at, updated_at
            FROM sessions WHERE id = %s
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "topic": row[1],
        "depth": row[2],
        "status": row[3],
        "run_name": row[4],
        "trace_url": row[5],
        "versioning_report": row[6],
        "state": row[7] if isinstance(row[7], dict) else {},
        "created_at": row[8].isoformat()
        if hasattr(row[8], "isoformat")
        else str(row[8]),
        "updated_at": row[9].isoformat()
        if hasattr(row[9], "isoformat")
        else str(row[9]),
    }


def update_session(session_id: str, updates: dict) -> None:
    """
    Update specific fields of a session.
    Accepted keys: status, run_name, trace_url, versioning_report, state.
    Always updates updated_at.
    """
    allowed = {"status", "run_name", "trace_url", "versioning_report", "state"}
    set_clauses = []
    values = []

    for key, value in updates.items():
        if key not in allowed:
            continue
        if key in ("state", "versioning_report"):
            set_clauses.append(f"{key} = %s::jsonb")
            values.append(json.dumps(value) if value is not None else None)
        else:
            set_clauses.append(f"{key} = %s")
            values.append(value)

    if not set_clauses:
        return

    set_clauses.append("updated_at = %s")
    values.append(datetime.now(timezone.utc).isoformat())
    values.append(session_id)

    with get_pool().connection() as conn:
        conn.execute(
            f"UPDATE sessions SET {', '.join(set_clauses)} WHERE id = %s",
            values,
        )


def cleanup_old_sessions(ttl_seconds: int) -> list:
    """
    Delete sessions older than ttl_seconds.
    Returns list of dicts with 'id' and 'uploaded_pdfs' so caller
    can delete associated files from disk.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    with get_pool().connection() as conn:
        rows = conn.execute(
            "DELETE FROM sessions WHERE created_at < %s RETURNING id, state",
            (cutoff,),
        ).fetchall()

    deleted = []
    for row in rows:
        session_id = row[0]
        state = row[1] if isinstance(row[1], dict) else {}
        deleted.append(
            {
                "id": session_id,
                "uploaded_pdfs": state.get("uploaded_pdfs", []),
            }
        )
        logger.info(f"Cleaned up expired session: {session_id}")

    return deleted
