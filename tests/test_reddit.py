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
    fetch_reddit_post_browser,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

GALLERY_POST_HTML = """
<html><body>
<shreddit-post id="t3_abc123" post-title="Meu post com galeria" post-type="gallery"
  subreddit-name="autohospedagem" author="alice" score="393" comment-count="71"
  created-timestamp="2026-07-07T14:30:31.094000+0000">
  <gallery-carousel>
    <li slot="page-1">
      <figure><img class="media-lightbox-img"
        src="https://preview.redd.it/one.jpg?width=640&amp;s=abc"
        srcset="https://preview.redd.it/one.jpg?width=320&amp;s=abc 320w, https://preview.redd.it/one.jpg?width=1080&amp;s=abc 1080w">
      </figure>
    </li>
    <li slot="page-2">
      <figure><img class="media-lightbox-img"
        data-lazy-src="https://preview.redd.it/two.jpg?width=640&amp;s=def"
        data-lazy-srcset="https://preview.redd.it/two.jpg?width=320&amp;s=def 320w, https://preview.redd.it/two.jpg?width=1080&amp;s=def 1080w">
      </figure>
    </li>
  </gallery-carousel>
  <div id="t3_abc123-post-rtjson-content">
    <p>Corpo <a href="https://example.com/ref">do post</a>.</p>
  </div>
  <shreddit-comment depth="0" score="15" author="carla" thingid="t1_low">
    <div id="t1_low-comment-rtjson-content" class="md" slot="comment">
      <p>Comentário com poucos votos.</p>
    </div>
  </shreddit-comment>
  <shreddit-comment depth="0" score="120" author="davi" thingid="t1_top">
    <div id="t1_top-comment-rtjson-content" class="md" slot="comment">
      <p>Comentário mais votado.</p>
    </div>
  </shreddit-comment>
  <shreddit-comment depth="1" score="999" author="reply" thingid="t1_reply">
    <div id="t1_reply-comment-rtjson-content" class="md" slot="comment">
      <p>Resposta aninhada, não deve contar.</p>
    </div>
  </shreddit-comment>
</shreddit-post>
</body></html>
"""

SINGLE_IMAGE_POST_HTML = """
<html><body>
<shreddit-post id="t3_xyz789" post-title="Post com uma imagem" post-type="image"
  subreddit-name="programming" author="bob" score="10" comment-count="2"
  created-timestamp="2026-01-01T00:00:00.000000+0000">
  <div slot="post-media-container">
    <img src="https://i.redd.it/hero.jpg?width=640&amp;s=xyz"
      srcset="https://i.redd.it/hero.jpg?width=320&amp;s=xyz 320w, https://i.redd.it/hero.jpg?width=1080&amp;s=xyz 1080w">
  </div>
  <div id="t3_xyz789-post-rtjson-content">
    <p>Texto simples.</p>
  </div>
</shreddit-post>
</body></html>
"""

NO_POST_ELEMENT_HTML = "<html><body><div>bloqueado / captcha</div></body></html>"


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


def test_fetch_reddit_post_extracts_top_comments_via_praw():
    def _make_comment(body, score):
        return SimpleNamespace(body=body, score=score)

    comments = MagicMock()
    comments.replace_more = MagicMock()
    comments.__iter__ = lambda self: iter(
        [
            _make_comment("comentário baixo", 1),
            _make_comment("comentário top", 50),
            _make_comment("comentário médio", 10),
        ]
    )

    sub = _make_submission(title="Hello", selftext="World")
    sub.comments = comments
    client = _fake_client(sub)

    payload = fetch_reddit_post(
        "https://reddit.com/r/programming/comments/abc/hello/",
        client=client,
    )

    comments.replace_more.assert_called_once_with(limit=0)
    assert payload.top_comments == ["comentário top", "comentário médio", "comentário baixo"]


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


def test_fetch_from_fixture_extracts_top_comments_sorted_by_score():
    payload = fetch_from_fixture(FIXTURES_DIR / "reddit_sample.json")
    assert 1 <= len(payload.top_comments) <= 5
    assert all(isinstance(c, str) and c for c in payload.top_comments)


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
    assert payload.top_comments == []


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


def test_fetch_reddit_post_browser_gallery():
    payload = fetch_reddit_post_browser(
        "https://www.reddit.com/r/autohospedagem/comments/abc123/x/",
        html=GALLERY_POST_HTML,
    )
    assert payload.raw_title == "Meu post com galeria"
    assert payload.metadata["subreddit"] == "autohospedagem"
    assert payload.metadata["author"] == "alice"
    assert payload.metadata["score"] == 393
    assert payload.metadata["num_comments"] == 71
    assert "do post" in payload.raw_content
    assert [str(u) for u in payload.media_urls] == [
        "https://preview.redd.it/one.jpg?width=1080&s=abc",
        "https://preview.redd.it/two.jpg?width=1080&s=def",
    ]
    assert payload.top_comments == ["Comentário mais votado.", "Comentário com poucos votos."]


def test_fetch_reddit_post_browser_single_image():
    payload = fetch_reddit_post_browser(
        "https://www.reddit.com/r/programming/comments/xyz789/y/",
        html=SINGLE_IMAGE_POST_HTML,
    )
    assert payload.raw_title == "Post com uma imagem"
    assert [str(u) for u in payload.media_urls] == [
        "https://i.redd.it/hero.jpg?width=1080&s=xyz"
    ]


def test_fetch_reddit_post_browser_raises_when_post_missing():
    with pytest.raises(RedditIngestError, match="could not locate"):
        fetch_reddit_post_browser("https://www.reddit.com/r/x/comments/y/z/", html=NO_POST_ELEMENT_HTML)


def test_fetch_reddit_post_browser_wraps_render_errors(monkeypatch):
    def _boom(url, headless=True, timeout_ms=30000):
        raise RuntimeError("browser launch failed")

    monkeypatch.setattr("app.ingest.reddit._fetch_rendered_html", _boom)

    with pytest.raises(RedditIngestError, match="browser fetch failed"):
        fetch_reddit_post_browser("https://www.reddit.com/r/x/comments/y/z/")
