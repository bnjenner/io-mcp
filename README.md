# io-mcp

**Local Research & Homelab Assistant Toolkit** — a Python tool suite that
automates research workflows (paper discovery, relevance scoring, digests) and
homelab monitoring (Prometheus, logs), exposed through an **MCP server** for
Open WebUI and a **CLI** for cron/systemd jobs. Both frontends share one core
library. LLM inference is local via **Ollama** — no cloud API dependencies.

## Features

- **Paper digests** — query arXiv + Semantic Scholar for your interests, score
  relevance with a local model, and push a digest to **ntfy**.
- **Homelab status** — structured host health and raw PromQL against Prometheus.
- **Log summaries** — journald logs summarized by a local model.
- **Two frontends, one core** — MCP (Streamable HTTP) for Open WebUI, CLI for cron.

## Install

Target is **Fedora** / any systemd Linux. One script sets up an isolated
environment (venv or conda), installs io-mcp, and (optionally) systemd units:

```bash
git clone <your-remote> io-mcp && cd io-mcp
./deploy/install.sh --systemd --timer            # venv
# or: ./deploy/install.sh --method conda --systemd --timer
```

Full deployment details, options, and external-service setup:
[`deploy/README.md`](deploy/README.md).

### Manual install (development)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'      # editable is required (prompts/ resolves at runtime)
io-mcp init                  # writes ~/.config/io-mcp/config.yaml + state.db
```

## Configuration

Single YAML file at `~/.config/io-mcp/config.yaml` (see
[`config.example.yaml`](config.example.yaml)). Any value can be overridden by an
environment variable with the `IO_MCP_` prefix and `__` nesting:

```bash
IO_MCP_OLLAMA__BASE_URL=http://localhost:11434
IO_MCP_NTFY__BASE_URL=http://10.0.0.88
IO_MCP_SERVER__PORT=8484
```

`io-mcp config` prints the fully resolved configuration.

### Semantic Scholar API key (optional)

Paper discovery uses arXiv + Semantic Scholar. The **unauthenticated** Semantic
Scholar API is a shared, heavily throttled pool (HTTP 429); discovery retries
with backoff and, if it stays throttled, **degrades gracefully to arXiv alone**.
For reliable Semantic Scholar results, get a [free API key](https://www.semanticscholar.org/product/api#api-key)
and expose it in the environment where the digest runs (it is never stored in
config):

```bash
export SEMANTIC_SCHOLAR_API_KEY=your-key-here
```

For the systemd timer, uncomment the `Environment=` line in
[`deploy/io-mcp-digest.service`](deploy/io-mcp-digest.service).

## CLI

```
io-mcp digest              Run discovery → score → digest → notify (ntfy)
  --dry-run                Show results without notifying
  --since YYYY-MM-DD       Override lookback date
  --interests NAME         Restrict to specific interest group(s)
  --prune-days N           Prune seen papers older than N days after the run
io-mcp search QUERY        One-off paper search (--source arxiv|s2|both, --limit N, --score)
io-mcp status              Homelab host health (--host NAME, --json)
io-mcp query PROMQL        Raw PromQL query
io-mcp logs                Summarize logs (--host, --unit, --since, --priority)
io-mcp serve               Start the MCP server (--host, --port)
io-mcp config              Print resolved configuration
io-mcp init                Create default config + state database
```

## MCP server

```bash
io-mcp serve      # binds server.host:server.port from config, path /mcp
```

Connect in Open WebUI: Admin Settings → External Tools → **+ Add Server** →
**MCP (Streamable HTTP)** → `http://127.0.0.1:8484/mcp`. Exposed tools:
`search_papers`, `score_paper`, `run_digest`, `recent_papers`, `host_status`,
`query_prometheus`, `summarize_logs`, `list_tools`.

`list_tools` is a self-describing helper: some clients (e.g. Open WebUI) collapse
the whole server into a single `io-mcp` entry, so the agent can call `list_tools`
to discover the individual tools with their descriptions and parameters.

## Development

```bash
pip install -e '.[dev]'
pytest          # all external services are mocked; no network needed
ruff check .
```

## Project layout

```
io_mcp/            core library (config, state, ollama, notify) + tools/ + server + cli
prompts/           system prompts (loaded at runtime; keep with the repo)
deploy/            install.sh, systemd units, crontab example, deployment guide
tests/             pytest suite (mocked APIs, in-memory SQLite)
```

## Requirements

- Python ≥ 3.11
- [Ollama](https://ollama.com) with your chosen models pulled
- ntfy (self-hosted or ntfy.sh) for digest notifications
- Prometheus + node_exporter for homelab tools (optional)
