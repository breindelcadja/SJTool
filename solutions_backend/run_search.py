#!/usr/bin/env python3
"""Run the pipeline from the command line on a real search and print what came back.
No server needed.

Cheapest first test (search + classify only, no pitches, no ClaimBuster):
    python run_search.py dengue --query '"dengue response"' --max 50 --stages search classify

Small full test (5 items per stage):
    python run_search.py dengue --max 50 --limit 5

Continue or retry an earlier run:
    python run_search.py dengue --run-id 1 --stages classify pitch factcheck

Needs ANTHROPIC_API_KEY, BSKY_HANDLE and BSKY_APP_PASSWORD (in .env or exported).
CLAIMBUSTER_API_KEY is only needed for the factcheck stage; without it that stage is skipped.
"""
import argparse
import logging
import sys

import db
import factcheck
import pipeline


def show(run_id):
    run = db.get_run(run_id)
    print(f"\n=== Run {run_id}: {run['status']} ===")
    for stage, result in run["stage_results"].items():
        print(f"  {stage}: {result}")
    if run["error"]:
        print(f"  ERROR: {run['error']}")
    c = db.counts(run_id)
    print(f"\n{c['posts']} posts | {c['solutions']} solutions, {c['not_solutions']} not, "
          f"{c['unclear']} unclear | {c['pitches']} pitches, {c['factchecked']} fact-checked")

    for label in ("solution", "unclear"):
        posts = db.list_posts(run_id, label)
        if posts:
            print(f"\n--- {label} ({len(posts)}) ---")
        for p in posts:
            print(f"* [{p['confidence']}] {(p['text'] or '')[:110]}\n    why: {p['reason']}\n    {p['url']}")

    for item in db.get_pitches(run_id):
        pitch = item["pitch"]
        print(f"\n--- pitch: {pitch['headline']} ({pitch['usefulness']['reason']}) ---")
        print(f"    {item['url']}")
        if item["factchecked_at"]:
            s = factcheck.summarize(item["factchecks"])
            print(f"    fact-check: {s['claims']} claims, {s['check_worthy']} check-worthy, "
                  f"{s['with_prior_factchecks']} with prior fact-checks")
            for f in item["factchecks"]:
                score = "n/a" if f["score"] is None else f"{f['score']:.2f}"
                print(f"      [{score}] {f['claim']}")
    print(f"\nFull data: python -c \"import export; print(export.export_run({run_id}))\"")


def main():
    ap = argparse.ArgumentParser(description="Run the pipeline on a real search.")
    ap.add_argument("topic", help='What the posts are about, e.g. "dengue"')
    ap.add_argument("--query", help="Bluesky search text (default: the topic). "
                    "Use quotes inside quotes for an exact phrase")
    ap.add_argument("--max", type=int, default=50, help="Max posts to fetch")
    ap.add_argument("--sort", choices=["latest", "top"], default="latest")
    ap.add_argument("--lang")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--limit", type=int, help="Cap items per stage (cheap test)")
    ap.add_argument("--stages", nargs="+", choices=pipeline.STAGES, default=list(pipeline.STAGES))
    ap.add_argument("--run-id", type=int, help="Continue an existing run")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    db.init_db()
    if args.run_id:
        if not db.get_run(args.run_id):
            sys.exit(f"No run {args.run_id}")
        run_id = args.run_id
    else:
        run_id = db.create_run(args.topic, {
            "topic": args.topic, "query": args.query or args.topic,
            "max_results": args.max, "sort": args.sort, "lang": args.lang,
            "since": args.since, "until": args.until})
    print(f"Run {run_id}: stages {', '.join(args.stages)}")
    pipeline.run_pipeline(run_id, tuple(args.stages), args.limit)
    show(run_id)


if __name__ == "__main__":
    main()
