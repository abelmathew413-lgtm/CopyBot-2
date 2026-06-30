"""
Scoring engine v2 -- weighted composite per spec:

    25% CLV (closing line value)
    20% Sharpe ratio (lifetime + trailing-90d, blended)
    15% Lifetime ROI
    15% 90-day ROI
    10% Max drawdown (smaller is better)
    10% Sample size (resolved-trade count, discounted for low market diversity)
     5% Win rate (Wilson-adjusted)
    -----
    100%

Design notes:
  - Every component is rescaled onto roughly [-1, 1] before blending, so the
    weights mean what they say regardless of each metric's native units.
  - If a component can't be computed for a wallet (most commonly CLV, given
    Polymarket's own data gaps -- see clv.py), that component is DROPPED and
    its weight is redistributed proportionally across whatever components
    *are* available for that wallet, rather than either zeroing it out
    (unfairly punishing the wallet for Polymarket's missing data) or
    skipping the wallet entirely (per your explicit choice).
  - Operational disqualification (>150 open positions, >50 or <1 trades/day)
    is checked BEFORE the composite is computed and short-circuits to -1.
  - Account age and "number of markets" are intentionally NOT weighted here:
    account age belongs in the under-the-radar gate (radar_filter.py), and
    "number of markets" is folded into the Sample Size component below
    (a wallet's sample is only as good as it is diversified).
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Optional

import clv as clv_module

SECONDS_PER_DAY = 86400

WEIGHTS = {
    "clv": 0.25,
    "sharpe": 0.20,
    "roi_lifetime": 0.15,
    "roi_90d": 0.15,
    "drawdown": 0.10,
    "sample_size": 0.10,
    "win_rate": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Small math helpers
# ---------------------------------------------------------------------------

def _wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p_hat = wins / n
    denom = 1 + z ** 2 / n
    center = p_hat + z ** 2 / (2 * n)
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z ** 2 / (4 * n)) / n)
    return max(0.0, (center - margin) / denom)


def _to_unit_range(x: float) -> float:
    """Squash an unbounded value onto (-1, 1) without clipping outliers to a wall."""
    return math.tanh(x)


def _filter_window(items: list[dict], days: Optional[int], now_ts: int, key: str = "timestamp") -> list[dict]:
    if days is None:
        return items
    cutoff = now_ts - days * SECONDS_PER_DAY
    return [p for p in items if p.get(key, 0) >= cutoff]


# ---------------------------------------------------------------------------
# Individual components
# ---------------------------------------------------------------------------

def _roi(closed_positions: list[dict]) -> Optional[float]:
    if not closed_positions:
        return None
    capital = sum(abs(p.get("totalBought", 0)) for p in closed_positions)
    pnl = sum(p.get("realizedPnl", 0) for p in closed_positions)
    return (pnl / capital) if capital > 0 else None


def _sharpe(closed_positions: list[dict]) -> Optional[float]:
    """Per-trade return series -> mean/stdev. Needs >=2 trades with capital deployed."""
    returns = []
    for p in closed_positions:
        capital = abs(p.get("totalBought", 0))
        if capital > 0:
            returns.append(p.get("realizedPnl", 0) / capital)
    if len(returns) < 2:
        return None
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    return mean / stdev


def _blended_sharpe(closed_positions: list[dict], now_ts: int) -> Optional[float]:
    lifetime = _sharpe(closed_positions)
    trailing_90 = _sharpe(_filter_window(closed_positions, 90, now_ts))
    candidates = [s for s in (lifetime, trailing_90) if s is not None]
    if not candidates:
        return None
    return sum(candidates) / len(candidates)


def _max_drawdown_fraction(closed_positions: list[dict]) -> Optional[float]:
    """
    Peak-to-trough decline of the cumulative realized-PnL curve, expressed as
    a fraction of total capital deployed (so it's comparable across wallets
    of different sizes). Returns None if there's no meaningful capital base.
    """
    if not closed_positions:
        return None
    ordered = sorted(closed_positions, key=lambda p: p.get("timestamp", 0))
    capital = sum(abs(p.get("totalBought", 0)) for p in ordered)
    if capital <= 0:
        return None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in ordered:
        cumulative += p.get("realizedPnl", 0)
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd / capital


def _capital_concentration_hhi(closed_positions: list[dict]) -> float:
    """
    Herfindahl-Hirschman index over CAPITAL DEPLOYED per market (not profit --
    capital is the more honest denominator here, since it doesn't retroactively
    look different depending on whether the bet happened to win). Ranges from
    ~1/n_markets (capital spread evenly) to 1.0 (one market ate the whole book).
    This is what actually catches "one huge bet carried the whole record," which
    market-count diversity alone can miss if that one bet is large relative to
    everything else the wallet did.
    """
    capital_by_market: dict[str, float] = {}
    for p in closed_positions:
        cid = p.get("conditionId", "unknown")
        capital_by_market[cid] = capital_by_market.get(cid, 0.0) + abs(p.get("totalBought", 0))
    total = sum(capital_by_market.values())
    if total <= 0:
        return 0.0
    return sum((c / total) ** 2 for c in capital_by_market.values())


def _sample_size_score(
    n_resolved: int, n_distinct_markets: int, capital_concentration_hhi: float = 0.0, k: float = 25.0
) -> Optional[float]:
    """
    Diminishing-returns score in [0, 1) that rewards trade COUNT, market
    DIVERSITY, and penalizes CAPITAL CONCENTRATION -- 50 trades in one market
    is a weak sample (low market diversity), and so is 50 trades across 50
    markets if one single bet was 60% of the money deployed (high capital
    concentration) -- both mean the "sample" is really riding on one thing.
    """
    if n_resolved == 0:
        return None
    diversity_ratio = min(1.0, n_distinct_markets / n_resolved)
    effective_n = n_resolved * math.sqrt(diversity_ratio) * (1 - capital_concentration_hhi)
    return 1 - math.exp(-effective_n / k)


def _win_rate_adj(closed_positions: list[dict]) -> Optional[float]:
    if not closed_positions:
        return None
    wins = sum(1 for p in closed_positions if p.get("realizedPnl", 0) > 0)
    return _wilson_lower_bound(wins, len(closed_positions))


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    composite: float
    components_used: dict[str, float]      # name -> raw (pre-weight) value, only what was available
    weights_used: dict[str, float]          # name -> renormalized weight actually applied
    consistency_adjustment: float           # small tiebreaker, OUTSIDE the 100% weighting -- see note below
    disqualified: bool = False
    disqualify_reasons: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)  # raw numbers for the dashboard (ROI%, Sharpe, etc.)


def score_wallet(
    closed_positions: list[dict],
    trades: list[dict],
    open_positions_count: int,
    now_ts: Optional[int] = None,
) -> ScoreBreakdown:
    now_ts = now_ts or int(time.time())
    reasons: list[str] = []
    disqualified = False

    if open_positions_count > 150:
        disqualified = True
        reasons.append(f"open positions {open_positions_count} > 150 cap")

    trades_30d = _filter_window(trades, 30, now_ts)
    avg_trades_per_day = len(trades_30d) / 30.0
    active_days_30d = len({t["timestamp"] // SECONDS_PER_DAY for t in trades_30d if "timestamp" in t})
    if trades_30d:
        if avg_trades_per_day > 50:
            disqualified = True
            reasons.append(f"avg {avg_trades_per_day:.1f} trades/day > 50 cap")
        elif avg_trades_per_day < 1 and active_days_30d < 15:
            disqualified = True
            reasons.append(f"avg {avg_trades_per_day:.2f} trades/day < 1 minimum")

    closed_90d = _filter_window(closed_positions, 90, now_ts)
    closed_30d = _filter_window(closed_positions, 30, now_ts)
    closed_180d = _filter_window(closed_positions, 180, now_ts)

    avg_clv, n_clv_with_data, n_clv_attempted = (None, 0, 0)
    if not disqualified:  # don't burn API calls/time on CLV for a wallet we're rejecting anyway
        avg_clv, n_clv_with_data, n_clv_attempted = clv_module.compute_avg_clv(closed_positions)

    sharpe = _blended_sharpe(closed_positions, now_ts)
    roi_lifetime = _roi(closed_positions)
    roi_90d = _roi(closed_90d)
    roi_30d = _roi(closed_30d)
    roi_180d = _roi(closed_180d)
    drawdown_frac = _max_drawdown_fraction(closed_positions)
    n_distinct_markets = len({p.get("conditionId") for p in closed_positions if p.get("conditionId")})
    capital_concentration = _capital_concentration_hhi(closed_positions)
    sample_score = _sample_size_score(len(closed_positions), n_distinct_markets, capital_concentration)
    win_rate = _win_rate_adj(closed_positions)

    # Map each raw value onto roughly [-1, 1] for blending.
    raw_components: dict[str, Optional[float]] = {
        "clv": _to_unit_range(avg_clv * 4) if avg_clv is not None else None,  # *4: CLV is naturally small (price units)
        "sharpe": _to_unit_range(sharpe) if sharpe is not None else None,
        "roi_lifetime": _to_unit_range(roi_lifetime) if roi_lifetime is not None else None,
        "roi_90d": _to_unit_range(roi_90d) if roi_90d is not None else None,
        "drawdown": (1 - 2 * min(1.0, drawdown_frac)) if drawdown_frac is not None else None,  # lower dd -> closer to +1
        "sample_size": (2 * sample_score - 1) if sample_score is not None else None,
        "win_rate": (2 * win_rate - 1) if win_rate is not None else None,
    }

    available = {k: v for k, v in raw_components.items() if v is not None}
    composite = 0.0
    weights_used: dict[str, float] = {}
    if not disqualified and available:
        total_weight = sum(WEIGHTS[k] for k in available)
        for k, v in available.items():
            w = WEIGHTS[k] / total_weight  # renormalized
            weights_used[k] = w
            composite += w * v

    # Small, OUT-OF-BAND consistency tiebreaker (not part of the 100%): a tiny
    # nudge for wallets that are positive across 30d/90d/lifetime simultaneously,
    # purely to break ties between otherwise-similar composites. +/-0.02 max.
    consistency_adjustment = 0.0
    if roi_30d is not None and roi_90d is not None and roi_lifetime is not None:
        if roi_30d > 0 and roi_90d > 0 and roi_lifetime > 0:
            consistency_adjustment = 0.02
        elif roi_30d < 0 and roi_90d < 0 and roi_lifetime < 0:
            consistency_adjustment = -0.02
    composite += consistency_adjustment

    if disqualified:
        composite = -1.0

    diagnostics = {
        "avg_clv": avg_clv,
        "n_clv_with_data": n_clv_with_data,
        "n_clv_attempted": n_clv_attempted,
        "sharpe": sharpe,
        "roi_lifetime": roi_lifetime,
        "roi_180d": roi_180d,
        "roi_90d": roi_90d,
        "roi_30d": roi_30d,
        "max_drawdown_fraction": drawdown_frac,
        "n_resolved": len(closed_positions),
        "n_distinct_markets": n_distinct_markets,
        "capital_concentration_hhi": capital_concentration,
        "win_rate_adj": win_rate,
        "avg_trades_per_day_30d": avg_trades_per_day,
    }

    return ScoreBreakdown(
        composite=composite,
        components_used=available,
        weights_used=weights_used,
        consistency_adjustment=consistency_adjustment,
        disqualified=disqualified,
        disqualify_reasons=reasons,
        diagnostics=diagnostics,
    )


def composite_to_100(composite: float) -> int:
    """
    Maps the composite (roughly -1..+1, occasionally a hair beyond due to the
    +/-0.02 consistency nudge) onto a 1-100 display score. Linear, not
    curved -- a wallet at composite 0.0 (net-neutral across all factors)
    lands at 50, not because "average" should feel mediocre, but because
    that's literally what it is: a transparent midpoint, not a sales number.
    Disqualified wallets won't reach the dashboard at all (they're filtered
    out before this point), so in practice you'll mostly see scores well
    above 50 for anything that made the cut.
    """
    clipped = max(-1.0, min(1.0, composite))
    return round(((clipped + 1.0) / 2.0) * 99) + 1
