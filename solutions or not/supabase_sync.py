#!/usr/bin/env python3
"""
Copies SJTool's data to and from the hosted database (Supabase), which is what
the website reads and writes.

    python supabase_sync.py pull     # bring website sign-ups into journalists.json
    python supabase_sync.py push     # send pitches, fact-checks and rankings up
    python supabase_sync.py status   # count what's in the hosted database

The usual order after new pitches or new sign-ups:
    python supabase_sync.py pull
    python rank_pitches.py
    python supabase_sync.py push

ONE-TIME SETUP
1. Create a free project at https://supabase.com.
2. In the project, open SQL Editor, paste all of supabase_schema.sql, click Run.
3. Click Connect (top of the project page), choose "Session pooler", and copy
   the connection string. Put your database password in place of [YOUR-PASSWORD].
4. pip install "psycopg[binary]"
5. In Terminal, paste this, then copy the connection string and press Enter
   (same trick as the API key):
       export SUPABASE_DB_URL="$(pbpaste | tr -d '[:space:]')"
The connection string contains your database password. Never put it in a file
in this repo, in the website's code, or in a screenshot.

HOW PUSH WORKS
Everything goes up in one step, so the website never sees half-updated data.
Pitches that no longer exist locally are removed, along with their sources,
fact-checks and rankings. Journalists are never deleted by a push, since people
sign up on the website; push only adds or updates the ones in journalists.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:
    sys.exit('Missing the psycopg package. Run: pip install "psycopg[binary]"')

from database import JSON_COLUMNS, TABLE_ORDER, rows

HERE = Path(__file__).resolve().parent
JOURNALISTS = HERE / "journalists.json"
KEYS = {"journalists": ["id"], "pitches": ["pitch_id"],
        "pitch_sources": ["pitch_id", "url"], "fact_checks": ["pitch_id"],
        "fact_check_claims": ["pitch_id", "position"],
        "rankings": ["journalist_id", "pitch_id"]}


def connect():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("No database connection found. See the setup steps at the top of "
                 "supabase_sync.py, then run:\n"
                 '  export SUPABASE_DB_URL="$(pbpaste | tr -d \'[:space:]\')"')
    try:
        return psycopg.connect(url, connect_timeout=20)
    except psycopg.OperationalError as e:
        sys.exit(f"Couldn't connect to the database. Check the connection string "
                 f"and password, and use the Session pooler one.\n({e})")


def insert(cur, table, items, upsert=False):
    """Insert rows (dicts with the same keys). With upsert, update rows whose
    key already exists instead of failing."""
    if not items:
        return
    cols = list(items[0])
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join(['%s'] * len(cols))})")
    if upsert:
        keys = KEYS[table]
        updates = [c for c in cols if c not in keys and c != "added_on"]
        sql += (f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in updates))
    values = [[Jsonb(row[c]) if c in JSON_COLUMNS else row[c] for c in cols]
              for row in items]
    cur.executemany(sql, values)


def push():
    data = rows()
    pitch_ids = [p["pitch_id"] for p in data["pitches"]]
    ranked_ids = sorted({r["journalist_id"] for r in data["rankings"]})
    conn = connect()
    try:
        with conn.transaction(), conn.cursor() as cur:  # all or nothing
            insert(cur, "journalists", data["journalists"], upsert=True)
            insert(cur, "pitches", data["pitches"], upsert=True)
            # Pitches that no longer exist locally go, with their sources,
            # fact-checks and rankings (the tables cascade).
            cur.execute("DELETE FROM pitches WHERE NOT (pitch_id = ANY(%s))", [pitch_ids])
            # Replace the details of every current pitch.
            cur.execute("DELETE FROM pitch_sources WHERE pitch_id = ANY(%s)", [pitch_ids])
            insert(cur, "pitch_sources", data["pitch_sources"])
            cur.execute("DELETE FROM fact_checks WHERE pitch_id = ANY(%s)", [pitch_ids])
            insert(cur, "fact_checks", data["fact_checks"])
            insert(cur, "fact_check_claims", data["fact_check_claims"])
            # Replace the rankings of everyone ranked locally.
            cur.execute("DELETE FROM rankings WHERE journalist_id = ANY(%s)", [ranked_ids])
            insert(cur, "rankings", data["rankings"])
    except psycopg.errors.UniqueViolation as e:
        sys.exit("Push stopped, and nothing was changed: someone in journalists.json "
                 "has the same email as a different profile on the website. Run "
                 f"python supabase_sync.py pull first, then push again.\n({e.diag.message_primary})")
    except psycopg.errors.UndefinedTable:
        sys.exit("The tables don't exist yet. Run supabase_schema.sql in your "
                 "Supabase project's SQL Editor first.")
    finally:
        conn.close()
    print(f"Pushed {len(data['pitches'])} pitches, {len(data['fact_checks'])} "
          f"fact-checks, rankings for {len(ranked_ids)} journalists, and "
          f"{len(data['journalists'])} local profiles.")


def pull():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM journalists ORDER BY added_on NULLS LAST, id")
            cols = [d.name for d in cur.description]
            remote = [dict(zip(cols, r)) for r in cur.fetchall()]
    except psycopg.errors.UndefinedTable:
        sys.exit("The tables don't exist yet. Run supabase_schema.sql in your "
                 "Supabase project's SQL Editor first.")
    finally:
        conn.close()

    local = json.loads(JOURNALISTS.read_text(encoding="utf-8")) if JOURNALISTS.exists() else []
    by_id = {j["id"]: j for j in local}
    new = updated = 0
    for r in remote:
        profile = {
            "id": r["id"], "name": r["name"], "email": r["email"] or "",
            "beat": r["beat"], "area": r["area"],
            "area_details": {"verified": bool(r["area_verified"]), "type": r["area_type"],
                             "country": r["area_country"], "lat": r["area_lat"],
                             "lon": r["area_lon"]},
            "publication": r["publication"],
            "publication_details": {"verified": bool(r["publication_verified"]),
                                    "source": r["publication_source"]},
            "added_on": r["added_on"], "updated_on": r["updated_on"],
        }
        # The website's copy wins. Drop a local profile with the same email
        # under a different ID, so there's only one of each person.
        if profile["email"]:
            for j in [j for j in by_id.values()
                      if j.get("email", "").lower() == profile["email"] and j["id"] != r["id"]]:
                del by_id[j["id"]]
        if r["id"] in by_id:
            old = by_id[r["id"]]
            # Keep extra local details (like map info) the website doesn't store.
            for key in ("area_details", "publication_details"):
                profile[key] = {**old.get(key, {}), **{k: v for k, v in profile[key].items()
                                                       if v is not None}}
            if old != profile:
                updated += 1
        else:
            new += 1
        by_id[r["id"]] = profile

    tmp = JOURNALISTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(by_id.values()), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(JOURNALISTS)
    print(f"Pulled {len(remote)} profiles from the website: {new} new, {updated} "
          f"updated. journalists.json now has {len(by_id)}.")
    if new or updated:
        print("Next: python rank_pitches.py, then python supabase_sync.py push")


def status():
    conn = connect()
    try:
        with conn.cursor() as cur:
            for table in TABLE_ORDER:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"{table:<18} {cur.fetchone()[0]}")
    except psycopg.errors.UndefinedTable:
        sys.exit("The tables don't exist yet. Run supabase_schema.sql first.")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Sync with the hosted database.")
    ap.add_argument("command", choices=["pull", "push", "status"])
    args = ap.parse_args()
    {"pull": pull, "push": push, "status": status}[args.command]()


if __name__ == "__main__":
    main()
