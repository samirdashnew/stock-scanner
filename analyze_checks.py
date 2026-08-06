"""
One-off research tool: which quality checks actually predict a winning trade?

Simulates EVERY hard-gate-passing candidate (not just the top N shown), then
for each quality check compares the win rate when it passed vs when it failed.
The "lift" column is what matters - a check with ~0 lift is decoration, and
weighting it in the score actively dilutes the checks that do work.

Also sweeps selectivity (top 1/2/3/5 per day) to measure the real cost of
"always show N picks".

Sample sizes here are small (~2 months of data is all Yahoo gives at 5m
resolution). Treat differences of a few percent as noise, not signal.

Usage:
    ./venv/bin/python analyze_checks.py
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import yfinance as yf

import scanner_core as core
import backtest as bt


def main():
    symbols = bt.load_symbols("fno_stocks.csv")
    print(f"Fetching data for {len(symbols)} symbols...")
    daily_raw = yf.download(symbols, period="250d", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=True, progress=False)
    intraday_raw = yf.download(symbols, period="60d", interval="5m", group_by="ticker",
                                auto_adjust=True, threads=True, progress=False)

    print("Collecting and simulating every candidate...")
    candidates = []
    for symbol in symbols:
        try:
            ddf = daily_raw[symbol] if isinstance(daily_raw.columns, pd.MultiIndex) else daily_raw
            idf = intraday_raw[symbol] if isinstance(intraday_raw.columns, pd.MultiIndex) else intraday_raw
            candidates.extend(bt.collect_candidates(symbol.replace(".NS", ""), ddf, idf))
        except Exception:
            continue

    for c in candidates:
        outcome, exit_price = bt.simulate_trade(
            c["entry"], c["stop_loss"], c["target"], c["_future_bars"])
        c["outcome"] = outcome
        c["pnl_pct"] = (exit_price - c["entry"]) / c["entry"] * 100

    n = len(candidates)
    base_wr = sum(1 for c in candidates if c["outcome"] == "win") / n * 100
    base_exp = sum(c["pnl_pct"] for c in candidates) / n
    print(f"\nAll candidates: {n} trades, baseline win rate {base_wr:.1f}%, "
          f"expectancy {base_exp:+.3f}%/trade\n")

    # --- Per-check predictive value ---
    print(f"{'Check':<32} {'n(pass)':>8} {'WR pass':>8} {'n(fail)':>8} {'WR fail':>8} {'LIFT':>7} {'Exp pass':>9}")
    print("-" * 92)
    check_names = list(candidates[0]["checks"].keys())
    rows = []
    for name in check_names:
        passed = [c for c in candidates if c["checks"][name]]
        failed = [c for c in candidates if not c["checks"][name]]
        if not passed or not failed:
            continue
        wr_p = sum(1 for c in passed if c["outcome"] == "win") / len(passed) * 100
        wr_f = sum(1 for c in failed if c["outcome"] == "win") / len(failed) * 100
        exp_p = sum(c["pnl_pct"] for c in passed) / len(passed)
        rows.append((name, len(passed), wr_p, len(failed), wr_f, wr_p - wr_f, exp_p))
    for r in sorted(rows, key=lambda x: x[5], reverse=True):
        print(f"{r[0]:<32} {r[1]:>8} {r[2]:>7.1f}% {r[3]:>8} {r[4]:>7.1f}% {r[5]:>+6.1f} {r[6]:>+8.3f}%")

    # --- Does the confluence count actually order outcomes? ---
    print(f"\n{'Checks passed':<16} {'n':>6} {'Win rate':>9} {'Expectancy':>11}")
    print("-" * 46)
    by_count = defaultdict(list)
    for c in candidates:
        by_count[c["checks_passed"]].append(c)
    for k in sorted(by_count):
        g = by_count[k]
        wr = sum(1 for c in g if c["outcome"] == "win") / len(g) * 100
        exp = sum(c["pnl_pct"] for c in g) / len(g)
        print(f"{k:<16} {len(g):>6} {wr:>8.1f}% {exp:>+10.3f}%")

    # --- Cost of forcing N picks per day ---
    print(f"\n{'Top N/day':<12} {'Trades':>8} {'Win rate':>9} {'Expectancy':>11}")
    print("-" * 44)
    by_day = defaultdict(list)
    for c in candidates:
        by_day[c["date"]].append(c)
    for top_n in (1, 2, 3, 5):
        taken = []
        for day, pool in by_day.items():
            taken.extend(sorted(pool, key=lambda x: x["score"], reverse=True)[:top_n])
        wr = sum(1 for c in taken if c["outcome"] == "win") / len(taken) * 100
        exp = sum(c["pnl_pct"] for c in taken) / len(taken)
        print(f"{top_n:<12} {len(taken):>8} {wr:>8.1f}% {exp:>+10.3f}%")


if __name__ == "__main__":
    main()
