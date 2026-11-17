"""The one entry point your scraper / backend calls.

    from verification.api import verify_pitches

    tiles = verify_pitches(pitches, claimbuster_key=os.environ["CLAIMBUSTER_API_KEY"],
                           evidence_finder=my_finder)

Keys are passed in by your server (read from its secret store). Journalists
never see or hold a key.

Returns a list of journalist-facing "tile" dicts (see to_tile). Internal
numbers like the ClaimBuster score are kept under "internal" for your team
and should not be shown to journalists.
"""

from .citations import fetch_page_text
from .claims import ClaimBusterScorer, OfflineClaimScorer
from .config import CLAIM_SCORE_CUTOFF
from .pipeline import score_claims, verify_pitch

TIER_TEXT = {
    "strong_lead": ("Strong lead", "Worth pitching now."),
    "promising": ("Promising", "Needs a closer look before pitching."),
    "early_signal": ("Early signal", "Treat as a tip and look for proof."),
    "flagged": ("Flagged", "Do not use without checking who is behind it."),
    "myth": ("Myth", "Popular idea that credible sources say does not hold up."),
}
RESULT_TEXT = {"supported": "Supported", "contradicted": "Contradicted",
               "unverified": "Not supported"}


def to_tile(r):
    """Journalist-facing view of one result. status is one of:
    verified | awaiting_evidence | no_checkable_claim | error"""
    if r.get("error"):
        return {"pitch_id": r.get("pitch_id"), "status": "error", "message": r["error"]}
    base = {
        "pitch_id": r["pitch_id"],
        "solution_summary": r.get("solution_summary"),
        "checked_at": r["checked_at"],
        "internal": {"claim_score": r["claim"]["score"],
                     "config_version": r["config_version"],
                     "source": r["source"]},
    }
    if r["next_step"] == "dropped_no_checkable_claim":
        return dict(base, status="no_checkable_claim")
    if r["tier"] is None:
        return dict(base, status="awaiting_evidence",
                    claim={"text": r["claim"]["text"]})
    label, action = TIER_TEXT[r["tier"]]
    seen, sources = set(), []
    for c in r["citations"]:
        if c["url"] not in seen:
            seen.add(c["url"])
            sources.append({"url": c["url"], "source_tier": c["source_tier"]})
    return dict(
        base, status="verified", tier=r["tier"], tier_label=label, action=action,
        claim={"text": r["claim"]["text"], "result": RESULT_TEXT[r["claim"]["result"]],
               "summary": r["claim"]["summary"]},
        pillars={k: v["state"] for k, v in r["pillars"].items()},
        flags=r["source"]["flags"], sources=sources)


def verify_pitches(pitches, claimbuster_key=None, evidence_finder=None,
                   cutoff=CLAIM_SCORE_CUTOFF, fake_claim_scores=False, scorer=None):
    """Run the pipeline on a list of pitches and return tiles.

    evidence_finder=None -> claims-only: tiles come back "awaiting_evidence".
    fake_claim_scores=True -> testing only; never use for real journalists.
    One bad pitch becomes an "error" tile and does not stop the batch.
    """
    if scorer is not None:
        pass  # e.g. LocalClaimScorer(path)
    elif fake_claim_scores:
        scorer = OfflineClaimScorer()
    elif claimbuster_key:
        scorer = ClaimBusterScorer(claimbuster_key)
    else:
        raise ValueError("Pass claimbuster_key, scorer=..., or fake_claim_scores=True for tests.")

    tiles = []
    for pitch in pitches:
        try:
            if evidence_finder is None:
                result = score_claims(pitch, scorer, cutoff)
            else:
                result = verify_pitch(pitch, scorer, evidence_finder,
                                      fetch_page_text, cutoff)
            tiles.append(to_tile(result))
        except Exception as error:
            tiles.append(to_tile({"pitch_id": pitch.get("pitch_id"), "error": str(error)}))
    return tiles
