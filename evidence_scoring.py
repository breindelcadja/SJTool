"""Evidence scoring utilities."""
SOURCE_WEIGHTS={
    'government':1.0,
    'peer_reviewed':0.95,
    'university':0.85,
    'nonprofit':0.75,
    'journalism':0.60,
    'anecdotal':0.40,
}

def calculate_score(sources):
    if not sources:
        return 0.0
    total=0
    for source in sources:
        total += SOURCE_WEIGHTS.get(source.source_type,0.5)*source.relevance
    return round(total/len(sources),3)

def determine_status(score):
    if score >= 0.80:
        return 'SUPPORTED'
    if score >= 0.55:
        return 'PROMISING'
    if score >= 0.25:
        return 'ANECDOTAL'
    return 'INSUFFICIENT_DATA'
