"""ClaimBuster client (https://idir.uta.edu/claimbuster/api).

What ClaimBuster does, and does not do:
  * score/text: rates each sentence 0 to 1 for how check-worthy it is. High
    means "a factual claim worth checking", low means opinion or chit-chat.
    It does NOT say whether a claim is true.
  * fact_matcher: finds claims that professional fact-checkers have already
    checked and that are similar to yours.

So this stage is triage. It tells a reporter which claims to check first and
whether someone has already checked something similar. It cannot confirm that a
solution works. That still takes a human, and the data in each pitch's
data_needed field.
"""
import time
from urllib.parse import quote

import requests

import config

BASE = "https://idir.uta.edu/claimbuster/api/v2"
NOT_STATED = "Not stated in post"


class ClaimBusterError(Exception):
    pass


def _request(method, path, **kwargs):
    key = config.claimbuster_key()
    if not key:
        raise ClaimBusterError("CLAIMBUSTER_API_KEY is not set")
    for attempt in range(3):
        try:
            r = requests.request(method, f"{BASE}/{path}", headers={"x-api-key": key},
                                 timeout=30, **kwargs)
        except requests.RequestException as e:
            raise ClaimBusterError(f"network error: {e}") from e
        if r.status_code == 429 and attempt < 2:
            time.sleep(int(r.headers.get("Retry-After", 10)))
            continue
        if r.status_code in (401, 403):
            raise ClaimBusterError("ClaimBuster rejected the API key")
        if not r.ok:
            raise ClaimBusterError(f"HTTP {r.status_code} from ClaimBuster")
        try:
            return r.json()
        except ValueError as e:
            raise ClaimBusterError("ClaimBuster returned something that isn't JSON") from e
    raise ClaimBusterError("rate limited")


def score_claim(text):
    """Return (highest sentence score, per-sentence scores)."""
    data = _request("POST", "score/text/", json={"input_text": text})
    sentences = [{"text": r.get("text", ""), "score": float(r["score"])}
                 for r in data.get("results", []) if "score" in r]
    top = max((s["score"] for s in sentences), default=None)
    return top, sentences


def find_prior_factchecks(text):
    """Return fact-checks of similar claims. The response shape isn't documented
    in detail, so keep whatever list comes back and don't depend on its fields."""
    data = _request("GET", f"query/fact_matcher/{quote(text[:500], safe='')}")
    matches = data.get("justification") or data.get("results") or []
    return matches if isinstance(matches, list) else []


def claims_from_pitch(pitch):
    """The claims worth running through ClaimBuster: what the pitch says a
    reporter must verify first. They are written in English even when the post
    wasn't, which suits ClaimBuster (trained on English)."""
    seen, claims = set(), []
    for c in pitch.get("to_verify", []):
        c = c.strip()
        if c and c != NOT_STATED and c.lower() not in seen:
            seen.add(c.lower())
            claims.append(c)
    return claims


def check_claims(claims, delay=None):
    delay = config.CLAIMBUSTER_DELAY if delay is None else delay
    rows = []
    for claim in claims:
        score, sentences = score_claim(claim)
        time.sleep(delay)
        matches = find_prior_factchecks(claim)
        time.sleep(delay)
        rows.append({"claim": claim, "score": score,
                     "sentence_scores": sentences, "matches": matches})
    return rows


def summarize(rows):
    return {
        "claims": len(rows),
        "check_worthy": sum(1 for r in rows
                            if r["score"] is not None and r["score"] >= config.CHECKWORTHY_THRESHOLD),
        "with_prior_factchecks": sum(1 for r in rows if r["matches"]),
        "note": "ClaimBuster scores how check-worthy a claim is. It does not say whether it is true.",
    }
