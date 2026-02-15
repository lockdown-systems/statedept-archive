#!/usr/bin/env python3
import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests

API_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"


# ---------- Time helpers ----------

def parse_until_utc(s: str) -> datetime:
    # Expected: YYYY-MM-DD_HH:MM:SS_UTC
    if not s.endswith("_UTC"):
        raise ValueError("until-utc must end with _UTC, e.g. 2024-01-20_17:00:00_UTC")
    core = s[:-4]
    dt = datetime.strptime(core, "%Y-%m-%d_%H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)

def fmt_utc(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d_%H:%M:%S_UTC")

def month_floor(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def add_months(dt: datetime, months: int) -> datetime:
    # safe month arithmetic without external deps
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    # clamp day to last day of target month
    import calendar
    last_day = calendar.monthrange(y, m)[1]
    d = min(dt.day, last_day)
    return dt.replace(year=y, month=m, day=d)

@dataclass
class Window:
    since: datetime  # inclusive
    until: datetime  # exclusive

def window_for_month_ending_at(until: datetime) -> Window:
    """
    Given an 'until' timestamp, create a window [since, until) covering the month
    that ends at the month boundary containing 'until'.
    Example: until=2024-03-15 -> window [2024-03-01, 2024-04-01) ? No.
    We want month-by-month BACKWARDS from 'until', so the first window is:
      [month_start_of(until), until)
    Then previous full months: [prev_month_start, month_start_of(until))
    """
    u = until.astimezone(timezone.utc)
    ms = month_floor(u)
    return Window(since=ms, until=u)

def prev_month_window(cur_since: datetime) -> Window:
    # Previous full month: [prev_month_start, cur_since)
    prev_start = add_months(cur_since, -1)
    prev_start = prev_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return Window(since=prev_start, until=cur_since)


# ---------- Checkpointing ----------

def load_checkpoint(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_checkpoint(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- Storage (dedupe) ----------

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tweets (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON tweets(created_at);")
    conn.commit()
    return conn

def insert_tweet(conn: sqlite3.Connection, tw: Dict[str, Any]) -> bool:
    """
    Insert tweet if new. Returns True if inserted, False if duplicate/ignored.
    """
    tid = str(tw.get("id", "")).strip()
    if not tid:
        return False
    created_at = tw.get("createdAt")
    payload = json.dumps(tw, ensure_ascii=False)

    cur = conn.execute(
        "INSERT OR IGNORE INTO tweets(id, created_at, json) VALUES (?, ?, ?)",
        (tid, created_at, payload),
    )
    return cur.rowcount == 1


# ---------- API ----------

def request_page(api_key: str, query: str, query_type: str, cursor: str) -> Dict[str, Any]:
    headers = {"X-API-Key": api_key}
    params = {"query": query, "queryType": query_type}
    if cursor:
        params["cursor"] = cursor

    resp = requests.get(API_URL, headers=headers, params=params, timeout=60)
    if resp.status_code in (429, 500, 502, 503, 504):
        raise RuntimeError(f"Transient HTTP {resp.status_code}: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()

def build_query(username: str, w: Window) -> str:
    # Use bounded window to reduce flakiness.
    # Note: until is exclusive; since is inclusive (per advanced search semantics).
    return f"from:{username} since:{fmt_utc(w.since)} until:{fmt_utc(w.until)}"


# ---------- Main crawl ----------

def main() -> int:
    p = argparse.ArgumentParser(description="Backup tweets month-by-month backwards with deduplication.")
    p.add_argument("--username", default="StateDept", help="Twitter/X username without @ (default: StateDept)")
    p.add_argument(
        "--until-utc",
        default="2026-01-20_17:00:00_UTC",
        help="Start crawling backwards from this UTC timestamp (exclusive). Format YYYY-MM-DD_HH:MM:SS_UTC",
    )
    p.add_argument("--out", default="statedept_backfill.jsonl", help="Append-only JSONL output path")
    p.add_argument("--db", default="statedept_backfill.sqlite", help="SQLite DB for dedupe + resumability")
    p.add_argument("--checkpoint", default="statedept_backfill.checkpoint.json", help="Checkpoint JSON path")
    p.add_argument("--query-type", default="Latest", choices=["Latest", "Top"], help="Query type (default: Latest)")
    p.add_argument(
        "--sleep",
        type=float,
        default=5.0,
        help="Seconds to sleep between successful pages (default: 5.0; free-tier is 1 req/5s)",
    )
    p.add_argument(
        "--stop-before-utc",
        default="",
        help="Optional: stop once window.since is <= this UTC timestamp (inclusive). Same format as until-utc.",
    )
    p.add_argument(
        "--max-months",
        type=int,
        default=0,
        help="Optional: stop after N months processed (0 = no limit).",
    )
    args = p.parse_args()

    api_key = os.environ.get("TWITTERAPI_IO_KEY") or os.environ.get("X_API_KEY")
    if not api_key:
        print("ERROR: Set env var TWITTERAPI_IO_KEY (or X_API_KEY) to your twitterapi.io API key.", file=sys.stderr)
        return 2

    start_until = parse_until_utc(args.until_utc)
    stop_before = parse_until_utc(args.stop_before_utc) if args.stop_before_utc else None

    # Load checkpoint if present
    cp = load_checkpoint(args.checkpoint)

    # Determine starting window + cursor from checkpoint or args
    if cp.get("window_since") and cp.get("window_until"):
        w = Window(parse_until_utc(cp["window_since"]), parse_until_utc(cp["window_until"]))
        cursor = cp.get("cursor", "") or ""
        months_done = int(cp.get("months_done", 0) or 0)
        pages_done = int(cp.get("pages_done", 0) or 0)
        total_seen = int(cp.get("total_seen", 0) or 0)
        total_new = int(cp.get("total_new", 0) or 0)
        print(f"Resuming from checkpoint window [{fmt_utc(w.since)}, {fmt_utc(w.until)}) cursor={'set' if cursor else 'empty'}")
    else:
        w = window_for_month_ending_at(start_until)
        cursor = ""
        months_done = 0
        pages_done = 0
        total_seen = 0
        total_new = 0
        print(f"Starting at window [{fmt_utc(w.since)}, {fmt_utc(w.until)})")

    conn = init_db(args.db)

    # Append-only JSONL for newly inserted tweets
    out_f = open(args.out, "a", encoding="utf-8")

    try:
        while True:
            if stop_before and w.since <= stop_before:
                print(f"Stop condition met: window.since {fmt_utc(w.since)} <= {fmt_utc(stop_before)}")
                break
            if args.max_months and months_done >= args.max_months:
                print(f"Reached --max-months={args.max_months}. Stopping.")
                break

            query = build_query(args.username, w)
            print(f"\n=== Month window [{fmt_utc(w.since)}, {fmt_utc(w.until)}) ===")
            print(f"Query: {query}")

            # Page through this window until exhausted
            while True:
                # Retry loop for transient errors
                for attempt in range(1, 6):
                    try:
                        data = request_page(api_key, query, args.query_type, cursor)
                        break
                    except Exception as e:
                        err_str = str(e)
                        if "429" in err_str or "Too Many Requests" in err_str or "QPS limit" in err_str:
                            wait = min(60.0, 5.0 * (2.0 ** (attempt - 1)))
                        else:
                            wait = min(60.0, 2.0 ** attempt)
                        print(f"[warn] fetch failed (attempt {attempt}/5): {e} — sleeping {wait:.1f}s", file=sys.stderr)
                        time.sleep(wait)
                else:
                    print("ERROR: Too many failures fetching pages; exiting.", file=sys.stderr)
                    return 1

                tweets = data.get("tweets") or []
                has_next = bool(data.get("has_next_page"))
                next_cursor = data.get("next_cursor") or ""

                seen = len(tweets)
                new_count = 0

                # Insert w/ dedupe; write JSONL only for new inserts
                for tw in tweets:
                    total_seen += 1
                    if insert_tweet(conn, tw):
                        total_new += 1
                        new_count += 1
                        out_f.write(json.dumps(tw, ensure_ascii=False) + "\n")

                conn.commit()
                out_f.flush()
                os.fsync(out_f.fileno())

                pages_done += 1

                print(
                    f"page={pages_done} window_seen={seen:2d} window_new={new_count:2d} "
                    f"total_new={total_new} has_next={has_next} next_cursor={'yes' if next_cursor else 'no'}"
                )

                # Update checkpoint after each page
                cursor = next_cursor if has_next else ""
                save_checkpoint(
                    args.checkpoint,
                    {
                        "username": args.username,
                        "window_since": fmt_utc(w.since),
                        "window_until": fmt_utc(w.until),
                        "cursor": cursor,
                        "months_done": months_done,
                        "pages_done": pages_done,
                        "total_seen": total_seen,
                        "total_new": total_new,
                    },
                )

                if not has_next or not next_cursor:
                    # done with this window
                    cursor = ""
                    save_checkpoint(
                        args.checkpoint,
                        {
                            "username": args.username,
                            "window_since": fmt_utc(w.since),
                            "window_until": fmt_utc(w.until),
                            "cursor": cursor,
                            "months_done": months_done,
                            "pages_done": pages_done,
                            "total_seen": total_seen,
                            "total_new": total_new,
                        },
                    )
                    break

                time.sleep(args.sleep)

            # Move to previous month window
            months_done += 1
            w = prev_month_window(w.since)
            cursor = ""
            save_checkpoint(
                args.checkpoint,
                {
                    "username": args.username,
                    "window_since": fmt_utc(w.since),
                    "window_until": fmt_utc(w.until),
                    "cursor": cursor,
                    "months_done": months_done,
                    "pages_done": pages_done,
                    "total_seen": total_seen,
                    "total_new": total_new,
                },
            )

    finally:
        out_f.close()
        conn.close()

    print(f"\nDone. Inserted {total_new} unique tweets. Output JSONL: {args.out} | DB: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
