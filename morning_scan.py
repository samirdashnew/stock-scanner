"""
Morning opening-range breakout scanner - graded daily shortlist.

Meant to be run around 9:30-9:45 AM IST, once the first 15 minutes of
trading (the "opening range") have printed. Surfaces the top few long
candidates of the day, each with an A/B/C confluence grade.

How the shortlist is built (see scanner_core.py for the rules themselves):
  1. Every stock must clear the HARD gate - opening-range breakout, above
     VWAP, real volume, tight structural stop, liquid enough to trade.
     Nothing that fails this is ever shown, at any grade.
  2. Survivors are scored on 11 quality checks spanning trend indicators
     (EMA/ADX/RSI) and price-action structure (support/resistance headroom,
     prior-day high, consolidation squeeze, higher-high/higher-low
     structure, extension, close strength).
  3. The best TOP_N are shown, each labelled A / B / C by how many checks it
     passed. Grade A is a full-confluence setup; C means it cleared the
     safety gate but little else - a watchlist name, not a conviction trade.

That grade is the honest answer to "give me 2-3 stocks every day": you get
candidates daily, but the scanner never pretends a thin setup is a strong
one. Some days the best available is a B or C - that is information, not a
failure.

IMPORTANT - read before trusting the numbers:
- Free Yahoo Finance intraday data commonly lags 15-20 minutes. Treat the
  displayed price/levels as indicative and re-check the live price on your
  broker terminal before placing any order.
- This is a screening tool, not a signal guaranteeing profit. It narrows a
  large universe to a short list worth *your* further judgment.
- Best run after 9:30 AM IST (the opening range needs to have printed) and
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

TOP_N = 3                 # must match backtest.TOP_N_PER_DAY
DECISION_BARS = 6         # 30 minutes in -> the 9:45 AM decision point
SYMBOLS_FILE = "fno_stocks.csv"
OUTPUT_FILE = "morning_picks.json"
IST = ZoneInfo("Asia/Kolkata")

ROUND_2 = ("entry", "stop_loss", "target", "risk_pct", "vwap", "gap_pct",
           "or_high", "or_low", "atr14", "rvol_early", "score",
           "ema9", "ema15", "ema50", "rsi14", "adx14", "close_strength")


def load_symbols(path: str) -> list[str]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [row["symbol"].strip() + ".NS" for row in reader if row["symbol"].strip()]


def fetch_daily(symbols: list[str]) -> pd.DataFrame:
    print(f"Fetching daily history for {len(symbols)} symbols...")
    return yf.download(
        symbols, period="250d", interval="1d", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )


def fetch_intraday(symbols: list[str]) -> pd.DataFrame:
    print(f"Fetching today's intraday (5m) data for {len(symbols)} symbols...")
    return yf.download(
        symbols, period="2d", interval="5m", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )


def clean(row: dict) -> dict:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    for k in ROUND_2:
        out[k] = round(out[k], 2)
    out["avg_turnover_cr"] = round(out["avg_turnover_cr"], 1)
    # `checks` is an ordered dict of human-readable check -> bool; the
    # dashboard renders it directly as the "why this stock" list.
    out["checks"] = {k: bool(v) for k, v in row["checks"].items()}
    return out


def main():
    now_ist = datetime.now(IST)
    if now_ist.time() < dtime(9, 30):
        print("Note: it's before 9:30 AM IST - the opening range may not have "
              "fully printed yet. Results may be unreliable this early.")

    symbols = load_symbols(SYMBOLS_FILE)
    daily_raw = fetch_daily(symbols)
    intraday_raw = fetch_intraday(symbols)

    candidates = []
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
            if len(today_bars) < DECISION_BARS:
                continue  # need at least 30 min of bars for a decision point

            ctx = core.prepare_symbol(ddf)
            if ctx is None:
                continue

            scanned += 1
            decision_bars = min(len(today_bars), DECISION_BARS)
            setup = core.evaluate_setup(symbol.replace(".NS", ""), ctx,
                                         today_bars, today, decision_bars)
            if setup is not None:
                candidates.append(setup)
        except Exception:
            continue

    candidates.sort(key=lambda r: r["score"], reverse=True)
    picks = [clean(r) for r in candidates[:TOP_N]]
    grade_counts = {g: sum(1 for c in candidates if c["grade"] == g) for g in ("A", "B", "C")}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_ist": now_ist.isoformat(),
        "universe_size": len(symbols),
        "candidates_scanned": scanned,
        "candidates_passing_gate": len(candidates),
        "grade_counts": grade_counts,
        "picks": picks,
        "filters": {
            "min_rvol_hard": core.MIN_RVOL_HARD,
            "min_rvol_strong": core.MIN_RVOL_STRONG,
            "gap_pct_range": [core.MIN_GAP_PCT, core.MAX_GAP_PCT],
            "risk_pct_range": [core.MIN_RISK_PCT, core.MAX_RISK_PCT],
            "min_avg_turnover_cr": core.MIN_AVG_TURNOVER_CR,
            "reward_multiple": core.REWARD_MULTIPLE,
            "min_adx": core.MIN_ADX,
            "min_headroom_r": core.MIN_HEADROOM_R,
            "top_n": TOP_N,
            "sell_enabled": core.SELL_ENABLED,
        },
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    shown = ", ".join(f"{p['symbol']}({p['grade']})" for p in picks) or "none"
    print(f"Scanned {scanned} symbols -> {len(candidates)} passed the hard gate "
          f"(A:{grade_counts['A']} B:{grade_counts['B']} C:{grade_counts['C']}) "
          f"-> showing top {len(picks)}: {shown}")
    print(f"Results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    sys.exit(main())
