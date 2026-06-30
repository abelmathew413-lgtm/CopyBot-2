"""
Closing Line Value (CLV) for a closed Polymarket position.

CLV = (price shortly before resolution) - (the trader's own entry price),
measured on the specific outcome token they held. Positive CLV means the
market moved in their favor *after* they entered -- i.e. they identified
the mispricing before the rest of the market did, which is a better skill
signal than win rate alone (you can win a bet you got into late/at a bad
price; CLV specifically rewards being early/right).

This is signed by the token they actually held (avgPrice / closing price
are both quoted for that same token), so no separate "side" adjustment is
needed -- a YES holder and a NO holder on the same market just have
different (complementary) token prices, and we always compare like-for-like.

DATA RELIABILITY CAVEAT (load-bearing for how this is used downstream):
Polymarket's /prices-history endpoint has documented gaps for resolved
markets -- sometimes only 12h+ fidelity, sometimes empty entirely, even on
high-volume markets. Per spec, when we can't get a usable closing price for
a position, we return None for that position rather than guessing, and
scoring.py drops CLV from that *wallet's* composite (redistributing its
weight to the other 6 components) rather than penalizing a wallet just
because Polymarket's own data was incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import polymarket_client as pc

# How far before resolution to look for a "closing" price. Polymarket's
# coarse fidelity means asking for the literal last second is unreliable;
# a window gives the lookup something to find.
CLOSING_WINDOW_HOURS = 36


def _parse_end_date(end_date: Optional[str]) -> Optional[int]:
    if not end_date:
        return None
    try:
        dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return None


@dataclass
class CLVResult:
    clv: Optional[float]  # signed, in price units (-1..1, typically much smaller); None = unknown
    closing_price: Optional[float]
    entry_price: Optional[float]
    reason: str  # why it's None, when it is -- useful for debugging a "no CLV" wallet


def compute_clv_for_position(position: dict) -> CLVResult:
    token_id = position.get("asset")
    entry_price = position.get("avgPrice")
    end_date_ts = _parse_end_date(position.get("endDate"))

    if not token_id or entry_price is None:
        return CLVResult(None, None, entry_price, "missing token id or entry price")
    if end_date_ts is None:
        return CLVResult(None, None, entry_price, "missing/unparseable resolution date")

    history = pc.get_prices_history(
        token_id,
        interval="1h",
        start_ts=end_date_ts - CLOSING_WINDOW_HOURS * 3600,
        end_ts=end_date_ts,
    )
    if not history:
        # Coarser fallback attempt before giving up entirely -- per the
        # documented community reports, "max"/longer intervals sometimes
        # return data when fine-grained windows come back empty.
        history = pc.get_prices_history(token_id, interval="max")
        # restrict to points at or before resolution, if we got anything back
        history = [h for h in history if h.get("t", 0) <= end_date_ts]

    if not history:
        return CLVResult(None, None, entry_price, "no price history available for this token")

    closing_point = max(history, key=lambda h: h.get("t", 0))
    closing_price = closing_point.get("p")
    if closing_price is None:
        return CLVResult(None, None, entry_price, "price history returned no usable price field")

    clv = float(closing_price) - float(entry_price)
    return CLVResult(clv, float(closing_price), float(entry_price), "ok")


def compute_avg_clv(closed_positions: list[dict], max_positions: int = 40) -> tuple[Optional[float], int, int]:
    """
    Returns (avg_clv, n_with_data, n_attempted). avg_clv is None if no
    position yielded usable data. Capped at `max_positions` per wallet to
    keep API usage/runtime sane -- biased toward the most recent positions,
    which is also the more relevant signal for "is this wallet still sharp."
    """
    recent = sorted(closed_positions, key=lambda p: p.get("timestamp", 0), reverse=True)[:max_positions]
    clvs = []
    for p in recent:
        result = compute_clv_for_position(p)
        if result.clv is not None:
            clvs.append(result.clv)
    avg = sum(clvs) / len(clvs) if clvs else None
    return avg, len(clvs), len(recent)
