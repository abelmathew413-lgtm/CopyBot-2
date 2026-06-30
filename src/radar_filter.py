"""
"Under the radar" filtering.

Polymarket's official public API does not expose follower counts or
profile-view counts (confirmed against the documented Gamma /public-profile
schema). So this filter relies only on signals that ARE reliably available:

  1. Account age          (createdAt from /public-profile)
  2. Portfolio efficiency  (PnL relative to capital deployed/portfolio size --
                            a wallet making strong returns on a small book is
                            "under the radar" almost by definition: it hasn't
                            attracted enough copy-capital to need a big book)
  3. Leaderboard visibility (rank position when it does appear on
                            DAY/WEEK/MONTH/ALL leaderboards -- being absent
                            from ALL-time top ranks while still scoring well
                            on our own model is itself a signal of being
                            early/undiscovered)

If a views/followers field is later confirmed to exist on Polymarket's own
(non-API) profile pages, it should be wired in as a DISPLAY-ONLY bonus field,
never as a gating filter -- since it would depend on a scrape that can fail
silently, and this filter must keep working even when that scrape is down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class RadarAssessment:
    is_under_radar: bool
    account_age_days: Optional[float]
    portfolio_value: float
    capital_efficiency: Optional[float]  # realized PnL / portfolio value, lifetime-ish proxy
    best_leaderboard_rank: Optional[int]  # lowest (best) rank seen across categories/periods, if any
    profile_views: Optional[int]  # scraped from polymarket.com itself; None = unknown
    reasons: list[str]


def _parse_created_at(created_at: Optional[str]) -> Optional[datetime]:
    if not created_at:
        return None
    try:
        # API returns ISO 8601; handle the common 'Z' suffix
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def assess_radar_status(
    profile: Optional[dict],
    open_positions: list[dict],
    closed_positions: list[dict],
    leaderboard_ranks: Optional[dict[str, int]] = None,  # e.g. {"OVERALL_WEEK": 340}
    profile_views: Optional[int] = None,  # from views_scraper.get_profile_views(); None = unknown
    max_views: Optional[int] = 250,  # hard cap per spec; set None to disable this gate entirely
    max_account_age_days: Optional[float] = None,  # None = no age requirement, just informative
    min_capital_efficiency: float = 0.05,
    max_acceptable_rank: int = 1000,  # API leaderboard offset cap -- being ranked deep or absent counts
) -> RadarAssessment:
    leaderboard_ranks = leaderboard_ranks or {}
    reasons: list[str] = []

    created_at = _parse_created_at(profile.get("createdAt") if profile else None)
    account_age_days = None
    if created_at:
        account_age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400

    portfolio_value = sum(p.get("currentValue", 0) for p in open_positions)
    capital_at_risk_lifetime = sum(abs(p.get("totalBought", 0)) for p in closed_positions) + sum(
        abs(p.get("totalBought", 0)) for p in open_positions
    )
    realized_pnl_lifetime = sum(p.get("realizedPnl", 0) for p in closed_positions)

    capital_efficiency = None
    if capital_at_risk_lifetime > 0:
        capital_efficiency = realized_pnl_lifetime / capital_at_risk_lifetime

    ranks = [r for r in leaderboard_ranks.values() if r is not None]
    best_rank = min(ranks) if ranks else None

    is_under_radar = True

    # Hard views gate: <=250 views on their own Polymarket profile page.
    # Unknown views (scrape failed, no public handle) are treated as a FAIL,
    # not a pass -- since the whole point of this gate is "don't surface
    # someone already getting attention," it's safer to exclude an unverified
    # wallet than risk surfacing a wallet that's actually highly visible.
    if max_views is not None:
        if profile_views is None:
            is_under_radar = False
            reasons.append("view count unknown (scrape failed or no public handle) -- excluded conservatively")
        elif profile_views > max_views:
            is_under_radar = False
            reasons.append(f"{profile_views:,} profile views exceeds {max_views} cap")

    # Small/efficient portfolio is the core signal: high return relative to
    # capital deployed, without needing a large book to do it.
    if capital_efficiency is not None and capital_efficiency < min_capital_efficiency:
        is_under_radar = False
        reasons.append(f"capital efficiency {capital_efficiency:.2%} below {min_capital_efficiency:.0%} threshold")

    # If they DO show up on a leaderboard, being ranked very high (e.g. top 20
    # overall, all-time) is the opposite of under-the-radar.
    if best_rank is not None and best_rank <= 20:
        is_under_radar = False
        reasons.append(f"ranked #{best_rank} on a public leaderboard (too visible)")

    # Optional age ceiling -- only enforced if the caller wants strictly "new"
    # wallets; otherwise age is informative only (older wallets can still be
    # under-the-radar if they're simply quiet/small).
    if max_account_age_days is not None and account_age_days is not None:
        if account_age_days > max_account_age_days:
            is_under_radar = False
            reasons.append(f"account age {account_age_days:.0f}d exceeds {max_account_age_days:.0f}d cap")

    if not reasons:
        reasons.append("passes: efficient/small portfolio, low view count, not top-ranked publicly")

    return RadarAssessment(
        is_under_radar=is_under_radar,
        account_age_days=account_age_days,
        portfolio_value=portfolio_value,
        capital_efficiency=capital_efficiency,
        best_leaderboard_rank=best_rank,
        profile_views=profile_views,
        reasons=reasons,
    )
