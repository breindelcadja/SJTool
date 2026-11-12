#!/usr/bin/env python3
"""Run the verification pipeline on a file of pitches.

  python3 run.py --offline                          everything from sample files, no keys
  python3 run.py --claims-only                      REAL ClaimBuster scores, no Anthropic key
  python3 run.py --claims-only --fake-claim-scores  same, with fake scores (no keys at all)
  python3 run.py --claims-only --local-scorer local_claim_model.joblib   free local model
  python3 run.py --pitches my.json                  live: Claude + ClaimBuster (costs money)

--claims-only needs only CLAIMBUSTER_API_KEY (free). Live mode also needs ANTHROPIC_API_KEY.
"""

import argparse
import json
import os
import sys

from verification.citations import fetch_page_text
from verification.claims import ClaimBusterScorer, OfflineClaimScorer
from verification.local_scorer import LocalClaimScorer
from verification.config import (CLAIM_SCORE_CUTOFF, ESTIMATED_COST_PER_EVIDENCE_CALL,
                                 MAX_DAILY_EVIDENCE_CALLS, MAX_DAILY_SCORING_CALLS,
                                 MAX_DAILY_SPEND_USD, MAX_MONTHLY_SPEND_USD, USAGE_FILE, MAX_EVIDENCE_CALLS_PER_RUN,
                                 MAX_PITCHES_PER_RUN, MAX_SCORING_CALLS_PER_RUN)
from verification.limits import (Budget, LimitReached, LimitedFinder, LimitedScorer,
                                 UsageLedger)
from verification.evidence import ClaudeEvidenceFinder, SampleEvidenceFinder
from report import build_html
from verification.pipeline import score_claims, verify_pitch

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = {"met": "●", "partial": "◐", "not_found": "○"}
TIER_LABEL = {"strong_lead": "STRONG LEAD", "promising": "PROMISING",
              "early_signal": "EARLY SIGNAL", "flagged": "FLAGGED", "myth": "MYTH"}
RESULT_LABEL = {"supported": "Supported", "contradicted": "Contradicted",
                "unverified": "Not supported"}


def format_tile(r):
    """A plain-text version of what a journalist's tile would show."""
    if r.get("error"):
        return "[ERROR] %s: %s\n" % (r["pitch_id"], r["error"])
    lines = []
    if r["next_step"] == "ready_for_evidence_search":
        lines.append("[CHECK-WORTHY %.2f] %s  %s" % (r["claim"]["score"], r["pitch_id"],
                                                   r.get("solution_summary") or ""))
        lines.append("  Claim: %s" % r["claim"]["text"])
        s = r["source"]
        lines.append("  Posters: %d independent, %d platform(s). Flags: %s" % (
            s["independent_posters"], s["platforms"], ", ".join(s["flags"]) or "none"))
        lines.append("  Next: evidence search not run yet\n")
        return "\n".join(lines)
    if r["tier"] is None:
        lines.append("[DROPPED] %s  (no checkable claim, score %.2f)" %
                     (r["pitch_id"], r["claim"]["score"]))
        lines.append("  best sentence: %s\n" % (r["claim"]["text"] or "(none)"))
        return "\n".join(lines)

    lines.append("[%s] %s  %s" % (TIER_LABEL[r["tier"]], r["pitch_id"],
                                  r.get("solution_summary") or ""))
    lines.append("  Claim checked: %s" % r["claim"]["text"])
    lines.append("  Result: %s. %s" % (RESULT_LABEL[r["claim"]["result"]],
                                       r["claim"]["summary"]))
    p = r["pillars"]
    lines.append("  Response %s  Insight %s  Evidence %s  Limitations %s" % (
        MARK[p["response"]["state"]], MARK[p["insight"]["state"]],
        MARK[p["evidence"]["state"]], MARK[p["limitations"]["state"]]))
    s = r["source"]
    lines.append("  Posters: %d independent, %d platform(s). Flags: %s" % (
        s["independent_posters"], s["platforms"], ", ".join(s["flags"]) or "none"))
    shown = set()
    for c in r["citations"]:
        if c["url"] not in shown:
            shown.add(c["url"])
            lines.append("  Source [%s]: %s" % (c["source_tier"], c["url"]))
    for d in r["dropped_citations"]:
        lines.append("  Dropped citation: %s (%s)" % (d["url"], d["reason"]))
    lines.append("")
    return "\n".join(lines)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Solutions verification pipeline")
    parser.add_argument("--pitches", default=os.path.join(HERE, "sample_data", "pitches.json"))
    parser.add_argument("--evidence", default=os.path.join(HERE, "sample_data", "evidence.json"),
                        help="sample evidence file (offline mode only)")
    parser.add_argument("--offline", action="store_true",
                        help="use sample files and fake claim scores, no keys needed")
    parser.add_argument("--no-claimbuster", action="store_true",
                        help="live mode with FAKE claim scores (testing only)")
    parser.add_argument("--claims-only", action="store_true",
                        help="score claims + source checks only; no evidence search, no Anthropic key")
    parser.add_argument("--fake-claim-scores", action="store_true",
                        help="with --claims-only: use fake scores so no keys are needed")
    parser.add_argument("--local-scorer", metavar="MODEL",
                        help="with --claims-only: free local model file made by train_local_scorer.py")
    parser.add_argument("--max-pitches", type=int, default=MAX_PITCHES_PER_RUN,
                        help="stop after this many pitches (0 = no limit)")
    parser.add_argument("--max-scoring-calls", type=int, default=MAX_SCORING_CALLS_PER_RUN,
                        help="cap on claim-scoring calls, one per post (0 = no limit)")
    parser.add_argument("--max-evidence-calls", type=int, default=MAX_EVIDENCE_CALLS_PER_RUN,
                        help="cap on the PAID Claude evidence searches (0 = no limit)")
    parser.add_argument("--max-daily-evidence-calls", type=int, default=MAX_DAILY_EVIDENCE_CALLS,
                        help="paid searches allowed per day across ALL runs (0 = no limit)")
    parser.add_argument("--max-daily-scoring-calls", type=int, default=MAX_DAILY_SCORING_CALLS)
    parser.add_argument("--max-daily-spend", type=float, default=MAX_DAILY_SPEND_USD,
                        help="rough estimated dollars per day (0 = no limit)")
    parser.add_argument("--max-monthly-spend", type=float, default=MAX_MONTHLY_SPEND_USD,
                        help="rough estimated dollars per month (0 = no limit)")
    parser.add_argument("--usage-file", default=USAGE_FILE,
                        help="where daily/monthly usage is remembered; delete it to reset")
    parser.add_argument("--cutoff", type=float, default=CLAIM_SCORE_CUTOFF)
    parser.add_argument("--out", default="results.json")
    parser.add_argument("--html", default="results.html",
                        help="readable report you can open in a browser")
    args = parser.parse_args()

    if args.claims_only:
        if args.local_scorer:
            scorer = LocalClaimScorer(args.local_scorer)
        elif args.fake_claim_scores:
            scorer = OfflineClaimScorer()
        else:
            key = os.environ.get("CLAIMBUSTER_API_KEY")
            if not key:
                sys.exit("Set CLAIMBUSTER_API_KEY first (see README), or add "
                         "--fake-claim-scores to try it without a key.")
            scorer = ClaimBusterScorer(key)
        finder, fetch = None, None
    elif args.offline:
        finder = SampleEvidenceFinder(args.evidence)
        scorer, fetch = OfflineClaimScorer(), finder.fetch
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Set ANTHROPIC_API_KEY, or run with --offline to use the sample files.")
        if args.no_claimbuster:
            scorer = OfflineClaimScorer()
        else:
            key = os.environ.get("CLAIMBUSTER_API_KEY")
            if not key:
                sys.exit("Set CLAIMBUSTER_API_KEY, or pass --no-claimbuster for testing.")
            scorer = ClaimBusterScorer(key)
        finder, fetch = ClaudeEvidenceFinder(), fetch_page_text

    ledger = UsageLedger(
        args.usage_file, args.max_daily_scoring_calls or None,
        args.max_daily_evidence_calls or None, args.max_daily_spend or None,
        args.max_monthly_spend or None, ESTIMATED_COST_PER_EVIDENCE_CALL)
    budget = Budget(args.max_pitches or None, args.max_scoring_calls or None,
                    args.max_evidence_calls or None, ledger=ledger)
    scorer = LimitedScorer(scorer, budget)
    if finder is not None and not args.offline:
        # the evidence caps are for the PAID Claude step only; offline is free
        finder = LimitedFinder(finder, budget)
    print("Limits:        %s" % budget.summary())
    print("Claim scoring:", scorer.label)
    print("Evidence:     ", finder.label if finder else "not run (claims-only)")
    print("Cutoff:       %s (provisional)\n" % args.cutoff)

    with open(args.pitches, encoding="utf-8") as f:
        pitches = json.load(f)

    results, stopped = [], None
    for pitch in pitches:
        try:
            budget.use("pitches")

            if args.claims_only:
                result = score_claims(pitch, scorer, cutoff=args.cutoff)
            else:
                result = verify_pitch(pitch, scorer, finder, fetch, cutoff=args.cutoff)
        except LimitReached as error:
            stopped = str(error)
            break
        except Exception as error:  # keep going so one bad pitch does not stop the batch
            result = {"pitch_id": pitch.get("pitch_id"), "error": str(error)}
        results.append(result)
        print(format_tile(result))

    if stopped:
        done = len(results)
        print("STOPPED EARLY: %s. %d of %d pitches finished; the rest were not run." % (
            stopped, done, len(pitches)))
    print("Used:", budget.summary())
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    if args.claims_only:
        note = ("Claims-only run: " + scorer.label + ". No evidence search yet, "
                "so no trust tiers.")
    else:
        note = ("Sample run: fake claim scores and made-up sample evidence."
                if args.offline else "Live run.")
    if stopped:
        note += " STOPPED EARLY (%s): %d of %d pitches finished." % (stopped, len(results), len(pitches))
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(build_html(results, note))
    print("Wrote", args.out, "and", args.html)
    print("Open the readable report with:  open", args.html)


if __name__ == "__main__":
    main()
