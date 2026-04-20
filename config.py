"""
Global configuration for the Options Trading Algorithm.
Loads from .env and provides constants used across the system.
"""

import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ─── Paths ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"

for d in [DATA_DIR, CACHE_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Real-Time Data: Angel One SmartAPI (FREE with demat account) ──
# Sign up: https://www.angelone.in → Enable SmartAPI
# This gives you REAL-TIME WebSocket data (<50ms latency)
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PASSWORD = os.getenv("ANGEL_PASSWORD", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# ─── Fallback: yfinance (no key needed, but ~15min delayed) ──
# Used for historical data + backtesting. Automatic fallback if Angel One not configured.

# ─── DhanHQ (OPTIONAL — only for live order execution) ────
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

# ─── Database ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ─── Reddit ──────────────────────────────────────────────
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "OptionsTrader/1.0")

# ─── Market Constants ────────────────────────────────────
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
AUTO_SQUAREOFF_HOUR = 15
AUTO_SQUAREOFF_MINUTE = 25

# ─── Supported Indices ───────────────────────────────────
INDICES = {
    "NIFTY": {
        "symbol": "NIFTY 50",
        "exchange_segment": "NSE_FNO",
        "lot_size": 25,
        "tick_size": 0.05,
        "strike_interval": 50,
    },
    "BANKNIFTY": {
        "symbol": "NIFTY BANK",
        "exchange_segment": "NSE_FNO",
        "lot_size": 15,
        "tick_size": 0.05,
        "strike_interval": 100,
    },
    "FINNIFTY": {
        "symbol": "NIFTY FIN SERVICE",
        "exchange_segment": "NSE_FNO",
        "lot_size": 25,
        "tick_size": 0.05,
        "strike_interval": 50,
    },
    "MIDCPNIFTY": {
        "symbol": "NIFTY MID SELECT",
        "exchange_segment": "NSE_FNO",
        "lot_size": 50,
        "tick_size": 0.05,
        "strike_interval": 25,
    },
}

# ─── Supported Stocks (Top F&O Stocks) ──────────────────
FNO_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "BAJFINANCE", "TATAMOTORS", "AXISBANK", "ITC",
    "LT", "KOTAKBANK", "HINDUNILVR", "MARUTI", "TATASTEEL",
]

# ─── Trading Parameters ─────────────────────────────────
PAPER_TRADING_CAPITAL = float(os.getenv("PAPER_TRADING_CAPITAL", "500000"))
MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "2"))
MAX_DAILY_DRAWDOWN_PCT = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "3"))
MAX_SIMULTANEOUS_POSITIONS = int(os.getenv("MAX_SIMULTANEOUS_POSITIONS", "2"))

# ─── Risk Limits ────────────────────────────────────────
MAX_LOSS_PER_TRADE = PAPER_TRADING_CAPITAL * (MAX_RISK_PER_TRADE_PCT / 100)
MAX_DAILY_LOSS = PAPER_TRADING_CAPITAL * (MAX_DAILY_DRAWDOWN_PCT / 100)
SLIPPAGE_PCT = 0.5       # 0.5% slippage on market orders
BROKERAGE_PER_ORDER = 20  # Flat ₹20 per F&O order

# ─── Signal Weights ──────────────────────────────────────
SIGNAL_WEIGHTS = {
    "technical": 0.25,
    "greeks": 0.20,
    "oi": 0.25,
    "sentiment": 0.15,
    "regime": 0.15,
}

# ─── Confluence Threshold ────────────────────────────────
MIN_ENTRY_SCORE = 0.35   # Minimum confluence to enter a trade (realistic: layers rarely all agree)
MIN_EXIT_SCORE = 0.20    # Exit when signal weakens significantly

# ─── Data Refresh Intervals (seconds) ───────────────────
OPTION_CHAIN_REFRESH = 60      # 1 minute
MARKET_DATA_REFRESH = 5        # 5 seconds
SENTIMENT_REFRESH = 300        # 5 minutes
SIGNAL_REFRESH = 30            # 30 seconds

# ─── Sentiment Sources ──────────────────────────────────
RSS_FEEDS = [
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://www.moneycontrol.com/rss/stocksnews.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.livemint.com/rss/markets",
]

REDDIT_SUBREDDITS = [
    "IndianStockMarket",
    "IndianStreetBets",
    "DalalStreetBets",
]

# ─── Greeks Defaults ────────────────────────────────────
RISK_FREE_RATE = 0.065    # 6.5% (India 10-year govt bond yield approx)
TRADING_DAYS_PER_YEAR = 252

# ─── Logging ─────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ─── Backtest Defaults ───────────────────────────────────
BACKTEST_START_CAPITAL = 500000  # Rs.5,00,000 (realistic F&O margin for NIFTY)
BACKTEST_DEFAULT_DAYS = 90

# ─── Option Chain Analysis (NSE OCA style) ───────────────
OI_STRIKE_RANGE = 10       # Number of strikes above/below ATM to analyze
OI_BOUNDARY_OFFSET = 2     # Strikes above for call boundary
PCR_BULLISH_THRESHOLD = 1.2
PCR_BEARISH_THRESHOLD = 0.8
ITM_RATIO_THRESHOLD = 1.5  # Ratio threshold for ITM signals

# ─── Expiry & Theta Decay ────────────────────────────────
DEFAULT_DAYS_TO_EXPIRY = 7       # Weekly expiry default
THETA_DECAY_MODEL = "accelerated"  # 'linear' or 'accelerated' (sqrt model)
MIN_OPTION_PREMIUM = 0.50         # Minimum realistic option premium (Rs.)
MIN_PREMIUM_TO_TRADE = 2.0        # Don't trade options below Rs.2
