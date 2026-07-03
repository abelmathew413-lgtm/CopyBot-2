"""
Entry-price recommendation for open positions of wallets you've chosen to
watch. Per your spec, this is a MIX of:
  (a) an independent fair-value estimate from current market data, and
  (b) the tracked wallet's own entry price, flagged if price has drifted
      too far from that entry to still be a sensible "copy" at today's price.

This is informational/heuristic, not a guarantee -- prediction-market prices
are themselves probability estimates, and chasing a moved price changes your
risk/reward versus the original trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import polymarket_client as pc

# If current price has moved more than this many percentage points (absolute,
# in probability terms) away from the tracked wallet's entry, flag it as
# "chasing" rather than "copying."
DEFAULT_DRIFT_FLAG_THRESHOLD = 0.08  # 8 cents


@dataclass
class EntryRecommendation:
    market_title: str
    outcome: str
    their_entry_price: float
    current_price: float
    fair_value_estimate: Optional[float]
    drift: float  # current_price - their_entry_price (signed, in probability terms)
    drift_pct_of_entry: Optional[float]
    suggested_entry_range: tuple[float, float]
    flagged_chasing: bool
    note: str
    conviction: float = 0.0      # this position's size relative to the wallet's whole open book, 0..1
    pick_score: float = 0.0      # conviction, penalized hard if the price has drifted (chasing)
    recommended: bool = False    # set by rank_entries_for_copy, not at construction time


def _fair_value_from_book(token_id: Optional[str]) -> Optional[float]:
    """
    Independent fair-value estimate: use the CLOB midpoint (avg of best bid/ask)
    as of right now. This is independent of the tracked wallet's own entry --
    it reflects current aggregate market sentiment, not their specific fill.
    """
    if not token_id:
        return None
    return pc.get_midpoint(token_id)


def recommend_entry(
    position: dict,
    token_id: Optional[str] = None,
    drift_threshold: float = DEFAULT_DRIFT_FLAG_THRESHOLD,
) -> EntryRecommendation:
    """
    `position` is a Position dict from /positions (has avgPrice = their entry,
    curPrice = latest known price from Polymarket's own data). `token_id` is
    the outcome token (the `asset` field) -- pass it to also pull a live CLOB
    midpoint as a cross-check fair-value estimate.
    """
    their_entry = float(position.get("avgPrice", 0) or 0)
    current_price = float(position.get("curPrice", their_entry) or their_entry)

    fair_value = _fair_value_from_book(token_id or position.get("asset"))

    drift = current_price - their_entry
    drift_pct = (drift / their_entry) if their_entry > 0 else None
    flagged = abs(drift) > drift_threshold

    # Suggested entry range: blend their entry with the fair-value estimate
    # (if we have one), then widen slightly to a workable band rather than a
    # single price point, since order books move between when you read this
    # and when you'd actually place an order.
    if fair_value is not None:
        center = (their_entry + fair_value) / 2
    else:
        center = their_entry
    band = max(0.01, abs(drift) * 0.5, 0.01)
    suggested_range = (round(max(0.001, center - band), 3), round(min(0.999, center + band), 3))

    if flagged:
        direction = "up" if drift > 0 else "down"
        note = (
            f"Price has moved {direction} {abs(drift):.2f} ({abs(drift_pct or 0):.0%}) "
            f"since their entry of {their_entry:.2f} -- buying now means chasing, "
            f"not replicating their original risk/reward."
        )
    else:
        note = (
            f"Price is close to their entry ({their_entry:.2f} -> {current_price:.2f}); "
            f"replicating their entry is still reasonably representative."
        )

    return EntryRecommendation(
        market_title=position.get("title", ""),
        outcome=position.get("outcome", ""),
        their_entry_price=their_entry,
        current_price=current_price,
        fair_value_estimate=fair_value,
        drift=drift,
        drift_pct_of_entry=drift_pct,
        suggested_entry_range=suggested_range,
        flagged_chasing=flagged,
        note=note,
    )


def recommend_entries_for_wallet(open_positions: list[dict]) -> list[EntryRecommendation]:
    # Only show positions that still have real current value > $0.50 AND
    # a current price between 1 and 99 cents -- positions outside that range
    # are almost certainly already resolved (price snapped to 0 or 1).
    active = [
        p for p in open_positions
        if (p.get("currentValue") or 0) > 0.50
        and 0.01 < float(p.get("curPrice") or 0.5) < 0.99
    ]
    entries = [recommend_entry(p, token_id=p.get("asset")) for p in active]
    # Attach position value so the dashboard bet-sizing formula can use it
    for p, e in zip(active, entries):
        e.__dict__["their_position_value"] = _position_size(p)
    return entries


def _position_size(position: dict) -> float:
    # currentValue is the live mark-to-market size; fall back to totalBought
    # (cost basis) if currentValue isn't populated for some reason.
    return abs(position.get("currentValue", 0) or position.get("totalBought", 0) or 0)


def rank_entries_for_copy(
    open_positions: list[dict],
    entries: list[EntryRecommendation],
    top_k: int = 3,
    chasing_penalty: float = 0.2,
) -> list[EntryRecommendation]:
    """
    Decide which of a wallet's open positions are actually worth copying right
    now, vs. just listing everything neutrally. Two things drive this:

      1. Conviction -- how big this position is relative to the wallet's
         WHOLE open book. A bet that's 40% of their book says more about
         their confidence than one that's 1%.
      2. Price drift -- a position flagged as "chasing" (price has moved far
         from their entry) gets heavily discounted, even if it's a huge
         position, because copying it now means a different risk/reward than
         the trade they actually made.

    Returns the SAME entries, mutated with conviction/pick_score/recommended
    set, re-ordered so recommended picks sort to the top.
    """
    portfolio_value = sum(_position_size(p) for p in open_positions) or 1.0

    for pos, entry in zip(open_positions, entries):
        conviction = min(1.0, _position_size(pos) / portfolio_value)
        entry.conviction = round(conviction, 4)
        entry.pick_score = round(conviction * (chasing_penalty if entry.flagged_chasing else 1.0), 4)

    ranked_by_score = sorted(entries, key=lambda e: e.pick_score, reverse=True)
    top_cutoff = {id(e) for e in ranked_by_score[:top_k] if e.pick_score > 0}

    for entry in entries:
        entry.recommended = id(entry) in top_cutoff

    return sorted(entries, key=lambda e: (not e.recommended, -e.pick_score))