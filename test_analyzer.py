import asyncio
import os
import sys

# Ensure UTF-8 output encoding for Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from mexc_client import MEXCClient
from analyzer import CryptoAnalyzer
from chart_generator import ChartGenerator


async def test_pipeline():
    print("=" * 60)
    print("TESTING MEXC LIVE DATA & SIGNAL PIPELINE")
    print("=" * 60)

    client = MEXCClient()
    symbols_to_test = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    for symbol in symbols_to_test:
        print(f"\n[1] Fetching live 5m candlesticks from MEXC for {symbol}...")
        df = await client.get_klines(symbol, interval="5m", limit=120)
        
        if df is None or len(df) < 35:
            print(f"[X] Failed to fetch data for {symbol}")
            continue

        print(f"[+] Received {len(df)} candles. Latest Close: ${df['close'].iloc[-1]:,.2f}")

        print(f"[2] Running technical analysis engine...")
        ticker_24h = await client.get_24hr_ticker(symbol)
        result = CryptoAnalyzer.analyze(df, symbol, "5m", ticker_24h)

        if not result:
            print(f"[X] Analysis failed for {symbol}")
            continue

        print(f"   * Overall Signal: {result.overall_signal} ({result.signal_strength_pct}%)")
        print(f"   * Market Condition: {result.market_condition}")
        print(f"   * Sentiment: {result.sentiment}")
        print(f"   * Volatility: {result.volatility_status} ({result.volatility_atr_pct:.2f}% ATR)")
        print(f"   * RSI (14): {result.rsi_value} ({result.rsi_status})")
        print(f"   * MACD: {result.macd_status}")
        print(f"   * Moving Average: {result.ma_trend_status}")
        print(f"   * Pivot Point: ${result.pivot_point:,.2f} | R1: ${result.resistance_1:,.2f} | S1: ${result.support_1:,.2f}")
        print(f"   * Suggested Entry: ${result.suggested_entry:,.2f}")
        print(f"   * Stop Loss (SL): ${result.stop_loss:,.2f}")
        print(f"   * Take Profit (TP1): ${result.take_profit_1:,.2f} | (TP2): ${result.take_profit_2:,.2f}")

        # Test Banner generation
        print(f"[3] Generating Signal Banner image...")
        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=symbol,
            interval="5m",
            signal=result.overall_signal,
            confidence_pct=result.signal_strength_pct,
        )
        os.makedirs("test_output", exist_ok=True)
        banner_path = f"test_output/banner_{symbol.replace('/', '_')}.png"
        with open(banner_path, "wb") as f:
            f.write(banner_buf.read())
        print(f"[+] Saved signal banner to {banner_path}")

        # Test Detailed Chart generation
        print(f"[4] Generating Detailed Multi-Panel Chart...")
        chart_buf = ChartGenerator.generate_detailed_chart(df, symbol, "5m")
        chart_path = f"test_output/chart_{symbol.replace('/', '_')}.png"
        with open(chart_path, "wb") as f:
            f.write(chart_buf.read())
        print(f"[+] Saved detailed chart to {chart_path}")

    await client.close()
    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_pipeline())
