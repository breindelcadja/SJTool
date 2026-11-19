"""End-to-end test with no network and no API keys. Fakes Bluesky, Claude and
ClaimBuster, but runs the REAL classify(), group_posts(), draft_pitch(), Bluesky
verifier, database and API code.
Run:  python test_pipeline.py"""
import os
import tempfile
from types import SimpleNamespace

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ["CLAIMBUSTER_API_KEY"] = "test"
os.environ["CLAIMBUSTER_DELAY"] = "0"
os.environ["VERIFIER_OFFLINE"] = "1"

from fastapi.testclient import TestClient

import api
import db
import factcheck
import make_pitches
import pipeline

POSTS = [
    {"created_at": "2026-11-01T10:00:00Z", "author": "a.bsky.social", "likes": 1,
     "reposts": 0, "replies": 0, "url": "https://bsky.app/profile/a/post/1",
     "text": "The county launched a new mosquito program this week.",
     "link_title": "", "link_description": ""},
    {"created_at": "2026-11-02T10:00:00Z", "author": "b.bsky.social", "likes": 0,
     "reposts": 0, "replies": 0, "url": "https://bsky.app/profile/b/post/2",
     "text": "Cases are up again. Terrible.", "link_title": "", "link_description": ""},
    {"created_at": "2026-11-03T10:00:00Z", "author": "c.bsky.social", "likes": 0,
     "reposts": 0, "replies": 0, "url": "https://bsky.app/profile/c/post/3",
     "text": "The county launched a new mosquito program this week.",  # fails once
     "link_title": "", "link_description": ""},
]


class FakeClaude:
    """Answers the way the real tools are shaped, so classify() and
    draft_pitch() run unchanged."""

    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        import anthropic
        tool = kw["tools"][0]["name"]
        text = kw["messages"][0]["content"]
        if tool == "record_groups":
            # Every post given is about the same county program.
            n = text.count("<post number=")
            out = {"groups": [{"post_numbers": list(range(1, n + 1)),
                               "existing_pitch_id": "", "reason": "Same program."}]}
        elif tool == "record_label":
            if "program" in text:
                out = {"label": "solution", "confidence": "high", "reason": "Names a program."}
            else:
                out = {"label": "not_solution", "confidence": "high", "reason": "Case counts."}
        else:
            out = {f: "x" for f in make_pitches.TEXT_FIELDS}
            out["headline"] = "County mosquito program: does it work?"
            out.update({f: ["a", "b"] for f in make_pitches.LIST_FIELDS})
            out["to_verify"] = ["The county launched a mosquito program this week.",
                                "Not stated in post"]
            out.update({f"check_{k}": True for k in make_pitches.USEFULNESS_CHECKS})
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=tool, input=out)])


# --- fakes for the network edges
pipeline.make_client = FakeClaude
pipeline.login = lambda h, p: "token"
pipeline.search_posts = lambda *a, **k: iter(POSTS)


class FakeResp:
    ok, status_code, headers = True, 200, {}
    def __init__(self, data): self._d = data
    def json(self): return self._d


def fake_request(method, url, **kw):
    if "score/text" in url:
        assert kw["json"]["input_text"]
        return FakeResp({"results": [{"index": 0, "score": 0.72, "text": kw["json"]["input_text"]}]})
    if "fact_matcher" in url:
        return FakeResp({"justification": [{"claim": "similar claim", "truth_rating": "Mostly True"}]})
    raise AssertionError(url)


factcheck.requests.request = fake_request

with TestClient(api.app) as client:
    r = client.post("/runs", json={"topic": "dengue", "query": '"dengue response"'})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    run = client.get(f"/runs/{run_id}").json()
    print("status:", run["status"], "| counts:", run["counts"])
    assert run["status"] == "done", run
    assert run["counts"]["posts"] == 3 and run["counts"]["solutions"] == 2
    assert run["counts"]["verified"] == 2 and run["counts"]["hidden"] == 0, run
    # The two posts about the same program become ONE pitch with both as sources.
    assert run["counts"]["pitches"] == 1 and run["counts"]["factchecked"] == 1

    pitches = client.get(f"/runs/{run_id}/pitches").json()
    assert len(pitches) == 1 and pitches[0]["usefulness"] == "high"
    assert len(pitches[0]["source_urls"]) == 2 and pitches[0]["tier"], pitches[0]
    assert pitches[0]["factcheck_summary"]["claims"] == 1          # "Not stated" dropped
    assert pitches[0]["factcheck_summary"]["check_worthy"] == 1
    assert pitches[0]["factcheck_summary"]["with_prior_factchecks"] == 1

    one = client.get(f"/runs/{run_id}/pitches/{pitches[0]['pitch_id']}").json()
    assert one["factchecks"][0]["score"] == 0.72
    assert one["verification"]["tier"] == pitches[0]["tier"]
    print("verifier tier:", one["verification"]["tier"], "-", one["verification"]["tier_reason"])
    assert client.get(f"/runs/{run_id}/pitches/nope").status_code == 404
    sols = client.get(f"/runs/{run_id}/posts", params={"label": "solution"}).json()
    assert len(sols) == 2 and all(p["pitch_url"] and p["tier"] for p in sols), sols

    # Resume does no duplicate work
    before = client.get(f"/runs/{run_id}").json()["counts"]
    client.post(f"/runs/{run_id}/resume", json={})
    assert client.get(f"/runs/{run_id}").json()["counts"] == before

    # Export feeds check_accuracy.py
    folder = client.post(f"/runs/{run_id}/export").json()["folder"]
    assert os.path.exists(os.path.join(folder, "solutions_results.csv"))
    assert client.get("/runs/999").status_code == 404

    # A ClaimBuster outage marks the run partial instead of crashing it
    def boom(method, url, **kw): raise factcheck.requests.ConnectionError("down")
    pipeline.search_posts = lambda *a, **k: iter(POSTS[:1])
    factcheck.requests.request = boom          # outage starts BEFORE the run
    run2 = client.post("/runs", json={"topic": "dengue"}).json()["run_id"]
    info = client.get(f"/runs/{run2}").json()
    print("run2 status:", info["status"], info["stage_results"].get("factcheck"))
    assert info["status"] == "partial" and info["counts"]["pitches"] == 1
    assert info["counts"]["factchecked"] == 0   # nothing marked done, so it can be retried

    # Service comes back: resume finishes the job without redoing earlier stages
    factcheck.requests.request = fake_request
    client.post(f"/runs/{run2}/resume", json={})
    info = client.get(f"/runs/{run2}").json()
    assert info["status"] == "done" and info["counts"]["factchecked"] == 1, info

    # A post the verifier calls a Myth (or Flagged) gets no pitch and stays hidden.
    import verify
    real_verify = verify.verify_posts
    verify.verify_posts = lambda topic, sols, all_posts: (
        [{"url": p["url"], "solution_id": "s1", "tier": "Myth",
          "tier_reason": "Contradicted by a primary source.", "tile": None} for p in sols], {})
    run3 = client.post("/runs", json={"topic": "dengue"}).json()["run_id"]
    info = client.get(f"/runs/{run3}").json()
    assert info["counts"]["hidden"] == 1 and info["counts"]["pitches"] == 0, info
    verify.verify_posts = real_verify

print("\nAll checks passed.")
