"""Log parsing / summarization.

v1 fetches logs via ``journalctl`` — locally when ``host`` is localhost, or over
SSH otherwise (best-effort; assumes key-based SSH). The raw JSON lines are
truncated to a context-window-friendly size and summarized by Ollama using the
``log_summary`` prompt. Loki / remote aggregation is a v2 extension point.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC

from io_mcp.config import Config, load_prompt
from io_mcp.ollama import OllamaClient

log = logging.getLogger(__name__)

LOG_PROMPT_NAME = "log_summary"
MAX_LOG_CHARS = 12000  # rough truncation to keep within a small model's context
LOCAL_HOSTS = {"localhost", "127.0.0.1", ""}


async def summarize_journal_logs(
    host: str = "localhost",
    unit: str | None = None,
    since: str = "1h",
    priority: str | None = None,
    *,
    config: Config | None = None,
    ollama: OllamaClient | None = None,
) -> str:
    """Fetch recent journald logs for a host/unit and return an LLM summary."""
    config = config or Config.load()
    if ollama is None:
        ollama = OllamaClient(
            base_url=config.ollama.base_url, default_model=config.ollama.default_model
        )

    raw = await _fetch_journal(host, unit=unit, since=since, priority=priority)
    if not raw.strip():
        return f"No log entries for host={host} unit={unit or 'all'} since={since}."

    entries = _format_entries(raw)
    if len(entries) > MAX_LOG_CHARS:
        entries = entries[-MAX_LOG_CHARS:]  # keep the most recent tail

    system = load_prompt(LOG_PROMPT_NAME)
    model = config.ollama.model_for("log_parsing")
    prompt = (
        f"Host: {host}\nUnit: {unit or 'all'}\nSince: {since}\n\n"
        f"Logs:\n{entries}"
    )
    return await ollama.generate(prompt, model=model, system=system)


def _build_journalctl_cmd(
    host: str, unit: str | None, since: str, priority: str | None
) -> list[str]:
    cmd = ["journalctl", "--no-pager", "--output=json", f"--since={since}"]
    if unit:
        cmd += ["-u", unit]
    if priority:
        cmd += ["-p", priority]
    if host.lower() not in LOCAL_HOSTS:
        # Remote: run journalctl over SSH. Quote args for the remote shell.
        remote = " ".join(_shquote(a) for a in cmd)
        return ["ssh", host, remote]
    return cmd


async def _fetch_journal(
    host: str, unit: str | None, since: str, priority: str | None
) -> str:
    import asyncio

    cmd = _build_journalctl_cmd(host, unit, since, priority)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"journalctl failed (exit {proc.returncode}): {err}")
    return stdout.decode(errors="replace")


def _format_entries(raw: str) -> str:
    """Render journalctl JSON lines into compact ``ts host unit: message`` lines."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            lines.append(line)
            continue
        ts = obj.get("__REALTIME_TIMESTAMP")
        if ts:
            try:
                ts = _usec_to_iso(int(ts))
            except (ValueError, TypeError):
                pass
        unit = obj.get("_SYSTEMD_UNIT") or obj.get("SYSLOG_IDENTIFIER") or ""
        msg = obj.get("MESSAGE", "")
        if isinstance(msg, list):  # journald can encode MESSAGE as a byte array
            msg = "".join(chr(b) for b in msg if isinstance(b, int))
        lines.append(f"{ts or ''} {unit}: {msg}".strip())
    return "\n".join(lines)


def _usec_to_iso(usec: int) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(usec / 1_000_000, tz=UTC).isoformat()


def _shquote(s: str) -> str:
    import shlex

    return shlex.quote(s)
