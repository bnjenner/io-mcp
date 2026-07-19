"""ntfy push-notification client.

ntfy encodes metadata (title, priority, tags, markdown) as HTTP headers and
takes the message as the request body.
See https://docs.ntfy.sh/publish/ for the header contract.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0


class NtfyClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8090",
        default_topic: str = "io-mcp",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_topic = default_topic
        self.timeout = timeout

    async def send(
        self,
        message: str,
        topic: str | None = None,
        title: str | None = None,
        priority: int = 3,
        tags: list[str] | None = None,
        markdown: bool = True,
    ) -> bool:
        """POST to ntfy. Returns True on success, False on delivery failure.

        Delivery failures are logged, never raised, so a failed notification
        does not crash the surrounding pipeline.
        """
        topic = topic or self.default_topic
        url = f"{self.base_url}/{topic}"
        # ntfy priority header must be 1..5.
        priority = min(5, max(1, int(priority)))
        headers: dict[str, str] = {"Priority": str(priority)}
        if title:
            headers["Title"] = title
        if tags:
            headers["Tags"] = ",".join(tags)
        if markdown:
            headers["Markdown"] = "yes"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url, content=message.encode("utf-8"), headers=headers
                )
                resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("ntfy delivery to %s failed: %s", url, exc)
            return False
