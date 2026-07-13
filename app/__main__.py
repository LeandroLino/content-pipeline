import argparse
import json
import sys

from app.ingest.reddit import (
    RedditIngestError,
    fetch_from_fixture,
    fetch_reddit_post,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Reddit post URL (needs API credentials)")
    group.add_argument("--fixture", help="Path to a local JSON fixture file")
    args = parser.parse_args()

    try:
        if args.fixture:
            payload = fetch_from_fixture(args.fixture)
        else:
            payload = fetch_reddit_post(args.url)
    except RedditIngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
