"""Homelab / Prometheus tests with mocked query responses."""

from __future__ import annotations

from io_mcp.tools import homelab as homelab_mod


def _vector(value):
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, str(value)]}]},
    }


def _empty():
    return {"status": "success", "data": {"resultType": "vector", "result": []}}


def test_scalar_extraction():
    assert homelab_mod._scalar(_vector(42.5)) == 42.5
    assert homelab_mod._scalar(_empty()) is None


def test_instance_matcher():
    m = homelab_mod._instance_matcher("baphomech")
    assert "baphomech" in m
    assert m.endswith("(:.*)?")


def test_instance_matcher_escapes_dots():
    # A literal dot in a PromQL =~ regex lives inside a double-quoted string, so
    # it must be written as \\. — the backslash is escaped in the string literal.
    # (A single \. is rejected by Prometheus: "unknown escape sequence".)
    assert homelab_mod._instance_matcher("node.io.lan") == r"node\\.io\\.lan(:.*)?"


async def test_get_host_status_single(monkeypatch, config):
    responses = {
        "up": _vector(1),
        "cpu": _vector(12.5),
        "mem": _vector(40.0),
    }

    async def fake_query(query, config=None):
        if query.startswith("up"):
            return responses["up"]
        if "cpu" in query:
            return responses["cpu"]
        if "Available" in query or "Mem" in query:
            return responses["mem"]
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    status = await homelab_mod.get_host_status("baphomech", config=config)
    assert status["host"] == "baphomech"
    assert status["reachable"] is True
    assert status["up"] == 1.0
    assert status["cpu_usage_pct"] == 12.5


async def test_get_host_status_all(monkeypatch, config):
    async def fake_query(query, config=None):
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    result = await homelab_mod.get_host_status(None, config=config)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["host"] == "baphomech"
    assert result[0]["reachable"] is False


def _labeled_vector(*items):
    """Build a Prometheus vector from (labels, value) pairs."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": lbls, "value": [0, str(v)]} for lbls, v in items],
        },
    }


async def test_get_probe_status_rolls_up_and_sorts_down_first(monkeypatch, config):
    graf = {"instance": "http://grafana.lan", "job": "blackbox"}
    nas = {"instance": "http://nas.lan", "job": "blackbox"}

    async def fake_query(query, config=None):
        if query == "probe_success":
            return _labeled_vector((graf, 1), (nas, 0))
        if query == "probe_duration_seconds":
            return _labeled_vector((graf, 0.12), (nas, 5.0))
        if query == "probe_http_status_code":
            return _labeled_vector((graf, 200), (nas, 0))
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    rows = await homelab_mod.get_probe_status(config=config)
    assert len(rows) == 2
    # Down endpoint sorts first.
    assert rows[0]["target"] == "http://nas.lan"
    assert rows[0]["alive"] is False
    graf_row = next(r for r in rows if r["target"] == "http://grafana.lan")
    assert graf_row["alive"] is True
    assert graf_row["http_code"] == 200
    assert graf_row["duration_s"] == 0.12


async def test_get_probe_status_filters_by_target(monkeypatch, config):
    graf = {"instance": "http://grafana.lan", "job": "blackbox"}
    nas = {"instance": "http://nas.lan", "job": "blackbox"}

    async def fake_query(query, config=None):
        if query == "probe_success":
            return _labeled_vector((graf, 1), (nas, 0))
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    rows = await homelab_mod.get_probe_status("grafana", config=config)
    assert [r["target"] for r in rows] == ["http://grafana.lan"]


async def test_get_probe_status_no_metrics(monkeypatch, config):
    async def fake_query(query, config=None):
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    rows = await homelab_mod.get_probe_status(config=config)
    assert "note" in rows[0]


async def test_get_service_status_no_metrics(monkeypatch, config):
    async def fake_query(query, config=None):
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    result = await homelab_mod.get_service_status("baphomech", config=config)
    assert "note" in result[0]


async def test_get_homelab_overview_rolls_up_problems(monkeypatch, config):
    async def fake_host_status(hostname, config=None):
        return [
            {"host": "baphomech", "reachable": True, "cpu_usage_pct": 10.0},
            {"host": "daemon", "reachable": False},
        ]

    async def fake_probe_status(target=None, config=None):
        return [
            {"target": "http://nas.lan", "alive": False, "job": "blackbox"},
            {"target": "http://grafana.lan", "alive": True, "job": "blackbox"},
        ]

    monkeypatch.setattr(homelab_mod, "get_host_status", fake_host_status)
    monkeypatch.setattr(homelab_mod, "get_probe_status", fake_probe_status)
    ov = await homelab_mod.get_homelab_overview(config=config)
    assert ov["healthy"] is False
    assert ov["problems"]["unreachable_hosts"] == ["daemon"]
    assert ov["problems"]["down_endpoints"] == ["http://nas.lan"]
    assert ov["counts"] == {
        "hosts_total": 2, "hosts_reachable": 1,
        "probes_total": 2, "probes_up": 1,
    }


async def test_get_homelab_overview_healthy_ignores_probe_note(monkeypatch, config):
    # When blackbox isn't scraped, probe_status returns a single {"note": ...} row
    # with no "alive" key — it must not be counted as a down endpoint.
    async def fake_host_status(hostname, config=None):
        return [{"host": "baphomech", "reachable": True}]

    async def fake_probe_status(target=None, config=None):
        return [{"note": "No probe_success metrics found"}]

    monkeypatch.setattr(homelab_mod, "get_host_status", fake_host_status)
    monkeypatch.setattr(homelab_mod, "get_probe_status", fake_probe_status)
    ov = await homelab_mod.get_homelab_overview(config=config)
    assert ov["healthy"] is True
    assert ov["counts"]["probes_total"] == 0
