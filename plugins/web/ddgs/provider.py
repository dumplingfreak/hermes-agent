"""DuckDuckGo search — plugin form (via the ``ddgs`` package).

Subclasses the plugin-facing :class:`agent.web_search_provider.WebSearchProvider`.
The legacy in-tree module ``tools.web_providers.ddgs`` was removed in the
same commit that moved this code under ``plugins/``; this file is now the
canonical implementation.

The ``ddgs`` package is an optional dependency. ``is_available()`` reflects
whether the package is importable; the plugin still registers either way so
``hermes tools`` can prompt the user to install it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


class DDGSWebSearchProvider(WebSearchProvider):
    """DuckDuckGo HTML-scrape search provider.

    No API key needed. Rate limits are enforced server-side by DuckDuckGo;
    the provider surfaces ``DuckDuckGoSearchException`` and other ddgs errors
    as ``{"success": False, "error": ...}`` rather than raising.
    """

    @property
    def name(self) -> str:
        return "ddgs"

    @property
    def display_name(self) -> str:
        return "DuckDuckGo (ddgs)"

    def is_available(self) -> bool:
        """Return True when the ``ddgs`` package is importable.

        Probes the import once; cheap because Python caches the import. Must
        NOT perform network I/O — runs at tool-registration time and on every
        ``hermes tools`` paint.
        """
        try:
            import ddgs  # noqa: F401

            return True
        except ImportError:
            try:
                from tools.lazy_deps import ensure

                ensure("search.ddgs")
                import ddgs  # noqa: F401

                return True
            except Exception:
                return False

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: list[str], **kwargs: Any) -> list[Dict[str, Any]]:
        """Extract readable content from one or more URLs using ddgs.extract."""
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError:
            return [
                {
                    "url": str(url),
                    "title": "",
                    "content": "",
                    "raw_content": "",
                    "error": "ddgs package is not installed - run `uv pip install ddgs`",
                }
                for url in urls
            ]

        results: list[Dict[str, Any]] = []
        try:
            with DDGS() as client:
                for url in urls:
                    try:
                        hit = client.extract(str(url))
                        content = str(hit.get("content") or hit.get("text") or "") if isinstance(hit, dict) else str(hit or "")
                        title = str(hit.get("title") or "") if isinstance(hit, dict) else ""
                        results.append({
                            "url": str(url),
                            "title": title,
                            "content": content,
                            "raw_content": content,
                            "metadata": {"provider": "ddgs"},
                        })
                    except Exception as exc:  # noqa: BLE001
                        results.append({
                            "url": str(url),
                            "title": "",
                            "content": "",
                            "raw_content": "",
                            "error": f"DDGS extract failed: {exc}",
                        })
        except Exception as exc:  # noqa: BLE001
            return [
                {
                    "url": str(url),
                    "title": "",
                    "content": "",
                    "raw_content": "",
                    "error": f"DDGS extract failed: {exc}",
                }
                for url in urls
            ]

        logger.info("DDGS extract: %d URL(s)", len(results))
        return results

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a DuckDuckGo search and return normalized results."""
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError:
            return {
                "success": False,
                "error": "ddgs package is not installed — run `pip install ddgs`",
            }

        # DDGS().text yields at most `max_results` items; we cap defensively
        # in case the package ignores the hint.
        safe_limit = max(1, int(limit))

        try:
            web_results = []
            with DDGS() as client:
                for i, hit in enumerate(client.text(query, max_results=safe_limit)):
                    if i >= safe_limit:
                        break
                    url = str(hit.get("href") or hit.get("url") or "")
                    web_results.append(
                        {
                            "title": str(hit.get("title", "")),
                            "url": url,
                            "description": str(hit.get("body", "")),
                            "position": i + 1,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 — ddgs raises its own exceptions
            logger.warning("DDGS search error: %s", exc)
            return {"success": False, "error": f"DuckDuckGo search failed: {exc}"}

        logger.info("DDGS search '%s': %d results (limit %d)", query, len(web_results), limit)
        return {"success": True, "data": {"web": web_results}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "DuckDuckGo (ddgs)",
            "badge": "free · no key · search only",
            "tag": "Search via the ddgs Python package — no API key (pair with any extract provider)",
            "env_vars": [],
            # Trigger `_run_post_setup("ddgs")` after the user picks this row
            # so the ddgs Python package gets pip-installed on first selection.
            "post_setup": "ddgs",
        }
