"""
Related-research search (ported from SJTool's find_evidence.py).

SJTool's original `verify_solution` searched OpenAlex (a free scholarly index)
with the post text and called every hit "peer_reviewed" with relevance 0.80.

What changed when it joined the verifier, and why:
  * ON BY DEFAULT. OpenAlex is a free scholarly API (no key, no cost), so the
    team allowed it in Phase 1 even though paid/fact-check APIs stay off. Turn
    it off with `evidence_search.enabled: false` in config.yaml. It counts
    against the API-call caps (not the dollar estimate).
  * Searches with the post's KEY CLAIM, not the whole post. Hashtags,
    handles and promo text make poor search queries.
  * Results are shown as "related research", NOT as verified sources. An
    OpenAlex hit means "a paper on a similar topic exists", not "this paper
    supports this claim", and the 0.80 relevance in the original was a fixed
    number, not a measurement. Under the verifier's rule (page loads AND the
    claim is on it) a hit cannot count until its text is checked. So by
    default related research never changes a tier; it gives the reporter
    reading leads. Set `can_raise_tier` only after Sydney reviews the rule.
  * The request goes through an injectable backend so tests never touch
    the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import List

from .context import RunContext
from .evidence_scoring import calculate_score, determine_status
from .models import CheckResult, Solution

OPENALEX_URL = "https://api.openalex.org/works"


@dataclass
class EvidenceSource:
    title: str
    url: str
    source_type: str
    summary: str
    relevance: float


def clean_solution_text(text: str) -> str:
    """Remove links and extra spaces (as in SJTool), plus handles and hashtags."""
    text = re.sub(r"http\S+|www\.\S+", "", text)
    text = re.sub(r"[@#]\w[\w.-]*", "", text)
    return re.sub(r"\s+", " ", text).strip()


class OpenAlexBackend:
    """The real network call. Tests pass a fake with the same `search` method."""

    def __init__(self, timeout: float = 20, mailto: str | None = None):
        self.timeout = timeout
        self.mailto = mailto          # OpenAlex asks for a contact email; keep it generic/optional

    def search(self, search_text: str, max_results: int = 5) -> List[EvidenceSource]:
        import requests
        params = {"search": search_text, "per-page": max_results}
        if self.mailto:
            params["mailto"] = self.mailto
        r = requests.get(OPENALEX_URL, params=params, timeout=self.timeout)
        r.raise_for_status()
        out = []
        for work in r.json().get("results", []):
            out.append(EvidenceSource(
                title=work.get("title") or "(untitled)",
                url=work.get("id", ""),
                source_type="peer_reviewed",
                summary="Academic work located via OpenAlex (topic match only)",
                relevance=0.80,          # SJTool's fixed value; NOT a measured relevance
            ))
        return out


class AcademicEvidenceCheck:
    name = "evidence"

    def __init__(self, backend=None):
        self.backend = backend

    def run(self, solution: Solution, ctx: RunContext) -> list[CheckResult]:
        cfg = ctx.config.get("evidence_search", {})
        if not cfg.get("enabled", False):
            # Switched off in config: add one run-level note, not a result per solution.
            note = "related-research search is off (evidence_search.enabled is false)"
            if note not in ctx.notes:
                ctx.notes.append(note)
            return []

        caps = ctx.config["caps"]
        used = ctx.api_calls_this_run
        if used >= caps.get("max_api_calls_per_run", 0):
            note = "per-run API-call cap reached"
            if note not in ctx.notes:
                ctx.notes.append(note)
            return [CheckResult("evidence.related_research", None, f"not searched: {note}",
                                0.0, None, outcome="skipped", details={"reason": note})]
        if ctx.ledger is not None:
            daily, monthly = ctx.ledger.count("api_call")
            if daily >= caps.get("max_api_calls_per_day", 0) or monthly >= caps.get("max_api_calls_per_month", 0):
                return [CheckResult("evidence.related_research", None,
                                    "not searched: daily or monthly API-call cap reached", 0.0, None,
                                    outcome="skipped", details={"reason": "daily/monthly cap"})]

        query = clean_solution_text(solution.key_claim or solution.summary)
        backend = self.backend or OpenAlexBackend(cfg.get("timeout_seconds", 20))
        ctx.api_calls_this_run += 1
        if ctx.ledger is not None:
            ctx.ledger.add("api_call")
        try:
            sources = backend.search(query, cfg.get("max_results", 5))
        except Exception as e:                      # network, rate limit, bad JSON ...
            return [CheckResult("evidence.related_research", None,
                                f"search failed: {type(e).__name__}", 0.0, OPENALEX_URL,
                                outcome="unknown", details={"query": query, "error": str(e)[:120]})]

        score = calculate_score(sources)
        status = determine_status(score)
        found = "no" if not sources else str(len(sources))
        return [CheckResult(
            "evidence.related_research", score if sources else 0.0,
            f"Found {found} possibly related scholarly work(s) for “{query[:80]}”. "
            f"Topic match only: nobody has checked that they support the claim. "
            f"SJTool status: {status}.",
            0.3, OPENALEX_URL,
            outcome="info",           # never "pass": see module docstring
            details={"query": query, "status": status, "can_raise_tier": cfg.get("can_raise_tier", False),
                     "sources": [asdict(s) for s in sources]})]
