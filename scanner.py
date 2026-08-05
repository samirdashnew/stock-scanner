"""
Daily volume/momentum scanner for NSE F&O stocks.

Pulls recent daily OHLCV data (free, via yfinance) for every symbol in
fno_stocks.csv, scores each stock on volume surge + price momentum, and
writes the ranked results to results.json for the dashboard (index.html).

Data notes:
- yfinance daily bars for NSE are end-of-day. Run this after market close
  (post ~4pm IST) for a clean "today's" scan, or before market open to
  screen off yesterday's action for today's watchlist.
- This is NOT real-time intraday data. For a true live intraday scanner
  you'd need a paid feed (Kite Connect, TrueData, etc).

Usage:
    ./venv/bin/python scanner.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

# --- Config -----------------------------------------------------------

LOOKBACK_DAYS = 40          # history window to compute averages
AVG_VOLUME_WINDOW = 20      # rolling window for average volume baseline
MIN_PRICE = 30              # ignore very low-priced / illiquid stocks
MIN_AVG_TURNOVER_CR = 5     # min 20-day avg turnover (price*volume), in crore INR
MIN_RVOL = 1.3              # relative volume must be at least this to qualify
TOP_N = 20                  # how many gainers/losers to keep

SYMBOLS_FILE = "fno_stocks.csv"
OUTPUT_FILE = "results.json"


def load_symbols(path: str) -> list[str]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [row["symbol"].strip() + ".NS" for row in reader if row["symbol"].strip()]


def fetch_data(symbols: list[str]) -> pd.DataFrame:
    print(f"Fetching {len(symbols)} symbols from yfinance...")
    data = yf.download(
        symbols,
        period=f"{LOOKBACK_DAYS}d",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    return data


def score_stock(symbol: str, df: pd.DataFrame) -> dict | None:
    df = df.dropna(subset=["Close", "Volume"])
    if len(df) < AVG_VOLUME_WINDOW + 1:
        return None

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(latest["Close"])
    prev_close = float(prev["Close"])
    volume = float(latest["Volume"])
    high = float(latest["High"])
    low = float(latest["Low"])
    open_ = float(latest["Open"])

    if close < MIN_PRICE or volume <= 0:
        return None

    avg_volume = float(df["Volume"].iloc[-(AVG_VOLUME_WINDOW + 1):-1].mean())
    if avg_volume <= 0:
        return None

    avg_turnover_cr = (close * avg_volume) / 1e7  # INR crore
    if avg_turnover_cr < MIN_AVG_TURNOVER_CR:
        return None

    rvol = volume / avg_volume
    if rvol < MIN_RVOL:
        return None

    pct_change = (close - prev_close) / prev_close * 100

    day_range = high - low
    # 1.0 = closed at the high (strong bullish close), 0.0 = closed at the low
    range_position = (close - low) / day_range if day_range > 0 else 0.5

    # Momentum score: rewards volume surge and strength of the price move,
    # with a bonus for closing near the day's extreme (conviction, not just
    # a wick). Weighting is deliberately simple/transparent, tune as you learn
    # what works for you.
    momentum_score = (rvol * 10) + (abs(pct_change) * 2) + (abs(range_position - 0.5) * 20)

    return {
        "symbol": symbol.replace(".NS", ""),
        "close": round(close, 2),
        "prev_close": round(prev_close, 2),
        "pct_change": round(pct_change, 2),
        "open": round(open_, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "volume": int(volume),
        "avg_volume_20d": int(avg_volume),
        "rvol": round(rvol, 2),
        "avg_turnover_cr": round(avg_turnover_cr, 1),
        "range_position": round(range_position, 2),
        "score": round(momentum_score, 2),
        "direction": "bullish" if pct_change >= 0 else "bearish",
    }


def main():
    symbols = load_symbols(SYMBOLS_FILE)
    raw = fetch_data(symbols)

    results = []
    failed = []
    for symbol in symbols:
        try:
            df = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
            row = score_stock(symbol, df)
            if row:
                results.append(row)
        except Exception:
            failed.append(symbol)

    results.sort(key=lambda r: r["score"], reverse=True)
    gainers = [r for r in results if r["direction"] == "bullish"][:TOP_N]
    losers = [r for r in results if r["direction"] == "bearish"][:TOP_N]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(symbols),
        "qualified": len(results),
        "filters": {
            "min_price": MIN_PRICE,
            "min_avg_turnover_cr": MIN_AVG_TURNOVER_CR,
            "min_rvol": MIN_RVOL,
        },
        "gainers": gainers,
        "losers": losers,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Scanned {len(symbols)} symbols -> {len(results)} qualified "
          f"({len(gainers)} gainers, {len(losers)} losers).")
    if failed:
        print(f"Skipped {len(failed)} symbols due to fetch/data errors: "
              f"{', '.join(s.replace('.NS','') for s in failed[:10])}"
              f"{'...' if len(failed) > 10 else ''}")
    print(f"Results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    sys.exit(main())
