"""
Shared opening-range / VWAP / RVOL scoring logic.

Used by both morning_scan.py (live daily run) and backtest.py (historical
simulation) so the two never drift apart - the backtest tests the exact
same qualification rules the live scanner uses.
"""

from __future__ import annotations

import pandas as pd

SESSION_MINUTES = 375           # NSE session: 9:15 -> 15:30
OR_BARS = 3                     # first 15 minutes of 5m bars (9:15, 9:20, 9:25)

MIN_PRICE = 30
MIN_AVG_TURNOVER_CR = 20
MIN_RVOL = 1.5
MIN_GAP_PCT = 0.3
MAX_GAP_PCT = 6.0
MAX_RISK_PCT = 1.5
MIN_RISK_PCT = 0.15
REWARD_MULTIPLE = 2.0


def prior_day_stats(daily_df: pd.DataFrame, today) -> dict | None:
    """20-day avg volume, prev close, ATR(14) - all computed strictly
    before `today`, so this never leaks same-day information."""
    daily_df = daily_df.dropna(subset=["Close", "Volume"])
    prior = daily_df[daily_df.index.date < today]
    if len(prior) < 21:
        return None

    prev_close = float(prior["Close"].iloc[-1])
    avg_volume_20d = float(prior["Volume"].iloc[-20:].mean())
    if avg_volume_20d <= 0 or prev_close < MIN_PRICE:
        return None

    avg_turnover_cr = (prev_close * avg_volume_20d) / 1e7
    if avg_turnover_cr < MIN_AVG_TURNOVER_CR:
        return None

    high, low, close = prior["High"], prior["Low"], prior["Close"]
    prev_c = close.shift(1)
    tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    atr14 = float(tr.iloc[-14:].mean())

    return {
        "prev_close": prev_close,
        "avg_volume_20d": avg_volume_20d,
        "avg_turnover_cr": avg_turnover_cr,
        "atr14": atr14,
    }


def evaluate_setup(symbol: str, today_bars: pd.DataFrame, stats: dict,
                    decision_bar_count: int) -> dict | None:
    """
    today_bars: today's 5m OHLCV bars, in order, from session start.
    decision_bar_count: how many bars are "known" at decision time
      (6 bars = 30 minutes elapsed, i.e. a 9:45 AM decision point).
    Returns a dict with both a 'bull' and 'bear' evaluation, or None if
    there isn't enough data yet.
    """
    if len(today_bars) < max(OR_BARS + 1, decision_bar_count):
        return None

    or_df = today_bars.iloc[:OR_BARS]
    or_high = float(or_df["High"].max())
    or_low = float(or_df["Low"].min())

    known = today_bars.iloc[:decision_bar_count]
    latest = known.iloc[-1]
    current_price = float(latest["Close"])
    if current_price < MIN_PRICE:
        return None

    typical_price = (known["High"] + known["Low"] + known["Close"]) / 3
    cum_vol = known["Volume"].cumsum()
    vwap_series = (typical_price * known["Volume"]).cumsum() / cum_vol.replace(0, pd.NA)
    vwap = float(vwap_series.iloc[-1]) if pd.notna(vwap_series.iloc[-1]) else current_price

    volume_so_far = float(known["Volume"].sum())
    minutes_elapsed = decision_bar_count * 5
    fraction_of_day = min(1.0, minutes_elapsed / SESSION_MINUTES)
    expected_volume_by_now = stats["avg_volume_20d"] * fraction_of_day
    rvol_early = volume_so_far / expected_volume_by_now if expected_volume_by_now > 0 else 0

    prev_close = stats["prev_close"]
    gap_pct = (current_price - prev_close) / prev_close * 100

    result = {
        "symbol": symbol,
        "entry": current_price,
        "vwap": vwap,
        "rvol_early": rvol_early,
        "gap_pct": gap_pct,
        "or_high": or_high,
        "or_low": or_low,
        "atr14": stats["atr14"],
        "avg_turnover_cr": stats["avg_turnover_cr"],
    }

    # --- Bullish (buy) setup ---
    bull_risk_pct = (current_price - or_low) / current_price * 100
    bull = dict(result)
    bull.update({
        "direction": "buy",
        "stop_loss": or_low,
        "target": current_price + REWARD_MULTIPLE * (current_price - or_low),
        "risk_pct": bull_risk_pct,
        "qualifies": (
            current_price > or_high
            and current_price > vwap
            and rvol_early >= MIN_RVOL
            and MIN_GAP_PCT <= gap_pct <= MAX_GAP_PCT
            and MIN_RISK_PCT <= bull_risk_pct <= MAX_RISK_PCT
        ),
    })
    bull["score"] = (rvol_early * 8) + (gap_pct * 3) + ((MAX_RISK_PCT - bull_risk_pct) * 4)

    # --- Bearish (sell/short) setup - mirror image ---
    bear_risk_pct = (or_high - current_price) / current_price * 100
    bear = dict(result)
    bear.update({
        "direction": "sell",
        "stop_loss": or_high,
        "target": current_price - REWARD_MULTIPLE * (or_high - current_price),
        "risk_pct": bear_risk_pct,
        "qualifies": (
            current_price < or_low
            and current_price < vwap
            and rvol_early >= MIN_RVOL
            and -MAX_GAP_PCT <= gap_pct <= -MIN_GAP_PCT
            and MIN_RISK_PCT <= bear_risk_pct <= MAX_RISK_PCT
        ),
    })
    bear["score"] = (rvol_early * 8) + (abs(gap_pct) * 3) + ((MAX_RISK_PCT - bear_risk_pct) * 4)

    return {"bull": bull, "bear": bear}
