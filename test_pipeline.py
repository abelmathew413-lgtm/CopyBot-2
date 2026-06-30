"""
End-to-end test of the full pipeline (discovery -> scoring -> radar filter ->
pricing -> report) using mocked API responses, since this sandbox's network
allowlist blocks Polymarket's domains. This validates that all modules wire
together correctly; it does NOT validate that Polymarket's live API still
matches the documented shapes (test that separately once you have network
access, e.g. `python3 src/main.py --max-candidates 20`).
"""
import sys, time, random
from unittest.mock import patch
sys.path.insert(0, "src")

import main as pipeline

NOW = int(time.time())
DAY = 86400

FAKE_WALLETS = [f"0x{i:040x}" for i in range(1, 6)]


def fake_leaderboard_paged(category="OVERALL", time_period="WEEK", order_by="PNL", max_results=500):
    # Return a small fixed set so discovery terminates fast in the test.
    # Ranks simulate realistic discovery: wallet1 is a top-1 whale (too visible),
    # wallet0/2/4 sit deeper in the leaderboard (plausibly under-the-radar),
    # wallet3 is irrelevant here since it gets disqualified on scoring anyway.
    realistic_ranks = {0: 340, 1: 1, 2: 55, 3: 12, 4: 812}
    return [
        {"rank": str(realistic_ranks[i]), "proxyWallet": w, "userName": f"trader{i}",
         "pnl": 5000 - i * 500, "vol": 20000 - i * 1000}
        for i, w in enumerate(FAKE_WALLETS)
    ]


def fake_public_profile(address):
    # wallet 0 = brand-new small efficient wallet (should pass everything)
    # wallet 1 = looks great but ranked #1 -> should fail radar (too visible)
    idx = FAKE_WALLETS.index(address)
    created = {
        0: "2025-11-01T00:00:00Z",
        1: "2024-01-01T00:00:00Z",
        2: "2025-06-01T00:00:00Z",
        3: "2025-01-01T00:00:00Z",
        4: "2025-08-01T00:00:00Z",
    }[idx]
    return {"createdAt": created, "proxyWallet": address}


def fake_positions(user, **kwargs):
    idx = FAKE_WALLETS.index(user)
    if idx == 0:
        return [{"currentValue": 800, "totalBought": 700, "title": "Will it rain", "outcome": "Yes",
                  "avgPrice": 0.35, "curPrice": 0.37, "asset": f"tok{idx}"}]
    if idx == 3:
        # over the 150-position cap -> should be disqualified by scoring, not even reach radar
        return [{"currentValue": 10, "totalBought": 10, "title": f"m{j}", "outcome": "Yes",
                  "avgPrice": 0.5, "curPrice": 0.5, "asset": f"tok{idx}_{j}"} for j in range(200)]
    return [{"currentValue": 5000, "totalBought": 4500, "title": "Big market", "outcome": "Yes",
              "avgPrice": 0.4, "curPrice": 0.42, "asset": f"tok{idx}"}]


def fake_closed_positions(user, **kwargs):
    idx = FAKE_WALLETS.index(user)
    random.seed(idx)
    n = 60 if idx != 4 else 0  # wallet 4 has no resolved trades -> should fail (no track record)
    out = []
    win_rate = {0: 0.65, 1: 0.70, 2: 0.55, 3: 0.55}.get(idx, 0.5)
    for i in range(n):
        is_win = random.random() < win_rate
        pnl = random.uniform(5, 20) if is_win else -random.uniform(2, 10)
        out.append({"conditionId": f"c{idx}_{i}", "avgPrice": 0.4, "totalBought": 15,
                     "realizedPnl": pnl, "timestamp": NOW - random.randint(0, 30) * DAY})
    return out


def fake_trades(user, **kwargs):
    idx = FAKE_WALLETS.index(user)
    if idx == 3:
        # absurd cadence -> disqualified
        return [{"timestamp": NOW - d * DAY - s * 60, "side": "BUY"} for d in range(30) for s in range(60)]
    n_per_day = {0: 3, 1: 4, 2: 2, 4: 0}.get(idx, 2)
    return [{"timestamp": NOW - d * DAY - i * 60, "side": "BUY"} for d in range(30) for i in range(n_per_day)]


def fake_midpoint(token_id):
    return 0.38


def fake_get_profile_views(handle):
    # wallet0 = clearly under-the-radar by views too; wallet2 = exceeds the 250 cap
    views_by_idx = {0: 180, 1: 50000, 2: 900, 3: 10, 4: 5}
    idx = int(handle.replace("trader", ""))  # fake userNames are "trader0".."trader4"
    return views_by_idx.get(idx)


def fake_compute_avg_clv(closed_positions, max_positions=40):
    # No real price-history network access in this test -- treat CLV as
    # universally unavailable, which also exercises the weight-renormalization
    # path (every wallet here is scored on the remaining 6 components).
    return None, 0, 0


with patch.object(pipeline.discovery.pc, "get_leaderboard_paged", fake_leaderboard_paged), \
     patch.object(pipeline.pc, "get_public_profile", fake_public_profile), \
     patch.object(pipeline.pc, "get_all_current_positions", fake_positions), \
     patch.object(pipeline.pc, "get_all_closed_positions", fake_closed_positions), \
     patch.object(pipeline.pc, "get_all_trades", fake_trades), \
     patch.object(pipeline.pc, "get_midpoint", fake_midpoint), \
     patch.object(pipeline.views_scraper, "get_profile_views", fake_get_profile_views), \
     patch("clv.compute_avg_clv", fake_compute_avg_clv):
    report = pipeline.build_report(top_n=8, max_candidates=10, verbose=True)

print()
print("=" * 70)
print("REPORT SUMMARY")
print(f"discovered={report['n_candidates_discovered']} evaluated={report['n_candidates_evaluated']} "
      f"passed_filters={report['n_passed_filters']} errors={report['n_errors']}")
for w in report["wallets"]:
    print(f"  {w['wallet'][:10]}... composite={w['score']['composite']:.3f} "
          f"age={w['radar']['account_age_days']}d eff={w['radar']['capital_efficiency']}")

# Expected: wallet0 (new, efficient, unranked-enough) passes.
# wallet1 (rank #1) should be filtered out by radar (too visible).
# wallet3 (>150 positions, insane trade cadence) should be filtered by scoring.
# wallet4 (zero resolved trades) should be filtered by scoring (no activity).
passed_wallets = {w["wallet"] for w in report["wallets"]}
assert FAKE_WALLETS[0] in passed_wallets, "FAIL: wallet0 (good, under-radar) should have passed"
assert FAKE_WALLETS[1] not in passed_wallets, "FAIL: wallet1 (rank #1, too visible) should have been filtered"
assert FAKE_WALLETS[3] not in passed_wallets, "FAIL: wallet3 (over position/trade caps) should have been filtered"
assert FAKE_WALLETS[4] not in passed_wallets, "FAIL: wallet4 (no track record) should have been filtered"
print()
print("ALL PIPELINE ASSERTIONS PASSED.")
