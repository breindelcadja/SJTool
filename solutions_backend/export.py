"""Writes a run's data back out as CSV/JSON, including the files that
check_accuracy.py expects (solutions_results.csv)."""
import csv
import json

import config
import db


def export_run(run_id):
    out = config.DATA_DIR / f"run_{run_id}"
    out.mkdir(parents=True, exist_ok=True)

    posts = [p for p in db.list_posts(run_id) if p["label"]]
    fields = ["label", "confidence", "reason", "created_at", "author", "text",
              "likes", "reposts", "replies", "url", "link_url", "link_title",
              "link_description"]
    for name, rows in [("solutions_results.csv", posts),
                       ("solutions_only.csv", [p for p in posts if p["label"] == "solution"])]:
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    (out / "pitches.json").write_text(
        json.dumps(db.get_pitches(run_id), indent=2, ensure_ascii=False), encoding="utf-8")
    return out
