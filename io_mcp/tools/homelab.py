"""Homelab monitoring via Prometheus.

Instant/range PromQL queries plus a structured ``get_host_status`` that rolls up
the common node_exporter health metrics for one or all configured hosts.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from io_mcp.config import Config

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20.0


def _prom_base(config: Config | None) -> str:
    config = config or Config.load()
    return config.homelab.prometheus.base_url.rstrip("/")


async def query_prometheus(query: str, *, config: Config | None = None) -> dict:
    """Execute a PromQL instant query (POST /api/v1/query)."""
    base = _prom_base(config)
    url = f"{base}/api/v1/query"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(url, data={"query": query})
        resp.raise_for_status()
        return resp.json()


async def query_prometheus_range(
    query: str,
    start: datetime,
    end: datetime,
    step: str = "1m",
    *,
    config: Config | None = None,
) -> dict:
    """Execute a PromQL range query (POST /api/v1/query_range)."""
    base = _prom_base(config)
    url = f"{base}/api/v1/query_range"
    data = {
        "query": query,
        "start": start.timestamp(),
        "end": end.timestamp(),
        "step": step,
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- #
# Structured host status
# --------------------------------------------------------------------------- #
def _scalar(result: dict) -> float | None:
    """Pull the first scalar value out of a Prometheus vector response."""
    try:
        data = result["data"]["result"]
        if not data:
            return None
        return float(data[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


async def get_host_status(
    hostname: str | None = None, *, config: Config | None = None
) -> dict | list[dict]:
    """Query common health metrics for one host, or all configured hosts.

    Returns a structured dict per host (not raw PromQL). If ``hostname`` is None,
    returns a list covering every configured host.
    """
    config = config or Config.load()
    if hostname is not None:
        return await _host_status_one(hostname, config)
    return [
        await _host_status_one(h.name, config) for h in config.homelab.hosts
    ]


async def _host_status_one(hostname: str, config: Config) -> dict:
    inst = _instance_matcher(hostname)
    queries = {
        "up": f'up{{instance=~"{inst}"}}',
        "cpu_usage_pct": (
            f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle",instance=~"{inst}"}}[5m])))'
        ),
        "mem_used_pct": (
            f'100 * (1 - (node_memory_MemAvailable_bytes{{instance=~"{inst}"}} '
            f'/ node_memory_MemTotal_bytes{{instance=~"{inst}"}}))'
        ),
        "disk_avail_bytes": (
            f'min(node_filesystem_avail_bytes{{instance=~"{inst}",fstype!~"tmpfs|overlay"}})'
        ),
        "load1": f'node_load1{{instance=~"{inst}"}}',
        "boot_time_seconds": f'node_boot_time_seconds{{instance=~"{inst}"}}',
    }

    status: dict = {"host": hostname}
    for key, promql in queries.items():
        try:
            result = await query_prometheus(promql, config=config)
            status[key] = _scalar(result)
        except httpx.HTTPError as exc:
            log.warning("Prometheus query for %s/%s failed: %s", hostname, key, exc)
            status[key] = None

    up = status.get("up")
    status["reachable"] = up is not None and up >= 1
    return status


def _instance_matcher(hostname: str) -> str:
    """Regex fragment matching ``host`` or ``host:port`` instance labels."""
    escaped = hostname.replace(".", r"\\.")
    return f"{escaped}(:.*)?"


async def get_service_status(
    host: str, services: list[str] | None = None, *, config: Config | None = None
) -> list[dict]:
    """Query systemd unit states via node_exporter's systemd collector.

    Requires node_exporter to be started with ``--collector.systemd`` (exposing
    ``node_systemd_unit_state``). Returns a note if no data is available.
    """
    inst = _instance_matcher(host)
    if services:
        names = "|".join(re_escape(s) for s in services)
        promql = f'node_systemd_unit_state{{instance=~"{inst}",name=~"{names}",state="active"}}'
    else:
        promql = f'node_systemd_unit_state{{instance=~"{inst}",state="active"}}'

    try:
        result = await query_prometheus(promql, config=config)
    except httpx.HTTPError as exc:
        return [{"host": host, "error": f"Prometheus query failed: {exc}"}]

    rows = result.get("data", {}).get("result", [])
    if not rows:
        return [
            {
                "host": host,
                "note": "No node_systemd_unit_state metrics found — requires "
                "node_exporter with the systemd collector enabled.",
            }
        ]
    out = []
    for row in rows:
        metric = row.get("metric", {})
        out.append(
            {
                "host": host,
                "unit": metric.get("name"),
                "state": metric.get("state"),
                "active": _first_value(row) == 1.0,
            }
        )
    return out


def _first_value(row: dict) -> float | None:
    try:
        return float(row["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def re_escape(s: str) -> str:
    import re

    return re.escape(s)
