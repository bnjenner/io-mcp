"""Configuration loading for io-mcp.

Config is a single YAML file (default: ``~/.config/io-mcp/config.yaml``) with
environment-variable overrides using the ``IO_MCP_`` prefix and double-underscore
nesting, e.g. ``IO_MCP_OLLAMA__BASE_URL=http://localhost:11434``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "IO_MCP_"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "io-mcp" / "config.yaml"
# Prompt files live at the repo root (prompts/), one level above the package.
# A packaged copy under io_mcp/prompts/ is checked as a fallback so a non-editable
# install still works if that directory is shipped.
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PROMPT_SEARCH = [
    PROMPTS_DIR,
    Path(__file__).resolve().parent / "prompts",
]


def load_prompt(name: str) -> str:
    """Load a system prompt from the ``prompts/`` directory by basename.

    ``name`` may be given with or without the ``.txt`` extension.
    """
    if not name.endswith(".txt"):
        name = f"{name}.txt"
    for directory in _PROMPT_SEARCH:
        candidate = directory / name
        if candidate.exists():
            return candidate.read_text()
    searched = ", ".join(str(d) for d in _PROMPT_SEARCH)
    raise FileNotFoundError(
        f"Prompt {name!r} not found (searched: {searched}). "
        "io-mcp must be installed editable (pip install -e .) so the repo-root "
        "prompts/ directory is reachable — see deploy/install.sh."
    )


# --------------------------------------------------------------------------- #
# Structured config sections
# --------------------------------------------------------------------------- #
@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    default_model: str = "mistral:7b"
    models: dict[str, str] = field(default_factory=dict)

    def model_for(self, tool: str | None = None) -> str:
        """Return the model configured for a tool, falling back to the default."""
        if tool and tool in self.models:
            return self.models[tool]
        return self.default_model


@dataclass
class NtfyConfig:
    base_url: str = "http://localhost:8090"
    default_topic: str = "io-mcp"
    topics: dict[str, str] = field(default_factory=dict)

    def topic_for(self, name: str | None = None) -> str:
        if name and name in self.topics:
            return self.topics[name]
        return self.default_topic


@dataclass
class InterestConfig:
    name: str
    arxiv_query: str | None = None
    semantic_scholar_query: str | None = None


@dataclass
class ResearchConfig:
    interests: list[InterestConfig] = field(default_factory=list)
    relevance_threshold: int = 3
    max_papers_per_digest: int = 20
    context: str = ""


@dataclass
class PrometheusConfig:
    base_url: str = "http://localhost:9090"


@dataclass
class HostConfig:
    name: str
    role: str = ""


@dataclass
class HomelabConfig:
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    hosts: list[HostConfig] = field(default_factory=list)

    def host_names(self) -> list[str]:
        return [h.name for h in self.hosts]


@dataclass
class StateConfig:
    db_path: str = "~/.local/share/io-mcp/state.db"

    @property
    def resolved_path(self) -> Path:
        return Path(os.path.expanduser(self.db_path))


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8484


@dataclass
class Config:
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    homelab: HomelabConfig = field(default_factory=HomelabConfig)
    state: StateConfig = field(default_factory=StateConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    raw: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------------------- #
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> Config:
        """Load config from YAML (if present) and apply env-var overrides."""
        cfg_path = _resolve_config_path(path)
        data: dict[str, Any] = {}
        if cfg_path and cfg_path.exists():
            with open(cfg_path) as fh:
                data = yaml.safe_load(fh) or {}
        data = _apply_env_overrides(data, os.environ)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        ollama_d = data.get("ollama", {}) or {}
        ntfy_d = data.get("ntfy", {}) or {}
        research_d = data.get("research", {}) or {}
        homelab_d = data.get("homelab", {}) or {}
        state_d = data.get("state", {}) or {}
        server_d = data.get("server", {}) or {}

        ollama = OllamaConfig(
            base_url=ollama_d.get("base_url", OllamaConfig.base_url),
            default_model=ollama_d.get("default_model", OllamaConfig.default_model),
            models=dict(ollama_d.get("models", {}) or {}),
        )
        ntfy = NtfyConfig(
            base_url=ntfy_d.get("base_url", NtfyConfig.base_url),
            default_topic=ntfy_d.get("default_topic", NtfyConfig.default_topic),
            topics=dict(ntfy_d.get("topics", {}) or {}),
        )
        research = ResearchConfig(
            interests=[
                InterestConfig(
                    name=i.get("name", "unnamed"),
                    arxiv_query=i.get("arxiv_query"),
                    semantic_scholar_query=i.get("semantic_scholar_query"),
                )
                for i in (research_d.get("interests", []) or [])
            ],
            relevance_threshold=int(research_d.get("relevance_threshold", 3)),
            max_papers_per_digest=int(research_d.get("max_papers_per_digest", 20)),
            context=research_d.get("context", "") or "",
        )
        prom_d = homelab_d.get("prometheus", {}) or {}
        homelab = HomelabConfig(
            prometheus=PrometheusConfig(
                base_url=prom_d.get("base_url", PrometheusConfig.base_url)
            ),
            hosts=[
                HostConfig(name=h.get("name", ""), role=h.get("role", ""))
                for h in (homelab_d.get("hosts", []) or [])
            ],
        )
        state = StateConfig(db_path=state_d.get("db_path", StateConfig.db_path))
        server = ServerConfig(
            host=server_d.get("host", ServerConfig.host),
            port=int(server_d.get("port", ServerConfig.port)),
        )
        return cls(
            ollama=ollama,
            ntfy=ntfy,
            research=research,
            homelab=homelab,
            state=state,
            server=server,
            raw=data,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _resolve_config_path(path: str | os.PathLike | None) -> Path | None:
    if path:
        return Path(os.path.expanduser(str(path)))
    env_path = os.environ.get(f"{ENV_PREFIX}CONFIG")
    if env_path:
        return Path(os.path.expanduser(env_path))
    return DEFAULT_CONFIG_PATH


def _coerce(value: str) -> Any:
    """Best-effort coercion of an env-var string to int/float/bool."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def _apply_env_overrides(data: dict[str, Any], environ: dict[str, str]) -> dict[str, Any]:
    """Return a copy of ``data`` with ``IO_MCP_``-prefixed overrides merged in.

    ``IO_MCP_SERVER__PORT=8484`` sets ``data["server"]["port"] = 8484``.
    The reserved key ``IO_MCP_CONFIG`` (config file location) is ignored here.
    """
    result = _deepcopy_dict(data)
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        remainder = key[len(ENV_PREFIX):]
        if remainder == "CONFIG":
            continue
        parts = [p.lower() for p in remainder.split("__") if p]
        if not parts:
            continue
        node = result
        for part in parts[:-1]:
            existing = node.get(part)
            if not isinstance(existing, dict):
                existing = {}
                node[part] = existing
            node = existing
        node[parts[-1]] = _coerce(value)
    return result


def _deepcopy_dict(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        out[k] = _deepcopy_dict(v) if isinstance(v, dict) else v
    return out


def default_config_yaml() -> str:
    """Return the packaged example config as a string (used by ``io-mcp init``)."""
    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    if example.exists():
        return example.read_text()
    return ""
