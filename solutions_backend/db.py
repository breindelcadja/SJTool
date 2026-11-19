"""SQLite storage. This replaces the loose CSV/JSON files as the source of
truth, so every stage can see what the others have done and a run can resume
after a crash. export.py writes the old CSV files for check_accuracy.py."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  params TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued, running, done, partial, failed
  stage TEXT,
  stage_results TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  url TEXT NOT NULL,
  author TEXT, created_at TEXT, text TEXT,
  likes INTEGER, reposts INTEGER, replies INTEGER,
  link_url TEXT, link_title TEXT, link_description TEXT,
  quoted_text TEXT, image_alt TEXT,
  label TEXT, confidence TEXT, reason TEXT, classified_at TEXT,
  PRIMARY KEY (run_id, url)
);
CREATE TABLE IF NOT EXISTS pitches (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  url TEXT NOT NULL,
  pitch_id TEXT NOT NULL,
  data TEXT NOT NULL,
  usefulness_rating TEXT, usefulness_points INTEGER,
  created_at TEXT NOT NULL,
  factchecked_at TEXT,
  PRIMARY KEY (run_id, url)
);
CREATE TABLE IF NOT EXISTS factchecks (
  run_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  claim_idx INTEGER NOT NULL,
  claim TEXT NOT NULL,
  score REAL,
  sentence_scores TEXT,
  matches TEXT,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (run_id, url, claim_idx)
);
-- Which posts each pitch was built from. A pitch can combine several posts
-- about the same response; pitches.url is the first of them.
CREATE TABLE IF NOT EXISTS pitch_sources (
  run_id INTEGER NOT NULL,
  pitch_url TEXT NOT NULL,
  url TEXT NOT NULL,
  PRIMARY KEY (run_id, url)
);
-- The Bluesky verifier's result for each solution post: its tier (Strong lead,
-- Promising, Early signal, Flagged or Myth) and the tile shown to reporters.
CREATE TABLE IF NOT EXISTS verifications (
  run_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  solution_id TEXT,
  tier TEXT NOT NULL,
  tier_reason TEXT,
  tile TEXT,
  verified_at TEXT NOT NULL,
  PRIMARY KEY (run_id, url)
);
"""

# Tiers the verifier keeps away from reporters. These posts get no pitch.
HIDDEN_TIERS = ("Myth", "Flagged")

POST_COLUMNS = ["author", "created_at", "text", "likes", "reposts", "replies",
                "link_url", "link_title", "link_description", "quoted_text", "image_alt"]
RUN_FIELDS = {"status", "stage", "error", "stage_results"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        # Existing local databases predate storage of the URL behind a post's
        # link card. Add it in place so upgrading preserves earlier runs.
        columns = {row["name"] for row in c.execute("PRAGMA table_info(posts)")}
        for column in ("link_url", "quoted_text", "image_alt"):
            if column not in columns:
                c.execute(f"ALTER TABLE posts ADD COLUMN {column} TEXT")
        # Pitches from before pitch_sources existed came from one post each.
        c.execute("INSERT OR IGNORE INTO pitch_sources (run_id, pitch_url, url) "
                  "SELECT run_id, url, url FROM pitches")


# ----------------------------------------------------------------- runs ---
def create_run(topic, params):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO runs (topic, params, created_at, updated_at) VALUES (?,?,?,?)",
            (topic, json.dumps(params), now(), now()))
        return cur.lastrowid


def update_run(run_id, **fields):
    assert set(fields) <= RUN_FIELDS, fields
    if "stage_results" in fields:
        fields["stage_results"] = json.dumps(fields["stage_results"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE runs SET {sets}, updated_at=? WHERE id=?",
                  (*fields.values(), now(), run_id))


def _run_dict(row):
    d = dict(row)
    d["params"] = json.loads(d["params"])
    d["stage_results"] = json.loads(d["stage_results"])
    return d


def get_run(run_id):
    with connect() as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _run_dict(row) if row else None


def list_runs():
    with connect() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
    return [_run_dict(r) for r in rows]


def counts(run_id):
    with connect() as c:
        r = c.execute(
            "SELECT COUNT(*) AS posts, "
            "COALESCE(SUM(label IS NOT NULL),0) AS classified, "
            "COALESCE(SUM(label='solution'),0) AS solutions, "
            "COALESCE(SUM(label='not_solution'),0) AS not_solutions, "
            "COALESCE(SUM(label='unclear'),0) AS unclear "
            "FROM posts WHERE run_id=?", (run_id,)).fetchone()
        p = c.execute(
            "SELECT COUNT(*) AS pitches, "
            "COALESCE(SUM(factchecked_at IS NOT NULL),0) AS factchecked "
            "FROM pitches WHERE run_id=?", (run_id,)).fetchone()
        v = c.execute(
            "SELECT COUNT(*) AS verified, "
            f"COALESCE(SUM(tier IN ({','.join('?' * len(HIDDEN_TIERS))})),0) AS hidden "
            "FROM verifications WHERE run_id=?", (*HIDDEN_TIERS, run_id)).fetchone()
    return {**dict(r), **dict(p), **dict(v)}


# ---------------------------------------------------------------- posts ---
def add_posts(run_id, posts):
    """Insert posts, skipping ones already stored. Returns how many were new."""
    with connect() as c:
        before = c.total_changes
        for p in posts:
            c.execute(
                f"INSERT OR IGNORE INTO posts (run_id, url, {', '.join(POST_COLUMNS)}) "
                f"VALUES (?, ?, {', '.join('?' * len(POST_COLUMNS))})",
                (run_id, p["url"], *[p.get(k) for k in POST_COLUMNS]))
        return c.total_changes - before


def unclassified_posts(run_id):
    with connect() as c:
        rows = c.execute("SELECT * FROM posts WHERE run_id=? AND label IS NULL "
                         "ORDER BY created_at DESC", (run_id,)).fetchall()
    return [dict(r) for r in rows]


def save_classification(run_id, url, result):
    with connect() as c:
        c.execute("UPDATE posts SET label=?, confidence=?, reason=?, classified_at=? "
                  "WHERE run_id=? AND url=?",
                  (result["label"], result["confidence"], result["reason"], now(),
                   run_id, url))


def list_posts(run_id, label=None):
    """Posts with their verifier tier and the pitch they ended up in, if any."""
    q = ("SELECT p.*, v.tier, v.tier_reason, s.pitch_url FROM posts p "
         "LEFT JOIN verifications v ON v.run_id=p.run_id AND v.url=p.url "
         "LEFT JOIN pitch_sources s ON s.run_id=p.run_id AND s.url=p.url "
         "WHERE p.run_id=?")
    args = [run_id]
    if label:
        q += " AND p.label=?"
        args.append(label)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY p.created_at DESC", args)]


# --------------------------------------------------------- verification ---
def solution_posts(run_id):
    with connect() as c:
        rows = c.execute("SELECT * FROM posts WHERE run_id=? AND label='solution' "
                         "ORDER BY created_at", (run_id,)).fetchall()
    return [dict(r) for r in rows]


def unverified_solution_count(run_id):
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) FROM posts p LEFT JOIN verifications v "
            "ON v.run_id=p.run_id AND v.url=p.url "
            "WHERE p.run_id=? AND p.label='solution' AND v.url IS NULL",
            (run_id,)).fetchone()[0]


def save_verifications(run_id, rows):
    """rows: dicts with url, solution_id, tier, tier_reason and tile (or None).
    Replaces the run's earlier results, because the verifier judges posts together."""
    with connect() as c:
        c.execute("DELETE FROM verifications WHERE run_id=?", (run_id,))
        for r in rows:
            c.execute(
                "INSERT OR REPLACE INTO verifications (run_id, url, solution_id, tier, "
                "tier_reason, tile, verified_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, r["url"], r.get("solution_id"), r["tier"], r.get("tier_reason"),
                 json.dumps(r["tile"], ensure_ascii=False) if r.get("tile") else None, now()))


def verifications_by_url(run_id):
    with connect() as c:
        rows = c.execute("SELECT * FROM verifications WHERE run_id=?", (run_id,)).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["tile"] = json.loads(d["tile"]) if d["tile"] else None
        out[d["url"]] = d
    return out


# -------------------------------------------------------------- pitches ---
def solutions_without_pitch(run_id):
    """Solution posts not yet in a pitch, leaving out ones the verifier hid."""
    with connect() as c:
        rows = c.execute(
            "SELECT p.* FROM posts p "
            "LEFT JOIN pitch_sources s ON s.run_id=p.run_id AND s.url=p.url "
            "LEFT JOIN verifications v ON v.run_id=p.run_id AND v.url=p.url "
            "WHERE p.run_id=? AND p.label='solution' AND s.url IS NULL "
            f"AND (v.tier IS NULL OR v.tier NOT IN ({','.join('?' * len(HIDDEN_TIERS))})) "
            "ORDER BY p.created_at", (run_id, *HIDDEN_TIERS)).fetchall()
    return [dict(r) for r in rows]


def pitch_list(run_id):
    """Every pitch in a run, as the pitch dicts make_pitches.py writes."""
    with connect() as c:
        rows = c.execute("SELECT data FROM pitches WHERE run_id=?", (run_id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def save_pitch(run_id, url, pitch):
    """Save a pitch under its first post's URL. Saving it again (with more posts)
    replaces it and clears its fact-check, so the new version is checked."""
    u = pitch.get("usefulness", {})
    sources = [s["url"] for s in pitch.get("sources", [])] or [url]
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO pitches (run_id, url, pitch_id, data, "
            "usefulness_rating, usefulness_points, created_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, url, pitch["pitch_id"], json.dumps(pitch, ensure_ascii=False),
             u.get("rating"), u.get("points"), now()))
        for source in sources:
            c.execute("INSERT OR REPLACE INTO pitch_sources (run_id, pitch_url, url) "
                      "VALUES (?,?,?)", (run_id, url, source))


def pitches_without_factcheck(run_id):
    with connect() as c:
        rows = c.execute("SELECT url, data FROM pitches "
                         "WHERE run_id=? AND factchecked_at IS NULL", (run_id,)).fetchall()
    return [(r["url"], json.loads(r["data"])) for r in rows]


def save_factchecks(run_id, url, rows):
    with connect() as c:
        c.execute("DELETE FROM factchecks WHERE run_id=? AND url=?", (run_id, url))
        for i, r in enumerate(rows):
            c.execute(
                "INSERT INTO factchecks (run_id, url, claim_idx, claim, score, "
                "sentence_scores, matches, checked_at) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, url, i, r["claim"], r["score"],
                 json.dumps(r["sentence_scores"]), json.dumps(r["matches"]), now()))
        c.execute("UPDATE pitches SET factchecked_at=? WHERE run_id=? AND url=?",
                  (now(), run_id, url))


def get_pitches(run_id, rating=None):
    q, args = "SELECT * FROM pitches WHERE run_id=?", [run_id]
    if rating:
        q += " AND usefulness_rating=?"
        args.append(rating)
    with connect() as c:
        pitches = c.execute(q + " ORDER BY usefulness_points DESC, created_at", args).fetchall()
        fcs = c.execute("SELECT * FROM factchecks WHERE run_id=? ORDER BY url, claim_idx",
                        (run_id,)).fetchall()
    by_url = {}
    for f in fcs:
        d = dict(f)
        d["sentence_scores"] = json.loads(d["sentence_scores"] or "[]")
        d["matches"] = json.loads(d["matches"] or "[]")
        by_url.setdefault(d["url"], []).append(d)
    return [{"url": p["url"], "pitch_id": p["pitch_id"], "pitch": json.loads(p["data"]),
             "factchecked_at": p["factchecked_at"], "factchecks": by_url.get(p["url"], [])}
            for p in pitches]
