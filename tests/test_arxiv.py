"""arXiv client tests — Atom parsing and mocked search."""

from __future__ import annotations

from datetime import date

from io_mcp.tools import arxiv as arxiv_mod

ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2505.08764v1</id>
    <updated>2025-05-13T17:59:00Z</updated>
    <published>2025-05-13T17:59:00Z</published>
    <title>A thermodynamic model of promoter activity</title>
    <summary>  We present a statistical mechanical
    model of transcription regulation.  </summary>
    <author><name>A. Researcher</name></author>
    <author><name>B. Scientist</name></author>
    <link href="http://arxiv.org/abs/2505.08764v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2505.08764v1" rel="related" type="application/pdf"/>
    <category term="q-bio.MN" scheme="http://arxiv.org/schemas/atom"/>
    <category term="physics.bio-ph" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <updated>2024-01-02T00:00:00Z</updated>
    <published>2024-01-01T00:00:00Z</published>
    <title>An older paper</title>
    <summary>Older abstract.</summary>
    <author><name>C. Author</name></author>
    <link href="http://arxiv.org/abs/2401.00001v2" rel="alternate" type="text/html"/>
    <category term="q-bio.GN" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""


def test_parse_atom_basic():
    papers = arxiv_mod.parse_arxiv_atom(ATOM_FIXTURE)
    assert len(papers) == 2
    p = papers[0]
    assert p.id == "2505.08764"  # version stripped
    assert p.title == "A thermodynamic model of promoter activity"
    assert p.abstract == "We present a statistical mechanical model of transcription regulation."
    assert p.authors == ["A. Researcher", "B. Scientist"]
    assert "q-bio.MN" in p.categories
    assert p.pdf_url.endswith("2505.08764v1")
    assert p.source == "arxiv"
    assert p.published.date() == date(2025, 5, 13)


def test_extract_arxiv_id_variants():
    assert arxiv_mod._extract_arxiv_id("http://arxiv.org/abs/2505.08764v1") == "2505.08764"
    assert arxiv_mod._extract_arxiv_id("http://arxiv.org/abs/2505.08764") == "2505.08764"


async def test_search_arxiv_filters_by_start_date(monkeypatch):
    async def fake_fetch(params):
        return ATOM_FIXTURE

    monkeypatch.setattr(arxiv_mod, "_fetch", fake_fetch)
    # No date filter -> both papers.
    all_papers = await arxiv_mod.search_arxiv("q")
    assert len(all_papers) == 2
    # Filter to 2025 -> only the recent one.
    recent = await arxiv_mod.search_arxiv("q", start_date=date(2025, 1, 1))
    assert len(recent) == 1
    assert recent[0].id == "2505.08764"
