import math
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List
import numpy as np
import pandas as pd


@dataclass
class PreOrderSetup:
    symbol: str
    direction: str  # "LIMIT LONG (BUY THE DIP)" or "LIMIT SHORT (SELL THE RIP)"
    current_price: float
    limit_entry_price: float
    distance_pct: float
    distance_usd: float
    tight_stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: str
    setup_zone: str
    setup_reason: str
    estimated_fill_time: str


@dataclass
class SwingSetup:
    symbol: str
    timeframe: str  # "1h" or "4h"
    direction: str  # "SWING LONG 🟢" or "SWING SHORT 🔴" or "RANGE CONSOLIDATION ⏳"
    confidence: int  # 0-100%
    current_price: float
    entry_level: float
    entry_zone: str
    stop_loss: float
    target_1: float
    target_1_gain_pct: float
    target_2: float
    target_2_gain_pct: float
    risk_reward: str
    trend_regime: str  # e.g., "Macro Bullish Expansion (Above 200 EMA)"
    catalyst_reason: str
    estimated_hold_duration: str  # e.g., "12h - 48h" or "2 - 5 days"


@dataclass
class ScalpRecommendation:
    action: str  # "SCALP LONG", "SCALP SHORT", "WAIT / NO TRADE"
    confidence: int  # 0-100%
    urgency: str  # "Immediate Entry", "Wait for Pullback", "Wait for Breakout", "Neutral - Stay Out"
    reasoning: str  # Clear actionable explanation
    entry_zone: str  # e.g., "$78,720 - $78,750"
    tight_stop_loss: float
    scalp_tp1: float
    scalp_tp2: float
    risk_reward: str
    invalidation_rule: str
    estimated_hold_time: str  # e.g., "5 - 25 mins"
    is_mtf_confluence: bool = False
    mtf_description: str = ""
    pre_order: Optional[PreOrderSetup] = None


@dataclass
class TechnicalAnalysisResult:
    symbol: str
    interval: str
    current_price: float
    price_change_24h_pct: float
    high_24h: float
    low_24h: float
    volume_24h: float

    # Market Info
    volatility_status: str
    volatility_atr_pct: float
    asset_strength_volume: int
    volume_result: int
    sentiment: str

    # Technical Overview
    resistance_1: float
    support_1: float
    resistance_2: float
    support_2: float
    pivot_point: float

    rsi_value: float
    rsi_status: str
    
    macd_status: str
    macd_value: float
    macd_signal: float
    macd_hist: float

    ma_trend_status: str
    ema_20: float
    ema_50: float
    ema_200: Optional[float]

    bb_upper: float
    bb_lower: float
    bb_status: str

    # Signal Strength
    overall_signal: str
    signal_strength_pct: int
    market_condition: str
    
    # Suggested Risk Management
    suggested_entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: str

    # AI Scalp Recommendation & Pre-Measure
    scalp: Optional[ScalpRecommendation] = None
    pre_order: Optional[PreOrderSetup] = None
    swing: Optional[SwingSetup] = None


class CryptoAnalyzer:
    """Calculates technical indicators and generates multi-factor market signals & scalping advice."""

    @staticmethod
    def calculate_ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def calculate_sma(series: pd.Series, window: int) -> pd.Series:
        return series.rolling(window=window).mean()

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_macd(
        series: pd.Series, fast: int = 12, slow: int = 26, signal_span: int = 9
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=signal_span, adjust=False).mean()
        hist = macd - signal
        return macd, signal, hist

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        return atr

    @staticmethod
    def calculate_bollinger_bands(
        series: pd.Series, window: int = 20, num_std: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        sma = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        upper = sma + (std * num_std)
        lower = sma - (std * num_std)
        return upper, sma, lower

    @classmethod
    def generate_swing_setup(
        cls, df: pd.DataFrame, symbol: str, timeframe: str = "4h"
    ) -> Optional[SwingSetup]:
        """
        Generates High-Win-Rate 1h / 4h Swing Trade Setups with Macro Hold Projections.
        Uses 200 EMA Macro Regime, Multi-Week Support/Resistance, and MACD Expansions.
        """
        if df is None or len(df) < 40:
            return None

        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        current_p = float(closes.iloc[-1])

        ema_20 = cls.calculate_ema(closes, 20).iloc[-1]
        ema_50 = cls.calculate_ema(closes, 50).iloc[-1]
        ema_200 = cls.calculate_ema(closes, 200).iloc[-1] if len(df) >= 200 else cls.calculate_ema(closes, len(df)).iloc[-1]

        rsi = cls.calculate_rsi(closes, 14).iloc[-1]
        macd, signal, hist = cls.calculate_macd(closes, 12, 26, 9)
        h_val = hist.iloc[-1]
        atr = cls.calculate_atr(df, 14).iloc[-1]

        # Key Multi-Period Levels
        period_high = float(highs.iloc[-30:].max())
        period_low = float(lows.iloc[-30:].min())
        pivot = (period_high + period_low + current_p) / 3.0
        r1 = (2 * pivot) - period_low
        s1 = (2 * pivot) - period_high
        r2 = pivot + (period_high - period_low)
        s2 = pivot - (period_high - period_low)

        # Macro Trend Regime
        is_above_200 = current_p >= ema_200
        is_golden_cross = ema_50 >= ema_200
        is_death_cross = ema_50 < ema_200

        hold_duration = "12h - 48h (Swing)" if timeframe == "1h" else "2 - 6 Days (Macro Hold)"

        if current_p > ema_50 and (is_above_200 or h_val > 0) and rsi >= 45:
            direction = "SWING LONG 🟢"
            confidence = min(95, 75 + (10 if is_above_200 else 0) + (10 if h_val > 0 else 0))
            regime = "Macro Bullish Expansion (Above 200 EMA)" if is_above_200 else "Intraday Bullish Rebound"
            
            entry_level = max(s1, current_p - (0.5 * atr))
            stop_loss = min(current_p - (1.8 * atr), s2 if s2 < current_p else current_p * 0.96)
            risk = entry_level - stop_loss
            if risk <= 0:
                risk = entry_level * 0.02
                stop_loss = entry_level - risk

            target_1 = entry_level + (1.8 * risk)
            target_2 = max(r2, entry_level + (3.2 * risk))
            t1_gain = ((target_1 - current_p) / current_p) * 100.0
            t2_gain = ((target_2 - current_p) / current_p) * 100.0

            reason = f"Holding strong above EMA 50 ({ema_50:,.2f}), RSI {rsi:.1f} bullish momentum with macro upside expansion."
            zone = f"${entry_level - (0.3 * atr):,.2f} - ${entry_level + (0.3 * atr):,.2f}"

        elif current_p < ema_50 and (not is_above_200 or h_val < 0) and rsi <= 55:
            direction = "SWING SHORT 🔴"
            confidence = min(95, 75 + (10 if not is_above_200 else 0) + (10 if h_val < 0 else 0))
            regime = "Macro Bearish Breakdown (Below 200 EMA)" if not is_above_200 else "Intraday Bearish Pullback"

            entry_level = min(r1, current_p + (0.5 * atr))
            stop_loss = max(current_p + (1.8 * atr), r2 if r2 > current_p else current_p * 1.04)
            risk = stop_loss - entry_level
            if risk <= 0:
                risk = entry_level * 0.02
                stop_loss = entry_level + risk

            target_1 = entry_level - (1.8 * risk)
            target_2 = min(s2, entry_level - (3.2 * risk))
            t1_gain = ((current_p - target_1) / current_p) * 100.0
            t2_gain = ((current_p - target_2) / current_p) * 100.0

            reason = f"Rejecting below EMA 50 ({ema_50:,.2f}), RSI {rsi:.1f} bearish expansion towards major support."
            zone = f"${entry_level - (0.3 * atr):,.2f} - ${entry_level + (0.3 * atr):,.2f}"

        else:
            direction = "RANGE CONSOLIDATION ⏳"
            confidence = 55
            regime = "Sideways Accumulation / Chop"
            entry_level = current_p
            stop_loss = current_p - (1.5 * atr)
            target_1 = current_p + (1.5 * atr)
            target_2 = current_p + (3.0 * atr)
            t1_gain = ((target_1 - current_p) / current_p) * 100.0
            t2_gain = ((target_2 - current_p) / current_p) * 100.0
            reason = "Price is coiling between major EMAs. Awaiting clear breakout above resistance or below support."
            zone = f"${current_p - (0.5 * atr):,.2f} - ${current_p + (0.5 * atr):,.2f}"

        return SwingSetup(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            confidence=confidence,
            current_price=round(current_p, 4),
            entry_level=round(entry_level, 4),
            entry_zone=zone,
            stop_loss=round(stop_loss, 4),
            target_1=round(target_1, 4),
            target_1_gain_pct=round(t1_gain, 2),
            target_2=round(target_2, 4),
            target_2_gain_pct=round(t2_gain, 2),
            risk_reward="1:2.5 to 1:3.5",
            trend_regime=regime,
            catalyst_reason=reason,
            estimated_hold_duration=hold_duration,
        )

    @classmethod
    def generate_preorder_setup(
        cls, df: pd.DataFrame, symbol: str, current_price: float, atr: float
    ) -> Optional[PreOrderSetup]:
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]

        ema_20 = cls.calculate_ema(closes, 20).iloc[-1]
        ema_50 = cls.calculate_ema(closes, 50).iloc[-1]

        recent_high = highs.iloc[-24:].max()
        recent_low = lows.iloc[-24:].min()
        recent_close = closes.iloc[-1]
        pivot = (recent_high + recent_low + recent_close) / 3.0
        r1 = (2 * pivot) - recent_low
        s1 = (2 * pivot) - recent_high

        is_bullish = current_price >= ema_50 or ema_20 >= ema_50

        if is_bullish:
            direction = "LIMIT LONG 🟢 (Buy The Dip)"
            limit_entry = min(current_price * 0.9985, max(s1, current_price - (0.8 * atr)))
            distance_usd = current_price - limit_entry
            distance_pct = (distance_usd / current_price) * 100.0
            
            tight_sl = limit_entry - (1.1 * atr)
            risk = limit_entry - tight_sl
            if risk <= 0:
                risk = limit_entry * 0.008
                tight_sl = limit_entry - risk
            tp1 = limit_entry + (1.5 * risk)
            tp2 = limit_entry + (2.8 * risk)
            zone_low = limit_entry - (0.2 * atr)
            zone_high = limit_entry + (0.2 * atr)
            setup_zone = f"${zone_low:,.2f} - ${zone_high:,.2f}"
            reason = "Support Floor S1 & EMA 20 Demand Pullback Zone"
            fill_time = "Approx. 5 - 20 mins"

        else:
            direction = "LIMIT SHORT 🔴 (Sell The Rip)"
            limit_entry = max(current_price * 1.0015, min(r1, current_price + (0.8 * atr)))
            distance_usd = limit_entry - current_price
            distance_pct = (distance_usd / current_price) * 100.0
            
            tight_sl = limit_entry + (1.1 * atr)
            risk = tight_sl - limit_entry
            if risk <= 0:
                risk = limit_entry * 0.008
                tight_sl = limit_entry + risk
            tp1 = limit_entry - (1.5 * risk)
            tp2 = limit_entry - (2.8 * risk)
            zone_low = limit_entry - (0.2 * atr)
            zone_high = limit_entry + (0.2 * atr)
            setup_zone = f"${zone_low:,.2f} - ${zone_high:,.2f}"
            reason = "Resistance Ceiling R1 & EMA 20 Supply Retest Zone"
            fill_time = "Approx. 5 - 20 mins"

        return PreOrderSetup(
            symbol=symbol,
            direction=direction,
            current_price=round(current_price, 4),
            limit_entry_price=round(limit_entry, 4),
            distance_pct=round(distance_pct, 2),
            distance_usd=round(distance_usd, 4),
            tight_stop_loss=round(tight_sl, 4),
            take_profit_1=round(tp1, 4),
            take_profit_2=round(tp2, 4),
            risk_reward_ratio="1:2.5 to 1:2.8",
            setup_zone=setup_zone,
            setup_reason=reason,
            estimated_fill_time=fill_time,
        )

    @classmethod
    def generate_scalp_recommendation(
        cls, df: pd.DataFrame, current_price: float, atr: float, rsi: float, macd_hist: float, symbol: str = ""
    ) -> ScalpRecommendation:
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        volumes = df["volume"]

        ema_9 = cls.calculate_ema(closes, 9).iloc[-1]
        ema_21 = cls.calculate_ema(closes, 21).iloc[-1]
        prev_ema_9 = cls.calculate_ema(closes, 9).iloc[-2]
        prev_ema_21 = cls.calculate_ema(closes, 21).iloc[-2]

        c0, c1 = closes.iloc[-1], closes.iloc[-2]
        o0, o1 = df["open"].iloc[-1], df["open"].iloc[-2]
        h0, l0 = highs.iloc[-1], lows.iloc[-1]

        vol_avg = volumes.rolling(15).mean().iloc[-1]
        vol_surge = volumes.iloc[-1] > (vol_avg * 1.3)

        upper_wick = h0 - max(o0, c0)
        lower_wick = min(o0, c0) - l0
        body_size = abs(c0 - o0) + 1e-8

        bullish_pinbar = lower_wick > (2.0 * body_size)
        bearish_pinbar = upper_wick > (2.0 * body_size)

        long_score = 0
        reasons_long = []

        if ema_9 > ema_21:
            long_score += 2
            reasons_long.append("EMA 9 > EMA 21 bullish momentum")
        if prev_ema_9 <= prev_ema_21 and ema_9 > ema_21:
            long_score += 3
            reasons_long.append("Fresh EMA 9/21 crossover")
        if 35 <= rsi <= 62 and c0 > o0:
            long_score += 2
            reasons_long.append(f"RSI ({rsi:.1f}) upward headroom")
        if bullish_pinbar:
            long_score += 2
            reasons_long.append("Bullish pinbar wick rejection")
        if macd_hist > 0:
            long_score += 1
            reasons_long.append("MACD histogram ticking positive")
        if vol_surge and c0 > o0:
            long_score += 2
            reasons_long.append("Volume surge confirming buyer push")

        short_score = 0
        reasons_short = []

        if ema_9 < ema_21:
            short_score += 2
            reasons_short.append("EMA 9 < EMA 21 bearish momentum")
        if prev_ema_9 >= prev_ema_21 and ema_9 < ema_21:
            short_score += 3
            reasons_short.append("Fresh EMA 9/21 crossover")
        if 38 <= rsi <= 68 and c0 < o0:
            short_score += 2
            reasons_short.append(f"RSI ({rsi:.1f}) breaking downward")
        if bearish_pinbar:
            short_score += 2
            reasons_short.append("Bearish pinbar wick rejection")
        if macd_hist < 0:
            short_score += 1
            reasons_short.append("MACD histogram accelerating negative")
        if vol_surge and c0 < o0:
            short_score += 2
            reasons_short.append("Volume surge confirming seller push")

        is_choppy = abs(ema_9 - ema_21) / current_price < 0.0003 and 45 <= rsi <= 55

        if long_score >= 5 and long_score > short_score and not is_choppy:
            action = "SCALP LONG 🟢"
            confidence = min(94, 65 + long_score * 5)
            urgency = "Immediate Entry (Momentum Buy)" if vol_surge else "Wait for Minor Pullback to EMA 9"
            reasoning = "; ".join(reasons_long[:3])
            
            tight_sl = current_price - (1.1 * atr)
            risk = current_price - tight_sl
            tp1 = current_price + (1.3 * risk)
            tp2 = current_price + (2.4 * risk)
            invalidation = f"Close candle below ${tight_sl:,.2f}"
            hold_time = "5 - 20 mins"

        elif short_score >= 5 and short_score > long_score and not is_choppy:
            action = "SCALP SHORT 🔴"
            confidence = min(94, 65 + short_score * 5)
            urgency = "Immediate Entry (Momentum Sell)" if vol_surge else "Wait for Minor Retest of EMA 9"
            reasoning = "; ".join(reasons_short[:3])
            
            tight_sl = current_price + (1.1 * atr)
            risk = tight_sl - current_price
            tp1 = current_price - (1.3 * risk)
            tp2 = current_price - (2.4 * risk)
            invalidation = f"Close candle above ${tight_sl:,.2f}"
            hold_time = "5 - 20 mins"

        else:
            action = "WAIT / NO TRADE ⏳"
            confidence = 50
            urgency = "Stay Out - Await Clear Breakout"
            if is_choppy:
                reasoning = "Market is in tight sideways chop. Wait for a clear range breakout."
            elif rsi > 72:
                reasoning = "RSI is heavily overbought. High risk of immediate pullback; wait for pullback or short setup."
            elif rsi < 28:
                reasoning = "RSI is heavily oversold. Downward momentum exhausted; wait for base formation before scalping."
            else:
                reasoning = "Mixed signals across moving averages. Wait for EMA 9/21 cross confirmation."

            tight_sl = current_price - (1.0 * atr)
            tp1 = current_price + (1.0 * atr)
            tp2 = current_price + (2.0 * atr)
            invalidation = "Wait for directional breakout candle"
            hold_time = "N/A"

        entry_low = current_price - (0.2 * atr)
        entry_high = current_price + (0.2 * atr)
        entry_zone = f"${entry_low:,.2f} - ${entry_high:,.2f}"

        pre_setup = cls.generate_preorder_setup(df, symbol, current_price, atr)

        return ScalpRecommendation(
            action=action,
            confidence=confidence,
            urgency=urgency,
            reasoning=reasoning,
            entry_zone=entry_zone,
            tight_stop_loss=round(tight_sl, 4),
            scalp_tp1=round(tp1, 4),
            scalp_tp2=round(tp2, 4),
            risk_reward="1:1.5 to 1:2.4",
            invalidation_rule=invalidation,
            estimated_hold_time=hold_time,
            is_mtf_confluence=False,
            pre_order=pre_setup,
        )

    @classmethod
    def analyze_dual_timeframe_confluence(
        cls, df_5m: pd.DataFrame, df_1m: pd.DataFrame, symbol: str
    ) -> Optional[TechnicalAnalysisResult]:
        if df_5m is None or len(df_5m) < 35 or df_1m is None or len(df_1m) < 35:
            return None

        res_5m = cls.analyze(df_5m, symbol, "5m")
        res_1m = cls.analyze(df_1m, symbol, "1m")
        if not res_5m or not res_1m:
            return None

        is_5m_bullish = "BUY" in res_5m.overall_signal or "Upward" in res_5m.ma_trend_status or res_5m.rsi_value >= 50
        is_5m_bearish = "SELL" in res_5m.overall_signal or "Downward" in res_5m.ma_trend_status or res_5m.rsi_value <= 50

        is_1m_long = "LONG" in (res_1m.scalp.action if res_1m.scalp else "")
        is_1m_short = "SHORT" in (res_1m.scalp.action if res_1m.scalp else "")

        if is_5m_bullish and is_1m_long:
            confluence_action = "SCALP LONG 🟢"
            confluence_confidence = min(96, max(82, int((res_5m.signal_strength_pct + res_1m.signal_strength_pct) / 2) + 10))
            mtf_description = "⭐ **DUAL-CONFLUENCE**: 5m Bullish Trend + 1m Precision Momentum Trigger"
            urgency = "High Probability Long • Enter in Limit Zone"
            reasoning = f"5m structure is trending up ({res_5m.ma_trend_status}, RSI {res_5m.rsi_value}) with 1m pullback confirmation."
        elif is_5m_bearish and is_1m_short:
            confluence_action = "SCALP SHORT 🔴"
            confluence_confidence = min(96, max(82, int((res_5m.signal_strength_pct + res_1m.signal_strength_pct) / 2) + 10))
            mtf_description = "⭐ **DUAL-CONFLUENCE**: 5m Bearish Trend + 1m Precision Downside Trigger"
            urgency = "High Probability Short • Enter in Limit Zone"
            reasoning = f"5m structure is in downtrend ({res_5m.ma_trend_status}, RSI {res_5m.rsi_value}) with 1m breakdown confirmation."
        else:
            confluence_action = "WAIT / NO TRADE ⏳"
            confluence_confidence = 50
            mtf_description = "⚠️ **TIMEFRAME CONFLICT**: 5m Macro Trend and 1m Micro Trigger disagree. Staying flat."
            urgency = "Stay Out - Waiting for MTF Alignment"
            reasoning = f"5m indicates {res_5m.overall_signal} while 1m micro shows {res_1m.scalp.action if res_1m.scalp else 'Neutral'}."

        current_p = res_1m.current_price
        atr_5m = res_5m.volatility_atr_pct / 100.0 * current_p

        if "LONG" in confluence_action:
            tight_sl = max(res_5m.support_1, current_p - (1.2 * atr_5m))
            risk = current_p - tight_sl
            if risk <= 0:
                risk = current_p * 0.008
                tight_sl = current_p - risk
            tp1 = current_p + (1.5 * risk)
            tp2 = current_p + (2.8 * risk)
            invalidation = f"5m close below ${tight_sl:,.2f}"
            hold_time = "10 - 45 mins"
        elif "SHORT" in confluence_action:
            tight_sl = min(res_5m.resistance_1, current_p + (1.2 * atr_5m))
            risk = tight_sl - current_p
            if risk <= 0:
                risk = current_p * 0.008
                tight_sl = current_p + risk
            tp1 = current_p - (1.5 * risk)
            tp2 = current_p - (2.8 * risk)
            invalidation = f"5m close above ${tight_sl:,.2f}"
            hold_time = "10 - 45 mins"
        else:
            tight_sl = current_p - (1.0 * atr_5m)
            tp1 = current_p + (1.0 * atr_5m)
            tp2 = current_p + (2.0 * atr_5m)
            invalidation = "Wait for 5m+1m alignment"
            hold_time = "N/A"

        entry_low = current_p - (0.2 * atr_5m)
        entry_high = current_p + (0.2 * atr_5m)
        entry_zone = f"${entry_low:,.2f} - ${entry_high:,.2f}"

        pre_setup = cls.generate_preorder_setup(df_5m, symbol, current_p, atr_5m)

        scalp_rec = ScalpRecommendation(
            action=confluence_action,
            confidence=confluence_confidence,
            urgency=urgency,
            reasoning=reasoning,
            entry_zone=entry_zone,
            tight_stop_loss=round(tight_sl, 4),
            scalp_tp1=round(tp1, 4),
            scalp_tp2=round(tp2, 4),
            risk_reward="1:1.5 to 1:2.8",
            invalidation_rule=invalidation,
            estimated_hold_time=hold_time,
            is_mtf_confluence=True,
            mtf_description=mtf_description,
            pre_order=pre_setup,
        )

        res_5m.scalp = scalp_rec
        res_5m.pre_order = pre_setup
        res_5m.current_price = current_p
        return res_5m

    @classmethod
    def analyze(
        cls, df: pd.DataFrame, symbol: str, interval: str, ticker_24h: Optional[Dict[str, Any]] = None
    ) -> Optional[TechnicalAnalysisResult]:
        if df is None or len(df) < 35:
            return None

        df = df.copy()
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        volumes = df["volume"]
        current_price = float(closes.iloc[-1])

        ema_20 = cls.calculate_ema(closes, 20)
        ema_50 = cls.calculate_ema(closes, 50)
        ema_200 = cls.calculate_ema(closes, 200) if len(df) >= 200 else None

        e20 = ema_20.iloc[-1]
        e50 = ema_50.iloc[-1]
        e200 = ema_200.iloc[-1] if ema_200 is not None else None

        if current_price > e20 > e50:
            ma_trend = "Upward trend"
            trend_score = 1.0
        elif current_price < e20 < e50:
            ma_trend = "Downward trend"
            trend_score = -1.0
        elif current_price > e20:
            ma_trend = "Short-term bullish"
            trend_score = 0.4
        else:
            ma_trend = "Short-term bearish"
            trend_score = -0.4

        rsi_series = cls.calculate_rsi(closes, 14)
        current_rsi = rsi_series.iloc[-1]

        recent_closes = closes.iloc[-10:]
        recent_rsi = rsi_series.iloc[-10:]

        price_making_lower_low = recent_closes.iloc[-1] < recent_closes.iloc[-5]
        rsi_making_higher_low = recent_rsi.iloc[-1] > recent_rsi.iloc[-5]
        price_making_higher_high = recent_closes.iloc[-1] > recent_closes.iloc[-5]
        rsi_making_lower_high = recent_rsi.iloc[-1] < recent_rsi.iloc[-5]

        if price_making_lower_low and rsi_making_higher_low and current_rsi < 45:
            rsi_status = "Bullish divergence"
            rsi_score = 0.9
        elif price_making_higher_high and rsi_making_lower_high and current_rsi > 55:
            rsi_status = "Bearish divergence"
            rsi_score = -0.9
        elif current_rsi >= 70:
            rsi_status = "Overbought"
            rsi_score = -0.5
        elif current_rsi <= 30:
            rsi_status = "Oversold"
            rsi_score = 0.5
        elif current_rsi > 50:
            rsi_status = "Bullish bias"
            rsi_score = 0.3
        else:
            rsi_status = "Bearish bias"
            rsi_score = -0.3

        macd, signal, hist = cls.calculate_macd(closes, 12, 26, 9)
        m_val = macd.iloc[-1]
        s_val = signal.iloc[-1]
        h_val = hist.iloc[-1]
        prev_h = hist.iloc[-2]

        if macd.iloc[-2] <= signal.iloc[-2] and m_val > s_val:
            macd_status = "Bullish crossover"
            macd_score = 1.0
        elif macd.iloc[-2] >= signal.iloc[-2] and m_val < s_val:
            macd_status = "Bearish crossover"
            macd_score = -1.0
        elif h_val > 0 and h_val > prev_h:
            macd_status = "Bullish expansion"
            macd_score = 0.6
        elif h_val < 0 and h_val < prev_h:
            macd_status = "Bearish expansion"
            macd_score = -0.6
        elif h_val > 0:
            macd_status = "Weakening bullish"
            macd_score = 0.2
        else:
            macd_status = "Weakening bearish"
            macd_score = -0.2

        atr_series = cls.calculate_atr(df, 14)
        current_atr = atr_series.iloc[-1]
        atr_pct = (current_atr / current_price) * 100

        if atr_pct > 2.0:
            volatility_status = "Elevated"
        elif atr_pct > 0.8:
            volatility_status = "Moderate"
        else:
            volatility_status = "Low"

        bb_upper, bb_mid, bb_lower = cls.calculate_bollinger_bands(closes, 20, 2.0)
        b_up = bb_upper.iloc[-1]
        b_low = bb_lower.iloc[-1]

        if current_price >= b_up:
            bb_status = "Upper Band Rejection/Break"
            bb_score = -0.3
        elif current_price <= b_low:
            bb_status = "Lower Band Support/Break"
            bb_score = 0.3
        else:
            bb_status = "Inside Bands"
            bb_score = 0.0

        vol_ma = volumes.rolling(20).mean().iloc[-1]
        current_vol = volumes.iloc[-1]
        vol_ratio = current_vol / (vol_ma + 1e-10)

        price_up = closes.iloc[-1] >= closes.iloc[-2]
        base_strength = min(100, int(50 + (25 if price_up else -25) * min(vol_ratio, 2.0)))
        asset_strength_volume = max(10, min(95, base_strength))
        volume_result = max(15, min(98, int(min(vol_ratio, 2.5) / 2.5 * 100)))

        recent_high = highs.iloc[-24:].max()
        recent_low = lows.iloc[-24:].min()
        recent_close = closes.iloc[-1]

        pivot = (recent_high + recent_low + recent_close) / 3.0
        r1 = (2 * pivot) - recent_low
        s1 = (2 * pivot) - recent_high
        r2 = pivot + (recent_high - recent_low)
        s2 = pivot - (recent_high - recent_low)

        composite_score = (
            (trend_score * 0.30)
            + (rsi_score * 0.25)
            + (macd_score * 0.25)
            + (bb_score * 0.10)
            + ((1 if price_up else -1) * (vol_ratio / 2.0) * 0.10)
        )

        confidence = int(60 + (abs(composite_score) / 1.0) * 38)
        confidence = max(55, min(97, confidence))

        if composite_score >= 0.55:
            overall_signal = "STRONG BUY"
            sentiment = "Strong Buy"
            market_condition = "Bullish breakout setup"
        elif composite_score >= 0.20:
            overall_signal = "BUY"
            sentiment = "Moderate Buy"
            market_condition = "Upward continuation"
        elif composite_score <= -0.55:
            overall_signal = "STRONG SELL"
            sentiment = "Strong Sell"
            market_condition = "Bearish setup"
        elif composite_score <= -0.20:
            overall_signal = "SELL"
            sentiment = "Moderate Sell"
            market_condition = "Downward pressure"
        else:
            overall_signal = "NEUTRAL"
            sentiment = "Neutral"
            market_condition = "Range-bound consolidation"

        if "BUY" in overall_signal:
            suggested_entry = current_price
            stop_loss = max(s1, current_price - (1.5 * current_atr))
            risk = current_price - stop_loss
            if risk <= 0:
                risk = current_price * 0.015
                stop_loss = current_price - risk
            take_profit_1 = current_price + (1.5 * risk)
            take_profit_2 = current_price + (2.5 * risk)
            risk_reward_ratio = "1:2.0"
        elif "SELL" in overall_signal:
            suggested_entry = current_price
            stop_loss = min(r1, current_price + (1.5 * current_atr))
            risk = stop_loss - current_price
            if risk <= 0:
                risk = current_price * 0.015
                stop_loss = current_price + risk
            take_profit_1 = current_price - (1.5 * risk)
            take_profit_2 = current_price - (2.5 * risk)
            risk_reward_ratio = "1:2.0"
        else:
            suggested_entry = current_price
            stop_loss = current_price - (1.5 * current_atr)
            take_profit_1 = current_price + (1.5 * current_atr)
            take_profit_2 = current_price + (3.0 * current_atr)
            risk_reward_ratio = "1:1.5"

        scalp_rec = cls.generate_scalp_recommendation(
            df=df,
            current_price=current_price,
            atr=current_atr,
            rsi=current_rsi,
            macd_hist=h_val,
            symbol=symbol,
        )
        pre_setup = cls.generate_preorder_setup(df, symbol, current_price, current_atr)
        swing_setup = cls.generate_swing_setup(df, symbol, interval) if interval in ("1h", "4h", "1d") else None

        price_change_24h_pct = 0.0
        high_24h = recent_high
        low_24h = recent_low
        volume_24h = volumes.sum()

        if ticker_24h:
            price_change_24h_pct = float(ticker_24h.get("priceChangePercent", 0.0))
            high_24h = float(ticker_24h.get("highPrice", recent_high))
            low_24h = float(ticker_24h.get("lowPrice", recent_low))
            volume_24h = float(ticker_24h.get("volume", volume_24h))

        return TechnicalAnalysisResult(
            symbol=symbol,
            interval=interval,
            current_price=current_price,
            price_change_24h_pct=price_change_24h_pct,
            high_24h=high_24h,
            low_24h=low_24h,
            volume_24h=volume_24h,
            volatility_status=volatility_status,
            volatility_atr_pct=atr_pct,
            asset_strength_volume=asset_strength_volume,
            volume_result=volume_result,
            sentiment=sentiment,
            resistance_1=r1,
            support_1=s1,
            resistance_2=r2,
            support_2=s2,
            pivot_point=pivot,
            rsi_value=round(current_rsi, 2),
            rsi_status=rsi_status,
            macd_status=macd_status,
            macd_value=round(m_val, 4),
            macd_signal=round(s_val, 4),
            macd_hist=round(h_val, 4),
            ma_trend_status=ma_trend,
            ema_20=round(e20, 4),
            ema_50=round(e50, 4),
            ema_200=round(e200, 4) if e200 else None,
            bb_upper=round(b_up, 4),
            bb_lower=round(b_low, 4),
            bb_status=bb_status,
            overall_signal=overall_signal,
            signal_strength_pct=confidence,
            market_condition=market_condition,
            suggested_entry=round(suggested_entry, 6),
            stop_loss=round(stop_loss, 6),
            take_profit_1=round(take_profit_1, 6),
            take_profit_2=round(take_profit_2, 6),
            risk_reward_ratio=risk_reward_ratio,
            scalp=scalp_rec,
            pre_order=pre_setup,
            swing=swing_setup,
        )
