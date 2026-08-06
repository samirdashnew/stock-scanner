"""
One-off research tool: does a different EXIT rescue this strategy?

Entry-side analysis (analyze_checks.py) showed the confluence checks barely
discriminate - the population of hard-gate candidates sits near 37% win rate
with negative expectancy regardless of which indicators are stacked on top.

But at a 2.5R target the breakeven win rate is only ~29%, so 37% *should* be
profitable. It isn't, because only ~1 in 5 "wins" ever reaches the target -
winners drift to a small end-of-day close while losers take the full stop.
That asymmetry is an exit problem, not an entry problem.

This tests exit variants on the identical candidate set so the comparison is
apples-to-apples: same entries, only the management rule changes.

Usage:
    ./venv/bin/python exit_lab.py
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

import backtest as bt


def sim_fixed_r(c, reward, be_after=None, trail_after=None):
    """Simulate one candidate.
      reward      - target as a multiple of initial risk
      be_after    - move stop to breakeven once price has gone this many R
      trail_after - after this many R, trail the stop under each bar's low
    """
    entry, stop0 = c["entry"], c["stop_loss"]
    risk = entry - stop0
    if risk <= 0:
        return None
    target = entry + reward * risk
    stop = stop0
    peak = entry

    for _, bar in c["_future_bars"].iterrows():
        hi, lo = float(bar["High"]), float(bar["Low"])
        # Conservative: stop is checked before target within a bar, since
        # intra-bar ordering is unknown.
        if lo <= stop:
            return (stop - entry) / entry * 100
        if hi >= target:
            return (target - entry) / entry * 100

        peak = max(peak, hi)
        gain_r = (peak - entry) / risk
        if be_after is not None and gain_r >= be_after:
            stop = max(stop, entry)
        if trail_after is not None and gain_r >= trail_after:
            stop = max(stop, lo)

    last = float(c["_future_bars"]["Close"].iloc[-1])
    return (last - entry) / entry * 100


def report(name, pnls):
    pnls = [p for p in pnls if p is not None]
    if not pnls:
        print(f"{name:<44} (no trades)")
        return
    wins = [p for p in pnls if p > 0]
    wr = len(wins) / len(pnls) * 100
    exp = sum(pnls) / len(pnls)
    total = sum(pnls)
    print(f"{name:<44} {len(pnls):>6} {wr:>7.1f}% {exp:>+9.3f}% {total:>+9.1f}%")


def main():
    symbols = bt.load_symbols("fno_stocks.csv")
    print(f"Fetching data for {len(symbols)} symbols...")
    daily_raw = yf.download(symbols, period="250d", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=True, progress=False)
    intraday_raw = yf.download(symbols, period="60d", interval="5m", group_by="ticker",
                                auto_adjust=True, threads=True, progress=False)

    candidates = []
    for symbol in symbols:
        try:
            ddf = daily_raw[symbol] if isinstance(daily_raw.columns, pd.MultiIndex) else daily_raw
            idf = intraday_raw[symbol] if isinstance(intraday_raw.columns, pd.MultiIndex) else intraday_raw
            candidates.extend(bt.collect_candidates(symbol.replace(".NS", ""), ddf, idf))
        except Exception:
            continue
    print(f"Testing exit variants on the same {len(candidates)} entries.\n")

    print(f"{'Exit rule':<44} {'n':>6} {'WinRate':>8} {'Exp/trade':>10} {'Total':>10}")
    print("-" * 82)

    variants = [
        ("1.0R fixed target",                 dict(reward=1.0)),
        ("1.5R fixed target",                 dict(reward=1.5)),
        ("2.0R fixed target",                 dict(reward=2.0)),
        ("2.5R fixed target (current)",       dict(reward=2.5)),
        ("1.5R + breakeven stop after 1R",    dict(reward=1.5, be_after=1.0)),
        ("2.5R + breakeven stop after 1R",    dict(reward=2.5, be_after=1.0)),
        ("2.5R + trail bar-lows after 1R",    dict(reward=2.5, trail_after=1.0)),
        ("2.5R + trail bar-lows after 1.5R",  dict(reward=2.5, trail_after=1.5)),
        ("5R + trail bar-lows after 1R",      dict(reward=5.0, trail_after=1.0)),
        ("5R + trail after 1R + BE at 1R",    dict(reward=5.0, trail_after=1.0, be_after=1.0)),
    ]
    for name, kwargs in variants:
        report(name, [sim_fixed_r(c, **kwargs) for c in candidates])


if __name__ == "__main__":
    main()
