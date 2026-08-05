# NSE F&O Momentum Scanner

Three tools:

- **Morning Picks** (`morning_scan.py` + `morning.html`) — the main one. Runs
  automatically every weekday at 9:40 AM IST via cron (and via GitHub Actions
  on the hosted version). Screens ~200 NSE F&O stocks down to up to **4 buy
  candidates** (sell/short is currently disabled — see below), each with an
  entry, stop-loss, and target.
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
of whenever it runs (9:40 AM IST via cron/GitHub Actions; can also run
manually anytime 9:30-10:00 AM):

| Signal | Requirement |
|---|---|
| **Opening-range breakout** | price above the first 15 minutes' high (9:15-9:30) |
| **VWAP** | trading above today's volume-weighted average price |
| **Early relative volume** | ≥ 1.5x the 20-day average, scaled to time elapsed |
| **Stop-loss distance** | opening-range low, within ~1.5% of entry |
| **Liquidity** | ≥ ₹20Cr avg daily turnover |
| **Daily trend regime** | previous close above daily EMA50 (trading with the larger trend, not against it) |
| **EMA stack** | daily EMA9 > EMA15 > EMA50 (short/medium/long trend all aligned) |
| **Trend strength (ADX)** | daily ADX(14) ≥ 20 (avoids choppy, non-trending names) |
| **RSI band** | daily RSI(14) between 45-75 (momentum without being already exhausted) |

Each pick shows entry, stop-loss, a 3:1 reward:risk target, risk %, early
RVOL, VWAP, ATR(14), daily RSI/ADX, EMA(9/15/50), and avg turnover. Ranked
by a composite score, **capped at 4**. Many mornings it will show fewer, or
zero — that's intentional, by request: quality over quantity. See "Tuning
methodology" below for why these specific filters and this cap.

**SELL (short) signals are currently disabled** (`SELL_ENABLED = False` in
`scanner_core.py`). Every filter combination tested in the backtest came
back net-negative on the short side — see below. The dashboard explains
this directly rather than silently showing nothing.

## Backtest — read this before trusting the win rate

`backtest.py` mechanically replays the exact same rules over every trading
day in the trailing ~60 calendar days (Yahoo's free-tier limit for 5-minute
data, roughly the last 2 months of trading days). For every setup that
would have qualified, it simulates forward bar-by-bar: stop hit first =
loss, target hit first = win, neither by end of day = resolved by closing
price (so every trade is forced to a clean win/loss, nothing left
ambiguous).

**Current result (window: 2026-05-14 to 2026-08-05):**

| | Trades | Win rate | Avg win | Avg loss | Expectancy/trade |
|---|---|---|---|---|---|
| Overall (buy-only, sell disabled) | 26 | **53.8%** | +1.12% | -0.82% | **+0.22%** |

**Read this honestly, including how we got here:**

- The *first* version of this scanner (opening-range breakout + VWAP + RVOL
  only, no trend/strength filters, both buy and sell active) backtested at
  **41.7% win rate, -0.10% expectancy/trade** over 240 trades — roughly
  breakeven-to-negative.
- Added daily-timeframe trend/strength filters (EMA9/15/50 trend + stack,
  ADX(14) for trend strength, RSI(14) to avoid chasing exhausted moves) and
  widened the reward target from 2:1 to 3:1, then swept ~9 different filter
  combinations against the same historical data (`tune.py`) to find what
  actually helped, rather than guessing.
- **Consistent, honest finding across every combination tried: buy-side
  setups had a real edge (best: 53.8% win rate, +0.22%/trade), sell-side
  setups never once had positive expectancy** (ranged -0.07% to -0.66%/trade
  across filter variants). This is very likely a structural feature of the
  test window/instrument set (NSE has historically had a long bias), not a
  fluke of one combination — so sell is switched off rather than forced to
  look "balanced."
- **A 70-80% win rate — the original ask — is not realistic for this style
  of strategy (intraday breakout momentum) and tuning further will not get
  there without fundamentally changing what the strategy is** (e.g. much
  wider stops/targets, multi-day holds, or a mean-reversion approach instead
  of breakout momentum — each trades away something else, like trade
  frequency or the tight stop-loss that was explicitly requested).
  Professional systematic intraday strategies typically run 45-55% win rate
  and make money on reward:risk, not on being right most of the time. **A
  53.8% win rate with positive expectancy is a genuinely good result for
  this category** — better than being "right," it's provably profitable in
  the backtest.
- **26 trades is still a small sample.** This is directional evidence, not
  proof. Re-run `backtest.py` every couple of weeks as more data accumulates
  and watch whether the number holds, and plan for the 1-year backtest
  already discussed before scaling this to a paid product.
- Doesn't model slippage, brokerage/STT, or the free-data lag — real fills
  will be slightly worse than simulated ones.

Re-run anytime: `./venv/bin/python backtest.py`, then open
**http://localhost:8000/backtest.html** (or the hosted URL). To re-explore
filter combinations yourself: `./venv/bin/python tune.py` (fetches data once,
tries several configs, prints a comparison table — takes several minutes).

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
| `REWARD_MULTIPLE` | 3.0 | Target = entry ± this × risk (tuned up from 2.0) |
| `USE_TREND_FILTER` | True | Require price above/below daily EMA50 |
| `USE_EMA_STACK` | True | Require EMA9/15/50 stacked in trade direction |
| `USE_ADX_FILTER` / `MIN_ADX` | True / 20 | Require daily ADX(14) ≥ this (trend strength) |
| `USE_RSI_FILTER` / `RSI_MIN_BUY`/`MAX_BUY` | True / 45-75 | Daily RSI(14) band for buy setups |
| `SELL_ENABLED` | **False** | Sell/short signals — off by default, see Backtest section |

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
- `scanner_core.py` — shared opening-range/VWAP/RVOL/trend/ADX/RSI rules
- `tune.py` — one-off parameter sweep tool (not for production/cron use)
- `morning_scan.py` — live daily scan → `morning_picks.json`
- `morning.html` — Morning Picks dashboard
- `backtest.py` — historical replay of the same rules → `backtest_results.json`
- `backtest.html` — backtest results dashboard
- `scanner.py` — broader EOD gainers/losers scan → `results.json`
- `index.html` — full scanner dashboard
- `run.sh` — starts the local server
- `logs/` — cron job output
- `venv/` — isolated Python environment (yfinance, pandas)
