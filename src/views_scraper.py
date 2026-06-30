"""
Polymarket renders a "X views" counter directly on its own profile pages
(e.g. polymarket.com/@wan123 -> "Joined Dec 2025 · 71.4K views"), but this
field is NOT part of the documented public API (confirmed against the
/public-profile schema). So we get it the only way available: fetching the
profile page itself and parsing the rendered count out of the HTML.

This is first-party data (Polymarket's own domain), which is materially more
stable than scraping a third-party aggregator like polymarketanalytics.com --
but it's still an undocumented page-scrape, not an API contract, so it can
break if Polymarket changes their page markup. Always treat failures as
"unknown" (None), never as "zero views" -- the radar filter decides how to
treat unknown views (see radar_filter.py).
"""

from __future__ import annotations

import re
import time
from typing import Optional

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

VIEWS_PATTERN = re.compile(r"([\d,]+\.?\d*)\s*([KM]?)\s*views", re.IGNORECASE)


def _parse_views(text: str) -> Optional[int]:
    match = VIEWS_PATTERN.search(text)
    if not match:
        return None
    number_str, suffix = match.group(1).replace(",", ""), match.group(2).upper()
    multiplier = {"K": 1_000, "M": 1_000_000, "": 1}.get(suffix, 1)
    try:
        return int(float(number_str) * multiplier)
    except ValueError:
        return None


def get_profile_views(handle: str, retries: int = 2, timeout: int = 15) -> Optional[int]:
    """
    Returns the view count shown on polymarket.com/@<handle>, or None if it
    couldn't be determined (page changed, handle doesn't resolve, network
    error, etc.). Callers must treat None as "unknown," not "zero."
    """
    if not handle:
        return None
    url = f"https://polymarket.com/@{handle}"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return _parse_views(resp.text)
        except requests.RequestException:
            time.sleep(0.5 * (attempt + 1))
    return None
