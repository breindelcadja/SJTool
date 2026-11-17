"""Tests for the cases in the spec. Run from the project folder with:

    python -m unittest discover -s tests -t .

No keys or network needed: scorers, finders and page fetchers are stubs.
"""

import unittest

from verification.pipeline import verify_pitch
from verification.source_checks import source_checks

GOV = "https://vector.example.gov/pilot"
NEWS = "https://news.example.com/story"
PAGES = {
    GOV: "The pilot cut adult mosquito counts by 48 percent. The district applies larvicide after rain.",
    NEWS: "Officials said the treatment must be repeated every few weeks and costs rise in rain.",
}


class FixedScorer:
    def __init__(self, score):
        self.value = score

    def score(self, text):
        return [(text, self.value)]


class ScriptedFinder:
    def __init__(self, evidence):
        self.evidence = evidence

    def find(self, pitch, claim_text):
        return self.evidence


def fetch(url):
    return PAGES.get(url)


def post(author, text="Treating drains cut mosquito counts a lot.", age=500, rate=1,
         platform="reddit"):
    return {"platform": platform, "url": "https://x/" + author, "text": text,
            "author_id": author, "account_age_days": age, "posts_last_hour": rate}


def pitch(posts=None):
    return {"pitch_id": "t", "problem": "dengue", "solution_summary": "drains",
            "posts": posts or [post("a"), post("b", text="Another view of drains.")]}


def pillar(state, *ids):
    return {"state": state, "citations": list(ids)}


def strong_evidence(**overrides):
    evidence = {
        "claim_result": {"result": "supported", "summary": "Pilot found fewer mosquitoes."},
        "pillars": {
            "response": pillar("met"),
            "insight": pillar("met", "c2"),
            "evidence": pillar("met", "c1"),
            "limitations": pillar("partial", "c3"),
        },
        "contradiction": {"found": False, "citations": []},
        "citations": [
            {"id": "c1", "url": GOV, "supporting_quote": "The pilot cut adult mosquito counts by 48 percent."},
            {"id": "c2", "url": GOV, "supporting_quote": "The district applies larvicide after rain."},
            {"id": "c3", "url": NEWS, "supporting_quote": "Officials said the treatment must be repeated every few weeks"},
        ],
    }
    evidence.update(overrides)
    return evidence


def run(evidence, posts=None, score=0.9):
    return verify_pitch(pitch(posts), FixedScorer(score), ScriptedFinder(evidence), fetch)


class TierTests(unittest.TestCase):
    def test_1_strong_lead(self):
        self.assertEqual(run(strong_evidence())["tier"], "strong_lead")

    def test_2_promising_when_evidence_is_secondary_only(self):
        evidence = strong_evidence()
        evidence["pillars"]["evidence"] = pillar("partial", "c3")
        evidence["pillars"]["limitations"] = pillar("not_found")
        self.assertEqual(run(evidence)["tier"], "promising")

    def test_3_early_signal_when_only_the_idea_is_found(self):
        evidence = {"pillars": {"response": pillar("partial")}, "citations": [],
                    "contradiction": {"found": False, "citations": []}}
        self.assertEqual(run(evidence)["tier"], "early_signal")

    def test_4_flagged_when_posts_are_flagged_and_no_evidence(self):
        posts = [post("n%d" % i, text="Spray prevents it.", age=2, rate=20) for i in range(3)]
        evidence = {"pillars": {"response": pillar("partial")}, "citations": [],
                    "contradiction": {"found": False, "citations": []}}
        self.assertEqual(run(evidence, posts)["tier"], "flagged")

    def test_5_myth_when_a_credible_source_contradicts(self):
        evidence = {
            "claim_result": {"result": "contradicted", "summary": "Agency says no."},
            "pillars": {"response": pillar("met")},
            "contradiction": {"found": True, "citations": ["c1"]},
            "citations": [{"id": "c1", "url": GOV,
                           "supporting_quote": "The pilot cut adult mosquito counts by 48 percent."}],
        }
        result = run(evidence)
        self.assertEqual(result["tier"], "myth")
        self.assertEqual(result["claim"]["result"], "contradicted")

    def test_6_flags_do_not_demote_a_well_evidenced_pitch(self):
        posts = [post("n%d" % i, text="Drains cut counts.", age=2, rate=20) for i in range(3)]
        result = run(strong_evidence(), posts)
        self.assertEqual(result["tier"], "strong_lead")
        self.assertIn("new_account", result["source"]["flags"])

    def test_7_a_contradiction_caps_the_tier(self):
        evidence = strong_evidence(contradiction={"found": True, "citations": ["c3"]})
        self.assertEqual(run(evidence)["tier"], "myth")

    def test_a_contradiction_without_a_valid_source_does_not_count(self):
        evidence = strong_evidence(contradiction={"found": True, "citations": ["nope"]})
        self.assertEqual(run(evidence)["tier"], "strong_lead")


class SourceTests(unittest.TestCase):
    def test_8_unknown_signals_are_recorded_but_not_penalized(self):
        summary, _ = source_checks([post("a", age=None, rate=None)])
        self.assertIn("unknown_signals", summary["flags"])
        self.assertEqual(summary["flag_ratio"], 0.0)
        result = run(strong_evidence(), [post("a", age=None, rate=None)])
        self.assertEqual(result["tier"], "strong_lead")

    def test_10_repeated_text_counts_as_one_poster(self):
        posts = [post("u%d" % i, text="Same words every time!", age=900) for i in range(5)]
        summary, _ = source_checks(posts)
        self.assertEqual(summary["independent_posters"], 1)
        self.assertIn("duplicate_text", summary["flags"])


class CitationTests(unittest.TestCase):
    def test_9_a_quote_not_on_the_page_is_dropped_and_the_pillar_lowered(self):
        evidence = strong_evidence()
        evidence["citations"][0]["supporting_quote"] = "Fogging reduced mosquitoes by 99 percent."
        result = run(evidence)
        self.assertEqual(result["pillars"]["evidence"]["state"], "not_found")
        self.assertNotEqual(result["tier"], "strong_lead")
        self.assertEqual(result["dropped_citations"][0]["reason"], "quote_not_on_page")

    def test_social_media_sources_are_never_evidence(self):
        evidence = strong_evidence()
        evidence["citations"][0]["url"] = "https://www.reddit.com/r/x/comments/1/"
        result = run(evidence)
        self.assertEqual(result["dropped_citations"][0]["reason"], "social_media_source")
        self.assertEqual(result["pillars"]["evidence"]["state"], "not_found")

    def test_a_url_the_search_never_returned_is_dropped(self):
        evidence = strong_evidence(seen_urls=[NEWS])
        result = run(evidence)
        reasons = {d["reason"] for d in result["dropped_citations"]}
        self.assertIn("url_not_in_search_results", reasons)

    def test_an_unreachable_page_is_dropped(self):
        evidence = strong_evidence()
        evidence["citations"][2]["url"] = "https://gone.example.com/missing"
        result = run(evidence)
        self.assertEqual(result["pillars"]["limitations"]["state"], "not_found")


class ClaimGateTests(unittest.TestCase):
    def test_11_a_low_claim_score_is_dropped_before_verification(self):
        class ExplodingFinder:
            def find(self, *args):
                raise AssertionError("evidence search should not run")

        result = verify_pitch(pitch(), FixedScorer(0.1), ExplodingFinder(), fetch)
        self.assertEqual(result["next_step"], "dropped_no_checkable_claim")
        self.assertIsNone(result["tier"])


if __name__ == "__main__":
    unittest.main()


class ClaimsOnlyTests(unittest.TestCase):
    def pitch(self):
        return {"pitch_id": "p", "problem": "dengue", "solution_summary": "s",
                "posts": [{"platform": "bluesky", "url": "u1", "author_id": "a",
                           "text": "It works.", "account_age_days": 400,
                           "posts_last_hour": 1}]}

    def test_high_score_is_ready_and_has_no_tier(self):
        from verification.pipeline import score_claims
        r = score_claims(self.pitch(), FixedScorer(0.8))
        self.assertEqual(r["next_step"], "ready_for_evidence_search")
        self.assertIsNone(r["tier"])
        self.assertEqual(r["posts"][0]["claim_score"], 0.8)

    def test_low_score_is_dropped(self):
        from verification.pipeline import score_claims
        r = score_claims(self.pitch(), FixedScorer(0.1))
        self.assertEqual(r["next_step"], "dropped_no_checkable_claim")


class ApiTests(unittest.TestCase):
    def test_tiles_hide_internals_from_main_fields(self):
        import json, os
        from verification.api import verify_pitches
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sample_data", "pitches.json")) as f:
            pitches = json.load(f)
        tiles = verify_pitches(pitches, fake_claim_scores=True)
        self.assertEqual(len(tiles), len(pitches))
        for t in tiles:
            self.assertIn(t["status"], {"awaiting_evidence", "no_checkable_claim", "error"})
            self.assertNotIn("claim_score", t)

    def test_needs_a_key(self):
        from verification.api import verify_pitches
        with self.assertRaises(ValueError):
            verify_pitches([])


class LocalScorerTests(unittest.TestCase):
    def test_trains_and_scores(self):
        import tempfile, os
        try:
            import joblib
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import make_pipeline
        except ImportError:
            self.skipTest("scikit-learn not installed")
        from verification.local_scorer import LocalClaimScorer
        X = ["Cases fell 40 percent last year.", "Thanks for sharing!",
             "The program cut costs by 10 million dollars.", "I love this song.",
             "Unemployment is at 4 percent.", "Have a great day."]
        y = [1, 0, 1, 0, 1, 0]
        m = make_pipeline(TfidfVectorizer(), LogisticRegression()).fit(X, y)
        path = os.path.join(tempfile.mkdtemp(), "m.joblib")
        joblib.dump(m, path)
        out = LocalClaimScorer(path).score("Cases fell 30 percent. Thanks!")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(0 <= s <= 1 for _, s in out))


class LimitsTests(unittest.TestCase):
    def test_budget_stops_at_cap(self):
        from verification.limits import Budget, LimitReached, LimitedScorer
        b = Budget(max_scoring_calls=2)
        s = LimitedScorer(FixedScorer(0.9), b)
        s.score("a."); s.score("b.")
        with self.assertRaises(LimitReached):
            s.score("c.")

    def test_none_means_unlimited(self):
        from verification.limits import Budget
        b = Budget()
        for _ in range(1000):
            b.use("evidence")


class LedgerTests(unittest.TestCase):
    def ledger(self, path, **kw):
        from verification.limits import UsageLedger
        return UsageLedger(path, today=kw.pop("today", "2026-11-12"), **kw)

    def test_daily_evidence_cap_holds_across_runs(self):
        import os, tempfile
        from verification.limits import LimitReached
        path = os.path.join(tempfile.mkdtemp(), "u.json")
        a = self.ledger(path, max_daily_evidence=2)
        a.check_and_record("evidence"); a.check_and_record("evidence")
        b = self.ledger(path, max_daily_evidence=2)  # a new run, same day
        with self.assertRaises(LimitReached):
            b.check_and_record("evidence")

    def test_resets_next_day(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "u.json")
        self.ledger(path, max_daily_evidence=1).check_and_record("evidence")
        self.ledger(path, max_daily_evidence=1, today="2026-11-13").check_and_record("evidence")

    def test_spend_caps(self):
        import os, tempfile
        from verification.limits import LimitReached
        path = os.path.join(tempfile.mkdtemp(), "u.json")
        led = self.ledger(path, max_monthly_spend=0.30, cost_per_evidence=0.15)
        led.check_and_record("evidence"); led.check_and_record("evidence")
        with self.assertRaises(LimitReached):
            led.check_and_record("evidence")
        # still blocked on another day of the same month
        with self.assertRaises(LimitReached):
            self.ledger(path, max_monthly_spend=0.30, cost_per_evidence=0.15,
                        today="2026-11-20").check_and_record("evidence")

    def test_scoring_is_free_in_spend(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "u.json")
        led = self.ledger(path, max_daily_spend=0.01, cost_per_evidence=0.15)
        for _ in range(50):
            led.check_and_record("scoring")


class OfflineNotCappedTest(unittest.TestCase):
    def test_offline_runs_every_sample_pitch(self):
        import json, os, subprocess, sys, tempfile
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = tempfile.mkdtemp()
        out = os.path.join(d, "r.json")
        subprocess.run([sys.executable, os.path.join(here, "run.py"), "--offline",
                        "--out", out, "--html", os.path.join(d, "r.html"),
                        "--usage-file", os.path.join(d, "u.json")],
                       check=True, capture_output=True, cwd=d)
        with open(os.path.join(here, "sample_data", "pitches.json")) as f:
            total = len(json.load(f))
        with open(out) as f:
            self.assertEqual(len(json.load(f)), total)


class BlueskyStepTests(unittest.TestCase):
    def raw(self, likes=0, reposts=0, replies=0, **kw):
        d = {"text": "Larvicide cut mosquito counts by half.", "author": "a.bsky.social",
             "url": "https://bsky.app/profile/a.bsky.social/post/1",
             "likes": likes, "reposts": reposts, "replies": replies,
             "created_at": "2026-11-09T14:00:00Z",
             "account_created_at": "2026-10-01T00:00:00Z"}
        d.update(kw)
        return d

    def test_maps_fields_and_account_age(self):
        from verification.bluesky import prepare_posts
        p = prepare_posts([self.raw(5)], today="2026-11-10")[0]
        self.assertEqual(p["platform"], "bluesky")
        self.assertEqual(p["author_id"], "a.bsky.social")
        self.assertEqual(p["account_age_days"], 40)
        self.assertEqual(p["engagement"]["total"], 5)

    def test_missing_account_date_is_unknown_not_new(self):
        from verification.bluesky import prepare_posts
        p = prepare_posts([self.raw(account_created_at=None)])[0]
        self.assertIsNone(p["account_age_days"])

    def test_at_uri_becomes_web_link(self):
        from verification.bluesky import post_url
        raw = {"uri": "at://did:plc:abc/app.bsky.feed.post/3xyz"}
        self.assertEqual(post_url(raw), "https://bsky.app/profile/did:plc:abc/post/3xyz")

    def test_top_share_with_floor(self):
        from verification.bluesky import prepare_posts
        raws = [self.raw(likes=n, author="u%d" % n) for n in (100, 10, 8, 3, 0)]
        flags = [p["high_engagement"] for p in prepare_posts(raws)]
        self.assertEqual(flags, [True, False, False, False, False])  # top 20% of 5 = 1 post

    def test_zero_engagement_batch_has_no_lead(self):
        from verification.bluesky import prepare_posts, social_lead
        posts = prepare_posts([self.raw(), self.raw(author="b.bsky.social")])
        self.assertIsNone(social_lead(posts))

    def test_social_lead_has_contact_and_is_not_evidence(self):
        from verification.bluesky import build_pitch
        from verification.context import make_context
        ctx = make_context("dengue prevention", "Tampa, FL", "2025-11-01", "2026-11-01")
        pitch = build_pitch("p1", ctx, "larvicide", [self.raw(50, 10, 5)])
        lead = pitch["social_lead"]
        self.assertEqual(lead["contact"]["profile_url"], "https://bsky.app/profile/a.bsky.social")
        self.assertIn("not evidence", lead["note"])


class ContextAndEngagementTests(unittest.TestCase):
    def test_fixed_minimum_also_counts(self):
        from verification.bluesky import high_engagement_cutoff
        q = high_engagement_cutoff([500, 400, 300, 60, 1, 1, 1, 1, 1, 1],
                                   top_share=0.1, floor=5, fixed_min=50)
        self.assertTrue(q(500))   # top 10%
        self.assertTrue(q(60))    # not top 10%, but passes the fixed number
        self.assertFalse(q(1))

    def test_coverage_links_use_place_topic_timeframe(self):
        from verification.context import make_context, coverage_search_links
        ctx = make_context("dengue prevention", "Tampa, FL", "2025-11-01", "2026-11-01")
        links = coverage_search_links(ctx, "larvicide in storm drains")
        self.assertEqual(len(links), 2)
        for l in links:
            self.assertIn("Tampa", l["url"])
            self.assertIn("after%3A2025-11-01", l["url"])

    def test_set_coverage_rejects_bad_status(self):
        from verification.bluesky import build_pitch
        from verification.context import make_context, set_coverage
        ctx = make_context("dengue", "Tampa, FL", "2025-11-01", "2026-11-01")
        pitch = build_pitch("p", ctx, "s", [])
        self.assertEqual(pitch["coverage"]["status"], "not_checked")
        set_coverage(pitch, "not_reported")
        with self.assertRaises(ValueError):
            set_coverage(pitch, "maybe")


class RealScraperFormatTests(unittest.TestCase):
    def test_loads_real_csv_and_cleans_it(self):
        import os
        from verification.bluesky import clean_raw_posts, load_posts, prepare_posts
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "sample_data", "bluesky_results_real.csv")
        if not os.path.exists(path):
            self.skipTest("real scraper CSV not present (kept out of the repo on purpose)")
        raws = load_posts(path)
        self.assertEqual(len(raws), 100)
        kept, report = clean_raw_posts(raws)
        self.assertGreater(report["duplicates_removed"], 0)
        self.assertEqual(report["rows"], report["kept"] + report["duplicates_removed"]
                         + report["empty_removed"])
        posts = prepare_posts(kept)
        self.assertTrue(all(p["account_age_days"] is None for p in posts))

    def test_link_only_detection(self):
        from verification.bluesky import is_link_only
        self.assertTrue(is_link_only("www.cidrap.umn.edu/dengue/60-ne..."))
        self.assertTrue(is_link_only("https://example.com/x #dengue"))
        self.assertFalse(is_link_only("Florida has 485 cases www.cidrap.umn.edu/x"))
