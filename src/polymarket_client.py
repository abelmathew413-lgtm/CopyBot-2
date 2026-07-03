"""
Thin, dependency-light client for Polymarket's three public APIs.

- Gamma API  (gamma-api.polymarket.com): market/event metadata, public profiles
- Data API   (data-api.polymarket.com):  leaderboard, positions, trades, activity
- CLOB API   (clob.polymarket.com):      order book / pricing (used for fair-value estimate)

All endpoints used here are public and require no authentication.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

DEFAULT_TIMEOUT = 15
USER_AGENT = "polytracker/0.1 (research tool; contact: local)"


class PolymarketAPIError(RuntimeError):
    pass


def _get(base: str, path: str, params: Optional[dict] = None, retries: int = 3) -> Any:
    """GET with basic retry/backoff. Raises PolymarketAPIError on persistent failure."""
    url = f"{base}{path}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 429:
                # rate limited -- back off and retry
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    raise PolymarketAPIError(f"GET {url} failed after {retries} attempts: {last_exc}")


# ---------------------------------------------------------------------------
# Data API: leaderboard, positions, trades
# ---------------------------------------------------------------------------

LEADERBOARD_CATEGORIES = [
    "OVERALL", "POLITICS", "SPORTS", "CRYPTO", "CULTURE",
    "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE",
]
LEADERBOARD_PERIODS = ["DAY", "WEEK", "MONTH", "ALL"]


def get_leaderboard(
    category: str = "OVERALL",
    time_period: str = "WEEK",
    order_by: str = "PNL",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    GET /v1/leaderboard
    limit is capped at 50 per call by the API; offset capped at 1000.
    Caller should page with offset in steps of `limit` to go deeper.
    """
    params = {
        "category": category,
        "timePeriod": time_period,
        "orderBy": order_by,
        "limit": min(limit, 50),
        "offset": offset,
    }
    return _get(DATA_API, "/v1/leaderboard", params)


def get_leaderboard_paged(
    category: str = "OVERALL",
    time_period: str = "WEEK",
    order_by: str = "PNL",
    max_results: int = 500,
) -> list[dict]:
    """Page through the leaderboard up to max_results (API hard caps offset at 1000)."""
    out: list[dict] = []
    offset = 0
    while len(out) < max_results and offset <= 1000:
        page = get_leaderboard(category, time_period, order_by, limit=50, offset=offset)
        if not page:
            break
        out.extend(page)
        offset += 50
    return out[:max_results]


def get_current_positions(
    user: str,
    limit: int = 500,
    offset: int = 0,
    size_threshold: float = 1.0,
    sort_by: str = "CURRENT",
) -> list[dict]:
    """GET /positions -- a wallet's open positions. limit capped at 500."""
    params = {
        "user": user,
        "limit": min(limit, 500),
        "offset": offset,
        "sizeThreshold": size_threshold,
        "sortBy": sort_by,
        "sortDirection": "DESC",
    }
    return _get(DATA_API, "/positions", params)


def get_all_current_positions(user: str, hard_cap: int = 2000) -> list[dict]:
    """Page through ALL open positions for a wallet (used to enforce the <=150 active-positions rule)."""
    out: list[dict] = []
    offset = 0
    while offset < hard_cap:
        page = get_current_positions(user, limit=500, offset=offset)
        if not page:
            break
        out.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return out


def get_closed_positions(
    user: str,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "TIMESTAMP",
) -> list[dict]:
    """GET /closed-positions -- realized PnL per resolved market. limit capped at 50."""
    params = {
        "user": user,
        "limit": min(limit, 50),
        "offset": offset,
        "sortBy": sort_by,
        "sortDirection": "DESC",
    }
    return _get(DATA_API, "/closed-positions", params)


def get_all_closed_positions(user: str, hard_cap: int = 2000) -> list[dict]:
    """Page through closed positions (used for win-rate, ROI, and concentration calculations)."""
    out: list[dict] = []
    offset = 0
    while offset < hard_cap:
        page = get_closed_positions(user, limit=50, offset=offset)
        if not page:
            break
        out.extend(page)
        if len(page) < 50:
            break
        offset += 50
    return out


def get_trades(
    user: Optional[str] = None,
    market: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
    side: Optional[str] = None,
) -> list[dict]:
    """GET /trades -- raw fills. Used to compute trades/day cadence."""
    params: dict = {"limit": min(limit, 10000), "offset": offset}
    if user:
        params["user"] = user
    if market:
        params["market"] = market
    if side:
        params["side"] = side
    return _get(DATA_API, "/trades", params)


def get_all_trades(user: str, hard_cap: int = 20000) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while offset < hard_cap:
        page = get_trades(user=user, limit=1000, offset=offset)
        if not page:
            break
        out.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return out


# ---------------------------------------------------------------------------
# Gamma API: public profile (account age, display info -- NOTE: no follower
# count is exposed by the official API; see radar_filter.py for the caveat)
# ---------------------------------------------------------------------------

def get_public_profile(address: str) -> Optional[dict]:
    try:
        return _get(GAMMA_API, "/public-profile", {"address": address})
    except PolymarketAPIError:
        return None


# ---------------------------------------------------------------------------
# CLOB API: order book / midpoint (used for the independent fair-value estimate)
# ---------------------------------------------------------------------------

def get_midpoint(token_id: str) -> Optional[float]:
    try:
        data = _get(CLOB_API, "/midpoint", {"token_id": token_id})
        return float(data.get("mid")) if data and "mid" in data else None
    except (PolymarketAPIError, TypeError, ValueError):
        return None


def get_prices_history(
    token_id: str,
    interval: str = "1h",
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> list[dict]:
    """
    GET /prices-history -- historical {t, p} points for one outcome token.
    NOTE: Polymarket's own granularity on this endpoint is inconsistent for
    resolved markets -- documented community reports show it sometimes only
    returning 12h+ fidelity, or empty, even for high-volume markets. Callers
    (see clv.py) must treat an empty/short result as "unknown," never as
    "price was flat" or any other inferred value.
    """
    params: dict = {"market": token_id, "interval": interval}
    if start_ts is not None:
        params["startTs"] = start_ts
    if end_ts is not None:
        params["endTs"] = end_ts
    try:
        data = _get(CLOB_API, "/prices-history", params)
        return data.get("history", []) if isinstance(data, dict) else []
    except PolymarketAPIError:
        return []


def get_book(token_id: str) -> Optional[dict]:
    try:
        return _get(CLOB_API, "/book", {"token_id": token_id})
    except PolymarketAPIError:
        return None


def get_price_history(
    token_id: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    interval: str = "max",
) -> list[dict]:
    """
    GET /prices-history -- returns [{"t": unix_ts, "p": price}, ...].
    NOTE: this endpoint has known reliability gaps on resolved markets --
    granularity can be as coarse as 12+ hours even on high-volume markets,
    and it occasionally returns empty for some tokens entirely (documented
    upstream issue). Callers must treat an empty result as "unknown," not
    as "price was zero." Used for CLV estimation in scoring.py.
    """
    params: dict = {"market": token_id, "interval": interval}
    if start_ts is not None:
        params["startTs"] = start_ts
    if end_ts is not None:
        params["endTs"] = end_ts
    try:
        data = _get(CLOB_API, "/prices-history", params)
        return data.get("history", []) if isinstance(data, dict) else []
    except PolymarketAPIError:
        return []


def get_closing_price(token_id: str, before_ts: int, lookback_days: int = 3) -> Optional[float]:
    """
    Best-effort 'closing price' estimate: the last known price point for this
    token at or before `before_ts`. Returns None if no price history is
    available (caller must skip this trade's CLV contribution, not treat as 0).
    """
    history = get_price_history(
        token_id,
        start_ts=before_ts - lookback_days * 86400,
        end_ts=before_ts,
        interval="max",
    )
    if not history:
        return None
    # history is generally time-ascending; take the last point <= before_ts
    candidates = [pt for pt in history if pt.get("t", 0) <= before_ts]
    if not candidates:
        candidates = history  # fall back to whatever we got
    last = max(candidates, key=lambda pt: pt.get("t", 0))
    price = last.get("p")
    return float(price) if price is not None else None


@dataclass
class WalletSnapshot:
    """Everything we pulled for one wallet, bundled for the scoring stage."""
    address: str
    profile: Optional[dict]
    open_positions: list[dict]
    closed_positions: list[dict]
    trades: list[dict]


def fetch_wallet_snapshot(address: str) -> WalletSnapshot:
    return WalletSnapshot(
        address=address,
        profile=get_public_profile(address),
        open_positions=get_all_current_positions(address),
        closed_positions=get_all_closed_positions(address),
        trades=get_all_trades(address),
    )
