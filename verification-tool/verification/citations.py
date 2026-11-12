"""Citation checks done in code, so the model cannot invent a source.

A citation survives only if:
  1. its domain is not a social media site,
  2. its URL was actually returned by the search (when we know which were),
  3. the page loads, and
  4. the quoted sentence really appears on the page.

Source tier (primary or secondary) is decided here by domain, never by the model.
"""

import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlparse

from .config import PRIMARY_DOMAINS, PRIMARY_SUFFIXES, SOCIAL_DOMAINS

MIN_QUOTE_CHARS = 15


def host_of(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def source_tier(url):
    """'primary', 'secondary', or None for social media (never evidence)."""
    host = host_of(url)
    if not host or _matches(host, SOCIAL_DOMAINS):
        return None
    if host.endswith(PRIMARY_SUFFIXES) or _matches(host, PRIMARY_DOMAINS):
        return "primary"
    return "secondary"


def normalize_text(text):
    text = (text.replace("‘", "'").replace("’", "'")
                .replace("“", '"').replace("”", '"'))
    return " ".join(text.lower().split())


def quote_in_page(quote, page_text):
    q = normalize_text(quote or "")
    return len(q) >= MIN_QUOTE_CHARS and q in normalize_text(page_text or "")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def fetch_page_text(url, timeout=20):
    """Live page fetch. Returns visible text, or None for non-text pages.

    Limits: pages rendered by JavaScript and PDFs return no usable text, so
    citations to them are dropped. Add a PDF/JS-capable fetcher if that matters.
    """
    import requests  # imported here so offline mode needs no install

    response = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "solutions-verification-prototype"})
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "") and \
            "text" not in response.headers.get("content-type", ""):
        return None
    parser = _TextExtractor()
    parser.feed(response.text)
    return " ".join(parser.chunks)


def validate_citations(citations, fetch, seen_urls=None, today=None):
    """Returns ({id: citation}, [dropped]). `fetch(url)` returns page text or None."""
    today = today or date.today().isoformat()
    valid, dropped = {}, []
    for c in citations or []:
        cid, url, quote = c.get("id"), c.get("url", ""), c.get("supporting_quote", "")
        tier = source_tier(url)
        reason = None
        if tier is None:
            reason = "social_media_source"
        elif seen_urls is not None and url not in seen_urls:
            reason = "url_not_in_search_results"
        else:
            try:
                page = fetch(url)
            except Exception:
                page = None
            if not page:
                reason = "page_unreachable"
            elif not quote_in_page(quote, page):
                reason = "quote_not_on_page"
        if reason:
            dropped.append({"id": cid, "url": url, "reason": reason})
        else:
            valid[cid] = {"id": cid, "url": url, "source_tier": tier,
                          "supporting_quote": quote, "fetched_at": today}
    return valid, dropped
