import argparse
import json
import sys
from pathlib import Path

from app.ingest.medium import (
    MediumIngestError,
    fetch_from_fixture as fetch_medium_fixture,
    fetch_web_article,
)
from app.ingest.reddit import (
    RedditIngestError,
    fetch_from_fixture as fetch_reddit_fixture,
    fetch_reddit_post,
    fetch_reddit_post_browser,
)
from app.llm.base import LLMError
from app.llm.factory import generate_image_post
from app.media.image_post import DEFAULT_WATERMARK_PATH, build_carousel
from app.storage import load_ingest_payload, save_ingest_payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="command", required=True)

    reddit = sub.add_parser("reddit", help="Ingest a Reddit post")
    reddit_source = reddit.add_mutually_exclusive_group(required=True)
    reddit_source.add_argument("--url", help="Reddit post URL (uses a stealth browser by default)")
    reddit_source.add_argument("--fixture", help="Local JSON fixture file path")
    reddit.add_argument("--no-save", action="store_true", help="Skip saving to data/ingested/")
    reddit.add_argument(
        "--praw",
        action="store_true",
        help="Use PRAW/OAuth instead of the browser; requires a registered Reddit app in .env",
    )

    medium = sub.add_parser("web", help="Ingest a Medium article (via Freedium)")
    medium_source = medium.add_mutually_exclusive_group(required=True)
    medium_source.add_argument("--url", help="Article URL")
    medium_source.add_argument("--fixture", help="Local Freedium __data.json fixture file path")
    medium.add_argument(
        "--as-url",
        help="Original URL to record when using --fixture",
    )
    medium.add_argument("--no-save", action="store_true", help="Skip saving to data/ingested/")

    image_post = sub.add_parser(
        "image-post", help="Generate an Instagram image-post (carousel) from an ingested payload"
    )
    image_post.add_argument(
        "--ingest-json", required=True, help="Path to a saved IngestPayload JSON (from data/ingested/)"
    )
    image_post.add_argument(
        "--output-dir", help="Where to save the carousel images + caption.txt (default: data/posts/{stem})"
    )
    image_post.add_argument(
        "--watermark", help="Path to a watermark PNG (default: bundled placeholder)"
    )
    image_post.add_argument(
        "--max-images", type=int, default=None, help="Limit how many media_urls to process (default: all)"
    )

    return parser


def _run_image_post(args: argparse.Namespace) -> int:
    payload = load_ingest_payload(args.ingest_json)

    if not payload.media_urls:
        print("error: ingested payload has no media_urls to build a carousel from", file=sys.stderr)
        return 1

    try:
        image_post = generate_image_post(payload)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    stem = Path(args.ingest_json).stem
    output_dir = Path(args.output_dir) if args.output_dir else Path("data/posts") / stem
    watermark_path = args.watermark or DEFAULT_WATERMARK_PATH

    urls = [str(u) for u in payload.media_urls]
    if args.max_images:
        urls = urls[: args.max_images]

    saved_images = build_carousel(urls, image_post.image_caption, output_dir, watermark_path)

    caption_path = output_dir / "caption.txt"
    caption_path.write_text(image_post.post_caption, encoding="utf-8")

    print(f"image_caption: {image_post.image_caption}")
    print(f"post_caption saved to: {caption_path}")
    for path in saved_images:
        print(f"image saved to: {path}")

    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "image-post":
        return _run_image_post(args)

    try:
        if args.command == "reddit":
            if args.fixture:
                payload = fetch_reddit_fixture(args.fixture)
            elif args.praw:
                payload = fetch_reddit_post(args.url)
            else:
                payload = fetch_reddit_post_browser(args.url)
        else:
            if args.fixture and not args.as_url:
                print("error: --as-url is required with --fixture", file=sys.stderr)
                return 2
            payload = (
                fetch_medium_fixture(args.fixture, args.as_url)
                if args.fixture
                else fetch_web_article(args.url)
            )
    except (RedditIngestError, MediumIngestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False))

    if not args.no_save:
        saved = save_ingest_payload(payload)
        print(f"saved to: {saved.json_path}", file=sys.stderr)
        print(f"saved to: {saved.md_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
