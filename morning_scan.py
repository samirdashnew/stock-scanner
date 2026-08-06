"""
Morning opening-range breakout scanner - buy + sell shortlist.

Meant to be run around 9:30-9:45 AM IST, once the first 15 minutes of
trading (the "opening range") have printed. Surfaces a SMALL shortlist -
up to 4 buy (long) and 4 sell (short) candidates - instead of a broad
gainers/losers table. See scanner_core.py for the exact qualification
rules (opening-range breakout, VWAP, early relative volume, tight
structural stop, liquidity floor) - both directions use the same rules,
mirrored.

IMPORTANT - read before trusting the numbers:
- Free Yahoo Finance intraday data commonly lags 15-20 minutes. Treat the
  displayed price/levels as indicative and re-check the live price on your
  broker terminal before placing any order.
- SELL/short picks are intraday short-sell setups. In the NSE cash segment
  you can only short intraday and MUST square off the same day (no
  overnight short without F&O). Make sure your broker/segment supports
  this before acting on a sell pick.
- This is a screening tool, not a signal guaranteeing profit. It narrows a
  large universe to a short list worth *your* further judgment - it does
  not replace it.
- Best run after 9:30 AM IST (opening range needs to have printed) and
  before intraday moves make the setup stale, i.e. by ~10:00 AM.

Usage:
    ./venv/bin/python morning_scan.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import scanner_core as core

TOP_N_PER_SIDE = 4
SYMBOLS_FILE = "fno_stocks.csv"
OUTPUT_FILE = "morning_picks.json"
IST = ZoneInfo("Asia/Kolkata")


def load_symbols(path: str) -> list[str]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [row["symbol"].strip() + ".NS" for row in reader if row["symbol"].strip()]


def fetch_daily(symbols: list[str]) -> pd.DataFrame:
    print(f"Fetching daily history for {len(symbols)} symbols...")
    return yf.download(
        symbols, period="90d", interval="1d", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )


def fetch_intraday(symbols: list[str]) -> pd.DataFrame:
    print(f"Fetching today's intraday (5m) data for {len(symbols)} symbols...")
    return yf.download(
        symbols, period="2d", interval="5m", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )


def clean(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "qualifies"}
    for k in ("entry", "stop_loss", "target", "risk_pct", "vwap", "gap_pct",
              "or_high", "or_low", "atr14", "rvol_early", "score",
              "ema9", "ema15", "ema50", "rsi14", "adx14"):
        out[k] = round(out[k], 2)
    out["avg_turnover_cr"] = round(out["avg_turnover_cr"], 1)
    out["reward_multiple"] = core.REWARD_MULTIPLE
    return out


def main():
    now_ist = datetime.now(IST)
    if now_ist.time() < dtime(9, 30):
        print("Note: it's before 9:30 AM IST - the opening range may not have "
              "fully printed yet. Results may be unreliable this early.")

    symbols = load_symbols(SYMBOLS_FILE)
    daily_raw = fetch_daily(symbols)
    intraday_raw = fetch_intraday(symbols)

    buys, sells = [], []
    scanned = 0
    for symbol in symbols:
        try:
            ddf = daily_raw[symbol] if isinstance(daily_raw.columns, pd.MultiIndex) else daily_raw
            idf = intraday_raw[symbol] if isinstance(intraday_raw.columns, pd.MultiIndex) else intraday_raw
            idf = idf.dropna(subset=["Close"])
            if idf.empty:
                continue
            today = idf.index[-1].date()
            today_bars = idf[idf.index.date == today]
            if len(today_bars) < 6:
                continue  # need at least 30 min of bars for a decision point

            indicators = core.compute_daily_indicators(ddf)
            stats = core.prior_day_stats(indicators, today)
            if stats is None:
                continue

            decision_bars = min(len(today_bars), 6)
            evald = core.evaluate_setup(symbol.replace(".NS", ""), today_bars, stats, decision_bars)
            if evald is None:
                continue

            scanned += 1
            if evald["bull"]["qualifies"]:
                buys.append(evald["bull"])
            if evald["bear"]["qualifies"]:
                sells.append(evald["bear"])
        except Exception:
            continue

    buys.sort(key=lambda r: r["score"], reverse=True)
    sells.sort(key=lambda r: r["score"], reverse=True)
    buy_picks = [clean(r) for r in buys[:TOP_N_PER_SIDE]]
    sell_picks = [clean(r) for r in sells[:TOP_N_PER_SIDE]]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_ist": now_ist.isoformat(),
        "universe_size": len(symbols),
        "candidates_scanned": scanned,
        "qualified_buy": len(buys),
        "qualified_sell": len(sells),
        "buy_picks": buy_picks,
        "sell_picks": sell_picks,
        "filters": {
            "min_rvol": core.MIN_RVOL,
            "gap_pct_range": [core.MIN_GAP_PCT, core.MAX_GAP_PCT],
            "risk_pct_range": [core.MIN_RISK_PCT, core.MAX_RISK_PCT],
            "min_avg_turnover_cr": core.MIN_AVG_TURNOVER_CR,
            "reward_multiple": core.REWARD_MULTIPLE,
            "min_adx": core.MIN_ADX,
            "sell_enabled": core.SELL_ENABLED,
        },
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Scanned {scanned} candidates -> {len(buys)} buy-qualified, "
          f"{len(sells)} sell-qualified -> showing top {len(buy_picks)} buy / "
          f"{len(sell_picks)} sell.")
    print(f"Results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    sys.exit(main())
