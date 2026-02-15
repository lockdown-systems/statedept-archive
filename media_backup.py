#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests


# ---------- DB ----------

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            url TEXT PRIMARY KEY,
            tweet_id TEXT,
            local_path TEXT,
            sha256 TEXT,
            fetched_at TEXT,
            ok INTEGER,
            kind TEXT
        )
        """
    )
    conn.commit()
    return conn

def media_already_done(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT ok FROM media WHERE url = ?", (url,)).fetchone()
    return row is not None and row[0] == 1


# ---------- URL helpers ----------

def is_probably_media_url(u: str) -> bool:
    if not isinstance(u, str):
        return False
    if not u.startswith(("http://", "https://")):
        return False
    # Don't fetch t.co shortlinks (they resolve to tweet pages or tracking)
    if "t.co/" in u:
        return False
    return True

def guess_ext_from_url(url: str) -> str:
    path = urlparse(url).path
    base = os.path.basename(path)
    _, ext = os.path.splitext(base)
    ext = ext.lower().strip(".")
    if ext and re.fullmatch(r"[a-z0-9]{1,6}", ext):
        return ext
    return "bin"


# ---------- Media extraction (tailored to your tweet objects) ----------

def extract_media(tweet: Dict[str, Any], prefer_mp4: bool = True) -> List[Tuple[str, str]]:
    """
    Returns list of (url, kind) where kind in {"photo","video","other"}.
    Tailored for twitterapi.io structure: extendedEntities.media[].
    For video, picks highest bitrate video/mp4 by default.
    """
    out: List[Tuple[str, str]] = []

    ee = tweet.get("extendedEntities") or {}
    media = ee.get("media") or []
    if isinstance(media, list):
        for m in media:
            if not isinstance(m, dict):
                continue
            mtype = (m.get("type") or "").lower()

            # Photos: direct pbs.twimg.com URL
            if mtype == "photo":
                u = m.get("media_url_https") or m.get("media_url")
                if is_probably_media_url(u):
                    out.append((u, "photo"))
                continue

            # Videos / gifs: choose best mp4 if present
            if mtype in ("video", "animated_gif"):
                vi = m.get("video_info") or {}
                variants = vi.get("variants") or []
                best_mp4_url = None
                best_bitrate = -1

                if isinstance(variants, list):
                    for var in variants:
                        if not isinstance(var, dict):
                            continue
                        vurl = var.get("url")
                        ctype = (var.get("content_type") or "").lower()
                        bitrate = var.get("bitrate")
                        if not is_probably_media_url(vurl):
                            continue

                        # Prefer MP4, skip HLS unless user wants it
                        if prefer_mp4:
                            if ctype == "video/mp4":
                                b = int(bitrate) if isinstance(bitrate, int) else 0
                                if b > best_bitrate:
                                    best_bitrate = b
                                    best_mp4_url = vurl
                        else:
                            # If not preferring mp4, allow whatever is there
                            out.append((vurl, "video"))

                if prefer_mp4 and best_mp4_url:
                    out.append((best_mp4_url, "video"))
                else:
                    # Fall back to thumbnail image if no mp4
                    thumb = m.get("media_url_https") or m.get("media_url")
                    if is_probably_media_url(thumb):
                        out.append((thumb, "video_thumb"))
                continue

            # Fallback: sometimes still have media_url_https
            u = m.get("media_url_https") or m.get("media_url")
            if is_probably_media_url(u):
                out.append((u, "other"))

    # Dedup while preserving order
    seen: Set[str] = set()
    deduped: List[Tuple[str, str]] = []
    for u, k in out:
        if u not in seen:
            seen.add(u)
            deduped.append((u, k))
    return deduped


# ---------- Download ----------

def download_one(url: str, dest_path: str, timeout: int = 120) -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            h = hashlib.sha256()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 128):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
            return True, h.hexdigest(), None
    except Exception as e:
        return False, None, str(e)


# ---------- Main ----------

def main() -> int:
    p = argparse.ArgumentParser(description="Download media referenced in existing twitterapi.io tweet JSONL (no API calls).")
    p.add_argument("--jsonl", required=True, help="Path to JSONL file containing tweet objects")
    p.add_argument("--media-dir", default="media", help="Directory to save media (default: ./media)")
    p.add_argument("--db", default="media.sqlite", help="SQLite DB for media dedupe (default: media.sqlite)")
    p.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between downloads (default: 0)")
    p.add_argument("--max-per-tweet", type=int, default=0, help="Optional cap on media per tweet (0 = no cap)")
    p.add_argument("--prefer-mp4", action="store_true", default=True, help="Prefer highest-bitrate MP4 for videos (default: true)")
    p.add_argument("--allow-hls", action="store_true", help="If set, allow downloading .m3u8 variant URLs too (not recommended).")
    args = p.parse_args()

    conn = init_db(args.db)

    downloaded = 0
    scanned = 0

    with open(args.jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tweet = json.loads(line)
            except json.JSONDecodeError:
                continue

            scanned += 1
            tid = str(tweet.get("id", "")).strip() or "unknown"

            # Extract (url, kind)
            prefer_mp4 = True
            media_items = extract_media(tweet, prefer_mp4=prefer_mp4)

            if args.max_per_tweet and len(media_items) > args.max_per_tweet:
                media_items = media_items[: args.max_per_tweet]

            for idx, (url, kind) in enumerate(media_items):
                if not args.allow_hls and url.endswith(".m3u8"):
                    continue
                if media_already_done(conn, url):
                    continue

                ext = guess_ext_from_url(url)
                url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
                rel_path = os.path.join(args.media_dir, tid, f"{idx:02d}_{kind}_{url_hash}.{ext}")
                abs_path = os.path.abspath(rel_path)

                ok, sha, err = download_one(url, abs_path)
                conn.execute(
                    "INSERT OR REPLACE INTO media(url, tweet_id, local_path, sha256, fetched_at, ok, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        url,
                        tid,
                        rel_path,
                        sha,
                        datetime.now(timezone.utc).isoformat(),
                        1 if ok else 0,
                        kind,
                    ),
                )
                conn.commit()

                if ok:
                    downloaded += 1
                    print(f"[media] {rel_path}")
                else:
                    print(f"[media] FAILED {url} ({err})", file=sys.stderr)

                if args.sleep:
                    time.sleep(args.sleep)

    print(f"\nDone. Scanned {scanned} tweets. Downloaded {downloaded} media files.")
    print(f"Media dir: {os.path.abspath(args.media_dir)} | DB: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())