"""Semantic Scholar Graph API client.

Uses the relevance search endpoint and the paper-details endpoint. The public
*unauthenticated* API is a shared pool that throttles aggressively (HTTP 429),
so we rate-limit to ~1 req/sec and retry with exponential backoff, honoring any
``Retry-After`` header.

Set a (free) API key via the ``SEMANTIC_SCHOLAR_API_KEY`` environment variable to
get a much higher, dedicated rate limit:
https://www.semanticscholar.org/product/api#api-key
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import UTC, datetime

import httpx

from io_mcp.tools import Paper

log = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
DEFAULT_FIELDS = ["paperId", "title", "abstract", "authors", "year", "url", "tldr", "externalIds"]
REQUEST_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT = 30.0

API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 20.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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
def _headers() -> dict:
    key = os.environ.get(API_KEY_ENV)
    return {"x-api-key": key} if key else {}


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter, capped."""
    base = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_CAP_SECONDS)
    return base + random.uniform(0, 0.5)


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Prefer the server's Retry-After header; otherwise back off exponentially."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), BACKOFF_CAP_SECONDS)
        except ValueError:
            pass
    return _backoff(attempt)


async def _get(path: str, params: dict) -> dict:
    url = f"{S2_BASE}{path}"
    headers = _headers()
    for attempt in range(MAX_RETRIES):
        await _rate_limiter.wait()
        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if attempt == MAX_RETRIES - 1:
                log.warning("Semantic Scholar request to %s failed: %s", url, exc)
                raise
            await asyncio.sleep(_backoff(attempt))
            continue

        if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES - 1:
            delay = _retry_delay(resp, attempt)
            log.info(
                "Semantic Scholar %s on %s; backing off %.1fs (attempt %d/%d)",
                resp.status_code, path, delay, attempt + 1, MAX_RETRIES,
            )
            await asyncio.sleep(delay)
            continue

        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Semantic Scholar request to %s failed: %s", url, exc)
            raise
        return resp.json()

    raise RuntimeError("Semantic Scholar retries exhausted")  # pragma: no cover


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
