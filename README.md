# Cyd Research

Backup tweet text (hits the `twitterapi.io` API):

```
pip install requests
export TWITTERAPI_IO_KEY="YOUR_KEY"
python3 backup.py --until-utc=2026-01-20_17:00:00_UTC
```

It dumps to a JSONL and a sqlitedb.

Now take the JSONL and fetch the media contents:

```
python3 media_backup.py \
  --jsonl statedept_backfill.jsonl \
  --media-dir media \
  --sleep 0.2
```

## Static Site

```
python3 build_site.py
cd docs
python3 -m http.server 8000
```