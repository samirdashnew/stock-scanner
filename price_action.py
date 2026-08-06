"""
price_action.py - structural / price-action analysis from daily OHLCV.

Everything the indicator layer (scanner_core) does is derivative-of-price:
EMAs, RSI, ADX. This module adds what a discretionary trader actually looks
at on the chart - where the swing highs and lows are, whether there's clear
air above the breakout or a wall of old resistance sitting on top of it,
whether the stock coiled into a tight range before breaking out, and whether
the higher-high/higher-low structure is intact.

CAUSALITY - this is the part that's easy to get wrong and would silently
inflate every backtest number:
  - A swing point at bar i is only *confirmed* once SWING_RIGHT more bars
    have printed. `find_swings` returns `confirmed_at` for exactly this
    reason, and `analyze` refuses to look at any swing whose confirmed_at
    is not strictly before the trade day.
  - Every per-day lookup slices bars strictly before the trade day.
Nothing in this module can see the future.
"""

from __future__ import annotations

import pandas as pd

# --- Structure detection ------------------------------------------------

SWING_LEFT = 2               # bars either side that a pivot must dominate
SWING_RIGHT = 2
SR_LOOKBACK_DAYS = 120       # how far back to gather S/R zones
ZONE_MERGE_ATR_FRAC = 0.6    # swings within this * ATR merge into one zone
SQUEEZE_WINDOW = 20          # Bollinger window for the consolidation check
SQUEEZE_PERCENTILE_WINDOW = 100   # how far back to rank today's band width


def find_swings(daily_df: pd.DataFrame,
                left: int = SWING_LEFT, right: int = SWING_RIGHT) -> list[dict]:
    """Confirmed swing highs/lows over the whole series, computed ONCE per
    symbol. Each swing carries `confirmed_at` - the positional index by which
    a live trader would actually have known about it (i + right)."""
    high, low = daily_df["High"], daily_df["Low"]
    window = left + right + 1

    roll_max = high.rolling(window, center=True).max()
    roll_min = low.rolling(window, center=True).min()
    is_swing_high = (high >= roll_max) & roll_max.notna()
    is_swing_low = (low <= roll_min) & roll_min.notna()

    index = daily_df.index
    swings = []
    for pos, flag in enumerate(is_swing_high.to_numpy()):
        if flag:
            swings.append({"pos": pos, "date": index[pos].date(),
                           "price": float(high.iloc[pos]), "kind": "high",
                           "confirmed_pos": pos + right})
    for pos, flag in enumerate(is_swing_low.to_numpy()):
        if flag:
            swings.append({"pos": pos, "date": index[pos].date(),
                           "price": float(low.iloc[pos]), "kind": "low",
                           "confirmed_pos": pos + right})
    swings.sort(key=lambda s: s["pos"])
    return swings


def _merge_into_zones(prices: list[float], tolerance: float) -> list[dict]:
    """Cluster nearby swing prices into zones. A level touched repeatedly is
    a stronger level than one touched once - `touches` captures that."""
    if not prices:
        return []
    ordered = sorted(prices)
    clusters, current = [], [ordered[0]]
    for price in ordered[1:]:
        if price - current[-1] <= tolerance:
            current.append(price)
        else:
            clusters.append(current)
            current = [price]
    clusters.append(current)
    return [{"price": sum(c) / len(c), "touches": len(c)} for c in clusters]


def compute_squeeze_series(daily_df: pd.DataFrame) -> pd.Series:
    """Bollinger band-width percentile: 0 = tightest coil in recent history,
    100 = widest. Low values mean the stock consolidated, which is the
    classic pre-breakout condition. Computed once per symbol.

    (This is the honest use for Bollinger Bands - they were previously
    computed and thrown away. Here the band *width* is the signal, not the
    bands themselves.)"""
    close = daily_df["Close"]
    mid = close.rolling(SQUEEZE_WINDOW).mean()
    std = close.rolling(SQUEEZE_WINDOW).std()
    width = (2 * std * 2) / mid.replace(0, pd.NA) * 100  # % of price
    return width.rolling(SQUEEZE_PERCENTILE_WINDOW, min_periods=20).rank(pct=True) * 100


def analyze(daily_df: pd.DataFrame, swings: list[dict], squeeze_pctile: pd.Series,
            today, entry: float, stop: float, atr: float) -> dict | None:
    """Price-action picture as of the close BEFORE `today`, for a long setup.

    Returns None when there isn't enough confirmed history to judge.
    """
    prior_mask = daily_df.index.date < today
    prior = daily_df[prior_mask]
    if len(prior) < 30:
        return None
    prior_len = len(prior)   # positional cutoff: swings must be confirmed by here

    # --- Prior day levels: the most-watched intraday reference points ---
    pdh = float(prior["High"].iloc[-1])
    pdl = float(prior["Low"].iloc[-1])

    # --- Support / resistance zones from confirmed swings only ---
    window_start = max(0, prior_len - SR_LOOKBACK_DAYS)
    usable = [s for s in swings
              if s["confirmed_pos"] < prior_len and s["pos"] >= window_start]
    tolerance = max(atr * ZONE_MERGE_ATR_FRAC, entry * 0.001)

    res_zones = _merge_into_zones([s["price"] for s in usable if s["kind"] == "high"], tolerance)
    sup_zones = _merge_into_zones([s["price"] for s in usable if s["kind"] == "low"], tolerance)

    # Resistance that actually matters = above the entry. Ignore anything the
    # stock has already cleared.
    above = [z for z in res_zones if z["price"] > entry * 1.001]
    below = [z for z in sup_zones if z["price"] < entry * 0.999]

    nearest_res = min(above, key=lambda z: z["price"]) if above else None
    nearest_sup = max(below, key=lambda z: z["price"]) if below else None

    risk = max(entry - stop, 1e-9)
    if nearest_res:
        headroom_pct = (nearest_res["price"] - entry) / entry * 100
        headroom_r = (nearest_res["price"] - entry) / risk
    else:
        # Nothing overhead within the lookback - the strongest possible case
        # (blue sky). Represent as a large finite number, not infinity, so it
        # serialises to JSON cleanly.
        headroom_pct = 99.0
        headroom_r = 99.0

    support_distance_pct = ((entry - nearest_sup["price"]) / entry * 100) if nearest_sup else 99.0

    # --- Extension check: how far above the 20-day base are we already? ---
    high_20 = float(prior["High"].iloc[-20:].max())
    high_60 = float(prior["High"].iloc[-60:].max()) if prior_len >= 60 else high_20
    pct_from_20d_high = (entry - high_20) / high_20 * 100
    at_new_20d_high = entry > high_20
    at_new_60d_high = entry > high_60

    # --- Consolidation / squeeze ---
    sq_prior = squeeze_pctile[prior_mask]
    squeeze = float(sq_prior.iloc[-1]) if len(sq_prior) and pd.notna(sq_prior.iloc[-1]) else 50.0

    # --- Market structure: higher highs AND higher lows on the last 2 swings ---
    confirmed = [s for s in usable]
    swing_highs = [s["price"] for s in confirmed if s["kind"] == "high"][-2:]
    swing_lows = [s["price"] for s in confirmed if s["kind"] == "low"][-2:]
    higher_highs = len(swing_highs) == 2 and swing_highs[-1] > swing_highs[0]
    higher_lows = len(swing_lows) == 2 and swing_lows[-1] > swing_lows[0]
    structure_bullish = higher_highs and higher_lows

    return {
        "pdh": round(pdh, 2),
        "pdl": round(pdl, 2),
        "above_pdh": entry > pdh,
        "resistance": round(nearest_res["price"], 2) if nearest_res else None,
        "resistance_touches": nearest_res["touches"] if nearest_res else 0,
        "support": round(nearest_sup["price"], 2) if nearest_sup else None,
        "support_touches": nearest_sup["touches"] if nearest_sup else 0,
        "headroom_pct": round(headroom_pct, 2),
        "headroom_r": round(headroom_r, 2),
        "support_distance_pct": round(support_distance_pct, 2),
        "pct_from_20d_high": round(pct_from_20d_high, 2),
        "at_new_20d_high": bool(at_new_20d_high),
        "at_new_60d_high": bool(at_new_60d_high),
        "squeeze_pctile": round(squeeze, 1),
        "higher_highs": bool(higher_highs),
        "higher_lows": bool(higher_lows),
        "structure_bullish": bool(structure_bullish),
    }
