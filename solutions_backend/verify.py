"""Runs Inaaya's Bluesky verifier (bluesky-verifier/) on a run's solution posts.

The verifier groups posts about the same solution, checks the accounts and the
links they cite, looks for related research on OpenAlex (free) and gives each
solution a tier:

    Strong lead, Promising, Early signal   shown to reporters, with evidence
    Flagged, Myth                          kept out: they get no pitch

Its code is used as-is. This file only finds it, points its usage counter at
our data folder and saves its results per post."""
import logging
import os
import sys
from pathlib import Path

import config

log = logging.getLogger("verify")
HERE = Path(__file__).resolve().parent

# In the repo the verifier sits next to solutions_backend/; on the live server
# deploy/make_space.py copies it inside.
for folder in (HERE / "bluesky-verifier", HERE.parent / "bluesky-verifier"):
    if (folder / "bsky_verifier").is_dir():
        VERIFIER_DIR = folder
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        break
else:
    VERIFIER_DIR = None


def available():
    return VERIFIER_DIR is not None


def verify_posts(topic, solutions, all_posts):
    """Return one row per solution post: url, solution_id, tier, tier_reason, tile."""
    from bsky_verifier.pipeline import load_config, run_pipeline

    cfg = load_config(str(VERIFIER_DIR / "config.yaml"))
    # Count page fetches and API calls in our data folder (always writable).
    cfg["paths"]["usage_ledger_path"] = str(config.DATA_DIR / "verifier_usage.json")
    if os.environ.get("VERIFIER_OFFLINE"):     # tests: no page fetches or OpenAlex
        cfg["fetching"]["enabled"] = False
        cfg["evidence_search"]["enabled"] = False
    report = run_pipeline(solutions, topic, config=cfg, context_posts=all_posts,
                          max_tiles=len(solutions) or 1)

    by_url = {}
    # Every solution, including Flagged and Myth, is in the audit log.
    for entry in report["audit_log"]:
        for url in entry.get("post_urls") or [entry["lead_post"]]:
            by_url[url] = {"url": url, "solution_id": entry["solution_id"],
                           "tier": entry["tier"], "tier_reason": entry["reason"], "tile": None}
    # The tiles (Strong lead, Promising, Early signal) carry the evidence.
    for tile in report["tiles"]:
        for url in [tile["bluesky_post"]["url"], *tile.get("other_posts", [])]:
            row = by_url.setdefault(url, {"url": url, "solution_id": tile["solution_id"],
                                          "tier": tile["tier"], "tier_reason": tile["tier_reason"]})
            row["tile"] = tile
    # Posts the verifier set aside (non-English, empty or repeated) stay unverified.
    for skipped in report.get("held", []) + report.get("dropped_rows", []):
        url = skipped.get("url") if isinstance(skipped, dict) else None
        if url and url not in by_url:
            log.info("verifier set aside %s: %s", url, skipped.get("reason"))
    return list(by_url.values()), report["usage"]
