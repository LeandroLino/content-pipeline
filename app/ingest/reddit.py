import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import praw
from bs4 import BeautifulSoup
from bs4.element import Tag
from markdownify import markdownify as md
from prawcore.exceptions import PrawcoreException

from app.config import RedditConfig, load_reddit_config
from app.schemas import IngestPayload

IMAGE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|gif|webp|mp4)$", re.IGNORECASE)
TOP_COMMENTS_LIMIT = 5


class RedditIngestError(Exception):
    pass


def _build_client(config: RedditConfig) -> praw.Reddit:
    return praw.Reddit(
        client_id=config.client_id,
        client_secret=config.client_secret,
        user_agent=config.user_agent,
        check_for_async=False,
    )


def _extract_media_urls(submission: Any) -> list[str]:
    urls: list[str] = []

    direct = getattr(submission, "url", None)
    if direct and IMAGE_EXT_RE.search(direct):
        urls.append(direct)

    preview = getattr(submission, "preview", None) or {}
    for img in preview.get("images", []) if isinstance(preview, dict) else []:
        src = img.get("source", {}).get("url")
        if src:
            urls.append(src.replace("&amp;", "&"))

    if getattr(submission, "is_gallery", False):
        gallery = getattr(submission, "media_metadata", None) or {}
        for item in gallery.values():
            src = item.get("s", {}).get("u")
            if src:
                urls.append(src.replace("&amp;", "&"))

    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _extract_top_comments_praw(submission: Any, limit: int = TOP_COMMENTS_LIMIT) -> list[str]:
    """Fetch the top-voted top-level comments via PRAW, as plain text."""
    comments = getattr(submission, "comments", None)
    if comments is None:
        return []
    try:
        comments.replace_more(limit=0)  # drop "load more comments" placeholders
        top_level = list(comments)
    except Exception:
        return []

    scored = [
        (getattr(c, "score", 0) or 0, getattr(c, "body", "") or "")
        for c in top_level
        if getattr(c, "body", None)
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [body.strip() for _, body in scored[:limit] if body.strip()]


def _to_payload(submission: Any, url: str, top_comments: list[str] | None = None) -> IngestPayload:
    return IngestPayload(
        source="reddit",
        original_url=url,
        raw_title=getattr(submission, "title", "") or "",
        raw_content=getattr(submission, "selftext", "") or "",
        media_urls=_extract_media_urls(submission),
        top_comments=top_comments or [],
        metadata={
            "subreddit": getattr(getattr(submission, "subreddit", None), "display_name", None),
            "author": getattr(getattr(submission, "author", None), "name", None),
            "score": getattr(submission, "score", None),
            "num_comments": getattr(submission, "num_comments", None),
            "created_utc": getattr(submission, "created_utc", None),
            "over_18": getattr(submission, "over_18", None),
        },
    )


def fetch_reddit_post(url: str, client: praw.Reddit | None = None) -> IngestPayload:
    """Fetch a Reddit post via PRAW and normalize it to IngestPayload."""
    reddit = client or _build_client(load_reddit_config())
    try:
        submission = reddit.submission(url=url)
        submission._fetch()  # force fetch to surface errors here
    except PrawcoreException as exc:
        raise RedditIngestError(f"Reddit API error: {exc}") from exc
    except Exception as exc:
        raise RedditIngestError(f"failed to load submission: {exc}") from exc

    top_comments = _extract_top_comments_praw(submission)
    return _to_payload(submission, url, top_comments=top_comments)


def _extract_post_from_listing(data: Any) -> dict:
    """Extract the post dict from either a Reddit Listing array or a flat dict."""
    if isinstance(data, list):
        try:
            post = data[0]["data"]["children"][0]["data"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RedditIngestError(f"unexpected Reddit Listing shape: {exc}") from exc
        if not isinstance(post, dict):
            raise RedditIngestError("Reddit Listing children[0].data is not a dict")
        return post
    if isinstance(data, dict):
        return data
    raise RedditIngestError(f"unsupported fixture root type: {type(data).__name__}")


def _get_field(post: dict, *keys: str) -> Any:
    """First non-None field among keys, or None."""
    for key in keys:
        value = post.get(key)
        if value is not None:
            return value
    return None


def _extract_top_comments_from_listing(data: Any, limit: int = TOP_COMMENTS_LIMIT) -> list[str]:
    """Extract top-voted top-level comments from a Reddit Listing's 2nd element.

    Reddit's `.json` endpoint (and fixtures saved from it) return a 2-item
    array: `[post_listing, comments_listing]`. Only present for the full
    Listing shape -- a flat post dict (no comments_listing) yields [].
    """
    if not isinstance(data, list) or len(data) < 2:
        return []
    try:
        children = data[1]["data"]["children"]
    except (KeyError, IndexError, TypeError):
        return []

    scored: list[tuple[int, str]] = []
    for child in children:
        comment = child.get("data", {}) if isinstance(child, dict) else {}
        body = comment.get("body")
        if not body or body in ("[deleted]", "[removed]"):
            continue
        scored.append((comment.get("score", 0) or 0, body.strip()))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [body for _, body in scored[:limit]]


def _submission_from_fixture(post: dict) -> SimpleNamespace:
    subreddit = _get_field(post, "subreddit_name", "subreddit")
    author = _get_field(post, "author_name", "author")
    return SimpleNamespace(
        title=post.get("title", "") or "",
        selftext=post.get("selftext", "") or "",
        url=_get_field(post, "url_overridden_by_dest", "url"),
        preview=post.get("preview"),
        is_gallery=post.get("is_gallery", False),
        media_metadata=post.get("media_metadata"),
        subreddit=SimpleNamespace(display_name=subreddit),
        author=SimpleNamespace(name=author),
        score=post.get("score"),
        num_comments=post.get("num_comments"),
        created_utc=post.get("created_utc"),
        over_18=post.get("over_18", False),
    )


def _derive_original_url(post: dict) -> str | None:
    if post.get("original_url"):
        return post["original_url"]
    permalink = post.get("permalink")
    if permalink:
        return f"https://www.reddit.com{permalink}"
    return post.get("url")


def fetch_from_fixture(path: str | Path) -> IngestPayload:
    """Load a Reddit payload from a local JSON file. Accepts Reddit Listing shape or flat dict."""
    fixture_path = Path(path)
    if not fixture_path.exists():
        raise RedditIngestError(f"fixture not found: {fixture_path}")
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RedditIngestError(f"invalid JSON in fixture: {exc}") from exc

    post = _extract_post_from_listing(raw)
    original_url = _derive_original_url(post)
    if not original_url:
        raise RedditIngestError("fixture missing 'permalink', 'original_url', or 'url'")

    submission = _submission_from_fixture(post)
    top_comments = _extract_top_comments_from_listing(raw)
    return _to_payload(submission, original_url, top_comments=top_comments)


# --- Browser-based fallback (Camoufox) ---------------------------------
#
# PRAW needs a registered OAuth app (client_id/secret). Reddit's public
# `.json` endpoint also 403s even when requested from within a real browser
# context (checked empirically), so when no app is registered the only way
# left to fetch a post is to render the page in an actual browser and scrape
# the `<shreddit-post>` web component the client hydrates it into. Camoufox
# is a stealth Firefox build (patched to resist bot fingerprinting), used
# instead of stock Playwright to reduce the chance of getting blocked.


def _fetch_rendered_html(url: str, headless: bool = True, timeout_ms: int = 30000) -> str:
    """Render a Reddit post page in a real browser and return its HTML."""
    from camoufox.sync_api import Camoufox

    with Camoufox(headless=headless, humanize=True) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_selector("shreddit-post", timeout=timeout_ms)
        # Gallery images and the text body finish hydrating shortly after
        # the custom element appears; a short settle avoids truncated content.
        page.wait_for_timeout(1500)
        return page.content()


def _parse_srcset_best(srcset: str | None) -> str | None:
    """Pick the highest-resolution URL from an HTML `srcset` attribute."""
    if not srcset:
        return None
    best_width = -1
    best_url: str | None = None
    for candidate in srcset.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        parts = candidate.rsplit(" ", 1)
        url = parts[0]
        width = 0
        if len(parts) == 2 and parts[1].endswith("w"):
            try:
                width = int(parts[1][:-1])
            except ValueError:
                width = 0
        if width >= best_width:
            best_width = width
            best_url = url
    return best_url


def _best_image_url(img: Tag) -> str | None:
    src = img.get("data-lazy-src") or img.get("src")
    srcset = img.get("data-lazy-srcset") or img.get("srcset")
    chosen = _parse_srcset_best(srcset) or src
    return chosen.replace("&amp;", "&") if chosen else None


def _extract_gallery_media(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for img in soup.select("gallery-carousel img.media-lightbox-img"):
        best = _best_image_url(img)
        if best:
            urls.append(best)
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _extract_single_media(post: Tag) -> list[str]:
    """Non-gallery posts: the hero image lives in the post-media-container slot."""
    img = post.select_one('div[slot="post-media-container"] img')
    if img is None:
        return []
    best = _best_image_url(img)
    return [best] if best else []


def _extract_body_markdown(soup: BeautifulSoup, post_id: str) -> str:
    content_div = soup.select_one(f"#{post_id}-post-rtjson-content")
    if content_div is None:
        return ""
    markdown = md(str(content_div), heading_style="ATX", strip=["script", "style"])
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_top_comments(soup: BeautifulSoup, limit: int = TOP_COMMENTS_LIMIT) -> list[str]:
    """Extract the top-voted top-level (depth=0) comments as plain text.

    Reddit renders each comment as a `<shreddit-comment>` custom element; its
    body lives in a sibling `div#{thingid}-comment-rtjson-content`, the same
    pattern as the post body (`#{post_id}-post-rtjson-content`).
    """
    scored: list[tuple[int, str]] = []
    for comment in soup.select('shreddit-comment[depth="0"]'):
        thing_id = comment.get("thingid")
        if not thing_id:
            continue
        content_div = soup.select_one(f"#{thing_id}-comment-rtjson-content")
        if content_div is None:
            continue
        text = content_div.get_text(separator=" ", strip=True)
        if not text:
            continue
        scored.append((_to_int(comment.get("score")) or 0, text))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:limit]]


def _parse_shreddit_post(html: str, url: str) -> IngestPayload:
    soup = BeautifulSoup(html, "html.parser")
    post = soup.select_one("shreddit-post")
    if post is None:
        raise RedditIngestError(
            "could not locate <shreddit-post> element (page blocked or layout changed)"
        )

    post_id = post.get("id", "") or ""
    post_type = post.get("post-type", "")
    media_urls = (
        _extract_gallery_media(soup) if post_type == "gallery" else _extract_single_media(post)
    )

    return IngestPayload(
        source="reddit",
        original_url=url,
        raw_title=post.get("post-title", "") or "",
        raw_content=_extract_body_markdown(soup, post_id) if post_id else "",
        media_urls=media_urls,
        top_comments=_extract_top_comments(soup),
        metadata={
            "subreddit": post.get("subreddit-name"),
            "author": post.get("author"),
            "score": _to_int(post.get("score")),
            "num_comments": _to_int(post.get("comment-count")),
            "created_utc": post.get("created-timestamp"),
            "over_18": post.has_attr("nsfw"),
        },
    )


def fetch_reddit_post_browser(url: str, html: str | None = None, headless: bool = True) -> IngestPayload:
    """Fetch a Reddit post by driving a real (stealth) browser via Camoufox.

    Bypasses PRAW/OAuth entirely -- useful while no Reddit app is registered.
    Pass `html` to skip the browser launch (used in tests/fixtures).
    """
    try:
        rendered = html if html is not None else _fetch_rendered_html(url, headless=headless)
    except RedditIngestError:
        raise
    except Exception as exc:  # camoufox/playwright errors (timeout, launch failure, etc.)
        raise RedditIngestError(f"browser fetch failed: {exc}") from exc

    return _parse_shreddit_post(rendered, url)
