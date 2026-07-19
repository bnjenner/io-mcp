"""Semantic Scholar Graph API client.

Uses the relevance search endpoint and the paper-details endpoint. No API key is
required for modest usage; we stay polite with a ~1 req/sec limiter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx

from io_mcp.tools import Paper

log = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
DEFAULT_FIELDS = ["paperId", "title", "abstract", "authors", "year", "url", "tldr", "externalIds"]
REQUEST_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT = 30.0


class _RateLimiter:
    def __init__(self, delay: float):
        self.delay = delay
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self.delay:
                await asyncio.sleep(self.delay - elapsed)
            self._last = time.monotonic()


_rate_limiter = _RateLimiter(REQUEST_DELAY_SECONDS)


async def search_papers(
    query: str,
    limit: int = 50,
    fields: list[str] | None = None,
    year_range: str | None = None,
) -> list[Paper]:
    """Search papers via the relevance endpoint."""
    fields = fields or DEFAULT_FIELDS
    params = {
        "query": query,
        "limit": limit,
        "fields": ",".join(fields),
    }
    if year_range:
        params["year"] = year_range
    data = await _get("/paper/search", params)
    return [_parse_paper(item) for item in data.get("data", [])]


async def get_paper_details(paper_id: str, fields: list[str] | None = None) -> Paper:
    """Get full details for a single paper."""
    fields = fields or DEFAULT_FIELDS
    data = await _get(f"/paper/{paper_id}", {"fields": ",".join(fields)})
    return _parse_paper(data)


# --------------------------------------------------------------------------- #
async def _get(path: str, params: dict) -> dict:
    await _rate_limiter.wait()
    url = f"{S2_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        log.warning("Semantic Scholar request to %s failed: %s", url, exc)
        raise


def _parse_paper(item: dict) -> Paper:
    external = item.get("externalIds") or {}
    arxiv_id = external.get("ArXiv")

    authors = [a.get("name", "") for a in (item.get("authors") or []) if a.get("name")]

    tldr_field = item.get("tldr")
    tldr = tldr_field.get("text") if isinstance(tldr_field, dict) else tldr_field

    year = item.get("year")
    published = None
    if isinstance(year, int):
        published = datetime(year, 1, 1, tzinfo=UTC)

    # Prefer the arXiv ID as the canonical id when present (helps dedup across
    # sources); otherwise use the Semantic Scholar paperId.
    paper_id = arxiv_id or item.get("paperId", "")

    return Paper(
        id=paper_id,
        title=item.get("title") or "",
        authors=authors,
        abstract=item.get("abstract") or "",
        categories=[],
        published=published,
        updated=published,
        pdf_url="",
        arxiv_url=item.get("url") or "",
        source="semantic_scholar",
        tldr=tldr,
    )
