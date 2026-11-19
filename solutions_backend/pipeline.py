"""Runs the stages in order: search -> classify -> verify -> pitch -> factcheck.

The stage logic lives in the original scripts (bskysearch.py,
classify_solutions.py, make_pitches.py, and the Bluesky verifier through
verify.py); this file only calls their functions
and keeps the results in the database. Every stage skips work it has already
done, so running a run again picks up where it stopped and retries failures."""
import logging
import os
import threading
from collections import Counter

import anthropic

import config
import db
import factcheck
import verify
from bskysearch import BlueskyAccessError, login, search_posts
from classify_solutions import classify
from make_pitches import draft_pitch, group_posts, post_id, source_record

log = logging.getLogger("pipeline")
STAGES = ("search", "classify", "verify", "pitch", "factcheck")
RESUME_STAGES = STAGES[1:]   # re-running the search would just re-add the same posts

_active, _lock = set(), threading.Lock()


class FatalStageError(Exception):
    """Something that will fail every time (bad key, wrong model), so stop."""


def make_client():
    return anthropic.Anthropic(max_retries=4)


def is_active(run_id):
    with _lock:
        return run_id in _active


def _llm_loop(items, work, save, what):
    done = failed = 0
    for item in items:
        try:
            result = work(item)
        except (anthropic.AuthenticationError, anthropic.NotFoundError) as e:
            raise FatalStageError(f"{type(e).__name__}: {e}") from e
        except (anthropic.APIError, KeyError, ValueError) as e:
            failed += 1   # left unsaved, so the next run tries it again
            log.warning("%s failed for %s: %s", what, item["url"], e)
            continue
        save(item, result)
        done += 1
    return done, failed


# --------------------------------------------------------------- stages ---
def stage_search(run, params, limit):
    handle, pw = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    token = login(handle, pw) if handle and pw else None
    try:
        posts = list(search_posts(
            params["query"], params.get("max_results", 100), params.get("sort", "latest"),
            params.get("lang"), params.get("since"), params.get("until"), token))
    except BlueskyAccessError as e:
        raise FatalStageError(str(e)) from e
    return {"found": len(posts), "new": db.add_posts(run["id"], posts), "failed": 0}


def stage_classify(run, params, limit):
    todo = db.unclassified_posts(run["id"])[:limit]
    client, labels = make_client(), Counter()

    def save(post, result):
        db.save_classification(run["id"], post["url"], result)
        labels[result["label"]] += 1

    done, failed = _llm_loop(
        todo, lambda p: classify(client, config.CLASSIFY_MODEL, run["topic"], p),
        save, "classify")
    return {"classified": done, "failed": failed, **labels}


def stage_verify(run, params, limit):
    """Tier every solution post with the Bluesky verifier. No AI and no paid
    APIs: it fetches the pages posts cite and searches OpenAlex (free)."""
    if not verify.available():
        log.warning("bluesky-verifier folder not found; skipping the verify stage")
        return {"skipped": "bluesky-verifier not found", "failed": 0}
    if not db.unverified_solution_count(run["id"]):
        return {"verified": 0, "failed": 0}
    solutions = db.solution_posts(run["id"])
    try:
        rows, usage = verify.verify_posts(run["topic"], solutions, db.list_posts(run["id"]))
    except Exception as e:  # unverified posts still get pitches; resume retries this
        log.exception("verifier failed")
        return {"verified": 0, "failed": 1, "error": str(e)}
    db.save_verifications(run["id"], rows)
    tiers = Counter(r["tier"] for r in rows)
    return {"verified": len(rows), "failed": 0, **tiers,
            "page_fetches": usage.get("page_fetches_this_run", 0)}


def stage_pitch(run, params, limit):
    """Josie's make_pitches.py: group posts about the same response, then write
    one pitch per group. Posts about an existing pitch's response are added to
    it and the pitch is rewritten."""
    posts = db.solutions_without_pitch(run["id"])
    if not posts:
        return {"pitched": 0, "failed": 0}
    client = make_client()
    pitches = db.pitch_list(run["id"])
    try:
        groups = group_posts(client, config.PITCH_MODEL, run["topic"], posts, pitches)
    except (anthropic.AuthenticationError, anthropic.NotFoundError) as e:
        raise FatalStageError(f"{type(e).__name__}: {e}") from e
    except (anthropic.APIError, ValueError) as e:
        log.warning("grouping failed: %s", e)
        return {"pitched": 0, "failed": 1}

    new_groups = [g for g in groups if g[1] is None]
    updates = [g for g in groups if g[1] is not None]
    if limit:
        new_groups = new_groups[:limit]
    by_id = {p["pitch_id"]: p for p in pitches}
    done = failed = 0
    for members, target in updates + new_groups:
        new_sources = [source_record(posts[m]) for m in members]
        if target:
            old = by_id[target]
            have = {s["url"] for s in old["sources"]}
            sources = old["sources"] + [s for s in new_sources if s["url"] not in have]
            pitch_id = target
        else:
            sources = new_sources
            pitch_id = post_id(sources[0]["url"])  # the earliest post
        try:
            pitch = draft_pitch(client, config.PITCH_MODEL, run["topic"], sources, pitch_id)
        except (anthropic.AuthenticationError, anthropic.NotFoundError) as e:
            raise FatalStageError(f"{type(e).__name__}: {e}") from e
        except (anthropic.APIError, KeyError, ValueError) as e:
            failed += 1   # its posts stay unpitched, so the next run tries again
            log.warning("pitch failed for %s: %s", pitch_id, e)
            continue
        db.save_pitch(run["id"], sources[0]["url"], pitch)
        by_id[pitch_id] = pitch
        done += 1
    combined = sum(len(m) for m, _ in groups) - len(groups)
    return {"pitched": done, "failed": failed, "combined": combined}


def stage_factcheck(run, params, limit):
    if not config.claimbuster_key():
        log.warning("CLAIMBUSTER_API_KEY not set; skipping the factcheck stage")
        return {"skipped": "CLAIMBUSTER_API_KEY not set", "failed": 0}
    done = failed = 0
    for url, pitch in db.pitches_without_factcheck(run["id"])[:limit]:
        try:
            rows = factcheck.check_claims(factcheck.claims_from_pitch(pitch))
        except factcheck.ClaimBusterError as e:
            if "API key" in str(e):
                raise FatalStageError(str(e)) from e
            failed += 1
            log.warning("factcheck failed for %s: %s", url, e)
            continue
        db.save_factchecks(run["id"], url, rows)
        done += 1
    return {"checked": done, "failed": failed}


STAGE_FUNCS = {"search": stage_search, "classify": stage_classify,
               "verify": stage_verify, "pitch": stage_pitch, "factcheck": stage_factcheck}


# --------------------------------------------------------------- runner ---
def run_pipeline(run_id, stages=STAGES, limit=None):
    """Run the chosen stages for one run. Safe to call again for the same run."""
    with _lock:
        if run_id in _active:
            return False
        _active.add(run_id)
    stage = None
    try:
        run = db.get_run(run_id)
        results = dict(run["stage_results"])
        db.update_run(run_id, status="running", error=None)
        for stage in stages:
            db.update_run(run_id, stage=stage)
            results[stage] = STAGE_FUNCS[stage](run, run["params"], limit)
            db.update_run(run_id, stage_results=results)
        partial = any(r.get("failed") for r in results.values())
        db.update_run(run_id, status="partial" if partial else "done", stage=None)
    except Exception as e:  # keep the server alive; record what went wrong
        log.exception("run %s failed in stage %s", run_id, stage)
        db.update_run(run_id, status="failed", error=f"{stage}: {e}")
    finally:
        with _lock:
            _active.discard(run_id)
    return True
