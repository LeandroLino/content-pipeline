import argparse
import json
import sys

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
from app.storage import save_ingest_payload


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

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

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
