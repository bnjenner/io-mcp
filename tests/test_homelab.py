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


async def test_get_service_status_no_metrics(monkeypatch, config):
    async def fake_query(query, config=None):
        return _empty()

    monkeypatch.setattr(homelab_mod, "query_prometheus", fake_query)
    result = await homelab_mod.get_service_status("baphomech", config=config)
    assert "note" in result[0]
