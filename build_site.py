#!/usr/bin/env python3
"""
Static site generator for State Dept tweet archive.
Reads statedept_backfill.sqlite + media.sqlite, writes docs/ (or --out) with index, month pages, tweet pages.
Use docs/ for GitHub Pages (Settings → Pages → Source: Deploy from branch → /docs).
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

# Public Git LFS objects are served from this host (not raw.githubusercontent.com).
# Path must match repo default branch + path under repo root (here: docs/media/...).
DEFAULT_GIT_LFS_MEDIA_BASE = (
    "https://media.githubusercontent.com/media/lockdown-systems/statedept-archive/main/docs"
)

# Vendored at docs/assets/logo-wide.svg; served like media via Git LFS CDN (see --media-base).
LOCKDOWN_HOME = "https://lockdown.systems/"
LOGO_PATH_UNDER_DOCS = "assets/logo-wide.svg"


def lockdown_byline_html(logo_src: str) -> str:
    return (
        f'    <p class="site-byline">\n'
        f'      <span class="site-byline-label">A project of</span>\n'
        f'      <a class="lockdown-logo-link" href="{LOCKDOWN_HOME}" target="_blank" rel="noopener noreferrer">\n'
        f'        <img src="{logo_src}" alt="Lockdown Systems" width="400" height="160" loading="lazy" decoding="async" />\n'
        f'      </a>\n'
        f"    </p>\n"
    )


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
    p.add_argument("--out", default="docs", help="Output directory for GitHub Pages (default: docs)")
    p.add_argument(
        "--media-base",
        default=DEFAULT_GIT_LFS_MEDIA_BASE,
        help="Base URL for media in tweet JSON (Git LFS CDN; no trailing slash). Override for fork/branch.",
    )
    p.add_argument(
        "--relative-media",
        action="store_true",
        help="Use ../media/... URLs instead of Git LFS CDN (only if Pages serves real binaries, not pointers).",
    )
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
    # So GitHub Pages serves static files without Jekyll
    with open(os.path.join(out, ".nojekyll"), "wb") as f:
        f.write(b"")

    tweets_conn = sqlite3.connect(args.tweets_db)
    tweets_conn.row_factory = sqlite3.Row
    media_conn = sqlite3.connect(args.media_db)
    media_conn.row_factory = sqlite3.Row

    # Media count per tweet_id (ok=1 only)
    media_count: Dict[str, int] = {}
    for row in media_conn.execute("SELECT tweet_id, COUNT(*) AS c FROM media WHERE ok = 1 GROUP BY tweet_id"):
        media_count[row["tweet_id"]] = row["c"]

    # Media rows per tweet_id for tweet pages: local_path is e.g. media/<id>/file.jpg; URLs use Git LFS CDN by default
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

    logo_src = f"{args.media_base.rstrip('/')}/{LOGO_PATH_UNDER_DOCS}"
    byline = lockdown_byline_html(logo_src)

    # index.html
    total_tweets = sum(m["tweet_count"] for m in months_payload)
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>State Dept X Archive</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <div class="site-wrap">
  <header>
    <h1>State Dept X Archive</h1>
""" + byline + """  </header>
  <main>
    <section class="about">
      <h2>Why this archive exists</h2>
      <p>In February 2026, the State Department <a href="https://www.npr.org/2026/02/07/nx-s1-5704785/state-department-trump-posts-x">announced</a> it would delete all posts on its official X accounts made before Trump returned to office on January 20, 2025. The posts would be internally archived but no longer publicly accessible - anyone wanting to see them would have to file a Freedom of Information Act (FOIA) request.</p>
      <p>These posts are not just press statements. They include the day-to-day record of U.S. diplomacy - often the only public record of those moments.</p>
      <p>This archive preserves <strong>""" + f"{total_tweets:,}" + """ tweets</strong> and their associated media from the @StateDept account, spanning 2008–2025.</p>
    </section>
    <h2>Browse by month</h2>
    <ul class="month-list">
""" + "\n".join(
        f'      <li><a href="month/{m["year_month"]}.html">{m["year_month"]}<span class="month-meta"> · {m["tweet_count"]} tweets</span></a></li>'
        for m in months_payload
    ) + """
    </ul>
  </main>
  </div>
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
  <div class="site-wrap">
  <header>
    <h1><a href="../index.html">State Dept archive</a></h1>
    <p class="month-title">{{year_month}}</p>
""" + byline + """  </header>
  <main>
    <div id="tweet-list"></div>
    <button id="load-more" style="display:none;">Load more</button>
    <p id="done-msg" style="display:none;"></p>
  </main>
  </div>
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

    # Tweet page: embedded JSON, one file per tweet (Twitter-style layout)
    tweet_html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>State Dept — {{tweet_id}}</title>
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <div class="site-wrap">
  <header>
    <h1><a href="../index.html">State Dept archive</a></h1>
""" + byline + """  </header>
  <main id="tweet-page">
    <script type="application/json" id="tweet-data">{{tweet_data}}</script>
    <div class="tweet-card" id="tweet-content"></div>
  </main>
  </div>
  <script>
    (function() {
      function formatTweetDate(iso) {
        try {
          var d = new Date(iso);
          var time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
          var date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
          return time + " \\u00b7 " + date;
        } catch (e) { return iso; }
      }
      var el = document.getElementById("tweet-data");
      var data = JSON.parse(el.textContent);
      var root = document.getElementById("tweet-content");
      var avatar = document.createElement("div");
      avatar.className = "tweet-avatar";
      avatar.textContent = "S";
      root.appendChild(avatar);
      var contentWrap = document.createElement("div");
      contentWrap.className = "tweet-content-wrap";
      var header = document.createElement("div");
      header.className = "tweet-header";
      var name = document.createElement("span");
      name.className = "tweet-name";
      name.textContent = "State Dept";
      var handle = document.createElement("span");
      handle.className = "tweet-handle";
      handle.textContent = " @StateDept";
      var dot = document.createElement("span");
      dot.className = "tweet-dot";
      dot.textContent = " \\u00b7 ";
      var meta = document.createElement("span");
      meta.className = "tweet-meta";
      meta.textContent = formatTweetDate(data.created_at);
      header.appendChild(name);
      header.appendChild(handle);
      header.appendChild(dot);
      header.appendChild(meta);
      contentWrap.appendChild(header);
      var text = document.createElement("div");
      text.className = "tweet-text";
      text.textContent = data.text;
      contentWrap.appendChild(text);
      var mediaRoot = document.createElement("div");
      mediaRoot.className = "tweet-media";
      for (var i = 0; i < (data.media || []).length; i++) {
        var m = data.media[i];
        var url = m.url;
        if (m.kind === "video" || (url && /\\.(mp4|webm|mov)(\\?|$)/i.test(url))) {
          var v = document.createElement("video");
          v.controls = true;
          v.preload = "metadata";
          var source = document.createElement("source");
          source.src = url;
          source.type = "video/mp4";
          v.appendChild(source);
          mediaRoot.appendChild(v);
        } else {
          var img = document.createElement("img");
          img.src = url;
          img.alt = "";
          mediaRoot.appendChild(img);
        }
      }
      contentWrap.appendChild(mediaRoot);
      root.appendChild(contentWrap);
    })();
  </script>
</body>
</html>
"""
    def media_url(local_path: str) -> str:
        if args.relative_media:
            return f"../{local_path}"
        return f"{args.media_base.rstrip('/')}/{local_path}"

    written = 0
    for tid, info in tweet_rows.items():
        media_list = media_by_tweet.get(tid, [])
        media_payload = [{"url": media_url(path), "kind": kind} for path, kind in media_list]
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

    # assets/style.css — Twitter/X–style feed
    style_css = """/* State Dept archive — Twitter/X–style layout */
:root {
  --bg: #ffffff;
  --fg: #0f1419;
  --muted: #657786;
  --link: #1d9bf0;
  --border: #e1e8ed;
  --hover-bg: #f5f8fa;
  --avatar-bg: #1d9bf0;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--fg);
  margin: 0;
  padding: 0;
  line-height: 1.3125;
  font-size: 15px;
}
.site-wrap { max-width: 600px; margin: 0 auto; min-height: 100vh; }
header {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  padding: 1rem 1rem 0.75rem;
}
header h1 { font-size: 1.25rem; font-weight: 800; margin: 0; }
header h1 a { color: var(--fg); text-decoration: none; }
header h1 a:hover { text-decoration: underline; }
header p.month-title { margin: 0.25rem 0 0; color: var(--muted); font-size: 13px; }

/* Lockdown Systems branding */
.site-byline {
  margin: 0.75rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.35rem;
}
.site-byline-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.lockdown-logo-link {
  display: inline-block;
  line-height: 0;
}
.lockdown-logo-link img {
  height: 64px;
  width: auto;
  max-width: min(100%, 420px);
  display: block;
}
.lockdown-logo-link:hover { opacity: 0.92; }
main { padding: 0; }
main h2 { font-size: 1rem; font-weight: 700; margin: 1.5rem 1rem 0.5rem; color: var(--fg); }

/* About section */
.about {
  padding: 1rem;
  border-bottom: 1px solid var(--border);
  line-height: 1.5;
}
.about h2 { margin: 0 0 0.75rem; font-size: 1rem; font-weight: 700; }
.about p { margin: 0 0 0.75rem; color: var(--fg); font-size: 15px; }
.about p:last-child { margin-bottom: 0; }
.about a { color: var(--link); }
.about a:hover { text-decoration: underline; }
.about strong { font-weight: 600; }

/* Index: month list as simple links */
.month-list { list-style: none; padding: 0.5rem 0; margin: 0; }
.month-list li { margin: 0; border-bottom: 1px solid var(--border); }
.month-list a {
  display: block;
  padding: 1rem 1rem;
  color: var(--fg);
  text-decoration: none;
  font-weight: 500;
}
.month-list a:hover { background: var(--hover-bg); }
.month-list .month-meta { font-weight: 400; color: var(--muted); font-size: 13px; }

/* Tweet card: avatar left, body right (Twitter feed row) */
.tweet-card {
  display: flex;
  gap: 12px;
  padding: 1rem 1rem;
  border-bottom: 1px solid var(--border);
  text-decoration: none;
  color: inherit;
  transition: background 0.15s ease;
}
.tweet-card:hover { background: var(--hover-bg); }
.tweet-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--avatar-bg);
  color: #fff;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 1.1rem;
}
.tweet-body { flex: 1; min-width: 0; }
.tweet-header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px;
  margin-bottom: 2px;
}
.tweet-name { font-weight: 700; color: var(--fg); }
.tweet-handle { color: var(--muted); font-size: 13px; }
.tweet-dot { color: var(--muted); font-size: 13px; }
.tweet-meta { color: var(--muted); font-size: 13px; }
.tweet-text {
  font-size: 15px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.tweet-text.truncated { max-height: 4.5em; overflow: hidden; }
.tweet-card .tweet-link {
  color: var(--link);
  font-size: 14px;
  margin-top: 4px;
  display: inline-block;
}
.tweet-card .tweet-link:hover { text-decoration: underline; }

#load-more {
  margin: 0;
  padding: 1rem 1rem;
  width: 100%;
  background: var(--bg);
  color: var(--link);
  border: none;
  border-bottom: 1px solid var(--border);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}
#load-more:hover { background: var(--hover-bg); }
#done-msg { padding: 1rem; color: var(--muted); font-size: 14px; text-align: center; }

/* Single tweet page: same row layout */
#tweet-page .tweet-card { flex-direction: row; }
#tweet-page .tweet-card:hover { background: transparent; }
#tweet-page .tweet-content-wrap { flex: 1; min-width: 0; padding: 0; }
#tweet-page .tweet-header { margin-bottom: 4px; }
#tweet-page .tweet-meta { margin: 0 0 8px; }
#tweet-page .tweet-text { white-space: pre-wrap; word-break: break-word; margin-bottom: 12px; }
.tweet-media {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  border-radius: 16px;
  overflow: hidden;
}
.tweet-media img, .tweet-media video {
  max-width: 100%;
  height: auto;
  border-radius: 12px;
  background: var(--border);
  border: 1px solid var(--border);
}
"""
    with open(os.path.join(out, "assets", "style.css"), "w", encoding="utf-8") as f:
        f.write(style_css)
    print("Wrote assets/style.css", flush=True)

    # assets/app.js — load month JSON, render tweets (Twitter-style cards), "Load more"
    app_js = """document.addEventListener("DOMContentLoaded", function() {
  if (typeof YEAR_MONTH === "undefined") return;
  const listEl = document.getElementById("tweet-list");
  const loadMoreBtn = document.getElementById("load-more");
  const doneMsg = document.getElementById("done-msg");
  if (!listEl) return;

  function formatTweetDate(iso) {
    try {
      const d = new Date(iso);
      const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
      const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
      return time + " \\u00b7 " + date;
    } catch (e) { return iso; }
  }

  fetch("../data/" + YEAR_MONTH + ".json")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      const tweets = data.tweets || [];
      let shown = 0;
      const pageSize = typeof PAGE_SIZE !== "undefined" ? PAGE_SIZE : 50;

      function renderTweet(t) {
        const card = document.createElement("a");
        card.className = "tweet-card";
        card.href = "../tweet/" + t.id + ".html";
        const avatar = document.createElement("div");
        avatar.className = "tweet-avatar";
        avatar.textContent = "S";
        card.appendChild(avatar);
        const body = document.createElement("div");
        body.className = "tweet-body";
        const header = document.createElement("div");
        header.className = "tweet-header";
        const name = document.createElement("span");
        name.className = "tweet-name";
        name.textContent = "State Dept";
        const handle = document.createElement("span");
        handle.className = "tweet-handle";
        handle.textContent = "@StateDept";
        const dot = document.createElement("span");
        dot.className = "tweet-dot";
        dot.textContent = " \\u00b7 ";
        const meta = document.createElement("span");
        meta.className = "tweet-meta";
        meta.textContent = formatTweetDate(t.created_at);
        header.appendChild(name);
        header.appendChild(handle);
        header.appendChild(dot);
        header.appendChild(meta);
        body.appendChild(header);
        const text = document.createElement("div");
        text.className = "tweet-text" + (t.text.length > 200 ? " truncated" : "");
        text.textContent = t.text.length > 200 ? t.text.slice(0, 200) + "\\u2026" : t.text;
        body.appendChild(text);
        if (t.media_count > 0) {
          const mediaLink = document.createElement("span");
          mediaLink.className = "tweet-link";
          mediaLink.textContent = t.media_count + " media attachment" + (t.media_count !== 1 ? "s" : "");
          body.appendChild(mediaLink);
        }
        card.appendChild(body);
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
