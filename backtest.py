"""
Backtests the exact opening-range/VWAP/RVOL rules in scanner_core.py over
the trailing ~60 calendar days (Yahoo's free-tier limit for 5-minute bars,
~40 trading days / ~2 months) across the full F&O universe.

Methodology (per symbol, per day):
  1. Decision point = the 9:45 AM bar (30 minutes / 6 5-min bars into the
     session) - same point morning_scan.py evaluates at live.
  2. If the setup qualifies (buy or sell, same rules as live), simulate
     forward through the rest of that day's 5-min bars:
       - stop hit first  -> loss
       - target hit first -> win
       - neither by end of day -> resolved by close vs entry (still
         forced into win/loss, no "undecided" trades in the stats)
       - if both the stop and target fall inside the same 5-min bar, the
         stop is assumed to hit first (the standard conservative
         backtesting convention, since we don't know the intra-bar order)
  3. Every qualifying setup becomes exactly one trade.

This tests the rule, not judgement - it's mechanical by design so the
result isn't cherry-picked. Real trading will differ (slippage, the
15-20min data lag noted elsewhere, discretion on when to actually pull the
trigger).

Usage:
    ./venv/bin/python backtest.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

import scanner_core as core

SYMBOLS_FILE = "fno_stocks.csv"
OUTPUT_FILE = "backtest_results.json"
DECISION_BARS = 6  # 30 minutes in -> the 9:45 AM decision point


def load_symbols(path: str) -> list[str]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [row["symbol"].strip() + ".NS" for row in reader if row["symbol"].strip()]


def simulate_trade(direction: str, entry: float, stop: float, target: float,
                    future_bars: pd.DataFrame) -> tuple[str, float]:
    """Returns (outcome, exit_price). outcome is 'win' or 'loss'."""
    for _, bar in future_bars.iterrows():
        hi, lo = float(bar["High"]), float(bar["Low"])
        if direction == "buy":
            stop_hit = lo <= stop
            target_hit = hi >= target
        else:
            stop_hit = hi >= stop
            target_hit = lo <= target
        if stop_hit:
            return "loss", stop
        if target_hit:
            return "win", target

    last_close = float(future_bars["Close"].iloc[-1]) if len(future_bars) else entry
    if direction == "buy":
        return ("win" if last_close > entry else "loss"), last_close
    else:
        return ("win" if last_close < entry else "loss"), last_close


def backtest_symbol(symbol: str, daily_df: pd.DataFrame, intraday_df: pd.DataFrame) -> list[dict]:
    trades = []
    daily_df = daily_df.dropna(subset=["Close", "Volume"])
    intraday_df = intraday_df.dropna(subset=["Close"])
    if intraday_df.empty:
        return trades

    trading_days = sorted(set(intraday_df.index.date))
    for day in trading_days:
        today_bars = intraday_df[intraday_df.index.date == day]
        if len(today_bars) <= DECISION_BARS:
            continue  # no future bars to simulate, or half-day session

        stats = core.prior_day_stats(daily_df, day)
        if stats is None:
            continue

        evald = core.evaluate_setup(symbol, today_bars, stats, DECISION_BARS)
        if evald is None:
            continue

        future_bars = today_bars.iloc[DECISION_BARS:]

        for direction_key, direction in (("bull", "buy"), ("bear", "sell")):
            setup = evald[direction_key]
            if not setup["qualifies"]:
                continue
            outcome, exit_price = simulate_trade(
                direction, setup["entry"], setup["stop_loss"], setup["target"], future_bars
            )
            pnl_pct = (exit_price - setup["entry"]) / setup["entry"] * 100
            if direction == "sell":
                pnl_pct = -pnl_pct
            trades.append({
                "symbol": symbol,
                "date": str(day),
                "direction": direction,
                "entry": round(setup["entry"], 2),
                "stop_loss": round(setup["stop_loss"], 2),
                "target": round(setup["target"], 2),
                "exit_price": round(exit_price, 2),
                "outcome": outcome,
                "pnl_pct": round(pnl_pct, 2),
            })
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0,
                "avg_win_pct": 0, "avg_loss_pct": 0, "expectancy_pct": 0}
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    win_rate = len(wins) / len(trades) * 100
    expectancy = sum(t["pnl_pct"] for t in trades) / len(trades)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 2),
    }


def main():
    symbols = load_symbols(SYMBOLS_FILE)
    print(f"Fetching ~120d daily history for {len(symbols)} symbols...")
    daily_raw = yf.download(symbols, period="120d", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=True, progress=False)
    print(f"Fetching 60d intraday (5m) history for {len(symbols)} symbols "
          f"(Yahoo's max window for 5m bars, ~2 months of trading days)...")
    intraday_raw = yf.download(symbols, period="60d", interval="5m", group_by="ticker",
                                auto_adjust=True, threads=True, progress=False)

    all_trades = []
    for symbol in symbols:
        try:
            ddf = daily_raw[symbol] if isinstance(daily_raw.columns, pd.MultiIndex) else daily_raw
            idf = intraday_raw[symbol] if isinstance(intraday_raw.columns, pd.MultiIndex) else intraday_raw
            all_trades.extend(backtest_symbol(symbol.replace(".NS", ""), ddf, idf))
        except Exception:
            continue

    buy_trades = [t for t in all_trades if t["direction"] == "buy"]
    sell_trades = [t for t in all_trades if t["direction"] == "sell"]

    date_range = sorted(set(t["date"] for t in all_trades))
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": date_range[0] if date_range else None,
                   "to": date_range[-1] if date_range else None,
                   "trading_days": len(date_range)},
        "overall": summarize(all_trades),
        "buy": summarize(buy_trades),
        "sell": summarize(sell_trades),
        "trades": all_trades,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print()
    print(f"Backtest period: {output['period']['from']} to {output['period']['to']} "
          f"({output['period']['trading_days']} trading days)")
    print(f"Overall: {output['overall']['trades']} trades, "
          f"win rate {output['overall']['win_rate_pct']}%, "
          f"expectancy {output['overall']['expectancy_pct']}% per trade")
    print(f"  Buy:  {output['buy']['trades']} trades, win rate {output['buy']['win_rate_pct']}%")
    print(f"  Sell: {output['sell']['trades']} trades, win rate {output['sell']['win_rate_pct']}%")
    print(f"Full results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    sys.exit(main())
