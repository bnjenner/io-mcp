"""io-mcp command-line entry point (cron jobs and one-off tasks).

Commands share the core library with the MCP server. All I/O-bound work is async
under the hood; each command wraps it in ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from datetime import date

import click

from io_mcp.config import DEFAULT_CONFIG_PATH, Config, default_config_yaml
from io_mcp.state import StateStore
from io_mcp.tools import arxiv as arxiv_mod
from io_mcp.tools import homelab as homelab_mod
from io_mcp.tools import logs as logs_mod
from io_mcp.tools import paper_digest
from io_mcp.tools import semantic_scholar as s2_mod


@click.group()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="Path to config file (default: ~/.config/io-mcp/config.yaml).")
@click.pass_context
def main(ctx: click.Context, config_path: str | None) -> None:
    """io-mcp — Local Research & Homelab Assistant Toolkit."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


def _load_config(ctx: click.Context) -> Config:
    return Config.load(ctx.obj.get("config_path"))


# --------------------------------------------------------------------------- #
@main.command()
@click.option("--dry-run", is_flag=True, help="Score and display without notifying.")
@click.option("--since", "since_str", default=None, help="Lookback date (YYYY-MM-DD).")
@click.option("--interests", "interest_names", multiple=True,
              help="Run only specific interest group(s) by name.")
@click.option("--prune-days", type=int, default=None,
              help="After the run, prune seen papers older than N days.")
@click.pass_context
def digest(ctx, dry_run, since_str, interest_names, prune_days):
    """Run the paper discovery pipeline; push results to ntfy."""
    config = _load_config(ctx)
    since = date.fromisoformat(since_str) if since_str else None
    interests = _select_interests(config, interest_names)

    async def _run():
        result = await paper_digest.run_digest(
            interests=interests, since=since, dry_run=dry_run, config=config
        )
        if prune_days is not None:
            state = StateStore(str(config.state.resolved_path))
            await state.init()
            pruned = await state.prune_old(prune_days)
            await state.close()
            result["pruned"] = pruned
        return result

    result = asyncio.run(_run())
    click.echo(result["digest_markdown"])
    status = "delivered" if result["delivered"] else ("dry-run" if dry_run else "not delivered")
    click.echo(f"\n[{result['count']} relevant papers — {status}]", err=True)


@main.command()
@click.argument("query")
@click.option("--source", type=click.Choice(["arxiv", "s2", "both"]), default="both")
@click.option("--limit", type=int, default=20)
@click.option("--score", is_flag=True, help="Also run relevance scoring on results.")
@click.pass_context
def search(ctx, query, source, limit, score):
    """One-off paper search (arXiv + Semantic Scholar)."""
    config = _load_config(ctx)

    async def _run():
        papers = []
        if source in ("arxiv", "both"):
            try:
                papers += await arxiv_mod.search_arxiv(query, max_results=limit)
            except Exception as exc:  # noqa: BLE001
                click.echo(f"arxiv error: {exc}", err=True)
        if source in ("s2", "both"):
            try:
                papers += await s2_mod.search_papers(query, limit=limit)
            except Exception as exc:  # noqa: BLE001
                click.echo(f"semantic scholar error: {exc}", err=True)
        results = []
        for p in papers:
            entry = {"paper": p}
            if score:
                sp = await paper_digest.score_relevance(
                    p, config.research.context, config=config
                )
                entry["scored"] = sp
            results.append(entry)
        return results

    results = asyncio.run(_run())
    for entry in results:
        p = entry["paper"]
        line = f"• {p.title}"
        if "scored" in entry:
            line = f"• [{entry['scored'].score}/5] {p.title}"
        click.echo(line)
        if p.arxiv_url:
            click.echo(f"    {p.arxiv_url}")
        if "scored" in entry and entry["scored"].rationale:
            click.echo(f"    → {entry['scored'].rationale}")
    click.echo(f"\n[{len(results)} results]", err=True)


@main.command()
@click.option("--host", "hostname", default=None, help="Specific host (default all).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def status(ctx, hostname, as_json):
    """Show homelab host health summary."""
    config = _load_config(ctx)
    result = asyncio.run(homelab_mod.get_host_status(hostname, config=config))
    if as_json:
        click.echo(jsonlib.dumps(result, indent=2))
        return
    hosts = result if isinstance(result, list) else [result]
    for h in hosts:
        reach = "UP" if h.get("reachable") else "DOWN"
        cpu = _fmt(h.get("cpu_usage_pct"), "%")
        mem = _fmt(h.get("mem_used_pct"), "%")
        load = _fmt(h.get("load1"))
        click.echo(f"{h.get('host'):<12} {reach:<5} cpu={cpu} mem={mem} load={load}")


@main.command()
@click.argument("promql")
@click.pass_context
def query(ctx, promql):
    """Run a raw PromQL query and display results."""
    config = _load_config(ctx)
    result = asyncio.run(homelab_mod.query_prometheus(promql, config=config))
    click.echo(jsonlib.dumps(result, indent=2))


@main.command()
@click.option("--host", "hostname", default="localhost", help="Host to query.")
@click.option("--unit", default=None, help="Systemd unit to filter.")
@click.option("--since", "since_str", default="1h", help="Lookback window.")
@click.option("--priority", default=None, help="Journald priority (err, warning...).")
@click.pass_context
def logs(ctx, hostname, unit, since_str, priority):
    """Summarize recent logs."""
    config = _load_config(ctx)
    summary = asyncio.run(
        logs_mod.summarize_journal_logs(
            hostname, unit=unit, since=since_str, priority=priority, config=config
        )
    )
    click.echo(summary)


@main.command()
@click.option("--host", "host_", default=None, help="Bind address (default from config).")
@click.option("--port", type=int, default=None, help="Port (default from config).")
@click.pass_context
def serve(ctx, host_, port):
    """Start the MCP server (Streamable HTTP)."""
    from io_mcp.server import serve as _serve

    config = _load_config(ctx)
    click.echo(
        f"Starting io-mcp MCP server on "
        f"http://{host_ or config.server.host}:{port or config.server.port}/mcp",
        err=True,
    )
    _serve(host=host_, port=port)


@main.command()
@click.pass_context
def config(ctx):
    """Print resolved configuration (with env overrides applied)."""
    cfg = _load_config(ctx)
    import dataclasses

    def _serialize(obj):
        if dataclasses.is_dataclass(obj):
            return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
        return obj

    printable = _serialize(cfg)
    printable.pop("raw", None)
    click.echo(jsonlib.dumps(printable, indent=2, default=str))


@main.command()
@click.pass_context
def init(ctx):
    """Create the default config file and state database."""
    cfg_path = DEFAULT_CONFIG_PATH
    if ctx.obj.get("config_path"):
        from pathlib import Path
        cfg_path = Path(ctx.obj["config_path"]).expanduser()
    if cfg_path.exists():
        click.echo(f"Config already exists at {cfg_path}", err=True)
    else:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(default_config_yaml())
        click.echo(f"Wrote default config to {cfg_path}")

    config = Config.load(str(cfg_path))
    state = StateStore(str(config.state.resolved_path))
    asyncio.run(state.init())
    click.echo(f"Initialized state database at {config.state.resolved_path}")


# --------------------------------------------------------------------------- #
def _select_interests(config: Config, names):
    if not names:
        return None
    wanted = set(names)
    selected = [i for i in config.research.interests if i.name in wanted]
    if not selected:
        raise click.ClickException(
            f"No configured interests match: {', '.join(names)}"
        )
    return selected


def _fmt(value, suffix=""):
    if value is None:
        return "?"
    return f"{value:.1f}{suffix}"


if __name__ == "__main__":
    main()
