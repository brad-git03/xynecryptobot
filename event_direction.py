"""An experimental live-pressure filter for a user's chosen 10m direction."""

from dataclasses import replace
import math
import time

from event_futures import EventSignal, break_even_rate


CHECK_STRATEGY_VERSION = "event-check-v1"
CLOCK_SKEW_ALLOWANCE = 2.0


def normalize_direction(value):
    value = value.strip().upper()
    if value not in ("UP", "DOWN"):
        raise ValueError("Direction must be UP or DOWN. Example: !eventcheck BTC UP 80")
    return value


def summarize_pressure(depth, trades, now=None):
    """Use only timestamped futures depth and trades, without claiming dollar volume.

    Contract-size conversion cancels in same-symbol volume shares. This describes
    recent executed flow and displayed book liquidity, not future settled direction.
    """
    now = time.time() if now is None else now
    result = {"bid_pct": None, "taker_buy_pct": None, "trade_count": 0,
              "book_time": None, "latest_trade_time": None}
    try:
        stamp = float(depth["timestamp"])/1000
        bids = sorted(depth["bids"], key=lambda row: float(row[0]), reverse=True)[:20]
        asks = sorted(depth["asks"], key=lambda row: float(row[0]))[:20]
        levels = bids+asks
        if (bids and asks and -CLOCK_SKEW_ALLOWANCE <= now-stamp <= 10
                and all(math.isfinite(float(row[0])) and float(row[0]) > 0
                        and math.isfinite(float(row[1])) and float(row[1]) >= 0 for row in levels)):
            buy = sum(float(row[1]) for row in bids)
            sell = sum(float(row[1]) for row in asks)
            if buy > 0 and sell > 0 and math.isfinite(buy+sell) and float(bids[0][0]) < float(asks[0][0]):
                result.update(bid_pct=100*buy/(buy+sell), book_time=stamp)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        pass
    try:
        # At most 100 trades from the last 60 seconds; drop stale, future,
        # non-finite, unknown-side and self-traded observations.
        eligible = []
        for trade in trades or []:
            try:
                stamp, qty = float(trade["t"])/1000, float(trade["v"])
                if (trade.get("T") in (1, 2) and trade.get("M") != 1
                        and math.isfinite(stamp) and math.isfinite(qty) and qty > 0
                        and -CLOCK_SKEW_ALLOWANCE <= now-stamp <= 60):
                    eligible.append((stamp, qty, trade["T"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        eligible = sorted(eligible, reverse=True)[:100]
        if len(eligible) >= 20 and now-eligible[0][0] <= 10:
            buy = sum(qty for _, qty, side in eligible if side == 1)
            total = sum(qty for _, qty, _ in eligible)
            result.update(taker_buy_pct=100*buy/total, trade_count=len(eligible),
                          latest_trade_time=eligible[0][0])
    except (TypeError, ValueError):
        pass
    return result


def check_direction(signal: EventSignal, requested, payout, pressure, now=None):
    """The pressure filter can veto a chart signal, never upgrade WAIT into an entry."""
    requested = normalize_direction(requested)
    break_even_rate(payout)
    now = time.time() if now is None else now
    context = dict(pressure or {})
    context["requested_direction"] = requested
    result = replace(signal, verdict="WAIT", payout_pct=payout,
                     strategy_version=CHECK_STRATEGY_VERSION, research_context=context)
    if signal.verdict != requested:
        if signal.bias in ("UP", "DOWN") and signal.bias != requested:
            result.reason = (f"WAIT on {requested}: the chart bias is {signal.bias}, against your chosen side. "
                             "Check the opposite side separately with its own payout; it is not automatically approved.")
        else:
            result.reason = f"{requested} is not supported by the chart/history screen. {signal.reason}"
        return result
    bid, buy = context.get("bid_pct"), context.get("taker_buy_pct")
    book_time, trade_time = context.get("book_time"), context.get("latest_trade_time")
    valid = all(isinstance(v, (int, float)) and math.isfinite(v)
                for v in (bid, buy, book_time, trade_time))
    if (not valid or not 0 <= bid <= 100 or not 0 <= buy <= 100
            or not -CLOCK_SKEW_ALLOWANCE <= now-book_time <= 10
            or not -CLOCK_SKEW_ALLOWANCE <= now-trade_time <= 10
            or context.get("trade_count", 0) < 20):
        result.reason = f"WAIT on {requested}: fresh futures order-book and executed-volume evidence is incomplete."
        return result
    book_share = bid if requested == "UP" else 100-bid
    flow_share = buy if requested == "UP" else 100-buy
    if book_share < 58 or flow_share < 60:
        result.reason = (f"WAIT on {requested}: chart/history checks pass, but live pressure does not agree. "
                         f"Needs at least 58% book liquidity and 60% executed volume on your side.")
    else:
        result.verdict = requested
        result.reason = (f"{requested} is supported by the chart/history screen and the experimental live-pressure filter. "
                         "An improvement in win rate has not yet been demonstrated.")
    return result
