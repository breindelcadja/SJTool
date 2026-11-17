"""
Train a modern claim-spotting model from ClaimBuster's labelled data.

Why: the ClaimBuster repo (utaresearch/claimbuster-spotter) ships training
code and data but no trained weights, and its transformer needs TensorFlow 2.2
(Python <= 3.8). Its data, however, is plain JSON we can train on today.

Usage:
    git clone https://github.com/utaresearch/claimbuster-spotter.git /tmp/cb
    python scripts/train_claimbuster_svm.py /tmp/cb models/claimbuster_svm.joblib

Data: data/two_class/train.json (7,976 sentences) and test.json (702),
label 1 = check-worthy factual sentence, 0 = not. Licence: the repository is
GPL-3.0; check with the team before redistributing the trained model file.
"""

import json
import sys

import joblib
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import classification_report
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC


def load(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return [r["text"] for r in rows], [int(r["label"]) for r in rows]


def main(repo_dir, out_path):
    X_train, y_train = load(f"{repo_dir}/data/two_class/train.json")
    X_test, y_test = load(f"{repo_dir}/data/two_class/test.json")

    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                        token_pattern=r"(?u)\b\w+\b"),   # keep 1-char tokens like "5"
        # Calibration turns SVM margins into probabilities we can threshold.
        CalibratedClassifierCV(LinearSVC(C=0.5, class_weight="balanced"), cv=5),
    )
    model.fit(X_train, y_train)
    report = classification_report(y_test, model.predict(X_test), digits=3)
    print(report)
    joblib.dump(model, out_path)
    with open(out_path + ".report.txt", "w") as f:
        f.write(f"Trained on {len(X_train)} sentences, tested on {len(X_test)} "
                f"(ClaimBuster two_class split).\n\n{report}")
    print(f"saved {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
