"""Paper discovery orchestrator — the nightly digest pipeline.

Also usable interactively: ``discover_papers`` / ``score_relevance`` /
``format_digest`` are independently callable, and ``run_digest`` chains them and
delivers via ntfy.

Clients (config, Ollama, state, ntfy) are injectable so the pipeline can be
tested without live services.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date

from io_mcp.config import Config, load_prompt
from io_mcp.notify import NtfyClient
from io_mcp.ollama import OllamaClient, OllamaError
from io_mcp.state import StateStore
from io_mcp.tools import Paper, ScoredPaper
from io_mcp.tools import arxiv as arxiv_mod
from io_mcp.tools import semantic_scholar as s2_mod

log = logging.getLogger(__name__)

RELEVANCE_PROMPT_NAME = "relevance_score"


# --------------------------------------------------------------------------- #
# Client construction helpers
# --------------------------------------------------------------------------- #
def _ollama_from_config(config: Config) -> OllamaClient:
    return OllamaClient(
        base_url=config.ollama.base_url, default_model=config.ollama.default_model
    )


def _ntfy_from_config(config: Config) -> NtfyClient:
    return NtfyClient(
        base_url=config.ntfy.base_url, default_topic=config.ntfy.default_topic
    )


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
async def discover_papers(
    interests=None,
    since: date | None = None,
    skip_seen: bool = True,
    *,
    config: Config | None = None,
    state: StateStore | None = None,
    ollama: OllamaClient | None = None,
) -> list[ScoredPaper]:
    """Full pipeline: query sources → dedup → drop seen → score → threshold/sort."""
    config = config or Config.load()
    interests = interests if interests is not None else config.research.interests
    ollama = ollama or _ollama_from_config(config)

    # 1. Query both sources for every interest, tolerating per-source failures.
    raw: list[Paper] = []
    origin: dict[str, str] = {}  # paper id -> interest name (first seen)
    for interest in interests:
        papers = await _query_interest(interest, since)
        for p in papers:
            raw.append(p)
            origin.setdefault(_dedup_key(p), interest.name)

    # 2. Deduplicate across sources.
    deduped = _dedupe(raw)

    # 3. Drop previously-seen papers.
    if skip_seen and state is not None:
        unseen = []
        for p in deduped:
            if not await state.is_paper_seen(p.id):
                unseen.append(p)
        deduped = unseen

    # 4. Score relevance (sequential — small local models on the BC250).
    scored: list[ScoredPaper] = []
    for p in deduped:
        try:
            sp = await score_relevance(p, config.research.context, ollama=ollama, config=config)
        except OllamaError as exc:
            log.warning("Scoring failed for %s: %s", p.id, exc)
            continue
        sp.interest = origin.get(_dedup_key(p))
        scored.append(sp)

    # 5. Threshold + sort by score (desc).
    threshold = config.research.relevance_threshold
    relevant = [s for s in scored if s.score >= threshold]
    relevant.sort(key=lambda s: s.score, reverse=True)
    return relevant[: config.research.max_papers_per_digest]


async def _query_interest(interest, since: date | None) -> list[Paper]:
    """Query arXiv + Semantic Scholar for one interest; never raises."""
    results: list[Paper] = []
    if interest.arxiv_query:
        try:
            results.extend(
                await arxiv_mod.search_arxiv(interest.arxiv_query, start_date=since)
            )
        except Exception as exc:  # noqa: BLE001 — one source failing must not stop the pipeline
            log.warning("arXiv query for %r failed: %s", interest.name, exc)
    if interest.semantic_scholar_query:
        try:
            results.extend(
                await s2_mod.search_papers(interest.semantic_scholar_query)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Semantic Scholar query for %r failed: %s", interest.name, exc)
    return results


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
async def score_relevance(
    paper: Paper,
    context: str,
    *,
    ollama: OllamaClient | None = None,
    config: Config | None = None,
) -> ScoredPaper:
    """Send abstract + research context to Ollama; return score + rationale."""
    if ollama is None:
        config = config or Config.load()
        ollama = _ollama_from_config(config)

    system = load_prompt(RELEVANCE_PROMPT_NAME)
    model = None
    if config is not None:
        model = config.ollama.model_for("relevance_scoring")

    prompt = (
        f"Researcher context:\n{context.strip()}\n\n"
        f"Paper title: {paper.title}\n\n"
        f"Abstract: {paper.abstract or paper.tldr or '(no abstract available)'}"
    )
    data = await ollama.generate_json(prompt, model=model, system=system)
    score = _clamp_score(data.get("score"))
    rationale = str(data.get("rationale", "")).strip()
    return ScoredPaper(paper=paper, score=score, rationale=rationale)


def _clamp_score(value) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, score))


# --------------------------------------------------------------------------- #
# Dedup
# --------------------------------------------------------------------------- #
def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _dedup_key(paper: Paper) -> str:
    return paper.id or _normalize_title(paper.title)


def _dedupe(papers: list[Paper]) -> list[Paper]:
    """Dedup by exact id first, then by normalized-title fallback."""
    by_id: dict[str, Paper] = {}
    by_title: dict[str, Paper] = {}
    ordered: list[Paper] = []
    for p in papers:
        title_key = _normalize_title(p.title)
        if p.id and p.id in by_id:
            continue
        if title_key and title_key in by_title:
            continue
        if p.id:
            by_id[p.id] = p
        if title_key:
            by_title[title_key] = p
        ordered.append(p)
    return ordered


# --------------------------------------------------------------------------- #
# Formatting + delivery
# --------------------------------------------------------------------------- #
async def format_digest(papers: list[ScoredPaper]) -> str:
    """Format scored papers into a readable markdown digest."""
    if not papers:
        return "No new relevant papers found."
    lines = [f"# Paper digest — {len(papers)} relevant\n"]
    for sp in papers:
        p = sp.paper
        authors = ", ".join(p.authors[:3])
        if len(p.authors) > 3:
            authors += " et al."
        stars = "★" * sp.score + "☆" * (5 - sp.score)
        url = p.arxiv_url or p.pdf_url
        header = f"## {stars} ({sp.score}/5) {p.title}"
        lines.append(header)
        if sp.interest:
            lines.append(f"*Interest:* {sp.interest}")
        if authors:
            lines.append(f"*Authors:* {authors}")
        if sp.rationale:
            lines.append(f"*Why:* {sp.rationale}")
        if url:
            lines.append(f"[Link]({url})")
        lines.append("")
    return "\n".join(lines).strip()


async def run_digest(
    interests=None,
    since: date | None = None,
    *,
    dry_run: bool = False,
    config: Config | None = None,
) -> dict:
    """Full pipeline: discover → score → format → (notify) → persist state."""
    config = config or Config.load()
    state = StateStore(str(config.state.resolved_path))
    await state.init()

    scored = await discover_papers(
        interests=interests, since=since, config=config, state=state
    )
    digest_md = await format_digest(scored)

    delivered = False
    if not dry_run and scored:
        ntfy = _ntfy_from_config(config)
        topic = config.ntfy.topic_for("papers")
        delivered = await ntfy.send(
            digest_md,
            topic=topic,
            title=f"Paper digest: {len(scored)} relevant",
            tags=["books"],
            markdown=True,
        )

    # Persist seen state regardless of delivery outcome.
    if not dry_run:
        for sp in scored:
            await state.mark_paper_seen(
                sp.paper.id, sp.paper.source, title=sp.paper.title, scored=float(sp.score)
            )
        interest_names = [i.name for i in (interests or config.research.interests)]
        await state.record_digest_run(
            papers_found=len(scored), papers_relevant=len(scored),
            interests_queried=interest_names,
        )
    await state.close()

    return {
        "papers": [sp.to_dict() for sp in scored],
        "count": len(scored),
        "digest_markdown": digest_md,
        "delivered": delivered,
        "dry_run": dry_run,
    }


def run_digest_sync(**kwargs) -> dict:
    """Blocking wrapper for CLI/cron entry points."""
    return asyncio.run(run_digest(**kwargs))
