"""Expiry-specific research signals using MEXC index prices, not perpetual PnL."""

from dataclasses import dataclass
import math
import time
from typing import Optional

import numpy as np
import pandas as pd


EXPIRY_MINUTES = 10
MIN_SAMPLES = 30


@dataclass
class EventSignal:
    verdict: str = "WAIT"
    bias: str = "NEUTRAL"
    reason: str = ""
    index_price: Optional[float] = None
    payout_pct: Optional[float] = None
    samples: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    lower_win_rate: Optional[float] = None
    historical_ev: Optional[float] = None


def break_even_rate(payout_pct: float) -> float:
    """Required win rate among non-tied outcomes; payout is NET profit percent."""
    if not math.isfinite(payout_pct) or payout_pct <= 0 or payout_pct > 100:
        raise ValueError("Payout must be a finite net-profit percentage above 0 and at most 100.")
    return 1.0 / (1.0 + payout_pct / 100.0)


def wilson_lower(wins: int, total: int) -> float:
    """95% Wilson lower bound, a screening measure rather than a calibrated forecast."""
    if total == 0:
        return 0.0
    z = 1.96
    p = wins / total
    return (p + z*z/(2*total) - z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))) / (1 + z*z/total)


def _features(df: pd.DataFrame):
    close = df["close"]
    e9 = close.ewm(span=9, adjust=False).mean()
    e21 = close.ewm(span=21, adjust=False).mean()
    e50 = close.ewm(span=50, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).fillna(50)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-close.shift()).abs(),
                    (df["low"]-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    not_extended = (close-e9).abs() <= 1.5*atr
    up = (close > e9) & (e9 > e21) & (e21 > e50) & (close > close.shift(3)) & rsi.between(50, 72)
    down = (close < e9) & (e9 < e21) & (e21 < e50) & (close < close.shift(3)) & rsi.between(28, 50)
    directions = pd.Series(np.where(up & not_extended, 1, np.where(down & not_extended, -1, 0)), index=df.index)
    return directions, atr


def analyze_event(df: Optional[pd.DataFrame], index_price: Optional[float],
                  quote_time: Optional[float], up_payout: Optional[float] = None,
                  down_payout: Optional[float] = None, now: Optional[float] = None) -> EventSignal:
    """Use closed 1m candles and completed, non-overlapping 10m analogues only.

    Historical rates are exploratory in-sample observations, not win probabilities.
    A current index quote is required; spot and contract-last-price fallbacks are forbidden.
    """
    now = time.time() if now is None else now
    for payout in (up_payout, down_payout):
        if payout is not None:
            break_even_rate(payout)
    result = EventSignal()
    if (index_price is None or not math.isfinite(index_price) or index_price <= 0
            or quote_time is None or not math.isfinite(quote_time) or not 0 <= now-quote_time <= 30):
        result.reason = "A fresh MEXC index quote (within 30 seconds) is unavailable."
        return result
    result.index_price = index_price
    required = ["open_time", "open", "high", "low", "close"]
    if df is None or not set(required).issubset(df.columns):
        result.reason = "Index candle history is unavailable."
        return result
    candles = df[required].copy().sort_values("open_time").reset_index(drop=True)
    try:
        candles = candles.astype(float)
    except (ValueError, TypeError):
        result.reason = "Invalid index candle history."
        return result
    if (not np.isfinite(candles.to_numpy()).all() or (candles[["open", "high", "low", "close"]] <= 0).any().any()
            or (candles["high"] < candles[["open", "close", "low"]].max(axis=1)).any()
            or (candles["low"] > candles[["open", "close", "high"]].min(axis=1)).any()):
        result.reason = "Invalid index candle prices."
        return result
    candles = candles[candles["open_time"] + 60000 <= now*1000].reset_index(drop=True)
    if len(candles) < 100:
        result.reason = "At least 100 closed one-minute index candles are required."
        return result
    if not (candles["open_time"].diff().iloc[1:] == 60000).all():
        result.reason = "Index history has gaps or duplicate minutes; retry when complete."
        return result
    if not 0 <= now-(candles["open_time"].iloc[-1]/1000+60) <= 90:
        result.reason = "The latest closed index candle is stale."
        return result
    directions, atr = _features(candles)
    side = int(directions.iloc[-1])
    if side == 0:
        result.reason = "EMA trend, momentum and RSI do not agree, or price is overextended."
        return result
    result.bias = "UP" if side == 1 else "DOWN"
    result.payout_pct = up_payout if side == 1 else down_payout
    # Sample starts are separated by at least the expiry; no overlapping labels.
    i = 50
    while i + EXPIRY_MINUTES < len(candles):
        if directions.iloc[i] != side:
            i += 1
            continue
        move = (candles["close"].iloc[i+EXPIRY_MINUTES] - candles["close"].iloc[i]) * side
        result.wins += int(move > 0)
        result.losses += int(move < 0)
        result.ties += int(move == 0)
        i += EXPIRY_MINUTES
    result.samples = result.wins + result.losses + result.ties
    decisive = result.wins + result.losses
    result.lower_win_rate = wilson_lower(result.wins, decisive)
    if result.payout_pct is not None and result.samples:
        result.historical_ev = (result.wins*result.payout_pct/100-result.losses)/result.samples
    if abs(index_price-candles["close"].iloc[-1]) > 0.5*atr.iloc[-1]:
        result.reason = "Index price has moved over half an ATR since the signal candle; wait for a fresh setup."
    elif result.payout_pct is None:
        result.reason = "Enter the current net-profit payout from MEXC to assess the entry."
    elif decisive < MIN_SAMPLES:
        result.reason = f"Only {decisive} completed non-tied analogues; at least {MIN_SAMPLES} are required."
    elif result.lower_win_rate <= break_even_rate(result.payout_pct) + 0.02:
        result.reason = "Historical evidence does not clear the payout break-even rate plus a 2-point margin."
    else:
        result.verdict = result.bias
        result.reason = "Trend and momentum align; the historical screening threshold passes. Research signal only."
    return result
