"""Usage caps, so a run can never spend or call more than you allow.

Counts are per run. Use None for "no limit". The evidence search is the only
paid step, so its default cap is the tightest.
"""


class LimitReached(Exception):
    """Raised when a cap is hit. The run stops cleanly and keeps results so far."""


import json
import os
from datetime import date


class UsageLedger:
    """Remembers usage across runs in a small JSON file, so daily and monthly
    caps hold even if you run the tool many times. Delete the file to reset.

    est_spend is a rough estimate of paid cost, not your real bill.
    """

    def __init__(self, path, max_daily_scoring=None, max_daily_evidence=None,
                 max_daily_spend=None, max_monthly_spend=None,
                 cost_per_evidence=0.0, today=None):
        self.path = path
        self.caps = {"daily_scoring": max_daily_scoring,
                     "daily_evidence": max_daily_evidence,
                     "daily_spend": max_daily_spend,
                     "monthly_spend": max_monthly_spend}
        self.cost_per_evidence = cost_per_evidence
        self.today = today or date.today().isoformat()
        self.data = {"days": {}}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                raise RuntimeError(
                    "Can't read %s. Fix or delete it (deleting resets the daily "
                    "and monthly counts)." % path)

    def _day(self):
        return self.data["days"].setdefault(
            self.today, {"scoring": 0, "evidence": 0, "est_spend": 0.0})

    def _month_spend(self):
        month = self.today[:7]
        return sum(d.get("est_spend", 0.0) for k, d in self.data["days"].items()
                   if k.startswith(month))

    def check_and_record(self, kind):
        if kind not in ("scoring", "evidence"):
            return
        day = self._day()
        cap = self.caps["daily_" + kind]
        if cap is not None and day[kind] >= cap:
            raise LimitReached("daily %s limit of %d reached (resets tomorrow)" % (kind, cap))
        cost = self.cost_per_evidence if kind == "evidence" else 0.0
        if cost:
            cap = self.caps["daily_spend"]
            if cap is not None and day["est_spend"] + cost > cap:
                raise LimitReached("daily estimated spend cap of $%.2f reached" % cap)
            cap = self.caps["monthly_spend"]
            if cap is not None and self._month_spend() + cost > cap:
                raise LimitReached("monthly estimated spend cap of $%.2f reached" % cap)
        day[kind] += 1
        day["est_spend"] = round(day["est_spend"] + cost, 4)
        self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def summary(self):
        d = self._day()
        return ("today: %d scoring, %d evidence, ~$%.2f est. | this month: ~$%.2f est." % (
            d["scoring"], d["evidence"], d["est_spend"], self._month_spend()))


class Budget:
    def __init__(self, max_pitches=None, max_scoring_calls=None, max_evidence_calls=None,
                 ledger=None):
        self.limits = {"pitches": max_pitches, "scoring": max_scoring_calls,
                       "evidence": max_evidence_calls}
        self.used = {"pitches": 0, "scoring": 0, "evidence": 0}
        self.ledger = ledger

    def use(self, kind):
        limit = self.limits[kind]
        if limit is not None and self.used[kind] >= limit:
            raise LimitReached("%s limit of %d reached" % (kind, limit))
        if self.ledger is not None:
            self.ledger.check_and_record(kind)
        self.used[kind] += 1

    def summary(self):
        parts = []
        for kind in ("pitches", "scoring", "evidence"):
            cap = self.limits[kind]
            parts.append("%s %d/%s" % (kind, self.used[kind], cap if cap is not None else "no limit"))
        text = ", ".join(parts)
        if self.ledger is not None:
            text += " | " + self.ledger.summary()
        return text


class LimitedScorer:
    """Wraps any claim scorer and counts each call against the budget."""

    def __init__(self, scorer, budget):
        self.scorer, self.budget = scorer, budget
        self.label = getattr(scorer, "label", "custom scorer")

    def score(self, text):
        self.budget.use("scoring")
        return self.scorer.score(text)


class LimitedFinder:
    """Wraps any evidence finder and counts each call against the budget."""

    def __init__(self, finder, budget):
        self.finder, self.budget = finder, budget
        self.label = getattr(finder, "label", "custom evidence finder")

    def find(self, pitch, claim_text):
        self.budget.use("evidence")
        return self.finder.find(pitch, claim_text)
