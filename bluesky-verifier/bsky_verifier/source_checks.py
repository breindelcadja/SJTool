"""
Source checks: is there a problem with the Bluesky ACCOUNT or its BEHAVIOUR?

Required rules (from the brief):
  1. Flag accounts under 30 days old.
  2. Flag accounts posting more than 10 posts per hour.
  3. Flag the same text posted by 3 or more accounts.
  4. If account details are hidden or unavailable, note it but NEVER penalize.

Extra context (never penalizes, shown as "info"):
  * custom-domain handle vs default *.bsky.social
  * bridged account (*.brid.gy) - the person lives on another network
  * aggregator / automated feed (handle words like "feed", "bot", "news")

All thresholds come from config.yaml ("source_checks").
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .context import RunContext
from .ingest import text_without_links
from .models import CheckResult, Post, Solution


def normalize_for_duplicates(text: str) -> str:
    """Lower-case, drop links, mentions, hashtags, 'Source: X' and punctuation
    so share-button copies of the same headline compare equal."""
    t = text_without_links(text).lower()
    t = re.sub(r"@[\w.-]+", " ", t)
    t = re.sub(r"#\w+", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)                  # "[REUTERS]"
    t = re.sub(r"\bsource:\s*\w+", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def shingles(text: str, n: int = 3) -> set:
    words = text.split()
    return {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of 3-word shingles (0 = different, 1 = identical)."""
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def accounts_with_same_text(post: Post, all_posts: list[Post], threshold: float,
                            min_words: int = 6) -> list[str]:
    """Distinct accounts (including this one) whose text is (near-)identical."""
    base = normalize_for_duplicates(post.text)
    if len(base.split()) < min_words:          # too short to call it copying
        return []
    authors = set()
    for other in all_posts:
        o = normalize_for_duplicates(other.text)
        same = (o == base
                or base in o                                   # copy with extra words
                or (o in base and len(o.split()) >= min_words)  # shorter copy
                or similarity(base, o) >= threshold)           # near-duplicate
        if same:
            authors.add(other.author)
    return sorted(authors)


def max_posts_in_window(author: str, all_posts: list[Post], window: timedelta) -> int:
    times = sorted(p.created_at for p in all_posts
                   if p.author == author and p.created_at is not None)
    best, start = 0, 0
    for end in range(len(times)):
        while times[end] - times[start] > window:
            start += 1
        best = max(best, end - start + 1)
    return best


class SourceChecks:
    name = "source"

    def run(self, solution: Solution, ctx: RunContext) -> list[CheckResult]:
        cfg = ctx.config["source_checks"]
        out: list[CheckResult] = []
        # Rules are applied to every post in the solution's cluster; a flag on
        # one account is reported with that account's handle.
        for post in solution.posts:
            out += self._account_age(post, cfg)
            out += self._posting_rate(post, ctx, cfg)
            out += self._account_type(post, cfg)
        out += self._same_text(solution, ctx, cfg)
        return out

    # 1. account age -------------------------------------------------------
    def _account_age(self, post: Post, cfg) -> list[CheckResult]:
        if post.account_created_at is None:
            return [CheckResult(
                "source.account_age", None,
                f"Account age for @{post.author} is not available (the scraper "
                f"CSV has no profile data; profile lookups are off). Not penalized.",
                1.0, post.profile_url, outcome="unknown")]
        ref = post.created_at or datetime.now(timezone.utc)
        age_days = (ref - post.account_created_at).days
        if age_days < cfg["min_account_age_days"]:
            return [CheckResult(
                "source.account_age", 0.0,
                f"@{post.author} was {age_days} days old when it posted "
                f"(under {cfg['min_account_age_days']} days).",
                0.9, post.profile_url, outcome="flag",
                details={"age_days": age_days, "author": post.author})]
        return [CheckResult("source.account_age", 1.0,
                            f"@{post.author} is {age_days} days old.", 0.9,
                            post.profile_url, outcome="info")]

    # 2. posting rate ------------------------------------------------------
    def _posting_rate(self, post: Post, ctx: RunContext, cfg) -> list[CheckResult]:
        n = max_posts_in_window(post.author, ctx.all_posts, timedelta(hours=1))
        limit = cfg["max_posts_per_hour"]
        if n > limit:
            return [CheckResult(
                "source.posting_rate", 0.0,
                f"@{post.author} posted {n} times within one hour in this "
                f"dataset (limit {limit}).", 0.8, post.profile_url, outcome="flag",
                details={"max_posts_per_hour": n, "author": post.author})]
        return [CheckResult(
            "source.posting_rate", 1.0,
            f"@{post.author}: at most {n} post(s) per hour in this dataset. "
            f"(Lower bound: we only see posts matching the search.)",
            0.5, post.profile_url, outcome="info")]

    # 3. same text from many accounts -------------------------------------
    def _same_text(self, solution: Solution, ctx: RunContext, cfg) -> list[CheckResult]:
        out = []
        for post in solution.posts:
            authors = accounts_with_same_text(post, ctx.all_posts,
                                              cfg["near_duplicate_similarity"])
            if len(authors) >= cfg["same_text_min_accounts"]:
                out.append(CheckResult(
                    "source.same_text_many_accounts", 0.0,
                    f"The same text was posted by {len(authors)} accounts: "
                    + ", ".join("@" + a for a in authors[:6])
                    + (". This is often share-button copying of one headline, "
                       "not independent confirmation."),
                    0.8, post.url, outcome="flag",
                    details={"accounts": authors}))
                break                          # one flag per solution is enough
        return out

    # extra context --------------------------------------------------------
    def _account_type(self, post: Post, cfg) -> list[CheckResult]:
        h = post.author.lower()
        out = []
        if h.endswith(".brid.gy"):
            out.append(CheckResult(
                "source.bridged_account", None,
                f"@{post.author} is bridged from another network (Bridgy Fed). "
                f"Replies may not reach the person; contact them on their home server.",
                0.9, post.profile_url, outcome="info"))
        elif not h.endswith(".bsky.social"):
            out.append(CheckResult(
                "source.custom_domain_handle", None,
                f"@{post.author} uses a custom-domain handle, so whoever runs the "
                f"account controls that domain. Weak positive signal only.",
                0.6, post.profile_url, outcome="info"))
        words = cfg.get("aggregator_handle_words", [])
        known = cfg.get("known_aggregators", [])
        first_label = h.split(".")[0]
        if h in known or any(w in first_label for w in words):
            out.append(CheckResult(
                "source.aggregator", None,
                f"@{post.author} looks like an automated feed or news aggregator. "
                f"Treat it as a pointer to the original source, not an independent voice.",
                0.6, post.profile_url, outcome="info"))
        return out
