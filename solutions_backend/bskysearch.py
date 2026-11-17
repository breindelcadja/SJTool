#!/usr/bin/env python3
"""
Keyword search on Bluesky posts via the AT Protocol API.

Setup:
    For the first time using it, run this in terminal first
    pip install requests

Auth (recommended; post search may require it):
    1. Bluesky > Settings > Privacy and Security > App Passwords > Add
    Then run these two export lines in terminal
    2. export BSKY_HANDLE="yourname.bsky.social"
       export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"

Usage examples:
    python bluesky_search.py "pothole" --max 200
    python bluesky_search.py "neighborhood cleanup" --sort latest --lang en --out results.csv
    python bluesky_search.py "mutual aid" --since 2026-10-01T00:00:00Z
"""

import argparse
import csv
import os
import sys
import time

import requests

PDS_HOST = "https://bsky.social/xrpc"            # used when logged in
PUBLIC_HOST = "https://public.api.bsky.app/xrpc"  # used when not logged in


class BlueskyAccessError(Exception):
    """Raised when Bluesky refuses the request (usually: log in first)."""

"""
Just giving authorization to login to the bluesky account
"""
def login(handle: str, app_password: str) -> str:
    """Create a session and return the access token."""
    r = requests.post(
        f"{PDS_HOST}/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["accessJwt"]

""" 
Function for going through the posts, first finds all posts containing keyword, if 'lang' is marked in the query, checks if they match,
if 'since' is marked in the query, checks if they match, if 'until' is marked in the query, checks
if they match. Then counts the amount of posts that match the criteria and returns them, exported to "bluesky_results.csv".
"""
def search_posts(query, max_results=100, sort="latest", lang=None,
                 since=None, until=None, token=None, delay=0.4):
    """Yield post dicts matching the query, paginating until max_results."""
    host = PDS_HOST if token else PUBLIC_HOST
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params = {"q": query, "sort": sort, "limit": min(100, max_results)}
    if lang:
        params["lang"] = lang
    if since:
        params["since"] = since
    if until:
        params["until"] = until

    fetched = 0
    cursor = None
    while fetched < max_results:
        if cursor:
            params["cursor"] = cursor
        params["limit"] = min(100, max_results - fetched)

        r = requests.get(f"{host}/app.bsky.feed.searchPosts",
                         params=params, headers=headers, timeout=30)

        if r.status_code == 429:  # rate limited: wait and retry
            wait = int(r.headers.get("Retry-After", 30))
            print(f"Rate limited, waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            raise BlueskyAccessError("Access denied. Search may require login: "
                                     "set BSKY_HANDLE and BSKY_APP_PASSWORD.")
        r.raise_for_status()

        data = r.json()
        posts = data.get("posts", [])
        if not posts:
            break

        for p in posts:
            fetched += 1
            handle = p["author"]["handle"]
            rkey = p["uri"].rsplit("/", 1)[-1]
            yield {
                "created_at": p["record"].get("createdAt", ""),
                "author": handle,
                "text": p["record"].get("text", "").replace("\n", " "),
                "likes": p.get("likeCount", 0),
                "reposts": p.get("repostCount", 0),
                "replies": p.get("replyCount", 0),
                "url": f"https://bsky.app/profile/{handle}/post/{rkey}",
                **_link_card(p),
            }
            if fetched >= max_results:
                return

        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(delay)  # be polite


def _link_card(p):
    """Title and description of a link card attached to the post, if any.
    classify_solutions.py and make_pitches.py read these two columns."""
    embed = p.get("embed") or {}
    if "media" in embed:  # a quote post with media attached
        embed = embed["media"] or {}
    ext = embed.get("external") or {}
    return {"link_url": ext.get("uri", ""),
            "link_title": ext.get("title", ""),
            "link_description": ext.get("description", "")}


def main():
    ap = argparse.ArgumentParser(description="Search Bluesky posts by keyword.")
    ap.add_argument("query", help='Search terms, e.g. "pothole" or "#mutualaid"')
    ap.add_argument("--max", type=int, default=100, help="Max posts to fetch")
    ap.add_argument("--sort", choices=["latest", "top"], default="latest")
    ap.add_argument("--lang", help="Language code, e.g. en")
    ap.add_argument("--since", help="ISO datetime, e.g. 2026-10-01T00:00:00Z")
    ap.add_argument("--until", help="ISO datetime")
    ap.add_argument("--out", default="bluesky_results.csv", help="CSV output path")
    args = ap.parse_args()

    token = None
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if handle and pw:
        token = login(handle, pw)
    else:
        print("No credentials found; trying unauthenticated search...",
              file=sys.stderr)

    fields = ["created_at", "author", "text", "likes", "reposts", "replies", "url",
              "link_title", "link_description"]
    count = 0
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        try:
            for post in search_posts(args.query, args.max, args.sort, args.lang,
                                     args.since, args.until, token):
                w.writerow(post)
                count += 1
        except BlueskyAccessError as e:
            sys.exit(str(e))

    print(f"Saved {count} posts to {args.out}")


if __name__ == "__main__":
    main()
