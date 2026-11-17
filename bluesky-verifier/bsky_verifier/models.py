"""
Data shapes used across the verifier.

Read this file first: every other module passes these objects around.

  Post         one Bluesky post (one CSV row after cleaning)
  Link         one link found inside a post's text
  Solution     a group of posts that describe the same solution idea
  CheckResult  the output of ONE verification check (the pluggable contract)
  Tile         what a journalist finally sees
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


@dataclass
class Link:
    """A link as it appears in Bluesky post text.

    Bluesky shortens long links in the *display text* (e.g.
    "www.cidrap.umn.edu/dengue/60-ne..."). The real URL lives in the post's
    "facets", which the current scraper does not save. So a truncated link
    usually cannot be fetched from the CSV alone.
    """

    display: str                 # exactly what was in the text
    url: Optional[str]           # a fetchable URL, or None if we can't build one
    domain: str                  # e.g. "www.cidrap.umn.edu"
    truncated: bool              # True if the display text ends in "..." / "…"
    shortener: bool = False      # bit.ly, share.google, etc. (fetch follows redirects)


@dataclass
class Post:
    post_id: str                 # the record key at the end of the post URL
    url: str
    author: str                  # Bluesky handle, e.g. "crof.bsky.social"
    text: str
    created_at: Optional[datetime]   # normalized to UTC (None if unparseable)
    created_at_raw: str
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    links: list[Link] = field(default_factory=list)
    language: str = "en"         # best guess: "en", "pt", "es", "fr", "unknown"
    # Optional account details. The CSV does not have these; they stay None
    # unless a profile file is supplied. Missing details are NEVER penalized.
    account_created_at: Optional[datetime] = None
    # Free-form notes added during cleaning (e.g. "link-only post").
    notes: list[str] = field(default_factory=list)

    @property
    def engagement(self) -> int:
        """Weighted engagement. Reposts count double because they spread the
        post to new audiences (an assumption; see config.yaml)."""
        return self.likes + 2 * self.reposts + self.replies

    @property
    def profile_url(self) -> str:
        return f"https://bsky.app/profile/{self.author}"


@dataclass
class CheckResult:
    """The pluggable verifier contract.

    Every check (local today, an API tomorrow) returns a list of these.
    The five fields the brief requires are first; the rest help the tier
    engine and the audit log.
    """

    check_name: str              # e.g. "citation.page_loads_and_text_found"
    score: Optional[float]       # 0..1 strength of this signal (None = n/a)
    evidence: str                # plain-language explanation of what we saw
    confidence: float            # 0..1 how much we trust this result
    source_url: Optional[str]    # the page or post the evidence came from
    # outcome is one of:
    #   "pass"     evidence supports the solution
    #   "fail"     we tried and it did not support it (with a reason)
    #   "flag"     a problem with the account or behaviour
    #   "contradiction" a source contradicts the claim
    #   "info"     neutral context (e.g. "bridged account")
    #   "unknown"  data not available; never penalized
    #   "skipped"  not run (e.g. usage cap reached)
    outcome: str = "info"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Solution:
    """One solution idea, backed by one or more posts."""

    solution_id: str
    summary: str
    posts: list[Post]
    lead_post: Post              # the post we show on the tile
    key_claim: str = ""          # most check-worthy sentence (from claim spotter)
    claim_score: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)
    tier: str = ""
    tier_reason: str = ""

    @property
    def authors(self) -> list[str]:
        return sorted({p.author for p in self.posts})


TIERS_SHOWN = ("Strong lead", "Promising", "Early signal")   # become tiles
TIERS_HIDDEN = ("Myth", "Flagged")                            # audit log only
TIER_RANK = {"Strong lead": 0, "Promising": 1, "Early signal": 2,
             "Flagged": 3, "Myth": 4}
