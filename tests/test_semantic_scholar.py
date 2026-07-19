"""Semantic Scholar client tests — parsing, mocked search, and 429 retry."""

from __future__ import annotations

import httpx
import pytest

from io_mcp.tools import semantic_scholar as s2_mod

SEARCH_RESPONSE = {
    "total": 2,
    "data": [
        {
            "paperId": "abc123",
            "title": "Statistical mechanics of gene expression",
            "abstract": "A biophysical model.",
            "authors": [{"name": "D. Author"}, {"name": "E. Coauthor"}],
            "year": 2024,
            "url": "https://www.semanticscholar.org/paper/abc123",
            "tldr": {"text": "Models gene expression thermodynamically."},
            "externalIds": {"ArXiv": "2402.12345", "DOI": "10.1/x"},
        },
        {
            "paperId": "def456",
            "title": "No arxiv id here",
            "abstract": None,
            "authors": [],
            "year": None,
            "url": "https://www.semanticscholar.org/paper/def456",
            "tldr": None,
            "externalIds": {},
        },
    ],
}


def test_parse_paper_prefers_arxiv_id():
    paper = s2_mod._parse_paper(SEARCH_RESPONSE["data"][0])
    assert paper.id == "2402.12345"  # arXiv id preferred for dedup
    assert paper.tldr == "Models gene expression thermodynamically."
    assert paper.authors == ["D. Author", "E. Coauthor"]
    assert paper.source == "semantic_scholar"
    assert paper.published.year == 2024


def test_parse_paper_without_arxiv_or_year():
    paper = s2_mod._parse_paper(SEARCH_RESPONSE["data"][1])
    assert paper.id == "def456"
    assert paper.abstract == ""
    assert paper.tldr is None
    assert paper.published is None


async def test_search_papers_mocked(monkeypatch):
    async def fake_get(path, params):
        assert path == "/paper/search"
        return SEARCH_RESPONSE

    monkeypatch.setattr(s2_mod, "_get", fake_get)
    papers = await s2_mod.search_papers("gene regulation")
    assert len(papers) == 2
    assert papers[0].id == "2402.12345"


class _FakeClient:
    """AsyncClient stand-in that pops queued responses from a shared list."""

    def __init__(self, queue):
        self._queue = queue  # shared reference across retry attempts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        return self._queue.pop(0)


def _resp(status, *, json=None, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return httpx.Response(
        status,
        json=json if json is not None else {},
        headers=headers,
        request=httpx.Request("GET", s2_mod.S2_BASE + "/paper/search"),
    )


async def test_get_retries_on_429_then_succeeds(monkeypatch):
    queue = [
        _resp(429, retry_after=0),          # first attempt: throttled
        _resp(200, json=SEARCH_RESPONSE),   # retry: success
    ]
    monkeypatch.setattr(s2_mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(queue))

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(s2_mod.asyncio, "sleep", fake_sleep)

    data = await s2_mod._get("/paper/search", {"query": "x"})
    assert data["total"] == 2
    assert queue == []          # both responses consumed → exactly one retry
    assert slept                # backed off at least once


async def test_get_raises_after_exhausting_retries(monkeypatch):
    queue = [_resp(429, retry_after=0) for _ in range(s2_mod.MAX_RETRIES)]
    monkeypatch.setattr(s2_mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(queue))

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(s2_mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await s2_mod._get("/paper/search", {"query": "x"})


def test_api_key_header(monkeypatch):
    monkeypatch.delenv(s2_mod.API_KEY_ENV, raising=False)
    assert s2_mod._headers() == {}
    monkeypatch.setenv(s2_mod.API_KEY_ENV, "secret-key")
    assert s2_mod._headers() == {"x-api-key": "secret-key"}
