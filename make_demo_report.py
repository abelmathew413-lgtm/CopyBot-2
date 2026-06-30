import json
from pathlib import Path
from datetime import datetime, timezone

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "duration_seconds": 142.3,
    "n_candidates_discovered": 487,
    "n_candidates_evaluated": 150,
    "n_passed_filters": 2,
    "n_errors": 1,
    "errors": [{"wallet": "0xdead...beef", "error": "timeout"}],
    "wallets": [
        {
            "wallet": "0x71a3f9c2e8b4d6a1c5f0e9d8b7a6c5f4e3d2c1b0",
            "userName": "quietquant",
            "display_name": "quietquant",
            "profile_url": "https://polymarket.com/@quietquant",
            "score": {
                "composite": 0.412,
                "weights_used": {"clv": 0.25, "sharpe": 0.20, "roi_lifetime": 0.15, "roi_90d": 0.15,
                                  "drawdown": 0.10, "sample_size": 0.10, "win_rate": 0.05},
                "consistency_adjustment": 0.02,
                "clv": 0.041, "n_clv_with_data": 18, "n_clv_attempted": 22,
                "sharpe": 0.71, "roi_lifetime": 0.34, "roi_180d": 0.29, "roi_90d": 0.38, "roi_30d": 0.41,
                "max_drawdown_fraction": 0.09, "n_resolved": 64, "n_distinct_markets": 41,
                "capital_concentration_hhi": 0.04, "win_rate_adj": 0.61, "avg_trades_per_day_30d": 2.3,
            },
            "radar": {"account_age_days": 41, "portfolio_value": 3120.5, "capital_efficiency": 0.18,
                      "best_leaderboard_rank": None, "profile_views": 180,
                      "reasons": ["passes: efficient/small portfolio, low view count, not top-ranked publicly"]},
            "open_positions_count": 12,
            "entry_recommendations": [
                {"market_title": "Will the Fed cut rates in September?", "outcome": "Yes",
                 "their_entry_price": 0.34, "current_price": 0.36, "fair_value_estimate": 0.355,
                 "suggested_entry_range": [0.34, 0.36], "flagged_chasing": False,
                 "note": "Price is close to their entry (0.34 -> 0.36); replicating their entry is still reasonably representative.",
                 "conviction": 0.42, "pick_score": 0.42, "recommended": True},
                {"market_title": "Lakers to make the playoffs", "outcome": "No",
                 "their_entry_price": 0.22, "current_price": 0.51, "fair_value_estimate": 0.49,
                 "suggested_entry_range": [0.22, 0.50], "flagged_chasing": True,
                 "note": "Price has moved up 0.29 (132%) since their entry of 0.22 -- buying now means chasing, not replicating their original risk/reward.",
                 "conviction": 0.18, "pick_score": 0.036, "recommended": False},
            ],
        },
        {
            "wallet": "0x12bb88c4f1e9a7d6c5b4a3f2e1d0c9b8a7f6e5d4",
            "userName": None,
            "display_name": "0x12bb88c4f1...",
            "profile_url": "https://polymarket.com/profile/0x12bb88c4f1e9a7d6c5b4a3f2e1d0c9b8a7f6e5d4",
            "score": {
                "composite": 0.355,
                "weights_used": {"sharpe": 0.27, "roi_lifetime": 0.20, "roi_90d": 0.20,
                                  "drawdown": 0.13, "sample_size": 0.13, "win_rate": 0.07},
                "consistency_adjustment": 0.0,
                "clv": None, "n_clv_with_data": 0, "n_clv_attempted": 12,
                "sharpe": 0.44, "roi_lifetime": 0.21, "roi_180d": 0.19, "roi_90d": 0.21, "roi_30d": -0.05,
                "max_drawdown_fraction": 0.14, "n_resolved": 88, "n_distinct_markets": 35,
                "capital_concentration_hhi": 0.07, "win_rate_adj": 0.58, "avg_trades_per_day_30d": 3.1,
            },
            "radar": {"account_age_days": 210, "portfolio_value": 8800.0, "capital_efficiency": 0.09,
                      "best_leaderboard_rank": 412, "profile_views": 95,
                      "reasons": ["passes: efficient/small portfolio, low view count, not top-ranked publicly"]},
            "open_positions_count": 31,
            "entry_recommendations": [
                {"market_title": "2026 midterm: GOP holds the House", "outcome": "Yes",
                 "their_entry_price": 0.58, "current_price": 0.59, "fair_value_estimate": 0.585,
                 "suggested_entry_range": [0.58, 0.59], "flagged_chasing": False,
                 "note": "Price is close to their entry (0.58 -> 0.59); replicating their entry is still reasonably representative.",
                 "conviction": 0.30, "pick_score": 0.30, "recommended": True},
            ],
        },
    ],
}

(REPORTS_DIR / "latest.json").write_text(json.dumps(report, indent=2))
print("wrote", REPORTS_DIR / "latest.json")
