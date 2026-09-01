# 🚀 MEXC Crypto Analysis & Signal Discord Bot

An institutional-grade Crypto Analysis & Signal Bot powered by real-time market data directly from **MEXC** (Spot & Futures). It performs multi-factor technical analysis, detects divergences & crossovers, scores signal confidence (0-100%), and generates visual candlestick banners matching professional crypto signal channels.

---

## 📸 Key Features

- **⚡ Real-Time MEXC Data**: Zero-delay OHLCV candlestick data and 24h ticker metrics directly from MEXC Open API (no API keys required).
- **🧠 Multi-Indicator Technical Engine**:
  - **RSI (14)** + Bullish & Bearish Divergence Detection
  - **MACD (12, 26, 9)** Crossovers & Histogram Momentum
  - **Moving Averages**: Fast EMA (20), Medium EMA (50), Slow EMA (200)
  - **Volatility & ATR**: Volatility categorization (`Elevated`, `Moderate`, `Low`)
  - **Bollinger Bands**: Upper/Lower band rejection and expansion
  - **Volume Pressure**: 24h volume percentile and buy/sell flow analysis
  - **Pivot Points**: Support levels (S1, S2) and Resistance levels (R1, R2)
- **🎯 Precision Risk Management**: Automatically computes Suggested Entry, Stop Loss (SL), and Take Profit (TP1 & TP2) with realistic risk-to-reward ratios.
- **🎨 Custom Visual Chart Generator**:
  - Dark neon signal header banner (matching the reference design with glowing candlestick backdrop).
  - Detailed 3-panel technical chart with Candlesticks, EMAs, Volume, and RSI subplots.
- **🚨 Automated Market Scanner**: Continuously monitors watchlisted pairs and automatically posts high-confidence setups (`>75%` confidence) to your alert channel.

---

## 🛠️ Quickstart Installation

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 2. Configure Your Bot
Copy the `.env.example` file to `.env`:
```powershell
cp .env.example .env
```
Open `.env` and fill in your Discord Bot Token:
```ini
DISCORD_BOT_TOKEN=your_discord_token_here
ALERT_CHANNEL_ID=optional_channel_id_here
```
*(See [DISCORD_SETUP.md](DISCORD_SETUP.md) for full guide on creating your Discord Bot)*

### 3. Test Locally (No Discord token needed for testing)
Verify that data fetching, indicator math, and chart generation work:
```powershell
python test_analyzer.py
```

### 4. Run the Bot
```powershell
python bot.py
```

---

## ⚡ Slash Commands in Discord

| Command | Description | Example |
| :--- | :--- | :--- |
| `/analyze <symbol> [timeframe]` | Deep-dive signal card with live chart & technical breakdown | `/analyze symbol:BTC/USDT timeframe:5m` |
| `/chart <symbol> [timeframe]` | Full 3-panel technical chart with RSI & EMA overlays | `/chart symbol:SOL/USDT timeframe:15m` |
| `/scan` | Scans all monitored pairs and outputs live market opportunities | `/scan` |
| `/watchlist list` | Displays currently monitored coins | `/watchlist action:List Watchlist` |
| `/watchlist add <symbol>` | Adds a new coin to automated monitoring | `/watchlist action:Add Coin symbol:PEPE/USDT` |
| `/watchlist remove <symbol>` | Removes a coin from watchlist | `/watchlist action:Remove Coin symbol:PEPE/USDT` |
| `/help` | Shows detailed bot command guide | `/help` |

---

## ⏱️ Supported Timeframes

- `1m` - Scalping (Ultra short-term)
- `5m` - Day Trading (Recommended default)
- `15m` - Intraday
- `1h` - Swing Trading
- `4h` - Trend Trading
- `1d` - Macro / Position Trading

---

## 📄 License & Disclaimer
*This bot is for educational and analytical purposes only. Cryptocurrency trading involves substantial risk of loss. Always exercise proper risk management.*
