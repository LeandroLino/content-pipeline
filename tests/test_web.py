from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ingest.web import (
    WebIngestError,
    _absolutize,
    _detect_source,
    _extract_media_urls,
    fetch_from_fixture,
    fetch_web_article,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MEDIUM_URL = (
    "https://medium.com/@gurkanc/"
    "self-hosting-n8n-on-hetzner-from-zero-to-production-9a85189eb4f6"
)


def test_detect_source_medium():
    assert _detect_source("https://medium.com/@user/foo") == "medium"


def test_detect_source_medium_subdomain():
    assert _detect_source("https://towardsdatascience.medium.com/foo") == "medium"


def test_detect_source_unsupported():
    with pytest.raises(WebIngestError, match="unsupported host"):
        _detect_source("https://example.com/article")


def test_absolutize_relative_path():
    assert (
        _absolutize("/foo.jpg", "https://medium.com/@u/post")
        == "https://medium.com/foo.jpg"
    )


def test_absolutize_protocol_relative():
    assert _absolutize("//cdn.example.com/x.png", "https://medium.com/") == (
        "https://cdn.example.com/x.png"
    )


def test_absolutize_absolute_url():
    assert (
        _absolutize("https://cdn.example.com/x.png", "https://medium.com/")
        == "https://cdn.example.com/x.png"
    )


def test_extract_media_urls_filters_data_uri():
    html = """
    <article>
      <img src="data:image/png;base64,AAAA">
      <img src="https://cdn.example.com/real.jpg">
      <img data-src="https://cdn.example.com/lazy.jpg">
    </article>
    """
    urls = _extract_media_urls(html, "https://medium.com/")
    assert "https://cdn.example.com/real.jpg" in urls
    assert "https://cdn.example.com/lazy.jpg" in urls
    assert all(not u.startswith("data:") for u in urls)


def test_extract_media_urls_dedupes():
    html = """
    <article>
      <img src="https://cdn.example.com/a.jpg">
      <img src="https://cdn.example.com/a.jpg">
    </article>
    """
    urls = _extract_media_urls(html, "https://medium.com/")
    assert urls == ["https://cdn.example.com/a.jpg"]


def test_fetch_from_fixture_medium_sample():
    payload = fetch_from_fixture(FIXTURES_DIR / "medium_sample.html", MEDIUM_URL)
    assert payload.source == "medium"
    assert "n8n" in payload.raw_title.lower()
    assert "hetzner" in payload.raw_content.lower()
    assert payload.metadata["author"] == "Gürkan Çanakçı"
    assert payload.metadata["published_at"]
    assert str(payload.original_url).startswith("https://medium.com/")


def test_fetch_from_fixture_missing_file(tmp_path):
    with pytest.raises(WebIngestError, match="fixture not found"):
        fetch_from_fixture(tmp_path / "missing.html", MEDIUM_URL)


def test_fetch_web_article_with_inline_html():
    html = """
    <html><head><title>Test</title></head><body>
    <article>
      <h1>Simple Title</h1>
      <p>Some body text that is long enough to be extracted by trafilatura.
      This paragraph needs to be substantial enough that the extractor keeps it.
      Adding more content here so the recall favors this paragraph and returns it.</p>
      <p>More content to ensure extraction succeeds with enough text volume.</p>
    </article>
    </body></html>
    """
    payload = fetch_web_article("https://medium.com/@u/x", html=html)
    assert payload.source == "medium"
    assert "Simple Title" in payload.raw_title or "Test" in payload.raw_title


def test_fetch_web_article_http_error(mocker):
    fake = MagicMock()
    fake.status_code = 403
    mocker.patch("app.ingest.web.requests.get", return_value=fake)
    with pytest.raises(WebIngestError, match="HTTP 403"):
        fetch_web_article("https://medium.com/@u/x")


def test_fetch_web_article_empty_extraction(mocker):
    mocker.patch("app.ingest.web.trafilatura.extract", return_value=None)
    with pytest.raises(WebIngestError, match="no content"):
        fetch_web_article("https://medium.com/@u/x", html="<html></html>")
