"""
One-off: sweep REWARD_MULTIPLE specifically, and report not just win
rate/expectancy but what fraction of "wins" actually hit the stated target
vs just closed positive at EOD - the realism check that showed the 3R
target was almost never actually reached intraday.
"""

from __future__ import annotations

import csv
import pandas as pd
import yfinance as yf

import scanner_core as core
import backtest as bt


def load_symbols(path: str) -> list[str]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [row["symbol"].strip() + ".NS" for row in reader if row["symbol"].strip()]


def run_config(symbols, daily_raw, intraday_raw, **overrides):
    for k, v in overrides.items():
        setattr(core, k, v)
    all_trades = []
    for symbol in symbols:
        try:
            ddf = daily_raw[symbol] if isinstance(daily_raw.columns, pd.MultiIndex) else daily_raw
            idf = intraday_raw[symbol] if isinstance(intraday_raw.columns, pd.MultiIndex) else intraday_raw
            all_trades.extend(bt.backtest_symbol(symbol.replace(".NS", ""), ddf, idf))
        except Exception:
            continue
    return all_trades


def analyze(trades):
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    true_target_hits = [t for t in wins if abs(t["exit_price"] - t["target"]) < 0.01]
    true_stop_hits = [t for t in losses if abs(t["exit_price"] - t["stop_loss"]) < 0.01]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    expectancy = sum(t["pnl_pct"] for t in trades) / len(trades) if trades else 0
    target_hit_rate = len(true_target_hits) / len(wins) * 100 if wins else 0
    return {
        "trades": len(trades), "win_rate": win_rate, "expectancy": expectancy,
        "wins": len(wins), "target_hits": len(true_target_hits), "target_hit_rate": target_hit_rate,
        "losses": len(losses), "stop_hits": len(true_stop_hits),
    }


def main():
    symbols = load_symbols("fno_stocks.csv")
    print(f"Fetching data for {len(symbols)} symbols once, reused across all reward multiples...")
    daily_raw = yf.download(symbols, period="250d", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=True, progress=False)
    intraday_raw = yf.download(symbols, period="60d", interval="5m", group_by="ticker",
                                auto_adjust=True, threads=True, progress=False)

    base = dict(USE_TREND_FILTER=True, USE_EMA_STACK=True, USE_ADX_FILTER=True, MIN_ADX=20,
                USE_RSI_FILTER=True, RSI_MIN_BUY=45, RSI_MAX_BUY=75,
                RSI_MIN_SELL=25, RSI_MAX_SELL=55, MIN_RVOL=1.5)

    print(f"\n{'Reward':>7} {'Trades':>7} {'WinRate':>8} {'Expectancy':>11} {'TargetHitRate':>14}  (of the wins, how many actually hit the target price)")
    print("-" * 90)
    for reward in (1.0, 1.5, 2.0, 2.5, 3.0):
        core.REWARD_MULTIPLE = reward
        trades = run_config(symbols, daily_raw, intraday_raw, **base, REWARD_MULTIPLE=reward)
        stats = analyze(trades)
        print(f"{reward:>6.1f}R {stats['trades']:>7} {stats['win_rate']:>7.1f}% "
              f"{stats['expectancy']:>10.2f}% {stats['target_hit_rate']:>13.1f}%  "
              f"({stats['target_hits']}/{stats['wins']} wins hit target)")


if __name__ == "__main__":
    main()
