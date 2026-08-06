"""
Shared setup-evaluation logic: opening range + VWAP + volume, daily-trend
indicators, and price-action structure (see price_action.py).

Used by both morning_scan.py (live daily run) and backtest.py (historical
simulation) so the two never drift apart - the backtest tests the exact
same rules the live scanner runs.

Two-tier filtering:
  * HARD gate  - tradability. Never relaxed.
  * QUALITY    - confluence checks, scored rather than pass/fail, so the
                 scanner can surface the most notable candidates each day.

READ THIS BEFORE TRUSTING THE OUTPUT
------------------------------------
Backtesting over 267 candidates (May-Aug 2026, the full window Yahoo's free
5-minute data allows) found this rule set had NO demonstrable edge:

  * Whole candidate population: 37.5% win rate, -0.17% expectancy per trade.
  * Per-check lift analysis (analyze_checks.py) showed the confluence checks
    barely discriminate. "Clear air to resistance" +0.3pts, "Volume surge"
    +0.7pts; RSI band, EMA stack, prior-day-high and the squeeze check all
    measured NEGATIVE lift. Only ADX (+5.3) and market structure (+4.8) were
    mildly positive, both within noise at this sample size.
  * Confluence count does not order outcomes monotonically - setups passing
    10 of 11 checks won 22% of the time; 9 of 11 won 33%.
  * Every exit variant tested (exit_lab.py: 1R-5R targets, breakeven stops,
    trailing stops) was also negative. Best was -0.139%/trade.

An earlier version of this file reported 53.8% win rate / +0.22% expectancy.
That was 26 trades - roughly +/-10% standard error - i.e. almost certainly
noise, not an edge. The larger sample here supersedes it.

The `score` below is therefore a "most notable setup" heuristic, NOT a
validated predictor of profit, and the checks are shown to the user as
observations rather than as a quality promise. Do not present this tool's
output as a signal service on the strength of these numbers.

The strategy is not proven *unprofitable forever* - two months is a short,
possibly unfavourable window, and it may behave differently in a strongly
trending market. But it is unproven, and that is the honest status.
"""

from __future__ import annotations

import pandas as pd

import price_action as pa

SESSION_MINUTES = 375           # NSE session: 9:15 -> 15:30
OR_BARS = 3                     # first 15 minutes of 5m bars (9:15, 9:20, 9:25)

# --- HARD gate (tradability - never relaxed) ---
MIN_PRICE = 30
MIN_AVG_TURNOVER_CR = 20
MIN_RVOL_HARD = 1.2             # below this there's no real participation
MIN_GAP_PCT = 0.3
MAX_GAP_PCT = 6.0
MAX_RISK_PCT = 1.5
MIN_RISK_PCT = 0.15

REWARD_MULTIPLE = 2.5           # tuned via tune_target.py: same expectancy as 3R
                                # but the target is actually reached ~3x more often

# --- QUALITY thresholds (scored, not hard gates) ---
MIN_RVOL_STRONG = 1.5           # "real" volume conviction
MIN_ADX = 20                    # daily trend strength
RSI_MIN_BUY, RSI_MAX_BUY = 45, 75
MIN_HEADROOM_R = 2.0            # clear air to nearest resistance, in R multiples
MAX_SQUEEZE_PCTILE = 50         # band-width percentile: coiled, not already expanded
MAX_EXT_ABOVE_20D_HIGH = 3.0    # don't chase something already far above its base
MIN_CLOSE_STRENGTH = 0.60       # decision bar closing in the upper part of the day's range

# Grade thresholds, as a count of the quality checks passed (out of 11)
GRADE_A_MIN = 9
GRADE_B_MIN = 7

# SELL/short setups tested net-negative expectancy in EVERY filter combination
# tried (see tune.py output) - never once positive. Disabled until real data
# shows an edge. Note the price-action layer below is long-biased; re-enabling
# shorts properly would need its checks mirrored, not just this flag flipped.
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


def compute_daily_indicators(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Every daily-timeframe indicator as rolling series, computed ONCE over
    the full history. Each row uses data up to and including that row."""
    df = daily_df.dropna(subset=["Close", "Volume"]).copy()
    high, low, close, volume = df["High"], df["Low"], df["Close"], df["Volume"]

    df["ema9"] = _ema(close, 9)
    df["ema15"] = _ema(close, 15)
    df["ema50"] = _ema(close, 50)
    df["rsi14"] = _rsi(close, 14)
    df["adx14"] = _adx(high, low, close, 14)

    prev_c = close.shift(1)
    tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["avg_volume_20d"] = volume.rolling(20).mean()
    df["avg_turnover_cr"] = (close * df["avg_volume_20d"]) / 1e7

    return df


def prepare_symbol(daily_df: pd.DataFrame) -> dict | None:
    """Everything that only needs computing once per symbol: indicators,
    confirmed swing points, and the squeeze percentile series. Call this once,
    then evaluate_setup() per day - never recompute inside a day loop."""
    indicators = compute_daily_indicators(daily_df)
    if len(indicators) < 55:
        return None
    return {
        "indicators": indicators,
        "swings": pa.find_swings(indicators),
        "squeeze": pa.compute_squeeze_series(indicators),
    }


def prior_day_stats(indicators_df: pd.DataFrame, today) -> dict | None:
    """Cheap lookup of the last row strictly before `today`. Never leaks
    same-day information."""
    prior = indicators_df[indicators_df.index.date < today]
    if len(prior) < 55:  # enough history for a stable EMA50/ADX14
        return None

    row = prior.iloc[-1]
    prev_close = float(row["Close"])
    avg_volume_20d = float(row["avg_volume_20d"])
    if pd.isna(avg_volume_20d) or avg_volume_20d <= 0 or prev_close < MIN_PRICE:
        return None

    avg_turnover_cr = float(row["avg_turnover_cr"])
    if avg_turnover_cr < MIN_AVG_TURNOVER_CR:
        return None

    return {
        "prev_close": prev_close,
        "avg_volume_20d": avg_volume_20d,
        "avg_turnover_cr": avg_turnover_cr,
        "atr14": float(row["atr14"]),
        "ema9": float(row["ema9"]),
        "ema15": float(row["ema15"]),
        "ema50": float(row["ema50"]),
        "rsi14": float(row["rsi14"]),
        "adx14": float(row["adx14"]),
    }


def _grade_from(passed: int) -> str:
    if passed >= GRADE_A_MIN:
        return "A"
    if passed >= GRADE_B_MIN:
        return "B"
    return "C"


def evaluate_setup(symbol: str, ctx: dict, today_bars: pd.DataFrame,
                    today, decision_bar_count: int) -> dict | None:
    """Evaluate a long setup for one symbol on one day.

    Returns None if the setup fails the HARD gate (not tradable at any grade)
    or there isn't enough data. Otherwise returns the setup with its quality
    checks, score and A/B/C grade.
    """
    if len(today_bars) < max(OR_BARS + 1, decision_bar_count):
        return None

    stats = prior_day_stats(ctx["indicators"], today)
    if stats is None:
        return None

    or_df = today_bars.iloc[:OR_BARS]
    or_high = float(or_df["High"].max())
    or_low = float(or_df["Low"].min())

    known = today_bars.iloc[:decision_bar_count]
    entry = float(known["Close"].iloc[-1])
    if entry < MIN_PRICE:
        return None

    typical_price = (known["High"] + known["Low"] + known["Close"]) / 3
    cum_vol = known["Volume"].cumsum()
    vwap_series = (typical_price * known["Volume"]).cumsum() / cum_vol.replace(0, pd.NA)
    vwap = float(vwap_series.iloc[-1]) if pd.notna(vwap_series.iloc[-1]) else entry

    volume_so_far = float(known["Volume"].sum())
    minutes_elapsed = decision_bar_count * 5
    fraction_of_day = min(1.0, minutes_elapsed / SESSION_MINUTES)
    expected_volume = stats["avg_volume_20d"] * fraction_of_day
    rvol = volume_so_far / expected_volume if expected_volume > 0 else 0

    prev_close = stats["prev_close"]
    gap_pct = (entry - prev_close) / prev_close * 100
    risk_pct = (entry - or_low) / entry * 100

    # --- HARD gate: tradability. Fail any -> not a candidate at all. ---
    if not (entry > or_high
            and entry > vwap
            and rvol >= MIN_RVOL_HARD
            and MIN_GAP_PCT <= gap_pct <= MAX_GAP_PCT
            and MIN_RISK_PCT <= risk_pct <= MAX_RISK_PCT):
        return None

    stop_loss = or_low
    target = entry + REWARD_MULTIPLE * (entry - stop_loss)

    # --- Price action / structure ---
    pa_data = pa.analyze(ctx["indicators"], ctx["swings"], ctx["squeeze"],
                          today, entry, stop_loss, stats["atr14"])
    if pa_data is None:
        return None

    # Where in today's range so far is price sitting? Closing near the highs
    # is conviction; closing mid-range after a breakout is a warning.
    day_high = float(known["High"].max())
    day_low = float(known["Low"].min())
    day_range = day_high - day_low
    close_strength = (entry - day_low) / day_range if day_range > 0 else 0.5

    # --- QUALITY checks (scored, human-readable) ---
    checks = {
        "Above 50 EMA (daily uptrend)": prev_close > stats["ema50"],
        "EMA 9/15/50 stacked": stats["ema9"] > stats["ema15"] > stats["ema50"],
        "Strong trend (ADX ≥ 20)": stats["adx14"] >= MIN_ADX,
        "RSI in healthy band": RSI_MIN_BUY <= stats["rsi14"] <= RSI_MAX_BUY,
        "Volume surge (RVOL ≥ 1.5)": rvol >= MIN_RVOL_STRONG,
        "Clear air to resistance": pa_data["headroom_r"] >= MIN_HEADROOM_R,
        "Above prior day high": pa_data["above_pdh"],
        "Broke from tight base": pa_data["squeeze_pctile"] <= MAX_SQUEEZE_PCTILE,
        "Higher highs & lows": pa_data["structure_bullish"],
        "Not overextended": pa_data["pct_from_20d_high"] <= MAX_EXT_ABOVE_20D_HIGH,
        "Holding near day's high": close_strength >= MIN_CLOSE_STRENGTH,
    }
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    grade = _grade_from(passed)

    # Ranking score. Confluence count dominates (that's what the grade means),
    # with continuous tie-breakers so two same-grade setups still order
    # sensibly - more headroom, more volume, tighter stop, stronger trend.
    score = (passed * 10
             + min(pa_data["headroom_r"], 10) * 2
             + min(rvol, 5) * 3
             + (MAX_RISK_PCT - risk_pct) * 3
             + min(stats["adx14"], 50) * 0.2
             + close_strength * 5)

    return {
        "symbol": symbol,
        "direction": "buy",
        "entry": entry,
        "stop_loss": stop_loss,
        "target": target,
        "risk_pct": risk_pct,
        "reward_multiple": REWARD_MULTIPLE,
        "gap_pct": gap_pct,
        "rvol_early": rvol,
        "vwap": vwap,
        "or_high": or_high,
        "or_low": or_low,
        "atr14": stats["atr14"],
        "avg_turnover_cr": stats["avg_turnover_cr"],
        "ema9": stats["ema9"], "ema15": stats["ema15"], "ema50": stats["ema50"],
        "rsi14": stats["rsi14"], "adx14": stats["adx14"],
        "close_strength": close_strength,
        # price action
        "support": pa_data["support"],
        "resistance": pa_data["resistance"],
        "support_touches": pa_data["support_touches"],
        "resistance_touches": pa_data["resistance_touches"],
        "headroom_pct": pa_data["headroom_pct"],
        "headroom_r": pa_data["headroom_r"],
        "pdh": pa_data["pdh"],
        "pdl": pa_data["pdl"],
        "squeeze_pctile": pa_data["squeeze_pctile"],
        "at_new_20d_high": pa_data["at_new_20d_high"],
        "at_new_60d_high": pa_data["at_new_60d_high"],
        "pct_from_20d_high": pa_data["pct_from_20d_high"],
        # grading
        "checks": checks,
        "checks_passed": passed,
        "checks_total": total,
        "grade": grade,
        "score": score,
    }
