#!/usr/bin/env python3
"""
SJTool's database: one file, sjtool.db, that holds everything the website needs
to decide which pitches each journalist sees.

    journalists        profiles from onboarding.py (journalists.json)
    pitches            pitches from make_pitches.py (pitches.json)
    pitch_sources      the Bluesky posts behind each pitch
    fact_checks        each pitch's fact-check rating (fact_checks.json)
    fact_check_claims  each claim's verdict and sources
    rankings           each journalist's ranked pitches (rankings.json)

Usage, from inside the "solutions or not" folder (no API key needed):
    python database.py sync                         # load the latest files in
    python database.py feed --journalist jane@example.com
    python database.py feed --journalist jane-doe --all   # include hidden ones
    python database.py stats

Run sync after any of the other scripts. It's safe to run again anytime: it
replaces old rows with the latest data. The website (or any code) can read the
feed with:  from database import feed;  feed("jane@example.com")

WHICH PITCHES A JOURNALIST SEES (the feed)
Starts from their ranking (rank_pitches.py), then hides:
    - pitches rated not_relevant to them
    - pitches whose fact-check came back low (a claim was contradicted, or
      nothing could be confirmed)
and shows at most MAX_PITCHES. Pitches not fact-checked yet are shown, marked
unchecked. Change the settings just below to change the rules.

sjtool.db holds journalists' emails, so it's left out of GitHub (see .gitignore).
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "sjtool.db"

# Feed settings
MAX_PITCHES = 10
HIDE_RELEVANCE = {"not_relevant"}
HIDE_CONFIDENCE = {"low"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS journalists (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT UNIQUE,          -- NULL when skipped
    beat                 TEXT NOT NULL,
    area                 TEXT NOT NULL,
    area_type            TEXT,
    area_country         TEXT,
    area_lat             REAL,
    area_lon             REAL,
    area_verified        INTEGER NOT NULL DEFAULT 0,
    publication          TEXT NOT NULL,
    publication_source   TEXT,                 -- wikidata, website, freelance
    publication_verified INTEGER NOT NULL DEFAULT 0,
    added_on             TEXT,
    updated_on           TEXT
);
CREATE TABLE IF NOT EXISTS pitches (
    pitch_id           TEXT PRIMARY KEY,
    status             TEXT,
    headline           TEXT NOT NULL,
    summary            TEXT,
    problem            TEXT,
    solution           TEXT,
    response           TEXT,
    location           TEXT,
    beat               TEXT,
    why_now            TEXT,
    usefulness_rating  TEXT,
    usefulness_points  INTEGER,
    full_pitch         TEXT NOT NULL           -- the whole pitch as JSON
);
CREATE TABLE IF NOT EXISTS pitch_sources (
    pitch_id    TEXT NOT NULL REFERENCES pitches(pitch_id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    author      TEXT,
    posted_on   TEXT,
    text        TEXT,
    link_url    TEXT,
    link_title  TEXT,
    PRIMARY KEY (pitch_id, url)
);
CREATE TABLE IF NOT EXISTS fact_checks (
    pitch_id              TEXT PRIMARY KEY REFERENCES pitches(pitch_id) ON DELETE CASCADE,
    confidence            TEXT NOT NULL,       -- high, medium, low
    reason                TEXT,
    solution_verdict      TEXT,
    solution_explanation  TEXT,
    solution_sources      TEXT,                -- JSON list of {title, url}
    checked_on            TEXT
);
CREATE TABLE IF NOT EXISTS fact_check_claims (
    pitch_id     TEXT NOT NULL REFERENCES fact_checks(pitch_id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    claim        TEXT NOT NULL,
    verdict      TEXT NOT NULL,                -- confirmed, partly_confirmed, contradicted, not_found
    explanation  TEXT,
    sources      TEXT,                         -- JSON list of {title, url}
    PRIMARY KEY (pitch_id, position)
);
CREATE TABLE IF NOT EXISTS rankings (
    journalist_id  TEXT NOT NULL REFERENCES journalists(id) ON DELETE CASCADE,
    pitch_id       TEXT NOT NULL REFERENCES pitches(pitch_id) ON DELETE CASCADE,
    rank           INTEGER NOT NULL,
    relevance      TEXT NOT NULL,              -- high, medium, low, not_relevant
    reason         TEXT,
    ranked_on      TEXT,
    PRIMARY KEY (journalist_id, pitch_id)
);
CREATE INDEX IF NOT EXISTS rankings_by_journalist ON rankings (journalist_id, rank);
"""


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def read_json(name, default):
    path = HERE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


JSON_COLUMNS = {"full_pitch", "solution_sources", "sources"}
TABLE_ORDER = ["journalists", "pitches", "pitch_sources", "fact_checks",
               "fact_check_claims", "rankings"]


def rows():
    """Every row for every table, built from the latest files, as dicts whose
    keys are the column names. Used here for sjtool.db and by supabase_sync.py
    for the hosted database. JSON columns hold Python lists and dicts."""
    journalists = read_json("journalists.json", [])
    pitches = read_json("pitches.json", [])
    checks = read_json("fact_checks.json", [])
    rankings = read_json("rankings.json", {})
    out = {t: [] for t in TABLE_ORDER}

    for j in journalists:
        area = j.get("area_details") or {}
        pub = j.get("publication_details") or {}
        out["journalists"].append({
            "id": j["id"], "name": j["name"], "email": j.get("email") or None,
            "beat": j["beat"], "area": j["area"], "area_type": area.get("type"),
            "area_country": area.get("country"), "area_lat": area.get("lat"),
            "area_lon": area.get("lon"), "area_verified": int(bool(area.get("verified"))),
            "publication": j["publication"], "publication_source": pub.get("source"),
            "publication_verified": int(bool(pub.get("verified"))),
            "added_on": j.get("added_on"), "updated_on": j.get("updated_on")})

    for p in pitches:
        use = p.get("usefulness", {})
        out["pitches"].append({
            "pitch_id": p["pitch_id"], "status": p.get("status"),
            "headline": p["headline"], "summary": p.get("summary"),
            "problem": p.get("problem"), "solution": p.get("solution"),
            "response": p.get("response"), "location": p.get("location"),
            "beat": p.get("beat"), "why_now": p.get("why_now"),
            "usefulness_rating": use.get("rating"), "usefulness_points": use.get("points"),
            "full_pitch": p})
        seen = set()
        for src in p.get("sources") or [p.get("source", {})]:
            if src.get("url") and src["url"] not in seen:
                seen.add(src["url"])
                out["pitch_sources"].append({
                    "pitch_id": p["pitch_id"], "url": src["url"],
                    "author": src.get("author"), "posted_on": src.get("date"),
                    "text": src.get("text"), "link_url": src.get("link_url"),
                    "link_title": src.get("link_title")})

    pitch_ids = {p["pitch_id"] for p in pitches}
    for c in checks:
        if c["pitch_id"] not in pitch_ids:
            continue  # a check for a pitch that no longer exists
        sol = c.get("solution_evidence", {})
        out["fact_checks"].append({
            "pitch_id": c["pitch_id"], "confidence": c["confidence"],
            "reason": c.get("reason"), "solution_verdict": sol.get("verdict"),
            "solution_explanation": sol.get("explanation"),
            "solution_sources": sol.get("sources", []), "checked_on": c.get("checked_on")})
        for n, cl in enumerate(c.get("claims", []), 1):
            out["fact_check_claims"].append({
                "pitch_id": c["pitch_id"], "position": n, "claim": cl["claim"],
                "verdict": cl["verdict"], "explanation": cl.get("explanation"),
                "sources": cl.get("sources", [])})

    journalist_ids = {j["id"] for j in journalists}
    for jid, r in rankings.items():
        if jid not in journalist_ids:
            continue  # a ranking for someone no longer onboarded
        for item in r.get("pitches", []):
            if item["pitch_id"] in pitch_ids:
                out["rankings"].append({
                    "journalist_id": jid, "pitch_id": item["pitch_id"],
                    "rank": item["rank"], "relevance": item["relevance"],
                    "reason": item.get("reason"), "ranked_on": r.get("ranked_on")})
    return out


def sync():
    """Replace the database's contents with the latest files, in one step, so
    the website never sees a half-loaded database."""
    data = rows()
    conn = connect()
    with conn:  # one transaction: all or nothing
        for table in reversed(TABLE_ORDER):
            conn.execute(f"DELETE FROM {table}")
        for table in TABLE_ORDER:
            for row in data[table]:
                cols = list(row)
                values = [json.dumps(row[c], ensure_ascii=False) if c in JSON_COLUMNS
                          else row[c] for c in cols]
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) "
                             f"VALUES ({', '.join('?' * len(cols))})", values)
    counts = {t: len(data[t]) for t in ("journalists", "pitches", "fact_checks", "rankings")}
    conn.close()
    return counts


def find_journalist(conn, who):
    who = (who or "").strip().lower()
    return conn.execute("SELECT * FROM journalists WHERE id = ? OR lower(email) = ?",
                        (who, who)).fetchone()


def feed(who, show_all=False, limit=MAX_PITCHES):
    """The pitches one journalist sees, best first. `who` is an email or ID.
    Returns a list of dicts, or None if there's no such journalist."""
    conn = connect()
    journalist = find_journalist(conn, who)
    if journalist is None:
        conn.close()
        return None
    rows = conn.execute("""
        SELECT r.rank, r.relevance, r.reason,
               p.pitch_id, p.headline, p.summary, p.solution, p.location, p.beat,
               p.usefulness_rating, p.usefulness_points,
               COALESCE(f.confidence, 'unchecked') AS fact_check_confidence,
               f.reason AS fact_check_reason
        FROM rankings r
        JOIN pitches p ON p.pitch_id = r.pitch_id
        LEFT JOIN fact_checks f ON f.pitch_id = r.pitch_id
        WHERE r.journalist_id = ?
        ORDER BY r.rank
    """, (journalist["id"],)).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        hidden = (item["relevance"] in HIDE_RELEVANCE or
                  item["fact_check_confidence"] in HIDE_CONFIDENCE)
        item["hidden"] = hidden
        if hidden and not show_all:
            continue
        item["sources"] = [dict(s) for s in conn.execute(
            "SELECT url, author, link_url, link_title FROM pitch_sources WHERE pitch_id = ?",
            (item["pitch_id"],))]
        items.append(item)
    conn.close()
    return items if show_all else items[:limit]


def main():
    ap = argparse.ArgumentParser(description="SJTool's database.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="Load the latest files into sjtool.db")
    f = sub.add_parser("feed", help="Show the pitches one journalist sees")
    f.add_argument("--journalist", required=True, help="Their email or ID")
    f.add_argument("--all", action="store_true", help="Include hidden pitches")
    sub.add_parser("stats", help="Count what's in the database")
    args = ap.parse_args()

    if args.command == "sync":
        counts = sync()
        print("Synced sjtool.db: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
        if counts["journalists"] and not counts["rankings"]:
            print("No rankings yet. Run: python rank_pitches.py, then sync again.")

    elif args.command == "stats":
        conn = connect()
        for table in ("journalists", "pitches", "pitch_sources", "fact_checks",
                      "fact_check_claims", "rankings"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<18} {n}")
        conn.close()

    elif args.command == "feed":
        items = feed(args.journalist, show_all=args.all)
        if items is None:
            sys.exit(f"No journalist with the email or ID {args.journalist}. "
                     "Did you run: python database.py sync?")
        if not items:
            print("No pitches to show yet. Run rank_pitches.py, then database.py sync.")
        for item in items:
            flag = "  (hidden)" if item["hidden"] else ""
            print(f"{item['rank']}. {item['headline']}{flag}")
            print(f"   {item['relevance']} relevance · fact-check "
                  f"{item['fact_check_confidence']} · {item['reason']}")


if __name__ == "__main__":
    main()
