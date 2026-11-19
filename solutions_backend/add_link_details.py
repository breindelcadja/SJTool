#!/usr/bin/env python3
"""
Looks up each post in bluesky_results.csv on Bluesky and adds what the post
links to, quotes or shows, so the classifier can judge posts that are mostly a
link. Saves the result to bluesky_with_links.csv in this folder.

New columns:
    link_url          the full web address the post links to
    link_title        the title of the linked article
    link_description  the article's short description
    quoted_text       the text of a post it quotes
    image_alt         descriptions of its images, if the author wrote them

Usage, from inside the "solutions or not" folder (no API key needed):
    python add_link_details.py
    python add_link_details.py --input ../other_results.csv

The full process, from inside the "solutions or not" folder:
    python ../bskysearch.py "dengue" --max 100 --out ../bluesky_results.csv
    python add_link_details.py
    python classify_solutions.py --topic dengue
    python make_pitches.py --topic dengue
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = os.environ.get("BSKY_API_URL", "https://public.api.bsky.app/xrpc")
NEW_FIELDS = ["link_url", "link_title", "link_description", "quoted_text",
              "image_alt"]
BATCH = 25  # the most posts Bluesky returns per request


def get(method, params):
    """Call a public Bluesky API method, waiting and retrying if rate limited."""
    url = f"{API}/{method}?{urllib.parse.urlencode(params, doseq=True)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 30)
                print(f"Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
            elif e.code == 400:
                return None  # a deleted account or bad handle, for example
            elif attempt < 4 and e.code >= 500:
                time.sleep(2 ** attempt)
            else:
                raise
        except urllib.error.URLError:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Gave up on {method} after several tries.")


def post_uri(url, dids):
    """Turn https://bsky.app/profile/HANDLE/post/ID into an at:// address."""
    parts = url.rstrip("/").split("/")
    handle, rkey = parts[-3], parts[-1]
    if handle not in dids:
        answer = get("com.atproto.identity.resolveHandle", {"handle": handle})
        dids[handle] = answer["did"] if answer else None
    did = dids[handle]
    return f"at://{did}/app.bsky.feed.post/{rkey}" if did else None


def details(post):
    """Pull the link, quote and image details out of one post from the API."""
    found = dict.fromkeys(NEW_FIELDS, "")
    embed = post.get("embed") or {}
    kind = embed.get("$type", "")

    # A quote post with an image or link has both parts nested inside.
    media, quoted = None, None
    if kind.startswith("app.bsky.embed.recordWithMedia"):
        media = embed.get("media") or {}
        quoted = (embed.get("record") or {}).get("record")
    elif kind.startswith("app.bsky.embed.record"):
        quoted = embed.get("record")
    else:
        media = embed

    media_kind = (media or {}).get("$type", "")
    if media_kind.startswith("app.bsky.embed.external"):
        external = media.get("external") or {}
        found["link_url"] = external.get("uri", "")
        found["link_title"] = external.get("title", "")
        found["link_description"] = external.get("description", "")
    elif media_kind.startswith("app.bsky.embed.images"):
        alts = [img.get("alt", "").strip() for img in media.get("images", [])]
        found["image_alt"] = " | ".join(a for a in alts if a)
    elif media_kind.startswith("app.bsky.embed.video"):
        found["image_alt"] = (media.get("alt") or "").strip()

    if quoted and quoted.get("$type", "").endswith("viewRecord"):
        found["quoted_text"] = (quoted.get("value") or {}).get("text", "")

    # The post text cuts links short ("nytimes.com/2026/11/02/u..."), but the
    # full address is saved separately. Use it if there's no link card.
    if not found["link_url"]:
        for facet in (post.get("record") or {}).get("facets", []):
            for feature in facet.get("features", []):
                if feature.get("$type", "").endswith("#link"):
                    found["link_url"] = feature.get("uri", "")
                    break
            if found["link_url"]:
                break

    return {k: " ".join(v.split()) for k, v in found.items()}  # tidy spacing


def main():
    ap = argparse.ArgumentParser(description="Add link details to Bluesky posts.")
    ap.add_argument("--input", default=HERE.parent / "bluesky_results.csv",
                    type=Path, help="CSV made by bskysearch.py")
    ap.add_argument("--out", default=HERE / "bluesky_with_links.csv", type=Path)
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"Could not find {args.input}")
    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = [c for c in reader.fieldnames if c not in NEW_FIELDS] + NEW_FIELDS
        # The same post can show up twice in a search, so keep one per URL.
        posts = list({row["url"]: row for row in reader}.values())

    dids, uris = {}, {}
    print(f"Looking up {len(posts)} posts...")
    for row in posts:
        uri = post_uri(row["url"], dids)
        if uri:
            uris[uri] = row

    found = {}
    uri_list = list(uris)
    for start in range(0, len(uri_list), BATCH):
        answer = get("app.bsky.feed.getPosts", {"uris": uri_list[start:start + BATCH]})
        for post in (answer or {}).get("posts", []):
            found[post["uri"]] = details(post)
        time.sleep(0.4)  # be polite

    # Bluesky may hand back a slightly different address, so match on the ID too.
    by_rkey = {u.rsplit("/", 1)[-1]: d for u, d in found.items()}
    with_details = missing = 0
    for row in posts:
        rkey = row["url"].rstrip("/").rsplit("/", 1)[-1]
        extra = by_rkey.get(rkey)
        if extra is None:
            missing += 1
            extra = dict.fromkeys(NEW_FIELDS, "")
        elif any(extra.values()):
            with_details += 1
        row.update(extra)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(posts)

    print(f"Added details to {with_details} of {len(posts)} posts.")
    if missing:
        print(f"{missing} posts couldn't be found. They may have been deleted.")
    print(f"Saved to {args.out.name}")


if __name__ == "__main__":
    main()
