# Solutions pipeline backend

Wraps your four scripts into one service. The editorial logic is unchanged:
`classify_solutions.py` and `make_pitches.py` are imported as-is, so editing their
INSTRUCTIONS still changes behavior. SQLite replaces the loose CSV/JSON files.

    search (bskysearch) -> classify -> pitch -> factcheck (ClaimBuster)
          |                   |          |           |
          +-------------- solutions.db (data/) -------+  <- every stage reads and writes here

## Run it
    pip install -r requirements.txt
    cp .env.example .env        # fill in keys
    python test_pipeline.py     # offline check, no keys needed
    uvicorn api:app --reload    # docs at http://127.0.0.1:8000/docs

    curl -X POST localhost:8000/runs -H 'content-type: application/json' \
      -d '{"topic":"dengue","query":"\"dengue response\"","max_results":100,"limit":5}'
    curl localhost:8000/runs/1                 # status + counts
    curl localhost:8000/runs/1/pitches?rating=high
    curl localhost:8000/runs/1/pitches/<pitch_id>

`limit` caps items per stage, so a first run is cheap. Re-running (`POST /runs/{id}/resume`)
only does unfinished work and retries failures.

## Changes to your scripts
Only `bskysearch.py`: it now saves link-card title/description (the classifier and pitch
scripts already read those columns, but nothing was producing them), raises an exception
instead of calling `sys.exit` so a server can survive it, and the example credentials in
its docstring are placeholders.

## ClaimBuster: what it can and can't tell you
It scores how check-worthy each claim is and finds similar claims fact-checkers already
checked. It does not say whether a claim is true. Results are triage for reporters. The
claims come from each pitch's `to_verify` list.

## Not built yet
`find_evidence.py` and `grade_pitches.py` (named in make_pitches.py's messages) weren't
uploaded. Add them as a stage in `pipeline.STAGE_FUNCS`.
Before exposing this beyond localhost: add auth to the API, and swap BackgroundTasks for
a job queue (RQ/Celery) if runs get long or numerous.

`check_accuracy.py`: `POST /runs/{id}/export`, then
`python check_accuracy.py --results data/run_1/solutions_results.csv --sheet data/run_1/accuracy_sheet.csv`
