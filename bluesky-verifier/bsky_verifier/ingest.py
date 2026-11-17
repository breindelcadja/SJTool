"""
Step 0: read the scraper's CSV and clean it.

What this handles (all seen in the dengue sample):
  * mixed timestamp formats  ("...Z", "...148Z", "+00:00", "-03:00", microseconds)
  * exact duplicate rows     (same post URL twice)
  * empty posts
  * links inside the text, including Bluesky's truncated display links
  * link-only posts          (nothing but a URL)
  * non-English posts        (simple stopword vote; Phase 1 is English only)

It does NOT decide whether a post is a solution. That is the classifier's job
(steps 1-2 of the product), which we do not rebuild here.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlparse

from .models import Link, Post

# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------


def parse_timestamp(raw: str) -> Optional[datetime]:
    """Parse any ISO-8601 variant in the sample and return UTC.

    Python 3.11+ `fromisoformat` accepts 'Z', offsets like -03:00 and 3 or 6
    digit fractions. A timestamp without any offset is assumed to be UTC.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------

# A link either has a scheme (https://...), starts with www., or looks like
# "domain.tld/path". Requiring a "/" for the bare-domain case avoids matching
# things like "BigEarthData.ai" that are just names in a sentence.
LINK_RE = re.compile(
    r"(https?://[^\s]+"
    r"|\bwww\.[^\s]+"
    r"|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+/[^\s]*)",
    re.IGNORECASE,
)
SHORTENERS = {"bit.ly", "share.google", "t.co", "tinyurl.com", "ow.ly",
              "buff.ly", "share.newsbreak.com", "mailchi.mp", "youtu.be"}


def extract_links(text: str) -> list[Link]:
    links = []
    for m in LINK_RE.finditer(text or ""):
        raw = m.group(0)
        # Check for Bluesky's "..." BEFORE trimming sentence punctuation.
        truncated = raw.endswith("...") or raw.endswith("…")
        display = raw if truncated else raw.rstrip(").,;!?\"'")
        clean = display.rstrip(".…")
        url = clean if clean.lower().startswith("http") else "https://" + clean
        domain = urlparse(url).netloc.lower()
        if not domain or "." not in domain:
            continue
        links.append(Link(
            display=display,
            # A truncated link is a guess at best, so we do not pretend it is
            # fetchable. The full URL needs the post record (facets).
            url=None if truncated else url,
            domain=domain,
            truncated=truncated,
            shortener=domain.removeprefix("www.") in SHORTENERS,
        ))
    return links


def text_without_links(text: str) -> str:
    return LINK_RE.sub(" ", text or "")


def is_link_only(text: str) -> bool:
    """True when, after removing links, hashtags and symbols, fewer than three
    real words remain (e.g. "www.nytimes.com/2026/11/02/u...")."""
    rest = re.sub(r"#\w+", " ", text_without_links(text))
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", rest)
    return len(words) < 3


# --------------------------------------------------------------------------
# Language (tiny, dependency-free guess)
# --------------------------------------------------------------------------

STOPWORDS = {
    "en": set("the and of to in is for that with on are was this it be as at by "
              "from have not but you my so we they after more about".split()),
    "pt": set("de da do que em para com os as uma um não no na dos das se são "
              "por mais contra após como".split()),
    "es": set("el la los las que de en para con por una un es del se lo pero "
              "me hoy queda".split()),
    "fr": set("le la les des de du et en pour une un est dans sur au aux".split()),
}


def guess_language(text: str) -> str:
    words = re.findall(r"[a-zà-ÿ]+", text_without_links(text).lower())
    if len(words) < 3:
        return "unknown"
    votes = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
    best = max(votes, key=votes.get)
    if votes[best] == 0:
        return "unknown"
    # Ties between English and another language go to English.
    if votes["en"] == votes[best]:
        return "en"
    return best


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _to_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def load_csv(path: str) -> tuple[list[Post], list[dict]]:
    """Read the scraper CSV. Returns (posts, dropped) where `dropped` lists
    every row we removed and why, for the audit log."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows_to_posts(rows)


def rows_to_posts(rows: Iterable[dict]) -> tuple[list[Post], list[dict]]:
    posts: list[Post] = []
    dropped: list[dict] = []
    seen_urls: set[str] = set()

    for i, row in enumerate(rows, start=2):        # row 1 is the header
        url = (row.get("url") or "").strip()
        text = (row.get("text") or "").strip()
        author = (row.get("author") or "").strip()

        if url in seen_urls:
            dropped.append({"row": i, "url": url, "author": author,
                            "reason": "exact duplicate row (same post URL)"})
            continue
        seen_urls.add(url)

        if not text:
            dropped.append({"row": i, "url": url, "author": author,
                            "reason": "empty text (image/video-only or deleted post)"})
            continue

        post = Post(
            post_id=url.rsplit("/", 1)[-1],
            url=url,
            author=author,
            text=text,
            created_at=parse_timestamp(row.get("created_at", "")),
            created_at_raw=row.get("created_at", ""),
            likes=_to_int(row.get("likes")),
            reposts=_to_int(row.get("reposts")),
            replies=_to_int(row.get("replies")),
            links=extract_links(text),
            language=guess_language(text),
            # Optional column; absent in today's scraper output.
            account_created_at=parse_timestamp(row.get("account_created_at", "")),
        )
        if post.created_at is None:
            post.notes.append(f"unparseable timestamp '{post.created_at_raw}'")
        if is_link_only(text):
            post.notes.append("link-only post (no claim text of its own)")
        if any(l.truncated for l in post.links):
            post.notes.append("contains a truncated link (full URL not in CSV)")
        posts.append(post)

    return posts, dropped
