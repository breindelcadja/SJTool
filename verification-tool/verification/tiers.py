"""Tier assignment: plain code, from the rules in the spec. No model involved.

Order of evaluation, first match wins:
  1. myth          a credible source contradicts the claim
  2. flagged       at least half the source posts are flagged AND no evidence found
  3. strong_lead   response met, insight and limitations at least partial,
                   evidence at least partial with one primary source
  4. promising     response met, insight at least partial
  5. early_signal  everything else

Flags alone never demote a pitch that has evidence. They are shown on the tile.
"""

from .config import FLAG_RATIO_CUTOFF

_RANK = {"not_found": 0, "partial": 1, "met": 2}


def assign_tier(pillars, contradiction, source, citations):
    if contradiction["found"]:
        return "myth"

    response = pillars["response"]["state"]
    insight = pillars["insight"]["state"]
    evidence = pillars["evidence"]["state"]
    limitations = pillars["limitations"]["state"]

    if source["flag_ratio"] >= FLAG_RATIO_CUTOFF and evidence == "not_found":
        return "flagged"

    evidence_tiers = {citations[i]["source_tier"]
                      for i in pillars["evidence"]["citations"] if i in citations}
    if (response == "met" and _RANK[insight] >= 1 and _RANK[evidence] >= 1
            and "primary" in evidence_tiers and _RANK[limitations] >= 1):
        return "strong_lead"
    if response == "met" and _RANK[insight] >= 1:
        return "promising"
    return "early_signal"
