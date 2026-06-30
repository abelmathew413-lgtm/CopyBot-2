"""
Reads reports/latest.json and pings a Discord webhook ONLY if at least one
wallet in this run's results has a score_100 >= the threshold. Silent
(exit 0, no message) otherwise -- this is meant to run every 20 minutes, so
it would be useless noise if it pinged on every single run regardless of
whether anything notable showed up.

Usage:
    python3 src/notify.py --threshold 80

Reads the webhook URL from the DISCORD_WEBHOOK_URL environment variable.
If that's not set, this prints a message and exits 0 (not an error -- lots
of people will run the pipeline without ever wanting Discord notifications).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def build_message(wallets: list[dict], threshold: int, dashboard_url: str) -> str:
    lines = [f"**Wallet Watch: {len(wallets)} account(s) scored {threshold}+ this run**"]
    for w in wallets:
        lines.append(
            f"• **{w['display_name']}** — {w['score']['score_100']}/100 "
            f"— <{w['profile_url']}>"
        )
    lines.append(f"\nFull dashboard: {dashboard_url}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Conditional Discord notifier for Wallet Watch")
    parser.add_argument("--threshold", type=int, default=80, help="Minimum score_100 to trigger a notification")
    parser.add_argument("--dashboard-url", type=str, default=os.environ.get("DASHBOARD_URL", ""),
                         help="Dashboard URL to include in the message")
    parser.add_argument("--report", type=str, default=str(REPORTS_DIR / "latest.json"))
    args = parser.parse_args()

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set -- skipping notification (this is not an error).", file=sys.stderr)
        return

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"{report_path} not found -- nothing to notify about.", file=sys.stderr)
        return

    report = json.loads(report_path.read_text())
    qualifying = [w for w in report.get("wallets", []) if w.get("score", {}).get("score_100", 0) >= args.threshold]

    if not qualifying:
        print(f"No wallets >= {args.threshold} this run -- staying quiet.", file=sys.stderr)
        return

    message = build_message(qualifying, args.threshold, args.dashboard_url)
    try:
        resp = requests.post(webhook_url, json={"content": message}, timeout=15)
        resp.raise_for_status()
        print(f"Notified Discord: {len(qualifying)} wallet(s) >= {args.threshold}.", file=sys.stderr)
    except requests.RequestException as exc:
        # Don't fail the whole workflow run just because the notification step
        # had a hiccup -- the dashboard itself still updated successfully.
        print(f"Discord notification failed (continuing anyway): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
