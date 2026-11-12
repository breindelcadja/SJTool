"""A free, local claim scorer. No API key, no network, no cost.

NOT the ClaimBuster API and NOT ClaimBuster's model. It is a small
scikit-learn model you train yourself on the labeled sentences in the
public claimbuster-spotter repo (see train_local_scorer.py).

Same interface as the other scorers: score(text) -> [(sentence, score), ...]
Scores are 0 to 1: how much the sentence looks like a checkable factual claim.
"""

from .claims import split_sentences


class LocalClaimScorer:
    label = "LOCAL model (trained on claimbuster-spotter data; not the ClaimBuster API)"

    def __init__(self, model_path):
        import joblib  # imported here so other modes need no install
        self.model = joblib.load(model_path)

    def score(self, text):
        sentences = split_sentences(text)
        if not sentences:
            return []
        probs = self.model.predict_proba(sentences)[:, 1]
        return [(s, float(p)) for s, p in zip(sentences, probs)]
