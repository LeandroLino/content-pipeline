import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from app.schemas import IngestPayload, Source

# Medium blocks datacenter/VPS IPs and intermittently serves an empty JS-only
# app-shell even to browser-like requests (non-deterministic, depends on
# Cloudflare cache state). We fetch articles via the Freedium mirror instead,
# which exposes a stable SvelteKit JSON endpoint with the rendered content.
FREEDIUM_BASE = "https://freedium-mirror.cfd"
FREEDIUM_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,si;q=0.8",
    "priority": "u=1, i",
    "referer": f"{FREEDIUM_BASE}/",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}
REQUEST_TIMEOUT_SECONDS = 25


class MediumIngestError(Exception):
    pass


def _detect_source(url: str) -> Source:
    host = (urlparse(url).hostname or "").lower()
    if "medium.com" in host or host.endswith(".medium.com"):
        return "medium"
    raise MediumIngestError(f"unsupported host: {host or url!r}")


def _build_freedium_json_url(url: str) -> str:
    clean_url = url.split("?")[0].strip()
    return f"{FREEDIUM_BASE}/{clean_url}/__data.json?x-sveltekit-invalidated=01"


def _fetch_freedium_json(json_url: str) -> dict:
    try:
        response = requests.get(
            json_url, headers=FREEDIUM_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise MediumIngestError(f"failed to reach Freedium: {exc}") from exc
    if response.status_code != 200:
        raise MediumIngestError(
            f"HTTP {response.status_code} fetching {json_url}"
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise MediumIngestError(f"Freedium response not JSON: {exc}") from exc


def _resolve_ref(value: Any, items: list) -> str:
    """SvelteKit payloads may reference another array entry by its index."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and 0 <= value < len(items) and isinstance(items[value], str):
        return items[value]
    return ""


def _parse_freedium_payload(data: dict) -> dict[str, Any]:
    """Parse SvelteKit layout arrays from the Freedium `__data.json` response.

    The payload is a flat array of values (`nodes[1]["data"]`) where objects
    reference other array entries by index. There is no fixed schema, so we
    scan defensively for the fields we need.
    """
    nodes = data.get("nodes", [])
    if len(nodes) < 2 or "data" not in nodes[1]:
        raise MediumIngestError("unexpected Freedium payload layout")

    items = nodes[1]["data"]

    content_html = ""
    if "markdown" in items:
        idx = items.index("markdown")
        if idx + 1 < len(items) and isinstance(items[idx + 1], str):
            content_html = items[idx + 1]
    if not content_html:
        for item in items:
            if isinstance(item, str) and len(item) > 100 and "<p" in item:
                content_html = item
                break
    if not content_html:
        raise MediumIngestError("could not extract article content from Freedium payload")

    title = ""
    author = ""
    date = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if "title" in item and not title:
            title = _resolve_ref(item["title"], items)
        if "name" in item and not author:
            author = _resolve_ref(item["name"], items)
        if "publishedAt" in item and not date:
            date = _resolve_ref(item["publishedAt"], items)

    if not title or title.strip() in {"References", "To close", "Untitled"}:
        for item in items:
            if isinstance(item, str) and 10 < len(item) < 120:
                if not item.startswith(("http", "#", "/", "markdown")):
                    title = item
                    break

    return {
        "title": str(title or "").strip(),
        "author": str(author or "").strip() or None,
        "date": str(date or "").strip() or None,
        "content_html": content_html,
    }


def _absolutize(url: str, base: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin(base, url)


def _normalize_images(html: str, base_url: str) -> str:
    """Absolutize img src, drop placeholder alt text, and strip UI-only elements.

    Freedium's copy-to-clipboard buttons carry the code block's text in a
    `data-code` attribute with embedded newlines, which confuses the HTML
    parser and causes the code to appear duplicated in the output. They add
    no content value, so we drop them before conversion.
    """
    soup = BeautifulSoup(html, "html.parser")
    for button in soup.find_all("button", class_="code-copy-btn"):
        button.decompose()
    # Freedium renders code blocks twice (light + dark theme, toggled via CSS);
    # drop the dark variant to avoid duplicated code in the extracted text.
    for pre in soup.find_all("pre", class_="github-dark"):
        pre.decompose()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and not src.startswith("data:"):
            img["src"] = _absolutize(src, base_url)
        img["alt"] = "" if img.get("alt") in (None, "None") else img.get("alt")
    return str(soup)


def _html_to_markdown(html: str, base_url: str) -> str:
    """Convert article HTML to Markdown, preserving headings/lists/code blocks.

    Plain-text extraction loses structure an LLM needs to understand the
    article (section breaks, code snippets, lists), so we keep Markdown
    instead of flattening to prose.
    """
    normalized = _normalize_images(html, base_url)
    markdown = md(normalized, heading_style="ATX", strip=["script", "style"])
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def _extract_media_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        urls.append(_absolutize(src, base_url))
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _build_payload(article: dict[str, Any], url: str) -> IngestPayload:
    source = _detect_source(url)
    content_html = article["content_html"]
    text = _html_to_markdown(content_html, FREEDIUM_BASE)
    if not text:
        raise MediumIngestError("no article body extracted")

    return IngestPayload(
        source=source,
        original_url=url,
        raw_title=article["title"],
        raw_content=text,
        media_urls=_extract_media_urls(content_html, FREEDIUM_BASE),
        metadata={
            "author": article["author"],
            "published_at": article["date"],
        },
    )


def fetch_web_article(url: str, freedium_json: dict | None = None) -> IngestPayload:
    """Fetch and normalize a web article via the Freedium mirror.

    Pass `freedium_json` to skip network (used in tests/fixtures).
    """
    data = (
        freedium_json
        if freedium_json is not None
        else _fetch_freedium_json(_build_freedium_json_url(url))
    )
    article = _parse_freedium_payload(data)
    return _build_payload(article, url)


def fetch_from_fixture(path: str | Path, url: str) -> IngestPayload:
    """Load a saved Freedium `__data.json` fixture and normalize as if fetched from `url`."""
    fixture_path = Path(path)
    if not fixture_path.exists():
        raise MediumIngestError(f"fixture not found: {fixture_path}")
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MediumIngestError(f"invalid JSON in fixture: {exc}") from exc
    return fetch_web_article(url, freedium_json=data)
