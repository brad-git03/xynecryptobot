import asyncio
import logging
import re
from typing import Optional, List, Dict, Any, Tuple
import aiohttp
import pandas as pd

logger = logging.getLogger(__name__)

MEXC_SPOT_API_BASE = "https://api.mexc.com/api/v3"
MEXC_CONTRACT_API_BASE = "https://contract.mexc.com/api/v1/contract"

# Interval mapping for Spot
SPOT_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "4h",
    "1d": "1d",
}

# Interval mapping for Contract / Futures
CONTRACT_INTERVAL_MAP = {
    "1m": "Min1",
    "5m": "Min5",
    "15m": "Min15",
    "30m": "Min30",
    "1h": "Min60",
    "4h": "Hour4",
    "1d": "Day1",
}


def resolve_symbols(raw_input: str) -> Tuple[str, str, str]:
    """
    Given any user input (e.g. 'GOLD', 'GOLD(XAU)/USDT', 'BTC/USDT', 'sol', 'XAU_USDT'),
    returns: (display_name, spot_symbol, contract_symbol)
    """
    clean = raw_input.strip().upper()

    # Commodity handling (Gold, Silver, Oil)
    if "GOLD" in clean or "XAU" in clean:
        return "GOLD(XAU)/USDT", "XAUUSDT", "XAU_USDT"
    if "SILVER" in clean or "XAG" in clean:
        return "SILVER(XAG)/USDT", "XAGUSDT", "XAG_USDT"

    # Remove special chars (keep letters and digits)
    alphanumeric = re.sub(r"[^A-Z0-9]", "", clean)

    # Detect quote currency
    quote = "USDT"
    base = alphanumeric
    for q in ["USDT", "USDC", "USD", "BTC", "ETH"]:
        if alphanumeric.endswith(q) and len(alphanumeric) > len(q):
            quote = q
            base = alphanumeric[: -len(q)]
            break

    disp = f"{base}/{quote}"
    spot_sym = f"{base}{quote}"
    contract_sym = f"{base}_{quote}"

    return disp, spot_sym, contract_sym


def format_display_symbol(symbol: str) -> str:
    disp, _, _ = resolve_symbols(symbol)
    return disp


def normalize_symbol(symbol: str) -> str:
    _, spot_sym, _ = resolve_symbols(symbol)
    return spot_sym


class MEXCClient:
    """Async Client for fetching real-time market data directly from MEXC (Spot & Futures)."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_klines(
        self, symbol: str, interval: str = "5m", limit: int = 120
    ) -> Optional[pd.DataFrame]:
        """
        Fetches OHLCV candlestick data for a given symbol and interval.
        Checks Contract/Futures if it's a commodity or futures pair, and fallback to Spot.
        """
        disp, spot_sym, contract_sym = resolve_symbols(symbol)
        session = await self._get_session()

        # 1. If it's a commodity (Gold/Silver), prioritize Contract/Futures endpoint
        is_commodity = any(k in symbol.upper() for k in ["GOLD", "XAU", "SILVER", "XAG"])
        if is_commodity:
            df_contract = await self._get_contract_klines(contract_sym, interval, limit)
            if df_contract is not None and len(df_contract) >= 30:
                return df_contract

        # 2. Try Spot API
        spot_interval = SPOT_INTERVAL_MAP.get(interval, interval)
        try:
            async with session.get(
                f"{MEXC_SPOT_API_BASE}/klines",
                params={"symbol": spot_sym, "interval": spot_interval, "limit": limit},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) >= 30:
                        df = pd.DataFrame(
                            data,
                            columns=[
                                "open_time",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "close_time",
                                "quote_volume",
                            ],
                        )
                        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
                        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
                            df[col] = df[col].astype(float)
                        return df.sort_values("open_time").reset_index(drop=True)
        except Exception as e:
            logger.debug(f"Spot klines failed for {symbol}: {e}")

        # 3. Fallback to Contract / Futures API (e.g. BTC_USDT, SOL_USDT)
        return await self._get_contract_klines(contract_sym, interval, limit)

    async def _get_contract_klines(
        self, contract_symbol: str, interval: str = "5m", limit: int = 120
    ) -> Optional[pd.DataFrame]:
        """Fetches candlestick data from MEXC Futures/Contract API."""
        session = await self._get_session()
        c_interval = CONTRACT_INTERVAL_MAP.get(interval, "Min5")
        url = f"{MEXC_CONTRACT_API_BASE}/kline/{contract_symbol}"
        params = {"interval": c_interval}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                res = await resp.json()
                data = res.get("data")
                if not data or not isinstance(data, dict):
                    return None

                times = data.get("time", [])
                if len(times) < 30:
                    return None

                df = pd.DataFrame(
                    {
                        "open_time": [t * 1000 for t in times],
                        "open": [float(x) for x in data.get("open", [])],
                        "high": [float(x) for x in data.get("high", [])],
                        "low": [float(x) for x in data.get("low", [])],
                        "close": [float(x) for x in data.get("close", [])],
                        "volume": [float(x) for x in data.get("vol", [])],
                    }
                )
                df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
                df["quote_volume"] = df["volume"] * df["close"]
                df = df.tail(limit).reset_index(drop=True)
                return df
        except Exception as e:
            logger.error(f"Contract klines error for {contract_symbol}: {e}")
            return None

    async def get_order_book_depth(self, symbol: str, limit: int = 20) -> Optional[Dict[str, Any]]:
        """
        Fetches live order book bids/asks and calculates buyer/seller liquidity depth imbalance.
        """
        disp, spot_sym, contract_sym = resolve_symbols(symbol)
        session = await self._get_session()

        # Try Spot Depth
        try:
            async with session.get(f"{MEXC_SPOT_API_BASE}/depth", params={"symbol": spot_sym, "limit": limit}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                    if bids and asks:
                        total_bid_vol = sum(float(b[1]) for b in bids)
                        total_ask_vol = sum(float(a[1]) for a in asks)
                        total_vol = total_bid_vol + total_ask_vol
                        bid_pct = (total_bid_vol / total_vol * 100.0) if total_vol > 0 else 50.0
                        ask_pct = 100.0 - bid_pct

                        # Find largest support & resistance walls
                        best_bid_wall = max(bids, key=lambda x: float(x[1]))
                        best_ask_wall = max(asks, key=lambda x: float(x[1]))

                        return {
                            "symbol": disp,
                            "bid_pct": bid_pct,
                            "ask_pct": ask_pct,
                            "total_bid_vol": total_bid_vol,
                            "total_ask_vol": total_ask_vol,
                            "bid_wall_price": float(best_bid_wall[0]),
                            "bid_wall_qty": float(best_bid_wall[1]),
                            "ask_wall_price": float(best_ask_wall[0]),
                            "ask_wall_qty": float(best_ask_wall[1]),
                            "imbalance": "BULLISH_DOMINANT" if bid_pct >= 58 else ("BEARISH_DOMINANT" if ask_pct >= 58 else "BALANCED"),
                        }
        except Exception as e:
            logger.debug(f"Order book depth error for {symbol}: {e}")

        # Fallback to Contract Depth
        try:
            async with session.get(f"{MEXC_CONTRACT_API_BASE}/depth/{contract_sym}") as resp:
                if resp.status == 200:
                    res = await resp.json()
                    cdata = res.get("data", {})
                    bids = cdata.get("bids", [])
                    asks = cdata.get("asks", [])
                    if bids and asks:
                        total_bid_vol = sum(float(b[1]) for b in bids[:limit])
                        total_ask_vol = sum(float(a[1]) for a in asks[:limit])
                        total_vol = total_bid_vol + total_ask_vol
                        bid_pct = (total_bid_vol / total_vol * 100.0) if total_vol > 0 else 50.0
                        ask_pct = 100.0 - bid_pct

                        best_bid_wall = max(bids[:limit], key=lambda x: float(x[1]))
                        best_ask_wall = max(asks[:limit], key=lambda x: float(x[1]))

                        return {
                            "symbol": disp,
                            "bid_pct": bid_pct,
                            "ask_pct": ask_pct,
                            "total_bid_vol": total_bid_vol,
                            "total_ask_vol": total_ask_vol,
                            "bid_wall_price": float(best_bid_wall[0]),
                            "bid_wall_qty": float(best_bid_wall[1]),
                            "ask_wall_price": float(best_ask_wall[0]),
                            "ask_wall_qty": float(best_ask_wall[1]),
                            "imbalance": "BULLISH_DOMINANT" if bid_pct >= 58 else ("BEARISH_DOMINANT" if ask_pct >= 58 else "BALANCED"),
                        }
        except Exception as e:
            logger.debug(f"Contract depth error for {symbol}: {e}")

        return None

    async def get_market_trades_flow(self, symbol: str, limit: int = 60) -> Optional[Dict[str, Any]]:
        """
        Fetches real-time executed market trades and calculates Taker Buy/Sell Order Flow Delta.
        """
        disp, spot_sym, contract_sym = resolve_symbols(symbol)
        session = await self._get_session()

        # Try Spot Trades
        try:
            async with session.get(f"{MEXC_SPOT_API_BASE}/trades", params={"symbol": spot_sym, "limit": limit}) as resp:
                if resp.status == 200:
                    trades = await resp.json()
                    if isinstance(trades, list) and len(trades) > 0:
                        buy_vol = sum(float(t["qty"]) for t in trades if not t.get("isBuyerMaker", True))
                        sell_vol = sum(float(t["qty"]) for t in trades if t.get("isBuyerMaker", True))
                        total_vol = buy_vol + sell_vol
                        taker_buy_pct = (buy_vol / total_vol * 100.0) if total_vol > 0 else 50.0
                        taker_sell_pct = 100.0 - taker_buy_pct

                        # Approximate trade values in USD
                        recent_prices = [float(t["price"]) for t in trades]
                        avg_price = sum(recent_prices) / len(recent_prices) if recent_prices else 0.0
                        buy_dollars = buy_vol * avg_price
                        sell_dollars = sell_vol * avg_price
                        net_delta_usd = buy_dollars - sell_dollars

                        # Whale trade detection (> $10k per trade)
                        whale_trades = [t for t in trades if float(t["qty"]) * float(t["price"]) >= 10000]

                        return {
                            "symbol": disp,
                            "taker_buy_pct": taker_buy_pct,
                            "taker_sell_pct": taker_sell_pct,
                            "buy_volume_usd": buy_dollars,
                            "sell_volume_usd": sell_dollars,
                            "net_delta_usd": net_delta_usd,
                            "total_trades_analyzed": len(trades),
                            "whale_trades_count": len(whale_trades),
                            "flow_pressure": "STRONG_BUY_FLOW" if taker_buy_pct >= 60 else ("STRONG_SELL_FLOW" if taker_sell_pct >= 60 else "NEUTRAL_FLOW"),
                        }
        except Exception as e:
            logger.debug(f"Spot trades error for {symbol}: {e}")

        # Fallback to Contract Deals
        try:
            async with session.get(f"{MEXC_CONTRACT_API_BASE}/deals/{contract_sym}") as resp:
                if resp.status == 200:
                    res = await resp.json()
                    deals = res.get("data", [])
                    if isinstance(deals, list) and len(deals) > 0:
                        deals = deals[:limit]
                        # 1 = buy, 2 = sell in contract deals
                        buy_vol = sum(float(d.get("vol", 0)) for d in deals if d.get("T") == 1 or d.get("side") == 1)
                        sell_vol = sum(float(d.get("vol", 0)) for d in deals if d.get("T") == 2 or d.get("side") == 2)
                        total_vol = buy_vol + sell_vol
                        taker_buy_pct = (buy_vol / total_vol * 100.0) if total_vol > 0 else 50.0
                        taker_sell_pct = 100.0 - taker_buy_pct

                        avg_p = float(deals[0].get("p", 0)) if deals else 0.0
                        buy_dollars = buy_vol * avg_p
                        sell_dollars = sell_vol * avg_p
                        net_delta_usd = buy_dollars - sell_dollars

                        return {
                            "symbol": disp,
                            "taker_buy_pct": taker_buy_pct,
                            "taker_sell_pct": taker_sell_pct,
                            "buy_volume_usd": buy_dollars,
                            "sell_volume_usd": sell_dollars,
                            "net_delta_usd": net_delta_usd,
                            "total_trades_analyzed": len(deals),
                            "whale_trades_count": sum(1 for d in deals if float(d.get("vol", 0)) * avg_p >= 10000),
                            "flow_pressure": "STRONG_BUY_FLOW" if taker_buy_pct >= 60 else ("STRONG_SELL_FLOW" if taker_sell_pct >= 60 else "NEUTRAL_FLOW"),
                        }
        except Exception as e:
            logger.debug(f"Contract deals error for {symbol}: {e}")

        return None

    async def get_realtime_price(self, symbol: str) -> Optional[float]:
        """
        Fetches the ultra-low latency real-time live price directly from MEXC
        (Futures contract lastPrice or Spot real-time trade ticker).
        """
        disp, spot_sym, contract_sym = resolve_symbols(symbol)
        session = await self._get_session()
        is_commodity = any(k in symbol.upper() for k in ["GOLD", "XAU", "SILVER", "XAG"])

        # 1. Prioritize Futures Contract ticker for Commodities & Futures
        try:
            async with session.get(f"{MEXC_CONTRACT_API_BASE}/ticker", params={"symbol": contract_sym}) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    cdata = res.get("data", {})
                    if cdata and "lastPrice" in cdata:
                        p = float(cdata["lastPrice"])
                        if p > 0:
                            return p
        except Exception:
            pass

        # 2. Try Spot real-time ticker/price (instant sub-second trade execution price)
        if not is_commodity:
            try:
                async with session.get(f"{MEXC_SPOT_API_BASE}/ticker/price", params={"symbol": spot_sym}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and "price" in data:
                            p = float(data["price"])
                            if p > 0:
                                return p
            except Exception:
                pass

        # 3. Fallback to latest 1m kline close
        df = await self.get_klines(symbol, interval="1m", limit=2)
        if df is not None and len(df) > 0:
            return float(df["close"].iloc[-1])

        return None

    async def get_24hr_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetches 24-hour ticker statistics from Spot or Futures."""
        session = await self._get_session()
        disp, spot_sym, contract_sym = resolve_symbols(symbol)
        is_commodity = any(k in symbol.upper() for k in ["GOLD", "XAU", "SILVER", "XAG"])

        # 1. Prioritize Contract Ticker for Commodities
        if is_commodity:
            try:
                async with session.get(f"{MEXC_CONTRACT_API_BASE}/ticker", params={"symbol": contract_sym}) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        cdata = res.get("data", {})
                        if cdata and "lastPrice" in cdata:
                            return {
                                "symbol": cdata.get("symbol"),
                                "priceChangePercent": float(cdata.get("riseFallRate", 0)) * 100,
                                "highPrice": float(cdata.get("high24Price", 0)),
                                "lowPrice": float(cdata.get("lower24Price", 0)),
                                "volume": float(cdata.get("volume24", 0)),
                                "lastPrice": float(cdata.get("lastPrice", 0)),
                            }
            except Exception:
                pass

        # 2. Try Spot Ticker
        try:
            async with session.get(f"{MEXC_SPOT_API_BASE}/ticker/24hr", params={"symbol": spot_sym}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and "lastPrice" in data:
                        return data
        except Exception:
            pass

        # 3. Fallback to Contract Ticker for crypto
        try:
            async with session.get(f"{MEXC_CONTRACT_API_BASE}/ticker", params={"symbol": contract_sym}) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    cdata = res.get("data", {})
                    if cdata and "lastPrice" in cdata:
                        return {
                            "symbol": cdata.get("symbol"),
                            "priceChangePercent": float(cdata.get("riseFallRate", 0)) * 100,
                            "highPrice": float(cdata.get("high24Price", 0)),
                            "lowPrice": float(cdata.get("lower24Price", 0)),
                            "volume": float(cdata.get("volume24", 0)),
                            "lastPrice": float(cdata.get("lastPrice", 0)),
                        }
        except Exception:
            pass

        return None

    async def get_top_volume_pairs(self, quote: str = "USDT", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetches top active trading pairs sorted by 24h quote volume."""
        url = f"{MEXC_SPOT_API_BASE}/ticker/24hr"
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []

                filtered = [
                    item for item in data
                    if item.get("symbol", "").endswith(quote) and float(item.get("quoteVolume", 0)) > 0
                ]
                return sorted(
                    filtered,
                    key=lambda x: float(x.get("quoteVolume", 0)),
                    reverse=True,
                )[:limit]
        except Exception as e:
            logger.error(f"Exception fetching top volume pairs: {e}")
            return []
