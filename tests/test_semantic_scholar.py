"""Semantic Scholar client tests — parsing and mocked search."""

from __future__ import annotations

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
