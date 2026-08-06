"""
Backtests the exact rules in scanner_core.py over the trailing ~60 calendar
days (Yahoo's free-tier limit for 5-minute bars, ~2 months of trading days)
across the full F&O universe.

Methodology:
  1. Decision point = the 9:45 AM bar (30 minutes / 6 5-min bars into the
     session) - the same point morning_scan.py evaluates at live.
  2. Every symbol passing the HARD gate that day becomes a candidate.
  3. DAY-MAJOR SELECTION: candidates are pooled per day, ranked by score,
     and only the top TOP_N_PER_DAY are "taken" - exactly what the live
     scanner shows. (An earlier version tested every qualifying setup
     regardless of rank, which did not match what the dashboard actually
     recommends and so measured a different strategy than the one shipped.)
  4. Each taken setup is simulated forward bar-by-bar:
       - stop hit first  -> loss
       - target hit first -> win
       - neither by end of day -> resolved by close vs entry
       - if stop and target fall inside the same 5-min bar, the stop is
         assumed to hit first (standard conservative convention, since
         intra-bar ordering is unknown)

Results are broken out BY GRADE (A/B/C) so the difference between a
high-confluence setup and a filler pick is visible, not averaged away.

Usage:
    ./venv/bin/python backtest.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

import scanner_core as core

SYMBOLS_FILE = "fno_stocks.csv"
OUTPUT_FILE = "backtest_results.json"
DECISION_BARS = 6        # 30 minutes in -> the 9:45 AM decision point
TOP_N_PER_DAY = 3        # must match morning_scan.TOP_N


def load_symbols(path: str) -> list[str]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [row["symbol"].strip() + ".NS" for row in reader if row["symbol"].strip()]


def simulate_trade(entry: float, stop: float, target: float,
                    future_bars: pd.DataFrame) -> tuple[str, float]:
    """Returns (outcome, exit_price) for a long trade."""
    for _, bar in future_bars.iterrows():
        if float(bar["Low"]) <= stop:
            return "loss", stop
        if float(bar["High"]) >= target:
            return "win", target

    last_close = float(future_bars["Close"].iloc[-1]) if len(future_bars) else entry
    return ("win" if last_close > entry else "loss"), last_close


def collect_candidates(symbol: str, daily_df: pd.DataFrame,
                        intraday_df: pd.DataFrame) -> list[dict]:
    """Every HARD-gate-passing setup for this symbol, with the forward bars
    needed to simulate it. Indicators/swings computed once per symbol."""
    candidates = []
    intraday_df = intraday_df.dropna(subset=["Close"])
    if intraday_df.empty:
        return candidates

    ctx = core.prepare_symbol(daily_df)
    if ctx is None:
        return candidates

    for day in sorted(set(intraday_df.index.date)):
        today_bars = intraday_df[intraday_df.index.date == day]
        if len(today_bars) <= DECISION_BARS:
            continue  # no forward bars to simulate, or a half-day session

        setup = core.evaluate_setup(symbol, ctx, today_bars, day, DECISION_BARS)
        if setup is None:
            continue

        setup["date"] = str(day)
        setup["_future_bars"] = today_bars.iloc[DECISION_BARS:]
        candidates.append(setup)

    return candidates


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0,
                "avg_win_pct": 0, "avg_loss_pct": 0, "expectancy_pct": 0,
                "target_hit_rate_pct": 0}
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    # Of the wins, how many actually reached the stated target price intraday
    # vs just closed positive at end of day - the "is the target realistic"
    # check that the dashboard discloses.
    true_target_hits = [t for t in wins if abs(t["exit_price"] - t["target"]) < 0.01]
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 2),
        "target_hit_rate_pct": round(len(true_target_hits) / len(wins) * 100, 1) if wins else 0,
    }


def run_backtest(symbols: list[str], daily_raw, intraday_raw,
                  top_n: int = TOP_N_PER_DAY) -> tuple[list[dict], list[str], int]:
    """Collect candidates, apply day-major top-N selection, simulate.
    Returns (trades, trading_days, candidate_count). Shared by main() and the
    one-off tuning scripts so they always test the shipped selection logic."""
    all_candidates = []
    for symbol in symbols:
        try:
            ddf = daily_raw[symbol] if isinstance(daily_raw.columns, pd.MultiIndex) else daily_raw
            idf = intraday_raw[symbol] if isinstance(intraday_raw.columns, pd.MultiIndex) else intraday_raw
            all_candidates.extend(collect_candidates(symbol.replace(".NS", ""), ddf, idf))
        except Exception:
            continue

    by_day = defaultdict(list)
    for c in all_candidates:
        by_day[c["date"]].append(c)

    trades = []
    for day, pool in sorted(by_day.items()):
        pool.sort(key=lambda c: c["score"], reverse=True)
        for setup in pool[:top_n]:
            outcome, exit_price = simulate_trade(
                setup["entry"], setup["stop_loss"], setup["target"], setup["_future_bars"])
            trades.append({
                "symbol": setup["symbol"],
                "date": day,
                "grade": setup["grade"],
                "checks_passed": setup["checks_passed"],
                "entry": round(setup["entry"], 2),
                "stop_loss": round(setup["stop_loss"], 2),
                "target": round(setup["target"], 2),
                "exit_price": round(exit_price, 2),
                "outcome": outcome,
                "pnl_pct": round((exit_price - setup["entry"]) / setup["entry"] * 100, 2),
                "headroom_r": setup["headroom_r"],
                "rvol": round(setup["rvol_early"], 2),
            })

    return trades, sorted(by_day.keys()), len(all_candidates)


def main():
    symbols = load_symbols(SYMBOLS_FILE)
    print(f"Fetching ~250d daily history for {len(symbols)} symbols "
          f"(55+ trading days of buffer needed for stable EMA50/ADX14 and swing structure)...")
    daily_raw = yf.download(symbols, period="250d", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=True, progress=False)
    print(f"Fetching 60d intraday (5m) history for {len(symbols)} symbols...")
    intraday_raw = yf.download(symbols, period="60d", interval="5m", group_by="ticker",
                                auto_adjust=True, threads=True, progress=False)

    print("Evaluating setups...")
    trades, date_range, candidate_count = run_backtest(symbols, daily_raw, intraday_raw)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": date_range[0] if date_range else None,
                   "to": date_range[-1] if date_range else None,
                   "trading_days": len(date_range)},
        "selection": {"top_n_per_day": TOP_N_PER_DAY,
                      "candidates_before_ranking": candidate_count},
        "overall": summarize(trades),
        "by_grade": {g: summarize([t for t in trades if t["grade"] == g])
                      for g in ("A", "B", "C")},
        "trades": trades,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print()
    print(f"Backtest period: {output['period']['from']} to {output['period']['to']} "
          f"({output['period']['trading_days']} trading days)")
    o = output["overall"]
    print(f"Overall (top {TOP_N_PER_DAY}/day): {o['trades']} trades, "
          f"win rate {o['win_rate_pct']}%, expectancy {o['expectancy_pct']}%/trade")
    for g in ("A", "B", "C"):
        s = output["by_grade"][g]
        if s["trades"]:
            print(f"  Grade {g}: {s['trades']:>3} trades, win rate {s['win_rate_pct']:>5.1f}%, "
                  f"expectancy {s['expectancy_pct']:>6.2f}%/trade")
    print(f"Full results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    sys.exit(main())
