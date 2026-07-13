import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

from app.schemas import IngestPayload, Source

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15


class WebIngestError(Exception):
    pass


def _detect_source(url: str) -> Source:
    host = (urlparse(url).hostname or "").lower()
    if "medium.com" in host or host.endswith(".medium.com"):
        return "medium"
    raise WebIngestError(f"unsupported host: {host or url!r}")


def _fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "text/html,*/*"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=True,
    )
    if response.status_code != 200:
        raise WebIngestError(
            f"HTTP {response.status_code} fetching {url}"
        )
    return response.text


def _extract_content(html: str) -> dict[str, Any]:
    extracted = trafilatura.extract(
        html,
        output_format="json",
        with_metadata=True,
        include_images=True,
        include_links=False,
        favor_recall=True,
    )
    if not extracted:
        raise WebIngestError("trafilatura extracted no content")
    try:
        return json.loads(extracted)
    except json.JSONDecodeError as exc:
        raise WebIngestError(f"trafilatura output not JSON: {exc}") from exc


def _absolutize(url: str, base: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin(base, url)


def _extract_media_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article") or soup
    urls: list[str] = []
    for img in article.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        if src.startswith("data:"):
            continue
        urls.append(_absolutize(src, base_url))
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _build_payload(html: str, url: str) -> IngestPayload:
    source = _detect_source(url)
    meta = _extract_content(html)
    title = (meta.get("title") or "").strip()
    text = (meta.get("raw_text") or meta.get("text") or "").strip()
    if not text:
        raise WebIngestError("no article body extracted")

    return IngestPayload(
        source=source,
        original_url=url,
        raw_title=title,
        raw_content=text,
        media_urls=_extract_media_urls(html, url),
        metadata={
            "author": meta.get("author"),
            "published_at": meta.get("date"),
            "excerpt": meta.get("excerpt"),
            "sitename": meta.get("sitename"),
            "hostname": meta.get("hostname"),
        },
    )


def fetch_web_article(url: str, html: str | None = None) -> IngestPayload:
    """Fetch and normalize a web article. Pass `html` to skip network (used in tests/fixtures)."""
    body = html if html is not None else _fetch_html(url)
    return _build_payload(body, url)


def fetch_from_fixture(path: str | Path, url: str) -> IngestPayload:
    """Load a saved HTML fixture and normalize as if fetched from `url`."""
    fixture_path = Path(path)
    if not fixture_path.exists():
        raise WebIngestError(f"fixture not found: {fixture_path}")
    html = fixture_path.read_text(encoding="utf-8")
    return fetch_web_article(url, html=html)
