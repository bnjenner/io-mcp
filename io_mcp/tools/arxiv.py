"""arXiv API client.

Queries ``http://export.arxiv.org/api/query`` (Atom XML) and parses results into
the shared :class:`~io_mcp.tools.Paper` type. arXiv asks for a 3-second delay
between requests; we enforce it with a process-wide async rate limiter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime
from xml.etree import ElementTree as ET

import httpx

from io_mcp.tools import Paper

log = logging.getLogger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
REQUEST_DELAY_SECONDS = 3.0
DEFAULT_TIMEOUT = 30.0

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class _RateLimiter:
    """Serializes callers so consecutive requests are >= ``delay`` apart."""

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


async def search_arxiv(
    query: str,
    max_results: int = 50,
    sort_by: str = "submittedDate",
    sort_order: str = "descending",
    start_date: date | None = None,
) -> list[Paper]:
    """Query the arXiv API and return parsed papers.

    ``start_date`` is applied client-side (filtering on ``published``) because
    the arXiv query API does not support date ranges in all query modes.
    """
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    xml_text = await _fetch(params)
    papers = parse_arxiv_atom(xml_text)
    if start_date is not None:
        papers = [
            p
            for p in papers
            if p.published is not None and p.published.date() >= start_date
        ]
    return papers


async def _fetch(params: dict) -> str:
    await _rate_limiter.wait()
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(ARXIV_API_URL, params=params)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as exc:
        log.warning("arXiv request failed: %s", exc)
        raise


def parse_arxiv_atom(xml_text: str) -> list[Paper]:
    """Parse an arXiv Atom feed into a list of Papers."""
    root = ET.fromstring(xml_text)
    papers: list[Paper] = []
    for entry in root.findall(f"{ATOM}entry"):
        paper = _parse_entry(entry)
        if paper is not None:
            papers.append(paper)
    return papers


def _parse_entry(entry: ET.Element) -> Paper | None:
    raw_id = _text(entry.find(f"{ATOM}id"))
    if not raw_id:
        return None
    arxiv_id = _extract_arxiv_id(raw_id)

    title = _clean(_text(entry.find(f"{ATOM}title")))
    abstract = _clean(_text(entry.find(f"{ATOM}summary")))

    authors = [
        _text(a.find(f"{ATOM}name"))
        for a in entry.findall(f"{ATOM}author")
        if _text(a.find(f"{ATOM}name"))
    ]

    categories = [
        c.get("term")
        for c in entry.findall(f"{ATOM}category")
        if c.get("term")
    ]

    published = _parse_dt(_text(entry.find(f"{ATOM}published")))
    updated = _parse_dt(_text(entry.find(f"{ATOM}updated")))

    pdf_url = ""
    abs_url = raw_id
    for link in entry.findall(f"{ATOM}link"):
        rel = link.get("rel")
        href = link.get("href", "")
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = href
        elif rel == "alternate":
            abs_url = href
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    return Paper(
        id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        categories=categories,
        published=published,
        updated=updated,
        pdf_url=pdf_url,
        arxiv_url=abs_url,
        source="arxiv",
    )


def _extract_arxiv_id(raw_id: str) -> str:
    # e.g. "http://arxiv.org/abs/2505.08764v1" -> "2505.08764"
    tail = raw_id.rstrip("/").split("/")[-1]
    if "v" in tail:
        base, _, ver = tail.rpartition("v")
        if base and ver.isdigit():
            return base
    return tail


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # arXiv uses e.g. "2024-05-13T17:59:00Z"
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError:
            return None


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _clean(value: str) -> str:
    return " ".join(value.split())
