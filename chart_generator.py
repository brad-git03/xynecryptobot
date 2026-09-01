import io
from typing import Optional
import matplotlib
# Use Agg backend for headless server rendering
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as patches
import numpy as np
import pandas as pd


class ChartGenerator:
    """Generates high quality dark-themed trading charts and signal banner images for Discord."""

    @staticmethod
    def generate_signal_banner(
        df: pd.DataFrame,
        symbol: str,
        interval: str,
        signal: str,
        confidence_pct: int,
    ) -> io.BytesIO:
        """
        Generates a stylish header banner matching the reference card design
        (Big Signal text, dark neon aesthetic, candlestick backdrop).
        """
        df_sub = df.tail(45).copy().reset_index(drop=True)

        is_buy = "BUY" in signal.upper()
        is_sell = "SELL" in signal.upper()

        if is_buy:
            theme_color = "#00FF88"  # Neon green
            bg_gradient_top = "#081c15"
            bg_main = "#0d1b1e"
            action_text = "BUY" if signal == "BUY" else "STRONG BUY"
        elif is_sell:
            theme_color = "#FF0055"  # Neon pink/red
            bg_gradient_top = "#1f0910"
            bg_main = "#120d14"
            action_text = "SELL" if signal == "SELL" else "STRONG SELL"
        else:
            theme_color = "#3498DB"  # Neon blue
            bg_gradient_top = "#0a192f"
            bg_main = "#0d1117"
            action_text = "NEUTRAL"

        fig, ax = plt.subplots(figsize=(8.0, 3.2), dpi=140)
        fig.patch.set_facecolor(bg_main)
        ax.set_facecolor(bg_main)

        # Plot candles in background
        for i, row in df_sub.iterrows():
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            candle_color = theme_color if c >= o else (
                "#FF2A6D" if is_sell else ("#00F0FF" if not is_buy else "#FF4757")
            )
            alpha = 0.55

            # High-low wick
            ax.plot([i, i], [l, h], color=candle_color, linewidth=1.2, alpha=alpha)
            # Open-close body
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.05)
            rect = patches.Rectangle(
                (i - 0.35, body_bottom),
                0.7,
                body_height,
                linewidth=0,
                facecolor=candle_color,
                alpha=alpha,
            )
            ax.add_patch(rect)

        # Remove axes borders and ticks
        ax.set_axis_off()
        ax.set_xlim(-1, len(df_sub) + 1)
        
        # Add subtle glow curve
        closes = df_sub["close"].values
        ax.plot(
            range(len(closes)),
            closes,
            color=theme_color,
            linewidth=2.5,
            alpha=0.85,
        )

        # Big Bold Text on top left
        plt.figtext(
            0.08,
            0.52,
            action_text,
            fontsize=36,
            fontweight="heavy",
            color="#FFFFFF",
            fontfamily="sans-serif",
            ha="left",
            va="center",
        )

        # Subtext (Symbol, interval, signal strength)
        plt.figtext(
            0.08,
            0.28,
            f"{symbol}  •  {interval}  •  Signal Strength: {confidence_pct}%",
            fontsize=13,
            fontweight="medium",
            color=theme_color,
            fontfamily="sans-serif",
            ha="left",
            va="center",
        )

        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05, facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf

    @staticmethod
    def generate_detailed_chart(
        df: pd.DataFrame,
        symbol: str,
        interval: str,
    ) -> io.BytesIO:
        """
        Generates a comprehensive 3-panel trading chart:
        Panel 1: Candlesticks + EMA20 + EMA50 + Bollinger Bands
        Panel 2: Volume
        Panel 3: RSI (14) with 70/30 threshold lines
        """
        df_plot = df.tail(60).copy().reset_index(drop=True)

        fig, (ax_main, ax_vol, ax_rsi) = plt.subplots(
            3,
            1,
            figsize=(10, 7.5),
            dpi=130,
            gridspec_kw={"height_ratios": [5, 1.5, 2]},
            sharex=True,
        )
        bg_dark = "#0F141C"
        panel_dark = "#151B26"

        for ax in [ax_main, ax_vol, ax_rsi]:
            ax.set_facecolor(panel_dark)
            ax.grid(True, color="#252E3E", linestyle="--", linewidth=0.5, alpha=0.7)
            ax.tick_params(colors="#8892B0", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#252E3E")

        fig.patch.set_facecolor(bg_dark)

        # 1. Main Candlestick Panel
        for i, row in df_plot.iterrows():
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            color = "#00E676" if c >= o else "#FF3D71"
            ax_main.plot([i, i], [l, h], color=color, linewidth=1.0)
            rect = patches.Rectangle(
                (i - 0.35, min(o, c)),
                0.7,
                max(abs(c - o), (h - l) * 0.02),
                facecolor=color,
                edgecolor=color,
            )
            ax_main.add_patch(rect)

        # Calculate & Overlay EMAs
        ema_20 = df["close"].ewm(span=20, adjust=False).mean().tail(len(df_plot)).values
        ema_50 = df["close"].ewm(span=50, adjust=False).mean().tail(len(df_plot)).values
        ax_main.plot(range(len(df_plot)), ema_20, color="#FFD166", linewidth=1.2, label="EMA 20")
        ax_main.plot(range(len(df_plot)), ema_50, color="#06D6A0", linewidth=1.2, label="EMA 50")
        ax_main.legend(loc="upper left", facecolor="#1B2230", edgecolor="#2E384D", labelcolor="#FFFFFF", fontsize=8)
        ax_main.set_title(f"{symbol} ({interval}) • MEXC Live", color="#FFFFFF", fontsize=12, fontweight="bold", pad=10)

        # 2. Volume Panel
        vol_colors = ["#00E676" if c >= o else "#FF3D71" for o, c in zip(df_plot["open"], df_plot["close"])]
        ax_vol.bar(range(len(df_plot)), df_plot["volume"], color=vol_colors, width=0.7, alpha=0.8)
        ax_vol.set_ylabel("Volume", color="#8892B0", fontsize=8)

        # 3. RSI Panel
        delta = df["close"].diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        rsi_all = 100 - (100 / (1 + rs))
        rsi_plot = rsi_all.tail(len(df_plot)).values

        ax_rsi.plot(range(len(df_plot)), rsi_plot, color="#A78BFA", linewidth=1.3, label="RSI(14)")
        ax_rsi.axhline(70, color="#FF3D71", linestyle=":", linewidth=0.9, alpha=0.8)
        ax_rsi.axhline(30, color="#00E676", linestyle=":", linewidth=0.9, alpha=0.8)
        ax_rsi.axhline(50, color="#8892B0", linestyle="--", linewidth=0.6, alpha=0.5)
        ax_rsi.set_ylim(10, 90)
        ax_rsi.set_ylabel("RSI", color="#8892B0", fontsize=8)

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf
