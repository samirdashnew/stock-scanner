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
REWARD_MULTIPLE = 3.0           # tuned via tune.py: 3R outperformed 2R on the buy side

# --- Trend/strength filters (daily timeframe, computed strictly on prior
# days - see prior_day_stats). Tuned via backtest.py / tune.py, not guessed.
# See tune.py's output / README for the comparison table these came from. ---
USE_TREND_FILTER = True     # require price above/below daily EMA50 (trade with the regime)
USE_EMA_STACK = True        # require EMA9/EMA15/EMA50 stacked in trade direction
USE_ADX_FILTER = True       # require daily ADX(14) >= MIN_ADX (avoid choppy/sideways names)
MIN_ADX = 20
USE_RSI_FILTER = True       # avoid chasing an already-exhausted move
RSI_MIN_BUY, RSI_MAX_BUY = 45, 75
RSI_MIN_SELL, RSI_MAX_SELL = 25, 55

# SELL/short setups tested net-negative expectancy in EVERY filter combination
# tried (see tune.py output, -0.07% to -0.66%/trade) - never once positive
# except the unfiltered baseline's tiny, unreliable sample. Disabled by
# default until real data shows an actual edge. Flip to True to re-enable
# for further research/backtesting - do not flip it on for live picks without
# a backtest run first.
SELL_ENABLED = False


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move.clip(lower=0)

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx.fillna(0)


def _bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def prior_day_stats(daily_df: pd.DataFrame, today) -> dict | None:
    """20-day avg volume, prev close, ATR(14), and trend/strength indicators
    (EMA 9/15/50, RSI14, ADX14, Bollinger 20,2) - all computed strictly
    before `today`, so this never leaks same-day information."""
    daily_df = daily_df.dropna(subset=["Close", "Volume"])
    prior = daily_df[daily_df.index.date < today]
    if len(prior) < 55:  # need enough history for a stable EMA50/ADX14
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

    ema9 = float(_ema(close, 9).iloc[-1])
    ema15 = float(_ema(close, 15).iloc[-1])
    ema50 = float(_ema(close, 50).iloc[-1])
    rsi14 = float(_rsi(close, 14).iloc[-1])
    adx14 = float(_adx(high, low, close, 14).iloc[-1])
    bb_upper, bb_mid, bb_lower = _bollinger(close, 20, 2.0)
    bb_upper, bb_mid, bb_lower = float(bb_upper.iloc[-1]), float(bb_mid.iloc[-1]), float(bb_lower.iloc[-1])

    return {
        "prev_close": prev_close,
        "avg_volume_20d": avg_volume_20d,
        "avg_turnover_cr": avg_turnover_cr,
        "atr14": atr14,
        "ema9": ema9,
        "ema15": ema15,
        "ema50": ema50,
        "rsi14": rsi14,
        "adx14": adx14,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
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

    ema9, ema15, ema50 = stats["ema9"], stats["ema15"], stats["ema50"]
    rsi14, adx14 = stats["rsi14"], stats["adx14"]

    trend_up_ok = (not USE_TREND_FILTER) or (prev_close > ema50)
    trend_down_ok = (not USE_TREND_FILTER) or (prev_close < ema50)
    stack_up_ok = (not USE_EMA_STACK) or (ema9 > ema15 > ema50)
    stack_down_ok = (not USE_EMA_STACK) or (ema9 < ema15 < ema50)
    adx_ok = (not USE_ADX_FILTER) or (adx14 >= MIN_ADX)
    rsi_up_ok = (not USE_RSI_FILTER) or (RSI_MIN_BUY <= rsi14 <= RSI_MAX_BUY)
    rsi_down_ok = (not USE_RSI_FILTER) or (RSI_MIN_SELL <= rsi14 <= RSI_MAX_SELL)

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
        "ema9": ema9, "ema15": ema15, "ema50": ema50,
        "rsi14": rsi14, "adx14": adx14,
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
            and trend_up_ok
            and stack_up_ok
            and adx_ok
            and rsi_up_ok
        ),
    })
    bull["score"] = (rvol_early * 8) + (gap_pct * 3) + ((MAX_RISK_PCT - bull_risk_pct) * 4) + (adx14 * 0.5)

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
            and trend_down_ok
            and stack_down_ok
            and adx_ok
            and rsi_down_ok
            and SELL_ENABLED
        ),
    })
    bear["score"] = (rvol_early * 8) + (abs(gap_pct) * 3) + ((MAX_RISK_PCT - bear_risk_pct) * 4) + (adx14 * 0.5)

    return {"bull": bull, "bear": bear}
