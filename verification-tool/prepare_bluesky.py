#!/usr/bin/env python3
"""Step 1 check: read raw Bluesky scraper output and show what the tool sees.

  python3 prepare_bluesky.py                          uses the sample file
  python3 prepare_bluesky.py --posts my_posts.json    your scraper's output
"""
import argparse
import json
import os

from verification.bluesky import (clean_raw_posts, is_link_only, load_posts,
                                  prepare_posts, social_lead)

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--posts", default=os.path.join(HERE, "sample_data", "bluesky_posts.json"))
    p.add_argument("--out", default="prepared_posts.json")
    p.add_argument("--all", action="store_true", help="list every post, not just high-engagement ones")
    a = p.parse_args()
    raw, report = clean_raw_posts(load_posts(a.posts))
    print("Rows read: %(rows)d | duplicates removed: %(duplicates_removed)d | "
          "empty removed: %(empty_removed)d | kept: %(kept)d | link-only posts: %(link_only)d\n" % report)
    posts = prepare_posts(raw)
    for post in posts:
        if not post["high_engagement"] and "--all" not in __import__("sys").argv:
            continue
        e = post["engagement"]
        age = post["account_age_days"]
        print("%s  eng %d (%d likes, %d reposts, %d replies)  account age %s days%s" % (
            post["author_id"], e["total"], e["likes"], e["reposts"], e["replies"],
            "unknown" if age is None else age,
            "   <-- HIGH ENGAGEMENT" if post["high_engagement"] else ""))
        print("   %s" % post["text"][:100])
    lead = social_lead(posts)
    print()
    if lead:
        print("Social lead: %s" % lead["post_url"])
        print("  Reach out: @%s  %s" % (lead["contact"]["handle"], lead["contact"]["profile_url"]))
        print("  %s" % lead["note"])
    else:
        print("No post qualifies as a social lead.")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    print("\nWrote", a.out)


if __name__ == "__main__":
    main()
