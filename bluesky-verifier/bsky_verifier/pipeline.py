"""
The pipeline, and the ONE function the backend calls:

    verify_solutions(posts, issue) -> list[tile dicts]

`posts` are Bluesky posts the classifier already marked as solutions (either
`Post` objects or CSV-style dicts with created_at, author, text, likes,
reposts, replies, url). For the full report (audit log, dropped posts, usage)
call `run_pipeline` instead.

Order of operations:
  1. clean        normalize timestamps, drop exact duplicates and empty posts
  2. gate         keep posts labelled as solutions (stand-in labels for the
                  demo; in production the classifier has already done this)
  3. language     hold non-English posts for a later phase (logged, not lost)
  4. cluster      group posts about the same solution
  5. claim        pick each solution's most check-worthy sentence
  6. checks       source -> citation -> contradiction (each a Verifier)
  7. tier         rules R1..R6 in tiers.py
  8. tiles        Strong lead / Promising / Early signal only, top N
                  Myth and Flagged go to the internal audit log.
"""

from __future__ import annotations

import copy
import csv
import os
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

import yaml

from .academic_evidence import AcademicEvidenceCheck
from .citation_checks import CitationChecks
from .claims import best_claim, get_spotter
from .context import RunContext
from .contradiction_checks import ContradictionChecks, load_contradictions
from .evidence_scoring import calculate_score, determine_status
from .evidence_sources import classify_domain
from .fetcher import CappedFetcher, RequestsBackend, UsageLedger
from .ingest import rows_to_posts, text_without_links
from .models import TIER_RANK, TIERS_SHOWN, Post, Solution
from .source_checks import SourceChecks, normalize_for_duplicates, similarity
from .tiers import assign_tier, summarize, traction_thresholds

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_config(path: Optional[str] = None) -> dict:
    path = path or os.path.join(ROOT, "config.yaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Relative paths in the config are relative to the project folder.
    for key in ("claimbuster_model_path", "known_contradictions_path", "usage_ledger_path"):
        p = cfg["paths"].get(key)
        if p and not os.path.isabs(p):
            cfg["paths"][key] = os.path.join(ROOT, p)
    return cfg


def load_labels(path: str) -> dict[str, dict]:
    """Stand-in for the classifier's output: url -> {is_solution, solution_key, summary, note}."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["url"].strip(): r for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def cluster_posts(posts: list[Post], labels: Optional[dict], threshold: float) -> dict[str, list[Post]]:
    clusters: dict[str, list[Post]] = {}
    for p in posts:
        key = (labels or {}).get(p.url, {}).get("solution_key") or ""
        if key:
            clusters.setdefault(key, []).append(p)
            continue
        # No label: join the first cluster with near-identical text.
        norm = normalize_for_duplicates(p.text)
        for k, members in clusters.items():
            if any(similarity(norm, normalize_for_duplicates(m.text)) >= threshold for m in members):
                members.append(p)
                break
        else:
            clusters[f"auto-{p.post_id}"] = [p]
    return clusters


def auto_summary(text: str, limit: int = 180) -> str:
    t = re.sub(r"\s+", " ", text_without_links(text)).strip()
    first = re.split(r"(?<=[.!?])\s", t)[0]
    return first if len(first) <= limit else first[: limit - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def how_to_reach(post: Post) -> str:
    if post.author.endswith(".brid.gy"):
        return (f"This account is bridged from the fediverse. Reply on Bluesky at "
                f"{post.profile_url} or find the original account on its home server.")
    return (f"Mention or message @{post.author} on Bluesky (DMs depend on their settings): "
            f"{post.profile_url}")


def make_tile(sol: Solution, extras: dict) -> dict:
    evidence = []
    for c in sol.checks:
        if c.check_name == "citation.page_loads_and_text_found":
            d = c.details
            evidence.append({
                "link": c.source_url or d.get("display"),
                "label": d.get("label"),
                "status": "verified" if c.outcome == "pass" else "did not count",
                "reason": c.evidence,
            })
        elif c.check_name == "contradiction.number_mismatch":
            evidence.append({"link": c.source_url, "label": "secondary",
                             "status": "contradiction (partial)", "reason": c.evidence})
    verified = [e for e in evidence if e["status"] == "verified"]
    # Informational SJTool-style score over VERIFIED sources only (relevance 1.0
    # because the claim text was found). It does not decide the tier.
    class _S:                                   # tiny stand-in with the fields scoring needs
        def __init__(self, t): self.source_type, self.relevance = t, 1.0
    vsrc = [_S(classify_domain(c.details.get("domain", "")))
            for c in sol.checks
            if c.check_name == "citation.page_loads_and_text_found" and c.outcome == "pass"]
    ev_score = calculate_score(vsrc)
    related = next((c for c in sol.checks if c.check_name == "evidence.related_research"
                    and c.details.get("sources")), None)
    lead = sol.lead_post
    return {
        "solution_id": sol.solution_id,
        "solution_summary": sol.summary,
        "tier": sol.tier,
        "tier_reason": sol.tier_reason,
        "key_claim": sol.key_claim,
        "claim_check_worthiness": sol.claim_score,
        "evidence": evidence,
        "evidence_note": None if verified else
            "No verified source found. Nothing here has been confirmed yet.",
        "coverage_gap": extras.get("coverage_gap", False),
        "evidence_score": {"score": ev_score, "status": determine_status(ev_score),
                           "basis": f"{len(vsrc)} verified source(s); informational, does not set the tier"},
        "related_research": None if not related else {
            "note": "Topic matches from a scholarly index. Not checked against this claim.",
            "works": [{"title": w["title"], "url": w["url"]} for w in related.details["sources"]]},
        "bluesky_post": {
            "url": lead.url,
            "text": lead.text,
            "created_at": lead.created_at.isoformat() if lead.created_at else lead.created_at_raw,
            "likes": lead.likes, "reposts": lead.reposts, "replies": lead.replies,
            "weighted_engagement": lead.engagement,
        },
        "author": {"handle": "@" + lead.author, "profile_url": lead.profile_url,
                   "how_to_reach": how_to_reach(lead)},
        "other_posts": [p.url for p in sol.posts if p is not lead],
        # Flags (shown only when a Strong lead outranks them) plus context a
        # reporter should know before reaching out.
        "notes": [c.evidence for c in sol.checks
                  if c.outcome == "flag"
                  or c.check_name in ("source.aggregator", "source.bridged_account",
                                      "claim.check_worthiness")],
        "rule": extras.get("rule"),
    }


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def run_pipeline(posts: Iterable, issue: str, config: Optional[dict] = None,
                 labels: Optional[dict] = None, context_posts: Optional[Iterable] = None,
                 fetch_backend=None, evidence_backend=None, max_tiles: Optional[int] = None,
                 profiles: Optional[dict] = None) -> dict:
    cfg = copy.deepcopy(config) if config else load_config()
    started = datetime.now(timezone.utc)

    # 1. clean -----------------------------------------------------------
    posts = list(posts)
    if posts and isinstance(posts[0], dict):
        posts, dropped = rows_to_posts(posts)
    else:
        dropped = []
    if context_posts is None:
        all_posts = posts
    else:
        ctx_list = list(context_posts)
        all_posts = rows_to_posts(ctx_list)[0] if ctx_list and isinstance(ctx_list[0], dict) else ctx_list
    if profiles:                                # optional account details
        for p in posts + all_posts:
            if p.author in profiles:
                p.account_created_at = profiles[p.author]

    # 2. gate --------------------------------------------------------------
    not_solutions, held, candidates = [], [], []
    for p in posts:
        lab = (labels or {}).get(p.url)
        if labels is not None and (not lab or lab.get("is_solution", "").lower() not in ("1", "true", "yes")):
            not_solutions.append({"url": p.url, "author": p.author, "text": p.text[:140],
                                  "reason": "not a solution (classifier stand-in label)"
                                            + (f": {lab['note']}" if lab and lab.get("note") else ""),
                                  "post_notes": p.notes})
            continue
        # 3. language ------------------------------------------------------
        if cfg["ingest"]["english_only"] and p.language not in ("en", "unknown"):
            held.append({"url": p.url, "author": p.author, "text": p.text[:140],
                         "reason": f"held: non-English ({p.language}); Phase 1 is English only",
                         "summary": (lab or {}).get("summary", "")})
            continue
        candidates.append(p)

    # 4. cluster ---------------------------------------------------------
    clusters = cluster_posts(candidates, labels, cfg["clustering"]["near_duplicate_similarity"])

    # shared context -------------------------------------------------------
    spotter, spotter_note = get_spotter(cfg["paths"]["claimbuster_model_path"],
                                        cfg["claim_spotting"]["prefer"])
    fetcher = None
    ledger = UsageLedger(cfg["paths"].get("usage_ledger_path"))
    if cfg["fetching"]["enabled"]:
        backend = fetch_backend or RequestsBackend(cfg["fetching"]["timeout_seconds"],
                                                   cfg["fetching"]["user_agent"])
        fetcher = CappedFetcher(backend, cfg["caps"], ledger, cfg["caps"]["cost_per_page_fetch_usd"])
    ctx = RunContext(issue=issue, config=cfg, all_posts=all_posts, fetcher=fetcher,
                     spotter=spotter, traction=traction_thresholds(all_posts, cfg["traction"]),
                     ledger=ledger)
    # The evidence check runs after citations so the tile can show both.
    verifiers_before = [SourceChecks(), CitationChecks(), AcademicEvidenceCheck(evidence_backend)]
    contradiction = ContradictionChecks(load_contradictions(cfg["paths"]["known_contradictions_path"]))

    # 5-7. per solution ----------------------------------------------------
    solutions: list[Solution] = []
    for key, members in clusters.items():
        lead = max(members, key=lambda p: (p.engagement, -(p.created_at or started).timestamp()))
        # Use the most claim-rich post's sentence as the key claim.
        claim, score = max((best_claim(p.text, spotter) for p in members), key=lambda x: x[1])
        lab = (labels or {}).get(lead.url) or next(
            ((labels or {}).get(m.url) for m in members if (labels or {}).get(m.url)), None)
        sol = Solution(solution_id=key,
                       summary=(lab or {}).get("summary") or auto_summary(lead.text),
                       posts=members, lead_post=lead, key_claim=claim, claim_score=score)
        for v in verifiers_before:
            sol.checks += v.run(sol, ctx)
        sol.checks += contradiction.run(sol, ctx)
        if score < cfg["claim_spotting"]["min_check_worthiness"]:
            sol.checks.append(_low_claim_note(score, spotter.name))
        sol.tier, sol.tier_reason, extras = assign_tier(sol, ctx.traction, cfg["tiers"])
        sol._extras = extras                    # used when building tiles
        solutions.append(sol)

    # 8. tiles -------------------------------------------------------------
    def sort_key(s):
        sm = summarize(s.checks)
        # Best tier first; then more verified sources; then high traction;
        # then the more checkable claim; then raw engagement (weak signal last).
        return (TIER_RANK[s.tier], -len(sm["verified"]), -int(s._extras["high_traction"]),
                -s.claim_score, -s._extras["engagement"])

    shown = sorted([s for s in solutions if s.tier in TIERS_SHOWN], key=sort_key)
    n = max_tiles or cfg["output"]["max_tiles"]
    tiles = [make_tile(s, s._extras) for s in shown[:n]]

    audit = [{
        "solution_id": s.solution_id, "summary": s.summary, "tier": s.tier,
        "reason": s.tier_reason, "rule": s._extras["rule"],
        "shown_to_journalists": s.tier in TIERS_SHOWN,
        "in_top_tiles": s in shown[:n],
        "lead_post": s.lead_post.url, "post_urls": [p.url for p in s.posts],
        "authors": s.authors,
        "key_claim": s.key_claim, "claim_score": s.claim_score,
        "checks": [c.to_dict() for c in s.checks],
    } for s in sorted(solutions, key=sort_key)]

    usage = {
        "page_fetches_this_run": fetcher.fetches_this_run if fetcher else 0,
        "api_calls_this_run": ctx.api_calls_this_run,
        "paid_api_calls_this_run": 0,
        "estimated_cost_usd": ((fetcher.estimated_cost_usd if fetcher else 0.0)
                               + round(ctx.api_calls_this_run * cfg["caps"]["cost_per_api_call_usd"], 4)),
        "caps": cfg["caps"],
        "cap_events": (fetcher.cap_events if fetcher else []) + [n for n in ctx.notes if "cap reached" in n],
        "stopped_early": bool((fetcher and fetcher.cap_events) or any("cap reached" in n for n in ctx.notes)),
    }
    return {
        "issue": issue,
        "run_started_utc": started.isoformat(),
        "claim_spotter": {"name": spotter.name, "note": spotter_note},
        "run_notes": ctx.notes,
        "traction_thresholds": ctx.traction,
        "counts": {"input_rows_after_dedupe": len(posts), "dropped_rows": len(dropped),
                   "not_solutions": len(not_solutions), "held_non_english": len(held),
                   "solutions": len(solutions), "tiles": len(tiles)},
        "tiles": tiles,
        "audit_log": audit,
        "dropped_rows": dropped,
        "not_solutions": not_solutions,
        "held": held,
        "usage": usage,
    }


def _low_claim_note(score, name):
    from .models import CheckResult
    return CheckResult("claim.check_worthiness", score,
                       f"Low check-worthiness ({score:.2f} by {name}): the post reads as "
                       f"opinion or anecdote, so there is little to verify.",
                       0.6, None, outcome="info")


def verify_solutions(posts, issue: str, config: Optional[dict] = None, **kwargs) -> list[dict]:
    """THE backend entry point. Returns the top 3-5 tiles (Strong lead,
    Promising, Early signal). Never returns Myth or Flagged items."""
    return run_pipeline(posts, issue, config=config, **kwargs)["tiles"]
