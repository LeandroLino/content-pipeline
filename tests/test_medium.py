import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ingest.medium import (
    MediumIngestError,
    _absolutize,
    _build_freedium_json_url,
    _detect_source,
    _extract_media_urls,
    _html_to_markdown,
    _parse_freedium_payload,
    fetch_from_fixture,
    fetch_web_article,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MEDIUM_URL = (
    "https://medium.com/@nickguitar/"
    "analise-de-um-malware-que-rouba-pix-pixstealer-a25cfc52f1ab"
)


def test_detect_source_medium():
    assert _detect_source("https://medium.com/@user/foo") == "medium"


def test_detect_source_medium_subdomain():
    assert _detect_source("https://towardsdatascience.medium.com/foo") == "medium"


def test_detect_source_unsupported():
    with pytest.raises(MediumIngestError, match="unsupported host"):
        _detect_source("https://example.com/article")


def test_build_freedium_json_url():
    url = _build_freedium_json_url("https://medium.com/@u/foo-bar")
    assert url == (
        "https://freedium-mirror.cfd/https://medium.com/@u/foo-bar"
        "/__data.json?x-sveltekit-invalidated=01"
    )


def test_build_freedium_json_url_strips_query():
    url = _build_freedium_json_url("https://medium.com/@u/foo?source=rss")
    assert "?source=rss" not in url
    assert url.endswith("/__data.json?x-sveltekit-invalidated=01")


def test_absolutize_relative_path():
    assert (
        _absolutize("/foo.jpg", "https://freedium-mirror.cfd/")
        == "https://freedium-mirror.cfd/foo.jpg"
    )


def test_absolutize_protocol_relative():
    assert _absolutize("//cdn.example.com/x.png", "https://freedium-mirror.cfd/") == (
        "https://cdn.example.com/x.png"
    )


def test_absolutize_absolute_url():
    assert (
        _absolutize("https://cdn.example.com/x.png", "https://freedium-mirror.cfd/")
        == "https://cdn.example.com/x.png"
    )


def test_html_to_markdown_preserves_structure():
    html = "<h2>Title</h2><p>Hello <em>world</em></p><p>Second paragraph</p>"
    text = _html_to_markdown(html, "https://freedium-mirror.cfd/")
    assert "## Title" in text
    assert "Hello" in text
    assert "*world*" in text
    assert "Second paragraph" in text
    assert "<p>" not in text


def test_extract_media_urls_filters_data_uri():
    html = """
    <div>
      <img src="data:image/png;base64,AAAA">
      <img src="/img/real.jpg">
      <img data-src="/img/lazy.jpg">
    </div>
    """
    urls = _extract_media_urls(html, "https://freedium-mirror.cfd/")
    assert "https://freedium-mirror.cfd/img/real.jpg" in urls
    assert "https://freedium-mirror.cfd/img/lazy.jpg" in urls
    assert all(not u.startswith("data:") for u in urls)


def test_extract_media_urls_dedupes():
    html = """
    <div>
      <img src="/img/a.jpg">
      <img src="/img/a.jpg">
    </div>
    """
    urls = _extract_media_urls(html, "https://freedium-mirror.cfd/")
    assert urls == ["https://freedium-mirror.cfd/img/a.jpg"]


def test_parse_freedium_payload_missing_nodes():
    with pytest.raises(MediumIngestError, match="unexpected Freedium payload layout"):
        _parse_freedium_payload({"nodes": [{}]})


def test_fetch_from_fixture_medium_sample():
    payload = fetch_from_fixture(
        FIXTURES_DIR / "medium_freedium_sample.json", MEDIUM_URL
    )
    assert payload.source == "medium"
    assert "pixstealer" in payload.raw_title.lower()
    assert "malware" in payload.raw_content.lower()
    assert payload.metadata["author"] == "Nickguitar"
    assert payload.metadata["published_at"]
    assert str(payload.original_url) == MEDIUM_URL
    assert len(payload.media_urls) > 0


def test_fetch_from_fixture_missing_file(tmp_path):
    with pytest.raises(MediumIngestError, match="fixture not found"):
        fetch_from_fixture(tmp_path / "missing.json", MEDIUM_URL)


def test_fetch_from_fixture_invalid_json(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MediumIngestError, match="invalid JSON"):
        fetch_from_fixture(bad_file, MEDIUM_URL)


def test_fetch_web_article_http_error(mocker):
    fake = MagicMock()
    fake.status_code = 403
    mocker.patch("app.ingest.medium.requests.get", return_value=fake)
    with pytest.raises(MediumIngestError, match="HTTP 403"):
        fetch_web_article("https://medium.com/@u/x")


def test_fetch_web_article_network_error(mocker):
    import requests

    mocker.patch(
        "app.ingest.medium.requests.get",
        side_effect=requests.ConnectionError("boom"),
    )
    with pytest.raises(MediumIngestError, match="failed to reach Freedium"):
        fetch_web_article("https://medium.com/@u/x")


def test_fetch_web_article_with_injected_json():
    sample = json.loads(
        (FIXTURES_DIR / "medium_freedium_sample.json").read_text(encoding="utf-8")
    )
    payload = fetch_web_article(MEDIUM_URL, freedium_json=sample)
    assert payload.source == "medium"
    assert payload.raw_content
