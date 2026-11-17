"""Claim scoring: which sentences in a post are worth checking.

The score only decides what to check first. It never sets a trust tier.
"""

import re
import time

from .config import (CLAIMBUSTER_URL, PAUSE_SECONDS, RATE_LIMIT_RETRIES,
                     RATE_LIMIT_WAIT_SECONDS)


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def best_sentence(scored):
    """scored is [(sentence, score), ...]. Returns the highest-scoring pair."""
    return max(scored, key=lambda pair: pair[1]) if scored else ("", 0.0)


def _fake_score(sentence):
    score = 0.15
    lowered = sentence.lower()
    if re.search(r"\d|%", sentence):
        score += 0.3
    if any(w in lowered for w in ("reduced", "cut", "dropped", "prevent", "caused",
                                  "study", "percent", "fewer", "increase", "stopped")):
        score += 0.25
    if len(sentence.split()) >= 8:
        score += 0.1
    if "?" in sentence or lowered.startswith(("i feel", "i think", "ugh", "lol")):
        score -= 0.2
    return max(0.0, min(1.0, round(score, 2)))


class OfflineClaimScorer:
    """FAKE scores so the pipeline runs without a key. Not ClaimBuster."""

    label = "OFFLINE fake claim scores (not ClaimBuster)"

    def score(self, text):
        return [(s, _fake_score(s)) for s in split_sentences(text)]


class ClaimBusterScorer:
    """Scores sentences with the ClaimBuster API.

    NOTE: the endpoint and response shape follow ClaimBuster's published docs as
    understood, but were NOT tested. Check the ClaimBuster API docs page
    and adjust score() if the response differs.
    """

    label = "ClaimBuster API"

    def __init__(self, api_key):
        self.api_key = api_key

    def score(self, text):
        import requests  # imported here so offline mode needs no install

        wait = RATE_LIMIT_WAIT_SECONDS
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            response = requests.post(
                CLAIMBUSTER_URL,
                json={"input_text": text},
                headers={"x-api-key": self.api_key},
                timeout=30,
            )
            if response.status_code != 429:
                break
            if attempt == RATE_LIMIT_RETRIES:
                raise RuntimeError(
                    "ClaimBuster says too many requests (HTTP 429). Wait a while, "
                    "lower --max-scoring-calls, or raise PAUSE_SECONDS in config.py.")
            time.sleep(wait)
            wait *= 2
        response.raise_for_status()
        data = response.json()
        scored = [(r.get("text", ""), float(r["score"]))
                  for r in data.get("results", []) if "score" in r]
        if not scored:
            raise RuntimeError(
                "Unexpected ClaimBuster response (keys: %s). "
                "Check the API docs and adjust ClaimBusterScorer.score()." % list(data)
            )
        time.sleep(PAUSE_SECONDS)
        return scored
