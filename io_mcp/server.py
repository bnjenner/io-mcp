"""MCP server (Streamable HTTP) exposing io-mcp tools to Open WebUI.

Tools appear in Open WebUI with the ``io-mcp`` server prefix. Connect via
Admin Settings → External Tools → Add Server → MCP (Streamable HTTP) at
``http://<host>:<port>/mcp``.
"""

from __future__ import annotations

from datetime import date

from mcp.server.fastmcp import FastMCP

from io_mcp.config import Config
from io_mcp.state import StateStore
from io_mcp.tools import arxiv as arxiv_mod
from io_mcp.tools import homelab as homelab_mod
from io_mcp.tools import logs as logs_mod
from io_mcp.tools import paper_digest
from io_mcp.tools import semantic_scholar as s2_mod


def build_server(config: Config | None = None) -> FastMCP:
    config = config or Config.load()
    mcp = FastMCP("io-mcp", host=config.server.host, port=config.server.port)

    @mcp.tool()
    async def search_papers(
        query: str, source: str = "both", limit: int = 20
    ) -> list[dict]:
        """Search arXiv and/or Semantic Scholar for papers matching a query.

        Args:
            query: Free-text or arXiv-style search query.
            source: 'arxiv', 's2', or 'both' (default).
            limit: Max results per source.
        """
        results = []
        if source in ("arxiv", "both"):
            try:
                results += await arxiv_mod.search_arxiv(query, max_results=limit)
            except Exception as exc:  # noqa: BLE001
                results.append({"error": f"arxiv: {exc}"})
        if source in ("s2", "both"):
            try:
                results += await s2_mod.search_papers(query, limit=limit)
            except Exception as exc:  # noqa: BLE001
                results.append({"error": f"semantic_scholar: {exc}"})
        return [r.to_dict() if hasattr(r, "to_dict") else r for r in results]

    @mcp.tool()
    async def score_paper(title: str, abstract: str) -> dict:
        """Score a paper's relevance (1-5) to the user's research context.

        Args:
            title: Paper title.
            abstract: Paper abstract.
        """
        from io_mcp.tools import Paper

        paper = Paper(id="", title=title, abstract=abstract)
        scored = await paper_digest.score_relevance(
            paper, config.research.context, config=config
        )
        return {"score": scored.score, "rationale": scored.rationale}

    @mcp.tool()
    async def run_digest(dry_run: bool = False) -> dict:
        """Run the full paper-discovery pipeline (discover → score → digest).

        Args:
            dry_run: If true, do not push a notification via ntfy.
        """
        return await paper_digest.run_digest(dry_run=dry_run, config=config)

    @mcp.tool()
    async def recent_papers(since: str | None = None) -> list[dict]:
        """Show papers recorded in recent digest runs.

        Args:
            since: Optional ISO date (YYYY-MM-DD) lower bound.
        """
        state = StateStore(str(config.state.resolved_path))
        await state.init()
        since_date = date.fromisoformat(since) if since else None
        rows = await state.get_seen_papers(since=since_date)
        await state.close()
        return rows

    @mcp.tool()
    async def host_status(hostname: str | None = None) -> dict | list[dict]:
        """Get health metrics for one homelab host, or all configured hosts.

        Args:
            hostname: Specific host, or omit for all configured hosts.
        """
        return await homelab_mod.get_host_status(hostname, config=config)

    @mcp.tool()
    async def query_prometheus(query: str) -> dict:
        """Execute a raw PromQL instant query against Prometheus.

        Args:
            query: A PromQL expression.
        """
        return await homelab_mod.query_prometheus(query, config=config)

    @mcp.tool()
    async def summarize_logs(
        host: str = "localhost",
        unit: str | None = None,
        since: str = "1h",
        priority: str | None = None,
    ) -> str:
        """Summarize recent system logs for a host/unit via journalctl + Ollama.

        Args:
            host: Host to query (default localhost).
            unit: Optional systemd unit to filter.
            since: Lookback window, e.g. '1h', '30m', '2 days ago'.
            priority: Optional journald priority filter, e.g. 'err', 'warning'.
        """
        return await logs_mod.summarize_journal_logs(
            host, unit=unit, since=since, priority=priority, config=config
        )

    return mcp


def serve(host: str | None = None, port: int | None = None) -> None:
    """Build and run the MCP server over Streamable HTTP."""
    config = Config.load()
    if host is not None:
        config.server.host = host
    if port is not None:
        config.server.port = port
    mcp = build_server(config)
    mcp.run(transport="streamable-http")
