"""
Sanity tests for scoring v2 (CLV/Sharpe/ROI/Drawdown/Sample-size/Win-rate).
CLV requires network calls (price history), so it's mocked here via
clv.compute_avg_clv -- run directly:
    python3 test_scoring.py
"""
import sys, time, random
from unittest.mock import patch
sys.path.insert(0, "src")
import scoring

NOW = int(time.time())
DAY = 86400


def make_closed_positions(n, win_rate, avg_price=0.4, pnl_scale=20, days_back=30, n_markets=None):
    """n_markets=None -> every trade is its own market (max diversity).
    n_markets=1 -> all trades in the same market (min diversity, e.g. a grinder)."""
    out = []
    for i in range(n):
        is_win = random.random() < win_rate
        pnl = pnl_scale * random.uniform(0.5, 1.5) if is_win else -pnl_scale * 0.4 * random.uniform(0.5, 1.5)
        market_id = f"m{i % n_markets}" if n_markets else f"m{i}"
        out.append({
            "conditionId": market_id,
            "avgPrice": avg_price,
            "totalBought": pnl_scale,
            "realizedPnl": pnl,
            "timestamp": NOW - random.randint(0, days_back) * DAY,
        })
    return out


def make_trades(n_per_day, days):
    return [{"timestamp": NOW - d * DAY - i * 60, "side": "BUY"} for d in range(days) for i in range(n_per_day)]


random.seed(7)

# CLV is mocked identically (no data) for every case below UNLESS overridden,
# so these tests isolate the other 6 components first.
with patch.object(scoring.clv_module, "compute_avg_clv", return_value=(None, 0, 0)):

    print("=" * 70)
    print("CASE 1: Skilled, diversified, low-drawdown wallet")
    skilled = make_closed_positions(120, win_rate=0.62, pnl_scale=15, days_back=30)
    r1 = scoring.score_wallet(skilled, make_trades(3, 30), open_positions_count=40)
    print(f"  composite={r1.composite:.3f}  weights_used={ {k: round(v,2) for k,v in r1.weights_used.items()} }")
    print(f"  sharpe={r1.diagnostics['sharpe']:.3f}  roi_lifetime={r1.diagnostics['roi_lifetime']:.3f}  "
          f"drawdown_frac={r1.diagnostics['max_drawdown_fraction']:.3f}  n_markets={r1.diagnostics['n_distinct_markets']}")

    print()
    print("=" * 70)
    print("CASE 2: Same win rate/ROI profile, but ALL trades in ONE market")
    print("        (should score worse on sample_size due to zero diversity)")
    concentrated = make_closed_positions(120, win_rate=0.62, pnl_scale=15, days_back=30, n_markets=1)
    r2 = scoring.score_wallet(concentrated, make_trades(3, 30), open_positions_count=40)
    print(f"  composite={r2.composite:.3f}")
    print(f"  sample_size raw component: {r2.components_used.get('sample_size')}")
    print(f"  (case 1 sample_size raw): {r1.components_used.get('sample_size')}")

    print()
    print("=" * 70)
    print("CASE 3: One huge lucky win, mostly losers otherwise -- should show")
    print("        up as a large drawdown swing / poor Sharpe despite high ROI")
    lucky = make_closed_positions(40, win_rate=0.35, avg_price=0.3, pnl_scale=8, days_back=30)
    lucky.append({"conditionId": "the_one_big_win", "avgPrice": 0.05, "totalBought": 500,
                  "realizedPnl": 9500, "timestamp": NOW - 25 * DAY})
    r3 = scoring.score_wallet(lucky, make_trades(2, 30), open_positions_count=15)
    print(f"  composite={r3.composite:.3f}  roi_lifetime={r3.diagnostics['roi_lifetime']:.3f}  "
          f"sharpe={r3.diagnostics['sharpe']}  drawdown_frac={r3.diagnostics['max_drawdown_fraction']:.3f}  "
          f"capital_concentration_hhi={r3.diagnostics['capital_concentration_hhi']:.3f}")

    print()
    print("=" * 70)
    print("CASE 4: Hyperactive bot (80 trades/day) -- disqualified regardless of stats")
    bot_closed = make_closed_positions(300, win_rate=0.55, pnl_scale=5, days_back=30)
    r4 = scoring.score_wallet(bot_closed, make_trades(80, 30), open_positions_count=60)
    print(f"  composite={r4.composite:.3f}  disqualified={r4.disqualified}  reasons={r4.disqualify_reasons}")

    print()
    print("=" * 70)
    print("CASE 5: Dormant wallet (almost no trades) -- disqualified")
    dormant_closed = make_closed_positions(2, win_rate=1.0, pnl_scale=50, days_back=30)
    r5 = scoring.score_wallet(dormant_closed, make_trades(1, 2), open_positions_count=5)
    print(f"  composite={r5.composite:.3f}  disqualified={r5.disqualified}  reasons={r5.disqualify_reasons}")

    assert r1.composite > r2.composite, "FAIL: diversified sample should beat single-market sample at equal ROI/win-rate"
    assert r3.diagnostics["roi_lifetime"] > 0, "sanity: the lucky wallet should still show positive raw ROI"
    assert r1.composite > r3.composite, (
        "FAIL: the genuinely skilled diversified wallet should outrank the one-lucky-hit wallet "
        "despite the lucky wallet's much higher raw ROI -- this is the core thing the capital-"
        "concentration fix was added to fix"
    )
    assert r4.disqualified, "FAIL: hyperactive bot should be disqualified"
    assert r5.disqualified, "FAIL: dormant wallet should be disqualified"
    assert r1.composite > 0, "FAIL: a genuinely good, diversified wallet should score positive"

print()
print("=" * 70)
print("CASE 6: Weight renormalization -- CLV available for this wallet only")
with patch.object(scoring.clv_module, "compute_avg_clv", return_value=(0.08, 30, 30)):
    skilled_with_clv = make_closed_positions(120, win_rate=0.62, pnl_scale=15, days_back=30)
    r6 = scoring.score_wallet(skilled_with_clv, make_trades(3, 30), open_positions_count=40)
print(f"  composite={r6.composite:.3f}")
print(f"  weights_used sums to: {sum(r6.weights_used.values()):.4f} (should be 1.0)")
print(f"  clv weight used: {r6.weights_used.get('clv'):.4f} (should be exactly 0.25, since ALL 7 components present)")
assert abs(sum(r6.weights_used.values()) - 1.0) < 1e-9, "FAIL: renormalized weights must sum to 1.0"
assert abs(r6.weights_used.get("clv", 0) - 0.25) < 1e-9, "FAIL: with all components present, CLV weight should be the full 25%"

print()
print("=" * 70)
print("RANKING CHECK:")
for name, r in [("skilled_diversified", r1), ("skilled_concentrated", r2), ("lucky_one_hit", r3)]:
    print(f"  {name:24s} composite={r.composite:7.3f}")
print()
print("ALL ASSERTIONS PASSED.")
