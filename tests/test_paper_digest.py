"""Paper digest orchestrator tests (dedup, scoring, discovery, formatting)."""

from __future__ import annotations

from datetime import UTC, datetime

from io_mcp.state import StateStore
from io_mcp.tools import Paper, ScoredPaper, paper_digest


def _paper(pid, title, source="arxiv"):
    return Paper(
        id=pid,
        title=title,
        abstract="abstract",
        source=source,
        published=datetime(2025, 5, 1, tzinfo=UTC),
    )


def test_dedupe_by_id():
    papers = [
        _paper("2505.1", "Paper One"),
        _paper("2505.1", "Paper One duplicate id", source="semantic_scholar"),
        _paper("2505.2", "Paper Two"),
    ]
    out = paper_digest._dedupe(papers)
    assert [p.id for p in out] == ["2505.1", "2505.2"]


def test_dedupe_by_fuzzy_title():
    papers = [
        _paper("a", "A Thermodynamic Model!"),
        _paper("b", "a thermodynamic model"),  # same after normalization
    ]
    out = paper_digest._dedupe(papers)
    assert len(out) == 1


def test_clamp_score():
    assert paper_digest._clamp_score(7) == 5
    assert paper_digest._clamp_score(0) == 1
    assert paper_digest._clamp_score("3") == 3
    assert paper_digest._clamp_score(None) == 1
    assert paper_digest._clamp_score(4.6) == 5


async def test_score_relevance_uses_ollama(sample_paper, fake_ollama, config):
    fake_ollama.json_response = {"score": 5, "rationale": "directly relevant"}
    scored = await paper_digest.score_relevance(
        sample_paper, config.research.context, ollama=fake_ollama, config=config
    )
    assert scored.score == 5
    assert scored.rationale == "directly relevant"
    # model override for relevance scoring should be requested
    assert fake_ollama.calls[0][0] == "generate_json"


async def test_format_digest_empty():
    md = await paper_digest.format_digest([])
    assert "No new relevant papers" in md


async def test_format_digest_renders(sample_paper):
    sp = ScoredPaper(paper=sample_paper, score=4, rationale="closely related",
                     interest="Test interest")
    md = await paper_digest.format_digest([sp])
    assert sample_paper.title in md
    assert "closely related" in md
    assert "Test interest" in md
    assert "(4/5)" in md


async def test_discover_papers_end_to_end(monkeypatch, config, fake_ollama):
    async def fake_arxiv(query, max_results=50, **kwargs):
        return [_paper("2505.1", "Relevant paper")]

    async def fake_s2(query, limit=50, **kwargs):
        # Duplicate of the arxiv paper (same id) + one unique.
        return [
            _paper("2505.1", "Relevant paper", source="semantic_scholar"),
            _paper("s2unique", "Another relevant paper", source="semantic_scholar"),
        ]

    monkeypatch.setattr(paper_digest.arxiv_mod, "search_arxiv", fake_arxiv)
    monkeypatch.setattr(paper_digest.s2_mod, "search_papers", fake_s2)
    fake_ollama.json_response = {"score": 4, "rationale": "relevant"}

    state = StateStore(":memory:")
    await state.init()

    scored = await paper_digest.discover_papers(
        config=config, state=state, ollama=fake_ollama
    )
    # 2 unique papers after dedup, both above threshold 3.
    assert len(scored) == 2
    assert all(sp.score == 4 for sp in scored)
    # interest attribution present
    assert scored[0].interest == "Test interest"
    await state.close()


async def test_discover_papers_skips_seen(monkeypatch, config, fake_ollama):
    async def fake_arxiv(query, max_results=50, **kwargs):
        return [_paper("seen1", "Seen paper"), _paper("new1", "New paper")]

    async def fake_s2(query, limit=50, **kwargs):
        return []

    monkeypatch.setattr(paper_digest.arxiv_mod, "search_arxiv", fake_arxiv)
    monkeypatch.setattr(paper_digest.s2_mod, "search_papers", fake_s2)

    state = StateStore(":memory:")
    await state.init()
    await state.mark_paper_seen("seen1", "arxiv")

    scored = await paper_digest.discover_papers(
        config=config, state=state, ollama=fake_ollama
    )
    ids = {sp.paper.id for sp in scored}
    assert ids == {"new1"}
    await state.close()


async def test_discover_papers_caps_scoring_pool(monkeypatch, config, fake_ollama):
    # 5 candidates with distinct dates; cap scoring at 2 → only the 2 newest score.
    async def fake_arxiv(query, max_results=50, **kwargs):
        return [
            Paper(id=f"p{m}", title=f"Paper {m}", abstract="a", source="arxiv",
                  published=datetime(2025, m, 1, tzinfo=UTC))
            for m in range(1, 6)
        ]

    async def fake_s2(query, limit=50, **kwargs):
        return []

    monkeypatch.setattr(paper_digest.arxiv_mod, "search_arxiv", fake_arxiv)
    monkeypatch.setattr(paper_digest.s2_mod, "search_papers", fake_s2)
    config.research.max_papers_to_score = 2

    state = StateStore(":memory:")
    await state.init()
    scored = await paper_digest.discover_papers(
        config=config, state=state, ollama=fake_ollama
    )
    # Only 2 papers were sent to the model (the thermal cap), not all 5.
    assert len(fake_ollama.calls) == 2
    # And they are the two newest (May, April).
    assert {sp.paper.id for sp in scored} == {"p5", "p4"}
    await state.close()


async def test_list_tools_describes_each_tool(config):
    from io_mcp.server import build_server

    mcp = build_server(config)
    result = await mcp._tool_manager.get_tool("list_tools").fn()
    names = {t["name"] for t in result}
    # The core tools are present and list_tools excludes itself.
    assert {"search_papers", "run_digest", "host_status"} <= names
    assert "list_tools" not in names
    # Each entry carries a description and its parameter names.
    search = next(t for t in result if t["name"] == "search_papers")
    assert search["description"]
    assert search["parameters"] == ["limit", "query", "source"]


async def test_discover_papers_below_threshold_filtered(monkeypatch, config, fake_ollama):
    async def fake_arxiv(query, max_results=50, **kwargs):
        return [_paper("low", "Low relevance paper")]

    async def fake_s2(query, limit=50, **kwargs):
        return []

    monkeypatch.setattr(paper_digest.arxiv_mod, "search_arxiv", fake_arxiv)
    monkeypatch.setattr(paper_digest.s2_mod, "search_papers", fake_s2)
    fake_ollama.json_response = {"score": 1, "rationale": "not relevant"}

    state = StateStore(":memory:")
    await state.init()
    scored = await paper_digest.discover_papers(
        config=config, state=state, ollama=fake_ollama
    )
    assert scored == []
    await state.close()
