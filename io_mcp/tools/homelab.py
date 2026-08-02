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
    # PromQL regexes live inside a double-quoted string literal, where a literal
    # dot must be written as \\. (the backslash is itself escaped in the string).
    escaped = hostname.replace(".", r"\\.")
    return f"{escaped}(:.*)?"


# --------------------------------------------------------------------------- #
# Blackbox probe status (endpoint up/down checks)
# --------------------------------------------------------------------------- #
def _probe_target(labels: dict) -> str | None:
    """The probed endpoint — blackbox puts it in ``instance`` (or ``target``)."""
    return labels.get("instance") or labels.get("target")


def _probe_key(labels: dict) -> tuple:
    """Identity of a probe across metric families: (target, job)."""
    return (_probe_target(labels), labels.get("job"))


async def _probe_vector(metric: str, config: Config | None) -> list[tuple[dict, float]]:
    """Instant-query ``metric``; return [(labels, value), ...] ([] on error/empty)."""
    try:
        result = await query_prometheus(metric, config=config)
    except httpx.HTTPError as exc:
        log.warning("Prometheus query for %s failed: %s", metric, exc)
        return []
    out: list[tuple[dict, float]] = []
    for item in result.get("data", {}).get("result", []):
        try:
            out.append((item.get("metric", {}), float(item["value"][1])))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return out


def _match_value(vec: list[tuple[dict, float]], labels: dict) -> float | None:
    """Value from ``vec`` whose probe identity matches ``labels``."""
    key = _probe_key(labels)
    for lbls, val in vec:
        if _probe_key(lbls) == key:
            return val
    return None


async def get_probe_status(
    target: str | None = None, *, config: Config | None = None
) -> list[dict]:
    """Roll up blackbox_exporter probe results (endpoint up/down checks).

    Reads ``probe_success`` (plus duration and HTTP status where present) and
    returns one row per probed target — down ones first. Requires
    blackbox_exporter to be scraped by Prometheus. If ``target`` is given, only
    probes whose endpoint contains that substring are returned.
    """
    success = await _probe_vector("probe_success", config)
    if not success:
        return [
            {
                "note": "No probe_success metrics found — is blackbox_exporter "
                "scraped by Prometheus?"
            }
        ]
    durations = await _probe_vector("probe_duration_seconds", config)
    http_codes = await _probe_vector("probe_http_status_code", config)

    rows: list[dict] = []
    for labels, value in success:
        name = _probe_target(labels)
        if target is not None and (name is None or target not in name):
            continue
        row: dict = {"target": name, "alive": value >= 1, "job": labels.get("job")}
        dur = _match_value(durations, labels)
        if dur is not None:
            row["duration_s"] = round(dur, 4)
        code = _match_value(http_codes, labels)
        if code is not None:
            row["http_code"] = int(code)
        rows.append(row)
    # Down first, then alphabetically by target.
    rows.sort(key=lambda r: (r["alive"], r["target"] or ""))
    return rows


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


# --------------------------------------------------------------------------- #
# Whole-homelab snapshot
# --------------------------------------------------------------------------- #
async def get_homelab_overview(*, config: Config | None = None) -> dict:
    """Gather a full homelab snapshot: per-host status + all blackbox probes.

    Returns the raw host/probe data plus a ``problems`` rollup and a ``healthy``
    flag, so callers can decide whether to alert. Purely Prometheus-backed (no
    GPU/Ollama), so it's cheap enough to run frequently.
    """
    config = config or Config.load()
    hosts = await get_host_status(None, config=config)
    if isinstance(hosts, dict):  # defensive: get_host_status(None) returns a list
        hosts = [hosts]
    probes = await get_probe_status(config=config)
    # probe_status returns a single {"note": ...} row when blackbox isn't scraped.
    probes_ok = [p for p in probes if "alive" in p]

    unreachable = [h["host"] for h in hosts if not h.get("reachable")]
    down_endpoints = [p["target"] for p in probes_ok if p.get("alive") is False]

    return {
        "hosts": hosts,
        "probes": probes,
        "problems": {
            "unreachable_hosts": unreachable,
            "down_endpoints": down_endpoints,
        },
        "counts": {
            "hosts_total": len(hosts),
            "hosts_reachable": len(hosts) - len(unreachable),
            "probes_total": len(probes_ok),
            "probes_up": len(probes_ok) - len(down_endpoints),
        },
        "healthy": not unreachable and not down_endpoints,
    }
