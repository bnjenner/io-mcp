"""SQLite state store for io-mcp.

Tracks papers that have already been seen (so nightly digests don't repeat) and
records a row per digest run for light history/telemetry.

A single connection is opened lazily and reused, which keeps ``:memory:``
databases (used in tests) alive across calls.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_papers (
    paper_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    score REAL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    papers_found INTEGER,
    papers_relevant INTEGER,
    interests_queried TEXT
);
"""


class StateStore:
    """Async SQLite-backed store. Pass ``:memory:`` for tests."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._resolved = self._resolve(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _resolve(db_path: str) -> str:
        if db_path == ":memory:":
            return db_path
        return str(Path(os.path.expanduser(db_path)))

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return the shared connection, opening and initializing it if needed."""
        if self._conn is None:
            async with self._lock:
                if self._conn is None:
                    if self._resolved != ":memory:":
                        Path(self._resolved).parent.mkdir(parents=True, exist_ok=True)
                    conn = await aiosqlite.connect(self._resolved)
                    conn.row_factory = aiosqlite.Row
                    await conn.executescript(SCHEMA)
                    await conn.commit()
                    self._conn = conn
        return self._conn

    async def init(self) -> None:
        """Ensure the database file, parent dirs, and schema all exist."""
        await self._get_conn()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> StateStore:
        await self.init()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ----------------------------------------------------------------------- #
    async def is_paper_seen(self, paper_id: str) -> bool:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT 1 FROM seen_papers WHERE paper_id = ?", (paper_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_paper_seen(
        self,
        paper_id: str,
        source: str,
        title: str | None = None,
        scored: float | None = None,
    ) -> None:
        conn = await self._get_conn()
        await conn.execute(
            """
            INSERT INTO seen_papers (paper_id, source, title, score)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                source = excluded.source,
                title = COALESCE(excluded.title, seen_papers.title),
                score = COALESCE(excluded.score, seen_papers.score)
            """,
            (paper_id, source, title, scored),
        )
        await conn.commit()

    async def get_seen_papers(self, since: date | None = None) -> list[dict]:
        query = "SELECT paper_id, source, title, score, first_seen FROM seen_papers"
        params: tuple = ()
        if since is not None:
            query += " WHERE first_seen >= ?"
            params = (since.isoformat(),)
        query += " ORDER BY first_seen DESC"
        conn = await self._get_conn()
        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def record_digest_run(
        self,
        papers_found: int,
        papers_relevant: int,
        interests_queried: list[str] | None = None,
    ) -> None:
        conn = await self._get_conn()
        await conn.execute(
            """
            INSERT INTO digest_runs (papers_found, papers_relevant, interests_queried)
            VALUES (?, ?, ?)
            """,
            (papers_found, papers_relevant, json.dumps(interests_queried or [])),
        )
        await conn.commit()

    async def prune_old(self, older_than_days: int = 90) -> int:
        """Delete seen-paper rows older than ``older_than_days``. Returns count."""
        conn = await self._get_conn()
        cur = await conn.execute(
            "DELETE FROM seen_papers WHERE first_seen < datetime('now', ?)",
            (f"-{int(older_than_days)} days",),
        )
        await conn.commit()
        return cur.rowcount


def now() -> datetime:
    return datetime.now()
