# Bluesky verification layer

Takes Bluesky posts that the classifier already marked as solutions and returns
3-5 "tiles" for journalists: the solution, a tier, the evidence, the original
post and how to reach the author. Phase 1 uses no third-party APIs.

## Quick start

    pip install -r requirements.txt
    python -m pytest -q                      # 50 tests, no network needed
    python -m bsky_verifier.cli data/bluesky_results_1.csv --issue "dengue" \
        --labels data/solution_labels_dengue.csv --out-dir out

Outputs: `out/tiles.json` (what the web page shows), `out/results.html`
(the page), `out/audit_log.json` (everything, including Myth and Flagged).

## The one function the backend calls

    from bsky_verifier import verify_solutions
    tiles = verify_solutions(posts, issue="dengue")

`posts` is a list of dicts with the scraper's columns (created_at, author,
text, likes, reposts, replies, url). Every post passed in is treated as a
confirmed solution. Optional keyword arguments: `config`, `labels`,
`context_posts` (the whole scrape, for duplicate and posting-rate checks),
`profiles` ({handle: account creation datetime}), `max_tiles`.

## Files, in reading order

| File | What it does |
|---|---|
| `bsky_verifier/models.py` | Data shapes: Post, Link, Solution, CheckResult, tiers |
| `bsky_verifier/ingest.py` | Reads the CSV; timestamps, duplicates, links, language |
| `bsky_verifier/claims.py` | Claim spotter (ClaimBuster-data SVM, rule-based fallback) |
| `bsky_verifier/context.py` | The pluggable `Verifier` interface |
| `bsky_verifier/source_checks.py` | Account age, posting rate, same text from 3+ accounts |
| `bsky_verifier/citation_checks.py` | Page loads AND claim text is on it; primary labels |
| `bsky_verifier/contradiction_checks.py` | Curated myths; number mismatches |
| `bsky_verifier/fetcher.py` | HTTP fetching, failure reasons, usage caps |
| `bsky_verifier/tiers.py` | Traction thresholds and tier rules R1-R6 |
| `bsky_verifier/pipeline.py` | Ties it together; `verify_solutions` |
| `bsky_verifier/render.py`, `cli.py` | HTML page and command line |
| `config.yaml` | Every threshold and cap |
| `data/known_contradictions.yaml` | Human-reviewed myth list with sources |
| `data/solution_labels_dengue.csv` | STAND-IN for the classifier on the dengue sample |
| `scripts/train_claimbuster_svm.py` | Rebuilds `models/claimbuster_svm.joblib` |

## Adding a check (or swapping in an API later)

Write a class with a `name` and a `run(solution, ctx)` method that returns a
list of `CheckResult(check_name, score, evidence, confidence, source_url, outcome=...)`,
then add it to the verifier list in `pipeline.py`. The tier engine only reads
`outcome` and `details`, so a Phase 2 API check can replace a local one
without touching the tier rules.

## Things to know

* `models/claimbuster_svm.joblib` is trained from the ClaimBuster repo's
  GPL-3.0 data. Ask before redistributing it. Delete it and the rule-based
  spotter takes over automatically.
* Never put Bluesky credentials in code. This project needs none.
