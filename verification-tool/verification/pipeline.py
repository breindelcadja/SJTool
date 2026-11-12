"""The verification pipeline for one pitch.

    posts -> claim scoring -> source checks -> evidence search (LLM)
          -> citation checks (code) -> tier assignment (code) -> result

verify_pitch() is the function to call from your own code. Everything it needs
is passed in, so you can swap scorers, finders and page fetchers freely.
"""

from datetime import date

from .citations import validate_citations
from .claims import best_sentence
from .config import CLAIM_SCORE_CUTOFF, CONFIG_VERSION
from .source_checks import source_checks
from .tiers import assign_tier

PILLARS = ("response", "insight", "evidence", "limitations")
VALID_STATES = {"met", "partial", "not_found"}


def clean_pillars(raw, valid):
    """Keep only pillar states that are backed by a surviving citation.

    A met or partial pillar with no surviving citation drops to not_found.
    The response pillar is exempt: it is established by the posts themselves
    (the classifier already decided the post describes a solution).
    """
    out = {}
    for name in PILLARS:
        entry = (raw or {}).get(name) or {}
        state = entry.get("state", "not_found")
        if state not in VALID_STATES:
            state = "not_found"
        ids = [i for i in entry.get("citations", []) if i in valid]
        if name != "response" and state != "not_found" and not ids:
            state = "not_found"
        out[name] = {"state": state, "citations": ids}
    return out


def clean_contradiction(raw, valid):
    """A contradiction counts only if a surviving credible citation backs it."""
    ids = [i for i in (raw or {}).get("citations", []) if i in valid]
    found = bool((raw or {}).get("found")) and bool(ids)
    return {"found": found, "citations": ids if found else []}


def decide_claim_result(raw, pillars, contradiction):
    llm_result = (raw or {}).get("result")
    llm_summary = (raw or {}).get("summary", "")
    if contradiction["found"]:
        result = "contradicted"
    elif llm_result == "supported" and pillars["evidence"]["state"] != "not_found":
        result = "supported"
    else:
        result = "unverified"
    if result == llm_result:
        summary = llm_summary
    elif result == "contradicted":
        summary = "A credible source contradicts this claim."
    else:
        summary = "No verified source supports this claim."
    return result, summary


def score_claims(pitch, scorer, cutoff=CLAIM_SCORE_CUTOFF, today=None):
    """Claim scoring + source checks only. No evidence search, no Anthropic key.

    Returns the base result. next_step is "ready_for_evidence_search" when the
    best claim clears the cutoff, otherwise "dropped_no_checkable_claim".
    `posts` lists every post with its best sentence, score and flags.
    """
    today = today or date.today().isoformat()
    posts = pitch["posts"]
    source, per_post_flags = source_checks(posts)

    rows, best_text, best_score = [], "", 0.0
    for i, post in enumerate(posts):
        text, score = best_sentence(scorer.score(post["text"]))
        rows.append({"url": post.get("url"), "claim": text,
                     "claim_score": round(score, 2),
                     "flags": _flags_for(per_post_flags, i, post)})
        if not best_text or score > best_score:
            best_text, best_score = text, score

    rows.sort(key=lambda r: r["claim_score"], reverse=True)
    ready = best_score >= cutoff
    return {
        "pitch_id": pitch["pitch_id"],
        "problem": pitch.get("problem"),
        "solution_summary": pitch.get("solution_summary"),
        "claim": {"text": best_text, "score": round(best_score, 2),
                  "result": None, "summary": ""},
        "source": source,
        "posts": rows,
        "social_lead": pitch.get("social_lead"),
        "context": pitch.get("context"),
        "coverage": pitch.get("coverage"),
        "checked_at": today,
        "config_version": CONFIG_VERSION,
        "tier": None,
        "next_step": "ready_for_evidence_search" if ready else "dropped_no_checkable_claim",
        "pillars": None, "contradiction": None,
        "citations": [], "dropped_citations": [],
    }


def _flags_for(per_post_flags, index, post):
    """per_post_flags may be a list (by position) or dict (by url); be tolerant."""
    try:
        if isinstance(per_post_flags, dict):
            return list(per_post_flags.get(post.get("url"), []))
        return list(per_post_flags[index])
    except Exception:
        return []


def verify_pitch(pitch, scorer, finder, fetch, cutoff=CLAIM_SCORE_CUTOFF, today=None):
    """Verify one pitch.

    pitch   {"pitch_id", "problem", "solution_summary", "posts": [...]}
    scorer  object with score(text) -> [(sentence, score), ...]
    finder  object with find(pitch, claim_text) -> evidence dict
    fetch   function url -> page text (or None)
    """
    result = score_claims(pitch, scorer, cutoff, today)
    today = result["checked_at"]
    source, best_text = result["source"], result["claim"]["text"]

    if result["next_step"] == "dropped_no_checkable_claim":
        return result

    evidence = finder.find(pitch, best_text)
    valid, dropped = validate_citations(
        evidence.get("citations", []), fetch, evidence.get("seen_urls"), today)
    pillars = clean_pillars(evidence.get("pillars"), valid)
    contradiction = clean_contradiction(evidence.get("contradiction"), valid)
    claim_result, summary = decide_claim_result(
        evidence.get("claim_result"), pillars, contradiction)

    result["claim"].update(result=claim_result, summary=summary)
    result.update(
        tier=assign_tier(pillars, contradiction, source, valid),
        next_step="verified",
        pillars=pillars,
        contradiction=contradiction,
        citations=list(valid.values()),
        dropped_citations=dropped,
    )
    return result
