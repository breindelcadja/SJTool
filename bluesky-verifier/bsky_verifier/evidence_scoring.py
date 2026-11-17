"""
Evidence scoring (ported from SJTool's evidence_scoring.py, logic unchanged).

Each source type has a weight; a source's contribution is weight x relevance;
the score is the average. The status names (SUPPORTED, PROMISING, ...) are
SJTool's and are kept for the pitch stage.

IMPORTANT: this score is informational. It does NOT decide the tier. Tiers
come from the rules in tiers.py, which require the claim text to be found on
the source. See academic_evidence.py for why.
"""

SOURCE_WEIGHTS = {
    "government": 1.0,
    "peer_reviewed": 0.95,
    "university": 0.85,
    "nonprofit": 0.75,
    "journalism": 0.60,
    "anecdotal": 0.40,
}


def calculate_score(sources):
    """Average of weight x relevance. `sources` need .source_type and .relevance."""
    if not sources:
        return 0.0
    total = 0.0
    for source in sources:
        total += SOURCE_WEIGHTS.get(source.source_type, 0.5) * source.relevance
    return round(total / len(sources), 3)


def determine_status(score):
    if score >= 0.80:
        return "SUPPORTED"
    if score >= 0.55:
        return "PROMISING"
    if score >= 0.25:
        return "ANECDOTAL"
    return "INSUFFICIENT_DATA"
