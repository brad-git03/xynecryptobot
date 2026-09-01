import os
from typing import List
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "")
AUTO_SCAN_ENABLED = os.getenv("AUTO_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
AUTO_SCAN_INTERVAL_MINUTES = int(os.getenv("AUTO_SCAN_INTERVAL_MINUTES", "5"))
MIN_SIGNAL_CONFIDENCE = int(os.getenv("MIN_SIGNAL_CONFIDENCE", "75"))

# Parse default watchlist
raw_watchlist = os.getenv(
    "DEFAULT_WATCHLIST",
    "BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,PEPE/USDT,SUI/USDT,BNB/USDT",
)
WATCHLIST: List[str] = [s.strip().upper() for s in raw_watchlist.split(",") if s.strip()]

# Supported Timeframes
SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
DEFAULT_TIMEFRAME = "5m"

# Color constants for Discord Embeds
COLOR_STRONG_BUY = 0x00FF88   # Bright Green
COLOR_BUY = 0x2ECC71          # Green
COLOR_NEUTRAL = 0x95A5A6      # Gray
COLOR_SELL = 0xE74C3C         # Red
COLOR_STRONG_SELL = 0xFF0055  # Bright Red/Magenta
