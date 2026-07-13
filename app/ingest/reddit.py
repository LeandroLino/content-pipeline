import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import praw
from prawcore.exceptions import PrawcoreException

from app.config import RedditConfig, load_reddit_config
from app.schemas import IngestPayload

IMAGE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|gif|webp|mp4)$", re.IGNORECASE)


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


def _to_payload(submission: Any, url: str) -> IngestPayload:
    return IngestPayload(
        source="reddit",
        original_url=url,
        raw_title=getattr(submission, "title", "") or "",
        raw_content=getattr(submission, "selftext", "") or "",
        media_urls=_extract_media_urls(submission),
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

    return _to_payload(submission, url)


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
    return _to_payload(submission, original_url)
