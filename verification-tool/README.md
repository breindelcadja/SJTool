# Solutions verification pipeline

Takes a pitch (a proposed solution plus the public posts it came from) and returns a
tier, the claim that was checked with its result and sources, and the four-pillar
checklist. It plugs in after your scraper and the step that decides a post is a solution.

```
posts -> claim scoring (ClaimBuster) -> source checks (code)
      -> evidence search (Claude + web search) -> citation checks (code)
      -> tier assignment (code) -> result
```

Only two steps use a model. Flags, counts, citation checks and tiers are plain code,
so they are tested without any keys.

## Set up in VS Code

1. Install Python 3.9 or newer and open this folder in VS Code.
2. In the terminal, create an environment:
   - Mac/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
3. `pip install -r requirements.txt` (only needed for live mode)

## Run it

With the sample files, no keys needed:

```
python run.py --offline
python -m unittest discover -s tests -t .
```

Offline mode uses made-up sample pitches, canned evidence and FAKE claim scores. The
output labels this at the top. The tests cover the spec's test cases.

Live mode, on your own pitches:

```
export ANTHROPIC_API_KEY="..."        # Windows PowerShell: $env:ANTHROPIC_API_KEY="..."
export CLAIMBUSTER_API_KEY="..."
python run.py --pitches my_pitches.json
```

Add `--no-claimbuster` to try live evidence search before you have a ClaimBuster key
(uses fake claim scores, testing only). Results are printed and saved to `results.json`.

## Use it from your own code

```python
from verification.claims import ClaimBusterScorer
from verification.evidence import ClaudeEvidenceFinder
from verification.citations import fetch_page_text
from verification.pipeline import verify_pitch

result = verify_pitch(pitch, ClaimBusterScorer(key), ClaudeEvidenceFinder(), fetch_page_text)
```

`pitch` is a dict: `pitch_id`, `problem`, `solution_summary`, and `posts`, each post with
`platform`, `url`, `text`, `author_id` (hashed), `account_age_days` and `posts_last_hour`
(both may be `None`), and `posted_at`. Map your scraper's output to this shape.

## Files

- `verification/pipeline.py`: `verify_pitch`, the whole flow
- `verification/claims.py`: ClaimBuster scoring and the offline fake
- `verification/source_checks.py`: flags and independent-poster counts
- `verification/evidence.py`: Claude evidence search and the sample finder
- `verification/citations.py`: citation checks and source tiers by domain
- `verification/tiers.py`: tier rules
- `verification/config.py`: every threshold, all provisional
- `sample_data/`: made-up pitches and evidence

## Read before trusting it

- **The live calls are untested.** I could not reach ClaimBuster or the Claude API from
  my workspace. `ClaimBusterScorer` follows ClaimBuster's docs as understood, and
  `ClaudeEvidenceFinder` follows Anthropic's web search docs. Everything else, including
  the logic that decides tiers, was run and tested offline.
- **Check the model name** in `config.py` is available on your account (`VERIFIER_MODEL`
  overrides it). Each pitch is one request with up to 6 searches, so it uses your
  Anthropic credits.
- **The response pillar needs no citation.** It comes from the posts themselves, since the
  earlier step already decided they describe a solution. The spec said every pillar
  needs a citation, so confirm this choice.
- **Citation checks are strict.** A citation is dropped if its page cannot be loaded or
  the quote is not on it. Pages built with JavaScript and PDFs return no text, so
  citations to them are dropped. Add a better fetcher if that matters.
- **Claim scores come from a model trained on political speech.** Test on 50 to 100 real
  dengue posts and set the cutoff from that.
- **All thresholds are provisional.** Tune them in `config.py` after journalists rank a
  sample batch. `config_version` is stamped on every result.
- **The sample data is made up.** The sources, pages and findings are not real.


## Real ClaimBuster checks (free, no Anthropic key)

`--claims-only` scores every post with ClaimBuster and runs the source checks.
It does NOT search for evidence and does NOT assign trust tiers, so it never
uses your Claude/Anthropic key and costs nothing extra.

1. Get a ClaimBuster API key from the ClaimBuster API docs page
   (I could not open that page, so follow what it says there).
2. In the Terminal window you run the tool from, set the key. Never paste it
   into chat or save it in a file you share:

       export CLAIMBUSTER_API_KEY="your-key-here"

3. Run it:

       python3 run.py --claims-only
       open results.html

4. Use your own pitches (same format as sample_data/pitches.json):

       python3 run.py --claims-only --pitches my_pitches.json

No key yet? Add `--fake-claim-scores` to try the flow. Fake scores are labeled
and are not real.

The response format of the ClaimBuster call is untested by me. If the first run
errors, send me the error text (not the key).

Tuning: the 0.5 cutoff is a guess. Take 50 to 100 real dengue posts, mark which
a human thinks are checkable, then compare to the scores and move `--cutoff`.

## For the real product (journalists never touch this)

Your backend calls one function. Keys come from the server's secrets, never
from the journalist.

    from verification.api import verify_pitches
    tiles = verify_pitches(pitches,
                           claimbuster_key=os.environ["CLAIMBUSTER_API_KEY"],
                           evidence_finder=None)   # None = claims-only

Each tile has a `status`: verified, awaiting_evidence, no_checkable_claim or
error. Show journalists the tier, claim, result, pillars, flags and sources.
Keep `internal` (ClaimBuster score etc.) for your team, not the UI.
Before launch: check ClaimBuster's terms and rate limits, decide who pays for
the evidence step, and test on real dengue posts.

## Free local claim scorer (no API key, no cost)

Built from the labeled sentences in https://github.com/utaresearch/claimbuster-spotter
(that repo is training code; it ships no ready-to-use model). This is a small
scikit-learn model you train yourself. It is NOT the ClaimBuster API.

    git clone https://github.com/utaresearch/claimbuster-spotter ~/claimbuster-spotter
    pip3 install scikit-learn joblib
    python3 train_local_scorer.py --data ~/claimbuster-spotter/data/two_class
    python3 run.py --claims-only --local-scorer local_claim_model.joblib --cutoff 0.2
    open results.html

Know before you rely on it:
- Held-out accuracy (AUC 0.93) is on political debate sentences, not dengue posts.
- On the sample pitches at the default 0.5 cutoff it dropped the myth and two
  other pitches, so use a lower cutoff (0.2 to start) and tune on real posts.
- The repo is GPL v3 and the data's license is unclear. Do not commit or ship
  the trained model or the data until your team has checked.

## Usage limits (caps per run)

Every run has caps so it can't overspend or flood a service. Defaults are in
`verification/config.py`; override on the command line (0 = no limit):

    python3 run.py --claims-only --max-pitches 10 --max-scoring-calls 100
    python3 run.py --max-evidence-calls 3        # the PAID Claude step

- `--max-pitches`: stop after this many pitches (default 25).
- `--max-scoring-calls`: claim-scoring calls, one per post (default 200).
- `--max-evidence-calls`: paid Claude searches (default 5, the tightest).

When a cap is hit the run stops cleanly, keeps what it finished, and says so
in the terminal and at the top of results.html. ClaimBuster 429 ("too many
requests") errors are retried with waiting, then reported clearly. I could not
confirm ClaimBuster's, Google's or PubMed's actual rate limits from here, so read
each service's docs and set `PAUSE_SECONDS` and the caps to match.

## Daily and monthly caps (remembered across runs)

Usage is saved in `usage_log.json` (in the folder you run from), so running the
tool many times can't get around the limits. Defaults live in
`verification/config.py`; override on the command line (0 = no limit):

    python3 run.py --max-daily-evidence-calls 10 --max-daily-spend 2 --max-monthly-spend 15

- Daily paid searches: 20. Daily scoring calls: 1000.
- Estimated spend: $3 per day, $25 per month.
- Delete `usage_log.json` to reset. It resets on its own each new day/month.

The dollar figure is a ROUGH ESTIMATE ($0.15 per paid search, set by
`ESTIMATED_COST_PER_EVIDENCE_CALL`), not your real bill. Claim scoring is
counted as free. Also set a spending limit in the Anthropic Console as the real
backstop. If the tool runs on a server shared by several people, point
`--usage-file` (or `VERIFIER_USAGE_FILE`) at one shared location. The file is
not safe for two runs at the same moment.

## Push to the team's GitHub repo

    ./push_to_github.sh                                   asks for the repo address
    ./push_to_github.sh --repo https://github.com/owner/repo
    ./push_to_github.sh --dry-run                         everything except the push

It clones the team repo to a temporary folder, adds this project in a
`verification-tool/` folder on a NEW branch, scans for keys, shows what will be
sent, and pushes only after you say yes. It never pushes to main and never
changes your project folder. Keys, usage logs, trained models, result files and
the real scraper CSV are always left out. Then open the pull request link it prints.
