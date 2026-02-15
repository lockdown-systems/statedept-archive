#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

import requests

API_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"


def load_checkpoint(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"cursor": "", "pages": 0, "tweets": 0}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_query(username: str, until_utc: str) -> str:
    # twitterapi.io supports queries like: from:elonmusk since:... until:..._UTC
    # We'll use "until" so we crawl backwards from that moment.
    return f"from:{username} until:{until_utc}"


def request_page(api_key: str, query: str, query_type: str, cursor: str) -> Dict[str, Any]:
    headers = {"X-API-Key": api_key}
    params = {"query": query, "queryType": query_type}
    # First page is "" per docs; omit cursor param if empty to be safe
    if cursor:
        params["cursor"] = cursor

    resp = requests.get(API_URL, headers=headers, params=params, timeout=60)

    # Basic retry handling for transient failures / rate limiting
    if resp.status_code in (429, 500, 502, 503, 504):
        raise RuntimeError(f"Transient HTTP {resp.status_code}: {resp.text[:200]}")

    resp.raise_for_status()
    return resp.json()


def main() -> int:
    p = argparse.ArgumentParser(
        description="Backup tweets going backwards from a given UTC timestamp using twitterapi.io advanced_search."
    )
    p.add_argument("--username", default="StateDept", help="Twitter/X username without @ (default: StateDept)")
    p.add_argument(
        "--until-utc",
        default="2026-01-20_17:00:00_UTC",
        help="Upper bound timestamp in UTC, format YYYY-MM-DD_HH:MM:SS_UTC (default: 2026-01-20_17:00:00_UTC)",
    )
    p.add_argument(
        "--out",
        default="statedept_backfill_from_inaug.jsonl",
        help="Output JSONL path (default: statedept_backfill_from_inaug.jsonl)",
    )
    p.add_argument(
        "--checkpoint",
        default="statedept_backfill.checkpoint.json",
        help="Checkpoint file path (default: statedept_backfill.checkpoint.json)",
    )
    p.add_argument("--query-type", default="Latest", choices=["Latest", "Top"], help="Query type (default: Latest)")
    p.add_argument(
        "--sleep",
        type=float,
        default=5.0,
        help="Seconds to sleep between successful pages (default: 5.0; free-tier is 1 req/5s)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Stop after N pages (0 = no limit). Useful for testing.",
    )
    args = p.parse_args()

    api_key = os.environ.get("TWITTERAPI_IO_KEY") or os.environ.get("X_API_KEY")
    if not api_key:
        print("ERROR: Set env var TWITTERAPI_IO_KEY (or X_API_KEY) to your twitterapi.io API key.", file=sys.stderr)
        return 2

    checkpoint = load_checkpoint(args.checkpoint)
    cursor = checkpoint.get("cursor", "") or ""
    pages = int(checkpoint.get("pages", 0) or 0)
    tweets_written = int(checkpoint.get("tweets", 0) or 0)

    query = build_query(args.username, args.until_utc)

    print(f"Query: {query}")
    print(f"Resuming from cursor: {cursor!r} | pages={pages} tweets={tweets_written}")
    print(f"Writing JSONL to: {args.out}")
    print("Press Ctrl+C to stop; progress is checkpointed.\n")

    # Append mode so resume continues writing
    with open(args.out, "a", encoding="utf-8") as out_f:
        while True:
            if args.max_pages and pages >= args.max_pages:
                print(f"Reached --max-pages={args.max_pages}. Stopping.")
                break

            # Retry loop for transient errors (429 = rate limit: free-tier is 1 req/5s)
            for attempt in range(1, 6):
                try:
                    data = request_page(api_key, query, args.query_type, cursor)
                    break
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "Too Many Requests" in err_str or "QPS limit" in err_str:
                        wait = min(60.0, 5.0 * (2.0 ** (attempt - 1)))  # 5, 10, 20, 40, 60
                    else:
                        wait = min(60.0, 2.0 ** attempt)
                    print(f"[warn] page fetch failed (attempt {attempt}/5): {e} — sleeping {wait:.1f}s", file=sys.stderr)
                    time.sleep(wait)
            else:
                print("ERROR: Too many failures fetching pages; exiting.", file=sys.stderr)
                return 1

            tweets = data.get("tweets") or []
            has_next = bool(data.get("has_next_page"))
            next_cursor = data.get("next_cursor") or ""

            # Write each tweet object as JSONL (includes tweet "id" field in the object)
            for tw in tweets:
                out_f.write(json.dumps(tw, ensure_ascii=False) + "\n")

            out_f.flush()
            os.fsync(out_f.fileno())

            pages += 1
            tweets_written += len(tweets)

            # Update checkpoint
            cursor = next_cursor if has_next else ""
            save_checkpoint(
                args.checkpoint,
                {"cursor": cursor, "pages": pages, "tweets": tweets_written, "query": query, "until_utc": args.until_utc},
            )

            print(
                f"page={pages} wrote={len(tweets):2d} total={tweets_written} has_next={has_next} next_cursor={'yes' if next_cursor else 'no'}"
            )

            if not has_next or not next_cursor:
                print("No more pages. Done.")
                break

            time.sleep(args.sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

