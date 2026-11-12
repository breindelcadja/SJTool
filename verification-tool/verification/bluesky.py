"""Step 1: take posts from the Bluesky scraper and prepare them for verification.

Scraper format (from a real bluesky_results.csv): created_at, author, text, likes,
reposts, replies, url. The scraper does NOT provide account creation date or posts
per hour, so those checks are marked "unknown" and never penalized.
Field names live in FIELDS below, so changing one line adapts to a different scraper.

Engagement makes a post a "social lead": something to look into and a person to
contact. It is NEVER evidence and never raises a pitch's evidence pillar.
"""

import csv
import math
import re
from datetime import date, datetime

from .config import ENGAGEMENT_FIXED_MIN, ENGAGEMENT_FLOOR, ENGAGEMENT_TOP_SHARE
from .context import new_coverage

FIELDS = {
    "text": "text", "handle": "author", "url": "url", "uri": "uri",
    "likes": "likes", "reposts": "reposts", "replies": "replies",
    "posted_at": "created_at", "account_created_at": "account_created_at",
    "posts_last_hour": "posts_last_hour",
}


def _get(raw, name):
    return raw.get(FIELDS[name])


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def account_age_days(raw, today=None):
    created = _parse_date(_get(raw, "account_created_at"))
    if created is None:
        return None
    today = _parse_date(today) if isinstance(today, str) else (today or date.today())
    return max((today - created).days, 0)


def post_url(raw):
    """Web link to the post. Converts an at:// address if that is all we have."""
    url = _get(raw, "url")
    if url:
        return url
    uri = _get(raw, "uri") or ""
    if uri.startswith("at://") and "/app.bsky.feed.post/" in uri:
        who, rkey = uri[len("at://"):].split("/app.bsky.feed.post/", 1)
        return "https://bsky.app/profile/%s/post/%s" % (who, rkey)
    return None


def profile_url(handle):
    return "https://bsky.app/profile/%s" % handle if handle else None


def engagement(raw):
    likes = int(_get(raw, "likes") or 0)
    reposts = int(_get(raw, "reposts") or 0)
    replies = int(_get(raw, "replies") or 0)
    return {"likes": likes, "reposts": reposts, "replies": replies,
            "total": likes + reposts + replies}


def high_engagement_cutoff(totals, top_share=ENGAGEMENT_TOP_SHARE,
                           floor=ENGAGEMENT_FLOOR, fixed_min=ENGAGEMENT_FIXED_MIN):
    """Returns a function total -> bool."""
    ordered = sorted(totals, reverse=True)
    if ordered:
        k = max(1, math.ceil(top_share * len(ordered)))
        rank_cut = ordered[k - 1]
    else:
        rank_cut = float("inf")

    def qualifies(total):
        in_top = total >= rank_cut and total >= floor
        passes_fixed = fixed_min is not None and total >= fixed_min
        return in_top or passes_fixed
    return qualifies


def prepare_posts(raw_posts, today=None):
    """Raw scraper posts -> posts in the format the verification pipeline reads."""
    engagements = [engagement(r) for r in raw_posts]
    qualifies = high_engagement_cutoff([e["total"] for e in engagements])
    posts = []
    for raw, eng in zip(raw_posts, engagements):
        handle = _get(raw, "handle")
        posts.append({
            "platform": "bluesky",
            "url": post_url(raw),
            "text": _get(raw, "text") or "",
            "author_id": handle,
            "account_age_days": account_age_days(raw, today),
            "posts_last_hour": _get(raw, "posts_last_hour"),
            "posted_at": _get(raw, "posted_at"),
            "engagement": eng,
            "high_engagement": qualifies(eng["total"]),
            "profile_url": profile_url(handle),
        })
    return posts


def social_lead(posts):
    """The most-engaged high-engagement post as a lead, with how to reach the poster.

    Returns None if no post qualifies. A lead is something to look into, not evidence.
    """
    candidates = [p for p in posts if p.get("high_engagement")]
    if not candidates:
        return None
    best = max(candidates, key=lambda p: p["engagement"]["total"])
    return {
        "post_url": best["url"],
        "text": best["text"],
        "posted_at": best["posted_at"],
        "engagement": best["engagement"],
        "contact": {"handle": best["author_id"], "profile_url": best["profile_url"]},
        "note": "Social lead: a post to look into and a person to contact. "
                "It is not evidence.",
    }


def build_pitch(pitch_id, context, solution_summary, raw_posts, today=None):
    """One pitch in the format verify_pitch/score_claims reads.

    context   from make_context(problem, location, start, end)
    Includes the social lead and a coverage record with search links.
    """
    posts = prepare_posts(raw_posts, today)
    return {"pitch_id": pitch_id, "problem": context["problem"], "context": context,
            "solution_summary": solution_summary, "posts": posts,
            "social_lead": social_lead(posts),
            "coverage": new_coverage(context, solution_summary)}


def load_posts(path):
    """Read the scraper's output: a .csv (as the real scraper writes) or a .json list."""
    if path.lower().endswith(".json"):
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_link_only(text):
    """True if the post is just a link or hashtags, so it carries no claim of its own."""
    words = re.sub(r"#\w+", "", text or "").split()
    return bool(words) and all(re.match(r"^(https?://|www\.|[\w.-]+\.[a-z]{2,}/)", w, re.I)
                               or w.endswith("...") for w in words)


def clean_raw_posts(raw_posts):
    """Drop exact duplicate rows (same post link) and empty posts.

    Returns (kept, report). The real scraper output repeats some posts.
    """
    seen, kept = set(), []
    report = {"rows": len(raw_posts), "duplicates_removed": 0, "empty_removed": 0}
    for raw in raw_posts:
        text = (_get(raw, "text") or "").strip()
        key = post_url(raw) or text
        if not text:
            report["empty_removed"] += 1
        elif key in seen:
            report["duplicates_removed"] += 1
        else:
            seen.add(key)
            kept.append(raw)
    report["kept"] = len(kept)
    report["link_only"] = sum(1 for r in kept if is_link_only(_get(r, "text")))
    return kept, report
