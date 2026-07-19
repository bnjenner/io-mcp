# Deploying io-mcp

Target: **Fedora** (or any systemd Linux). The installer supports a stdlib
**venv** (default) or **conda**.

## Quick start

```bash
git clone <your-remote> io-mcp
cd io-mcp

# venv (default). Add --system-deps to dnf-install python3/pip/git first.
./deploy/install.sh --systemd --timer

# …or use conda instead of a venv:
./deploy/install.sh --method conda --env-name io-mcp --systemd --timer
```

Then edit `~/.config/io-mcp/config.yaml` (Ollama / ntfy / Prometheus URLs and
your research interests) and restart the server:

```bash
systemctl --user restart io-mcp.service
```

## What the installer does

1. (Optional `--system-deps`) `sudo dnf install -y python3 python3-pip git`.
2. Creates an isolated environment:
   - `venv` → `<repo>/.venv`
   - `conda` → env named by `--env-name` (default `io-mcp`, `python=3.11`)
3. Installs io-mcp **editable** (`pip install -e .`). Editable is required so the
   repo-root `prompts/` directory resolves at runtime.
4. Runs `io-mcp init` → writes `~/.config/io-mcp/config.yaml` and
   `~/.local/share/io-mcp/state.db`.
5. (`--systemd`) installs + enables the MCP server user unit.
6. (`--timer`) installs + enables the nightly digest timer (06:00 daily).

## Options

| Flag | Meaning |
|---|---|
| `--method venv\|conda` | Environment backend (default `venv`) |
| `--venv-dir PATH` | venv location (default `<repo>/.venv`) |
| `--env-name NAME` | conda env name (default `io-mcp`) |
| `--python VERSION` | Python for the conda env (default `3.11`) |
| `--dev` | Also install dev extras (pytest, ruff) |
| `--system-deps` | `dnf install` python3/pip/git first |
| `--systemd` | Install + enable the MCP server user unit |
| `--timer` | Install + enable the nightly digest timer |
| `--no-init` | Skip creating config / state DB |

## systemd (user units)

The server and digest run as **user** units under `~/.config/systemd/user/`:

```bash
systemctl --user status io-mcp.service
systemctl --user list-timers io-mcp-digest.timer
journalctl --user -u io-mcp.service -f
```

To keep them running while you're logged out:

```bash
loginctl enable-linger "$USER"
```

Prefer cron? See `deploy/crontab.example` instead of `--timer`.

## External services (not installed here)

io-mcp talks to services you run separately:

- **Ollama** — `dnf`/official installer; pull models, e.g. `ollama pull mistral:7b`.
- **ntfy** — self-hosted or ntfy.sh; set `ntfy.base_url` + topics.
- **Prometheus + node_exporter** — for `host_status` / `query_prometheus`.
  Service status needs node_exporter's `--collector.systemd`.

## Open WebUI

Admin Settings → External Tools → **+ Add Server** → type **MCP (Streamable
HTTP)** → URL `http://<host>:8484/mcp`. Tools appear prefixed with the server
name.

## Firewalld (if connecting from another host)

By default the server binds `127.0.0.1`. To expose it on the LAN, set
`server.host: 0.0.0.0` in config and open the port:

```bash
sudo firewall-cmd --add-port=8484/tcp --permanent
sudo firewall-cmd --reload
```

## Updating

```bash
cd io-mcp && git pull
# editable install picks up code changes automatically; just restart:
systemctl --user restart io-mcp.service
```
