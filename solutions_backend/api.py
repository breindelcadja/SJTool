"""HTTP API. Start it with:  uvicorn api:app --reload
Interactive docs appear at http://127.0.0.1:8000/docs"""
import logging
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

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
    stages: Optional[list[Literal["search", "classify", "pitch", "factcheck"]]] = None
    limit: Optional[int] = Field(None, ge=1)


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
    background.add_task(pipeline.run_pipeline, run_id, stages, req.limit)
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


def _pitch_view(item, full):
    p = item["pitch"]
    summary = factcheck.summarize(item["factchecks"]) if item["factchecked_at"] else None
    brief = {"pitch_id": item["pitch_id"], "headline": p["headline"],
             "usefulness": p["usefulness"]["rating"], "points": p["usefulness"]["points"],
             "source_url": item["url"], "factcheck_summary": summary}
    if not full:
        return brief
    return {**brief, "pitch": p, "factchecks": item["factchecks"]}


@app.get("/runs/{run_id}/pitches")
def get_pitches(run_id: int, rating: Optional[Literal["high", "medium", "low"]] = None):
    _run_or_404(run_id)
    return [_pitch_view(i, full=False) for i in db.get_pitches(run_id, rating)]


@app.get("/runs/{run_id}/pitches/{pitch_id}")
def get_pitch(run_id: int, pitch_id: str):
    _run_or_404(run_id)
    for item in db.get_pitches(run_id):
        if item["pitch_id"] == pitch_id:
            return _pitch_view(item, full=True)
    raise HTTPException(404, f"No pitch {pitch_id} in run {run_id}")


@app.post("/runs/{run_id}/export")
def export_run(run_id: int):
    _run_or_404(run_id)
    return {"folder": str(export.export_run(run_id))}
