"""Homelab health digest — gather a snapshot, format it, and (optionally) alert.

Mirrors the paper digest, but for infrastructure. ``run_homelab_digest`` chains
``get_homelab_overview`` → ``format_overview`` → ntfy delivery. By default it is
purely Prometheus-backed (no GPU) and only notifies when something is wrong, so
it is cheap to run on a frequent timer. The Ollama natural-language summary is
opt-in (``summarize=True``) because it spins up the local model.
"""

from __future__ import annotations

import asyncio
import json
import logging

from io_mcp.config import Config
from io_mcp.notify import NtfyClient
from io_mcp.ollama import OllamaClient, OllamaError
from io_mcp.tools import homelab as homelab_mod

log = logging.getLogger(__name__)

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def _fmt_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    val = float(n)
    for unit in _UNITS:
        if abs(val) < 1024 or unit == _UNITS[-1]:
            return f"{val:.0f}{unit}"
        val /= 1024
    return f"{val:.0f}PB"


def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}%"


def _fmt_load(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def format_overview(overview: dict) -> str:
    """Render a compact markdown digest (kept well under ntfy's 4 KB limit)."""
    counts = overview["counts"]
    problems = overview["problems"]
    healthy = overview["healthy"]

    header = "✅ Homelab healthy" if healthy else "⚠️ Homelab problems"
    lines = [f"# {header}"]
    lines.append(
        f"Hosts {counts['hosts_reachable']}/{counts['hosts_total']} up · "
        f"Probes {counts['probes_up']}/{counts['probes_total']} up\n"
    )

    if not healthy:
        lines.append("**Problems**")
        for h in problems["unreachable_hosts"]:
            lines.append(f"- ❌ Host unreachable: {h}")
        for t in problems["down_endpoints"]:
            lines.append(f"- ❌ Endpoint down: {t}")
        lines.append("")

    lines.append("**Hosts**")
    for h in overview["hosts"]:
        if h.get("reachable"):
            lines.append(
                f"- {h['host']} — cpu {_fmt_pct(h.get('cpu_usage_pct'))} · "
                f"mem {_fmt_pct(h.get('mem_used_pct'))} · "
                f"disk {_fmt_bytes(h.get('disk_avail_bytes'))} free · "
                f"load {_fmt_load(h.get('load1'))}"
            )
        else:
            lines.append(f"- {h['host']} — ❌ unreachable")

    return "\n".join(lines).strip()


async def _summarize(overview: dict, config: Config) -> str | None:
    """Optional Ollama natural-language summary (uses the GPU — opt-in only)."""
    ollama = OllamaClient(
        base_url=config.ollama.base_url, default_model=config.ollama.default_model
    )
    system = (
        "You are a homelab operations assistant. Given a JSON snapshot of host "
        "metrics and endpoint probes, write a 2-3 sentence status summary. Lead "
        "with any problems (unreachable hosts, down endpoints); if everything is "
        "healthy, say so briefly. Be concise and specific."
    )
    try:
        text = await ollama.generate(
            json.dumps(overview),
            model=config.ollama.model_for("summarization"),
            system=system,
        )
    except OllamaError as exc:
        log.warning("Homelab summary generation failed: %s", exc)
        return None
    return text.strip()


async def run_homelab_digest(
    *,
    dry_run: bool = False,
    always_notify: bool = False,
    summarize: bool = False,
    config: Config | None = None,
) -> dict:
    """Gather → format → (notify). Notifies only on problems unless always_notify."""
    config = config or Config.load()
    overview = await homelab_mod.get_homelab_overview(config=config)
    body = format_overview(overview)

    summary = None
    if summarize:
        summary = await _summarize(overview, config)
        if summary:
            body = f"{summary}\n\n{body}"

    healthy = overview["healthy"]
    n_problems = len(overview["problems"]["unreachable_hosts"]) + len(
        overview["problems"]["down_endpoints"]
    )

    delivered = False
    if not dry_run and (not healthy or always_notify):
        ntfy = NtfyClient(
            base_url=config.ntfy.base_url, default_topic=config.ntfy.default_topic
        )
        title = (
            "Homelab: all healthy"
            if healthy
            else f"Homelab: {n_problems} problem(s)"
        )
        delivered = await ntfy.send(
            body,
            topic=config.ntfy.topic_for("homelab"),
            title=title,
            priority=4 if not healthy else 3,
            tags=["warning"] if not healthy else ["white_check_mark"],
            markdown=True,
        )

    return {
        "overview": overview,
        "markdown": body,
        "summary": summary,
        "healthy": healthy,
        "problems": n_problems,
        "delivered": delivered,
        "dry_run": dry_run,
    }


def run_homelab_digest_sync(**kwargs) -> dict:
    """Blocking wrapper for CLI/cron entry points."""
    return asyncio.run(run_homelab_digest(**kwargs))
