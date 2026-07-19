"""State store tests using an in-memory SQLite database."""

from __future__ import annotations

import pytest

from io_mcp.state import StateStore


@pytest.fixture
async def store():
    s = StateStore(":memory:")
    await s.init()
    yield s
    await s.close()


async def test_unseen_then_seen(store):
    assert await store.is_paper_seen("2505.08764") is False
    await store.mark_paper_seen("2505.08764", "arxiv", title="A paper", scored=4.0)
    assert await store.is_paper_seen("2505.08764") is True


async def test_mark_is_idempotent(store):
    await store.mark_paper_seen("id1", "arxiv", title="T")
    await store.mark_paper_seen("id1", "semantic_scholar", title="T2", scored=5.0)
    rows = await store.get_seen_papers()
    assert len(rows) == 1
    assert rows[0]["score"] == 5.0
    assert rows[0]["source"] == "semantic_scholar"


async def test_get_seen_papers_ordering(store):
    await store.mark_paper_seen("a", "arxiv")
    await store.mark_paper_seen("b", "arxiv")
    rows = await store.get_seen_papers()
    ids = {r["paper_id"] for r in rows}
    assert ids == {"a", "b"}


async def test_record_digest_run(store):
    await store.record_digest_run(10, 3, ["Interest A"])
    # Insertion should not raise; verify via direct query.
    conn = await store._get_conn()
    async with conn.execute("SELECT papers_found, papers_relevant FROM digest_runs") as cur:
        row = await cur.fetchone()
    assert row["papers_found"] == 10
    assert row["papers_relevant"] == 3


async def test_prune_old_keeps_recent(store):
    await store.mark_paper_seen("recent", "arxiv")
    pruned = await store.prune_old(older_than_days=90)
    assert pruned == 0
    assert await store.is_paper_seen("recent") is True
