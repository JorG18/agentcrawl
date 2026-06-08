from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


class AgentCrawlWebProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "agentcrawl"

    @property
    def display_name(self) -> str:
        return "AgentCrawl (self-hosted)"

    def is_available(self) -> bool:
        return bool(os.getenv("AGENTCRAWL_BASE_URL", "").strip()) and bool(
            os.getenv("AGENTCRAWL_API_KEY", "").strip()
        )

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        requested_format = kwargs.get("format")
        formats = ["markdown", "metadata"]
        if requested_format == "html":
            formats = ["html", "metadata"]

        tasks = [asyncio.to_thread(self._extract_one, url, formats) for url in urls]
        return list(await asyncio.gather(*tasks))

    def _extract_one(self, url: str, formats: List[str]) -> Dict[str, Any]:
        base_url = os.getenv("AGENTCRAWL_BASE_URL", "").strip().rstrip("/")
        api_key = os.getenv("AGENTCRAWL_API_KEY", "").strip()
        if not base_url or not api_key:
            return self._error(url, "AgentCrawl URL or API key is not configured")

        payload = json.dumps(
            {"url": url, "formats": formats, "only_main_content": True}
        ).encode("utf-8")
        request = urllib.request.Request(
            base_url + "/v1/scrape",
            data=payload,
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return self._error(url, f"AgentCrawl HTTP {exc.code}: {detail[:500]}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentCrawl extraction failed for %s: %s", url, exc)
            return self._error(url, f"AgentCrawl extraction failed: {exc}")

        data = body.get("data") if isinstance(body, dict) else None
        if not body.get("success") or not isinstance(data, dict):
            errors = data.get("errors", []) if isinstance(data, dict) else []
            detail = "; ".join(str(item) for item in errors) or "unknown error"
            return self._error(url, f"AgentCrawl extraction failed: {detail}")

        metadata = dict(data.get("metadata") or {})
        source_url = data.get("url") or url
        title = metadata.get("title", "")
        content = data.get("html") if "html" in formats else data.get("markdown")
        content = content or data.get("text") or ""
        metadata.setdefault("sourceURL", source_url)
        metadata.setdefault("title", title)
        metadata["provider"] = "agentcrawl"
        return {
            "url": source_url,
            "title": title,
            "content": content,
            "raw_content": content,
            "metadata": metadata,
        }

    @staticmethod
    def _error(url: str, message: str) -> Dict[str, Any]:
        return {
            "url": url,
            "title": "",
            "content": "",
            "raw_content": "",
            "error": message,
            "metadata": {"sourceURL": url, "provider": "agentcrawl"},
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "self-hosted",
            "tag": "Local/VPS AgentCrawl extraction with no hosted scraping fee.",
            "env_vars": [
                {"key": "AGENTCRAWL_BASE_URL", "prompt": "AgentCrawl API URL"},
                {"key": "AGENTCRAWL_API_KEY", "prompt": "AgentCrawl API key"},
            ],
        }
