# NSE F&O Momentum Scanner

Three tools:

- **Morning Picks** (`morning_scan.py` + `morning.html`) — the main one. Runs
  automatically every weekday at 9:40 AM IST via cron. Screens ~200 NSE F&O
  stocks down to up to **4 buy + 4 sell candidates**, each with an entry,
  stop-loss, and target.
- **Backtest** (`backtest.py` + `backtest.html`) — mechanically tests the
  same rules over the trailing ~2 months of data and reports a real win rate.
- **Full scanner** (`scanner.py` + `index.html`) — a broader end-of-day
  gainers/losers view (~20-45 stocks). Useful for post-market review or a
  pre-market watchlist, not for picking a single trade.

`scanner_core.py` holds the shared opening-range/VWAP/RVOL rules that both
`morning_scan.py` (live) and `backtest.py` (historical) use — so the
backtest is guaranteed to test the exact same logic the live scanner runs,
not a re-implementation that could drift.

## Morning Picks — how it decides

A stock only makes the shortlist if it clears **all** of these, checked as
of whenever it runs (9:40 AM IST via cron; can also run manually anytime
9:30-10:00 AM):

| Signal | Buy (long) | Sell (short) |
|---|---|---|
| **Opening-range break** | above the first 15 minutes' high | below the first 15 minutes' low |
| **VWAP** | trading above | trading below |
| **Early relative volume** | ≥ 1.5x the 20-day average, scaled to time elapsed | same |
| **Stop-loss distance** | opening-range low, within ~1.5% of entry | opening-range high, within ~1.5% of entry |
| **Liquidity** | ≥ ₹20Cr avg daily turnover | same |

Each pick shows entry, stop-loss, a 2:1 reward:risk target, risk %, early
RVOL, VWAP, ATR(14), and avg turnover. Ranked by a composite of volume
conviction, move strength, and stop tightness — **capped at 4 per side**.
Some mornings it will show fewer, or zero, on one or both sides — that's
intentional; it stays quiet rather than force a weak pick.

**SELL picks are intraday short-sell setups.** In the NSE cash segment you
can only short intraday and must square off the same day — no overnight
short without an F&O position. Confirm your broker/segment supports this
before acting on one.

## Backtest — read this before trusting the win rate

`backtest.py` mechanically replays the exact same rules over every trading
day in the trailing ~60 calendar days (Yahoo's free-tier limit for 5-minute
data, roughly the last 2 months of trading days). For every setup that
would have qualified, it simulates forward bar-by-bar: stop hit first =
loss, target hit first = win, neither by end of day = resolved by closing
price (so every trade is forced to a clean win/loss, nothing left
ambiguous).

**Last run result (window: 2026-05-14 to 2026-08-05, 54 trading days):**

| | Trades | Win rate | Avg win | Avg loss | Expectancy/trade |
|---|---|---|---|---|---|
| Overall | 240 | **41.7%** | +0.97% | -0.87% | -0.10% |
| Buy | 167 | 38.3% | +0.99% | -0.88% | -0.16% |
| Sell | 73 | 49.3% | +0.94% | -0.83% | +0.04% |

**Read this honestly:** over this specific 2-month window, the rule set as
configured came out roughly breakeven-to-slightly-negative — buy signals
underperformed sell signals. This is real output from real data, not a
sales pitch. A few things worth knowing before drawing conclusions:

- 2 months is a small sample — win rate will move around as more data comes
  in. Re-run `backtest.py` periodically to see if it holds up.
- The backtest is *mechanical* — it takes every qualifying setup
  automatically. In practice you'd apply your own judgment on top (skip
  setups you don't like the look of, sector context, broader market trend),
  which could improve or worsen this number.
- It doesn't model slippage, brokerage/STT, or the free-data lag — real
  fills will be slightly worse than the simulated ones.
- The filters are tunable (see Config below) — e.g. this result suggests
  the buy side may need tighter conditions (higher `MIN_RVOL`, or a wider
  `REWARD_MULTIPLE`) while the sell side is closer to workable as-is. Re-run
  the backtest after any change to see the effect before trusting it live.

Re-run anytime: `./venv/bin/python backtest.py`, then open
**http://localhost:8000/backtest.html**.

## Running it

The dashboard server:
```bash
./run.sh
```
This only serves the pages — it does not run a scan. Morning Picks now runs
automatically via cron (see below); to run any scan manually:
```bash
./venv/bin/python morning_scan.py   # -> morning.html
./venv/bin/python scanner.py        # -> index.html
./venv/bin/python backtest.py       # -> backtest.html
```
Pages (refresh after a scan completes, they don't auto-poll):
- **http://localhost:8000/morning.html** — buy/sell shortlist
- **http://localhost:8000/index.html** — full gainers/losers
- **http://localhost:8000/backtest.html** — backtest results

## Automated daily scan (already set up)

A cron job runs `morning_scan.py` every weekday at **9:40 AM IST**:
```
40 9 * * 1-5 cd "/Users/samirdash/Desktop/Samir EA/stock-scanner" && ./venv/bin/python morning_scan.py >> logs/morning_scan.log 2>&1
```
View/edit it with `crontab -l` / `crontab -e`. Output logs to `logs/morning_scan.log`.

**Two things that will silently stop this from working:**
1. **Your Mac must be on, awake, and logged in at 9:40 AM.** Cron does not
   wake a sleeping Mac. If you close the lid overnight, either plug it in
   with sleep disabled around that time, or move this to a cloud host (see
   below) for real reliability.
2. **macOS may block cron from reading/writing inside `~/Desktop`.** If
   `logs/morning_scan.log` isn't updating after 9:40 AM on a day the Mac was
   on, go to **System Settings → Privacy & Security → Full Disk Access** and
   add `/usr/sbin/cron` (use Cmd+Shift+G in the file picker to type that
   path directly). This is a one-time fix.

Check it worked: `tail -f logs/morning_scan.log` any morning after 9:40 AM,
or just open `morning.html` and check the "Last scanned" timestamp.

You could add similar cron lines for `scanner.py` (e.g. post-market 4:15pm)
and `backtest.py` (e.g. weekly) — not set up by default since those aren't
time-sensitive the way the morning scan is.

## Hosting this for your community (samirdash.in / mindfultradinghub.com / LMS)

See the recommendation given in chat — short version: this needs a small
always-on scheduler + a public JSON endpoint, which a personal Mac and a
WordPress-style site can't provide on their own. The free, low-maintenance
path is GitHub Actions (runs the scan on a schedule, no Mac required) +
GitHub Pages (hosts the dashboard for free) + an `<iframe>` embed on
whichever of your sites/LMS you want it visible in. Ask if you want this set
up — it needs a GitHub account and roughly 20-30 minutes of one-time setup.

## Important limitations — read this before trusting the numbers

- **Free Yahoo Finance data typically lags 15-20 minutes**, including
  intraday. Treat displayed prices/levels as indicative — check the live
  price on your broker terminal before placing any order. Matters most for
  Morning Picks, where you're acting on it within minutes.
- **No screener guarantees profit** — and the backtest above is direct
  proof of that: this exact rule set was roughly breakeven over the last 2
  months. Treat picks as "worth a closer look," not "guaranteed wins."
- **Not a live streaming scanner.** Each run is a snapshot. For true
  real-time scanning you'd need a paid feed (Zerodha Kite Connect, TrueData,
  Fyers, etc.) — the logic in `scanner_core.py` would carry over directly,
  only the data-fetching would change.
- Best window for Morning Picks: **9:30-10:00 AM IST**.

## Config

**`scanner_core.py`** (shared by Morning Picks and the backtest):

| Setting | Default | Meaning |
|---|---|---|
| `MIN_RVOL` | 1.5 | Min early relative volume to qualify |
| `MAX_RISK_PCT` | 1.5 | Max distance from entry to stop-loss, as % |
| `MIN_GAP_PCT` / `MAX_GAP_PCT` | 0.3 / 6.0 | Move must be meaningful but not already extended |
| `MIN_AVG_TURNOVER_CR` | 20 | Liquidity floor (₹ crore, 20-day avg) |
| `REWARD_MULTIPLE` | 2.0 | Target = entry ± this × risk |

`morning_scan.py`'s `TOP_N_PER_SIDE` (default 4) caps picks per direction.

**`scanner.py`** (the broader end-of-day view) has its own separate config
block at the top (`MIN_PRICE`, `MIN_AVG_TURNOVER_CR`, `MIN_RVOL`, `TOP_N`).

Tune, then **re-run `backtest.py`** to see whether a change actually helped
before trusting it live.

## Maintaining the stock list

`fno_stocks.csv` is a snapshot of NSE F&O stocks. NSE revises this list
periodically (quarterly), and companies occasionally rename or restructure
(e.g. Zomato → Eternal, Vedanta Ltd trades as `VEDL`). If a script starts
skipping a stock you expect to see, check the console/log output for a
fetch error and fix the symbol in `fno_stocks.csv` (NSE symbol, no `.NS`
suffix — the scripts add that).

## Files

- `fno_stocks.csv` — the stock universe (edit to add/remove symbols)
- `scanner_core.py` — shared opening-range/VWAP/RVOL rules (buy + sell)
- `morning_scan.py` — live daily scan → `morning_picks.json`
- `morning.html` — Morning Picks dashboard
- `backtest.py` — historical replay of the same rules → `backtest_results.json`
- `backtest.html` — backtest results dashboard
- `scanner.py` — broader EOD gainers/losers scan → `results.json`
- `index.html` — full scanner dashboard
- `run.sh` — starts the local server
- `logs/` — cron job output
- `venv/` — isolated Python environment (yfinance, pandas)
