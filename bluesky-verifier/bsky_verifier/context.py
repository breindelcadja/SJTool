"""
RunContext: everything a check might need, passed to each Verifier.

The Verifier interface is deliberately tiny so an API-based check can replace
a local one later without touching the tier engine:

    class MyVerifier:
        name = "my_check"
        def run(self, solution: Solution, ctx: RunContext) -> list[CheckResult]:
            ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import CheckResult, Post, Solution


@dataclass
class RunContext:
    issue: str
    config: dict
    all_posts: list[Post]            # every cleaned post in the scrape (for duplicate/rate checks)
    fetcher: Any                     # CappedFetcher (or None when fetching is off)
    spotter: Any                     # ClaimSpotter
    traction: dict = field(default_factory=dict)   # thresholds computed from the data
    notes: list[str] = field(default_factory=list) # run-level notes for the report


class Verifier(Protocol):
    name: str

    def run(self, solution: Solution, ctx: RunContext) -> list[CheckResult]:
        ...
