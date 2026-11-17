"""
Traction thresholds and the tier engine.

TIER RULES - evaluated top to bottom, the FIRST rule that matches wins.
Each rule is a plain function of the check results, so it is testable.

  R1 Myth          A credible contradiction exists (curated reference that a
                   human reviewed or we re-confirmed live).
  R2 Strong lead   Strong evidence: >= 1 verified PRIMARY source (.gov/.edu)
                   OR >= 2 verified sources on independent domains,
                   AND no partial contradiction.
                   Account flags are listed but never lower this tier.
                   Coverage-gap check: if no verified secondary (news) source
                   exists, the tile says "possible coverage gap" instead of
                   being downgraded.
  R3 Flagged       Any account/behaviour flag (new account, >10 posts/hour,
                   same text from >= 3 accounts) without strong evidence.
  R4 Early signal  A partial contradiction (e.g. number mismatch with the
                   post's own source). Contradictions CAP the tier here.
  R5 Promising     >= 1 verified source (any label), OR high traction from an
                   account with no flags (allowed as a pitch without data).
  R6 Early signal  Everything else: a plausible solution with no verified
                   evidence yet. The tile says "no source found".

Only Strong lead, Promising and Early signal become tiles.
"""

from __future__ import annotations

from .models import CheckResult, Post, Solution


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile, no numpy needed."""
    if not values:
        return 0.0
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round(pct / 100 * len(v) + 0.5)) - 1))
    return float(v[k])


def traction_thresholds(posts: list[Post], cfg: dict) -> dict:
    """Thresholds RELATIVE to this dataset, with an absolute floor so a quiet
    dataset does not crown a post with 2 likes as 'high traction'."""
    eng = [p.engagement for p in posts]
    high = max(percentile(eng, cfg["high_traction_percentile"]), cfg["high_traction_min_engagement"])
    return {"high": high,
            "percentile": cfg["high_traction_percentile"],
            "floor": cfg["high_traction_min_engagement"],
            "median": percentile(eng, 50),
            "n_posts": len(eng),
            "formula": "likes + 2*reposts + replies"}


def solution_engagement(solution: Solution) -> int:
    return max(p.engagement for p in solution.posts)


# ---------------------------------------------------------------------------
# Evidence summary helpers
# ---------------------------------------------------------------------------

def summarize(checks: list[CheckResult]) -> dict:
    verified = [c for c in checks
                if c.check_name == "citation.page_loads_and_text_found" and c.outcome == "pass"]
    primary = [c for c in verified if c.details.get("label") == "primary"]
    domains = {c.details.get("registrable_domain") for c in verified}
    flags = [c for c in checks if c.outcome == "flag"]
    credible = [c for c in checks if c.outcome == "contradiction"
                and c.details.get("strength") == "credible"]
    partial = [c for c in checks if c.outcome == "contradiction"
               and c.details.get("strength") == "partial"]
    return {"verified": verified, "primary": primary, "independent_domains": domains,
            "flags": flags, "credible_contradictions": credible,
            "partial_contradictions": partial}


def assign_tier(solution: Solution, traction: dict, cfg: dict) -> tuple[str, str, dict]:
    """Return (tier, plain-language reason, extras)."""
    s = summarize(solution.checks)
    eng = solution_engagement(solution)
    high = eng >= traction["high"]
    extras = {"engagement": eng, "high_traction": high, "coverage_gap": False,
              "rule": None}
    strong = (len(s["primary"]) >= cfg["strong_min_primary"]
              or len(s["independent_domains"]) >= cfg["strong_min_independent_sources"])
    flag_text = "; ".join(f.evidence for f in s["flags"])

    # R1 Myth
    if s["credible_contradictions"]:
        c = s["credible_contradictions"][0]
        extras["rule"] = "R1"
        return "Myth", f"A credible source contradicts this claim. {c.evidence}", extras

    # R2 Strong lead
    if strong and not s["partial_contradictions"]:
        extras["rule"] = "R2"
        why = (f"{len(s['verified'])} verified source(s)"
               f" ({len(s['primary'])} primary .gov/.edu) load and contain the claim.")
        if not any(c.details.get("label") == "secondary" for c in s["verified"]):
            extras["coverage_gap"] = True
            why += (" No news coverage was verified for this in the dataset: possible "
                    "coverage gap, which makes it more pitchable, not less.")
        if s["flags"]:
            why += f" Account flags noted but do not lower a well-sourced lead: {flag_text}"
        return "Strong lead", why, extras

    # R3 Flagged
    if s["flags"]:
        extras["rule"] = "R3"
        return "Flagged", f"Account or behaviour problems and no strong evidence: {flag_text}", extras

    # R4 partial contradiction caps the tier
    if s["partial_contradictions"]:
        extras["rule"] = "R4"
        return ("Early signal",
                "Capped at Early signal: " + s["partial_contradictions"][0].evidence, extras)

    # R5 Promising
    if s["verified"]:
        extras["rule"] = "R5a"
        return ("Promising",
                f"{len(s['verified'])} verified source supports it, but not enough for a "
                f"strong lead (needs a .gov/.edu source or two independent sources).", extras)
    if high:
        extras["rule"] = "R5b"
        return ("Promising",
                f"No backing data found yet, but the post has high traction "
                f"({eng} weighted engagement; top threshold for this dataset is "
                f"{traction['high']:.0f}) from an account with no flags. Verify before publishing.",
                extras)

    # R6 Early signal
    extras["rule"] = "R6"
    fails = [c for c in solution.checks
             if c.check_name.startswith("citation.") and c.outcome in ("fail", "skipped", "unknown")]
    why = "A plausible solution idea with no verified source yet."
    if fails:
        why += " Why sources did not count: " + "; ".join(
            f"{c.details.get('display') or c.details.get('domain') or 'post'} - {c.evidence}"
            if c.details else c.evidence for c in fails[:3])
    return "Early signal", why, extras
