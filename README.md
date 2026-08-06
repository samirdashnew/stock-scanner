# NSE F&O Momentum Scanner

**Status: this is a screening / watchlist tool, not a validated signal
service.** Backtesting found no profitable edge — see the verdict section
below before using it for anything, and especially before showing it to
anyone as a recommendation.

Tools:

- **Morning Picks** (`morning_scan.py` + `morning.html`) — the main one. Runs
  automatically every weekday at ~9:40 AM IST via GitHub Actions. Screens
  ~200 NSE F&O stocks down to the **top 3 long candidates**, each with entry,
  stop-loss, target and the confluence signals present.
- **Backtest** (`backtest.py` + `backtest.html`) — replays the live rules over
  the trailing ~2 months and reports the real numbers.
- **Research tools** — `analyze_checks.py` (which checks actually predict
  wins) and `exit_lab.py` (does a different exit rule help). Both are one-off,
  not part of the daily run.
- **Full scanner** (`scanner.py` + `index.html`) — a broader end-of-day
  gainers/losers view. Post-market review, not trade selection.

`scanner_core.py` holds the rules; `price_action.py` holds the structural
analysis (swing support/resistance, headroom, consolidation squeeze, market
structure). Both `morning_scan.py` (live) and `backtest.py` (historical) use
them, so the backtest always tests the shipped logic rather than a
re-implementation that could drift.

## How a stock gets on the list

**Hard gate** — tradability, never relaxed. Fail any and the stock is not
shown at all:

| Requirement | Why |
|---|---|
| Price above the first 15 minutes' high | the actual breakout trigger |
| Trading above today's VWAP | buying is real, not drift |
| Early RVOL ≥ 1.2x | some genuine participation |
| Move between +0.3% and +6% | meaningful but not already blown out |
| Stop (opening-range low) within 0.15–1.5% | tight, structural risk |
| ≥ ₹20Cr average daily turnover | liquid enough to actually trade |

**Confluence signals** — 11 checks spanning trend indicators (EMA 9/15/50,
ADX, RSI) and price action (support/resistance headroom, prior-day high,
consolidation squeeze, higher-high/higher-low structure, extension, close
strength). These are **displayed as observations, not scored as a grade**,
because per-check backtesting showed they don't reliably separate winners
from losers (see verdict below). The ranking that picks the top 3 is a
"most notable setup" heuristic — not a validated predictor.

**Sell/short is disabled** (`SELL_ENABLED = False`). Short setups tested
net-negative in every filter combination tried. The price-action layer is
also long-biased, so re-enabling shorts needs its checks mirrored, not just
the flag flipped.

## Backtest — the honest verdict

**This rule set has not shown a profitable edge.** Read this before using
the output for anything.

`backtest.py` mechanically replays the live rules over every trading day in
the trailing ~60 calendar days (Yahoo's free-tier limit for 5-minute data).
It is *day-major*: each day's candidates are pooled, ranked, and only the
top 3 are "taken" — exactly what the dashboard shows.

**Result over 267 candidate setups (May–Aug 2026):**

| | Trades | Win rate | Expectancy/trade |
|---|---|---|---|
| All hard-gate candidates | 267 | 37.5% | **−0.17%** |
| Top 3 per day (what's shown) | 148 | 36.5% | −0.12% |

Three separate attempts to fix this all failed, and the negative results are
worth recording so they aren't retried blindly:

1. **More indicators didn't help.** `analyze_checks.py` measures each check's
   *lift* — win rate when it passes minus win rate when it fails:

   | Check | Lift |
   |---|---|
   | Strong trend (ADX ≥ 20) | +5.3 |
   | Higher highs & lows | +4.8 |
   | Volume surge (RVOL ≥ 1.5) | +0.7 |
   | Clear air to resistance | +0.3 |
   | Above 50 EMA | −0.1 |
   | Above prior day high | −5.4 |
   | EMA 9/15/50 stacked | −6.5 |
   | RSI in healthy band | −7.9 |
   | Broke from tight base (squeeze) | −7.9 |

   Only ADX and market structure were mildly positive, both within noise at
   this sample size. The price-action additions — support/resistance headroom
   and the consolidation squeeze — did **not** improve accuracy.

2. **Confluence count doesn't rank quality.** Setups passing 10 of 11 checks
   won 22% of the time; those passing 9 won 33%. There is no reliable
   ordering, so the dashboard presents the checks as *observations* rather
   than as a grade — an A/B/C badge would imply a validated quality tier that
   the data does not support.

3. **No exit fixed it.** `exit_lab.py` tested 1R–5R targets, breakeven stops
   and trailing stops on identical entries. Every variant was negative; the
   best was −0.139%/trade. This matters because at a 2.5R target the
   breakeven win rate is only ~29%, so 37.5% *should* be profitable — it
   isn't, because only ~1 in 5 winners reaches the target while losers take
   the full stop.

**About the old 53.8% number.** An earlier version of this README reported
53.8% win rate / +0.22% expectancy. That was **26 trades** — roughly ±10%
standard error — i.e. almost certainly noise. It came from a much stricter
gate that produced ~1 setup per day. The larger 267-trade sample supersedes
it. It was reported in good faith at the time; it does not hold up.

**What this does and doesn't mean.** It is *not* proof the approach can never
work — two months is a short window and possibly an unfavourable one for
breakouts, and behaviour could differ in a strongly trending market. But it
is **unproven**, and it should not be monetised, gated behind a paid course,
or presented to a community as profitable on these numbers.

**To test properly you need more data.** Yahoo caps free 5-minute history at
~60 days, which is why the sample is small. A genuine 1-year intraday
backtest needs a paid feed (Zerodha Kite historical, TrueData, GDFL). That is
the right gate before charging anyone for this.

Re-run anytime:
```bash
./venv/bin/python backtest.py         # headline numbers -> backtest.html
./venv/bin/python analyze_checks.py   # per-check lift + selectivity sweep
./venv/bin/python exit_lab.py         # exit-rule comparison
```

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
