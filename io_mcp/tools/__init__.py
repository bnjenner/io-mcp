"""Shared data types and a lightweight tool registry.

The ``Paper`` dataclass is shared by the arXiv and Semantic Scholar clients so
downstream code (dedup, scoring, digest formatting) works on one type.

The registry is a thin foundation for the future "auto-discover tools from
``io_mcp/tools/``" extension point noted in the spec. Modules register a
callable with :func:`register_tool`; the MCP server / CLI can enumerate them via
:func:`iter_tools`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Paper:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    categories: list[str] = field(default_factory=list)
    published: datetime | None = None
    updated: datetime | None = None
    pdf_url: str = ""
    arxiv_url: str = ""
    source: str = ""  # 'arxiv' or 'semantic_scholar'
    tldr: str | None = None  # Semantic Scholar model-generated TLDR, if present

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "categories": self.categories,
            "published": self.published.isoformat() if self.published else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "pdf_url": self.pdf_url,
            "arxiv_url": self.arxiv_url,
            "source": self.source,
            "tldr": self.tldr,
        }


@dataclass
class ScoredPaper:
    paper: Paper
    score: int
    rationale: str = ""
    interest: str | None = None  # which configured interest surfaced it

    def to_dict(self) -> dict:
        d = self.paper.to_dict()
        d.update(
            {"score": self.score, "rationale": self.rationale, "interest": self.interest}
        )
        return d


# --------------------------------------------------------------------------- #
# Minimal registry (extension point for auto-discovery)
# --------------------------------------------------------------------------- #
@dataclass
class ToolSpec:
    name: str
    func: Callable
    description: str


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(name: str, description: str = "") -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        _REGISTRY[name] = ToolSpec(name=name, func=func, description=description or (func.__doc__ or "").strip())
        return func

    return decorator


def iter_tools() -> Iterator[ToolSpec]:
    yield from _REGISTRY.values()


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)
