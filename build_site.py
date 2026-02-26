#!/usr/bin/env python3
"""
Static site generator for State Dept tweet archive.
Reads statedept_backfill.sqlite + media.sqlite, writes site/ with index, month pages, tweet pages.
"""
import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

# Twitter created_at format: "Fri Apr 01 00:13:57 +0000 2016"
CREATED_AT_FMT = "%a %b %d %H:%M:%S %z %Y"


def parse_created_at(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), CREATED_AT_FMT)
    except ValueError:
        return None


def month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def main() -> int:
    p = argparse.ArgumentParser(description="Build static site from tweet + media DBs.")
    p.add_argument("--tweets-db", default="statedept_backfill.sqlite", help="Tweets SQLite path")
    p.add_argument("--media-db", default="media.sqlite", help="Media SQLite path")
    p.add_argument("--out", default="site", help="Output directory (default: site)")
    args = p.parse_args()

    if not os.path.exists(args.tweets_db):
        print(f"ERROR: Tweets DB not found: {args.tweets_db}", flush=True)
        return 2
    if not os.path.exists(args.media_db):
        print(f"ERROR: Media DB not found: {args.media_db}", flush=True)
        return 2

    out = args.out
    os.makedirs(os.path.join(out, "data"), exist_ok=True)
    os.makedirs(os.path.join(out, "month"), exist_ok=True)
    os.makedirs(os.path.join(out, "tweet"), exist_ok=True)
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)

    tweets_conn = sqlite3.connect(args.tweets_db)
    tweets_conn.row_factory = sqlite3.Row
    media_conn = sqlite3.connect(args.media_db)
    media_conn.row_factory = sqlite3.Row

    # Media count per tweet_id (ok=1 only)
    media_count: Dict[str, int] = {}
    for row in media_conn.execute("SELECT tweet_id, COUNT(*) AS c FROM media WHERE ok = 1 GROUP BY tweet_id"):
        media_count[row["tweet_id"]] = row["c"]

    # Media rows per tweet_id for tweet pages: (local_path, kind) -> URL is / + local_path
    media_by_tweet: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for row in media_conn.execute("SELECT tweet_id, local_path, kind FROM media WHERE ok = 1 ORDER BY local_path"):
        media_by_tweet[row["tweet_id"]].append((row["local_path"], row["kind"]))

    # Load all tweets: id, created_at, text; group by month (newest first)
    tweets_by_month: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    tweet_rows: Dict[str, Dict[str, Any]] = {}  # id -> { created_at_iso, text } for tweet pages

    for row in tweets_conn.execute("SELECT id, created_at, json FROM tweets"):
        tid = row["id"]
        created_at_raw = row["created_at"]
        dt = parse_created_at(created_at_raw)
        if not dt:
            continue
        try:
            blob = json.loads(row["json"])
        except json.JSONDecodeError:
            continue
        text = (blob.get("text") or "").strip()
        created_at_iso = dt.isoformat()
        mc = media_count.get(tid, 0)
        key = month_key(dt)
        tweets_by_month[key].append({
            "id": tid,
            "created_at": created_at_iso,
            "text": text,
            "media_count": mc,
        })
        tweet_rows[tid] = {"created_at": created_at_iso, "text": text}

    # Sort each month's tweets newest first
    for key in tweets_by_month:
        tweets_by_month[key].sort(key=lambda t: t["created_at"], reverse=True)

    months_sorted = sorted(tweets_by_month.keys(), reverse=True)
    months_payload = [{"year_month": m, "tweet_count": len(tweets_by_month[m])} for m in months_sorted]

    with open(os.path.join(out, "data", "months.json"), "w", encoding="utf-8") as f:
        json.dump(months_payload, f, ensure_ascii=False, indent=2)

    print(f"Wrote data/months.json ({len(months_sorted)} months)", flush=True)

    for ym in months_sorted:
        payload = {"year_month": ym, "tweets": tweets_by_month[ym]}
        with open(os.path.join(out, "data", f"{ym}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote data/YYYY-MM.json for {len(months_sorted)} months", flush=True)

    # index.html
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>State Dept archive</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header>
    <h1>State Dept archive</h1>
    <p>Browse by month</p>
  </header>
  <main>
    <ul class="month-list">
""" + "\n".join(
        f'      <li><a href="month/{m["year_month"]}.html">{m["year_month"]}</a> ({m["tweet_count"]} tweets)</li>'
        for m in months_payload
    ) + """
    </ul>
  </main>
</body>
</html>
"""
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    print("Wrote index.html", flush=True)

    # Month page template: loads data/YYYY-MM.json via JS, render with "Load more" (50 per page)
    month_html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>State Dept — {{year_month}}</title>
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <header>
    <h1><a href="../index.html">State Dept archive</a></h1>
    <p>Month: {{year_month}}</p>
  </header>
  <main>
    <div id="tweet-list"></div>
    <button id="load-more" style="display:none;">Load more</button>
    <p id="done-msg" style="display:none;"></p>
  </main>
  <script>
    const YEAR_MONTH = "{{year_month}}";
    const PAGE_SIZE = 50;
  </script>
  <script src="../assets/app.js"></script>
</body>
</html>
"""
    for ym in months_sorted:
        html = month_html_template.replace("{{year_month}}", ym)
        with open(os.path.join(out, "month", f"{ym}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    print(f"Wrote month/YYYY-MM.html for {len(months_sorted)} months", flush=True)

    # Tweet page: embedded JSON, one file per tweet
    tweet_html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>State Dept — {{tweet_id}}</title>
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <header>
    <h1><a href="../index.html">State Dept archive</a></h1>
  </header>
  <main id="tweet-page">
    <script type="application/json" id="tweet-data">{{tweet_data}}</script>
    <div id="tweet-content"></div>
  </main>
  <script>
    (function() {
      const el = document.getElementById("tweet-data");
      const data = JSON.parse(el.textContent);
      const root = document.getElementById("tweet-content");
      const created = document.createElement("p");
      created.className = "tweet-meta";
      created.textContent = data.created_at;
      root.appendChild(created);
      const text = document.createElement("div");
      text.className = "tweet-text";
      text.textContent = data.text;
      root.appendChild(text);
      const mediaRoot = document.createElement("div");
      mediaRoot.className = "tweet-media";
      for (const m of data.media || []) {
        const url = m.url;
        if (m.kind === "video" || (url && /\\.(mp4|webm|mov)(\\?|$)/i.test(url))) {
          const v = document.createElement("video");
          v.controls = true;
          v.preload = "metadata";
          v.src = url;
          v.loading = "lazy";
          mediaRoot.appendChild(v);
        } else {
          const img = document.createElement("img");
          img.src = url;
          img.alt = "";
          img.loading = "lazy";
          mediaRoot.appendChild(img);
        }
      }
      root.appendChild(mediaRoot);
    })();
  </script>
</body>
</html>
"""
    written = 0
    for tid, info in tweet_rows.items():
        media_list = media_by_tweet.get(tid, [])
        media_payload = [{"url": "/" + path, "kind": kind} for path, kind in media_list]
        tweet_data = {
            "id": tid,
            "created_at": info["created_at"],
            "text": info["text"],
            "media": media_payload,
        }
        json_str = json.dumps(tweet_data, ensure_ascii=False)
        # Escape for embedding in HTML: </script> must not appear in the string
        json_str = json_str.replace("</", "<\\/")
        html = tweet_html_template.replace("{{tweet_id}}", tid).replace("{{tweet_data}}", json_str)
        with open(os.path.join(out, "tweet", f"{tid}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        written += 1
        if written % 5000 == 0:
            print(f"  tweet pages: {written}...", flush=True)
    print(f"Wrote {written} tweet pages", flush=True)

    # assets/style.css
    style_css = """/* State Dept archive — minimal layout */
:root {
  --bg: #0f0f0f;
  --fg: #e0e0e0;
  --muted: #888;
  --link: #6eb8ff;
  --border: #333;
}
* { box-sizing: border-box; }
body {
  font-family: system-ui, sans-serif;
  background: var(--bg);
  color: var(--fg);
  margin: 0;
  padding: 1rem;
  max-width: 640px;
  margin-left: auto;
  margin-right: auto;
  line-height: 1.5;
}
header { margin-bottom: 1.5rem; }
header h1 { font-size: 1.25rem; margin: 0; }
header h1 a { color: var(--fg); text-decoration: none; }
header h1 a:hover { color: var(--link); }
header p { margin: 0.25rem 0 0; color: var(--muted); font-size: 0.9rem; }
.month-list { list-style: none; padding: 0; margin: 0; }
.month-list li { margin: 0.5rem 0; }
.month-list a { color: var(--link); }
.tweet-card {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
}
.tweet-card .tweet-meta { font-size: 0.8rem; color: var(--muted); margin: 0 0 0.25rem; }
.tweet-card .tweet-text {
  font-size: 0.95rem;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.tweet-card .tweet-text.truncated { max-height: 4.5em; overflow: hidden; }
.tweet-card a { color: var(--link); }
#load-more {
  margin: 1rem 0;
  padding: 0.5rem 1rem;
  background: var(--border);
  color: var(--fg);
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
#load-more:hover { background: #444; }
#tweet-page .tweet-meta { color: var(--muted); font-size: 0.9rem; margin: 0 0 0.5rem; }
#tweet-page .tweet-text { white-space: pre-wrap; word-break: break-word; margin-bottom: 1rem; }
.tweet-media { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; }
.tweet-media img, .tweet-media video {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  background: var(--border);
}
"""
    with open(os.path.join(out, "assets", "style.css"), "w", encoding="utf-8") as f:
        f.write(style_css)
    print("Wrote assets/style.css", flush=True)

    # assets/app.js — load month JSON, render tweets, "Load more"
    app_js = """document.addEventListener("DOMContentLoaded", function() {
  if (typeof YEAR_MONTH === "undefined") return;
  const listEl = document.getElementById("tweet-list");
  const loadMoreBtn = document.getElementById("load-more");
  const doneMsg = document.getElementById("done-msg");
  if (!listEl) return;

  fetch("../data/" + YEAR_MONTH + ".json")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      const tweets = data.tweets || [];
      let shown = 0;
      const pageSize = typeof PAGE_SIZE !== "undefined" ? PAGE_SIZE : 50;

      function renderTweet(t) {
        const card = document.createElement("div");
        card.className = "tweet-card";
        const meta = document.createElement("p");
        meta.className = "tweet-meta";
        meta.textContent = t.created_at;
        card.appendChild(meta);
        const text = document.createElement("div");
        text.className = "tweet-text" + (t.text.length > 200 ? " truncated" : "");
        text.textContent = t.text.length > 200 ? t.text.slice(0, 200) + "…" : t.text;
        card.appendChild(text);
        const link = document.createElement("a");
        link.href = "../tweet/" + t.id + ".html";
        link.textContent = "View tweet" + (t.media_count > 0 ? " (" + t.media_count + " media)" : "");
        link.style.marginTop = "0.5rem";
        link.style.display = "inline-block";
        card.appendChild(link);
        return card;
      }

      function showNext() {
        const end = Math.min(shown + pageSize, tweets.length);
        for (let i = shown; i < end; i++) {
          listEl.appendChild(renderTweet(tweets[i]));
        }
        shown = end;
        if (shown >= tweets.length) {
          loadMoreBtn.style.display = "none";
          doneMsg.style.display = "block";
          doneMsg.textContent = "All " + tweets.length + " tweets shown.";
        } else {
          loadMoreBtn.style.display = "block";
          loadMoreBtn.textContent = "Load more (" + (tweets.length - shown) + " left)";
        }
      }

      loadMoreBtn.addEventListener("click", function() { showNext(); });
      showNext();
    })
    .catch(function(e) {
      listEl.innerHTML = "<p>Failed to load month data.</p>";
      console.error(e);
    });
});
"""
    with open(os.path.join(out, "assets", "app.js"), "w", encoding="utf-8") as f:
        f.write(app_js)
    print("Wrote assets/app.js", flush=True)

    tweets_conn.close()
    media_conn.close()
    print("Done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
