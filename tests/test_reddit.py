import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prawcore.exceptions import PrawcoreException

from app.ingest.reddit import (
    RedditIngestError,
    _extract_media_urls,
    fetch_from_fixture,
    fetch_reddit_post,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_submission(**overrides):
    defaults = dict(
        title="",
        selftext="",
        url=None,
        preview=None,
        is_gallery=False,
        media_metadata=None,
        subreddit=SimpleNamespace(display_name=None),
        author=SimpleNamespace(name=None),
        score=None,
        num_comments=None,
        created_utc=None,
        over_18=False,
    )
    defaults.update(overrides)
    sub = SimpleNamespace(**defaults)
    sub._fetch = MagicMock()
    return sub


def _fake_client(submission):
    client = MagicMock()
    client.submission.return_value = submission
    return client


def test_extract_media_urls_from_direct_image():
    sub = _make_submission(url="https://i.redd.it/foo.jpg")
    assert _extract_media_urls(sub) == ["https://i.redd.it/foo.jpg"]


def test_extract_media_urls_ignores_non_media():
    sub = _make_submission(url="https://example.com/article")
    assert _extract_media_urls(sub) == []


def test_extract_media_urls_from_preview_decodes_amp():
    sub = _make_submission(
        preview={"images": [{"source": {"url": "https://i.redd.it/bar.jpg&amp;s=1"}}]}
    )
    assert _extract_media_urls(sub) == ["https://i.redd.it/bar.jpg&s=1"]


def test_extract_media_urls_gallery():
    sub = _make_submission(
        is_gallery=True,
        media_metadata={
            "abc": {"s": {"u": "https://preview.redd.it/one.jpg"}},
            "def": {"s": {"u": "https://preview.redd.it/two.jpg"}},
        },
    )
    result = _extract_media_urls(sub)
    assert "https://preview.redd.it/one.jpg" in result
    assert "https://preview.redd.it/two.jpg" in result


def test_extract_media_urls_dedupes():
    sub = _make_submission(
        url="https://i.redd.it/foo.jpg",
        preview={"images": [{"source": {"url": "https://i.redd.it/foo.jpg"}}]},
    )
    assert _extract_media_urls(sub) == ["https://i.redd.it/foo.jpg"]


def test_fetch_reddit_post_normalizes_payload():
    sub = _make_submission(
        title="Hello",
        selftext="World body",
        url="https://i.redd.it/foo.png",
        subreddit=SimpleNamespace(display_name="programming"),
        author=SimpleNamespace(name="alice"),
        score=42,
        num_comments=3,
        created_utc=1700000000,
    )
    client = _fake_client(sub)

    payload = fetch_reddit_post(
        "https://reddit.com/r/programming/comments/abc/hello/",
        client=client,
    )

    client.submission.assert_called_once_with(
        url="https://reddit.com/r/programming/comments/abc/hello/"
    )
    sub._fetch.assert_called_once()
    assert payload.source == "reddit"
    assert payload.raw_title == "Hello"
    assert payload.raw_content == "World body"
    assert str(payload.media_urls[0]) == "https://i.redd.it/foo.png"
    assert payload.metadata["subreddit"] == "programming"
    assert payload.metadata["score"] == 42


def test_fetch_reddit_post_raises_on_prawcore_error():
    sub = _make_submission()
    sub._fetch.side_effect = PrawcoreException("boom")
    client = _fake_client(sub)

    with pytest.raises(RedditIngestError):
        fetch_reddit_post("https://reddit.com/r/x/comments/abc/y/", client=client)


def test_fetch_reddit_post_raises_on_generic_error():
    sub = _make_submission()
    sub._fetch.side_effect = RuntimeError("nope")
    client = _fake_client(sub)

    with pytest.raises(RedditIngestError):
        fetch_reddit_post("https://reddit.com/r/x/comments/abc/y/", client=client)


def test_fetch_from_fixture_loads_real_listing():
    payload = fetch_from_fixture(FIXTURES_DIR / "reddit_sample.json")
    assert payload.source == "reddit"
    assert payload.raw_title
    assert payload.metadata["subreddit"] == "autohospedagem"
    assert payload.metadata["author"] == "MorgothTheBauglir"
    assert "reddit.com" in str(payload.original_url)


def test_fetch_from_fixture_loads_flat_dict(tmp_path):
    flat = tmp_path / "flat.json"
    flat.write_text(
        json.dumps(
            {
                "title": "Flat Title",
                "selftext": "flat body",
                "subreddit_name": "programming",
                "author_name": "alice",
                "score": 10,
                "permalink": "/r/programming/comments/xyz/flat_title/",
            }
        ),
        encoding="utf-8",
    )
    payload = fetch_from_fixture(flat)
    assert payload.raw_title == "Flat Title"
    assert payload.metadata["subreddit"] == "programming"
    assert payload.metadata["author"] == "alice"


def test_fetch_from_fixture_bad_listing(tmp_path):
    bad = tmp_path / "bad_listing.json"
    bad.write_text(json.dumps([{"kind": "Listing"}]), encoding="utf-8")
    with pytest.raises(RedditIngestError, match="unexpected Reddit Listing"):
        fetch_from_fixture(bad)


def test_fetch_from_fixture_missing_file():
    with pytest.raises(RedditIngestError, match="fixture not found"):
        fetch_from_fixture(FIXTURES_DIR / "does_not_exist.json")


def test_fetch_from_fixture_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RedditIngestError, match="invalid JSON"):
        fetch_from_fixture(bad)


def test_fetch_from_fixture_missing_url(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"title": "no url"}), encoding="utf-8")
    with pytest.raises(RedditIngestError, match="missing 'permalink'"):
        fetch_from_fixture(empty)
