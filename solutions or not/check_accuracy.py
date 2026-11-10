#!/usr/bin/env python3
"""
Checks how often the classifier agrees with a person.

Step 1. Make a sheet of posts for a person to label:
    python check_accuracy.py --make-sheet
    python check_accuracy.py --make-sheet --size 60

    This creates accuracy_sheet.csv. It includes every post the AI called a
    solution plus a random mix of the rest, shuffled. The AI's answers are
    left out, so the person labeling isn't swayed by them.

Step 2. Open accuracy_sheet.csv and fill in the human_label column for every
row with one of: solution, not_solution, unclear. Use the same rules as the
INSTRUCTIONS in classify_solutions.py, and open the post when the text isn't
enough. A person should do this, not Claude, or the check means nothing.

Step 3. Compare:
    python check_accuracy.py

To see whether a change helped, save the old results under another name, rerun
the classifier, and compare both against the same sheet:
    python check_accuracy.py --results solutions_results_old.csv
    python check_accuracy.py

The most important number is "missed solutions": real solutions the AI threw out.
Those are stories journalists never see.
"""

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ("solution", "not_solution", "unclear")
SHEET_FIELDS = ["url", "text", "link_title", "human_label", "notes"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_sheet(results, sheet_path, size, force):
    if sheet_path.exists() and not force:
        if any(row.get("human_label", "").strip() for row in read_csv(sheet_path)):
            sys.exit(f"{sheet_path.name} already has labels in it. Use --force to "
                     "replace it, which erases them.")

    solutions = [r for r in results if r["label"] == "solution"]
    others = [r for r in results if r["label"] != "solution"]
    rng = random.Random(2026)  # same sample every time, so it can be redone
    picked = solutions + rng.sample(others, max(0, min(size - len(solutions),
                                                        len(others))))
    rng.shuffle(picked)

    with open(sheet_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        for row in picked:
            writer.writerow({"url": row["url"], "text": row.get("text", ""),
                             "link_title": row.get("link_title", ""),
                             "human_label": "", "notes": ""})
    print(f"Made {sheet_path.name} with {len(picked)} posts. Fill in the "
          "human_label column, then run: python check_accuracy.py")


def compare(results, sheet_path):
    if not sheet_path.exists():
        sys.exit("No accuracy sheet yet. Run: python check_accuracy.py --make-sheet")

    ai = {row["url"]: row for row in results}
    pairs, problems = [], []
    for n, row in enumerate(read_csv(sheet_path), 2):  # row 1 is the header
        human = row.get("human_label", "").strip().lower()
        if not human:
            continue
        if human not in LABELS:
            problems.append(f"row {n}: '{human}' isn't one of {', '.join(LABELS)}")
        elif row["url"] not in ai:
            problems.append(f"row {n}: this post isn't in the results file")
        else:
            pairs.append((human, ai[row["url"]]["label"], row))

    for p in problems:
        print("Skipped", p)
    if not pairs:
        sys.exit("No usable labels yet. Fill in the human_label column first.")

    agree = sum(h == a for h, a, _ in pairs)
    print(f"\nChecked {len(pairs)} posts. The AI agreed with the person on "
          f"{agree} ({agree / len(pairs):.0%}).\n")

    counts = Counter((h, a) for h, a, _ in pairs)
    print(f"{'person said':<16}" + "".join(f"{'AI: ' + l:>18}" for l in LABELS))
    for h in LABELS:
        print(f"{h:<16}" + "".join(f"{counts[(h, a)]:>18}" for a in LABELS))

    missed = [r for h, a, r in pairs if h == "solution" and a != "solution"]
    false_alarms = [r for h, a, r in pairs if a == "solution" and h != "solution"]
    real = sum(h == "solution" for h, _, _ in pairs)
    if real:
        print(f"\nThe AI caught {real - len(missed)} of the {real} real solutions.")
    for title, rows in [("Missed solutions (the AI threw these out)", missed),
                        ("False alarms (the AI called these solutions)",
                         false_alarms)]:
        print(f"\n{title}: {len(rows)}")
        for r in rows:
            print(f"- {(r['text'] or '(no text)')[:90]}\n  {r['url']}")
    print("\nIf the same kind of mistake keeps showing up, add a matching example "
          "to the INSTRUCTIONS in classify_solutions.py and relabel.")


def main():
    ap = argparse.ArgumentParser(description="Check the classifier against a person.")
    ap.add_argument("--make-sheet", action="store_true",
                    help="Make a blank sheet for a person to label")
    ap.add_argument("--size", type=int, default=40,
                    help="How many posts to put on the sheet")
    ap.add_argument("--force", action="store_true",
                    help="Replace a sheet that already has labels")
    ap.add_argument("--results", default=HERE / "solutions_results.csv", type=Path)
    ap.add_argument("--sheet", default=HERE / "accuracy_sheet.csv", type=Path)
    args = ap.parse_args()

    if not args.results.exists():
        sys.exit(f"Could not find {args.results}. Run classify_solutions.py first.")
    results = read_csv(args.results)
    if args.make_sheet:
        make_sheet(results, args.sheet, args.size, args.force)
    else:
        compare(results, args.sheet)


if __name__ == "__main__":
    main()
