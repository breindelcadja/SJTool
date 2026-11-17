"""
Unit tests. Run with:  python -m pytest -q

Two kinds of tests:
  * REAL DATA tests read data/bluesky_results_1.csv (the dengue sample).
  * TIER tests need web pages. Tests must not use the network, so they use
    FakeBackend with SYNTHETIC pages written for the test. Those pages are
    test fixtures, not evidence, and never appear in any output file.
"""

import csv
import os
from datetime import datetime, timedelta, timezone

import pytest

from bsky_verifier import load_config, run_pipeline, verify_solutions
from bsky_verifier.citation_checks import is_primary, text_on_page
from bsky_verifier.claims import RuleBasedSpotter, best_claim, get_spotter
from bsky_verifier.fetcher import CapReached, CappedFetcher, FetchResult, UsageLedger, classify_response
from bsky_verifier.ingest import extract_links, is_link_only, load_csv, parse_timestamp
from bsky_verifier.pipeline import load_labels
from bsky_verifier.source_checks import accounts_with_same_text, max_posts_in_window
from bsky_verifier.tiers import traction_thresholds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "bluesky_results_1.csv")
LABELS = os.path.join(ROOT, "data", "solution_labels_dengue.csv")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class FakeBackend:
    """Serves synthetic pages by URL. Unknown URLs return 404."""

    def __init__(self, pages=None, failures=None):
        self.pages = pages or {}
        self.failures = failures or {}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url in self.failures:
            return FetchResult(url, False, None, None, "", self.failures[url])
        if url in self.pages:
            body = "<html><body><p>" + self.pages[url] + "</p></body></html>"
            return classify_response(url, 200, url, "text/html", body + " " * 0)
        return classify_response(url, 404, url, "text/html", "")


LONG = " Background text about mosquito control and public health in Florida." * 6


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c["paths"]["usage_ledger_path"] = str(tmp_path / "ledger.json")
    # The OpenAlex search is ON in config.yaml, but tests must not use the
    # network. Tests that exercise it switch it on and pass a fake backend.
    c["evidence_search"]["enabled"] = False
    return c


@pytest.fixture(scope="module")
def sample():
    return load_csv(SAMPLE)


@pytest.fixture(scope="module")
def rows():
    with open(SAMPLE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def by_author(posts, handle):
    return [p for p in posts if p.author == handle]


def mk(author, text, likes=0, reposts=0, replies=0, when="2026-11-01T12:00:00Z", pid=None):
    pid = pid or f"{author}-{abs(hash(text)) % 10**8}"
    return {"created_at": when, "author": author, "text": text, "likes": likes,
            "reposts": reposts, "replies": replies,
            "url": f"https://bsky.app/profile/{author}/post/{pid}"}


def tier_of(report, key=None):
    logs = report["audit_log"]
    return (next(a for a in logs if a["solution_id"] == key) if key else logs[0])["tier"]


# --------------------------------------------------------------------------
# Ingest: the messy parts of the real sample
# --------------------------------------------------------------------------

def test_sample_loads_and_drops_exact_duplicates(sample):
    posts, dropped = sample
    dup = [d for d in dropped if "duplicate" in d["reason"]]
    empty = [d for d in dropped if "empty" in d["reason"]]
    assert len(dup) == 5          # g1-globo x2, wiki-vu, cna-poster, bigearthdata
    assert len(empty) == 1        # livcolemanfl posted no text
    assert len(posts) == 94
    assert len({p.url for p in posts}) == 94


@pytest.mark.parametrize("raw", [
    "2026-11-03T13:00:49.148Z", "2026-11-03T01:45:05.943592Z",
    "2026-11-02T23:20:15.285442+00:00", "2026-10-31T15:18:05-03:00",
    "2026-11-02T11:56:09.000Z",
])
def test_mixed_timestamp_formats_parse_to_utc(raw):
    dt = parse_timestamp(raw)
    assert dt is not None and dt.utcoffset() == timedelta(0)


def test_offset_timestamp_converted():
    assert parse_timestamp("2026-10-31T15:18:05-03:00").hour == 18


def test_every_sample_timestamp_parses(sample):
    assert all(p.created_at is not None for p in sample[0])


def test_truncated_link_is_marked_not_fetchable():
    (link,) = extract_links("totals jump to 485 www.cidrap.umn.edu/dengue/60-ne...")
    assert link.truncated and link.url is None and link.domain == "www.cidrap.umn.edu"


def test_full_and_short_links():
    full = extract_links("see https://www.npr.org/2026/10/30/nx-s1-6020514/florida #x")[0]
    assert not full.truncated and full.url.startswith("https://www.npr.org/")
    short = extract_links("Best of: cases double. bit.ly/3W48eNn")[0]
    assert short.shortener and short.url == "https://bit.ly/3W48eNn"


def test_name_with_dot_is_not_a_link():
    assert extract_links('More on "Dengue control" at BigEarthData.ai') == []


def test_link_only_posts_detected(sample):
    posts, _ = sample
    crof = [p for p in by_author(posts, "crof.bsky.social") if p.text.startswith("www.cidrap")]
    assert crof and is_link_only(crof[0].text)
    assert not is_link_only("The city of St. Petersburg filed an injunction.")


def test_non_english_detected(sample):
    posts, _ = sample
    assert all(p.language == "pt" for p in by_author(posts, "g1-globo.bsky.social"))
    assert by_author(posts, "namastah.bsky.social")[0].language == "es"
    assert by_author(posts, "rightardia.bsky.social")[0].language == "en"


# --------------------------------------------------------------------------
# Claim spotting
# --------------------------------------------------------------------------

def test_rule_based_spotter_scores_facts_above_chatter():
    r = RuleBasedSpotter()
    fact, chat = r.score_sentences([
        "The city of St. Petersburg filed an injunction against a local tire shop this week.",
        "like. my bones just hurt hope I don't have dengue fever lul"])
    assert fact > 0.35 > chat


def test_spotter_falls_back_when_model_missing():
    spotter, note = get_spotter("/no/such/model.joblib")
    assert spotter.name == "rule_based" and "not found" in note


def test_key_claim_does_not_split_on_st_abbreviation():
    claim, _ = best_claim("The city of St. Petersburg filed an injunction against a "
                          "local tire shop this week. Residents are worried.", RuleBasedSpotter())
    assert claim.startswith("The city of St. Petersburg filed an injunction")


def test_claimbuster_svm_if_available():
    spotter, _ = get_spotter(os.path.join(ROOT, "models", "claimbuster_svm.joblib"))
    if spotter.name != "claimbuster_svm":
        pytest.skip("model file not built; run scripts/train_claimbuster_svm.py")
    fact, chat = spotter.score_sentences([
        "Florida health officials have confirmed 485 locally acquired dengue fever cases this year.",
        "Not now, dengue"])
    assert fact > chat


# --------------------------------------------------------------------------
# Source checks (duplicates, bots, account details)
# --------------------------------------------------------------------------

def test_same_headline_from_three_or_more_accounts(sample):
    posts, _ = sample
    p = by_author(posts, "spennington.bsky.social")[0]     # "Bangladesh death toll ..."
    authors = accounts_with_same_text(p, posts, 0.8)
    assert len(authors) >= 3
    assert {"srauer20.bsky.social", "ccrider77-48.bsky.social"} <= set(authors)


def test_paraphrases_of_485_cases_are_not_called_copies(sample):
    # Many accounts report "485 locally acquired cases" in their OWN words.
    # That is one news event, not copy-paste, so the same-text flag stays off.
    posts, _ = sample
    p = by_author(posts, "docrodi.bsky.social")[0]
    assert len(accounts_with_same_text(p, posts, 0.8)) < 3


def test_duplicate_headline_solution_is_flagged(cfg, rows):
    labels = {r["url"]: {"is_solution": "true", "solution_key": "bd", "summary": "", "note": ""}
              for r in rows if "Bangladesh death toll" in r["text"]}
    cfg["fetching"]["enabled"] = False
    report = run_pipeline(rows, "dengue", config=cfg, labels=labels, context_posts=rows)
    a = report["audit_log"][0]
    assert a["tier"] == "Flagged" and not a["shown_to_journalists"]
    assert report["tiles"] == []                      # Flagged never becomes a tile


def test_posting_rate_window():
    cfg_posts = [mk("spam.bsky.social", f"dengue cure number {i} is here today friends",
                    when=f"2026-11-01T12:{i:02d}:00Z") for i in range(11)]
    from bsky_verifier.ingest import rows_to_posts
    posts, _ = rows_to_posts(cfg_posts)
    assert max_posts_in_window("spam.bsky.social", posts, timedelta(hours=1)) == 11


def test_more_than_ten_posts_per_hour_is_flagged(cfg):
    burst = [mk("spam.bsky.social", f"Fogging round {i} cut mosquito counts in district {i}",
                when=f"2026-11-01T12:{i:02d}:00Z") for i in range(11)]
    cfg["fetching"]["enabled"] = False
    report = run_pipeline(burst[:1], "dengue", config=cfg, context_posts=burst)
    assert tier_of(report) == "Flagged"
    assert "11 times within one hour" in report["audit_log"][0]["reason"]


def test_new_account_flagged_and_unknown_never_penalized(cfg):
    post = mk("newbie.bsky.social", "County crews removed 4,000 old tires to cut mosquito breeding.")
    cfg["fetching"]["enabled"] = False
    # No account details: noted, not penalized.
    r1 = run_pipeline([post], "dengue", config=cfg)
    assert tier_of(r1) == "Early signal"
    ages = [c for c in r1["audit_log"][0]["checks"] if c["check_name"] == "source.account_age"]
    assert ages[0]["outcome"] == "unknown" and "Not penalized" in ages[0]["evidence"]
    # 5-day-old account: flagged.
    created = datetime(2026, 10, 27, tzinfo=timezone.utc)
    r2 = run_pipeline([post], "dengue", config=cfg, profiles={"newbie.bsky.social": created})
    assert tier_of(r2) == "Flagged"


def test_bridged_and_aggregator_accounts_are_context_only(cfg, rows):
    cfg["fetching"]["enabled"] = False
    row = next(r for r in rows if r["author"].endswith(".brid.gy"))
    r = run_pipeline([row], "dengue", config=cfg, context_posts=rows)
    names = {c["check_name"]: c["outcome"] for c in r["audit_log"][0]["checks"]}
    assert names["source.bridged_account"] == "info"
    assert tier_of(r) != "Flagged"


# --------------------------------------------------------------------------
# Citation checks
# --------------------------------------------------------------------------

def test_primary_labels():
    c = {"count_country_gov_edu_as_primary": True}
    assert is_primary("www.cdc.gov", c) and is_primary("www.cidrap.umn.edu", c)
    assert is_primary("www.floridahealth.gov", c) and is_primary("www.nea.gov.sg", c)
    assert not is_primary("www.tampabay.com", c) and not is_primary("patimes.org", c)


def test_text_on_page_requires_numbers_and_words():
    c = {"min_keyword_overlap": 0.6}
    claim = "Florida recorded 60 more dengue cases, bringing the state total to 485."
    ok, _, _ = text_on_page(claim, claim, "Florida recorded 60 more locally acquired dengue cases "
                            "this week bringing the state total to 485.", c)
    bad, why, _ = text_on_page(claim, claim, "Florida recorded more dengue cases bringing the "
                               "state total to 425.", c)
    assert ok and not bad and "485" in why


def test_quoted_text_must_appear():
    c = {"min_keyword_overlap": 0.6}
    post = "“Of the 485 cases, 385 confirmed cases come from Hillsborough County” link"
    assert text_on_page(post, "", "... Of the 485 cases, 385 confirmed cases come from "
                        "Hillsborough County, officials said.", c)[0]
    assert not text_on_page(post, "", "Hillsborough County has most cases.", c)[0]


@pytest.mark.parametrize("status,ctype,body,expect", [
    (404, "text/html", "", "404"),
    (402, "text/html", "", "paywall"),
    (403, "text/html", "", "403"),
    (200, "application/pdf", "%PDF", "not a web page"),
    (200, "text/html", "<html><body>hi</body></html>", "almost no readable text"),
])
def test_failure_reasons_are_recorded(status, ctype, body, expect):
    r = classify_response("https://x.test/a", status, "https://x.test/a", ctype, body)
    assert not r.ok and expect in r.failure


def test_timeout_reason_reaches_the_tile(cfg):
    url = "https://www.example-news.test/tires"
    post = mk("a.bsky.social", f"County removed 4,000 old tires to stop mosquitoes. {url}")
    r = run_pipeline([post], "dengue", config=cfg, fetch_backend=FakeBackend(failures={url: "timeout"}))
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal"
    assert tile["evidence"][0]["status"] == "did not count" and "timeout" in tile["evidence"][0]["reason"]
    assert "No verified source found" in tile["evidence_note"]


# --------------------------------------------------------------------------
# Named test cases (real post text from the sample + synthetic pages)
# --------------------------------------------------------------------------

def named(rows, labels, key):
    urls = {u for u, l in labels.items() if l["solution_key"] == key}
    return [r for r in rows if r["url"] in urls], {u: labels[u] for u in urls}


@pytest.fixture(scope="module")
def labels():
    return load_labels(LABELS)


def test_tire_shop_csv_only_is_early_signal_with_reason(cfg, rows, labels):
    """From the CSV alone the only link is truncated, so nothing verifies."""
    posts, lab = named(rows, labels, "tire-shop-injunction")
    assert len(posts) == 2
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend())
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal"
    assert "truncated" in tile["evidence"][0]["reason"]
    assert tile["author"]["handle"] in ("@rightardia.bsky.social", "@headlesshornman.bsky.social")
    assert tile["author"]["profile_url"].startswith("https://bsky.app/profile/")


def test_tire_shop_with_primary_source_is_strong_lead(cfg, rows, labels):
    """If the scraper supplied a full .gov link whose page contains the claim,
    the same post becomes a Strong lead with a coverage-gap note."""
    posts, lab = named(rows, labels, "tire-shop-injunction")
    posts = [dict(p) for p in posts]
    gov = "https://www.stpete.example.gov/news/tire-shop-injunction"
    posts[0]["text"] += " " + gov
    page = ("The city of St. Petersburg filed an injunction against a local tire shop this week. "
            "Residents in the Euclid-St. Paul area urged the city to address the dengue situation." + LONG)
    r = run_pipeline(posts, "dengue", config=cfg, labels={**lab, posts[0]["url"]: lab[posts[0]["url"]]},
                     context_posts=rows, fetch_backend=FakeBackend({gov: page}))
    tile = r["tiles"][0]
    assert tile["tier"] == "Strong lead" and tile["coverage_gap"] is True
    assert any(e["label"] == "primary" and e["status"] == "verified" for e in tile["evidence"])


def test_smash_act_one_secondary_source_is_promising(cfg, rows, labels):
    posts, lab = named(rows, labels, "smash-2-act")
    posts = [dict(p) for p in posts]
    url = "https://patimes.org/from-outbreak-to-policy"
    posts[0]["text"] = posts[0]["text"].replace("patimes.org/from-outbrea...", url)
    page = ("We wrote about available congressional policy solutions for dengue virus, including "
            "the SMASH 2.0 Act (Strengthening Mosquito Abatement for Safety and Health 2.0 Act), "
            "in PA Times." + LONG)
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend({url: page}))
    assert r["tiles"][0]["tier"] == "Promising"
    assert r["tiles"][0]["evidence"][0]["label"] == "secondary"


def test_smash_act_text_not_found(cfg, rows, labels):
    posts, lab = named(rows, labels, "smash-2-act")
    posts = [dict(p) for p in posts]
    url = "https://patimes.org/from-outbreak-to-policy"
    posts[0]["text"] = posts[0]["text"].replace("patimes.org/from-outbrea...", url)
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend({url: "An unrelated essay about city budgets." + LONG}))
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal" and tile["evidence"][0]["status"] == "did not count"
    assert "not on the page" in tile["evidence"][0]["reason"]


def test_wolbachia_no_link_is_early_signal_and_marked_aggregator(cfg, rows, labels):
    posts, lab = named(rows, labels, "wolbachia-singapore")
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend())
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal" and tile["evidence"] == []
    assert any("aggregator" in n for n in tile["notes"])


def test_wolbachia_two_independent_sources_is_strong(cfg, rows, labels):
    posts, lab = named(rows, labels, "wolbachia-singapore")
    posts = [dict(p) for p in posts]
    a, b = "https://www.straitstimes.example.com/wolbachia", "https://www.cna.example.org/wolbachia"
    posts[0]["text"] += f" {a} {b}"
    page = "Project Wolbachia now covers more than half of all households in Singapore." + LONG
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend({a: page, b: page}))
    assert r["tiles"][0]["tier"] == "Strong lead" and r["tiles"][0]["coverage_gap"] is False


def test_tax_cut_mosquito_control_is_early_signal_without_source(cfg, rows, labels):
    posts, lab = named(rows, labels, "mosquito-control-funding")
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend())
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal"
    assert tile["evidence_note"].startswith("No verified source found")


def test_garlic_is_myth_and_never_shown(cfg, rows, labels):
    posts, lab = named(rows, labels, "garlic")
    src = "https://mosquitosite.russell.wisc.edu/preventing-mosquito-bites/home-remedies/"
    page = ("Eating garlic or taking vitamin B tablets don’t work to reduce mosquito bites. "
            "Studies of garlic and vitamin B did not find evidence that these substances could "
            "reduce mosquito attraction." + LONG)
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend({src: page}))
    a = r["audit_log"][0]
    assert a["tier"] == "Myth" and a["rule"] == "R1" and not a["shown_to_journalists"]
    assert "confirmed live" in a["reason"]
    assert r["tiles"] == [] and verify_solutions(posts, "dengue", config=cfg, labels=lab,
                                                 fetch_backend=FakeBackend({src: page})) == []


# --------------------------------------------------------------------------
# Tier rules
# --------------------------------------------------------------------------

GOV = "https://health.example.gov/report"
CLAIM = "County crews removed 4,000 old tires and mosquito counts fell 60% in six weeks."


def test_flags_never_lower_strong_evidence(cfg):
    burst = [mk("busy.bsky.social", f"{CLAIM} {GOV}", when=f"2026-11-01T12:{i:02d}:00Z", pid=str(i))
             for i in range(11)]
    r = run_pipeline(burst[:1], "dengue", config=cfg, context_posts=burst,
                     fetch_backend=FakeBackend({GOV: CLAIM + LONG}))
    tile = r["tiles"][0]
    assert tile["tier"] == "Strong lead"
    assert any("within one hour" in n for n in tile["notes"])      # flag still disclosed


def test_number_mismatch_caps_tier(cfg):
    post = mk("a.bsky.social", f"{CLAIM} {GOV}", likes=500, reposts=200)
    page = "County crews removed old tires and mosquito counts fell 35% in six weeks." + LONG
    r = run_pipeline([post], "dengue", config=cfg, fetch_backend=FakeBackend({GOV: page}))
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal" and tile["rule"] == "R4"   # capped despite huge traction
    assert "Capped" in tile["tier_reason"]


def test_high_traction_without_data_becomes_promising_pitch(cfg, rows):
    post = mk("organizer.bsky.social", "Our block's weekly tire and gutter cleanup is working, "
              "neighbors report far fewer mosquitoes.", likes=40, reposts=20, replies=6)
    cfg["fetching"]["enabled"] = False
    r = run_pipeline([post], "dengue", config=cfg, context_posts=rows + [post])
    tile = r["tiles"][0]
    assert tile["tier"] == "Promising" and tile["rule"] == "R5b"
    assert "@organizer.bsky.social" == tile["author"]["handle"]
    assert tile["evidence_note"].startswith("No verified source found")


def test_traction_threshold_is_relative_with_a_floor(sample, cfg):
    t = traction_thresholds(sample[0], cfg["traction"])
    assert t["high"] >= cfg["traction"]["high_traction_min_engagement"]
    top = [p for p in sample[0] if p.engagement >= t["high"]]
    assert 0 < len(top) <= 0.12 * len(sample[0])


# --------------------------------------------------------------------------
# Usage caps
# --------------------------------------------------------------------------

def test_per_run_cap_stops_gracefully(cfg):
    cfg["caps"]["max_page_fetches_per_run"] = 2
    posts = [mk(f"u{i}.bsky.social", f"City {i} cut mosquito breeding sites by {i+1}0 percent. "
                f"https://news.test/story{i}", pid=str(i)) for i in range(4)]
    be = FakeBackend({f"https://news.test/story{i}": f"City {i} cut mosquito breeding sites by "
                      f"{i+1}0 percent." + LONG for i in range(4)})
    r = run_pipeline(posts, "dengue", config=cfg, fetch_backend=be)
    assert len(be.calls) == 2
    assert r["usage"]["stopped_early"] and "per-run" in r["usage"]["cap_events"][0]
    skipped = [c for a in r["audit_log"] for c in a["checks"] if c["outcome"] == "skipped"]
    assert len(skipped) == 2 and len(r["tiles"]) == 4       # run still finishes


def test_daily_cap_persists_across_runs(tmp_path):
    ledger_path = str(tmp_path / "l.json")
    caps = {"max_page_fetches_per_run": 10, "max_page_fetches_per_day": 3,
            "max_page_fetches_per_month": 100}
    f1 = CappedFetcher(FakeBackend(), caps, UsageLedger(ledger_path))
    for i in range(3):
        f1.get(f"https://a.test/{i}")
    f2 = CappedFetcher(FakeBackend(), caps, UsageLedger(ledger_path))   # "next run"
    with pytest.raises(CapReached, match="daily"):
        f2.get("https://a.test/new")


# --------------------------------------------------------------------------
# The backend function and the page
# --------------------------------------------------------------------------

def test_verify_solutions_returns_at_most_five_visible_tiles(cfg, rows, labels):
    cfg["fetching"]["enabled"] = False
    tiles = verify_solutions(rows, "dengue", config=cfg, labels=labels, context_posts=rows)
    assert 3 <= len(tiles) <= 5
    assert all(t["tier"] in ("Strong lead", "Promising", "Early signal") for t in tiles)
    for t in tiles:
        for field in ("solution_summary", "tier", "tier_reason", "evidence", "bluesky_post", "author"):
            assert field in t
    assert not any("garlic" in t["solution_summary"].lower() for t in tiles)


def test_html_hides_myth_and_flagged_and_escapes(cfg, rows, labels):
    from bsky_verifier.render import render_page
    cfg["fetching"]["enabled"] = False
    html = render_page(run_pipeline(rows, "dengue <script>", config=cfg, labels=labels,
                                    context_posts=rows))
    assert "garlic" not in html.lower() and "Myth" not in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_no_credentials_anywhere_in_project():
    import re
    # Bluesky app passwords look like xxxx-xxxx-xxxx-xxxx.
    pat = re.compile(r"(BSKY_APP_PASSWORD\s*=\s*\S+|\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b)", re.I)
    for folder, _, files in os.walk(ROOT):
        if any(x in folder for x in (".git", "__pycache__", ".pytest_cache", "models")):
            continue
        for name in files:
            if name.endswith((".py", ".md", ".yaml", ".txt", ".json", ".html")) and name != "test_verifier.py":
                with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as f:
                    assert not pat.search(f.read()), f"possible credential in {name}"


# --------------------------------------------------------------------------
# Merged SJTool evidence code (evidence_scoring / evidence_sources / find_evidence)
# --------------------------------------------------------------------------

from bsky_verifier.academic_evidence import AcademicEvidenceCheck, EvidenceSource, clean_solution_text
from bsky_verifier.evidence_scoring import calculate_score, determine_status
from bsky_verifier.evidence_sources import classify_domain


class FakeOpenAlex:
    def __init__(self, works=None, boom=False):
        self.works, self.boom, self.queries = works or [], boom, []

    def search(self, text, max_results=5):
        self.queries.append(text)
        if self.boom:
            raise RuntimeError("rate limited")
        return [EvidenceSource(w, f"https://openalex.org/{i}", "peer_reviewed", "x", 0.8)
                for i, w in enumerate(self.works)]


def test_sjtool_scoring_unchanged():
    s = [EvidenceSource("a", "u", "government", "", 1.0), EvidenceSource("b", "u", "journalism", "", 1.0)]
    assert calculate_score(s) == 0.8 and calculate_score([]) == 0.0
    assert [determine_status(x) for x in (0.9, 0.6, 0.3, 0.1)] == [
        "SUPPORTED", "PROMISING", "ANECDOTAL", "INSUFFICIENT_DATA"]


def test_domain_classification():
    assert classify_domain("www.cdc.gov") == "government"
    assert classify_domain("www.cidrap.umn.edu") == "university"
    assert classify_domain("www.tampabay.com") == "journalism"


def test_clean_text_removes_links_handles_hashtags():
    assert clean_solution_text("Tires @city #dengue https://x.test/a removed") == "Tires removed"


def test_evidence_search_can_be_switched_off(cfg, rows, labels):
    posts, lab = named(rows, labels, "tire-shop-injunction")
    fake = FakeOpenAlex(["A study"])
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend(), evidence_backend=fake)
    assert fake.queries == [] and r["usage"]["api_calls_this_run"] == 0
    assert any("related-research search is off" in n for n in r["run_notes"])
    assert load_config()["evidence_search"]["enabled"] is True      # shipped default
    assert r["tiles"][0]["related_research"] is None


def test_related_research_is_shown_but_never_changes_the_tier(cfg, rows, labels):
    cfg["evidence_search"]["enabled"] = True
    posts, lab = named(rows, labels, "mosquito-control-funding")
    fake = FakeOpenAlex(["Mosquito control budgets", "Vector control funding"])
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend(), evidence_backend=fake)
    tile = r["tiles"][0]
    assert tile["tier"] == "Early signal"                       # not lifted by topic matches
    assert len(tile["related_research"]["works"]) == 2
    assert "Not checked against this claim" in tile["related_research"]["note"]
    assert tile["evidence_note"].startswith("No verified source found")
    assert "http" not in fake.queries[0] and "#" not in fake.queries[0]
    assert r["usage"]["api_calls_this_run"] == 1


def test_evidence_search_failure_is_recorded_not_fatal(cfg, rows, labels):
    cfg["evidence_search"]["enabled"] = True
    posts, lab = named(rows, labels, "wolbachia-singapore")
    r = run_pipeline(posts, "dengue", config=cfg, labels=lab, context_posts=rows,
                     fetch_backend=FakeBackend(), evidence_backend=FakeOpenAlex(boom=True))
    checks = [c for c in r["audit_log"][0]["checks"] if c["check_name"] == "evidence.related_research"]
    assert checks[0]["outcome"] == "unknown" and "RuntimeError" in checks[0]["evidence"]
    assert r["tiles"][0]["related_research"] is None


def test_api_call_cap_stops_gracefully(cfg, rows):
    cfg["evidence_search"]["enabled"] = True
    cfg["caps"]["max_api_calls_per_run"] = 1
    posts = [mk(f"u{i}.bsky.social", f"City {i} cut mosquito breeding sites by {i+1}0 percent this year.",
                pid=str(i)) for i in range(3)]
    fake = FakeOpenAlex(["A study"])
    r = run_pipeline(posts, "dengue", config=cfg, context_posts=posts, fetch_backend=FakeBackend(),
                     evidence_backend=fake)
    assert len(fake.queries) == 1
    assert r["usage"]["stopped_early"] and "API-call cap" in r["usage"]["cap_events"][0]


def test_verified_sources_get_an_informational_score(cfg):
    url = "https://www.cdc.gov/dengue/x"
    post = mk("a.bsky.social", f"{CLAIM} {url}")
    r = run_pipeline([post], "dengue", config=cfg, fetch_backend=FakeBackend({url: CLAIM + LONG}))
    t = r["tiles"][0]
    assert t["tier"] == "Strong lead"
    assert t["evidence_score"]["status"] == "SUPPORTED" and "does not set the tier" in t["evidence_score"]["basis"]
