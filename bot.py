import asyncio
import io
import logging
import os
import time
from typing import Optional, List, Dict, Any
import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from mexc_client import MEXCClient, format_display_symbol, normalize_symbol, resolve_symbols
from analyzer import CryptoAnalyzer, TechnicalAnalysisResult, ScalpRecommendation, PreOrderSetup, SwingSetup, MultiHorizonForecast, HorizonStatus, SafeEntrySetup
from chart_generator import ChartGenerator
from paper_trading import PaperTradingManager, Position, AccountSummary, TrackedTrade

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("MEXC_DiscordBot")

# Discord Client
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=["!", "/"], intents=intents, help_command=None)

mexc_client = MEXCClient()
paper_trader = PaperTradingManager()

active_watchlist: List[str] = [
    "GOLD",
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "PEPE/USDT",
    "SUI/USDT",
    "BNB/USDT",
    "NEAR/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "ADA/USDT",
]

last_alert_times: Dict[str, float] = {}
ALERT_COOLDOWN_SECONDS = 15 * 60

current_alert_channel_id: Optional[int] = (
    int(config.ALERT_CHANNEL_ID) if config.ALERT_CHANNEL_ID and config.ALERT_CHANNEL_ID.isdigit() else None
)


def get_embed_color(signal: str) -> int:
    sig = signal.upper()
    if "STRONG BUY" in sig or "LONG" in sig:
        return config.COLOR_STRONG_BUY
    if "BUY" in sig:
        return config.COLOR_BUY
    if "STRONG SELL" in sig or "SHORT" in sig:
        return config.COLOR_STRONG_SELL
    if "SELL" in sig:
        return config.COLOR_SELL
    return config.COLOR_NEUTRAL


async def fetch_current_price(symbol: str) -> Optional[float]:
    price = await mexc_client.get_realtime_price(symbol)
    if price is not None and price > 0:
        return price
    ticker = await mexc_client.get_24hr_ticker(symbol)
    if ticker and "lastPrice" in ticker:
        return float(ticker["lastPrice"])
    return None


def generate_winrate_bar(win_rate: float) -> str:
    green_blocks = int(round(win_rate / 10))
    red_blocks = 10 - green_blocks
    return "🟩" * green_blocks + "🟥" * red_blocks + f" **{win_rate:.1f}%**"


def generate_flow_bar(buy_pct: float) -> str:
    green_blocks = int(round(buy_pct / 10))
    red_blocks = 10 - green_blocks
    return "🟩" * green_blocks + "🟥" * red_blocks + f" **{buy_pct:.1f}% Buys** vs **{100-buy_pct:.1f}% Sells**"


# ==================== CLOUD WEB HEALTH CHECK (FOR RENDER / KOYEB) ====================

async def handle_health_check(request):
    return web.Response(text="🟢 MEXC AI Trading Bot is LIVE & Healthy!", status=200)

async def start_web_health_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Cloud Web Health Server running on port {port}")


# ==================== 1-CLICK INTERACTIVE UI BUTTONS ====================

class QuickTradeView(discord.ui.View):
    def __init__(self, symbol: str, entry_price: float, sl: Optional[float] = None, tp: Optional[float] = None):
        super().__init__(timeout=600)
        self.symbol = symbol
        self.entry_price = entry_price
        self.sl = sl
        self.tp = tp

    @discord.ui.button(label="🟢 Quick Paper Long ($500 5x)", style=discord.ButtonStyle.success, emoji="📈")
    async def quick_long(self, interaction: discord.Interaction, button: discord.ui.Button):
        price = await fetch_current_price(self.symbol) or self.entry_price
        success, msg, pos = paper_trader.open_position(
            user_id=str(interaction.user.id),
            user_name=interaction.user.display_name,
            symbol=self.symbol,
            direction="LONG",
            amount_usd=500.0,
            entry_price=price,
            leverage=5,
            stop_loss=self.sl if self.sl and self.sl < price else None,
            take_profit=self.tp if self.tp and self.tp > price else None,
        )
        if success:
            await interaction.response.send_message(
                f"✅ <@{interaction.user.id}> **Paper LONG Opened!** Position #{pos.id} on `{self.symbol}` at `${price:,.2f}` (5x).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)

    @discord.ui.button(label="🔴 Quick Paper Short ($500 5x)", style=discord.ButtonStyle.danger, emoji="📉")
    async def quick_short(self, interaction: discord.Interaction, button: discord.ui.Button):
        price = await fetch_current_price(self.symbol) or self.entry_price
        success, msg, pos = paper_trader.open_position(
            user_id=str(interaction.user.id),
            user_name=interaction.user.display_name,
            symbol=self.symbol,
            direction="SHORT",
            amount_usd=500.0,
            entry_price=price,
            leverage=5,
            stop_loss=self.sl if self.sl and self.sl > price else None,
            take_profit=self.tp if self.tp and self.tp < price else None,
        )
        if success:
            await interaction.response.send_message(
                f"✅ <@{interaction.user.id}> **Paper SHORT Opened!** Position #{pos.id} on `{self.symbol}` at `${price:,.2f}` (5x).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)

    @discord.ui.button(label="💼 My Portfolio", style=discord.ButtonStyle.secondary, emoji="📊")
    async def view_portfolio(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name
        positions = paper_trader.get_open_positions(user_id)
        live_prices = {self.symbol: self.entry_price}
        summary = paper_trader.get_portfolio_summary(user_id, user_name, live_prices)
        embed = build_portfolio_embed(user_name, summary, positions, live_prices)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CopilotActionView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=3600)
        self.user_id = user_id

    @discord.ui.button(label="🛑 Disengage Copilot (Trade Closed)", style=discord.ButtonStyle.danger, emoji="🏁")
    async def stop_copilot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("❌ This is not your active copilot session.", ephemeral=True)
            return
        paper_trader.stop_trade_tracking(self.user_id)
        await interaction.response.send_message("🏁 AI Trade Copilot disengaged. Trade tracking stopped!", ephemeral=True)


# ==================== EMBED BUILDERS ====================

def build_orderflow_embed(symbol: str, depth: Optional[Dict[str, Any]], flow: Optional[Dict[str, Any]]) -> discord.Embed:
    buy_bias = flow["taker_buy_pct"] if flow else (depth["bid_pct"] if depth else 50.0)
    color = config.COLOR_STRONG_BUY if buy_bias >= 55 else (config.COLOR_STRONG_SELL if buy_bias <= 45 else config.COLOR_NEUTRAL)

    embed = discord.Embed(
        title=f"🌊 LIVE ORDER BOOK & MARKET TRADES FLOW: {symbol}",
        color=color,
        description="**Institutional Liquidity Depth • Aggressive Taker Volume • Whale Order Tracking**",
    )

    if flow:
        flow_bar = generate_flow_bar(flow["taker_buy_pct"])
        delta_str = f"+${flow['net_delta_usd']:,.2f}" if flow['net_delta_usd'] >= 0 else f"-${abs(flow['net_delta_usd']):,.2f}"
        embed.add_field(
            name="⚡ Real-Time Executed Market Trades (Taker Flow)",
            value=(
                f"{flow_bar}\n"
                f"• **Aggressive Buys:** `${flow['buy_volume_usd']:,.2f}`\n"
                f"• **Aggressive Sells:** `${flow['sell_volume_usd']:,.2f}`\n"
                f"• **Net Order Flow Delta:** **`{delta_str}`**\n"
                f"• **Whale Orders Detected:** `🐋 {flow['whale_trades_count']} large institutional fills`"
            ),
            inline=False,
        )

    if depth:
        depth_bar = generate_flow_bar(depth["bid_pct"])
        embed.add_field(
            name="🧱 Order Book Liquidity Depth & Walls (Bids vs Asks)",
            value=(
                f"{depth_bar}\n"
                f"• **Major Bid Wall (Support):** **`${depth['bid_wall_price']:,.2f}`** (`{depth['bid_wall_qty']:,.2f}` volume)\n"
                f"• **Major Ask Wall (Resistance):** **`${depth['ask_wall_price']:,.2f}`** (`{depth['ask_wall_qty']:,.2f}` volume)\n"
                f"• **Order Book Status:** `{depth['imbalance']}`"
            ),
            inline=False,
        )

    embed.set_footer(text="Data sourced live from MEXC L2 Order Book & Trades • Sourced in real-time")
    return embed


def build_forecast_embed(forecast: MultiHorizonForecast) -> discord.Embed:
    bias = forecast.overall_bias
    color = config.COLOR_STRONG_BUY if "BULL" in bias else (config.COLOR_STRONG_SELL if "BEAR" in bias else config.COLOR_NEUTRAL)

    embed = discord.Embed(
        title=f"🔮 MULTI-HORIZON TREND OUTLOOK: {forecast.symbol}",
        color=color,
        description=(
            f"**Current Price:** `${forecast.current_price:,.2f}`\n"
            f"**Overall Market Bias:** **`{forecast.overall_bias}`** (`{forecast.overall_confidence}%` Confidence)"
        ),
    )

    # 1. Short-Term (Minutes)
    st = forecast.short_term
    embed.add_field(
        name=f"{st.horizon_title} ➔ {st.trend_status}",
        value=(
            f"• **Trend Rationale:** _{st.rationale}_\n"
            f"• 🎯 **Projected Target:** **`${st.projected_target:,.2f}`** (`{st.target_gain_pct:+.2f}%`)\n"
            f"• 🛑 **Invalidation (SL):** `${st.invalidation_level:,.2f}`"
        ),
        inline=False,
    )

    # 2. Mid-Term (Days)
    mt = forecast.mid_term
    embed.add_field(
        name=f"{mt.horizon_title} ➔ {mt.trend_status}",
        value=(
            f"• **Trend Rationale:** _{mt.rationale}_\n"
            f"• 🎯 **Projected Target:** **`${mt.projected_target:,.2f}`** (`{mt.target_gain_pct:+.2f}%`)\n"
            f"• 🛑 **Invalidation (SL):** `${mt.invalidation_level:,.2f}`"
        ),
        inline=False,
    )

    # 3. Long-Term (Weeks/Months)
    lt = forecast.long_term
    embed.add_field(
        name=f"{lt.horizon_title} ➔ {lt.trend_status}",
        value=(
            f"• **Trend Rationale:** _{lt.rationale}_\n"
            f"• 🎯 **Macro Cycle Target:** **`${lt.projected_target:,.2f}`** (`{lt.target_gain_pct:+.2f}%`)\n"
            f"• 🛑 **Macro Floor (200 EMA):** `${lt.invalidation_level:,.2f}`"
        ),
        inline=False,
    )

    # 4. Actionable Verdict
    embed.add_field(
        name="💡 AI Entry Recommendation & Verdict",
        value=(
            f"### **{forecast.entry_verdict}**\n"
            f"• **Optimal Entry Zone:** `{forecast.entry_zone}`\n"
            f"{forecast.detailed_action_plan}"
        ),
        inline=False,
    )

    embed.set_footer(text="Multi-horizon alignment ensures you only enter when higher timeframes support the trade")
    return embed


def build_safe_entry_embed(setup: SafeEntrySetup) -> discord.Embed:
    is_long = "LONG" in setup.direction
    color = config.COLOR_STRONG_BUY if is_long else config.COLOR_STRONG_SELL

    # Determine real-time entry readiness
    is_inside_zone = setup.safe_entry_low <= setup.current_price <= setup.safe_entry_high
    if is_long:
        if is_inside_zone or abs(setup.current_price - setup.safe_entry_high) / setup.current_price < 0.0015:
            entry_status_badge = "🟢 **ENTRY VERDICT: ✅ GOOD TO ENTER NOW (In Prime Buy Zone!)**"
            entry_action_sub = "Price is currently resting directly on the institutional demand floor. Optimal risk-to-reward to open LONG!"
        elif setup.current_price > setup.safe_entry_high:
            entry_status_badge = "⏳ **ENTRY VERDICT: ⏳ WAIT FOR LIMIT FILL (Do NOT Market Buy)**"
            entry_action_sub = f"Price is `{setup.distance_to_entry_pct:.2f}%` above the safe zone. Place a **Limit Buy Order** inside `{setup.safe_entry_zone}` and wait for the pullback!"
        else:
            entry_status_badge = "⚠️ **ENTRY VERDICT: ⚠️ BELOW SAFE FLOOR (Wait for Bounce Wick)**"
            entry_action_sub = "Price wicked below the standard floor. Await a 5m green candle close before entering."
    else:  # SHORT
        if is_inside_zone or abs(setup.current_price - setup.safe_entry_low) / setup.current_price < 0.0015:
            entry_status_badge = "🔴 **ENTRY VERDICT: ✅ GOOD TO SHORT NOW (Retesting Supply Ceiling!)**"
            entry_action_sub = "Price is currently testing the institutional resistance ceiling. High-conviction SHORT entry!"
        elif setup.current_price < setup.safe_entry_low:
            entry_status_badge = "⏳ **ENTRY VERDICT: ⏳ WAIT FOR RETEST (Do NOT Chase Short)**"
            entry_action_sub = f"Price already dropped `{setup.distance_to_entry_pct:.2f}%` below ceiling. Place a **Limit Sell Order** inside `{setup.safe_entry_zone}` and wait for relief bounce!"
        else:
            entry_status_badge = "⚠️ **ENTRY VERDICT: ⚠️ ABOVE CEILING (Wait for Bearish Wick)**"
            entry_action_sub = "Price wicked above resistance. Await a 5m bearish rejection wick before entering."

    embed = discord.Embed(
        title=f"🛡️ SNIPER SAFE ENTRY & LEVERAGE ADVISOR: {setup.symbol}",
        color=color,
        description=(
            f"**Current Price:** `${setup.current_price:,.2f}` • **Confidence:** `{setup.confidence}%`\n"
            f"**Strategy Bias:** **`{setup.direction}`**"
        ),
    )

    # 0. High-Visibility Entry Readiness Traffic Light
    embed.add_field(
        name="🚦 Real-Time Entry Readiness Status",
        value=f"{entry_status_badge}\n• _{entry_action_sub}_",
        inline=False,
    )

    # 1. Sniper Safe Entry Zone
    embed.add_field(
        name="🎯 Institutional Safe Entry Floor (Limit Order Zone)",
        value=(
            f"### **`{setup.safe_entry_zone}`**\n"
            f"• **Distance from Market:** `{setup.distance_to_entry_pct:.2f}%` away\n"
            f"• **Why Safe:** _{setup.entry_rationale}_\n"
            f"• {setup.order_book_wall_note}"
        ),
        inline=False,
    )

    # 2. Risk Management & Targets
    embed.add_field(
        name="🛑 Risk Management & Take Profit Targets",
        value=(
            f"• **Technical Stop Loss (SL):** **`${setup.stop_loss:,.2f}`** (`{setup.sl_risk_pct:.2f}%` risk)\n"
            f"• **🎯 Target 1 (TP1):** **`${setup.take_profit_1:,.2f}`** (**`+{setup.tp1_gain_pct:.2f}%`**)\n"
            f"• **🚀 Target 2 (TP2):** **`${setup.take_profit_2:,.2f}`** (**`+{setup.tp2_gain_pct:.2f}%`**)\n"
            f"• **Risk/Reward Ratio:** `{setup.risk_reward}`"
        ),
        inline=False,
    )

    # 3. Leverage Safety Audit & Liquidation Guardrail
    lev_val = (
        f"• 🟢 **Recommended Safe Leverage:** **`{setup.recommended_safe_leverage}x`**\n"
        f"  ➔ _Est. Liq Price: `${setup.safe_liq_price:,.2f}` (**`{setup.safe_liq_buffer_pct:.1f}%`** safe buffer below market)_\n"
        f"• 🟡 **Moderate Leverage:** `{setup.moderate_leverage}x` (Liq Price: `${setup.moderate_liq_price:,.2f}`)\n"
        f"• 🔴 **DANGER ZONE ({setup.danger_leverage_threshold}x+):** **DO NOT USE**. Liquidation sits inside standard daily wick range!"
    )
    embed.add_field(name="⚡ Leverage Safety Audit (Prevent Liquidation)", value=lev_val, inline=False)

    # 4. Stop-Hunt Status
    embed.add_field(name="🦈 Whale Stop-Hunt & Liquidity Sweep Tracker", value=setup.liquidity_sweep_status, inline=False)

    embed.set_footer(text="Never market-chase green/red candles • Place post-only limit orders in the safe entry zone")
    return embed


def build_copilot_status_embed(trade: TrackedTrade, current_price: float, analysis: Optional[TechnicalAnalysisResult]) -> discord.Embed:
    is_long = trade.direction == "LONG"
    pnl_pct = ((current_price - trade.entry_price) / trade.entry_price * 100.0) if is_long else ((trade.entry_price - current_price) / trade.entry_price * 100.0)
    pnl_color = config.COLOR_STRONG_BUY if pnl_pct >= 0 else config.COLOR_STRONG_SELL
    dir_emoji = "🟢" if is_long else "🔴"

    # ATR / Volatility buffer calculation
    atr_val = (analysis.volatility_atr_pct / 100.0 * trade.entry_price) if (analysis and analysis.volatility_atr_pct > 0) else (trade.entry_price * 0.012)

    # 1. AI Recommended Dynamic Stop Loss (SL) Calculation
    if is_long:
        initial_ai_sl = max(analysis.support_1 if analysis else (trade.entry_price - 1.5 * atr_val), trade.entry_price - (1.6 * atr_val))
        if initial_ai_sl >= trade.entry_price:
            initial_ai_sl = trade.entry_price * 0.985

        if pnl_pct >= 0.6:
            ai_sl = trade.entry_price  # Move to Break-Even!
            sl_pct_diff = 0.0
            sl_badge = "🛡️ **MOVE TO BREAK-EVEN (0.00% Risk-Free!)**"
            sl_desc = f"• **Recommended SL:** **`${trade.entry_price:,.2f}`** (`0.00%` - Risk-Free Break-Even)\n• _Lock in your entry so this trade can never result in a loss!_"
        else:
            ai_sl = initial_ai_sl
            sl_pct_diff = ((ai_sl - trade.entry_price) / trade.entry_price) * 100.0
            sl_badge = f"`${ai_sl:,.2f}` (**`{sl_pct_diff:.2f}%`**)"
            sl_desc = f"• **Recommended SL:** **`${ai_sl:,.2f}`** (**`{sl_pct_diff:.2f}%`** from entry)\n• _Invalidation: Close trade if candle closes below this floor to prevent large drawdown._"

        # AI Recommended Dynamic Take Profit (TP) Targets
        ai_tp1 = trade.entry_price + (1.6 * atr_val)
        ai_tp2 = trade.entry_price + (3.0 * atr_val)
        tp1_pct = ((ai_tp1 - trade.entry_price) / trade.entry_price) * 100.0
        tp2_pct = ((ai_tp2 - trade.entry_price) / trade.entry_price) * 100.0
    else:  # SHORT
        initial_ai_sl = min(analysis.resistance_1 if analysis else (trade.entry_price + 1.5 * atr_val), trade.entry_price + (1.6 * atr_val))
        if initial_ai_sl <= trade.entry_price:
            initial_ai_sl = trade.entry_price * 1.015

        if pnl_pct >= 0.6:
            ai_sl = trade.entry_price  # Move to Break-Even!
            sl_pct_diff = 0.0
            sl_badge = "🛡️ **MOVE TO BREAK-EVEN (0.00% Risk-Free!)**"
            sl_desc = f"• **Recommended SL:** **`${trade.entry_price:,.2f}`** (`0.00%` - Risk-Free Break-Even)\n• _Lock in your entry so this trade can never result in a loss!_"
        else:
            ai_sl = initial_ai_sl
            sl_pct_diff = -(((ai_sl - trade.entry_price) / trade.entry_price) * 100.0)
            sl_badge = f"`${ai_sl:,.2f}` (**`{sl_pct_diff:.2f}%`**)"
            sl_desc = f"• **Recommended SL:** **`${ai_sl:,.2f}`** (**`{sl_pct_diff:.2f}%`** from entry)\n• _Invalidation: Close trade if candle closes above this ceiling to prevent large drawdown._"

        # AI Recommended Dynamic Take Profit (TP) Targets
        ai_tp1 = trade.entry_price - (1.6 * atr_val)
        ai_tp2 = trade.entry_price - (3.0 * atr_val)
        tp1_pct = ((trade.entry_price - ai_tp1) / trade.entry_price) * 100.0
        tp2_pct = ((trade.entry_price - ai_tp2) / trade.entry_price) * 100.0

    # Leverage, Margin & ROE calculations
    lev = trade.leverage if trade.leverage else 1
    margin = trade.margin_usd if trade.margin_usd else 0.0
    roe_pct = pnl_pct * lev
    pnl_usd = (roe_pct / 100.0) * margin if margin > 0 else 0.0

    # Liquidation Price calculation and proximity
    liq_price = trade.liquidation_price
    if (not liq_price or liq_price <= 0) and lev > 1:
        from paper_trading import calculate_liquidation_price
        liq_price = calculate_liquidation_price(trade.entry_price, trade.direction, lev)

    dist_liq_pct = 999.0
    if lev > 1 and liq_price and liq_price > 0:
        if is_long:
            dist_liq_pct = ((current_price - liq_price) / current_price) * 100.0
            dist_str = f"{dist_liq_pct:.2f}% buffer below"
        else:
            dist_liq_pct = ((liq_price - current_price) / current_price) * 100.0
            dist_str = f"{dist_liq_pct:.2f}% buffer above"

        if dist_liq_pct <= 2.5:
            liq_display = f"🚨 **CRITICAL:** **`${liq_price:,.2f}`** (`{dist_str}`) ☠️"
        elif dist_liq_pct <= 5.0:
            liq_display = f"⚠️ **CAUTION:** **`${liq_price:,.2f}`** (`{dist_str}`)"
        else:
            liq_display = f"🟢 **`${liq_price:,.2f}`** (`{dist_str}`)"
    else:
        liq_display = "`None (Spot / 1x No Liquidation)`"

    # AI Recommendation Assessment
    if lev > 1 and liq_price and liq_price > 0 and dist_liq_pct <= 2.5:
        advice_title = "🚨 DANGER: APPROACHING LIQUIDATION"
        advice_desc = f"Price (`${current_price:,.2f}`) is only **`{dist_liq_pct:.1f}%`** away from your Est. Liq Price (`${liq_price:,.2f}`). Exit immediately or cut loss to protect remaining capital!"
    elif pnl_pct >= 1.2 and analysis and (("Overbought" in analysis.rsi_status and is_long) or ("Oversold" in analysis.rsi_status and not is_long)):
        advice_title = "⚡ FLASH CLOSE / LOCK IN GAINS"
        advice_desc = f"You are up **`+{pnl_pct:.2f}%`** and RSI is showing exhaustion. Consider taking profits or securing 70% of the position."
    elif pnl_pct >= 0.6:
        advice_title = "🛡️ MOVE STOP LOSS TO BREAK-EVEN"
        advice_desc = f"You are up **`+{pnl_pct:.2f}%`**. Move your Stop Loss to your Entry Price (`${trade.entry_price:,.2f}`) for a **guaranteed risk-free trade**!"
    elif pnl_pct <= -1.0 and analysis and (("Bearish crossover" in analysis.macd_status and is_long) or ("Bullish crossover" in analysis.macd_status and not is_long)):
        advice_title = "⚠️ FLASH CUT / RISK WARNING"
        advice_desc = f"Momentum is accelerating against your position (`{pnl_pct:.2f}%`). If Stop Loss isn't reached, consider exiting early to preserve capital."
    else:
        advice_title = "🧭 HOLD & STAY PATIENT"
        advice_desc = f"Price is tracking normally (`{pnl_pct:+.2f}%`). Maintain trade structure and let the setup play out."

    lev_badge = f"({lev}x)" if lev > 1 else ""
    embed = discord.Embed(
        title=f"🤖 AI TRADE COPILOT: {trade.symbol} {dir_emoji} {trade.direction} {lev_badge}",
        color=pnl_color,
        description=f"**Real-Time Position Sentinel • Focused on `{trade.timeframe}` Timeframe**",
    )

    embed.add_field(name="📍 Your Entry Price", value=f"`${trade.entry_price:,.2f}`", inline=True)
    embed.add_field(name="💵 Live Current Price", value=f"`${current_price:,.2f}`", inline=True)

    # Unrealized Move + ROE + Dollar PnL
    pnl_extra = ""
    if margin > 0:
        pnl_extra = f" (**`{roe_pct:+.2f}% ROE`** | **`${pnl_usd:+,.2f}`**)"
    elif lev > 1:
        pnl_extra = f" (**`{roe_pct:+.2f}% ROE`**)"
    embed.add_field(name="📈 Unrealized PnL", value=f"**`{pnl_pct:+.2f}%`**{pnl_extra}", inline=True)

    # Position Sizing & Liquidation Risk Block
    if lev > 1 or margin > 0:
        margin_info = f"`${margin:,.2f}` (Size: `${margin * lev:,.2f}`)" if margin > 0 else "`Not Specified`"
        embed.add_field(
            name="⚡ Futures Margin & Liquidation Risk",
            value=(
                f"• **Leverage:** `{lev}x`\n"
                f"• **Margin Allocated:** {margin_info}\n"
                f"• **☠️ Est. Liq Price:** {liq_display}"
            ),
            inline=False,
        )

    # Dedicated AI Recommended SL field
    embed.add_field(
        name="🛑 AI Recommended Stop Loss (Risk Prevention)",
        value=sl_desc,
        inline=False,
    )

    # Dedicated AI Recommended TP field
    tp_desc = (
        f"• **🎯 Target 1 (TP1):** **`${ai_tp1:,.2f}`** (**`+{tp1_pct:.2f}%`** gain)\n"
        f"• **🚀 Target 2 (TP2):** **`${ai_tp2:,.2f}`** (**`+{tp2_pct:.2f}%`** gain)"
    )
    embed.add_field(
        name="🎯 AI Recommended Take Profit (Profit Maximization)",
        value=tp_desc,
        inline=False,
    )

    if analysis:
        rsi_str = f"`{analysis.rsi_value:.1f}` ({analysis.rsi_status})"
        macd_str = f"`{analysis.macd_status}`"
        trend_str = f"`{analysis.ma_trend_status}`"
        embed.add_field(name="📊 Live Momentum Health", value=f"• **RSI**: {rsi_str}\n• **MACD**: {macd_str}\n• **Trend**: {trend_str}", inline=False)

    embed.add_field(name=f"💡 AI Advice: {advice_title}", value=advice_desc, inline=False)

    # User's personal configured targets (if any)
    if trade.take_profit or trade.stop_loss:
        user_tp = f"`${trade.take_profit:,.2f}`" if trade.take_profit else "`None`"
        user_sl = f"`${trade.stop_loss:,.2f}`" if trade.stop_loss else "`None`"
        embed.add_field(name="⚙️ Your Configured Manual Targets", value=f"• **TP**: {user_tp} | • **SL**: {user_sl}", inline=False)

    embed.set_footer(text="AI Sentinel dynamically updates SL & TP • Use !stoptrack when you exit")
    return embed


def build_portfolio_embed(user_name: str, summary: AccountSummary, positions: List[Position], live_prices: Dict[str, float]) -> discord.Embed:
    pnl_color = config.COLOR_STRONG_BUY if summary.unrealized_pnl >= 0 else config.COLOR_STRONG_SELL
    embed = discord.Embed(
        title=f"🎮 {user_name}'s Paper Trading Portfolio",
        color=pnl_color,
        description="**Live MEXC Simulator • Real-Time PnL & Automated TP/SL**",
    )

    embed.add_field(name="💰 Total Equity", value=f"**`${summary.equity:,.2f}`**", inline=True)
    embed.add_field(name="💵 Available Cash", value=f"`${summary.cash_balance:,.2f}`", inline=True)
    embed.add_field(name="🔒 Margin In Use", value=f"`${summary.margin_used:,.2f}`", inline=True)

    embed.add_field(
        name="📈 Unrealized PnL",
        value=f"**`${summary.unrealized_pnl:+,.2f}`**",
        inline=True,
    )
    embed.add_field(
        name="🏆 Realized PnL",
        value=f"**`${summary.total_realized_pnl:+,.2f}`**",
        inline=True,
    )
    embed.add_field(
        name="🎯 Win Rate",
        value=f"**`{summary.win_rate_pct:.1f}%`** ({summary.winning_trades}W / {summary.losing_trades}L)",
        inline=True,
    )

    if positions:
        pos_lines = []
        for p in positions:
            curr_p = live_prices.get(p.symbol, p.entry_price)
            pnl_u, pnl_pct = paper_trader.calculate_position_pnl(p, curr_p)
            dir_emoji = "🟢" if p.direction == "LONG" else "🔴"
            tp_text = f"`${p.take_profit:,.2f}`" if p.take_profit else "`None`"
            sl_text = f"`${p.stop_loss:,.2f}`" if p.stop_loss else "`None`"
            pos_lines.append(
                f"{dir_emoji} **#{p.id} {p.symbol} {p.direction} ({p.leverage}x)**\n"
                f"• Entry: `${p.entry_price:,.2f}` ➔ Now: `${curr_p:,.2f}`\n"
                f"• PnL: **`${pnl_u:+,.2f}` ({pnl_pct:+.2f}%)** | Margin: `${p.amount_usd:,.2f}`\n"
                f"• **TP**: {tp_text} | **SL**: {sl_text}\n"
            )
        embed.add_field(name="📍 Active Open Trades", value="\n".join(pos_lines), inline=False)
    else:
        embed.add_field(name="📍 Active Open Trades", value="_No open positions. Use `/long` or `/short` to open a trade._", inline=False)

    embed.set_footer(text="Use /close <id> to exit | /settp or /setsl to adjust targets | /winrate for stats")
    return embed


def build_winrate_embed(user_name: str, summary: AccountSummary) -> discord.Embed:
    color = config.COLOR_STRONG_BUY if summary.total_realized_pnl >= 0 else config.COLOR_STRONG_SELL
    bar = generate_winrate_bar(summary.win_rate_pct)

    embed = discord.Embed(
        title=f"📊 {user_name}'s Trading Performance & Win Rate",
        color=color,
        description=f"**Win Rate Progress:**\n{bar}",
    )

    embed.add_field(name="🎯 Total Trades", value=f"`{summary.total_trades}`", inline=True)
    embed.add_field(name="✅ Winning Trades", value=f"`{summary.winning_trades}`", inline=True)
    embed.add_field(name="❌ Losing Trades", value=f"`{summary.losing_trades}`", inline=True)

    embed.add_field(name="🏆 Total Net Profit", value=f"**`${summary.total_realized_pnl:+,.2f}`**", inline=True)
    embed.add_field(name="🚀 Best Single Trade", value=f"`${summary.best_trade_pnl:+,.2f}`", inline=True)
    embed.add_field(name="🔻 Worst Single Trade", value=f"`${summary.worst_trade_pnl:+,.2f}`", inline=True)

    embed.set_footer(text="Trade data updated live with every closed position • /leaderboard for rankings")
    return embed


def build_swing_embed(swing: SwingSetup) -> discord.Embed:
    color = config.COLOR_STRONG_BUY if "LONG" in swing.direction else (config.COLOR_STRONG_SELL if "SHORT" in swing.direction else config.COLOR_NEUTRAL)
    emoji = "🚀" if "LONG" in swing.direction else ("🔻" if "SHORT" in swing.direction else "⏳")

    embed = discord.Embed(
        title=f"{emoji} MACRO SWING RADAR: {swing.symbol} ({swing.timeframe})",
        color=color,
        description=f"**Macro Trend Regime:** `{swing.trend_regime}`\n**Current Price:** `${swing.current_price:,.2f}` • **Confidence:** `{swing.confidence}%`",
    )

    embed.add_field(
        name="🧭 Swing Trade Strategy & Rationale",
        value=f"### **{swing.direction}**\n_{swing.catalyst_reason}_\n• **Anticipated Hold Duration:** `{swing.estimated_hold_duration}`",
        inline=False,
    )

    plan_val = (
        f"• **Optimal Swing Entry Zone:** `{swing.entry_zone}`\n"
        f"• **Macro Invalidation (SL):** **`${swing.stop_loss:,.2f}`**\n"
        f"• **🎯 Swing Target 1 (TP1):** **`${swing.target_1:,.2f}`** (`+{swing.target_1_gain_pct:.2f}%`)\n"
        f"• **🚀 Macro Swing Target 2 (TP2):** **`${swing.target_2:,.2f}`** (`+{swing.target_2_gain_pct:.2f}%`)\n"
        f"• **Risk/Reward Ratio:** `{swing.risk_reward}`"
    )
    embed.add_field(name="📋 Institutional Swing Execution Plan (1h / 4h)", value=plan_val, inline=False)
    embed.set_footer(text="Higher timeframe swings filter out intraday noise for maximum win rate • 1-Click test below")
    return embed


def build_multi_horizon_embed(swings: List[Dict[str, Any]], daytrades: List[Dict[str, Any]], scalps: List[Dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="🌐 FULL MARKET POTENTIAL RADAR (Entry Points & Targets)",
        color=0x3498DB,
        description="**Multi-Horizon Market Intelligence • Categorized by Hold Style with Optimal Entry Zones:**",
    )

    if swings:
        lines = []
        for s in swings[:3]:
            d_icon = "🟢" if "LONG" in s["direction"] else "🔴"
            lines.append(
                f"{d_icon} **{s['symbol']}** ➔ **{s['direction']}** (`{s['confidence']}%`)\n"
                f"• 📍 **Good Entry Zone:** `{s['entry_zone']}` (Now: `${s['price']:,.2f}`)\n"
                f"• 🎯 **Target 1:** `${s['target']:,.2f}` (**`+{s['gain']:.2f}%`**) | 🛑 **SL:** `${s['sl']:,.2f}`\n"
                f"• ⏱️ **Hold:** `2 - 5 Days`\n"
            )
        embed.add_field(name="🌊 BEST SWING TRADES (Hold 2 – 6 Days | 85%+ Win Rate)", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🌊 BEST SWING TRADES (Hold 2 – 6 Days)", value="_No high-confidence 4h setups currently._", inline=False)

    if daytrades:
        lines = []
        for d in daytrades[:3]:
            d_icon = "🟢" if "LONG" in d["direction"] else "🔴"
            lines.append(
                f"{d_icon} **{d['symbol']}** ➔ **{d['direction']}** (`{d['confidence']}%`)\n"
                f"• 📍 **Good Entry Zone:** `{d['entry_zone']}` (Now: `${d['price']:,.2f}`)\n"
                f"• 🎯 **Target 1:** `${d['target']:,.2f}` (**`+{d['gain']:.2f}%`**) | 🛑 **SL:** `${d['sl']:,.2f}`\n"
                f"• ⏱️ **Hold:** `Today (2 - 12 Hours)`\n"
            )
        embed.add_field(name="📈 BEST DAY TRADES (Hold 2 – 12 Hours | 80%+ Win Rate)", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📈 BEST DAY TRADES (Hold 2 – 12 Hours)", value="_No high-confidence 1h setups currently._", inline=False)

    if scalps:
        lines = []
        for sc in scalps[:3]:
            d_icon = "🟢" if "LONG" in sc["direction"] else "🔴"
            lines.append(
                f"{d_icon} **{sc['symbol']}** ➔ **{sc['direction']}** (`{sc['confidence']}%`)\n"
                f"• 📍 **Good Entry Zone:** `{sc['entry_zone']}` (Now: `${sc['price']:,.2f}`)\n"
                f"• 🎯 **Quick Target:** `${sc['target']:,.2f}` (**`+{sc['gain']:.2f}%`**) | 🛑 **SL:** `${sc['sl']:,.2f}`\n"
                f"• ⏱️ **Hold:** `5 - 30 Mins`\n"
            )
        embed.add_field(name="⚡ FAST 5M SCALPS (Hold 5 – 30 Mins)", value="\n".join(lines), inline=False)

    embed.set_footer(text="Place Limit Orders at the Good Entry Zone in advance • Use /swing <symbol> for deep chart")
    return embed


def build_scan_overview_embed(timeframe: str, results: List[Dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔍 MULTI-COIN MARKET POTENTIAL SCANNER ({timeframe.upper()})",
        color=0x3498DB,
        description=f"**Scanned `{len(results)}` assets across MEXC • Ranked by Setup Quality & Win Rate Potential:**\n",
    )

    prime_setups = [r for r in results if r["confidence"] >= 85]
    good_setups = [r for r in results if 75 <= r["confidence"] < 85]
    neutral_setups = [r for r in results if r["confidence"] < 75]

    if prime_setups:
        lines = []
        for p in prime_setups:
            dir_emoji = "🟢" if "LONG" in p["direction"] or "BUY" in p["direction"] else "🔴"
            lines.append(
                f"{dir_emoji} **{p['symbol']}** ➔ **{p['direction']}** (`{p['confidence']}%`)\n"
                f"• 📍 **Good Entry Zone:** `{p['entry_zone']}` (Now: `${p['price']:,.2f}`)\n"
                f"• 🎯 **Target 1:** `${p['target']:,.2f}` (**`+{p['target_gain']:.2f}%`**) | 🛑 **SL:** `${p['sl']:,.2f}`\n"
            )
        embed.add_field(name="⭐ A+ PRIME OPPORTUNITIES (85%+ Win Rate)", value="\n".join(lines), inline=False)

    if good_setups:
        lines = []
        for g in good_setups:
            dir_emoji = "🟢" if "LONG" in g["direction"] or "BUY" in g["direction"] else "🔴"
            lines.append(
                f"{dir_emoji} **{g['symbol']}** ➔ `{g['direction']}` ({g['confidence']}%)\n"
                f"• 📍 Entry: `{g['entry_zone']}` | 🎯 Target: `${g['target']:,.2f}` (`+{g['target_gain']:.1f}%`) | 🛑 SL: `${g['sl']:,.2f}`"
            )
        embed.add_field(name="🟢 A-TIER TREND CONTINUATIONS (75% - 84%)", value="\n".join(lines), inline=False)

    if neutral_setups:
        lines = []
        for n in neutral_setups:
            lines.append(f"⏳ **{n['symbol']}**: `Consolidation / Chop` (RSI: `{n['rsi']:.1f}`)")
        embed.add_field(name="🛑 RANGE-BOUND / SKIP (< 75%)", value="\n".join(lines), inline=False)

    embed.set_footer(text="Use /swing <symbol> 4h for individual deep breakdowns • Sourced live from MEXC")
    return embed


def build_preorder_embed(pre: PreOrderSetup, interval: str = "5m") -> discord.Embed:
    color = config.COLOR_STRONG_BUY if "LONG" in pre.direction else config.COLOR_STRONG_SELL
    embed = discord.Embed(
        title=f"🛡️ PRE-MEASURE LIMIT RADAR: {pre.symbol} ({interval})",
        color=color,
        description=(
            f"**Current Price:** `${pre.current_price:,.2f}`\n"
            f"**Distance to Limit Fill:** `{pre.distance_pct:.2f}%` (`${pre.distance_usd:,.2f}` away) • _{pre.estimated_fill_time}_"
        ),
    )

    embed.add_field(
        name="🎯 Institutional Limit Setup",
        value=(
            f"### **{pre.direction}**\n"
            f"• **Place Pending Limit Order At**: **`${pre.limit_entry_price:,.2f}`**\n"
            f"• **Optimal Entry Range**: `{pre.setup_zone}`\n"
            f"• **Strategy Rationale**: _{pre.setup_reason}_"
        ),
        inline=False,
    )

    embed.add_field(
        name="📋 Order Parameters (Copy & Paste to MEXC)",
        value=(
            f"• **Order Type:** `Limit Order` (Post-Only)\n"
            f"• **Limit Price:** `${pre.limit_entry_price:,.2f}`\n"
            f"• **Stop Loss (SL):** `${pre.tight_stop_loss:,.2f}`\n"
            f"• **Take Profit 1 (TP1):** `${pre.take_profit_1:,.2f}`\n"
            f"• **Take Profit 2 (TP2):** `${pre.take_profit_2:,.2f}`\n"
            f"• **Risk/Reward:** `{pre.risk_reward_ratio}`"
        ),
        inline=False,
    )

    embed.set_footer(text="Pre-Measures allow your order to fill at the deepest discount before the bounce.")
    return embed


def build_signal_embed(result: TechnicalAnalysisResult, flow: Optional[Dict[str, Any]] = None, depth: Optional[Dict[str, Any]] = None) -> discord.Embed:
    color = get_embed_color(result.overall_signal)
    signal_emoji = "🟢" if "BUY" in result.overall_signal else ("🔴" if "SELL" in result.overall_signal else "⚪")
    chart_emoji = "📈" if "BUY" in result.overall_signal else "📉"

    title = f"{result.symbol} | {result.interval} | {result.overall_signal} {signal_emoji}"

    embed = discord.Embed(
        title=title,
        color=color,
        description="**MEXC Real-Time Market Intelligence & Order Flow**",
    )

    market_info_val = (
        f"• **Volatility**: `{result.volatility_status}` ({result.volatility_atr_pct:.2f}% ATR)\n"
        f"• **Asset strength by volume**: `{result.asset_strength_volume}%`\n"
        f"• **Volume result**: `{result.volume_result}%`\n"
        f"• **Sentiment**: **{result.sentiment}**"
    )
    embed.add_field(name="🧠 Market info:", value=market_info_val, inline=False)

    price_fmt = f"{result.current_price:,.4f}" if result.current_price < 10 else f"{result.current_price:,.2f}"
    r1_fmt = f"{result.resistance_1:,.4f}" if result.resistance_1 < 10 else f"{result.resistance_1:,.2f}"
    s1_fmt = f"{result.support_1:,.4f}" if result.support_1 < 10 else f"{result.support_1:,.2f}"

    tech_val = (
        f"• **Current price**: `{price_fmt}`\n"
        f"• **Resistance (R1)**: `{r1_fmt}`\n"
        f"• **Support (S1)**: `{s1_fmt}`\n"
        f"• **RSI (14)**: `{result.rsi_status}` (`{result.rsi_value}`)\n"
        f"• **MACD**: `{result.macd_status}`\n"
        f"• **Moving Average**: `{result.ma_trend_status}`"
    )
    embed.add_field(name="📄 Technical overview:", value=tech_val, inline=False)

    if flow:
        delta_str = f"+${flow['net_delta_usd']:,.2f}" if flow['net_delta_usd'] >= 0 else f"-${abs(flow['net_delta_usd']):,.2f}"
        flow_text = f"• **Taker Flow**: `{flow['taker_buy_pct']:.1f}% Buys` vs `{flow['taker_sell_pct']:.1f}% Sells` (Delta: **`{delta_str}`**)\n• **Whale Orders**: `🐋 {flow['whale_trades_count']} large fills`"
        embed.add_field(name="🌊 Live Market Trades Flow:", value=flow_text, inline=False)

    if depth:
        depth_text = f"• **Order Book**: `{depth['bid_pct']:.1f}% Bids` vs `{depth['ask_pct']:.1f}% Asks`\n• **Liquidity Walls**: Support at `${depth['bid_wall_price']:,.2f}` | Resistance at `${depth['ask_wall_price']:,.2f}`"
        embed.add_field(name="🧱 Order Book Liquidity Depth:", value=depth_text, inline=False)

    signal_val = (
        f"• **Strength**: **{result.overall_signal} {result.signal_strength_pct}%**\n"
        f"• **Market conditions**: `{result.market_condition}`"
    )
    embed.add_field(name=f"{chart_emoji} Signal strength:", value=signal_val, inline=False)

    if result.pre_order:
        pre = result.pre_order
        pre_val = (
            f"• **Direction**: **{pre.direction}**\n"
            f"• **Pending Limit Price**: **`${pre.limit_entry_price:,.2f}`** (`{pre.distance_pct:.2f}%` away)\n"
            f"• **Stop Loss (SL)**: `${pre.tight_stop_loss:,.2f}` | **TP1**: `${pre.take_profit_1:,.2f}`\n"
            f"• **Risk/Reward**: `{pre.risk_reward_ratio}` ({pre.setup_reason})"
        )
        embed.add_field(name="🛡️ Pre-Measure Limit Setup (Place on MEXC):", value=pre_val, inline=False)

    embed.set_image(url="attachment://signal_banner.png")
    embed.set_footer(text="MEXC Spot & Futures Engine • Set your limit order on exchange or paper trade below")
    return embed


def build_scalp_embed(result: TechnicalAnalysisResult, is_auto_alert: bool = False, flow: Optional[Dict[str, Any]] = None) -> discord.Embed:
    sc = result.scalp
    color = get_embed_color(sc.action if sc else result.overall_signal)
    
    if sc and sc.is_mtf_confluence:
        prefix_title = "⭐ DUAL-TIMEFRAME CONFLUENCE ALERT (5m + 1m):" if is_auto_alert else "⭐ DUAL CONFLUENCE RADAR (5m + 1m):"
    else:
        prefix_title = "🚨 AUTOMATED SCALP ALERT:" if is_auto_alert else "⚡ FAST SCALP RADAR:"

    embed = discord.Embed(
        title=f"{prefix_title} {result.symbol}",
        color=color,
        description=f"**Current Price:** `${result.current_price:,.2f}` | **Timeframe Alignment:** `5m Macro + 1m Trigger`",
    )

    if sc:
        if sc.mtf_description:
            embed.add_field(name="📊 Confluence Status", value=sc.mtf_description, inline=False)

        embed.add_field(
            name="🧭 AI Recommendation & Rationale",
            value=f"### **{sc.action}** ({sc.confidence}% Confidence)\n_{sc.reasoning}_\n• **Optimal Trigger Zone:** `{sc.entry_zone}`\n• **Est. Hold Time:** `{sc.estimated_hold_time}`",
            inline=False,
        )

    if flow:
        delta_str = f"+${flow['net_delta_usd']:,.2f}" if flow['net_delta_usd'] >= 0 else f"-${abs(flow['net_delta_usd']):,.2f}"
        embed.add_field(
            name="🌊 Live Market Trades & Order Flow Pressure",
            value=f"• **Taker Flow**: `{flow['taker_buy_pct']:.1f}% Aggressive Buys` vs `{flow['taker_sell_pct']:.1f}% Sells`\n• **Net Delta**: **`{delta_str}`** | **Whale Orders**: `🐋 {flow['whale_trades_count']} large fills`",
            inline=False,
        )

    if result.pre_order:
        pre = result.pre_order
        mexc_real_plan = (
            f"👉 **Order Type:** `Limit Order (Post-Only)`\n"
            f"👉 **Pending Limit Price:** **`${pre.limit_entry_price:,.2f}`** (`{pre.distance_pct:.2f}%` away)\n"
            f"👉 **Stop Loss (SL):** **`${pre.tight_stop_loss:,.2f}`**\n"
            f"👉 **Take Profit 1 (TP1):** **`${pre.take_profit_1:,.2f}`**\n"
            f"👉 **Take Profit 2 (TP2):** **`${pre.take_profit_2:,.2f}`**\n"
            f"👉 **Risk/Reward:** `{pre.risk_reward_ratio}` • _{pre.estimated_fill_time}_"
        )
        embed.add_field(
            name="📋 MEXC REAL-TRADE EXECUTION PLAN (Place Limit Order Now):",
            value=mexc_real_plan,
            inline=False,
        )

    embed.set_image(url="attachment://signal_banner.png")
    embed.set_footer(text="Set your limit order on MEXC in advance to eliminate latency • Click below to Paper Trade")
    return embed


@bot.event
async def on_ready():
    logger.info(f"Bot logged in as {bot.user.name} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} application slash command(s).")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

    if not auto_mtf_scanner.is_running():
        auto_mtf_scanner.start()
        logger.info("Auto MTF Scalp Scanner started (Scanning 5m+1m confluence every 1 min).")

    if not auto_tp_sl_checker.is_running():
        auto_tp_sl_checker.start()
        logger.info("Auto TP/SL Monitor started (Monitoring open positions every 10s).")

    if not active_trade_copilot_loop.is_running():
        active_trade_copilot_loop.start()
        logger.info("Active Trade Copilot Sentinel started (Monitoring tracked positions every 15s).")


# ==================== ORDER FLOW & LIQUIDITY COMMANDS ====================

@bot.tree.command(name="orderflow", description="🌊 Live Order Flow & Depth: Checks real-time Order Book, Taker Volume & Whale Trades.")
@app_commands.describe(symbol="Trading pair symbol (e.g. BTC/USDT, GOLD, ETH/USDT, SOL/USDT)")
async def orderflow_command(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    disp = format_display_symbol(symbol)
    try:
        depth = await mexc_client.get_order_book_depth(symbol, limit=20)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=60)

        if not depth and not flow:
            await interaction.followup.send(f"❌ Could not fetch order flow data for `{disp}` from MEXC.", ephemeral=True)
            return

        embed = build_orderflow_embed(disp, depth, flow)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in /orderflow: {e}")
        await interaction.followup.send(f"❌ Error: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="forecast", description="🔮 Multi-Horizon Trend Outlook: Predicts trend & targets for Minutes, Days, Weeks, & Months.")
@app_commands.describe(symbol="Trading pair symbol (e.g. BTC/USDT, GOLD, ETH/USDT, SOL/USDT)")
async def forecast_command(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    disp = format_display_symbol(symbol)
    try:
        df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=80)
        df_4h = await mexc_client.get_klines(symbol, interval="4h", limit=80)
        df_1d = await mexc_client.get_klines(symbol, interval="1d", limit=80)

        if df_5m is None or len(df_5m) < 30:
            await interaction.followup.send(f"❌ Could not fetch market data for `{disp}` from MEXC.", ephemeral=True)
            return

        forecast = CryptoAnalyzer.generate_multi_horizon_forecast(df_5m, df_4h, df_1d, disp)
        if not forecast:
            await interaction.followup.send(f"❌ Failed to generate multi-horizon forecast for `{disp}`.", ephemeral=True)
            return

        embed = build_forecast_embed(forecast)
        view = QuickTradeView(disp, forecast.current_price, sl=forecast.macro_invalidation, tp=forecast.mid_term.projected_target)
        await interaction.followup.send(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in /forecast: {e}")
        await interaction.followup.send(f"❌ Error: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="safeentry", description="🛡️ Sniper Safe Entry: Demand/Supply zone, Stop-Hunt tracker & Max Safe Leverage to avoid liquidation.")
@app_commands.describe(symbol="Asset symbol (e.g. BTC/USDT, GOLD, ETH/USDT, SOL/USDT)")
async def safeentry_command(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    disp = format_display_symbol(symbol)
    try:
        df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=80)
        df_4h = await mexc_client.get_klines(symbol, interval="4h", limit=80)
        depth = await mexc_client.get_order_book_depth(symbol, limit=20)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)

        if df_5m is None or len(df_5m) < 30:
            await interaction.followup.send(f"❌ Could not fetch market data for `{disp}` from MEXC.", ephemeral=True)
            return

        setup = CryptoAnalyzer.generate_safe_entry_setup(df_5m, df_4h, disp, depth=depth, flow=flow)
        if not setup:
            await interaction.followup.send(f"❌ Failed to calculate safe entry setup for `{disp}`.", ephemeral=True)
            return

        embed = build_safe_entry_embed(setup)
        target_entry = setup.safe_entry_low if "LONG" in setup.direction else setup.safe_entry_high
        view = QuickTradeView(disp, target_entry, sl=setup.stop_loss, tp=setup.take_profit_1)
        await interaction.followup.send(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in /safeentry: {e}")
        await interaction.followup.send(f"❌ Error: `{str(e)}`", ephemeral=True)


# ==================== AI ACTIVE TRADE COPILOT COMMANDS ====================

@bot.tree.command(name="track", description="🤖 AI Trade Copilot: Track your active trade entry price, leverage, margin & Est. Liq.")
@app_commands.describe(
    symbol="Asset symbol (e.g. GOLD, BTC/USDT, SOL/USDT)",
    direction="LONG or SHORT",
    entry_price="Your actual entry price",
    leverage="Leverage multiplier (e.g. 5, 10, 20, 50) - Default: 1x",
    margin="Margin allocated in USD (e.g. 100, 500, 1000)",
    timeframe="Timeframe to monitor (1m, 5m, 1h, 4h) - Default: 5m",
    stop_loss="Optional Stop Loss price",
    take_profit="Optional Take Profit price",
)
@app_commands.choices(
    direction=[app_commands.Choice(name="LONG 🟢", value="LONG"), app_commands.Choice(name="SHORT 🔴", value="SHORT")],
    timeframe=[
        app_commands.Choice(name="5 Minutes (Fast Scalp)", value="5m"),
        app_commands.Choice(name="1 Minute (Micro Scalp)", value="1m"),
        app_commands.Choice(name="1 Hour (Day Trade)", value="1h"),
        app_commands.Choice(name="4 Hours (Macro Swing)", value="4h"),
    ],
)
async def track_command(
    interaction: discord.Interaction,
    symbol: str,
    direction: app_commands.Choice[str],
    entry_price: float,
    leverage: Optional[int] = 1,
    margin: Optional[float] = 0.0,
    timeframe: Optional[app_commands.Choice[str]] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
):
    await interaction.response.defer(thinking=True)
    disp = format_display_symbol(symbol)
    tf = timeframe.value if timeframe else "5m"
    dir_val = direction.value
    lev = leverage if leverage and leverage >= 1 else 1
    m_usd = margin if margin and margin > 0 else 0.0

    success, msg, trade = paper_trader.start_trade_tracking(
        user_id=str(interaction.user.id),
        user_name=interaction.user.display_name,
        channel_id=interaction.channel.id,
        symbol=disp,
        direction=dir_val,
        entry_price=entry_price,
        timeframe=tf,
        stop_loss=stop_loss,
        take_profit=take_profit,
        leverage=lev,
        margin_usd=m_usd,
    )

    if not success:
        await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        return

    curr_p = await fetch_current_price(disp) or entry_price
    df = await mexc_client.get_klines(symbol, interval=tf, limit=60)
    analysis = CryptoAnalyzer.analyze(df, disp, tf) if df is not None and len(df) >= 35 else None

    embed = build_copilot_status_embed(trade, curr_p, analysis)
    view = CopilotActionView(str(interaction.user.id))
    await interaction.followup.send(content=f"🤖 **AI Active Copilot Engaged for <@{interaction.user.id}>!**", embed=embed, view=view)


@bot.tree.command(name="status", description="📊 Check live status, PnL & AI advice for your active tracked trade.")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    trade = paper_trader.get_active_tracked_trade(str(interaction.user.id))
    if not trade:
        await interaction.followup.send("ℹ️ You don't have any active trade tracked. Use `/track` or `!entrypricein <symbol> <long/short> <entry>` to start!", ephemeral=True)
        return

    curr_p = await fetch_current_price(trade.symbol) or trade.entry_price
    df = await mexc_client.get_klines(trade.symbol, interval=trade.timeframe, limit=60)
    analysis = CryptoAnalyzer.analyze(df, trade.symbol, trade.timeframe) if df is not None and len(df) >= 35 else None

    embed = build_copilot_status_embed(trade, curr_p, analysis)
    view = CopilotActionView(str(interaction.user.id))
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="stoptrack", description="🏁 Stop AI Copilot tracking on your trade.")
async def stoptrack_command(interaction: discord.Interaction):
    ok = paper_trader.stop_trade_tracking(str(interaction.user.id))
    if ok:
        await interaction.response.send_message("🏁 AI Trade Copilot disengaged. Tracking stopped successfully!")
    else:
        await interaction.response.send_message("ℹ️ No active trade was being tracked.", ephemeral=True)


# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="radar", description="🌐 Full Market Radar: Scans all coins & finds Best Swings, Day Trades, & Scalps.")
async def radar_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        swings, daytrades, scalps = [], [], []

        for symbol in active_watchlist:
            disp = format_display_symbol(symbol)
            df_4h = await mexc_client.get_klines(symbol, interval="4h", limit=60)
            df_1h = await mexc_client.get_klines(symbol, interval="1h", limit=60)
            df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=60)

            if df_4h is not None and len(df_4h) >= 35:
                sw = CryptoAnalyzer.generate_swing_setup(df_4h, disp, "4h")
                if sw and sw.confidence >= 80 and "CONSOLIDATION" not in sw.direction:
                    swings.append({
                        "symbol": disp,
                        "direction": sw.direction,
                        "confidence": sw.confidence,
                        "price": sw.current_price,
                        "entry_zone": sw.entry_zone,
                        "target": sw.target_1,
                        "gain": sw.target_1_gain_pct,
                        "sl": sw.stop_loss,
                    })

            if df_1h is not None and len(df_1h) >= 35:
                sw_1h = CryptoAnalyzer.generate_swing_setup(df_1h, disp, "1h")
                if sw_1h and sw_1h.confidence >= 80 and "CONSOLIDATION" not in sw_1h.direction:
                    daytrades.append({
                        "symbol": disp,
                        "direction": sw_1h.direction,
                        "confidence": sw_1h.confidence,
                        "price": sw_1h.current_price,
                        "entry_zone": sw_1h.entry_zone,
                        "target": sw_1h.target_1,
                        "gain": sw_1h.target_1_gain_pct,
                        "sl": sw_1h.stop_loss,
                    })

            if df_5m is not None and len(df_5m) >= 35:
                res_5m = CryptoAnalyzer.analyze(df_5m, disp, "5m")
                if res_5m and res_5m.scalp and res_5m.scalp.confidence >= 80 and "WAIT" not in res_5m.scalp.action:
                    sc = res_5m.scalp
                    curr_p = res_5m.current_price
                    gain = abs(sc.scalp_tp1 - curr_p) / curr_p * 100.0
                    scalps.append({
                        "symbol": disp,
                        "direction": sc.action,
                        "confidence": sc.confidence,
                        "price": curr_p,
                        "entry_zone": sc.entry_zone,
                        "target": sc.scalp_tp1,
                        "gain": gain,
                        "sl": sc.tight_stop_loss,
                    })

        swings.sort(key=lambda x: x["confidence"], reverse=True)
        daytrades.sort(key=lambda x: x["confidence"], reverse=True)
        scalps.sort(key=lambda x: x["confidence"], reverse=True)

        embed = build_multi_horizon_embed(swings, daytrades, scalps)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in /radar: {e}")
        await interaction.followup.send(f"❌ Error: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="swing", description="🌊 Higher Timeframe (1h/4h) Swing Trade Radar & Hold Duration Anticipation.")
@app_commands.describe(
    symbol="Trading pair (e.g. GOLD, BTC/USDT, SOL/USDT, ETH/USDT)",
    timeframe="Swing timeframe (1h for intraday swing, 4h for macro swing) - Default: 4h",
)
@app_commands.choices(
    timeframe=[
        app_commands.Choice(name="4 Hours (Macro Swing - High Win Rate)", value="4h"),
        app_commands.Choice(name="1 Hour (Intraday Swing)", value="1h"),
    ]
)
async def swing_command(interaction: discord.Interaction, symbol: str, timeframe: Optional[app_commands.Choice[str]] = None):
    await interaction.response.defer(thinking=True)
    tf = timeframe.value if timeframe else "4h"
    disp = format_display_symbol(symbol)

    try:
        df = await mexc_client.get_klines(symbol, interval=tf, limit=120)
        if df is None or len(df) < 35:
            await interaction.followup.send(f"❌ Could not fetch sufficient {tf} data for `{disp}`.", ephemeral=True)
            return

        swing_setup = CryptoAnalyzer.generate_swing_setup(df, disp, tf)
        if not swing_setup:
            await interaction.followup.send(f"❌ Failed to calculate swing setup for `{disp}`.", ephemeral=True)
            return

        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=disp,
            interval=f"{tf.upper()} Swing",
            signal=swing_setup.direction,
            confidence_pct=swing_setup.confidence,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_swing_embed(swing_setup)
        embed.set_image(url="attachment://signal_banner.png")

        view = QuickTradeView(disp, swing_setup.current_price, sl=swing_setup.stop_loss, tp=swing_setup.target_1)
        await interaction.followup.send(embed=embed, file=file, view=view)

    except Exception as e:
        logger.error(f"Error in /swing: {e}")
        await interaction.followup.send(f"❌ Error: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="scan", description="🔍 Multi-Coin Market Potential Scanner: Scans entire watchlist for highest win-rate setups.")
@app_commands.describe(timeframe="Timeframe to scan (4h, 1h, 15m, 5m) - Default: 4h")
@app_commands.choices(
    timeframe=[
        app_commands.Choice(name="4 Hours (Macro Swings - Hold 2-6 Days)", value="4h"),
        app_commands.Choice(name="1 Hour (Day Trades - Hold 2-12 Hours)", value="1h"),
        app_commands.Choice(name="15 Minutes (Day Trades)", value="15m"),
        app_commands.Choice(name="5 Minutes (Scalp Setups)", value="5m"),
    ]
)
async def scan_command(interaction: discord.Interaction, timeframe: Optional[app_commands.Choice[str]] = None):
    await interaction.response.defer(thinking=True)
    tf = timeframe.value if timeframe else "4h"

    try:
        scan_results = []
        for symbol in active_watchlist:
            df = await mexc_client.get_klines(symbol, interval=tf, limit=80)
            if df is None or len(df) < 35:
                continue

            disp = format_display_symbol(symbol)
            curr_p = float(df["close"].iloc[-1])
            rsi = float(CryptoAnalyzer.calculate_rsi(df["close"], 14).iloc[-1])

            if tf in ("1h", "4h"):
                sw = CryptoAnalyzer.generate_swing_setup(df, disp, tf)
                if sw:
                    scan_results.append({
                        "symbol": disp,
                        "direction": sw.direction,
                        "confidence": sw.confidence,
                        "price": curr_p,
                        "entry_zone": sw.entry_zone,
                        "target": sw.target_1,
                        "target_gain": sw.target_1_gain_pct,
                        "sl": sw.stop_loss,
                        "rsi": rsi,
                    })
            else:
                res = CryptoAnalyzer.analyze(df, disp, tf)
                if res and res.scalp:
                    sc = res.scalp
                    target_gain = abs(sc.scalp_tp1 - curr_p) / curr_p * 100.0
                    scan_results.append({
                        "symbol": disp,
                        "direction": sc.action,
                        "confidence": sc.confidence,
                        "price": curr_p,
                        "entry_zone": sc.entry_zone,
                        "target": sc.scalp_tp1,
                        "target_gain": target_gain,
                        "sl": sc.tight_stop_loss,
                        "rsi": rsi,
                    })

        scan_results.sort(key=lambda x: x["confidence"], reverse=True)
        embed = build_scan_overview_embed(tf, scan_results)
        await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"Error in /scan: {e}")
        await interaction.followup.send(f"❌ Error during market scan: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="preorder", description="🛡️ Pre-Measure Limit Setup: Get optimal pending limit order price before the bounce.")
@app_commands.describe(
    symbol="Trading pair (e.g. GOLD, BTC/USDT, SOL/USDT)",
    timeframe="Analysis timeframe (5m, 15m, 1h, 4h) - Default: 5m",
)
async def preorder_command(interaction: discord.Interaction, symbol: str, timeframe: Optional[str] = "5m"):
    await interaction.response.defer(thinking=True)
    disp = format_display_symbol(symbol)
    tf = timeframe or "5m"

    try:
        df = await mexc_client.get_klines(symbol, interval=tf, limit=100)
        if df is None or len(df) < 35:
            await interaction.followup.send(f"❌ Could not fetch market data for `{disp}`.", ephemeral=True)
            return

        current_p = df["close"].iloc[-1]
        atr = CryptoAnalyzer.calculate_atr(df, 14).iloc[-1]
        pre = CryptoAnalyzer.generate_preorder_setup(df, disp, current_p, atr)

        if not pre:
            await interaction.followup.send(f"❌ Failed to generate pre-order setup for `{disp}`.", ephemeral=True)
            return

        embed = build_preorder_embed(pre, tf)
        view = QuickTradeView(disp, pre.limit_entry_price, sl=pre.tight_stop_loss, tp=pre.take_profit_1)
        await interaction.followup.send(embed=embed, view=view)

    except Exception as e:
        logger.error(f"Error in /preorder: {e}")
        await interaction.followup.send(f"❌ Error: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="calc", description="🧮 Position Size & Risk Calculator: calculate exact margin & risk.")
@app_commands.describe(
    account_balance="Your total account size in USD (e.g. 10000)",
    risk_pct="Percentage of account to risk (e.g. 1.0 or 2.0) - Default: 1.0%",
    entry_price="Planned entry price",
    stop_loss="Planned stop loss price",
)
async def calc_command(
    interaction: discord.Interaction,
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    risk_pct: Optional[float] = 1.0,
):
    if entry_price <= 0 or stop_loss <= 0 or account_balance <= 0:
        await interaction.response.send_message("❌ Invalid price or balance parameters.", ephemeral=True)
        return

    risk_dollars = account_balance * ((risk_pct or 1.0) / 100.0)
    price_risk_pct = abs(entry_price - stop_loss) / entry_price * 100.0

    if price_risk_pct <= 0:
        await interaction.response.send_message("❌ Stop loss cannot equal entry price.", ephemeral=True)
        return

    position_size_usd = risk_dollars / (price_risk_pct / 100.0)
    suggested_leverage = max(1, min(50, int(position_size_usd / (account_balance * 0.2))))

    embed = discord.Embed(
        title="🧮 Professional Position Size & Risk Calculator",
        color=0x3498DB,
        description=f"Risking **{risk_pct:.1f}%** (`${risk_dollars:,.2f}`) of **`${account_balance:,.2f}`** account.",
    )
    embed.add_field(name="📍 Entry Price", value=f"`${entry_price:,.2f}`", inline=True)
    embed.add_field(name="🛑 Stop Loss", value=f"`${stop_loss:,.2f}`", inline=True)
    embed.add_field(name="📏 Distance to SL", value=f"`{price_risk_pct:.2f}%`", inline=True)

    embed.add_field(name="💵 Max Position Size", value=f"**`${position_size_usd:,.2f}`**", inline=True)
    embed.add_field(name="🔒 Max Loss if SL Hit", value=f"**`-${risk_dollars:,.2f}`**", inline=True)
    embed.add_field(name="⚡ Suggested Leverage", value=f"`{suggested_leverage}x`", inline=True)

    embed.set_footer(text="Strict risk management guarantees long-term trading survival.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="mtf", description="⭐ Multi-Timeframe Confluence: Checks 5m Trend + 1m Trigger for maximum win rate.")
@app_commands.describe(symbol="Symbol to analyze (e.g. GOLD, BTC/USDT, SOL/USDT)")
async def mtf_command(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    disp_symbol = format_display_symbol(symbol)

    try:
        df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=100)
        df_1m = await mexc_client.get_klines(symbol, interval="1m", limit=100)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)

        if df_5m is None or len(df_5m) < 35 or df_1m is None or len(df_1m) < 35:
            await interaction.followup.send(f"❌ Could not fetch sufficient data for `{disp_symbol}` from MEXC.", ephemeral=True)
            return

        result = CryptoAnalyzer.analyze_dual_timeframe_confluence(df_5m, df_1m, disp_symbol)
        if not result or not result.scalp:
            await interaction.followup.send(f"❌ Failed to run MTF analysis for `{disp_symbol}`.", ephemeral=True)
            return

        banner_buf = ChartGenerator.generate_signal_banner(
            df=df_5m,
            symbol=disp_symbol,
            interval="5m+1m MTF",
            signal=result.scalp.action,
            confidence_pct=result.scalp.confidence,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_scalp_embed(result, is_auto_alert=False, flow=flow)
        view = QuickTradeView(disp_symbol, result.current_price, sl=result.scalp.tight_stop_loss, tp=result.scalp.scalp_tp1)

        await interaction.followup.send(embed=embed, file=file, view=view)

    except Exception as e:
        logger.error(f"Error in /mtf command: {e}")
        await interaction.followup.send(f"❌ Error during MTF analysis: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="scalp", description="Instant AI Scalp Advisor: gives Long, Short, or Wait recommendation.")
@app_commands.describe(
    symbol="Symbol (e.g. BTC/USDT, GOLD, ETH/USDT, SOL/USDT)",
    timeframe="Timeframe (1m, 5m, 15m) - Default: 5m",
)
@app_commands.choices(
    timeframe=[
        app_commands.Choice(name="5 Minutes (High Win Rate)", value="5m"),
        app_commands.Choice(name="1 Minute (Fast Scalp)", value="1m"),
        app_commands.Choice(name="15 Minutes (Extended Scalp)", value="15m"),
    ]
)
async def scalp_command(
    interaction: discord.Interaction,
    symbol: str,
    timeframe: Optional[app_commands.Choice[str]] = None,
):
    await interaction.response.defer(thinking=True)
    tf = timeframe.value if timeframe else "5m"
    disp_symbol = format_display_symbol(symbol)

    try:
        df = await mexc_client.get_klines(symbol, interval=tf, limit=120)
        if df is None or len(df) < 35:
            await interaction.followup.send(f"❌ Could not fetch sufficient scalp data for `{disp_symbol}` from MEXC.", ephemeral=True)
            return

        ticker_24h = await mexc_client.get_24hr_ticker(symbol)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)
        result = CryptoAnalyzer.analyze(df, disp_symbol, tf, ticker_24h)

        if not result:
            await interaction.followup.send(f"❌ Failed to run scalp analysis for `{disp_symbol}`.", ephemeral=True)
            return

        action_label = result.scalp.action if result.scalp else result.overall_signal
        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=disp_symbol,
            interval=tf,
            signal=action_label,
            confidence_pct=result.scalp.confidence if result.scalp else result.signal_strength_pct,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_scalp_embed(result, flow=flow)
        view = QuickTradeView(disp_symbol, result.current_price, sl=result.scalp.tight_stop_loss if result.scalp else None, tp=result.scalp.scalp_tp1 if result.scalp else None)

        await interaction.followup.send(embed=embed, file=file, view=view)

    except Exception as e:
        logger.error(f"Error in /scalp command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error during scalp analysis: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="analyze", description="Deep technical analysis and signal card for a MEXC crypto or commodity pair.")
@app_commands.describe(
    symbol="Trading pair symbol (e.g. BTC/USDT, GOLD, ETH/USDT, SOL/USDT, PEPE/USDT)",
    timeframe="Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d) - Default: 5m",
)
@app_commands.choices(
    timeframe=[
        app_commands.Choice(name="4 Hours (Macro Swing)", value="4h"),
        app_commands.Choice(name="1 Hour (Intraday Swing)", value="1h"),
        app_commands.Choice(name="15 Minutes (Day Trade)", value="15m"),
        app_commands.Choice(name="5 Minutes (Fast Scalp)", value="5m"),
        app_commands.Choice(name="1 Minute (Micro Trigger)", value="1m"),
        app_commands.Choice(name="1 Day (Macro Trend)", value="1d"),
    ]
)
async def analyze_command(
    interaction: discord.Interaction,
    symbol: str,
    timeframe: Optional[app_commands.Choice[str]] = None,
):
    await interaction.response.defer(thinking=True)
    tf = timeframe.value if timeframe else config.DEFAULT_TIMEFRAME
    disp_symbol = format_display_symbol(symbol)

    try:
        df = await mexc_client.get_klines(symbol, interval=tf, limit=120)
        if df is None or len(df) < 35:
            await interaction.followup.send(f"❌ Could not fetch sufficient data for `{disp_symbol}` from MEXC.", ephemeral=True)
            return

        ticker_24h = await mexc_client.get_24hr_ticker(symbol)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)
        depth = await mexc_client.get_order_book_depth(symbol, limit=20)
        result = CryptoAnalyzer.analyze(df, disp_symbol, tf, ticker_24h)

        if not result:
            await interaction.followup.send(f"❌ Failed to run technical analysis for `{disp_symbol}`.", ephemeral=True)
            return

        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=disp_symbol,
            interval=tf,
            signal=result.overall_signal,
            confidence_pct=result.signal_strength_pct,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_signal_embed(result, flow=flow, depth=depth)
        view = QuickTradeView(disp_symbol, result.current_price, sl=result.stop_loss, tp=result.take_profit_1)

        await interaction.followup.send(embed=embed, file=file, view=view)

    except Exception as e:
        logger.error(f"Error in /analyze command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ An error occurred during analysis: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="setchannel", description="Set the current channel as the automated Scalp & Signal alert channel.")
@app_commands.describe(channel="Target channel to receive automated notifications (defaults to current channel)")
async def setchannel_command(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    global current_alert_channel_id
    target_channel = channel or interaction.channel
    current_alert_channel_id = target_channel.id

    embed = discord.Embed(
        title="⭐ Dual-Timeframe Confluence Alerts Configured!",
        description=(
            f"High-win-rate scalp signals will now be broadcast to {target_channel.mention}.\n\n"
            f"• **Engine**: **5m Trend + 1m Precision Entry Trigger (Dual Confluence)**\n"
            f"• **Scan Frequency**: Every 1 minute\n"
            f"• **Pre-Measures**: Automatically includes Real-Trade Limit Order Execution Plans\n"
            f"• **Monitored Pairs**: `{', '.join(active_watchlist[:6])}...`"
        ),
        color=0x00FF88,
    )
    await interaction.response.send_message(embed=embed)


# ==================== PAPER TRADING SLASH COMMANDS ====================

@bot.tree.command(name="long", description="🎮 Open a Paper Trading LONG position with Take Profit & Stop Loss.")
@app_commands.describe(
    symbol="Trading pair (e.g. GOLD, BTC/USDT, SOL/USDT)",
    amount="Virtual USD margin (e.g. 500 or 1000)",
    leverage="Leverage multiplier (1x to 50x) - Default: 1x",
    stop_loss="Optional Stop Loss price (e.g. 4410)",
    take_profit="Optional Take Profit price (e.g. 4460)",
)
async def paper_long_command(
    interaction: discord.Interaction,
    symbol: str,
    amount: float,
    leverage: Optional[int] = 1,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
):
    await interaction.response.defer(thinking=True)
    disp_symbol = format_display_symbol(symbol)
    price = await fetch_current_price(symbol)
    if not price:
        await interaction.followup.send(f"❌ Could not fetch market price for `{disp_symbol}`.", ephemeral=True)
        return

    success, msg, pos = paper_trader.open_position(
        user_id=str(interaction.user.id),
        user_name=interaction.user.display_name,
        symbol=disp_symbol,
        direction="LONG",
        amount_usd=amount,
        entry_price=price,
        leverage=leverage or 1,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    if not success:
        await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🟢 Paper LONG Opened: {disp_symbol}",
        color=config.COLOR_STRONG_BUY,
        description=f"**Position #{pos.id}** opened at current live price.",
    )
    embed.add_field(name="Entry Price", value=f"`${price:,.2f}`", inline=True)
    embed.add_field(name="Margin Allocated", value=f"`${amount:,.2f}`", inline=True)
    embed.add_field(name="Leverage / Size", value=f"`{pos.leverage}x` (${pos.amount_usd * pos.leverage:,.2f})", inline=True)
    embed.add_field(name="🛑 Stop Loss (SL)", value=f"`${stop_loss:,.2f}`" if stop_loss else "`None`", inline=True)
    embed.add_field(name="🎯 Take Profit (TP)", value=f"`${take_profit:,.2f}`" if take_profit else "`None`", inline=True)
    embed.set_footer(text="Auto TP/SL monitor is active • Check /portfolio to manage trade")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="short", description="🎮 Open a Paper Trading SHORT position with Take Profit & Stop Loss.")
@app_commands.describe(
    symbol="Trading pair (e.g. GOLD, BTC/USDT, SOL/USDT)",
    amount="Virtual USD margin (e.g. 500 or 1000)",
    leverage="Leverage multiplier (1x to 50x) - Default: 1x",
    stop_loss="Optional Stop Loss price (e.g. 4460)",
    take_profit="Optional Take Profit price (e.g. 4410)",
)
async def paper_short_command(
    interaction: discord.Interaction,
    symbol: str,
    amount: float,
    leverage: Optional[int] = 1,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
):
    await interaction.response.defer(thinking=True)
    disp_symbol = format_display_symbol(symbol)
    price = await fetch_current_price(symbol)
    if not price:
        await interaction.followup.send(f"❌ Could not fetch market price for `{disp_symbol}`.", ephemeral=True)
        return

    success, msg, pos = paper_trader.open_position(
        user_id=str(interaction.user.id),
        user_name=interaction.user.display_name,
        symbol=disp_symbol,
        direction="SHORT",
        amount_usd=amount,
        entry_price=price,
        leverage=leverage or 1,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    if not success:
        await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔴 Paper SHORT Opened: {disp_symbol}",
        color=config.COLOR_STRONG_SELL,
        description=f"**Position #{pos.id}** opened at current live price.",
    )
    embed.add_field(name="Entry Price", value=f"`${price:,.2f}`", inline=True)
    embed.add_field(name="Margin Allocated", value=f"`${amount:,.2f}`", inline=True)
    embed.add_field(name="Leverage / Size", value=f"`{pos.leverage}x` (${pos.amount_usd * pos.leverage:,.2f})", inline=True)
    embed.add_field(name="🛑 Stop Loss (SL)", value=f"`${stop_loss:,.2f}`" if stop_loss else "`None`", inline=True)
    embed.add_field(name="🎯 Take Profit (TP)", value=f"`${take_profit:,.2f}`" if take_profit else "`None`", inline=True)
    embed.set_footer(text="Auto TP/SL monitor is active • Check /portfolio to manage trade")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="portfolio", description="🎮 View your Paper Trading balance, open positions, TP/SL, and Win Rate.")
async def portfolio_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name

    positions = paper_trader.get_open_positions(user_id)
    live_prices: Dict[str, float] = {}

    for p in positions:
        if p.symbol not in live_prices:
            curr_p = await fetch_current_price(p.symbol)
            if curr_p:
                live_prices[p.symbol] = curr_p

    summary: AccountSummary = paper_trader.get_portfolio_summary(user_id, user_name, live_prices)
    embed = build_portfolio_embed(user_name, summary, positions, live_prices)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="winrate", description="📊 View your detailed Win Rate, Net Profits, and Trading Statistics.")
async def winrate_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name
    summary: AccountSummary = paper_trader.get_portfolio_summary(user_id, user_name, {})
    embed = build_winrate_embed(user_name, summary)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="settp", description="🎯 Set or update Take Profit price on an open paper trade.")
@app_commands.describe(position_id="Position ID (from /portfolio)", tp_price="New Take Profit price")
async def settp_command(interaction: discord.Interaction, position_id: int, tp_price: float):
    user_id = str(interaction.user.id)
    ok, msg = paper_trader.update_tpsl(position_id, user_id, take_profit=tp_price)
    if ok:
        await interaction.response.send_message(f"✅ {msg}")
    else:
        await interaction.response.send_message(f"❌ {msg}", ephemeral=True)


@bot.tree.command(name="setsl", description="🛑 Set or update Stop Loss price on an open paper trade.")
@app_commands.describe(position_id="Position ID (from /portfolio)", sl_price="New Stop Loss price")
async def setsl_command(interaction: discord.Interaction, position_id: int, sl_price: float):
    user_id = str(interaction.user.id)
    ok, msg = paper_trader.update_tpsl(position_id, user_id, stop_loss=sl_price)
    if ok:
        await interaction.response.send_message(f"✅ {msg}")
    else:
        await interaction.response.send_message(f"❌ {msg}", ephemeral=True)


@bot.tree.command(name="close", description="🎮 Close an open paper position and lock in PnL.")
@app_commands.describe(position_id="ID number of the position to close (check /portfolio)")
async def close_command(interaction: discord.Interaction, position_id: int):
    await interaction.response.defer(thinking=True)
    user_id = str(interaction.user.id)

    positions = paper_trader.get_open_positions(user_id)
    target_pos = next((p for p in positions if p.id == position_id), None)

    if not target_pos:
        await interaction.followup.send(f"❌ Position `#{position_id}` was not found in your open trades.", ephemeral=True)
        return

    curr_p = await fetch_current_price(target_pos.symbol)
    if not curr_p:
        await interaction.followup.send(f"❌ Could not fetch current market price for `{target_pos.symbol}`.", ephemeral=True)
        return

    success, msg, summary = paper_trader.close_position(position_id, curr_p, reason="MANUAL")
    if not success:
        await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        return

    pnl_val = summary["pnl_usd"]
    color = config.COLOR_STRONG_BUY if pnl_val >= 0 else config.COLOR_STRONG_SELL

    embed = discord.Embed(
        title=f"🏁 Position Closed: #{position_id} {target_pos.symbol}",
        color=color,
        description=f"Closed at current price of **`${curr_p:,.2f}`**.",
    )
    embed.add_field(name="Direction", value=f"`{target_pos.direction}` ({target_pos.leverage}x)", inline=True)
    embed.add_field(name="Entry ➔ Exit", value=f"`${target_pos.entry_price:,.2f}` ➔ `${curr_p:,.2f}`", inline=True)
    embed.add_field(name="Realized PnL", value=f"**`${pnl_val:+,.2f}` ({summary['pnl_pct']:+.2f}%)**", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="history", description="🎮 View your recent paper trade history.")
async def history_command(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    history = paper_trader.get_trade_history(user_id, limit=8)

    if not history:
        await interaction.response.send_message("ℹ️ You haven't closed any paper trades yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📜 {interaction.user.display_name}'s Recent Trade History",
        color=0x3498DB,
    )
    for h in history:
        pnl_usd = h["pnl_usd"]
        pnl_icon = "🟢" if pnl_usd >= 0 else "🔴"
        field_name = f"{pnl_icon} {h['symbol']} {h['direction']} ({h['leverage']}x) • {h['close_reason']}"
        field_val = (
            f"• Entry: `${h['entry_price']:,.2f}` ➔ Exit: `${h['exit_price']:,.2f}`\n"
            f"• Result: **`${pnl_usd:+,.2f}` ({h['pnl_pct']:+.2f}%)** | Margin: `${h['amount_usd']:,.2f}`"
        )
        embed.add_field(name=field_name, value=field_val, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="🏆 Server Paper Trading Leaderboard.")
async def leaderboard_command(interaction: discord.Interaction):
    leaders = paper_trader.get_leaderboard(limit=10)
    if not leaders:
        await interaction.response.send_message("ℹ️ No paper trading stats recorded yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏆 Server Paper Trading Leaderboard",
        color=0xF1C40F,
        description="Top traders ranked by Total Realized Profit:",
    )
    for i, l in enumerate(leaders, 1):
        win_rate = (l["winning_trades"] / l["total_trades"] * 100.0) if l["total_trades"] > 0 else 0
        badge = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"#{i}"))
        name = f"{badge} {l['user_name']}"
        val = (
            f"• **Total Profit:** `${l['total_realized_pnl']:+,.2f}`\n"
            f"• **Win Rate:** `{win_rate:.1f}%` ({l['winning_trades']}W / {l['losing_trades']}L across {l['total_trades']} trades)"
        )
        embed.add_field(name=name, value=val, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="resetaccount", description="🎮 Reset your paper trading balance back to $10,000.")
async def resetaccount_command(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    paper_trader.reset_account(user_id, interaction.user.display_name)
    await interaction.response.send_message("🔄 Your paper trading account has been reset back to **$10,000.00** virtual cash!", ephemeral=True)


# ==================== PREFIX COMMANDS ====================

@bot.command(name="orderflow", aliases=["depth", "book", "trades"])
async def prefix_orderflow(ctx: commands.Context, symbol: str = "BTC/USDT"):
    """Usage: !orderflow BTC or !orderflow GOLD"""
    disp = format_display_symbol(symbol)
    async with ctx.typing():
        depth = await mexc_client.get_order_book_depth(symbol, limit=20)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=60)

        if not depth and not flow:
            await ctx.send(f"❌ Could not fetch order flow data for `{disp}` from MEXC.")
            return

        embed = build_orderflow_embed(disp, depth, flow)
        await ctx.send(embed=embed)


@bot.command(name="forecast", aliases=["outlook", "trend", "predict"])
async def prefix_forecast(ctx: commands.Context, symbol: str = "BTC/USDT"):
    """Usage: !forecast BTC or !forecast GOLD"""
    disp = format_display_symbol(symbol)
    async with ctx.typing():
        df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=80)
        df_4h = await mexc_client.get_klines(symbol, interval="4h", limit=80)
        df_1d = await mexc_client.get_klines(symbol, interval="1d", limit=80)

        if df_5m is None or len(df_5m) < 30:
            await ctx.send(f"❌ Could not fetch market data for `{disp}` from MEXC.")
            return

        forecast = CryptoAnalyzer.generate_multi_horizon_forecast(df_5m, df_4h, df_1d, disp)
        if not forecast:
            await ctx.send(f"❌ Failed to generate multi-horizon forecast for `{disp}`.")
            return

        embed = build_forecast_embed(forecast)
        view = QuickTradeView(disp, forecast.current_price, sl=forecast.macro_invalidation, tp=forecast.mid_term.projected_target)
        await ctx.send(embed=embed, view=view)


@bot.command(name="safeentry", aliases=["safe", "sniper", "entryzone", "safety"])
async def prefix_safeentry(ctx: commands.Context, symbol: str = "BTC/USDT"):
    """Usage: !safeentry BTC or !safeentry GOLD"""
    disp = format_display_symbol(symbol)
    async with ctx.typing():
        df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=80)
        df_4h = await mexc_client.get_klines(symbol, interval="4h", limit=80)
        depth = await mexc_client.get_order_book_depth(symbol, limit=20)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)

        if df_5m is None or len(df_5m) < 30:
            await ctx.send(f"❌ Could not fetch market data for `{disp}` from MEXC.")
            return

        setup = CryptoAnalyzer.generate_safe_entry_setup(df_5m, df_4h, disp, depth=depth, flow=flow)
        if not setup:
            await ctx.send(f"❌ Failed to calculate safe entry setup for `{disp}`.")
            return

        embed = build_safe_entry_embed(setup)
        target_entry = setup.safe_entry_low if "LONG" in setup.direction else setup.safe_entry_high
        view = QuickTradeView(disp, target_entry, sl=setup.stop_loss, tp=setup.take_profit_1)
        await ctx.send(embed=embed, view=view)


@bot.command(name="entrypricein", aliases=["track", "follow"])
async def prefix_entrypricein(ctx: commands.Context, *args):
    """
    Usage:
      !entrypricein <symbol> <LONG/SHORT> <entry_price> [leverage: 10x] [margin: 500] [timeframe: 5m] [SL] [TP]
    Examples:
      !entrypricein GOLD LONG 4405.20 10x 500 5m
      !entrypricein BTC LONG 78500 20x $1000
      !entrypricein SOL SHORT 145.50 5x
      !entrypricein GOLD LONG 4405.20 5m
    """
    if len(args) < 3:
        await ctx.send(
            "❌ **Usage:** `!entrypricein <symbol> <LONG/SHORT> <entry_price> [leverage: 10x] [margin: 500] [timeframe: 5m] [SL] [TP]`\n"
            "• **Examples:**\n"
            "  `!entrypricein GOLD LONG 4405.20 10x 500 5m`\n"
            "  `!entrypricein BTC LONG 78500 20x 1000`\n"
            "  `!entrypricein SOL SHORT 145.50 5x`"
        )
        return

    symbol = args[0]
    direction = args[1].upper()
    try:
        entry_price = float(str(args[2]).replace("$", "").replace(",", ""))
    except ValueError:
        await ctx.send(f"❌ Invalid entry price: `{args[2]}`.")
        return

    if direction not in ("LONG", "SHORT"):
        await ctx.send("❌ Direction must be **LONG** or **SHORT**.")
        return

    # Dynamic parsing for remaining arguments
    leverage = 1
    margin_usd = 0.0
    timeframe = "5m"
    sl = None
    tp = None

    valid_tfs = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    remaining_floats = []

    for a in args[3:]:
        clean = str(a).strip().lower()
        if clean.endswith("x") and clean[:-1].isdigit():
            leverage = int(clean[:-1])
        elif clean in valid_tfs:
            timeframe = clean
        elif clean.startswith("$"):
            try:
                margin_usd = float(clean[1:].replace(",", ""))
            except ValueError:
                pass
        else:
            try:
                num = float(clean.replace(",", ""))
                remaining_floats.append(num)
            except ValueError:
                pass

    # Interpret unflagged numbers:
    if remaining_floats:
        first_num = remaining_floats[0]
        # Check if first number is leverage without 'x'
        if leverage == 1 and first_num in [1, 2, 3, 5, 10, 20, 25, 50, 75, 100, 125] and len(remaining_floats) >= 2:
            leverage = int(first_num)
            margin_usd = remaining_floats[1]
            remaining_floats = remaining_floats[2:]
        elif margin_usd == 0.0 and (first_num >= 10.0 or "." in str(first_num)):
            margin_usd = first_num
            remaining_floats = remaining_floats[1:]

    if len(remaining_floats) >= 1 and sl is None:
        sl = remaining_floats[0]
    if len(remaining_floats) >= 2 and tp is None:
        tp = remaining_floats[1]

    disp = format_display_symbol(symbol)
    success, msg, trade = paper_trader.start_trade_tracking(
        user_id=str(ctx.author.id),
        user_name=ctx.author.display_name,
        channel_id=ctx.channel.id,
        symbol=disp,
        direction=direction,
        entry_price=entry_price,
        timeframe=timeframe,
        stop_loss=sl,
        take_profit=tp,
        leverage=leverage,
        margin_usd=margin_usd,
    )

    if not success:
        await ctx.send(f"❌ {msg}")
        return

    curr_p = await fetch_current_price(disp) or entry_price
    df = await mexc_client.get_klines(symbol, interval=timeframe, limit=60)
    analysis = CryptoAnalyzer.analyze(df, disp, timeframe) if df is not None and len(df) >= 35 else None

    embed = build_copilot_status_embed(trade, curr_p, analysis)
    view = CopilotActionView(str(ctx.author.id))
    await ctx.send(content=f"🤖 **AI Active Copilot Engaged for {ctx.author.mention}!**", embed=embed, view=view)


@bot.command(name="status")
async def prefix_status(ctx: commands.Context):
    trade = paper_trader.get_active_tracked_trade(str(ctx.author.id))
    if not trade:
        await ctx.send("ℹ️ You don't have any active trade tracked. Type `!entrypricein <symbol> <LONG/SHORT> <entry> [timeframe]` to start!")
        return

    curr_p = await fetch_current_price(trade.symbol) or trade.entry_price
    df = await mexc_client.get_klines(trade.symbol, interval=trade.timeframe, limit=60)
    analysis = CryptoAnalyzer.analyze(df, trade.symbol, trade.timeframe) if df is not None and len(df) >= 35 else None

    embed = build_copilot_status_embed(trade, curr_p, analysis)
    view = CopilotActionView(str(ctx.author.id))
    await ctx.send(embed=embed, view=view)


@bot.command(name="stoptrack", aliases=["untrack"])
async def prefix_stoptrack(ctx: commands.Context):
    ok = paper_trader.stop_trade_tracking(str(ctx.author.id))
    if ok:
        await ctx.send("🏁 AI Trade Copilot disengaged. Tracking stopped!")
    else:
        await ctx.send("ℹ️ No active trade was being tracked.")


@bot.command(name="radar", aliases=["opportunities", "market"])
async def prefix_radar(ctx: commands.Context):
    async with ctx.typing():
        swings, daytrades, scalps = [], [], []
        for symbol in active_watchlist:
            disp = format_display_symbol(symbol)
            df_4h = await mexc_client.get_klines(symbol, interval="4h", limit=60)
            df_1h = await mexc_client.get_klines(symbol, interval="1h", limit=60)
            df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=60)

            if df_4h is not None and len(df_4h) >= 35:
                sw = CryptoAnalyzer.generate_swing_setup(df_4h, disp, "4h")
                if sw and sw.confidence >= 80 and "CONSOLIDATION" not in sw.direction:
                    swings.append({
                        "symbol": disp,
                        "direction": sw.direction,
                        "confidence": sw.confidence,
                        "price": sw.current_price,
                        "entry_zone": sw.entry_zone,
                        "target": sw.target_1,
                        "gain": sw.target_1_gain_pct,
                        "sl": sw.stop_loss,
                    })

            if df_1h is not None and len(df_1h) >= 35:
                sw_1h = CryptoAnalyzer.generate_swing_setup(df_1h, disp, "1h")
                if sw_1h and sw_1h.confidence >= 80 and "CONSOLIDATION" not in sw_1h.direction:
                    daytrades.append({
                        "symbol": disp,
                        "direction": sw_1h.direction,
                        "confidence": sw_1h.confidence,
                        "price": sw_1h.current_price,
                        "entry_zone": sw_1h.entry_zone,
                        "target": sw_1h.target_1,
                        "gain": sw_1h.target_1_gain_pct,
                        "sl": sw_1h.stop_loss,
                    })

            if df_5m is not None and len(df_5m) >= 35:
                res_5m = CryptoAnalyzer.analyze(df_5m, disp, "5m")
                if res_5m and res_5m.scalp and res_5m.scalp.confidence >= 80 and "WAIT" not in res_5m.scalp.action:
                    sc = res_5m.scalp
                    curr_p = res_5m.current_price
                    gain = abs(sc.scalp_tp1 - curr_p) / curr_p * 100.0
                    scalps.append({
                        "symbol": disp,
                        "direction": sc.action,
                        "confidence": sc.confidence,
                        "price": curr_p,
                        "entry_zone": sc.entry_zone,
                        "target": sc.scalp_tp1,
                        "gain": gain,
                        "sl": sc.tight_stop_loss,
                    })

        swings.sort(key=lambda x: x["confidence"], reverse=True)
        daytrades.sort(key=lambda x: x["confidence"], reverse=True)
        scalps.sort(key=lambda x: x["confidence"], reverse=True)

        embed = build_multi_horizon_embed(swings, daytrades, scalps)
        await ctx.send(embed=embed)


@bot.command(name="swing")
async def prefix_swing(ctx: commands.Context, symbol: str = "GOLD", timeframe: str = "4h"):
    disp = format_display_symbol(symbol)
    async with ctx.typing():
        df = await mexc_client.get_klines(symbol, interval=timeframe, limit=120)
        if df is None or len(df) < 35:
            await ctx.send(f"❌ Could not fetch data for `{disp}` on `{timeframe}`.")
            return

        sw = CryptoAnalyzer.generate_swing_setup(df, disp, timeframe)
        if not sw:
            await ctx.send(f"❌ Failed to generate swing setup for `{disp}`.")
            return

        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=disp,
            interval=f"{timeframe.upper()} Swing",
            signal=sw.direction,
            confidence_pct=sw.confidence,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_swing_embed(sw)
        embed.set_image(url="attachment://signal_banner.png")
        view = QuickTradeView(disp, sw.current_price, sl=sw.stop_loss, tp=sw.target_1)
        await ctx.send(embed=embed, file=file, view=view)


@bot.command(name="scan")
async def prefix_scan(ctx: commands.Context, timeframe: str = "4h"):
    async with ctx.typing():
        scan_results = []
        for s in active_watchlist:
            df = await mexc_client.get_klines(s, interval=timeframe, limit=80)
            if df is None or len(df) < 35:
                continue

            disp = format_display_symbol(s)
            curr_p = float(df["close"].iloc[-1])
            rsi = float(CryptoAnalyzer.calculate_rsi(df["close"], 14).iloc[-1])

            if timeframe in ("1h", "4h"):
                sw = CryptoAnalyzer.generate_swing_setup(df, disp, timeframe)
                if sw:
                    scan_results.append({
                        "symbol": disp,
                        "direction": sw.direction,
                        "confidence": sw.confidence,
                        "price": curr_p,
                        "entry_zone": sw.entry_zone,
                        "target": sw.target_1,
                        "target_gain": sw.target_1_gain_pct,
                        "sl": sw.stop_loss,
                        "rsi": rsi,
                    })
            else:
                res = CryptoAnalyzer.analyze(df, disp, timeframe)
                if res and res.scalp:
                    sc = res.scalp
                    target_gain = abs(sc.scalp_tp1 - curr_p) / curr_p * 100.0
                    scan_results.append({
                        "symbol": disp,
                        "direction": sc.action,
                        "confidence": sc.confidence,
                        "price": curr_p,
                        "entry_zone": sc.entry_zone,
                        "target": sc.scalp_tp1,
                        "target_gain": target_gain,
                        "sl": sc.tight_stop_loss,
                        "rsi": rsi,
                    })

        scan_results.sort(key=lambda x: x["confidence"], reverse=True)
        embed = build_scan_overview_embed(timeframe, scan_results)
        await ctx.send(embed=embed)


@bot.command(name="preorder")
async def prefix_preorder(ctx: commands.Context, symbol: str = "GOLD", timeframe: str = "5m"):
    disp = format_display_symbol(symbol)
    async with ctx.typing():
        df = await mexc_client.get_klines(symbol, interval=timeframe, limit=100)
        if df is None or len(df) < 35:
            await ctx.send(f"❌ Could not fetch data for `{disp}`.")
            return

        current_p = df["close"].iloc[-1]
        atr = CryptoAnalyzer.calculate_atr(df, 14).iloc[-1]
        pre = CryptoAnalyzer.generate_preorder_setup(df, disp, current_p, atr)
        if not pre:
            await ctx.send(f"❌ Failed to generate pre-order for `{disp}`.")
            return

        embed = build_preorder_embed(pre, timeframe)
        view = QuickTradeView(disp, pre.limit_entry_price, sl=pre.tight_stop_loss, tp=pre.take_profit_1)
        await ctx.send(embed=embed, view=view)


@bot.command(name="calc")
async def prefix_calc(ctx: commands.Context, account_balance: float = 10000.0, entry_price: float = 0.0, stop_loss: float = 0.0, risk_pct: float = 1.0):
    if entry_price <= 0 or stop_loss <= 0:
        await ctx.send("❌ Usage: `!calc <balance> <entry> <stop_loss> [risk_pct]` (e.g. `!calc 10000 4420 4400 1.0`)")
        return

    risk_dollars = account_balance * (risk_pct / 100.0)
    price_risk_pct = abs(entry_price - stop_loss) / entry_price * 100.0
    position_size_usd = risk_dollars / (price_risk_pct / 100.0)
    suggested_leverage = max(1, min(50, int(position_size_usd / (account_balance * 0.2))))

    embed = discord.Embed(
        title="🧮 Position Size & Risk Calculator",
        color=0x3498DB,
        description=f"Risking **{risk_pct:.1f}%** (`${risk_dollars:,.2f}`) of **`${account_balance:,.2f}`** account.",
    )
    embed.add_field(name="📍 Entry Price", value=f"`${entry_price:,.2f}`", inline=True)
    embed.add_field(name="🛑 Stop Loss", value=f"`${stop_loss:,.2f}`", inline=True)
    embed.add_field(name="📏 Distance to SL", value=f"`{price_risk_pct:.2f}%`", inline=True)
    embed.add_field(name="💵 Max Position Size", value=f"**`${position_size_usd:,.2f}`**", inline=True)
    embed.add_field(name="🔒 Max Loss if SL Hit", value=f"**`-${risk_dollars:,.2f}`**", inline=True)
    embed.add_field(name="⚡ Suggested Leverage", value=f"`{suggested_leverage}x`", inline=True)
    await ctx.send(embed=embed)


@bot.command(name="long")
async def prefix_long(ctx: commands.Context, symbol: str = "GOLD", amount: float = 500.0, leverage: int = 1, sl: Optional[float] = None, tp: Optional[float] = None):
    disp = format_display_symbol(symbol)
    price = await fetch_current_price(symbol)
    if not price:
        await ctx.send(f"❌ Price unavailable for `{disp}`.")
        return

    success, msg, pos = paper_trader.open_position(
        user_id=str(ctx.author.id),
        user_name=ctx.author.display_name,
        symbol=disp,
        direction="LONG",
        amount_usd=amount,
        entry_price=price,
        leverage=leverage,
        stop_loss=sl,
        take_profit=tp,
    )
    if not success:
        await ctx.send(f"❌ {msg}")
        return

    sl_str = f" | SL: `${sl:,.2f}`" if sl else ""
    tp_str = f" | TP: `${tp:,.2f}`" if tp else ""
    await ctx.send(f"🟢 **Paper LONG Opened:** #{pos.id} `{disp}` with `${amount:,.2f}` at `${price:,.2f}` ({leverage}x){sl_str}{tp_str}")


@bot.command(name="short")
async def prefix_short(ctx: commands.Context, symbol: str = "GOLD", amount: float = 500.0, leverage: int = 1, sl: Optional[float] = None, tp: Optional[float] = None):
    disp = format_display_symbol(symbol)
    price = await fetch_current_price(symbol)
    if not price:
        await ctx.send(f"❌ Price unavailable for `{disp}`.")
        return

    success, msg, pos = paper_trader.open_position(
        user_id=str(ctx.author.id),
        user_name=ctx.author.display_name,
        symbol=disp,
        direction="SHORT",
        amount_usd=amount,
        entry_price=price,
        leverage=leverage,
        stop_loss=sl,
        take_profit=tp,
    )
    if not success:
        await ctx.send(f"❌ {msg}")
        return

    sl_str = f" | SL: `${sl:,.2f}`" if sl else ""
    tp_str = f" | TP: `${tp:,.2f}`" if tp else ""
    await ctx.send(f"🔴 **Paper SHORT Opened:** #{pos.id} `{disp}` with `${amount:,.2f}` at `${price:,.2f}` ({leverage}x){sl_str}{tp_str}")


@bot.command(name="settp")
async def prefix_settp(ctx: commands.Context, position_id: int, tp_price: float):
    ok, msg = paper_trader.update_tpsl(position_id, str(ctx.author.id), take_profit=tp_price)
    await ctx.send(f"🎯 {msg}")


@bot.command(name="setsl")
async def prefix_setsl(ctx: commands.Context, position_id: int, sl_price: float):
    ok, msg = paper_trader.update_tpsl(position_id, str(ctx.author.id), stop_loss=sl_price)
    await ctx.send(f"🛑 {msg}")


@bot.command(name="portfolio", aliases=["pnl", "balance"])
async def prefix_portfolio(ctx: commands.Context):
    user_id = str(ctx.author.id)
    user_name = ctx.author.display_name
    positions = paper_trader.get_open_positions(user_id)
    live_prices = {}
    for p in positions:
        if p.symbol not in live_prices:
            curr_p = await fetch_current_price(p.symbol)
            if curr_p:
                live_prices[p.symbol] = curr_p

    summary = paper_trader.get_portfolio_summary(user_id, user_name, live_prices)
    embed = build_portfolio_embed(user_name, summary, positions, live_prices)
    await ctx.send(embed=embed)


@bot.command(name="winrate", aliases=["stats"])
async def prefix_winrate(ctx: commands.Context):
    user_id = str(ctx.author.id)
    user_name = ctx.author.display_name
    summary = paper_trader.get_portfolio_summary(user_id, user_name, {})
    embed = build_winrate_embed(user_name, summary)
    await ctx.send(embed=embed)


@bot.command(name="close")
async def prefix_close(ctx: commands.Context, position_id: int):
    positions = paper_trader.get_open_positions(str(ctx.author.id))
    target_pos = next((p for p in positions if p.id == position_id), None)
    if not target_pos:
        await ctx.send(f"❌ Position `#{position_id}` not found.")
        return

    curr_p = await fetch_current_price(target_pos.symbol)
    if not curr_p:
        await ctx.send(f"❌ Price unavailable.")
        return

    success, msg, summary = paper_trader.close_position(position_id, curr_p, reason="MANUAL")
    await ctx.send(f"🏁 {msg}")


@bot.command(name="mtf")
async def prefix_mtf(ctx: commands.Context, symbol: str = "GOLD"):
    disp = format_display_symbol(symbol)
    async with ctx.typing():
        df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=100)
        df_1m = await mexc_client.get_klines(symbol, interval="1m", limit=100)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)

        if df_5m is None or len(df_5m) < 35 or df_1m is None or len(df_1m) < 35:
            await ctx.send(f"❌ Could not fetch data for `{disp}`.")
            return

        result = CryptoAnalyzer.analyze_dual_timeframe_confluence(df_5m, df_1m, disp)
        if not result or not result.scalp:
            await ctx.send(f"❌ Failed MTF analysis for `{disp}`.")
            return

        banner_buf = ChartGenerator.generate_signal_banner(
            df=df_5m,
            symbol=disp,
            interval="5m+1m MTF",
            signal=result.scalp.action,
            confidence_pct=result.scalp.confidence,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_scalp_embed(result, is_auto_alert=False, flow=flow)
        view = QuickTradeView(disp, result.current_price, sl=result.scalp.tight_stop_loss, tp=result.scalp.scalp_tp1)
        await ctx.send(embed=embed, file=file, view=view)


@bot.command(name="scalp")
async def prefix_scalp(ctx: commands.Context, symbol: str = "GOLD", timeframe: str = "5m"):
    disp_symbol = format_display_symbol(symbol)
    async with ctx.typing():
        df = await mexc_client.get_klines(symbol, interval=timeframe, limit=120)
        if df is None or len(df) < 35:
            await ctx.send(f"❌ Could not fetch sufficient data for `{disp_symbol}` from MEXC.")
            return

        ticker_24h = await mexc_client.get_24hr_ticker(symbol)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)
        result = CryptoAnalyzer.analyze(df, disp_symbol, timeframe, ticker_24h)
        if not result:
            await ctx.send(f"❌ Failed to run scalp analysis for `{disp_symbol}`.")
            return

        action_label = result.scalp.action if result.scalp else result.overall_signal
        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=disp_symbol,
            interval=timeframe,
            signal=action_label,
            confidence_pct=result.scalp.confidence if result.scalp else result.signal_strength_pct,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_scalp_embed(result, flow=flow)
        view = QuickTradeView(disp_symbol, result.current_price, sl=result.scalp.tight_stop_loss if result.scalp else None, tp=result.scalp.scalp_tp1 if result.scalp else None)
        await ctx.send(embed=embed, file=file, view=view)


@bot.command(name="analyze")
async def prefix_analyze(ctx: commands.Context, symbol: str = "BTC/USDT", timeframe: str = "4h"):
    disp_symbol = format_display_symbol(symbol)
    async with ctx.typing():
        df = await mexc_client.get_klines(symbol, interval=timeframe, limit=120)
        if df is None or len(df) < 35:
            await ctx.send(f"❌ Could not fetch data for `{disp_symbol}`.")
            return

        ticker_24h = await mexc_client.get_24hr_ticker(symbol)
        flow = await mexc_client.get_market_trades_flow(symbol, limit=50)
        depth = await mexc_client.get_order_book_depth(symbol, limit=20)
        result = CryptoAnalyzer.analyze(df, disp_symbol, timeframe, ticker_24h)
        if not result:
            await ctx.send(f"❌ Failed to analyze `{disp_symbol}`.")
            return

        banner_buf = ChartGenerator.generate_signal_banner(
            df=df,
            symbol=disp_symbol,
            interval=timeframe,
            signal=result.overall_signal,
            confidence_pct=result.signal_strength_pct,
        )
        file = discord.File(banner_buf, filename="signal_banner.png")
        embed = build_signal_embed(result, flow=flow, depth=depth)
        view = QuickTradeView(disp_symbol, result.current_price, sl=result.stop_loss, tp=result.take_profit_1)
        await ctx.send(embed=embed, file=file, view=view)


@bot.command(name="chart")
async def prefix_chart(ctx: commands.Context, symbol: str = "BTC/USDT", timeframe: str = "4h"):
    disp_symbol = format_display_symbol(symbol)
    async with ctx.typing():
        df = await mexc_client.get_klines(symbol, interval=timeframe, limit=100)
        if df is None or len(df) < 30:
            await ctx.send(f"❌ Could not fetch chart data for `{disp_symbol}`.")
            return

        chart_buf = ChartGenerator.generate_detailed_chart(df, disp_symbol, timeframe)
        file = discord.File(chart_buf, filename="detailed_chart.png")

        embed = discord.Embed(
            title=f"📊 {disp_symbol} Chart ({timeframe})",
            color=0x3498DB,
            description="Live Technical Breakdown: Candlesticks • EMA(20/50) • Volume • RSI(14)",
        )
        embed.set_image(url="attachment://detailed_chart.png")
        embed.set_footer(text="Data sourced in real-time from MEXC")
        await ctx.send(embed=embed, file=file)


@bot.command(name="setchannel")
async def prefix_setchannel(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    global current_alert_channel_id
    target_channel = channel or ctx.channel
    current_alert_channel_id = target_channel.id
    embed = discord.Embed(
        title="⭐ Dual-Timeframe Confluence Alerts Configured!",
        description=f"Automated 5m+1m high win-rate scalp signals will now be sent to {target_channel.mention}.",
        color=0x00FF88,
    )
    await ctx.send(embed=embed)


# ==================== ACTIVE TRADE COPILOT BACKGROUND LOOP ====================

@tasks.loop(seconds=15)
async def active_trade_copilot_loop():
    """Monitors all user-registered active trades and sends periodic pulse updates & milestone alerts."""
    try:
        active_trades = paper_trader.get_all_active_tracked_trades()
        if not active_trades:
            return

        now = time.time()
        for trade in active_trades:
            curr_p = await fetch_current_price(trade.symbol)
            if not curr_p:
                continue

            df = await mexc_client.get_klines(trade.symbol, interval=trade.timeframe, limit=60)
            analysis = CryptoAnalyzer.analyze(df, trade.symbol, trade.timeframe) if df is not None and len(df) >= 35 else None

            is_long = trade.direction == "LONG"
            pnl_pct = ((curr_p - trade.entry_price) / trade.entry_price * 100.0) if is_long else ((trade.entry_price - curr_p) / trade.entry_price * 100.0)
            price_delta = (curr_p - trade.entry_price) if is_long else (trade.entry_price - curr_p)

            # Determine AI advice & status
            advice_key = "CONSOLIDATING"
            alert_urgency = False

            if pnl_pct >= 1.2 and analysis and (("Overbought" in analysis.rsi_status and is_long) or ("Oversold" in analysis.rsi_status and not is_long)):
                advice_key = "FLASH_CLOSE_PROFIT"
                alert_urgency = True
            elif pnl_pct >= 0.6 and "BREAK_EVEN" not in trade.last_advice:
                advice_key = "BREAK_EVEN"
                alert_urgency = True
            elif pnl_pct >= 0.3:
                advice_key = "PROFIT_CONTINUATION"
            elif pnl_pct <= -1.0 and analysis and (("Bearish crossover" in analysis.macd_status and is_long) or ("Bullish crossover" in analysis.macd_status and not is_long)):
                advice_key = "FLASH_CUT_WARNING"
                alert_urgency = True
            elif pnl_pct <= -0.5:
                advice_key = "PULLBACK_HOLD"

            # Periodic heartbeat timing:
            # 1m: every 90s | 5m: every 180s (3 mins) | 1h/4h: every 10 mins
            heartbeat_interval = 90 if trade.timeframe == "1m" else (180 if trade.timeframe == "5m" else 600)
            time_since_notified = now - trade.last_notified_at

            should_notify = alert_urgency or (time_since_notified >= heartbeat_interval)

            if should_notify:
                paper_trader.update_tracked_trade_stats(trade.id, curr_p, advice_key, update_notified=True)
                ch = bot.get_channel(trade.channel_id)
                if ch:
                    embed = build_copilot_status_embed(trade, curr_p, analysis)
                    view = CopilotActionView(trade.user_id)
                    # Quick SL/TP preview calculation for notification
                    atr_h = (analysis.volatility_atr_pct / 100.0 * trade.entry_price) if (analysis and analysis.volatility_atr_pct > 0) else (trade.entry_price * 0.012)
                    if is_long:
                        quick_sl = trade.entry_price if pnl_pct >= 0.6 else max(trade.entry_price - 1.5 * atr_h, trade.entry_price * 0.985)
                        quick_tp = trade.entry_price + (1.6 * atr_h)
                        sl_diff = 0.0 if pnl_pct >= 0.6 else ((quick_sl - trade.entry_price) / trade.entry_price * 100.0)
                        tp_diff = ((quick_tp - trade.entry_price) / trade.entry_price * 100.0)
                    else:
                        quick_sl = trade.entry_price if pnl_pct >= 0.6 else min(trade.entry_price + 1.5 * atr_h, trade.entry_price * 1.015)
                        quick_tp = trade.entry_price - (1.6 * atr_h)
                        sl_diff = 0.0 if pnl_pct >= 0.6 else -((quick_sl - trade.entry_price) / trade.entry_price * 100.0)
                        tp_diff = ((trade.entry_price - quick_tp) / trade.entry_price * 100.0)

                    sl_label = "Break-Even (0.00%)" if pnl_pct >= 0.6 else f"${quick_sl:,.2f} ({sl_diff:.2f}%)"
                    pnl_sign = "+" if pnl_pct >= 0 else ""
                    msg_header = (
                        f"🚨 <@{trade.user_id}> **AI Copilot Alert:** `{trade.symbol}` **{trade.direction}** is **`{pnl_sign}{pnl_pct:.2f}%`** (`${price_delta:+,.2f}`)\n"
                        f"• 🛑 **AI Rec. SL**: `{sl_label}` | 🎯 **AI Rec. TP**: `${quick_tp:,.2f} (+{tp_diff:.2f}%)`"
                    )
                    await ch.send(content=msg_header, embed=embed, view=view)
                    logger.info(f"Sent Copilot Notification for {trade.symbol} to user {trade.user_id} ({advice_key})")
            else:
                paper_trader.update_tracked_trade_stats(trade.id, curr_p, trade.last_advice, update_notified=False)

    except Exception as e:
        logger.error(f"Error in active_trade_copilot_loop: {e}")


# ==================== DUAL-TIMEFRAME AUTOMATED SCANNER ====================

@tasks.loop(minutes=1)
async def auto_mtf_scanner():
    if not current_alert_channel_id:
        return

    try:
        channel = bot.get_channel(current_alert_channel_id)
        if not channel:
            return

        now = time.time()

        for symbol in active_watchlist:
            df_5m = await mexc_client.get_klines(symbol, interval="5m", limit=80)
            df_1m = await mexc_client.get_klines(symbol, interval="1m", limit=80)

            if df_5m is None or len(df_5m) < 35 or df_1m is None or len(df_1m) < 35:
                continue

            disp_sym = format_display_symbol(symbol)
            result = CryptoAnalyzer.analyze_dual_timeframe_confluence(df_5m, df_1m, disp_sym)
            if not result or not result.scalp:
                continue

            sc = result.scalp

            if ("SCALP LONG" in sc.action or "SCALP SHORT" in sc.action) and sc.confidence >= 80:
                alert_key = f"{symbol}_{sc.action}"
                last_time = last_alert_times.get(alert_key, 0)

                if (now - last_time) < ALERT_COOLDOWN_SECONDS:
                    continue

                last_alert_times[alert_key] = now

                flow = await mexc_client.get_market_trades_flow(symbol, limit=50)

                banner_buf = ChartGenerator.generate_signal_banner(
                    df=df_5m,
                    symbol=disp_sym,
                    interval="5m+1m MTF",
                    signal=sc.action,
                    confidence_pct=sc.confidence,
                )
                file = discord.File(banner_buf, filename="signal_banner.png")
                embed = build_scalp_embed(result, is_auto_alert=True, flow=flow)
                view = QuickTradeView(disp_sym, result.current_price, sl=sc.tight_stop_loss, tp=sc.scalp_tp1)

                await channel.send(
                    content=f"⭐ **HIGH WIN-RATE DUAL-CONFLUENCE ALERT (5m + 1m):** `{disp_sym}` • **{sc.action}** ({sc.confidence}% Confidence)",
                    embed=embed,
                    file=file,
                    view=view,
                )
                logger.info(f"Broadcasted MTF Confluence Alert for {disp_sym} ({sc.action})")

            await asyncio.sleep(0.3)

    except Exception as e:
        logger.error(f"Error in auto_mtf_scanner: {e}")


@tasks.loop(seconds=10)
async def auto_tp_sl_checker():
    try:
        open_positions = paper_trader.get_open_positions()
        if not open_positions:
            return

        unique_symbols = list(set(p.symbol for p in open_positions))
        prices: Dict[str, float] = {}
        for s in unique_symbols:
            p = await fetch_current_price(s)
            if p:
                prices[s] = p

        for pos in open_positions:
            curr_p = prices.get(pos.symbol)
            if not curr_p:
                continue

            hit_tp = False
            hit_sl = False

            if pos.direction == "LONG":
                if pos.take_profit and curr_p >= pos.take_profit:
                    hit_tp = True
                elif pos.stop_loss and curr_p <= pos.stop_loss:
                    hit_sl = True
            elif pos.direction == "SHORT":
                if pos.take_profit and curr_p <= pos.take_profit:
                    hit_tp = True
                elif pos.stop_loss and curr_p >= pos.stop_loss:
                    hit_sl = True

            if hit_tp or hit_sl:
                reason = "TAKE_PROFIT 🎯" if hit_tp else "STOP_LOSS 🛑"
                success, msg, summary = paper_trader.close_position(pos.id, curr_p, reason=reason)
                if success:
                    logger.info(f"Auto Closed position #{pos.id} {pos.symbol} due to {reason}")
                    if current_alert_channel_id:
                        ch = bot.get_channel(current_alert_channel_id)
                        if ch:
                            pnl_usd = summary["pnl_usd"]
                            color = config.COLOR_STRONG_BUY if pnl_usd >= 0 else config.COLOR_STRONG_SELL
                            embed = discord.Embed(
                                title=f"⚡ Auto-Exit Triggered ({reason}): #{pos.id} {pos.symbol}",
                                color=color,
                                description=f"<@{pos.user_id}>'s position was automatically closed.",
                            )
                            embed.add_field(name="Direction", value=f"`{pos.direction}` ({pos.leverage}x)", inline=True)
                            embed.add_field(name="Exit Price", value=f"`${curr_p:,.2f}`", inline=True)
                            embed.add_field(name="Realized PnL", value=f"**`${pnl_usd:+,.2f}` ({summary['pnl_pct']:+.2f}%)**", inline=True)
                            await ch.send(embed=embed)

    except Exception as e:
        logger.error(f"Error in auto_tp_sl_checker: {e}")


@auto_mtf_scanner.before_loop
async def before_scanner():
    await bot.wait_until_ready()


@auto_tp_sl_checker.before_loop
async def before_tp_sl():
    await bot.wait_until_ready()


@active_trade_copilot_loop.before_loop
async def before_copilot():
    await bot.wait_until_ready()


# ==================== MAIN ENTRYPOINT ====================

async def main():
    token = config.DISCORD_BOT_TOKEN
    if not token or token == "your_discord_bot_token_here":
        print("\n❌ ERROR: DISCORD_BOT_TOKEN is not set in .env!\n")
        return

    # Start cloud web health server in background for Render/Koyeb
    asyncio.create_task(start_web_health_server())

    try:
        await bot.start(token)
    finally:
        await mexc_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
