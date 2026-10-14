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

### Event Futures (10-minute expiry)

Use `/event symbol:BTC/USDT payout:80` or `!event BTC 80` for an **UP / DOWN / WAIT**
research signal. Enter the **net-profit payout percentage currently shown in MEXC**;
80 means a win earns 0.80 USDT per 1 USDT staked, in addition to the returned stake.
If Down has a different payout, use `/event symbol:BTC/USDT payout:80 down_payout:75`
or `!event BTC 80 75`. One payout applies to both directions. Omitting payout produces
analysis with a WAIT verdict. Aliases: `!updown`, `!eventfutures`.

This feature uses MEXC **index** candles and a fresh index quote, never spot or perpetual
last-trade prices. It screens EMA 9/21/50 alignment, RSI, momentum and overextension,
then checks completed, non-overlapping 10-minute historical analogues. Entry requires
at least 30 non-tied observations and a 95% Wilson lower bound above the payout's
break-even win rate plus two percentage points. At 80% payout, break-even is 55.56%
among non-tied outcomes. Stale, incomplete or conflicting evidence returns WAIT.

Historical rates and returns are exploratory, based on up to 2000 minutes of index
history. They are **not calibrated probabilities or an independently validated edge**;
candle-close entries differ from your actual entry second, observations can remain
correlated, and current payouts are applied retrospectively. The command is a snapshot,
not a promise about the next 10 minutes. Re-run before entry and verify that the pair
is offered in Event Futures. It does not execute or automatically settle trades.

Under [MEXC's Event Futures rules](https://www.mexc.co/support/article/how-to-use-event-futures-on-mexc-a-simple-and-accessible-futures-trading-method-395277582692768768),
the index at expiry determines the outcome; a tie refunds the stake, a wrong direction
loses it, and positions cannot be closed early. MEXC currently does not offer Event
Futures API trading. Payouts can change before you submit an order.

Run the offline feature checks with `python -m unittest test_event_futures -v`.

### Automatic forward paper-tracking

Each qualifying UP/DOWN response from `/event` or `!event` automatically records a
**1 USDT virtual stake** at the displayed index price, using the entered payout and a
10-minute expiry measured from recording time. No real order is placed. WAIT signals
are not entered. Recording requires an index quote no more than five seconds old.
There is one pending paper entry per user/pair/server; repeated commands do not add
overlapping entries. This tracks command-triggered signals, not a continuous scanner.

Use `!eventstats` (alias `!eventreport`) or `/eventstats` for your results in the current
server. Filter with `!eventstats BTC` or `/eventstats symbol:BTC/USDT`. The report shows
pending/settled/missed entries, wins/losses/ties, win rate excluding ties, a Wilson lower
bound, payout-adjusted net return, ROI on settled stakes, maximum drawdown, longest
losing streak, separate Up/Down results, and the latest five entries. All-tie or empty
results show no win rate, rather than an invented accuracy figure. Strategy versions
are kept separate so later rule changes can be evaluated without blending their results.

The bot polls due entries every two seconds. Settlement uses a fresh MEXC index quote
timestamped from expiry through five seconds after expiry. This is an **approximate
paper settlement**, not MEXC's exact-second settlement; the report displays quote delay.
If a suitable quote cannot be captured by 15 seconds after expiry (including downtime),
the entry becomes MISSED and is excluded from win rate and returns. Missing results
can bias the remaining sample; review the missed count alongside performance.

Records persist in `event_tracking.db`, separate from the existing leveraged paper
accounts. Account resets do not erase this research record. Back up the database and
use persistent disk storage on cloud hosts; ephemeral deployments can lose it.
Restarts resume pending checks and mark expired entries with missing quotes as MISSED.
These are forward observations, but shared/correlated signals and user-selected payouts
mean the report alone does not establish a calibrated win probability or a profitable edge.

Run all Event Futures checks:
`python -m unittest test_event_futures test_event_tracking -v`.

### Check a direction you already have in mind

Use **`!eventcheck BTC UP 80`** or **`!eventcheck BTC DOWN 80`**. The slash equivalent
is `/eventcheck symbol:BTC/USDT direction:UP payout:80`. The payout is for your chosen
side only; the bot never assumes the opposite side has the same payout.

The check combines the existing 10-minute index-chart/history screen (EMA alignment,
RSI, momentum, overextension and payout threshold) with an experimental futures
liquidity/volume filter. UP requires at least 58% bid liquidity and 60% taker-buy
volume; DOWN requires at least 58% ask liquidity and 60% taker-sell volume. A fresh
book covers the nearest 20 levels per side; executed-volume shares cover up to 100
non-self trades from the last 60 seconds, requiring at least 20 trades. Book and latest
eligible trade timestamps must be within 10 seconds, allowing up to two seconds of
exchange clock skew. Missing, stale, balanced or
conflicting evidence returns WAIT. Volume cannot turn a chart WAIT into an entry.

The response explains whether **your requested direction** is supported and shows
the chart bias and both pressure readings. WAIT does not mean the opposite direction
is approved. These are perpetual-futures context indicators, not Event Futures order
flow. Historical analogue rates describe the chart screen only; they do not validate
the extra live-pressure filter. No improved win rate or guarantee is claimed.

Qualifying checks are automatically paper-tracked as strategy `event-check-v1`.
Use `!eventcheckstats` or `/eventcheckstats` (optional symbol filter) for their forward
results, separately from `!eventstats` for the original chart strategy. The existing
one-pending-entry-per-pair rule applies across both strategies. Pressure readings and
the requested side are stored with each check's entry snapshot.

Run all feature checks:
`python -m unittest test_event_futures test_event_tracking test_event_direction -v`.

### Toggle automatic bias messages

Enable alerts in the channel where you want to receive them:

```text
!eventalerts on BTC all
!eventalerts on BTC 10m 80
!eventalerts on ETH 30m 80 75
!eventalerts on BTC 1h 80
!eventalerts on BTC 1d 80
!eventalerts status
!eventalerts off BTC 10m
!eventalerts off
```

The slash equivalent is `/eventalerts action:on symbol:BTC horizon:all`. Horizons are
`10m`, `30m`, `1h`, `1d`, or `all`. Omitted symbol defaults to BTC when enabling;
`off` with no symbol disables **all your alerts in this server**. Alerts belong to the
user who enabled them. Enabling an existing pair/horizon updates it and moves it to
the current channel. Settings persist in `event_alerts.db`; preserve this file on cloud
hosts. At most 16 pair/horizon subscriptions can be active per user per server.

Each watch sends an initial reading, then changes in bias, entry approval or data
availability. Scans run approximately once per minute. Unchanged readings stay quiet;
ordinary changes have a five-minute notification cooldown. Withdrawals of an earlier
UP/DOWN approval send immediately on the next scan, including data loss. Failed sends
are retried, and disabling a watch stops future notifications. This is a background
bot feature: the bot must stay running and have permission to send in the channel.

The 10-minute horizon uses 1-minute index candles, 30 minutes and 1 hour use 5-minute
candles, and 1 day uses hourly candles. Historical labels are evaluated at the selected
expiry with non-overlapping windows. A daily reading is not a 10-minute signal relabelled.
All horizons require sufficient completed samples and fresh index data. The automatic
watch uses the chart/history screen; it does not apply the experimental short-lived
volume filter from `eventcheck`. Alerts do not open paper trades or real orders.

Without payout arguments, alerts show **bias only**, with WAIT for entry approval.
With payouts, UP/DOWN means the chart/history screen passes at your **configured
assumed payout**, not a live quote from Event Futures. One payout applies to both
sides unless a different DOWN payout is supplied. Payout assumptions expire after
one hour; alerts then continue in bias-only mode. Refresh using the `on` command when
MEXC payouts change, and configure different horizons separately if payouts differ.
Verify that the pair and expiry are offered in your MEXC account. No accuracy improvement
or guaranteed outcome is implied by an automated alert.

Run the complete offline suite:
`python -m unittest test_event_futures test_event_tracking test_event_direction test_event_alerts -v`.

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
