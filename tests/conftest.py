"""Shared fixtures for the io-mcp test suite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from io_mcp.config import Config
from io_mcp.tools import Paper


class FakeOllama:
    """Stand-in for OllamaClient that returns canned responses."""

    def __init__(self, json_response=None, text_response="summary text"):
        self.json_response = json_response or {"score": 4, "rationale": "relevant"}
        self.text_response = text_response
        self.calls = []

    async def generate(self, prompt, model=None, system=None, format=None):
        self.calls.append(("generate", prompt, model, system, format))
        return self.text_response

    async def generate_json(self, prompt, model=None, system=None):
        self.calls.append(("generate_json", prompt, model, system))
        return self.json_response

    async def chat(self, messages, model=None):
        self.calls.append(("chat", messages, model))
        return self.text_response


@pytest.fixture
def fake_ollama():
    return FakeOllama()


@pytest.fixture
def config():
    return Config.from_dict(
        {
            "research": {
                "relevance_threshold": 3,
                "max_papers_per_digest": 20,
                "context": "test researcher context",
                "interests": [
                    {
                        "name": "Test interest",
                        "arxiv_query": "cat:q-bio.MN",
                        "semantic_scholar_query": "gene regulation",
                    }
                ],
            },
            "homelab": {
                "prometheus": {"base_url": "http://prom:9090"},
                "hosts": [{"name": "baphomech", "role": "workstation"}],
            },
        }
    )


@pytest.fixture
def sample_paper():
    return Paper(
        id="2505.08764",
        title="A thermodynamic model of promoter activity",
        authors=["A. Researcher", "B. Scientist"],
        abstract="We present a statistical mechanical model of transcription.",
        categories=["q-bio.MN"],
        published=datetime(2025, 5, 13, tzinfo=UTC),
        updated=datetime(2025, 5, 13, tzinfo=UTC),
        source="arxiv",
    )
