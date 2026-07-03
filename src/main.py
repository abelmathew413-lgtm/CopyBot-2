"""
End-to-end pipeline: discover candidate wallets -> pull each wallet's
positions/trades/profile -> score -> apply under-the-radar + operational
filters -> rank -> attach entry-price recommendations for the survivors'
open positions -> write a dated JSON report for the dashboard to render.

Run:
    python3 src/main.py --top-n 8 --max-candidates 150

This is the only script meant to be invoked directly (by you, locally, or by
the GitHub Actions workflow every morning).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discovery
import polymarket_client as pc
import pricing
import views_scraper
from radar_filter import assess_radar_status
from scoring import score_wallet, composite_to_100

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def _handle_for(profile: dict | None, username: str | None) -> Optional[str]:
    if profile and profile.get("displayUsernamePublic"):
        h = profile.get("name") or profile.get("pseudonym")
        if h:
            return h
    return username or (profile.get("pseudonym") if profile else None)


def _get_views_best_effort(wallet: str, profile: dict | None, username: str | None) -> Optional[int]:
    """Never let a views-scrape failure take down the whole wallet evaluation."""
    handle = _handle_for(profile, username)
    if not handle:
        return None
    try:
        return views_scraper.get_profile_views(handle)
    except Exception:
        return None


def _profile_url(wallet: str, profile: dict | None, username: str | None) -> str:
    """
    Polymarket profile pages use the form polymarket.com/@<username> when a
    public username/pseudonym exists -- confirmed against a live example
    (polymarket.com/@AccountNames). Falls back to the address-based profile
    path when no username is public. That fallback form is the conventional
    pattern across Polymarket tooling but wasn't directly confirmed against
    a live example here -- worth a quick spot-check the first time you open
    the dashboard, in case a wallet with no public username links incorrectly.
    """
    handle = None
    if profile and profile.get("displayUsernamePublic"):
        handle = profile.get("name") or profile.get("pseudonym")
    handle = handle or username
    if handle:
        return f"https://polymarket.com/@{handle}"
    return f"https://polymarket.com/profile/{wallet}"


def build_report(top_n: int = 8, max_candidates: int = 150, verbose: bool = True) -> dict:
    started = time.time()
    log = (lambda *a: print(*a, file=sys.stderr)) if verbose else (lambda *a: None)

    log("Discovering candidate wallets across leaderboards...")
    candidates = discovery.discover_candidates()
    log(f"  {len(candidates)} unique candidate wallets found")

    # Cap how many we deep-dive on, biased toward wallets that appeared with
    # decent PnL on at least one cut, to keep daily API usage sane.
    ranked_candidates = sorted(candidates.items(), key=lambda kv: kv[1]["best_pnl_seen"], reverse=True)
    ranked_candidates = ranked_candidates[:max_candidates]

    results = []
    errors = []

    for i, (wallet, meta) in enumerate(ranked_candidates):
        try:
            log(f"  [{i+1}/{len(ranked_candidates)}] checking {wallet} ({meta.get('userName')})...")
            profile = pc.get_public_profile(wallet)
            open_positions = pc.get_all_current_positions(wallet)
            closed_positions = pc.get_all_closed_positions(wallet)

            # Cheap gate FIRST: account age, capital efficiency, leaderboard
            # rank, and the views scrape are all far cheaper than full scoring
            # (which includes CLV -- up to ~80 API calls per wallet on its
            # own). Most candidates get rejected right here, so checking this
            # before scoring saves the bulk of the runtime/API budget. This
            # ordering matters a lot once this runs every 20 minutes instead
            # of once a day.
            radar = assess_radar_status(
                profile=profile,
                open_positions=open_positions,
                closed_positions=closed_positions,
                leaderboard_ranks=meta["leaderboard_ranks"],
                profile_views=_get_views_best_effort(wallet, profile, meta.get("userName")),
            )
            if not radar.is_under_radar:
                log(f"      not under-the-radar: {radar.reasons}")
                continue

            trades = pc.get_all_trades(wallet)
            score = score_wallet(
                closed_positions=closed_positions,
                trades=trades,
                open_positions_count=len(open_positions),
            )
            if score.disqualified:
                log(f"      disqualified: {score.disqualify_reasons}")
                continue

            entries = pricing.recommend_entries_for_wallet(open_positions[:25])  # cap per-wallet detail
            entries = pricing.rank_entries_for_copy(open_positions[:25], entries, top_k=3)

            display_name = (
                (profile or {}).get("name")
                or (profile or {}).get("pseudonym")
                or meta.get("userName")
                or wallet[:10] + "..."
            )

            results.append({
                "wallet": wallet,
                "userName": meta.get("userName"),
                "display_name": display_name,
                "profile_url": _profile_url(wallet, profile, meta.get("userName")),
                "score": {
                    "composite": round(score.composite, 4),
                    "score_100": composite_to_100(score.composite),
                    "kelly_edge": round(score.diagnostics["kelly_edge"], 4) if score.diagnostics.get("kelly_edge") is not None else None,
                    "weights_used": {k: round(v, 4) for k, v in score.weights_used.items()},
                    "consistency_adjustment": score.consistency_adjustment,
                    "clv": round(score.diagnostics["avg_clv"], 4) if score.diagnostics["avg_clv"] is not None else None,
                    "n_clv_with_data": score.diagnostics["n_clv_with_data"],
                    "n_clv_attempted": score.diagnostics["n_clv_attempted"],
                    "sharpe": round(score.diagnostics["sharpe"], 4) if score.diagnostics["sharpe"] is not None else None,
                    "roi_lifetime": round(score.diagnostics["roi_lifetime"], 4) if score.diagnostics["roi_lifetime"] is not None else None,
                    "roi_180d": round(score.diagnostics["roi_180d"], 4) if score.diagnostics["roi_180d"] is not None else None,
                    "roi_90d": round(score.diagnostics["roi_90d"], 4) if score.diagnostics["roi_90d"] is not None else None,
                    "roi_30d": round(score.diagnostics["roi_30d"], 4) if score.diagnostics["roi_30d"] is not None else None,
                    "max_drawdown_fraction": round(score.diagnostics["max_drawdown_fraction"], 4) if score.diagnostics["max_drawdown_fraction"] is not None else None,
                    "n_resolved": score.diagnostics["n_resolved"],
                    "n_distinct_markets": score.diagnostics["n_distinct_markets"],
                    "capital_concentration_hhi": round(score.diagnostics["capital_concentration_hhi"], 4),
                    "win_rate_adj": round(score.diagnostics["win_rate_adj"], 4) if score.diagnostics["win_rate_adj"] is not None else None,
                    "avg_trades_per_day_30d": round(score.diagnostics["avg_trades_per_day_30d"], 2),
                },
                "radar": {
                    "account_age_days": round(radar.account_age_days, 1) if radar.account_age_days else None,
                    "portfolio_value": round(radar.portfolio_value, 2),
                    "capital_efficiency": round(radar.capital_efficiency, 4) if radar.capital_efficiency else None,
                    "best_leaderboard_rank": radar.best_leaderboard_rank,
                    "profile_views": radar.profile_views,
                    "reasons": radar.reasons,
                },
                "open_positions_count": len(open_positions),
                "entry_recommendations": [
                    {
                        "market_title": e.market_title,
                        "outcome": e.outcome,
                        "their_entry_price": e.their_entry_price,
                        "current_price": e.current_price,
                        "fair_value_estimate": e.fair_value_estimate,
                        "suggested_entry_range": e.suggested_entry_range,
                        "flagged_chasing": e.flagged_chasing,
                        "note": e.note,
                        "conviction": e.conviction,
                        "pick_score": e.pick_score,
                        "recommended": e.recommended,
                        "their_position_value": e.__dict__.get("their_position_value", 0),
                    }
                    for e in entries
                ],
            })
        except pc.PolymarketAPIError as exc:
            errors.append({"wallet": wallet, "error": str(exc)})
            log(f"      API error, skipping: {exc}")
        except Exception as exc:  # keep the daily run alive even if one wallet blows up
            errors.append({"wallet": wallet, "error": f"{exc}\n{traceback.format_exc()}"})
            log(f"      unexpected error, skipping: {exc}")

    results.sort(key=lambda r: r["score"]["composite"], reverse=True)
    top_results = results[:top_n]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 1),
        "n_candidates_discovered": len(candidates),
        "n_candidates_evaluated": len(ranked_candidates),
        "n_passed_filters": len(results),
        "n_errors": len(errors),
        "errors": errors[:20],  # don't blow up the JSON if something goes wrong on many wallets
        "wallets": top_results,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Daily Polymarket wallet-watchlist report")
    parser.add_argument("--top-n", type=int, default=8, help="Number of wallets to keep in the final list (5-10 per spec)")
    parser.add_argument("--max-candidates", type=int, default=150, help="Max candidates to deep-dive per run (API usage control)")
    parser.add_argument("--out", type=str, default=None, help="Output JSON path (default: reports/report-<date>.json)")
    args = parser.parse_args()

    report = build_report(top_n=args.top_n, max_candidates=args.max_candidates)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else REPORTS_DIR / f"report-{datetime.now(timezone.utc).date()}.json"
    out_path.write_text(json.dumps(report, indent=2))

    # Always also write/overwrite "latest.json" -- this is what the dashboard reads
    (REPORTS_DIR / "latest.json").write_text(json.dumps(report, indent=2))

    print(f"Wrote {out_path} and reports/latest.json", file=sys.stderr)
    print(f"{report['n_passed_filters']} wallets passed filters; kept top {len(report['wallets'])}", file=sys.stderr)


if __name__ == "__main__":
    main()