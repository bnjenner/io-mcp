"""Homelab digest tests — formatting and notify-on-problems gating."""

from __future__ import annotations

from io_mcp.tools import homelab as homelab_mod
from io_mcp.tools import homelab_digest


class FakeNtfy:
    def __init__(self):
        self.sent = []

    async def send(self, message, topic=None, title=None, priority=3, tags=None, markdown=True):
        self.sent.append({"message": message, "title": title, "topic": topic, "priority": priority})
        return True


def _healthy_overview():
    return {
        "hosts": [{"host": "baphomech", "reachable": True, "cpu_usage_pct": 10.0,
                   "mem_used_pct": 40.0, "disk_avail_bytes": 5.0e11, "load1": 0.3}],
        "probes": [{"target": "http://grafana.lan", "alive": True}],
        "problems": {"unreachable_hosts": [], "down_endpoints": []},
        "counts": {"hosts_total": 1, "hosts_reachable": 1, "probes_total": 1, "probes_up": 1},
        "healthy": True,
    }


def _problem_overview():
    ov = _healthy_overview()
    ov["hosts"].append({"host": "daemon", "reachable": False})
    ov["problems"] = {"unreachable_hosts": ["daemon"], "down_endpoints": ["http://nas.lan"]}
    ov["counts"] = {"hosts_total": 2, "hosts_reachable": 1, "probes_total": 1, "probes_up": 0}
    ov["probes"] = [{"target": "http://nas.lan", "alive": False}]
    ov["healthy"] = False
    return ov


def test_format_overview_healthy():
    md = homelab_digest.format_overview(_healthy_overview())
    assert "✅ Homelab healthy" in md
    assert "baphomech" in md
    assert "1/1 up" in md
    # Human-readable byte formatting.
    assert "GB free" in md


def test_format_overview_lists_problems():
    md = homelab_digest.format_overview(_problem_overview())
    assert "⚠️ Homelab problems" in md
    assert "Host unreachable: daemon" in md
    assert "Endpoint down: http://nas.lan" in md


async def test_run_digest_notifies_on_problems(monkeypatch, config):
    fake = FakeNtfy()

    async def fake_overview(*, config=None):
        return _problem_overview()

    monkeypatch.setattr(homelab_mod, "get_homelab_overview", fake_overview)
    monkeypatch.setattr(homelab_digest, "NtfyClient", lambda **kw: fake)

    result = await homelab_digest.run_homelab_digest(config=config)
    assert result["delivered"] is True
    assert result["healthy"] is False
    assert len(fake.sent) == 1
    assert "2 problem(s)" in fake.sent[0]["title"]
    assert fake.sent[0]["priority"] == 4


async def test_run_digest_silent_when_healthy(monkeypatch, config):
    fake = FakeNtfy()

    async def fake_overview(*, config=None):
        return _healthy_overview()

    monkeypatch.setattr(homelab_mod, "get_homelab_overview", fake_overview)
    monkeypatch.setattr(homelab_digest, "NtfyClient", lambda **kw: fake)

    result = await homelab_digest.run_homelab_digest(config=config)
    assert result["delivered"] is False
    assert fake.sent == []  # healthy → no notification by default


async def test_run_digest_always_notify_when_healthy(monkeypatch, config):
    fake = FakeNtfy()

    async def fake_overview(*, config=None):
        return _healthy_overview()

    monkeypatch.setattr(homelab_mod, "get_homelab_overview", fake_overview)
    monkeypatch.setattr(homelab_digest, "NtfyClient", lambda **kw: fake)

    result = await homelab_digest.run_homelab_digest(always_notify=True, config=config)
    assert result["delivered"] is True
    assert len(fake.sent) == 1
    assert "all healthy" in fake.sent[0]["title"]


async def test_run_digest_dry_run_never_sends(monkeypatch, config):
    fake = FakeNtfy()

    async def fake_overview(*, config=None):
        return _problem_overview()

    monkeypatch.setattr(homelab_mod, "get_homelab_overview", fake_overview)
    monkeypatch.setattr(homelab_digest, "NtfyClient", lambda **kw: fake)

    result = await homelab_digest.run_homelab_digest(dry_run=True, config=config)
    assert result["delivered"] is False
    assert fake.sent == []
