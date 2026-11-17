"""
Citation checks: do the links in the post actually back it up?

The rule from the brief: a source counts ONLY IF
    (a) the page loads, AND
    (b) the claimed or quoted text is on it.
We always record why a source failed: timeout, paywall, 404, text not found,
truncated link, network blocked, usage cap reached, ...

Labels:
  * "primary"  = domain ends in .gov or .edu (plus country forms like .gov.sg
                 if enabled in config).
  * anything else = "secondary".

How "the claimed text is on the page" is decided (in order):
  1. If the post quotes text ("..." or “...”, 6+ words), that quote must
     appear on the page (after normalizing spaces and punctuation).
  2. Otherwise we take the key claim chosen by the claim spotter and require
     EVERY number in it to appear on the page, and at least
     `min_keyword_overlap` of its content words.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .context import RunContext
from .fetcher import CapReached
from .ingest import text_without_links
from .models import CheckResult, Link, Solution

STOP = set("""a an the and or of to in on for with at by from is are was were be been
this that these those it its as into about over after before than then there their
they them we you your our my me i he she his her not no but so if just very can could
would should will has have had do does did via new more most""".split())


def is_primary(domain: str, cfg) -> bool:
    d = domain.lower().split(":")[0]
    if d.endswith(".gov") or d.endswith(".edu"):
        return True
    if cfg.get("count_country_gov_edu_as_primary", True):
        return bool(re.search(r"\.(gov|edu)\.[a-z]{2}$", d))
    return False


def registrable_domain(domain: str) -> str:
    """'www.cidrap.umn.edu' -> 'umn.edu'; 'www.nea.gov.sg' -> 'nea.gov.sg'.
    Used to count INDEPENDENT sources (two pages on one site count once)."""
    parts = domain.lower().split(":")[0].split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"gov", "edu", "co", "com", "org", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[^a-z0-9%' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quoted_spans(text: str) -> list[str]:
    spans = re.findall(r"[“\"]([^”\"]{12,})[”\"]", text)
    # Only treat it as a quotation if it is 6+ words. Shorter quoted phrases
    # are usually titles or labels ("Project Wolbachia"), not quoted evidence.
    return [s.strip() for s in spans if len(s.split()) >= 6]


def numbers_in(text: str) -> list[str]:
    # "5,889" -> "5889" so it matches either way on the page.
    found = [n.replace(",", "") for n in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text)]
    return list(dict.fromkeys(found))          # de-duplicated, order kept


def content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z'-]{2,}", _norm(text_without_links(text)))
    return [w for w in words if w not in STOP]


def text_on_page(post_text: str, key_claim: str, page_text: str, cfg) -> tuple[bool, str, dict]:
    """Return (found, explanation, details)."""
    page = _norm(page_text)
    page_nums = set(numbers_in(page_text.replace(",", "")))

    quotes = quoted_spans(post_text)
    if quotes:
        missing = [q for q in quotes if _norm(q) not in page]
        if not missing:
            return True, "the quoted text appears on the page", {"method": "quote"}
        return False, f"quoted text not found on page: “{missing[0][:80]}”", {"method": "quote"}

    claim = key_claim or post_text
    nums = numbers_in(claim)
    missing_nums = [n for n in nums if n not in page_nums]
    words = list(dict.fromkeys(content_words(claim)))
    hits = [w for w in words if w in page]
    overlap = len(hits) / len(words) if words else 0.0
    details = {"method": "key_terms", "numbers": nums, "missing_numbers": missing_nums,
               "keyword_overlap": round(overlap, 2)}
    if not words and not nums:
        return False, "post has no checkable claim text to look for", details
    if missing_nums:
        return False, (f"number(s) {', '.join(missing_nums)} from the post are not on the page "
                       f"(keyword overlap {overlap:.0%})"), details
    if overlap < cfg["min_keyword_overlap"]:
        return False, (f"claim text not found: only {overlap:.0%} of the claim's key words "
                       f"are on the page (need {cfg['min_keyword_overlap']:.0%})"), details
    return True, (f"claim found on page: all numbers present, {overlap:.0%} of key words"), details


class CitationChecks:
    name = "citation"

    def run(self, solution: Solution, ctx: RunContext) -> list[CheckResult]:
        cfg = ctx.config["citation_checks"]
        results: list[CheckResult] = []
        seen: set[str] = set()
        for post in solution.posts:
            for link in post.links:
                key = link.url or link.display
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._check_link(link, post, solution, ctx, cfg))
        if not results:
            results.append(CheckResult(
                "citation.none", None,
                "No links in the post(s), so there is no cited source to check.",
                1.0, None, outcome="unknown"))
        return results

    def _check_link(self, link: Link, post, solution: Solution, ctx: RunContext, cfg) -> CheckResult:
        label = "primary" if is_primary(link.domain, cfg) else "secondary"
        base = {"label": label, "domain": link.domain, "display": link.display,
                "registrable_domain": registrable_domain(link.domain),
                "post_url": post.url}

        if link.url is None:
            why = ("link is truncated in the post text and the full URL is not in the CSV; "
                   "resolving it needs a Bluesky post lookup, which is off (no APIs in Phase 1)")
            return CheckResult("citation.page_loads_and_text_found", 0.0, why, 0.9,
                               None, outcome="fail",
                               details={**base, "failure": "truncated link (unresolvable)"})

        if ctx.fetcher is None or not ctx.config["fetching"]["enabled"]:
            return CheckResult("citation.page_loads_and_text_found", None,
                               "page fetching is turned off in config", 0.0,
                               link.url, outcome="skipped",
                               details={**base, "failure": "fetching disabled"})
        try:
            page = ctx.fetcher.get(link.url)
        except CapReached as e:
            return CheckResult("citation.page_loads_and_text_found", None,
                               f"not checked: {e}", 0.0, link.url, outcome="skipped",
                               details={**base, "failure": str(e)})

        if not page.ok:
            return CheckResult("citation.page_loads_and_text_found", 0.0,
                               f"source failed: {page.failure}", 0.8, link.url,
                               outcome="fail", details={**base, "failure": page.failure,
                                                        "http_status": page.status})

        found, why, d = text_on_page(post.text, solution.key_claim, page.text, cfg)
        if page.paywalled and not found:
            return CheckResult("citation.page_loads_and_text_found", 0.0,
                               f"source failed: paywall (only a preview loaded; {why})",
                               0.7, page.final_url or link.url, outcome="fail",
                               details={**base, **d, "failure": "paywall"})
        if not found:
            return CheckResult("citation.page_loads_and_text_found", 0.2,
                               f"page loaded but {why}", 0.7, page.final_url or link.url,
                               outcome="fail", details={**base, **d, "failure": "text not found"})
        return CheckResult("citation.page_loads_and_text_found",
                           1.0 if label == "primary" else 0.7,
                           f"verified: page loaded and {why}", 0.85,
                           page.final_url or link.url, outcome="pass",
                           details={**base, **d})
