"""
Trusted source registry (ported from SJTool's evidence_sources.py).

SJTool's original dictionary is kept as-is. `classify_domain` is new: it turns
a web domain into one of the source types used by evidence_scoring.py.
"""

GOVERNMENT_SEARCHES = {
    "public_health": ["cdc.gov", "nih.gov"],
    "environment": ["epa.gov", "usgs.gov"],
    "agriculture": ["usda.gov"],
    "energy": ["energy.gov"],
    "education": ["ed.gov"],
}

_REGISTRY_DOMAINS = {d for ds in GOVERNMENT_SEARCHES.values() for d in ds}
_JOURNALISM_HINTS = ("news", "times", "post", "herald", "tribune", "npr.org",
                     "reuters", "cidrap", "conversation", "tampabay", "wlrn", "wusf")


def classify_domain(domain: str) -> str:
    """Best-effort source type from a domain. A heuristic, not a verdict."""
    d = domain.lower().split(":")[0].removeprefix("www.")
    if d in _REGISTRY_DOMAINS or any(d.endswith("." + r) for r in _REGISTRY_DOMAINS):
        return "government"
    if d.endswith(".gov") or ".gov." in d:
        return "government"
    if d.endswith(".edu") or ".edu." in d or ".ac." in d:
        return "university"
    if d.endswith(".org") and not any(h in d for h in _JOURNALISM_HINTS):
        return "nonprofit"
    return "journalism"
