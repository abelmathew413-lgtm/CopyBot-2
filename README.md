# Wallet Watch

A daily, automated watchlist of under-the-radar Polymarket wallets, ranked by
a skill-vs-luck composite score, for **manual** copy-trade review. It does
not place any trades itself -- it just tells you who to look at each morning.

Every morning it:
1. Discovers candidate wallets across Polymarket's leaderboards
2. Pulls each one's positions, closed trades, and trade history
3. Scores them (confidence-adjusted win rate + ROI + Kelly edge, penalized
   for profit concentrated in one market)
4. Filters out anyone too visible (>250 profile views, top-20 leaderboard
   rank) or operationally uncopyable (>150 open positions, >50 trades/day,
   <1 trade/day on average)
5. Publishes the top 5-10 survivors to a dashboard, with suggested entry
   odds for each of their open positions

## How it's deployed (no servers, no cost)

This runs entirely on **GitHub Actions** and publishes to **GitHub Pages**
(also free). There is nothing to host or pay for.

**One important setting: the repo needs to be PUBLIC.** GitHub Actions has a
2,000-minute/month free limit on *private* repos, but **unlimited** free
minutes on public repos -- and this runs hourly, so that limit matters.
Since this dashboard only displays other people's public on-chain wallet
activity (nothing personal of yours), making it public is the practical
choice. Anyone who has the link (or finds the repo) can see the code, the
scoring logic, and the live dashboard.

### One-time setup

1. **Create a new GitHub repository as Public** and push this folder's
   contents to it (`git init`, `git add .`, `git commit -m "init"`, then push
   to a new repo you create on github.com -- make sure "Public" is selected
   when you create it, not "Private").

2. **Enable GitHub Pages via Actions**:
   Repo -> Settings -> Pages -> under "Build and deployment", set
   **Source = GitHub Actions**. (Not "Deploy from a branch" -- the workflow
   deploys directly.)

3. **Enable Actions write permissions** (needed so the workflow can commit
   each run's report back into the repo):
   Repo -> Settings -> Actions -> General -> Workflow permissions ->
   **Read and write permissions**.

4. That's it. The workflow at `.github/workflows/daily.yml` (the file is
   still named `daily.yml`, but it now runs hourly -- see the `name:` field
   inside it) will run automatically every hour, on the hour. Edit the
   `cron:` line if you want a different cadence; cron syntax is UTC.

5. To trigger it immediately instead of waiting for the next hour: repo ->
   **Actions** tab -> "Hourly wallet watchlist" -> **Run workflow**.

6. Your dashboard will be live at:
   `https://<your-github-username>.github.io/<repo-name>/`
   (GitHub shows you the exact URL under Settings -> Pages once the first
   deployment completes.)

### Discord notification (only fires when a wallet scores 80+)

Unlike a routine "dashboard updated" ping every hour (which you'd just
learn to ignore), this **only messages you when at least one wallet clears
the score threshold** -- silence on a given run means nothing notable
showed up that hour, not that something broke.

1. In Discord: channel settings -> Integrations -> Webhooks -> New Webhook
   -> copy the Webhook URL.
2. In your repo: Settings -> Secrets and variables -> Actions -> **Secrets**
   tab -> New repository secret -> name it `DISCORD_WEBHOOK_URL`, paste the
   URL.
3. That's it -- no separate "enable" toggle needed, `notify.py` checks for
   this secret automatically every run.
4. Optional: change the threshold from the default of 80 by adding a repo
   **Variable** (same page, "Variables" tab) named `SCORE_THRESHOLD` with
   a different number.

### Tuning knobs (repo Variables, Settings -> Secrets and variables -> Actions -> Variables)

- `TOP_N` -- how many wallets to keep (default 8, your spec said 5-10)
- `MAX_CANDIDATES` -- how many candidates to deep-dive per run (default 60,
  kept lower than earlier versions of this project specifically because this
  now runs hourly -- higher is more thorough but slower, and pushes more load
  onto Polymarket's API every single hour)
- `SCORE_THRESHOLD` -- minimum score_100 to trigger a Discord ping (default 80)

**A note on actually hitting "hourly" reliably**: the very first few runs are
the real test of how long a full pipeline run takes (CLV lookups in
particular add a lot of API calls). Check the Actions tab after a day or two
-- if runs are taking close to or over an hour, or you're seeing rate-limit
errors from Polymarket, lower `MAX_CANDIDATES` rather than the schedule
itself.

## Running it locally instead (for testing/tuning)

```bash
pip install -r requirements.txt
python3 src/main.py --top-n 8 --max-candidates 150   # writes reports/latest.json
python3 src/render_dashboard.py                       # writes dashboard/index.html
open dashboard/index.html                             # (or just double-click it)
```

## Project structure

```
src/
  polymarket_client.py   API wrapper (leaderboard, positions, trades, profile, price history)
  discovery.py           Builds the candidate wallet universe from leaderboards
  scoring.py             The 7-factor weighted composite score (CLV/Sharpe/ROI/Drawdown/Sample-size/Win-rate)
  clv.py                 Closing Line Value calculation, with graceful fallback for Polymarket's data gaps
  radar_filter.py        Under-the-radar gate (views, account age, efficiency, rank)
  views_scraper.py       Best-effort scrape of Polymarket's own profile-view count
  pricing.py             Entry-price/odds recommendation + which-positions-to-actually-copy ranking
  main.py                Orchestrates everything into reports/latest.json
  render_dashboard.py    Injects the JSON into the dashboard template
dashboard/
  template.html          The dashboard shell (edit this to restyle)
  index.html              Generated output (this is what gets deployed -- don't hand-edit)
reports/
  latest.json             Always the most recent run
  report-<date>.json      Daily history
.github/workflows/daily.yml   The automation itself
test_scoring.py / test_pipeline.py   Sanity tests, run with `python3 test_scoring.py` etc.
```

## The scoring model

Composite = weighted blend of:

| Component | Weight | Notes |
|---|---|---|
| CLV (closing line value) | 25% | Best-effort; dropped (weight redistributed) if Polymarket's price-history data is missing for a wallet |
| Sharpe ratio | 20% | Blended lifetime + trailing-90-day |
| Lifetime ROI | 15% | |
| 90-day ROI | 15% | |
| Max drawdown | 10% | Smaller is better |
| Sample size | 10% | Discounted for low market diversity AND high capital concentration (one huge bet doesn't count as a strong sample) |
| Win rate | 5% | Wilson-adjusted for small samples |

30-day and 180-day ROI are shown on the dashboard as trend context but aren't separately weighted (to avoid ROI dominating the score by being counted multiple times). Account age and "number of markets" feed the under-the-radar gate and the sample-size component respectively, rather than being separate weighted buckets.

When a wallet is missing data for a component (most commonly CLV), that component's weight is redistributed proportionally across whatever components *are* available, rather than zeroing it out or excluding the wallet entirely.

## Honest limitations worth knowing

- **`views_scraper.py` scrapes Polymarket's own page markup**, which isn't a
  documented API contract. If Polymarket redesigns their profile pages, this
  can start returning "unknown" for everyone -- which, by design, means the
  views gate fails closed (excludes wallets rather than wrongly admitting
  them). If you see zero wallets pass for several days running, check this
  first.
- This is a **ranking heuristic, not investment advice**. Past performance
  doesn't guarantee future results, and copying a trade at a different price
  than the original wallet's entry changes your risk/reward versus theirs --
  that's exactly what the "suggested entry range" and "chasing" flag are
  trying to help you judge, not eliminate.
- Everything here is **read-only**. It never places, signs, or submits any
  trade -- you click through to Polymarket and decide yourself.
