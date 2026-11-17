#!/usr/bin/env python3
"""Train the free local claim scorer.

  git clone https://github.com/utaresearch/claimbuster-spotter ~/claimbuster-spotter
  pip3 install scikit-learn joblib
  python3 train_local_scorer.py --data ~/claimbuster-spotter/data/two_class

Writes local_claim_model.joblib here. Do not commit or share that file or the
training data until your team has checked their licenses (the repo is GPL v3).
"""

import argparse
import json
import os


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="the data/two_class folder")
    p.add_argument("--out", default="local_claim_model.joblib")
    a = p.parse_args()

    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.pipeline import make_pipeline, make_union

    def load(name):
        with open(os.path.join(a.data, name), encoding="utf-8") as f:
            rows = json.load(f)
        return [r["text"] for r in rows], [r["label"] for r in rows]

    X, y = load("train.json")
    Xt, yt = load("test.json")
    model = make_pipeline(
        make_union(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                   TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3,
                                   sublinear_tf=True)),
        LogisticRegression(C=4, max_iter=2000, class_weight="balanced"))
    model.fit(X, y)
    probs = model.predict_proba(Xt)[:, 1]
    print("Held-out test (political debate sentences): AUC %.3f, avg precision %.3f" % (
        roc_auc_score(yt, probs), average_precision_score(yt, probs)))
    print("This is NOT accuracy on dengue posts. Check those by hand.")
    joblib.dump(model, a.out)
    print("Saved", a.out)


if __name__ == "__main__":
    main()
