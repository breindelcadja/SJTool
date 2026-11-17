"""
Contradiction checks: does a credible source say the claim is wrong?

Phase 1 has no fact-check APIs, so contradictions come from two places:

1. A curated reference list (data/known_contradictions.yaml). Each entry has
   trigger patterns, the credible source URL and the exact sentence on that
   source that contradicts the claim, plus who reviewed it and when.
   At run time we re-fetch the source and confirm the sentence is still there.
     - live re-check passes            -> "credible" contradiction (Myth)
     - live re-check can't run/fails   -> still "credible" IF a human reviewed
       the entry, but confidence drops and the audit log says so.
   Phase 2 can replace this file with the Google Fact Check Tools API.

2. A "partial" contradiction: a page the post itself cites loads, the claim's
   words are there, but a number in the post is not. That caps the tier
   (the number may be wrong or out of date) but does not make it a Myth.
"""

from __future__ import annotations

import re

import yaml

from .context import RunContext
from .fetcher import CapReached
from .models import CheckResult, Solution


def load_contradictions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _matches(entry: dict, text: str) -> bool:
    return all(re.search(p, text, re.I) for p in entry["all_patterns"])


class ContradictionChecks:
    name = "contradiction"

    def __init__(self, entries: list[dict]):
        self.entries = entries

    def run(self, solution: Solution, ctx: RunContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        text = " ".join(p.text for p in solution.posts)
        for e in self.entries:
            if _matches(e, text):
                out.append(self._curated(e, ctx))
        out += self._numeric_mismatch(solution)
        return out

    def _curated(self, e: dict, ctx: RunContext) -> CheckResult:
        live = "not attempted (fetching off)"
        confidence = 0.6 if e.get("reviewed_by") else 0.3
        if ctx.fetcher is not None and ctx.config["fetching"]["enabled"]:
            try:
                page = ctx.fetcher.get(e["source_url"])
                if page.ok and _norm(e["source_quote"]) in _norm(page.text):
                    live, confidence = "confirmed live: quote found on source", 0.9
                elif page.ok:
                    live, confidence = "page loaded but quote not found - needs human review", 0.3
                else:
                    live = f"live re-check failed ({page.failure})"
            except CapReached as err:
                live = f"live re-check skipped ({err})"
        strength = "credible" if confidence >= 0.6 else "unverified"
        return CheckResult(
            "contradiction.curated_reference", 0.0,
            f"{e['publisher']} says: “{e['source_quote']}” "
            f"(contradicts: {e['claim']}). Live check: {live}. "
            f"Reference reviewed by {e.get('reviewed_by', 'nobody')} on {e.get('reviewed_on', '?')}.",
            confidence, e["source_url"],
            outcome="contradiction" if strength == "credible" else "info",
            details={"strength": strength, "entry_id": e["id"], "live_check": live,
                     "primary": e.get("primary", False),
                     "supporting_sources": e.get("supporting_sources", [])})

    def _numeric_mismatch(self, solution: Solution) -> list[CheckResult]:
        # Re-uses citation results already on the solution (run citation first).
        out = []
        for c in solution.checks:
            d = c.details or {}
            if (c.check_name == "citation.page_loads_and_text_found"
                    and d.get("method") == "key_terms" and d.get("missing_numbers")
                    and d.get("keyword_overlap", 0) >= 0.6):
                out.append(CheckResult(
                    "contradiction.number_mismatch", 0.3,
                    f"The post's own source covers the same story but does not contain "
                    f"the number(s) {', '.join(d['missing_numbers'])}. The figure may be "
                    f"wrong or out of date.", 0.5, c.source_url, outcome="contradiction",
                    details={"strength": "partial"}))
        return out


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s)).strip()
