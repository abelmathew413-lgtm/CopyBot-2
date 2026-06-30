"""
Discovery stage: build the universe of candidate wallets to score.

We can't just take the top of the ALL-time PNL leaderboard -- by definition
those are the most visible wallets, the opposite of what we want. Instead we
pull across multiple categories AND multiple time periods (DAY/WEEK/MONTH),
since a genuinely good but under-the-radar wallet is more likely to surface
on a WEEK or MONTH cut than to ever crack the ALL-time top ranks.

We keep each wallet's best (lowest-numbered) rank per category+period --
this doubles as the "leaderboard_ranks" input radar_filter.py needs to check
visibility.
"""

from __future__ import annotations

import polymarket_client as pc

DISCOVERY_PERIODS = ["WEEK", "MONTH"]  # DAY is too noisy, ALL is too visible-by-definition
DISCOVERY_CATEGORIES = ["OVERALL", "POLITICS", "SPORTS", "CRYPTO", "CULTURE", "ECONOMICS"]


def discover_candidates(
    periods: list[str] = None,
    categories: list[str] = None,
    per_call_limit: int = 200,
) -> dict[str, dict]:
    """
    Returns {proxyWallet: {"leaderboard_ranks": {"CATEGORY_PERIOD": rank, ...},
                            "best_pnl_seen": float, "best_vol_seen": float}}
    """
    periods = periods or DISCOVERY_PERIODS
    categories = categories or DISCOVERY_CATEGORIES

    candidates: dict[str, dict] = {}

    for category in categories:
        for period in periods:
            entries = pc.get_leaderboard_paged(
                category=category, time_period=period, order_by="PNL", max_results=per_call_limit
            )
            for entry in entries:
                wallet = entry.get("proxyWallet")
                if not wallet:
                    continue
                rank = int(entry.get("rank", 0)) if entry.get("rank") is not None else None
                slot = candidates.setdefault(
                    wallet, {"leaderboard_ranks": {}, "best_pnl_seen": 0.0, "best_vol_seen": 0.0, "userName": entry.get("userName")}
                )
                if rank is not None:
                    key = f"{category}_{period}"
                    existing = slot["leaderboard_ranks"].get(key)
                    if existing is None or rank < existing:
                        slot["leaderboard_ranks"][key] = rank
                slot["best_pnl_seen"] = max(slot["best_pnl_seen"], entry.get("pnl", 0) or 0)
                slot["best_vol_seen"] = max(slot["best_vol_seen"], entry.get("vol", 0) or 0)

    return candidates
