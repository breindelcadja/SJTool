"""
Command line: CSV in, JSON + HTML out.

    python -m bsky_verifier.cli data/bluesky_results_1.csv --issue "dengue" \
        --labels data/solution_labels_dengue.csv --out-dir out

  --labels   CSV saying which posts the classifier marked as solutions
             (url,is_solution,solution_key,summary,note). Without it, every
             row in the input is treated as an already-confirmed solution.
  --no-fetch Skip page fetching (offline run).
"""

from __future__ import annotations

import argparse
import csv
import json
import os

from .pipeline import load_config, load_labels, run_pipeline
from .render import render_page


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify Bluesky solution posts.")
    ap.add_argument("csv", help="scraper CSV (created_at,author,text,likes,reposts,replies,url)")
    ap.add_argument("--issue", required=True, help='the journalist\'s issue, e.g. "dengue"')
    ap.add_argument("--labels", help="classifier output CSV (optional)")
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--max-tiles", type=int)
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.no_fetch:
        cfg["fetching"]["enabled"] = False
    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    labels = load_labels(args.labels) if args.labels else None

    report = run_pipeline(rows, args.issue, config=cfg, labels=labels,
                          context_posts=rows, max_tiles=args.max_tiles)

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "tiles.json"), "w", encoding="utf-8") as f:
        json.dump(report["tiles"], f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out_dir, "audit_log.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(args.out_dir, "results.html"), "w", encoding="utf-8") as f:
        f.write(render_page(report))

    # Short console summary.
    c, u = report["counts"], report["usage"]
    print(f"Issue: {report['issue']}   claim spotter: {report['claim_spotter']['name']}")
    print(f"{c['input_rows_after_dedupe']} posts after de-duplication "
          f"({c['dropped_rows']} rows dropped), {c['not_solutions']} not solutions, "
          f"{c['held_non_english']} held (non-English), {c['solutions']} solutions checked")
    for a in report["audit_log"]:
        mark = "TILE  " if a["in_top_tiles"] else ("hidden" if not a["shown_to_journalists"] else "extra ")
        print(f"  [{mark}] {a['tier']:<12} {a['rule']:<4} {a['solution_id']}")
    print(f"Page fetches: {u['page_fetches_this_run']}  est. cost: ${u['estimated_cost_usd']:.2f}")
    if u["cap_events"]:
        print("STOPPED EARLY - cap reached:", "; ".join(u["cap_events"]))
    print(f"Wrote {args.out_dir}/tiles.json, audit_log.json, results.html")


if __name__ == "__main__":
    main()
