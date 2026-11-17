"""Plain-code source checks: flags and independent-poster counts. No model involved."""

import re
from collections import defaultdict

from .config import (BURST_POSTS_PER_HOUR, DUPLICATE_ACCOUNTS, NEW_ACCOUNT_DAYS)

PENALIZED_FLAGS = {"new_account", "burst_posting", "duplicate_text"}


def normalize(text):
    """Lowercase, drop punctuation, collapse spaces so near-identical posts match."""
    return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower()).split())


def source_checks(posts):
    """Returns (summary, per_post_flags).

    - unknown_signals is recorded when a platform hides account age or posting
      rate. It is never penalized.
    - independent_posters counts distinct post texts after normalizing, so a
      copy-paste campaign counts once however many accounts repeat it.
    """
    authors_by_text = defaultdict(set)
    for p in posts:
        authors_by_text[normalize(p["text"])].add(p["author_id"])

    per_post_flags = []
    for p in posts:
        flags = []
        age = p.get("account_age_days")
        rate = p.get("posts_last_hour")
        if age is None or rate is None:
            flags.append("unknown_signals")
        if age is not None and age < NEW_ACCOUNT_DAYS:
            flags.append("new_account")
        if rate is not None and rate > BURST_POSTS_PER_HOUR:
            flags.append("burst_posting")
        if len(authors_by_text[normalize(p["text"])]) >= DUPLICATE_ACCOUNTS:
            flags.append("duplicate_text")
        per_post_flags.append(flags)

    flagged = sum(1 for f in per_post_flags if PENALIZED_FLAGS & set(f))
    summary = {
        "independent_posters": len(authors_by_text),
        "platforms": len({p["platform"] for p in posts}),
        "flagged_posts": flagged,
        "flag_ratio": round(flagged / len(posts), 2) if posts else 0.0,
        "flags": sorted({flag for f in per_post_flags for flag in f}),
    }
    return summary, per_post_flags
