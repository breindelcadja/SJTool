"""
Claim spotting: which sentence in a post is a checkable factual claim?

We use this to pick the sentence we try to find on cited pages, and to tell
the journalist when a post has no checkable claim at all (pure opinion or
anecdote).

Two interchangeable implementations behind one interface:

  ClaimBusterSVMSpotter  A TF-IDF + linear SVM trained on ClaimBuster's own
                         labelled sentences (see scripts/train_claimbuster_svm.py).
                         This is NOT the original ClaimBuster transformer: that
                         model ships no trained weights and needs TensorFlow 2.2,
                         which does not install on modern Python.
  RuleBasedSpotter       No ML. Scores numbers, named agencies, laws/bills,
                         dates and statistics words. Always available.

`get_spotter()` returns the SVM if its model file loads, else the rules.
Add an API-backed spotter later (e.g. the ClaimBuster web API) by writing a
class with the same `score_sentences` method.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

from .ingest import text_without_links


class ClaimSpotter(Protocol):
    name: str

    def score_sentences(self, sentences: list[str]) -> list[float]:
        """Return a check-worthiness score in [0, 1] for each sentence."""
        ...


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text_without_links(text)).strip()
    # Protect common abbreviations ("St. Petersburg", "Dept. of Health") so
    # they do not end a sentence.
    text = re.sub(r"\b(St|Dr|Mr|Mrs|Ms|Dept|U\.S|vs|No|Sen|Rep|Gov)\.\s", r"\1.§", text)
    # Also split on "->" and "|", which aggregator accounts use to bolt a
    # source name and a promo onto a headline.
    text = re.sub(r"\s*(->|\|)\s*", ". ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", text)
    return [p.replace("§", " ").strip() for p in parts if len(p.strip()) > 3]


# --------------------------------------------------------------------------
# Rule-based fallback
# --------------------------------------------------------------------------

RULES = [
    # (name, regex, weight)
    ("number", r"\b\d[\d,.]*\b", 0.30),
    ("percent_or_stat", r"\b\d+(\.\d+)?\s?%|\bper ?cent\b|\bcases?\b|\bdeaths?\b|"
                        r"\brate\b|\bhalf\b|\bmajority\b|\bdouble[sd]?\b", 0.20),
    ("agency", r"\b(CDC|WHO|NIH|FDA|EPA|NEA|HHS|Dept\.? of Health|Department of "
               r"Health|health (department|ministry|officials)|Surgeon General|"
               r"county|city of|ministry|government|governor)\b", 0.25),
    ("law_or_policy", r"\b(Act|bill|law|ordinance|injunction|amendment|ballot|"
                      r"legislation|funding|tax|regulation|state of emergency|"
                      r"filed|program(me)?|project)\b", 0.25),
    ("date", r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b|"
             r"\b(19|20)\d\d\b|\b(this|last|past) (week|month|year)\b|"
             r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", 0.15),
]
OPINION_RE = re.compile(r"\b(I think|I hope|I feel|lol|lul|wonder|hope I)\b", re.I)


class RuleBasedSpotter:
    name = "rule_based"

    def explain(self, sentence: str) -> list[str]:
        return [n for n, rx, _ in RULES if re.search(rx, sentence, re.I)]

    def score_sentences(self, sentences: list[str]) -> list[float]:
        scores = []
        for s in sentences:
            score = sum(w for _, rx, w in RULES if re.search(rx, s, re.I))
            if OPINION_RE.search(s):
                score -= 0.2
            scores.append(max(0.0, min(1.0, score)))
        return scores


# --------------------------------------------------------------------------
# ClaimBuster-data SVM
# --------------------------------------------------------------------------

class ClaimBusterSVMSpotter:
    name = "claimbuster_svm"

    def __init__(self, model_path: str):
        import joblib                       # imported lazily: optional dependency
        self.model = joblib.load(model_path)

    def score_sentences(self, sentences: list[str]) -> list[float]:
        if not sentences:
            return []
        # Column 1 = probability of "check-worthy factual sentence" (label 1).
        return [float(p[1]) for p in self.model.predict_proba(sentences)]


def get_spotter(model_path: str | None, prefer: str = "claimbuster_svm"):
    """Return (spotter, note). Never raises: falls back to rules."""
    if prefer == "claimbuster_svm" and model_path and os.path.exists(model_path):
        try:
            return ClaimBusterSVMSpotter(model_path), "ClaimBuster-data SVM loaded"
        except Exception as e:              # missing sklearn, bad pickle, ...
            return RuleBasedSpotter(), f"SVM failed to load ({e}); using rules"
    if prefer == "claimbuster_svm":
        return RuleBasedSpotter(), "SVM model file not found; using rules"
    return RuleBasedSpotter(), "rule-based spotter selected in config"


def best_claim(text: str, spotter, rules: RuleBasedSpotter | None = None):
    """Pick the most check-worthy sentence. We blend the model score with the
    rule score (60/40) so the SVM, trained on political debates, does not
    ignore public-health specifics like agencies and bills."""
    sentences = split_sentences(text) or [text]
    model_scores = spotter.score_sentences(sentences)
    if isinstance(spotter, RuleBasedSpotter):
        blended = model_scores
    else:
        rule_scores = (rules or RuleBasedSpotter()).score_sentences(sentences)
        blended = [0.6 * m + 0.4 * r for m, r in zip(model_scores, rule_scores)]
    i = max(range(len(sentences)), key=lambda k: blended[k])
    return sentences[i], round(blended[i], 3)
