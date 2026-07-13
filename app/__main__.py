import argparse
import json
import sys

from app.ingest.reddit import (
    RedditIngestError,
    fetch_from_fixture as fetch_reddit_fixture,
    fetch_reddit_post,
)
from app.ingest.web import (
    WebIngestError,
    fetch_from_fixture as fetch_web_fixture,
    fetch_web_article,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="command", required=True)

    reddit = sub.add_parser("reddit", help="Ingest a Reddit post")
    reddit_source = reddit.add_mutually_exclusive_group(required=True)
    reddit_source.add_argument("--url", help="Reddit post URL (needs API credentials)")
    reddit_source.add_argument("--fixture", help="Local JSON fixture file path")

    web = sub.add_parser("web", help="Ingest a web article (Medium, etc.)")
    web_source = web.add_mutually_exclusive_group(required=True)
    web_source.add_argument("--url", help="Article URL")
    web_source.add_argument("--fixture", help="Local HTML fixture file path")
    web.add_argument(
        "--as-url",
        help="Original URL to record when using --fixture",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "reddit":
            payload = (
                fetch_reddit_fixture(args.fixture)
                if args.fixture
                else fetch_reddit_post(args.url)
            )
        else:
            if args.fixture and not args.as_url:
                print("error: --as-url is required with --fixture", file=sys.stderr)
                return 2
            payload = (
                fetch_web_fixture(args.fixture, args.as_url)
                if args.fixture
                else fetch_web_article(args.url)
            )
    except (RedditIngestError, WebIngestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
