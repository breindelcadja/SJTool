"""
Plain HTTP page fetching (allowed in Phase 1) with usage caps.

Every fetch goes through `CappedFetcher`, which:
  * refuses to fetch once a per-run, daily or monthly cap is reached
    (raising CapReached, which the pipeline catches and reports),
  * caches pages within a run so the same URL is fetched once,
  * records why a fetch failed in plain words: timeout, 404, paywall,
    blocked, network blocked, not HTML, ...

Daily/monthly counts persist in a small JSON "ledger" file so caps hold
across runs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


class CapReached(Exception):
    """Raised when a usage cap would be exceeded."""


@dataclass
class FetchResult:
    url: str
    ok: bool                     # True only if the page loaded with readable text
    status: Optional[int]
    final_url: Optional[str]
    text: str                    # visible page text ("" on failure)
    failure: Optional[str]       # plain-language reason when ok is False
    paywalled: bool = False


PAYWALL_MARKERS = [
    r'"isAccessibleForFree"\s*:\s*"?false',
    r"subscribe to (continue|read)", r"already a subscriber",
    r"this (article|content) is for subscribers", r"paywall",
]


def html_to_text(html: str) -> str:
    """Visible text only (scripts, styles and navigation removed)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(" ")
    except ImportError:                       # crude fallback without bs4
        text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


class UsageLedger:
    """Counts fetches and paid calls per day and month, saved to JSON."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self.data = {"daily": {}, "monthly": {}}
        if path and os.path.exists(path):
            with open(path) as f:
                self.data = json.load(f)

    @staticmethod
    def _keys():
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def count(self, kind: str) -> tuple[int, int]:
        d, m = self._keys()
        return (self.data["daily"].get(d, {}).get(kind, 0),
                self.data["monthly"].get(m, {}).get(kind, 0))

    def add(self, kind: str, n: int = 1):
        d, m = self._keys()
        for bucket, key in (("daily", d), ("monthly", m)):
            self.data[bucket].setdefault(key, {})
            self.data[bucket][key][kind] = self.data[bucket][key].get(kind, 0) + n
        self.save()

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2)


class RequestsBackend:
    """The real network. Tests replace this with a fake backend."""

    def __init__(self, timeout: float, user_agent: str):
        self.timeout = timeout
        self.user_agent = user_agent

    def get(self, url: str) -> FetchResult:
        import requests
        try:
            r = requests.get(url, timeout=self.timeout, allow_redirects=True,
                             headers={"User-Agent": self.user_agent})
        except requests.exceptions.Timeout:
            return FetchResult(url, False, None, None, "", "timeout")
        except requests.exceptions.ProxyError:
            return FetchResult(url, False, None, None, "",
                               "network blocked (the proxy/firewall where this ran "
                               "refused the site; the page itself was never tested)")
        except requests.exceptions.SSLError:
            return FetchResult(url, False, None, None, "", "TLS/certificate error")
        except requests.exceptions.ConnectionError:
            return FetchResult(url, False, None, None, "", "connection failed / DNS")
        except requests.exceptions.RequestException as e:
            return FetchResult(url, False, None, None, "", f"request error: {e}")

        return classify_response(url, r.status_code, r.url,
                                 r.headers.get("Content-Type", ""), r.text)


def classify_response(url, status, final_url, content_type, body) -> FetchResult:
    """Turn an HTTP response into a FetchResult with a clear failure reason."""
    if status == 404 or status == 410:
        return FetchResult(url, False, status, final_url, "", f"{status} not found")
    if status in (401, 402):
        return FetchResult(url, False, status, final_url, "", f"{status} paywall/login required", True)
    if status == 403:
        return FetchResult(url, False, status, final_url, "", "403 forbidden (site blocks automated readers)")
    if status == 429:
        return FetchResult(url, False, status, final_url, "", "429 rate limited by site")
    if status >= 400:
        return FetchResult(url, False, status, final_url, "", f"HTTP {status} error")
    if "html" not in content_type.lower() and "text" not in content_type.lower():
        return FetchResult(url, False, status, final_url, "",
                           f"not a web page ({content_type or 'unknown type'}); PDF/text extraction not in Phase 1")
    text = html_to_text(body)
    paywalled = any(re.search(p, body, re.I) for p in PAYWALL_MARKERS)
    if len(text) < 200:
        return FetchResult(url, False, status, final_url, text,
                           "page loaded but has almost no readable text (JavaScript-only or blocked)",
                           paywalled)
    return FetchResult(url, True, status, final_url, text, None, paywalled)


class CappedFetcher:
    def __init__(self, backend, caps: dict, ledger: UsageLedger,
                 cost_per_fetch_usd: float = 0.0):
        self.backend = backend
        self.caps = caps
        self.ledger = ledger
        self.cost_per_fetch_usd = cost_per_fetch_usd
        self.cache: dict[str, FetchResult] = {}
        self.fetches_this_run = 0
        self.cap_events: list[str] = []

    def _check_caps(self):
        daily, monthly = self.ledger.count("page_fetch")
        if self.fetches_this_run >= self.caps.get("max_page_fetches_per_run", 50):
            raise CapReached("per-run page-fetch cap reached")
        if daily >= self.caps.get("max_page_fetches_per_day", 500):
            raise CapReached("daily page-fetch cap reached")
        if monthly >= self.caps.get("max_page_fetches_per_month", 5000):
            raise CapReached("monthly page-fetch cap reached")

    def get(self, url: str) -> FetchResult:
        if url in self.cache:
            return self.cache[url]
        try:
            self._check_caps()
        except CapReached as e:
            if str(e) not in self.cap_events:
                self.cap_events.append(str(e))
            raise
        self.fetches_this_run += 1
        self.ledger.add("page_fetch")
        result = self.backend.get(url)
        self.cache[url] = result
        return result

    @property
    def estimated_cost_usd(self) -> float:
        return round(self.fetches_this_run * self.cost_per_fetch_usd, 4)
