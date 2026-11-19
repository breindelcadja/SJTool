"""HTTP API. Start it with:  uvicorn api:app --reload
Interactive docs appear at http://127.0.0.1:8000/docs"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles

import db
import export
import factcheck
import pipeline

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


app = FastAPI(title="Solutions pipeline", lifespan=lifespan)


class RunRequest(BaseModel):
    topic: str = Field(min_length=1, description='What the posts are about, e.g. "dengue"')
    query: Optional[str] = Field(None, description='Bluesky search text. Defaults to the '
                                 'topic. Use quotes for an exact phrase: "\\"dengue response\\""')
    max_results: int = Field(100, ge=1, le=1000)
    sort: Literal["latest", "top"] = "latest"
    lang: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: Optional[int] = Field(None, ge=1, description="Cap items per stage (test runs)")


class ResumeRequest(BaseModel):
    stages: Optional[list[Literal["search", "classify", "verify", "pitch", "factcheck"]]] = None
    limit: Optional[int] = Field(None, ge=1)


# Spending caps for a public server. Set these on the host (e.g. MAX_POSTS_CAP=50,
# STAGE_LIMIT_CAP=12) so no one can start a search that uses lots of API credits.
# Unset (as on your laptop) means no cap.
def _cap(name):
    value = os.environ.get(name)
    return int(value) if value else None


MAX_POSTS_CAP = _cap("MAX_POSTS_CAP")
STAGE_LIMIT_CAP = _cap("STAGE_LIMIT_CAP")


def _capped_limit(limit):
    if STAGE_LIMIT_CAP is None:
        return limit
    return min(limit, STAGE_LIMIT_CAP) if limit else STAGE_LIMIT_CAP


def _run_or_404(run_id):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, f"No run {run_id}")
    return run


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/runs", status_code=202)
def create_run(req: RunRequest, background: BackgroundTasks):
    if MAX_POSTS_CAP is not None:
        req.max_results = min(req.max_results, MAX_POSTS_CAP)
    req.limit = _capped_limit(req.limit)
    params = req.model_dump()
    params["query"] = req.query or req.topic
    run_id = db.create_run(req.topic, params)
    background.add_task(pipeline.run_pipeline, run_id, pipeline.STAGES, req.limit)
    return {"run_id": run_id, "status": "queued"}


@app.post("/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: int, req: ResumeRequest, background: BackgroundTasks):
    _run_or_404(run_id)
    if pipeline.is_active(run_id):
        raise HTTPException(409, "This run is already in progress")
    stages = tuple(req.stages) if req.stages else pipeline.RESUME_STAGES
    background.add_task(pipeline.run_pipeline, run_id, stages, _capped_limit(req.limit))
    return {"run_id": run_id, "status": "queued", "stages": stages}


@app.get("/runs")
def list_runs():
    return db.list_runs()


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    return {**_run_or_404(run_id), "counts": db.counts(run_id)}


@app.get("/runs/{run_id}/posts")
def get_posts(run_id: int, label: Optional[Literal["solution", "not_solution", "unclear"]] = None):
    _run_or_404(run_id)
    return db.list_posts(run_id, label)


TIER_ORDER = ("Strong lead", "Promising", "Early signal", "Flagged", "Myth")


def _verification(sources, verifications):
    """The verifier's best result among a pitch's posts, if it checked them."""
    found = [verifications[u] for u in sources if u in verifications]
    if not found:
        return None
    best = min(found, key=lambda v: TIER_ORDER.index(v["tier"]) if v["tier"] in TIER_ORDER else 99)
    return {"tier": best["tier"], "tier_reason": best["tier_reason"], "tile": best["tile"]}


def _pitch_view(item, full, verifications):
    p = item["pitch"]
    summary = factcheck.summarize(item["factchecks"]) if item["factchecked_at"] else None
    sources = [s["url"] for s in p.get("sources", [])] or [item["url"]]
    check = _verification(sources, verifications)
    brief = {"pitch_id": item["pitch_id"], "headline": p["headline"],
             "summary": p.get("summary", ""),
             "usefulness": p["usefulness"]["rating"], "points": p["usefulness"]["points"],
             "source_url": item["url"], "source_urls": sources,
             "tier": check["tier"] if check else None,
             "factcheck_summary": summary}
    if not full:
        return brief
    return {**brief, "pitch": p, "factchecks": item["factchecks"], "verification": check}


@app.get("/runs/{run_id}/pitches")
def get_pitches(run_id: int, rating: Optional[Literal["high", "medium", "low"]] = None):
    _run_or_404(run_id)
    verifications = db.verifications_by_url(run_id)
    return [_pitch_view(i, False, verifications) for i in db.get_pitches(run_id, rating)]


@app.get("/runs/{run_id}/pitches/{pitch_id}")
def get_pitch(run_id: int, pitch_id: str):
    _run_or_404(run_id)
    for item in db.get_pitches(run_id):
        if item["pitch_id"] == pitch_id:
            return _pitch_view(item, True, db.verifications_by_url(run_id))
    raise HTTPException(404, f"No pitch {pitch_id} in run {run_id}")


@app.post("/runs/{run_id}/export")
def export_run(run_id: int):
    _run_or_404(run_id)
    return {"folder": str(export.export_run(run_id))}


# Keep the browser interface in the same service as the API so the frontend
# can call these routes without a separate development server or CORS setup.
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
