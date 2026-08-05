"""
One-off parameter sweep - fetches data once, then tries several filter
combinations from scanner_core.py against the same backtest, to find a
defensible improvement instead of hand-guessing thresholds.

Not meant to run in production / cron. Prints a comparison table and exits.
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

    buy = [t for t in all_trades if t["direction"] == "buy"]
    sell = [t for t in all_trades if t["direction"] == "sell"]
    return bt.summarize(all_trades), bt.summarize(buy), bt.summarize(sell)


def main():
    symbols = load_symbols("fno_stocks.csv")
    print(f"Fetching data for {len(symbols)} symbols once, reused across all configs...")
    daily_raw = yf.download(symbols, period="250d", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=True, progress=False)
    intraday_raw = yf.download(symbols, period="60d", interval="5m", group_by="ticker",
                                auto_adjust=True, threads=True, progress=False)

    configs = [
        ("baseline (no indicator filters)", dict(
            USE_TREND_FILTER=False, USE_EMA_STACK=False, USE_ADX_FILTER=False, USE_RSI_FILTER=False)),
        ("trend + stack + ADX + RSI (current default)", dict(
            USE_TREND_FILTER=True, USE_EMA_STACK=True, USE_ADX_FILTER=True, MIN_ADX=20,
            USE_RSI_FILTER=True, RSI_MIN_BUY=45, RSI_MAX_BUY=75, RSI_MIN_SELL=25, RSI_MAX_SELL=55)),
        ("trend + ADX(15) + RSI, no EMA stack", dict(
            USE_TREND_FILTER=True, USE_EMA_STACK=False, USE_ADX_FILTER=True, MIN_ADX=15,
            USE_RSI_FILTER=True, RSI_MIN_BUY=45, RSI_MAX_BUY=75, RSI_MIN_SELL=25, RSI_MAX_SELL=55)),
        ("trend only (no ADX/RSI/stack)", dict(
            USE_TREND_FILTER=True, USE_EMA_STACK=False, USE_ADX_FILTER=False, USE_RSI_FILTER=False)),
        ("ADX(25) + RSI only, no trend/stack", dict(
            USE_TREND_FILTER=False, USE_EMA_STACK=False, USE_ADX_FILTER=True, MIN_ADX=25,
            USE_RSI_FILTER=True, RSI_MIN_BUY=45, RSI_MAX_BUY=75, RSI_MIN_SELL=25, RSI_MAX_SELL=55)),
        ("trend + ADX(20), no RSI, no stack", dict(
            USE_TREND_FILTER=True, USE_EMA_STACK=False, USE_ADX_FILTER=True, MIN_ADX=20,
            USE_RSI_FILTER=False)),
        ("wider reward (3R) + trend + ADX(20)", dict(
            REWARD_MULTIPLE=3.0, USE_TREND_FILTER=True, USE_EMA_STACK=False,
            USE_ADX_FILTER=True, MIN_ADX=20, USE_RSI_FILTER=False)),
        ("trend+stack+ADX+RSI, 3R (best-winrate combo x wider target)", dict(
            REWARD_MULTIPLE=3.0, USE_TREND_FILTER=True, USE_EMA_STACK=True,
            USE_ADX_FILTER=True, MIN_ADX=20, USE_RSI_FILTER=True,
            RSI_MIN_BUY=45, RSI_MAX_BUY=75, RSI_MIN_SELL=25, RSI_MAX_SELL=55)),
        ("trend+stack+ADX+RSI, 2.5R, RVOL>=2.0", dict(
            REWARD_MULTIPLE=2.5, MIN_RVOL=2.0, USE_TREND_FILTER=True, USE_EMA_STACK=True,
            USE_ADX_FILTER=True, MIN_ADX=20, USE_RSI_FILTER=True,
            RSI_MIN_BUY=45, RSI_MAX_BUY=75, RSI_MIN_SELL=25, RSI_MAX_SELL=55)),
        ("trend+ADX(25)+RSI, 3R, no stack", dict(
            REWARD_MULTIPLE=3.0, USE_TREND_FILTER=True, USE_EMA_STACK=False,
            USE_ADX_FILTER=True, MIN_ADX=25, USE_RSI_FILTER=True,
            RSI_MIN_BUY=45, RSI_MAX_BUY=75, RSI_MIN_SELL=25, RSI_MAX_SELL=55)),
    ]

    print(f"\n{'Config':<62} {'Trades':>7} {'WinRate':>8} {'Expectancy':>11}  |  Buy(n/wr/exp)          Sell(n/wr/exp)")
    print("-" * 145)
    for name, overrides in configs:
        # reset to known baseline defaults before applying overrides each time
        core.MIN_ADX = 20
        core.MIN_RVOL = 1.5
        core.RSI_MIN_BUY, core.RSI_MAX_BUY = 45, 75
        core.RSI_MIN_SELL, core.RSI_MAX_SELL = 25, 55
        core.REWARD_MULTIPLE = 2.0
        overall, buy, sell = run_config(symbols, daily_raw, intraday_raw, **overrides)
        print(f"{name:<62} {overall['trades']:>7} {overall['win_rate_pct']:>7.1f}% "
              f"{overall['expectancy_pct']:>10.2f}%  |  "
              f"{buy['trades']:>3}/{buy['win_rate_pct']:>5.1f}%/{buy['expectancy_pct']:>6.2f}%   "
              f"{sell['trades']:>3}/{sell['win_rate_pct']:>5.1f}%/{sell['expectancy_pct']:>6.2f}%")


if __name__ == "__main__":
    main()
